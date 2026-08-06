"""Tiny localhost capture server for browser-assisted ingestion.

Several eazli pages (FAQ accordions, all legal/policy pages) render their body
text client-side, so `curl` only ever sees nav + footer. We drive the real
browser instead and have the page POST what it rendered back to here.

Chrome treats http://localhost as a potentially-trustworthy origin, so an
HTTPS page can fetch() it without tripping mixed-content blocking. We just
have to answer CORS preflight ourselves.

Usage:
    python ingest/collector.py            # listens on :8765, writes kb/raw/
    python ingest/collector.py --port 9000 --out somewhere/

From the page:
    fetch('http://localhost:8765/capture', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: 'faq', data: [...]}),
    })
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


class CaptureHandler(BaseHTTPRequestHandler):
    out_dir = Path("kb/raw")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # Chrome's Private Network Access check: a public-origin page (eazli.com
        # over https) reaching a loopback address is blocked outright unless the
        # local server explicitly opts in on the preflight.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            name = SAFE_NAME.sub("-", str(payload.get("name", "capture")).lower())
            path = self.out_dir / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload.get("data"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            body = json.dumps({"ok": True, "wrote": str(path), "bytes": len(raw)})
            print(f"captured {len(raw):>8,} bytes -> {path}", flush=True)
            status = 200
        except Exception as exc:  # surface the error to the page, keep serving
            body = json.dumps({"ok": False, "error": str(exc)})
            print(f"capture failed: {exc}", flush=True)
            status = 400

        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args) -> None:
        """Silence the default per-request logging; we print our own."""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", default="kb/raw")
    args = parser.parse_args()

    CaptureHandler.out_dir = Path(args.out)
    CaptureHandler.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"collector listening on http://localhost:{args.port} -> {args.out}/")
    HTTPServer(("127.0.0.1", args.port), CaptureHandler).serve_forever()


if __name__ == "__main__":
    main()
