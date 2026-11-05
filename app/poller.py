"""Background polling loop for service health checks."""
import asyncio
import os

from . import db
from .checks import check_service

POLL_SECONDS = float(os.environ.get("DASH_POLL_SECONDS", "30"))
TRIM_EVERY = 100  # poll cycles between history trims


async def poll_once() -> int:
    services = db.list_services(enabled_only=True)
    if not services:
        return 0
    results = await asyncio.gather(*(check_service(s) for s in services), return_exceptions=True)
    n = 0
    for svc, res in zip(services, results):
        if isinstance(res, Exception):
            res = {"ok": False, "status_code": None, "latency_ms": None, "detail": f"checker error: {res}"}
        db.record_check(svc["id"], res)
        n += 1
    return n


async def poll_loop():
    cycle = 0
    while True:
        try:
            await poll_once()
        except Exception:
            pass
        cycle += 1
        if cycle % TRIM_EVERY == 0:
            try:
                db.trim_checks()
            except Exception:
                pass
        await asyncio.sleep(POLL_SECONDS)
