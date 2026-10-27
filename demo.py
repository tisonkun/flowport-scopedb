#!/usr/bin/env python3
"""Configure and verify direct FlowPort-to-ScopeDB HTTP delivery."""

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
FLOW_ID = "scopedb-http-demo"
TABLE = "scopedb.public.flowport_demo_points"
VIEW = "scopedb.public.flowport_demo_logs"


class APIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Client:
    def __init__(self, endpoint, headers=None):
        self.endpoint = endpoint.rstrip("/")
        self.headers = dict(headers or {})
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", **self.headers},
        )
        # Writes are submitted once: a lost response is not proof of rejection.
        try:
            with self.opener.open(request, timeout=20) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise APIError(error.code, error.read().decode(errors="replace")) from error


def wait_for(description, attempt, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        result = attempt()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {description}")
        time.sleep(0.5)


def query(client, statement):
    result = client.request("POST", "/v1/statements", {"statement": statement, "format": "json"})
    statement_id = result["statement_id"]

    def finished():
        nonlocal result
        if result["status"] in ("pending", "running"):
            result = client.request("GET", f"/v1/statements/{statement_id}?format=json")
        if result["status"] in ("pending", "running"):
            return None
        if result["status"] != "finished":
            raise RuntimeError(f"Statement {statement_id}: {result.get('error', result.get('message'))}")
        return result["result_set"]

    return wait_for(f"statement {statement_id}", finished)


def bootstrap(client):
    def ready():
        try:
            return query(client, "SELECT 1 AS ready")
        except (urllib.error.URLError, ConnectionError):
            return None
        except APIError as error:
            if error.status != 503:
                raise
            return None

    wait_for("ScopeDB", ready)
    query(client, "CREATE DATABASE IF NOT EXISTS scopedb")
    query(client, "CREATE SCHEMA IF NOT EXISTS scopedb.public")
    for statement in (ROOT / "schema.sql").read_text().split(";"):
        if statement.strip():
            query(client, statement)


def login(flowport):
    def ready():
        try:
            return flowport.request("POST", "/login", {
                "password": os.environ.get("FLOWPORT_ADMIN_PASSWORD", "local-demo-password")
            })
        except (urllib.error.URLError, ConnectionError):
            return None
        except APIError as error:
            if error.status != 503:
                raise
            return None

    session = wait_for("FlowPort", ready)
    flowport.headers["X-CSRF-Token"] = session["csrf"]


def configure(flowport, destination, source_token):
    flowport.request("PUT", f"/destinations/{FLOW_ID}", destination)
    try:
        previous = flowport.request("GET", f"/flows/{FLOW_ID}")
    except APIError as error:
        if error.status != 404:
            raise
        previous = {}
    draft = flowport.request("PUT", f"/flows/{FLOW_ID}", {
        "id": FLOW_ID,
        "name": "Direct HTTP to ScopeDB",
        "revision": previous.get("revision", 0),
        "draft_revision": previous.get("draft_revision", 0),
        "source": {"type": "http_server", "config": {
            "format": "json", "measurement": "app_logs", "token": source_token,
        }},
        "pipelines": {"logging": '\n'.join([
            'set_tag("service", service)',
            'set_tag("env", env)',
            'set_tag("demo_run", demo_run)',
        ])},
        "destination_ids": [FLOW_ID],
        "queue_bytes": 16777216,
    })
    published = flowport.request("POST", f"/flows/{FLOW_ID}/publish", {
        "draft_revision": draft["draft_revision"]
    })

    def installed():
        deployment = flowport.request("GET", f"/flows/{FLOW_ID}/deployment")
        workers = [worker for worker in deployment["workers"] if worker["desired"]]
        if deployment["status"] == "running" and workers and all(
            worker["installed"] == published["revision"] for worker in workers
        ):
            return deployment
        return None

    wait_for("published FlowPort configuration", installed)


def delivery_ready(observation):
    samples = observation.get("samples") or []
    return bool(samples) and all(
        sample["online"] and sample["stats"]["pending"] == 0
        for sample in samples
    )


def verify_rows(rows, events):
    if len(rows) > len(events):
        raise RuntimeError("Duplicate delivery detected for this demo run")
    if len(rows) != len(events):
        return False
    actual = sorted((row[4], row[1], row[2], row[5], int(row[6])) for row in rows)
    expected = sorted((event["event_id"], event["service"], event["env"],
                       event["message"], event["duration_ms"]) for event in events)
    if actual != expected:
        raise RuntimeError("Stored logs do not match the submitted sample")
    return True


def run(args):
    cloud = args.mode == "cloud"
    endpoint = os.environ.get("SCOPEDB_ENDPOINT", "") if cloud else "http://127.0.0.1:19083"
    api_key = os.environ.get("SCOPEDB_API_KEY", "") if cloud else ""
    if cloud:
        if not endpoint or not api_key:
            raise ValueError("Cloud mode requires SCOPEDB_ENDPOINT and SCOPEDB_API_KEY")
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("SCOPEDB_ENDPOINT must be an HTTPS URL without embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("SCOPEDB_ENDPOINT must not contain a query or fragment")
    compose = ["docker", "compose"]
    if not cloud:
        compose += ["--profile", "local"]
    print("Starting the demo services...", flush=True)
    subprocess.run(compose + ["up", "--build", "-d"], cwd=ROOT, check=True)

    scope_headers = {"Authorization": f"Bearer {api_key}"} if cloud else {
        "X-ScopeDB-Tenant-Id": "__system__"
    }
    scopedb = Client(endpoint, scope_headers)
    bootstrap(scopedb)
    print("ScopeDB table and log view are ready.", flush=True)

    flowport = Client("http://127.0.0.1:19080/api/v1")
    login(flowport)
    # Do not change the target of a previous run while it still has queued data.
    def drained():
        try:
            observation = flowport.request("GET", f"/flows/{FLOW_ID}/observe")
        except APIError as error:
            if error.status != 404:
                raise
            return True
        return True if delivery_ready(observation) else None

    wait_for("previous demo delivery to drain", drained)
    destination = json.loads((ROOT / "destination.example.json").read_text())
    config = destination["config"]
    target = endpoint.rstrip("/") if cloud else "http://scopedb:6543"
    config["url"] = target + "/v1/databases/scopedb/schemas/public/tables/flowport_demo_points/rows"
    config["compression"] = args.compression
    if cloud:
        config["token"] = api_key
    else:
        del config["token"]
        config["headers"] = scope_headers
    source_token = secrets.token_urlsafe(32)
    configure(flowport, destination, source_token)
    print(f"FlowPort published: HTTP NDJSON, compression={args.compression}.", flush=True)

    run_id = secrets.token_hex(8)
    events = json.loads((ROOT / "samples/logs.json").read_text())
    for event in events:
        event["demo_run"] = run_id
    source = Client("http://127.0.0.1:19081", {"Authorization": f"Bearer {source_token}"})
    source.request("POST", f"/ingest/{FLOW_ID}", events)
    print(f"FlowPort accepted {len(events)} logs; checking ScopeDB commits...", flush=True)

    def written():
        result = query(scopedb, f"FROM {VIEW} WHERE demo_run = '{run_id}' ORDER BY event_id")
        return result if verify_rows(result["rows"], events) else None

    result = wait_for("committed ScopeDB rows", written)
    wait_for("FlowPort queue to drain", lambda: True if delivery_ready(
        flowport.request("GET", f"/flows/{FLOW_ID}/observe")
    ) else None)
    print(f"\nPASS: {len(events)} matching rows in ScopeDB; FlowPort queue drained.")
    print("time | service | env | event_id | message | duration_ms")
    for row in result["rows"]:
        print(" | ".join(str(row[index]) for index in (0, 1, 2, 4, 5, 6)))
    print(f"\nFlowPort UI: http://127.0.0.1:19080\nDemo run: {run_id}")
    print("Inspect raw data: FROM scopedb.public.flowport_demo_points")
    print("Stop services: docker compose --profile local down")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "cloud"), default="local")
    parser.add_argument("--compression", choices=("none", "gzip"), default="gzip")
    args = parser.parse_args()
    try:
        run(args)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        print("Inspect services with: docker compose --profile local logs --tail 50", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
