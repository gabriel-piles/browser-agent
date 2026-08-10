"""Lightweight HTTP fixture server for robustness scenarios.

Serves HTML fixtures from ``scripts/fixtures/<scenario>/`` on a
local port so the generation pipeline can be tested against
deterministic, reproducible site patterns. No network dependency,
no rate limits, no site drift.

Each scenario directory may contain:
- Static ``.html`` files served verbatim by relative path.
- A ``manifest.json`` with scenario metadata (read by the runner).
- PDF fixture files served with ``Content-Type: application/pdf``.

Dynamic routes for SPA/scroll/filter scenarios are handled by
query-param dispatch (``?page=N``, ``?filter=value``) rendered
server-side from the fixture directory's HTML templates.

Start as: ``python scripts/fixture_server.py``
The port is a module-level constant per AGENTS.md (no CLI args).
"""

from __future__ import annotations

import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURE_HOST = "127.0.0.1"
FIXTURE_PORT = 8765
FIXTURES_ROOT = Path(__file__).parent / "fixtures"
_PORT_RANGE = 10

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


def _find_free_port() -> int:
    """Return the first free port in ``[FIXTURE_PORT, FIXTURE_PORT + range)``."""
    for port in range(FIXTURE_PORT, FIXTURE_PORT + _PORT_RANGE):
        if _is_port_free(port):
            return port
    raise RuntimeError(f"No free port in {FIXTURE_PORT}–{FIXTURE_PORT + _PORT_RANGE - 1}")


def _is_port_free(port: int) -> bool:
    """True when ``port`` is bindable on ``FIXTURE_HOST``."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((FIXTURE_HOST, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _fixture_dir(scenario: str) -> Path:
    """Return the fixture directory for ``scenario``."""
    return FIXTURES_ROOT / scenario


def _render_page(scenario_dir: Path, page_name: str) -> bytes:
    """Read a static HTML file from the scenario directory."""
    path = scenario_dir / page_name
    if not path.is_file():
        raise FileNotFoundError(page_name)
    return path.read_bytes()


class FixtureHandler(BaseHTTPRequestHandler):
    """Route by path + query params, serving static + dynamic fixtures."""

    def do_GET(self) -> None:
        """Handle GET: parse path/query, serve the matching fixture."""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        scenario = query.get("scenario", [None])[0]
        if scenario is None:
            self._serve_root()
            return
        scenario_dir = _fixture_dir(scenario)
        if not scenario_dir.is_dir():
            self._send_404(f"scenario '{scenario}' not found")
            return
        try:
            body, mime, status = self._route_with_status(scenario_dir, path, query)
        except FileNotFoundError as exc:
            self._send_404(str(exc))
            return
        if status == 200:
            self._send_ok(mime, body)
        elif status == 403:
            self._send_status(403, mime, body)
        elif status == 503:
            self._send_status(503, mime, body)
        else:
            self._send_ok(mime, body)

    def _serve_root(self) -> None:
        """Serve a simple index listing available scenarios."""
        scenarios = []
        if FIXTURES_ROOT.is_dir():
            scenarios = sorted(d.name for d in FIXTURES_ROOT.iterdir() if d.is_dir())
        html = "<html><body><h1>Fixture Server</h1><ul>"
        for s in scenarios:
            html += f"<li><a href='/?scenario={s}'>{s}</a></li>"
        html += "</ul></body></html>"
        self._send_ok("text/html; charset=utf-8", html.encode())

    def _route_with_status(self, scenario_dir: Path, path: str, query: dict[str, list[str]]) -> tuple[bytes, str, int]:
        """Dispatch by path; return (body, mime, status_code)."""
        dynamic = scenario_dir / "_dynamic.py"
        if dynamic.is_file() and _has_custom_route(scenario_dir):
            result = _call_custom_route(scenario_dir, path, query)
            if result is not None:
                body, mime, status = result
                return body, mime, status
        if path == "/" or path == "/index.html":
            body, mime = self._serve_index(scenario_dir, query)
            return body, mime, 200
        if path.startswith("/file/"):
            body, mime = self._serve_file(scenario_dir, path)
            return body, mime, 200
        if path.startswith("/pdf/"):
            body, mime = self._serve_pdf(scenario_dir, path)
            return body, mime, 200
        body, mime = self._serve_static(scenario_dir, path)
        return body, mime, 200

    def _route(self, scenario_dir: Path, path: str, query: dict[str, list[str]]) -> tuple[bytes, str]:
        """Dispatch by path: static file, dynamic page, or PDF route."""
        body, mime, _ = self._route_with_status(scenario_dir, path, query)
        return body, mime

    def _serve_index(self, scenario_dir: Path, query: dict[str, list[str]]) -> tuple[bytes, str]:
        """Serve index.html, or a dynamic variant for paginated/filtered scenarios."""
        dynamic = scenario_dir / "_dynamic.py"
        if dynamic.is_file():
            body = _call_dynamic(scenario_dir, query)
            return body, "text/html; charset=utf-8"
        return _render_page(scenario_dir, "index.html"), "text/html; charset=utf-8"

    def _serve_fragment(self, scenario_dir: Path, path: str, query: dict[str, list[str]]) -> tuple[bytes, str]:
        """Serve an AJAX HTML fragment (for infinite scroll scenarios)."""
        dynamic = scenario_dir / "_dynamic.py"
        if dynamic.is_file():
            body = _call_dynamic(scenario_dir, query, route="fragment")
            return body, "text/html; charset=utf-8"
        raise FileNotFoundError("fragment route without _dynamic.py")

    def _serve_pdf(self, scenario_dir: Path, path: str) -> tuple[bytes, str]:
        """Serve a PDF fixture file from the scenario directory."""
        name = path.split("/")[-1]
        pdf_path = scenario_dir / name
        if not pdf_path.is_file():
            raise FileNotFoundError(name)
        return pdf_path.read_bytes(), "application/pdf"

    def _serve_file(self, scenario_dir: Path, path: str) -> tuple[bytes, str]:
        """Serve any file from the scenario directory by its basename."""
        name = path.split("/")[-1]
        file_path = scenario_dir / name
        if not file_path.is_file():
            raise FileNotFoundError(name)
        suffix = file_path.suffix.lower()
        mime = _MIME.get(suffix, "application/octet-stream")
        return file_path.read_bytes(), mime

    def _serve_static(self, scenario_dir: Path, path: str) -> tuple[bytes, str]:
        """Serve any static file by its relative path under the scenario dir."""
        rel = path.lstrip("/")
        file_path = scenario_dir / rel
        if not file_path.is_file():
            raise FileNotFoundError(rel)
        suffix = file_path.suffix.lower()
        mime = _MIME.get(suffix, "application/octet-stream")
        return file_path.read_bytes(), mime

    def _send_ok(self, mime: str, body: bytes) -> None:
        """Send a 200 response with the given MIME type and body."""
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_status(self, status: int, mime: str, body: bytes) -> None:
        """Send a response with a custom status code."""
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_404(self, msg: str) -> None:
        """Send a 404 with a short message."""
        body = msg.encode()
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        """Suppress default logging to keep stdout clean."""
        pass


_DYNAMIC_CACHE: dict[str, object] = {}


def _load_dynamic(scenario_dir: Path):
    """Load and cache the scenario's _dynamic.py module."""
    import importlib.util

    key = str(scenario_dir)
    if key in _DYNAMIC_CACHE:
        return _DYNAMIC_CACHE[key]
    spec = importlib.util.spec_from_file_location("_dynamic", scenario_dir / "_dynamic.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError("_dynamic.py not loadable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _DYNAMIC_CACHE[key] = mod
    return mod


def _call_dynamic(scenario_dir: Path, query: dict[str, list[str]], route: str = "index") -> bytes:
    """Import and call the scenario's ``_dynamic.py`` renderer."""
    mod = _load_dynamic(scenario_dir)
    fn = getattr(mod, route, None)
    if fn is None:
        raise FileNotFoundError(f"_dynamic.py has no '{route}' function")
    return fn(query).encode("utf-8")


def _has_custom_route(scenario_dir: Path) -> bool:
    """True when the scenario's _dynamic.py defines a ``custom_route`` function."""
    try:
        mod = _load_dynamic(scenario_dir)
    except FileNotFoundError:
        return False
    return hasattr(mod, "custom_route")


def _call_custom_route(scenario_dir: Path, path: str, query: dict[str, list[str]]) -> tuple[bytes, str, int] | None:
    """Call the scenario's ``custom_route(path, query)`` if it exists."""
    try:
        mod = _load_dynamic(scenario_dir)
    except FileNotFoundError:
        return None
    fn = getattr(mod, "custom_route", None)
    if fn is None:
        return None
    result = fn(path, query)
    if result is None:
        return None
    body, mime, status = result
    if isinstance(body, str):
        body = body.encode("utf-8")
    return body, mime, status


def start_server(port: int | None = None) -> HTTPServer:
    """Start the fixture server on ``port`` (or the first free port)."""
    from http.server import ThreadingHTTPServer

    actual_port = port or _find_free_port()
    server = ThreadingHTTPServer((FIXTURE_HOST, actual_port), FixtureHandler)
    return server


def main() -> None:
    """Start the fixture server and serve until interrupted."""
    env_port = os.environ.get("FIXTURE_PORT")
    port = int(env_port) if env_port else _find_free_port()
    server = start_server(port)
    print(f"[fixture-server] serving on http://{FIXTURE_HOST}:{port}")
    print(f"[fixture-server] fixtures root: {FIXTURES_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[fixture-server] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
