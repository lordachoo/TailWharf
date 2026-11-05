"""Tailnet AI Service Dashboard - FastAPI app."""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, discovery, mcp_hub, poller
from .checks import LINK_PRESETS, PRESETS, apply_preset, check_service
from .tailscale import hostname_map, tailnet_status

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    db.seed_if_empty()
    task = asyncio.create_task(poller.poll_loop())
    yield
    task.cancel()


app = FastAPI(title="Tailnet AI Dashboard", lifespan=lifespan)


# ---------- schemas ----------

class LinkIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=4, max_length=300)


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str = "http"
    url: str = Field(min_length=3, max_length=300)
    method: str = "GET"
    expected_status: int = 200
    header_name: str | None = None
    header_value: str | None = None
    enabled: bool = True
    notes: str | None = None
    links: list[LinkIn] | None = None

    def normalized(self) -> dict:
        url = self.url.strip()
        if "://" not in url:
            url = "http://" + url
        d = self.model_dump()
        d["url"] = apply_preset(url, self.type)
        d["enabled"] = 1 if self.enabled else 0
        d["links"] = json.dumps([l.model_dump() for l in self.links]) if self.links else None
        return d


# ---------- API ----------

@app.get("/api/meta")
async def meta():
    return {
        "presets": {k: v for k, v in PRESETS.items()},
        "link_presets": LINK_PRESETS,
        "poll_seconds": poller.POLL_SECONDS,
        "discovery": discovery.STATE,
    }


@app.get("/api/overview")
async def overview():
    services = db.list_services()
    up = down = unknown = 0
    for s in services:
        lc = db.last_check(s["id"])
        if lc is None:
            unknown += 1
        elif lc["ok"]:
            up += 1
        else:
            down += 1
    tn = await tailnet_status()
    online = sum(1 for p in tn["peers"] if p["online"]) if tn["available"] else 0
    return {
        "services_total": len(services),
        "up": up, "down": down, "unknown": unknown,
        "devices_online": online,
        "devices_total": len(tn["peers"]) if tn["available"] else 0,
        "tailscale_available": tn["available"],
        "discoveries": len(db.list_discoveries()),
        "discovery_running": discovery.STATE["running"],
    }


@app.get("/api/services")
async def list_services():
    hosts = await hostname_map()
    out = []
    for s in db.list_services():
        lc = db.last_check(s["id"])
        try:
            host = urlparse(s["url"]).hostname or ""
        except ValueError:
            host = ""
        out.append({
            **{k: v for k, v in s.items() if k != "header_value"},
            "has_auth": bool(s.get("header_name")),
            "hostname": hosts.get(host),
            "links": _parse_links(s.get("links")),
            "last_check": lc,
            "uptime24": db.uptime_pct(s["id"], 24),
            "spark": db.history(s["id"], 24, limit=120),
        })
    return out


def _public(svc: dict) -> dict:
    """Never expose stored auth header values through the API."""
    if not svc:
        return svc
    out = dict(svc)
    out["has_auth"] = bool(out.pop("header_value", None))
    if "links" not in out:
        out["links"] = _parse_links(out.get("links"))
    return out


def _parse_links(v):
    if not v:
        return []
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else []
    except ValueError:
        return []


@app.post("/api/services", status_code=201)
async def create_service(s: ServiceIn):
    sid = db.add_service(s.normalized())
    svc = db.get_service(sid)
    asyncio.create_task(_check_and_record(svc))
    return _public(svc)


@app.get("/api/services/{sid}")
async def get_service(sid: int):
    svc = db.get_service(sid)
    if not svc:
        raise HTTPException(404, "service not found")
    svc["history"] = db.history(sid, 24)
    svc["uptime24"] = db.uptime_pct(sid, 24)
    svc["links"] = _parse_links(svc.get("links"))
    return _public(svc)


@app.put("/api/services/{sid}")
async def update_service(sid: int, s: ServiceIn):
    d = s.normalized()
    # blank password field in the edit form means "keep existing secret", not "delete it"
    if not d.get("header_value"):
        existing = db.get_service(sid)
        if existing:
            d["header_value"] = existing.get("header_value")
    ok = db.update_service(sid, d)
    if not ok:
        raise HTTPException(404, "service not found")
    return _public(db.get_service(sid))


@app.delete("/api/services/{sid}")
async def delete_service(sid: int):
    if not db.delete_service(sid):
        raise HTTPException(404, "service not found")
    return {"deleted": sid}


@app.post("/api/services/{sid}/check")
async def check_now(sid: int):
    svc = db.get_service(sid)
    if not svc:
        raise HTTPException(404, "service not found")
    return await _check_and_record(svc)


async def _check_and_record(svc):
    try:
        res = await check_service(svc)
    except Exception as e:
        res = {"ok": False, "status_code": None, "latency_ms": None, "detail": str(e)}
    db.record_check(svc["id"], res)
    return {"service_id": svc["id"], **res}


@app.get("/api/tailnet")
async def tailnet():
    return await tailnet_status()


# ---------- discovery ----------

@app.get("/api/discovery")
async def get_discovery():
    services = db.list_services()
    known = {(s["url"], ) for s in services}
    items = []
    for d in db.list_discoveries():
        already = any(
            f"{d['ip']}:{d['port']}" in (s["url"] or "") for s in services
        )
        items.append({**d, "already_added": already})
    return {"state": discovery.STATE, "items": items}


@app.get("/api/inspect")
async def inspect_endpoint(url: str):
    """Live endpoint deep-probe: identity, version, served models, path map."""
    return await discovery.inspect_endpoint(url)


@app.get("/api/harness-config")
async def harness_config(url: str):
    """Everything an AI harness needs to use this model endpoint."""
    info = await discovery.inspect_endpoint(url)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error", "unreachable")}
    kind = info["kind"]
    origin = info["url"].rstrip("/")
    openai_base = f"{origin}/v1"
    key_hint = {"ollama": "ollama", "llamaswap": "as configured in llama-swap",
                "litellm": "LiteLLM virtual key (or master key)",
                "vllm": "as served (--api-key or 'not-needed')"}.get(kind, "not-needed")
    return {"ok": True, "kind": kind, "openai_base": openai_base,
            "models": info.get("models") or [], "key_hint": key_hint,
            "http_status": info.get("http_status"),
            "hostname": (await hostname_map()).get(
                urlparse(origin).hostname or "")}


@app.post("/api/discovery/run")
async def run_discovery():
    started = await discovery.start_discovery()
    if not started:
        return {"started": False, "message": "discovery already running"}
    return {"started": True}


@app.post("/api/discovery/{did}/add")
async def add_from_discovery(did: int):
    d = db.get_discovery(did)
    if not d:
        raise HTTPException(404, "discovery not found")
    stype = d["kind"] or "http"
    label = d.get("title") or stype
    name = f"{d['hostname'] or d['ip']}:{d['port']} ({label})"
    url = d["url"].rstrip("/")
    # point the new service straight at the working health endpoint found during discovery
    hp = d.get("health_path")
    if hp and hp != "/":
        url += hp
    svc = ServiceIn(name=name, type=stype, url=url)
    sid = db.add_service(svc.normalized())
    svc = db.get_service(sid)
    asyncio.create_task(_check_and_record(svc))
    return _public(svc)


@app.delete("/api/discovery/{did}")
async def dismiss_discovery(did: int):
    if not db.dismiss_discovery(did):
        raise HTTPException(404, "discovery not found")
    return {"dismissed": did}


# ---------- registry export / import ----------

@app.get("/api/export")
async def export_registry(request: Request, secrets: bool = False):
    data = db.export_services(include_secrets=bool(secrets))
    data["secrets_included"] = bool(secrets)
    return JSONResponse(data, headers={
        "Content-Disposition": 'attachment; filename="tailnet-dash-services.json"'})


@app.post("/api/import")
async def import_registry(body: dict):
    services = body.get("services")
    if not isinstance(services, list):
        raise HTTPException(422, "expected {services: [...]}")
    mode = body.get("mode", "merge")
    if mode not in ("merge", "replace"):
        raise HTTPException(422, "mode must be 'merge' or 'replace'")
    return db.import_services(services, mode)


# Always revalidate UI assets: browser gets cheap 304s, never a stale app.js
@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    response = await call_next(request)
    p = request.url.path
    if p in ("", "/", "/index.html") or p.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


# MCP hub (POST /mcp + /api/mcp/*) then static SPA last so /api/* wins
app.include_router(mcp_hub.router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
