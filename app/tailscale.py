"""Tailscale CLI integration (no API key required)."""
import asyncio
import json
import shutil
from pathlib import Path

_CANDIDATES = [
    shutil.which("tailscale"),
    str(Path(r"C:\Program Files\Tailscale\tailscale.exe")),
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
]


def ts_binary():
    for c in _CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


async def tailnet_status() -> dict:
    """Return {available, self, peers} from `tailscale status --json`."""
    binary = ts_binary()
    if not binary:
        return {"available": False, "self": None, "peers": []}
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "status", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return {"available": False, "error": err.decode(errors="replace")[:200], "self": None, "peers": []}
        st = json.loads(out)
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}", "self": None, "peers": []}

    def map_dev(d: dict, is_self: bool):
        ips = d.get("TailscaleIPs", [])
        return {
            "ip": next((a for a in ips if ":" not in a), ips[0] if ips else None),
            "ipv6": next((a for a in ips if ":" in a), None),
            "hostname": d.get("HostName", ""),
            "dns_name": (d.get("DNSName") or "").rstrip("."),
            "os": d.get("OS", ""),
            "online": True if is_self else bool(d.get("Online", False)),
            "last_seen": None if is_self else _clean_ts(d.get("LastSeen")),
            "relay": d.get("Relay", ""),
            "is_self": is_self,
            "tags": d.get("Tags") or [],
        }

    self_info = st.get("Self")
    peers = [map_dev(p, False) for p in st.get("Peer", {}).values()]
    peers.sort(key=lambda p: p["hostname"].lower())
    return {
        "available": True,
        "self": map_dev(self_info, True) if self_info else None,
        "peers": peers,
        "tailnet_name": st.get("Tailnet", {}).get("Name") if isinstance(st.get("Tailnet"), dict) else None,
    }


def _clean_ts(v):
    if not v or str(v).startswith("0001-01-01"):
        return None
    return v
