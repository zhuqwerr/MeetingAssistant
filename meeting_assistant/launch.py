"""Run the local app in browser-development or Electron desktop mode."""
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .config import FRONTEND_DIST


class ReadyServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            # stdout is an IPC channel for Electron. Keep all regular server logs
            # on stderr and emit exactly one machine-readable readiness record.
            print(json.dumps({"type": "meeting-assistant-ready", "port": self.config.port}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--electron", action="store_true")
    args = parser.parse_args()
    if not FRONTEND_DIST.joinpath("index.html").exists():
        raise SystemExit(f"Frontend is not built: {FRONTEND_DIST}")

    if args.electron:
        if not os.environ.get("MEETING_ASSISTANT_DESKTOP_TOKEN"):
            raise SystemExit("Electron launch requires MEETING_ASSISTANT_DESKTOP_TOKEN")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            listener.setblocking(False)
            os.environ["MEETING_ASSISTANT_PORT"] = str(port)

            from .app import create_app

            app = create_app()
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info",
                                    access_log=False, timeout_graceful_shutdown=25)
            server = ReadyServer(config)
            app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
            server.run(sockets=[listener])
        return

    if not args.no_browser:
        def open_when_ready():
            for _ in range(60):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8766/api/health", timeout=1):
                        webbrowser.open("http://127.0.0.1:8766")
                        return
                except OSError:
                    time.sleep(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run("meeting_assistant.app:create_app", factory=True, host="127.0.0.1", port=8766,
                log_level="info", timeout_graceful_shutdown=20)


if __name__ == "__main__":
    main()
