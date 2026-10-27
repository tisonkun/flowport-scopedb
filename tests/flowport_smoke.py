"""Exercise real FlowPort HTTP encoding without database credentials."""

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demo


def main():
    captures = queue.Queue()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            captures.put((dict(self.headers), body))
            # This endpoint verifies HTTP output only; it is not a ScopeDB emulator.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    subprocess.run(["docker", "compose", "up", "--build", "-d", "flowport"],
                   cwd=demo.ROOT, check=True)
    server = ThreadingHTTPServer(("0.0.0.0", 19084), Receiver)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        flowport = demo.Client("http://127.0.0.1:19080/api/v1")
        demo.login(flowport)
        flow_id = "scopedb-http-wire-test"
        for compression in ("gzip", "none"):
            source_token = secrets.token_urlsafe(32)
            demo.configure(flowport, {
                "id": flow_id,
                "name": "HTTP wire contract test",
                "type": "http",
                "config": {
                    "url": "http://host.docker.internal:19084/points",
                    "token": "synthetic-wire-test-key",
                    "headers": {"X-Wire-Test": "flowport-scopedb"},
                    "encoding": "ndjson",
                    "compression": compression,
                },
            }, source_token, flow_id=flow_id, name="HTTP wire contract test")
            events = json.loads((demo.ROOT / "samples/logs.json").read_text())
            run_id = secrets.token_hex(8)
            for event in events:
                event["demo_run"] = run_id
            source = demo.Client("http://127.0.0.1:19081", {
                "Authorization": f"Bearer {source_token}"
            })
            source.request("POST", f"/ingest/{flow_id}", events)
            headers, body = captures.get(timeout=30)
            assert headers["Content-Type"] == "application/x-ndjson", headers
            assert headers.get("Content-Encoding", "none") == compression, headers
            assert headers["Authorization"] == "Bearer synthetic-wire-test-key", headers
            assert headers["X-Wire-Test"] == "flowport-scopedb", headers
            points = [json.loads(line) for line in body.splitlines()]
            assert len(points) == len(events), points
            for point, event in zip(points, events):
                assert point["measurement"] == "app_logs", point
                assert point["category"] == "logging", point
                assert isinstance(point["timestamp"], int) and point["timestamp"] > 0, point
                for name in ("service", "env", "demo_run"):
                    assert point["tags"][name] == event[name], point
                for name in ("event_id", "message", "duration_ms"):
                    assert point["fields"][name] == event[name], point
            demo.wait_for("wire test queue to drain", lambda: True if demo.delivery_ready(
                flowport.request("GET", f"/flows/{flow_id}/observe")
            ) else None)
            print(f"PASS: real FlowPort output, {compression}, {len(points)} exact Points", flush=True)
        flowport.request("POST", f"/flows/{flow_id}/stop", {})
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


if __name__ == "__main__":
    main()
