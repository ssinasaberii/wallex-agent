"""Local dashboard server: http://127.0.0.1:<port>/?t=<token> (python -m wallex_agent dashboard).

Binds to 127.0.0.1 only. Every /api request needs the per-run token (header X-Token) and a localhost Host header,
so a web page from elsewhere cannot read or change anything (CSRF / DNS rebinding).
"""

from __future__ import annotations

import json
import logging
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from wallex_agent.dashboard import service

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


def make_handler(token: str, port: int):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: one line per request would flood the console
            return

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

        def _allowed(self) -> bool:
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc not in hosts:
                return False
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                raise ValueError("too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                if self.headers.get("Host") not in hosts:
                    return self._send(403, b"forbidden", "text/plain")
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._send(404, b"not found", "text/plain")
            if not self._allowed():
                return self._json({"error": "forbidden"}, 403)
            try:
                if path == "/api/status":
                    return self._json(service.status())
                if path == "/api/config":
                    return self._json(service.config_view())
                if path == "/api/history":
                    return self._json(service.history())
                if path.startswith("/api/backtest/"):
                    return self._json(service.backtest_result(path.rsplit("/", 1)[1]))
            except Exception as e:  # noqa: BLE001
                log.exception("dashboard GET %s", path)
                return self._json({"error": type(e).__name__}, 500)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if not self._allowed():
                return self._json({"error": "forbidden"}, 403)
            try:
                b = self._body()
                if path == "/api/config":
                    return self._json(service.apply_changes(b.get("changes") or {}, bool(b.get("confirm")),
                                                            bool(b.get("dry"))))
                if path == "/api/backtest":
                    return self._json(service.start_backtest(b.get("changes") or {}))
                if path == "/api/control":
                    if not b.get("confirm"):
                        return self._json({"ok": False, "message": "تأیید لازم است"}, 400)
                    return self._json(service.control(str(b.get("action"))))
            except Exception as e:  # noqa: BLE001
                log.exception("dashboard POST %s", path)
                return self._json({"ok": False, "errors": [f"خطای داخلی: {type(e).__name__}"]}, 500)
            return self._json({"error": "not found"}, 404)

    return H


def serve(port: int = 8765, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(18)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, port))
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"Dashboard: {url}", flush=True)
    (service.ROOT / "reports").mkdir(exist_ok=True)
    (service.ROOT / "reports" / "dashboard_url.txt").write_text(url + "\n", encoding="utf-8")
    if open_browser:
        webbrowser.open(url)
    httpd.serve_forever()
