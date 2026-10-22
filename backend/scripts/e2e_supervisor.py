"""Runs uvicorn for end-to-end tests and restarts it when a marker file appears.

Lets a Playwright test prove a real process restart: the paused workflow must resume from the
stored state, not from memory.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

marker = Path(os.environ["E2E_RESTART_MARKER"])
command = [
    sys.executable,
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "18000",
]
child: subprocess.Popen | None = None


def start() -> None:
    global child
    child = subprocess.Popen(command)


def stop() -> None:
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(10)
        except subprocess.TimeoutExpired:
            child.kill()


signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
start()
while True:
    time.sleep(0.2)
    if marker.exists():
        marker.unlink()
        stop()
        start()
    elif child.poll() is not None:
        sys.exit(child.returncode)
