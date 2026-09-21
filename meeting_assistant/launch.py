"""Single-process local app launcher; serves the built frontend with the API."""
import argparse
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .config import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise SystemExit("Frontend is not built. Run install.ps1 first.")
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
    uvicorn.run("meeting_assistant.app:create_app", factory=True, host="127.0.0.1", port=8766, log_level="info", timeout_graceful_shutdown=20)


if __name__ == "__main__":
    main()
