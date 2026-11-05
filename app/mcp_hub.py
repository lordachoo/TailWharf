"""MCP hub: exposes dashboard + AI-Net tools as a streamable-HTTP MCP server.

Endpoint:  POST /mcp   (JSON-RPC 2.0, application/json responses, stateless)
Auth:      if MCP_TOKEN env is set, clients must send  Authorization: Bearer <MCP_TOKEN>

Admin API for the dashboard tab: /api/mcp  (status, tools, toggle, test, call log)
"""
import asyncio
import json
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db
from .checks import check_service
from .tailscale import tailnet_status

router = APIRouter()

PROTOCOL_FALLBACK = "2025-03-26"
SERVER_INFO = {"name": "tailnet-dash-mcp", "version": "1.0.0"}

SEARXNG_URL = os.environ.get("SEARXNG_URL", "")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "")
CRAWL4AI_KEY = os.environ.get("CRAWL4AI_API_KEY", "")
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")

DISABLED_KEY = "mcp_disabled_tools"


def _disabled() -> list:
    try:
        return json.loads(db.get_setting(DISABLED_KEY, "[]"))
    except ValueError:
        return []


# ---------------- tool implementations ----------------

async def tool_web_search(args: dict) -> str:
    if not SEARXNG_URL or "REPLACE-ME" in SEARXNG_URL:
        return ("SearXNG is not configured on this hub yet. Set SEARXNG_URL in "
                "the dashboard's .env and restart.")
    query = args["query"]
    n = int(args.get("n", 8))
    try:
        r = await httpx.AsyncClient(timeout=20).get(
            f"{SEARXNG_URL}/search", params={"q": query, "format": "json"})
        r.raise_for_status()
        results = r.json().get("results", [])[: max(1, n)]
    except Exception as e:
        return f"web_search failed: {type(e).__name__}: {e}"
    if not results:
        return f"No results for {query!r}."
    return "\n\n".join(
        f"[{i}] {x.get('title','')}\n    {x.get('url','')}\n    {(x.get('content') or '').strip()[:280]}"
        for i, x in enumerate(results, 1))


async def tool_web_fetch(args: dict) -> str:
    if not CRAWL4AI_URL or "REPLACE-ME" in CRAWL4AI_URL:
        return ("Crawl4AI is not configured on this hub yet. Set CRAWL4AI_URL "
                "(and CRAWL4AI_API_KEY if required) in the dashboard's .env and restart.")
    if not CRAWL4AI_KEY:
        return "web_fetch failed: CRAWL4AI_API_KEY not configured on the hub (.env)"
    mx = max(500, int(args.get("max_chars", 12000)))
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{CRAWL4AI_URL}/md",
                                  headers={"Authorization": f"Bearer {CRAWL4AI_KEY}"},
                                  json={"url": args["url"], "output_format": ["markdown"]})
        r.raise_for_status()
        md = (r.json().get("markdown") or "").strip()
    except Exception as e:
        return f"web_fetch failed: {type(e).__name__}: {e}"
    return md[:mx] + (f"\n…[truncated, {len(md)} chars]" if len(md) > mx else md)


async def tool_tailnet_status(_args: dict) -> str:
    st = await tailnet_status()
    if not st["available"]:
        return f"tailscale unavailable: {st.get('error', 'cli not found')}"
    peers = [{k: p[k] for k in ("ip", "hostname", "os", "online")} for p in st["peers"]]
    online = [p for p in peers if p["online"]]
    return json.dumps({"devices_total": len(peers), "online": len(online),
                       "peers_online": online, "peers_offline": [p["hostname"] for p in peers if not p["online"]]},
                      indent=1)


async def tool_services_status(_args: dict) -> str:
    out = []
    for s in db.list_services():
        lc = db.last_check(s["id"])
        out.append({"id": s["id"], "name": s["name"], "type": s["type"], "url": s["url"],
                    "up": bool(lc and lc["ok"]), "last_status_code": lc and lc["status_code"],
                    "latency_ms": lc and lc["latency_ms"],
                    "uptime24": db.uptime_pct(s["id"], 24)})
    return json.dumps(out, indent=1)


async def tool_check_service(args: dict) -> str:
    svc = db.get_service(int(args["service_id"]))
    if not svc:
        return f"no service with id {args['service_id']}"
    res = await check_service(svc)
    db.record_check(svc["id"], res)
    return json.dumps({"service": svc["name"], **res}, indent=1, default=str)


async def tool_discovery_run(_args: dict) -> str:
    from . import discovery
    started = await discovery.start_discovery()
    if not started and discovery.STATE["running"]:
        return "a discovery scan is already running; call discovery_list afterwards"
    for _ in range(60):  # wait up to ~60 s
        await asyncio.sleep(1)
        if not discovery.STATE["running"]:
            break
    return json.dumps({"ran": True, "open_ports_found": discovery.STATE["last_count"],
                       "last_run": discovery.STATE["last_run"]})


async def tool_discovery_list(_args: dict) -> str:
    items = [{"ip": d["ip"], "hostname": d["hostname"], "port": d["port"],
              "kind": d["kind"], "title": d.get("title"), "health_path": d.get("health_path"),
              "http_status": d["http_status"]} for d in db.list_discoveries()]
    return json.dumps(items, indent=1)


TOOLS = {
    "web_search": dict(fn=tool_web_search, description="Search the web via self-hosted SearXNG metasearch (title/URL/snippet per result).",
                       schema={"type": "object", "properties": {
                           "query": {"type": "string"}, "n": {"type": "integer", "default": 8}},
                           "required": ["query"]}),
    "web_fetch": dict(fn=tool_web_fetch, description="Read any URL as clean markdown via self-hosted Crawl4AI.",
                      schema={"type": "object", "properties": {
                          "url": {"type": "string"}, "max_chars": {"type": "integer", "default": 12000}},
                          "required": ["url"]}),
    "tailnet_status": dict(fn=tool_tailnet_status, description="Tailscale tailnet device inventory: online/offline counts and details.",
                           schema={"type": "object", "properties": {}}),
    "services_status": dict(fn=tool_services_status, description="All monitored AI services with health, latency and 24h uptime.",
                            schema={"type": "object", "properties": {}}),
    "check_service": dict(fn=tool_check_service, description="Run an immediate health check on one service by id (see services_status).",
                          schema={"type": "object", "properties": {"service_id": {"type": "integer"}},
                                  "required": ["service_id"]}),
    "discovery_run": dict(fn=tool_discovery_run, description="Run tailnet service discovery scan (waits until done, ~15 s).",
                          schema={"type": "object", "properties": {}}),
    "discovery_list": dict(fn=tool_discovery_list, description="List discovered endpoints on the tailnet with detected kinds.",
                           schema={"type": "object", "properties": {}}),
}


async def run_tool(name: str, args: dict, client: str) -> tuple[bool, str]:
    t0 = time.perf_counter()
    ok, text = True, ""
    try:
        if name not in TOOLS or name in _disabled():
            ok, text = False, f"tool not available: {name}"
        else:
            text = await TOOLS[name]["fn"](args or {})
    except Exception as e:
        ok, text = False, f"tool error: {type(e).__name__}: {e}"
    ms = round((time.perf_counter() - t0) * 1000, 1)
    summary = f"{name} " + ", ".join(f"{k}={str(v)[:60]}" for k, v in (args or {}).items())
    db.log_mcp_call(name, ok, ms, client, summary)
    return ok, text


# ---------------- MCP streamable-HTTP endpoint ----------------

def _rpc_error(rid, code, message, http=200):
    return JSONResponse({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": code, "message": message}}, status_code=http)


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    # bearer auth (only when MCP_TOKEN configured)
    if MCP_TOKEN:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {MCP_TOKEN}":
            return _rpc_error(None, -32001, "unauthorized: set Authorization: Bearer <MCP_TOKEN>", http=401)

    try:
        req = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "parse error")

    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}
    client = request.client.host if request.client else "?"

    if method == "initialize":
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_FALLBACK),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": "Tailnet AI hub: web search/fetch via self-hosted SearXNG+Crawl4AI, "
                            "plus tailnet/service health tools."}})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return JSONResponse({}, status_code=202)
    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {}})
    if method == "tools/list":
        dis = _disabled()
        tools = [{"name": n, "description": t["description"], "inputSchema": t["schema"],
                  "annotations": {"readOnlyHint": n not in ("discovery_run", "check_service"),
                                  "openWorldHint": n.startswith("web_")}}
                 for n, t in TOOLS.items() if n not in dis]
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
    if method == "tools/call":
        name, args = params.get("name", ""), params.get("arguments") or {}
        ok, text = await run_tool(name, args, client)
        return JSONResponse({"jsonrpc": "2.0", "id": rid,
                             "result": {"content": [{"type": "text", "text": text}], "isError": not ok}})
    if rid is not None:
        return _rpc_error(rid, -32601, f"method not found: {method}")
    return JSONResponse({}, status_code=202)


# ---------------- dashboard admin API ----------------

@router.get("/api/mcp")
async def mcp_admin(request: Request):
    dis = _disabled()
    host = request.headers.get("host", "localhost:8088")
    snippet = {"mcpServers": {"ainet": {"url": f"http://{host}/mcp"}}}
    if MCP_TOKEN:
        snippet["mcpServers"]["ainet"]["headers"] = {"Authorization": "Bearer <MCP_TOKEN>"}
    return {
        "endpoint": f"http://{host}/mcp",
        "token_configured": bool(MCP_TOKEN),
        "crawl4ai_key_configured": bool(CRAWL4AI_KEY),
        "tools": [{"name": n, "description": t["description"], "enabled": n not in dis}
                  for n, t in TOOLS.items()],
        "call_log": db.recent_mcp_calls(50),
        "client_snippet": json.dumps(snippet, indent=2),
    }


@router.post("/api/mcp/tools/{name}/toggle")
async def mcp_toggle(name: str):
    if name not in TOOLS:
        raise HTTPException(404, "unknown tool")
    dis = _disabled()
    if name in dis:
        dis.remove(name)
    else:
        dis.append(name)
    db.set_setting(DISABLED_KEY, json.dumps(dis))
    return {"name": name, "enabled": name not in dis}


@router.post("/api/mcp/test")
async def mcp_test(body: dict):
    name = body.get("name", "")
    if name not in TOOLS:
        raise HTTPException(404, "unknown tool")
    try:
        args = json.loads(body.get("args") or "{}")
    except ValueError as e:
        raise HTTPException(422, f"invalid JSON args: {e}")
    ok, text = await run_tool(name, args, "dashboard")
    return {"ok": ok, "output": text[:20000]}
