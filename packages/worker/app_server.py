"""Local HTTP front end for the one-click app (stdlib only).

Binds 127.0.0.1 on a random port. Every /api call needs the per-launch key (read by the
page from the URL fragment, never sent in a URL) and a loopback Host header, so other
websites and DNS-rebinding pages cannot drive the button.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import signal
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from packages.worker.app import AppError, Bundle, Controller, find_bundle, write_private
from packages.worker.app_ui import PAGE
from packages.worker.lease_client import TransportError, WorkerApiError
from packages.worker.runtime import RUNTIME_FILE, find_pins, load_pins
from packages.worker.state import clear_pid, pid_alive, read_pid, state_dir, write_pid

log = logging.getLogger(__name__)
URL_FILE = "app.url"
MAX_BODY = 16 * 1024
REQUEST_TIMEOUT = 60


class AppServer:
    def __init__(
        self,
        controller: Controller,
        loop: asyncio.AbstractEventLoop,
        port: int = 0,
        stop: asyncio.Event | None = None,
    ):
        self.controller = controller
        self.loop = loop
        self.stop = stop
        self._desktop_task: asyncio.Task | None = None
        self._quitting = False
        self.key = secrets.token_urlsafe(24)
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/#k={self.key}"

    def serve_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def run(self, coro) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(REQUEST_TIMEOUT)

    async def _call(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        c = self.controller
        if route == "state":
            pass
        elif route == "toggle":
            if self._quitting:
                raise AppError("invalid_input", "The app is shutting down")
            c.start_toggle(body)
            await asyncio.sleep(0.05)
        elif route in ("pause", "shutdown"):
            mode = body.get("offline_mode", "finish")
            if mode not in ("finish", "now"):
                raise AppError("invalid_input", "offline_mode must be finish or now")
            if route == "shutdown" and self.stop is None:
                raise AppError("invalid_input", "Shutdown is unavailable")
            if self._desktop_task is None or self._desktop_task.done():
                self._quitting = route == "shutdown"
                self._desktop_task = asyncio.create_task(self._desktop_pause(mode))
            elif route == "shutdown":
                self._quitting = True  # exit after the existing drain completes
            await asyncio.sleep(0.05)
        elif route == "update_check":
            await c.updater.check()
        elif route == "update_prepare":
            c.updater.start_prepare()
            await asyncio.sleep(0.05)
        elif route == "cancel":
            await c.cancel()
        elif route == "refresh":
            await c.refresh()
        elif route == "settings":
            await c.update_settings(body)
        elif route == "invite":
            await c.rotate_invite()
        elif route == "member-limits":
            await c.set_member_limits(
                str(body.get("user_id", "")),
                {k: body.get(k) for k in ("max_tasks_per_day", "max_minutes_per_day")},
            )
        elif route == "remove-member":
            await c.remove_member(str(body.get("user_id", "")))
        elif route == "install-tool":
            c.start_tool_install(str(body.get("tool", "")))
        elif route == "remove-tool":
            c.remove_tool(str(body.get("tool", "")))
        elif route == "network":
            await c.set_network(str(body.get("network", "")))
        elif route == "dashboard":
            return {"url": await c.dashboard_link()}
        else:
            raise KeyError(route)
        return {
            **c.snapshot(),
            "desktop_protocol": 1,
            "desktop_app": os.environ.get("SPAREAGENT_NATIVE_APP") == "1",
        }

    async def _desktop_pause(self, mode: str) -> None:
        if self.controller.snap.phase == "STARTING":
            await self.controller.cancel()
        await self.controller.go_offline(mode)
        if self._quitting and self.stop is not None:
            # Let the initiating HTTP response finish before closing the HTTP server.
            await asyncio.sleep(0.1)
            self.stop.set()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SpareAgentApp"
            sys_version = ""

            def log_message(self, fmt, *args):  # noqa: D401 - quiet access log
                log.debug(fmt, *args)

            def _host_ok(self) -> bool:
                return self.headers.get("Host", "") in (
                    f"127.0.0.1:{server.port}",
                    f"localhost:{server.port}",
                )

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                    "connect-src 'self'; img-src data:; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, data: Any) -> None:
                self._send(status, json.dumps(data, default=str).encode(), "application/json")

            def do_GET(self):  # noqa: N802
                if not self._host_ok():
                    return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
                if self.path in ("/", "/index.html"):
                    return self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

            def do_POST(self):  # noqa: N802
                if not self._host_ok() or not hmac.compare_digest(
                    self.headers.get("X-App-Key", ""), server.key
                ):
                    return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "json"})
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "size"})
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "json"})
                route = self.path.removeprefix("/api/")
                try:
                    return self._json(HTTPStatus.OK, server.run(server._call(route, body)))
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                except AppError as exc:
                    err = {"code": exc.code, "detail": exc.detail}
                except WorkerApiError as exc:
                    err = {
                        "code": "server_rejected",
                        "server_code": exc.code,
                        "detail": exc.message,
                    }
                except TransportError as exc:
                    err = {"code": "server_unreachable", "detail": str(exc)}
                except (ValueError, TypeError) as exc:
                    err = {"code": "invalid_input", "detail": str(exc)}
                return self._json(HTTPStatus.CONFLICT, {"error": err})

        return Handler


async def _refresh_loop(controller: Controller, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await controller.refresh()
        try:
            await asyncio.wait_for(stop.wait(), 5)
        except TimeoutError:
            pass


async def run_app(
    state_directory: str | None,
    bundle_path: str | None,
    *,
    port: int = 0,
    open_browser: bool = True,
) -> int:
    directory = state_dir(state_directory)
    pid = read_pid(directory)
    url_file = directory / URL_FILE
    if pid_alive(pid) and pid != os.getpid():
        # Already running (app or `start`): show the existing panel instead.
        if url_file.exists():
            url = url_file.read_text(encoding="utf-8").strip()
            print(url)
            if open_browser:
                webbrowser.open(url)
            return 0
        print("error: a SpareAgent worker is already running on this device", flush=True)
        return 2
    found = find_bundle(directory, bundle_path)
    bundle = Bundle.load(found) if found else None
    if found and found.resolve() != (directory / "bundle.json").resolve():
        # Keep a private copy so later launches (and updates) need no path.
        write_private(directory / "bundle.json", found.read_text(encoding="utf-8"))
    pins_path = find_pins(directory, found)
    if pins_path and pins_path.resolve() != (directory / RUNTIME_FILE).resolve():
        write_private(directory / RUNTIME_FILE, pins_path.read_text(encoding="utf-8"))
    controller = Controller(directory, bundle, runtime_pins=load_pins(pins_path))
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    server = AppServer(controller, loop, port, stop=stop)
    write_pid(directory)
    write_private(url_file, server.url)
    # SIGHUP: the owner closed the Terminal window that runs the app.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, stop.set)
    server.serve_in_thread()
    print(f"SpareAgent 一键提供算力 / one-click sharing: {server.url}", flush=True)
    print("关闭此窗口或按 Ctrl-C 会自动下线。Closing this window goes offline.", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        await controller.startup()
        await _refresh_loop(controller, stop)
    finally:
        await controller.shutdown()
        server.close()
        Path(url_file).unlink(missing_ok=True)
        clear_pid(directory)
    return 0
