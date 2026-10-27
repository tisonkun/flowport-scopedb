# FlowPort → ScopeDB over HTTP

Send logs from FlowPort directly to ScopeDB using its built-in HTTP Destination. This demo creates a raw Point table, publishes a FlowPort pipeline, submits three sample logs, and verifies their contents in ScopeDB. There is no OpenTelemetry dependency or intermediate forwarding service.

```text
Sample JSON → FlowPort → HTTP NDJSON (gzip) → ScopeDB Point table
                                                    ↓
                                               SQL log view
```

The Python script configures the services and checks the result. It is not in the forwarding path. Once configured, FlowPort delivers directly to ScopeDB while the script is stopped.

## Run the local demo

Requirements: Docker with Compose, Python 3.10+, and internet access for container images and the official FlowPort package. Both Linux amd64 and arm64 are supported. Docker Desktop or another Linux container runtime is required on macOS and Windows.

```sh
git clone https://github.com/tisonkun/flowport-scopedb.git
cd flowport-scopedb
python3 demo.py
```

The command builds FlowPort 0.5.1 from its official release package, verifies its architecture-specific SHA-256, and starts FlowPort plus ScopeDB 0.6.0 with PostgreSQL metadata and MinIO storage. PostgreSQL and MinIO are local ScopeDB dependencies; neither participates in forwarding logs between FlowPort and ScopeDB.

On success, the output includes:

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

Open [FlowPort](http://127.0.0.1:19080) with the demo password `local-demo-password`. Inspect **Direct HTTP to ScopeDB**, its HTTP target, the published pipeline, and the runtime queue. No username is required. All published container ports bind to loopback. Ports 19080, 19081, and 19083 must be available.

The local ScopeDB core uses its internal `__system__` tenant header without a Cloud authentication gateway. The fixed local credentials and internal header are for this disposable environment only; use the Cloud configuration below for a real workspace.

Stop services while preserving their volumes:

```sh
docker compose --profile local down
```

Reset the entire demo, including all stored logs and configuration:

```sh
docker compose --profile local down --volumes
```

## Use a ScopeDB Cloud workspace

Use a demo workspace where creating the `flowport_demo_points` table and `flowport_demo_logs` view is intended. The script creates or updates those resources and the dedicated `scopedb-http-demo` FlowPort destination and dataflow. Stop and reset the local demo before switching modes; do not switch a target while it has backlog.

```sh
docker compose --profile local down --volumes
export SCOPEDB_ENDPOINT='https://<your-workspace-endpoint>'
export SCOPEDB_API_KEY='<your-api-key>'
python3 demo.py --mode cloud
```

Cloud mode starts only FlowPort locally and configures its Destination with the workspace endpoint and Bearer API key. ScopeDB must be reachable from both the host and the FlowPort container. The `scopedb.public` namespace is used. The API key needs access to the DDL, append, and query operations used by the demo. The local core and both HTTP encodings have been exercised end to end; Cloud credential and gateway interoperability must be verified against the selected workspace.

`FLOWPORT_ADMIN_PASSWORD` optionally overrides the local demo password for a fresh instance. The initial password is persisted; keep the same value on subsequent runs. Environment variables must be exported in the shell running Python; the script does not load `.env` files.

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

Create the table and view using [schema.sql](schema.sql). Add a Destination using [destination.example.json](destination.example.json), replacing the endpoint and key. This is the direct ScopeDB Cloud configuration:

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

Select this Destination in the dataflow, save, publish, and confirm Worker installation. The demo's logging Pipeline extracts the query dimensions:

```text
set_tag("service", service)
set_tag("env", env)
set_tag("demo_run", demo_run)
```

`demo_run` is only needed to isolate demo verification. Existing applications can select their own Tags and retain the other attributes in `fields`.

Send the sample logs to the published HTTP Source using that Source's token. Source tokens, FlowPort administrator credentials, and ScopeDB API keys are separate credentials. The management API can save a Destination with `PUT /api/v1/destinations/{id}`; dataflow publication is a separate operation. See the [official management API guide](https://flowport.pages.dev/en/docs/api/).

## Delivery boundaries

- FlowPort's HTTP Source returning 204 confirms local durable acceptance, not ScopeDB commit. This demo verifies the stored rows separately.
- FlowPort 0.5.1 defaults to five retries after the first failed send. Retry exhaustion and channel capacity eviction can discard unconfirmed data. See the [official delivery policy](https://flowport.pages.dev/en/docs/delivery/).
- A lost write acknowledgement can cause duplicate records on replay. The demo script does not automatically retry mutating requests after an HTTP or transport failure. Inspect FlowPort's runtime state and query the current run before rerunning after an ambiguous outcome.
- Generic HTTP delivery does not validate ScopeDB's `append_state` or inserted-row count. A mocked HTTP 200 containing `append_state: unknown` is still counted as success by FlowPort 0.5.1. ScopeDB normally reports rejected appends with non-2xx status; custom gateways must preserve error status. A native ScopeDB Destination could add body-level receipt validation without another network hop.
- ScopeDB 0.6.0 rejects JSON integers outside the signed 64-bit range, including inside `fields`. The sample deliberately represents its large event ID as a string. One incompatible integer rejects the whole batch. HTTP 413 is not automatically split into smaller requests by FlowPort's generic HTTP output.
- Generic HTTP supports all accepted Point categories, but this demo verifies logs. Generic JSON hides internal protocol metadata and should not be assumed to preserve every OTLP-specific structure.

## Development

The runner uses only the Python standard library. Tests cover terminal statement failures, ambiguous HTTP writes, and exact sample verification boundaries. CI runs the tests and the full local demo with both supported HTTP compression settings.

```sh
python3 -m unittest discover -s tests -v
python3 demo.py
python3 demo.py --compression none
```

## References and licensing

- [FlowPort downloads and checksums](https://flowport.pages.dev/en/downloads/)
- [FlowPort HTTP Destination](https://flowport.pages.dev/en/integrations/#destination-http)
- [FlowPort public documentation repository](https://github.com/SamuelSupe/flowport)
- [ScopeDB Go SDK append contract](https://github.com/scopedb/goscopedb#streaming-writes)

This repository's code is MIT licensed. FlowPort is separately licensed upstream software; the Dockerfile downloads the official package, and no FlowPort binaries or source code are included here. The integration uses its built-in HTTP Destination and does not require a plugin SDK.
