"""Mock of a commercial oil lab's REST API, so the pipeline and Power BI have something real to call.

GET /v1/samples?modified_since=<ISO timestamp>&page=<n>&page_size=<n>
    Header X-Api-Key required. Returns {"data": [...], "meta": {page, page_size, total, next_page}}.
GET /v1/health

Like many real APIs it occasionally answers 503, so clients need retries.

Run on its own (e.g. for Power BI Desktop):  py -m oilmon.mock_api
"""
from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config


class _Handler(BaseHTTPRequestHandler):
    server: "MockLabServer"

    def do_GET(self):  # noqa: N802 (http.server naming)
        url = urlparse(self.path)
        if url.path == "/v1/health":
            return self._send(200, {"status": "ok"})
        if url.path != "/v1/samples":
            return self._send(404, {"error": "not found"})
        if self.headers.get("X-Api-Key") != config.API_KEY:
            return self._send(401, {"error": "missing or invalid API key"})
        if self.server.flaky and self.server.rng.random() < 0.08:
            return self._send(503, {"error": "service temporarily unavailable"})

        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            page = max(1, int(q.get("page", 1)))
            page_size = min(200, max(1, int(q.get("page_size", 100))))
        except ValueError:
            return self._send(400, {"error": "page and page_size must be integers"})
        since = q.get("modified_since")
        rows = [r for r in self.server.feed if since is None or r["updated_at"] > since]
        chunk = rows[(page - 1) * page_size: page * page_size]
        more = page * page_size < len(rows)
        self._send(200, {"data": chunk, "meta": {"page": page, "page_size": page_size,
                                                 "total": len(rows), "next_page": page + 1 if more else None}})

    def _send(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep demo output quiet
        pass


class MockLabServer(ThreadingHTTPServer):
    def __init__(self, host=config.API_HOST, port=config.API_PORT, flaky=True, seed=1):
        super().__init__((host, port), _Handler)
        self.feed = json.loads((config.DATA_DIR / "lab_feed.json").read_text(encoding="utf-8"))
        self.flaky = flaky
        self.rng = random.Random(seed)

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread


if __name__ == "__main__":
    server = MockLabServer(flaky=False)
    print(f"Mock lab API on {config.API_BASE_URL}/v1/samples  (header X-Api-Key: {config.API_KEY})")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
