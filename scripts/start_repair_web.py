#!/usr/bin/env python3
"""Cross-platform local web launcher for the repair panel.

The real repair operations are Windows-only. On Linux/macOS this server keeps the
web UI usable and returns clear unsupported-operation messages for API actions.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "repair.html"
SYSTEM = platform.system()


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def checked_at() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def status_payload() -> dict[str, object]:
    is_windows = SYSTEM == "Windows"
    home = Path.home()
    large_sessions: list[dict[str, object]] = []
    session_root = home / ".codex" / "sessions"
    if session_root.exists():
        for path in sorted(session_root.rglob("*.jsonl"), key=lambda p: p.stat().st_size, reverse=True)[:10]:
            size = path.stat().st_size
            if size > 5 * 1024 * 1024:
                large_sessions.append({"mb": round(size / 1024 / 1024, 2), "FullName": str(path)})

    return {
        "ok": True,
        "isAdmin": False,
        "sandbox": "unknown" if is_windows else f"unsupported on {SYSTEM}",
        "baseUrl": "unknown" if is_windows else "Windows-only repair backend",
        "tokenManaged": False,
        "ccSwitchHealthy": False,
        "ccSwitchRunning": False,
        "provider": "none",
        "lastError": None if is_windows else "Repair actions require Windows Codex Desktop.",
        "hasLoopback": False,
        "loopback": [],
        "hasPortproxy": False,
        "largeSessions": large_sessions,
        "proxyVars": [
            f"{name}={value}"
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
            if (value := os.environ.get(name))
        ],
        "guardInstalled": (home / ".codex" / "sandbox-guard.ps1").exists(),
        "guardRunning": False,
        "checkedAt": checked_at(),
    }


def diagnose_payload() -> dict[str, object]:
    log = [
        f"[{stamp()}] Web panel is running on {SYSTEM}.",
        f"[{stamp()}] Windows repair commands are not available from this launcher.",
        f"[{stamp()}] To perform repairs, run start-repair-web.bat on Windows as needed.",
    ]
    return {"ok": True, "log": log, "status": status_payload()}


def unsupported_action(action: str) -> dict[str, object]:
    log = [
        f"[{stamp()}] {action} was requested.",
        f"[{stamp()}] This action requires Windows Codex Desktop and the PowerShell repair backend.",
        f"[{stamp()}] On Ubuntu this launcher only starts the local web panel for viewing and diagnostics.",
    ]
    return {"ok": True, "log": log, "status": status_payload()}


class RepairHandler(BaseHTTPRequestHandler):
    server_version = "CodexRepairWeb/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, "application/json", body)

    def route(self, method: str) -> None:
        path = urlparse(self.path).path
        if method == "GET" and path in ("/", "/repair.html"):
            self.send_bytes(HTTPStatus.OK, "text/html", PAGE.read_bytes())
            return
        if method == "GET" and path == "/api/status":
            self.send_json(status_payload())
            return
        if method == "GET" and path == "/api/diagnose":
            self.send_json(diagnose_payload())
            return
        if method == "POST" and path.startswith("/api/"):
            self.send_json(unsupported_action(path.removeprefix("/api/")))
            return
        self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802 - http.server API name
        self.route("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server API name
        self.route("POST")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the local Codex repair web panel.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not PAGE.exists():
        print(f"Missing web page: {PAGE}", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), RepairHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Codex repair web panel: {url}")
    print("Press Ctrl+C to stop.")
    if SYSTEM != "Windows":
        print("Note: repair actions are Windows-only; Ubuntu can launch and view the web panel.")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
