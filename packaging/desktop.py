"""RHOBEAR Captur'd — desktop entry point.

Starts the FastAPI/uvicorn server on a background thread, then opens the UI in a
native WebView2 window (pywebview). Falls back to the default browser if no
webview backend is available. This is the PyInstaller entry script — see
packaging/sunsponge.spec.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}"


def _bundle_root() -> Path:
    """Where our data files live: the PyInstaller temp dir when frozen, else the repo."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def _configure_env() -> None:
    root = _bundle_root()
    # Tell the app where the bundled UI lives (see app.py UI_DIR).
    ui = root / "ui"
    if ui.is_dir():
        os.environ.setdefault("SUNSPONGE_UI_DIR", str(ui))
    # Bundled Playwright browsers live inside the package (PLAYWRIGHT_BROWSERS_PATH=0
    # at build time). Mirror that at runtime so the frozen app finds Chromium; if it
    # is missing the capture engine falls back to the system Edge channel.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


def _serve() -> None:
    import uvicorn

    from sunsponge.app import app  # import the app object directly (frozen-safe)

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_for_server(timeout: float = 20.0) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)  # noqa: S310 (localhost only)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> None:
    _configure_env()

    server = threading.Thread(target=_serve, daemon=True)
    server.start()
    _wait_for_server()

    try:
        import webview  # pywebview → native Edge WebView2 window on Windows

        webview.create_window("RHOBEAR Captur'd", URL, width=1280, height=860)
        webview.start()
    except Exception:
        import webbrowser

        webbrowser.open(URL)
        # Keep the process (and the server thread) alive when there's no GUI window.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
