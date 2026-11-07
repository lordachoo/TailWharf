"""Entry point for the Tailnet AI Service Dashboard.

Configuration comes from environment variables, optionally loaded from a
local .env file (gitignored). See .env.example.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

# Windows consoles default to cp1252; never crash on our UTF-8 status glyphs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

TS_CANDIDATES = [
    r"C:\Program Files\Tailscale\tailscale.exe",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
]


def detect_tailnet_ip() -> str | None:
    """Ask the tailscale CLI for this machine's tailnet IP (100.x). None if unavailable."""
    import shutil
    binary = shutil.which("tailscale") or next((c for c in TS_CANDIDATES if Path(c).exists()), None)
    if not binary:
        return None
    try:
        out = subprocess.run([binary, "ip", "-4"], capture_output=True, text=True, timeout=5)
        ip = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
        return ip if ip.startswith("100.") else None
    except Exception:
        return None


def load_dotenv(path: Path = BASE_DIR / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars always win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def resolve_host(preferred: str) -> str:
    """Try to bind the preferred host; fall back to localhost if unavailable
    (e.g. different machine, or Tailscale not running yet)."""
    port = int(os.environ.get("DASH_PORT", "8088"))
    last_err = None
    for attempt in range(3):                      # survive restart TIME_WAIT races
        for port_try in (preferred, "127.0.0.1"):
            with socket.socket() as s:
                # Same option uvicorn's real server uses — without it a probe
                # bind fails with EADDRINUSE on a TIME_WAIT socket even though
                # nothing is listening (classic restart false-negative).
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((port_try, port))
                    if attempt:
                        print(f"bind to {port_try} succeeded on retry {attempt + 1}")
                    return port_try
                except OSError as e:
                    last_err = f"{port_try}: {e} (errno {e.errno})"
        time.sleep(1.0)
    print(f"⚠ could not bind preferred {last_err} — falling back to 127.0.0.1")
    return "127.0.0.1"


if __name__ == "__main__":
    load_dotenv()
    preferred = os.environ.get("DASH_HOST", "").strip()
    source = "DASH_HOST (.env/env)"
    if not preferred:
        preferred = detect_tailnet_ip() or "127.0.0.1"
        source = "auto-detected from tailscale CLI" if preferred != "127.0.0.1" else "no tailscale found"
    host = resolve_host(preferred)
    print(f"Bind source: {source}")
    port = int(os.environ.get("DASH_PORT", "8088"))
    print(f"Tailnet AI Dashboard -> http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")
