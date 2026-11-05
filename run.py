"""Entry point for the Tailnet AI Service Dashboard.

Configuration comes from environment variables, optionally loaded from a
local .env file (gitignored). See .env.example.
"""
import os
import socket
import subprocess
import sys
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
    for port_try in (preferred, "127.0.0.1"):
        with socket.socket() as s:
            try:
                s.bind((port_try, int(os.environ.get("DASH_PORT", "8088"))))
                if port_try != preferred:
                    print(f"⚠ could not bind {preferred} (not a local address / Tailscale down?) "
                          f"— falling back to {port_try}")
                return port_try
            except OSError:
                continue
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
