# FlowPort → ScopeDB over HTTP

Send logs from FlowPort directly to ScopeDB using its built-in HTTP Destination. This demo creates a raw Point table, publishes a FlowPort pipeline, submits three sample logs, and verifies their contents in ScopeDB. There is no OpenTelemetry dependency or intermediate forwarding service.

```text
Sample JSON → FlowPort → HTTP NDJSON (gzip) → ScopeDB Point table
                                                    ↓
                                               SQL log view
```

The Python script configures the services and checks the result. It is not in the forwarding path. Once configured, FlowPort delivers directly to ScopeDB while the script is stopped.

## Run the demo

Requirements: Docker with Compose, Python 3.10+, and a reachable ScopeDB workspace with an API key. Linux amd64 and arm64 are supported; macOS and Windows need a Linux container runtime. Ports 19080 and 19081 must be available.

Use a demo workspace where creating `scopedb.public.flowport_demo_points` and `scopedb.public.flowport_demo_logs` is intended. The script creates or updates those resources and the dedicated `scopedb-http-demo` FlowPort destination and dataflow.

```sh
git clone https://github.com/tisonkun/flowport-scopedb.git
cd flowport-scopedb
export SCOPEDB_ENDPOINT='https://<your-workspace-endpoint>'
export SCOPEDB_API_KEY='<your-api-key>'
python3 demo.py
```

The command downloads FlowPort 0.5.1 from the official release site, verifies its architecture-specific SHA-256, and starts it in Docker. It then prepares the ScopeDB table and view, publishes the HTTP Destination, sends [three sample logs](samples/logs.json), and queries the stored rows. ScopeDB must be reachable from both the host and the FlowPort container. The key needs access to the demo's DDL, append, and query operations.

Successful output includes:

```text
ScopeDB table and log view are ready.
FlowPort published: HTTP NDJSON, compression=gzip.
FlowPort accepted 3 logs; checking ScopeDB commits...

PASS: 3 matching rows in ScopeDB; FlowPort queue drained.
time | service | env | event_id | message | duration_ms
<timestamp> | checkout | production | 18446744073709551615 | payment completed | 42
<timestamp> | checkout | production | payment-002 | payment gateway timed out | 1500
<timestamp> | search | staging | search-003 | search completed | 17
```

Each run gets a fresh `demo_run` tag. Verification checks that run's exact IDs, services, environments, messages, and durations, so rerunning does not mistake old records for successful delivery. A duplicate observed during verification fails the demo; this check is not an exactly-once guarantee.

Test uncompressed delivery too:

```sh
python3 demo.py --compression none
```

Open [FlowPort](http://127.0.0.1:19080) with the demo password `local-demo-password`; no username is required. Inspect **Direct HTTP to ScopeDB**, its HTTP target, the published pipeline, and the runtime queue. Published container ports bind to loopback. `FLOWPORT_ADMIN_PASSWORD` optionally overrides the password for a fresh instance; it is persisted, so keep the same value on subsequent runs. Variables must be exported in the shell running Python; the script does not load `.env` files.

Stop FlowPort while preserving its configuration:

```sh
docker compose down
```

Remove the demo's local configuration and encrypted queued data:

```sh
docker compose down --volumes
```

This does not delete the ScopeDB table or view. Never remove the volume before required backlog has drained.

## What the integration writes

The HTTP Destination produces one Point per NDJSON line. A formatted example is:

```json
{
  "measurement": "app_logs",
  "tags": {
    "collectway_flow": "scopedb-http-demo",
    "source_type": "http_server",
    "service": "checkout",
    "env": "production",
    "demo_run": "example-run"
  },
  "fields": {
    "event_id": "18446744073709551615",
    "message": "payment completed",
    "duration_ms": 42,
    "status": "info"
  },
  "timestamp": 1793097642224104104,
  "category": "logging"
}
```

The sample omits event time, so FlowPort assigns the Point timestamp. Configure source event-time extraction separately when preserving upstream time is required.

[schema.sql](schema.sql) stores the five top-level fields directly. `timestamp` is an `int` holding Unix nanoseconds. The view casts it to ScopeDB `timestamp` and exposes selected nested fields as ordinary columns. A ScopeDB `timestamp` column does not accept the raw JSON integer through this append interface; keeping the integer avoids a transport conversion.

```sql
FROM scopedb.public.flowport_demo_logs
WHERE service = 'checkout'
SELECT time, env, message, duration_ms
ORDER BY time DESC
LIMIT 20;
```

## Configure an existing FlowPort instance

Create the table and view using [schema.sql](schema.sql). Add a Destination using [destination.example.json](destination.example.json), replacing the endpoint and key:

```json
{
  "id": "scopedb-http-demo",
  "name": "ScopeDB HTTP demo",
  "type": "http",
  "config": {
    "url": "https://<endpoint>/v1/databases/scopedb/schemas/public/tables/flowport_demo_points/rows",
    "token": "<api-key>",
    "method": "POST",
    "encoding": "ndjson",
    "compression": "gzip",
    "timeout_seconds": 20
  }
}
```

Select the Destination in the dataflow, save, publish, and confirm Worker installation. The demo's logging Pipeline extracts the query dimensions:

```text
set_tag("service", service)
set_tag("env", env)
set_tag("demo_run", demo_run)
```

`demo_run` is only needed to isolate demo verification. Existing applications can select their own Tags and retain the other attributes in `fields`.

Send the sample logs to the published HTTP Source using that Source's token. Source tokens, FlowPort administrator credentials, and ScopeDB API keys are separate credentials. The management API saves a Destination with `PUT /api/v1/destinations/{id}`; dataflow publication is separate. See the [official management API guide](https://flowport.pages.dev/en/docs/api/).

## Optional local ScopeDB environment

The default demo connects to an existing endpoint and does not require a ScopeDB container image. If you already have an authorized ScopeDB 0.6.0 image, the `local` Compose profile can start a complete disposable environment. Set the image reference explicitly; this repository does not distribute the ScopeDB server.

```sh
export SCOPEDB_IMAGE='<your-authorized-scopedb-0.6.0-image>'
python3 demo.py --mode local
python3 demo.py --mode local --compression none
```

This profile adds ScopeDB, PostgreSQL metadata, and MinIO storage. PostgreSQL and MinIO are ScopeDB dependencies; neither forwards logs between FlowPort and ScopeDB. ScopeDB listens at `127.0.0.1:19083` and uses its internal `__system__` tenant header without a Cloud authentication gateway. Fixed credentials and this header are only for the disposable local stack.

Both gzip and uncompressed HTTP delivery have been verified against a real ScopeDB 0.6.0 local instance, including a clean start and rerun. Cloud credential and gateway interoperability must be verified against the selected workspace.

Before switching modes, drain and reset the local environment:

```sh
docker compose --profile local down --volumes
```

This removes all local demo data, including logs in the local ScopeDB profile. Do not switch the Destination's endpoint while it has backlog.

## Delivery boundaries

- HTTP Source 204 confirms FlowPort's local durable acceptance, not ScopeDB commit. This demo verifies stored rows separately.
- FlowPort 0.5.1 defaults to five retries after the first failed send. Exhaustion and channel capacity eviction can discard unconfirmed data. See the [official delivery policy](https://flowport.pages.dev/en/docs/delivery/).
- Lost write acknowledgements can cause duplicate records on replay. The script does not automatically retry mutating requests after HTTP or transport failures. Inspect runtime state and query the current run before rerunning after an ambiguous outcome.
- Generic HTTP does not validate ScopeDB's `append_state` or inserted-row count. A mocked HTTP 200 containing `append_state: unknown` is still counted as success by FlowPort 0.5.1. ScopeDB normally reports rejected appends with non-2xx status; custom gateways must preserve error status. A native ScopeDB Destination could add receipt validation without another network hop.
- ScopeDB 0.6.0 rejects JSON integers outside the signed 64-bit range, including inside `fields`. The sample's large event ID is a string. One incompatible integer rejects the whole batch. FlowPort's generic HTTP output does not automatically split HTTP 413 requests.
- Generic HTTP supports all accepted Point categories, but this demo verifies logs. Generic JSON hides internal protocol metadata and should not be assumed to preserve every OTLP-specific structure.

## Tests

The code uses only Python's standard library. Unit tests cover failed statements, ambiguous writes, duplicates, and identifier precision. Public CI also starts the real FlowPort binary and captures its gzip and uncompressed NDJSON output, checking authentication, headers, and Point contents. That receiver verifies the HTTP contract only; it is not a ScopeDB emulator or a database commit test.

```sh
python3 -m unittest discover -s tests -v
python3 tests/flowport_smoke.py
```

The wire test uses its own `scopedb-http-wire-test` dataflow and a temporary HTTP receiver on port 19084. Full database validation is performed by `demo.py` against the configured ScopeDB endpoint.

## References and licensing

- [FlowPort downloads and checksums](https://flowport.pages.dev/en/downloads/)
- [FlowPort HTTP Destination](https://flowport.pages.dev/en/integrations/#destination-http)
- [FlowPort public documentation repository](https://github.com/SamuelSupe/flowport)
- [Connect to ScopeDB](https://docs.scopedb.io/guides/connect-to-scopedb)
- [ScopeDB Go SDK append contract](https://github.com/scopedb/goscopedb#streaming-writes)

This repository's code is MIT licensed. FlowPort is separately licensed upstream software; the Dockerfile downloads the official package, and no FlowPort binaries or source code are included here. The integration uses its built-in HTTP Destination and does not require a plugin SDK.
