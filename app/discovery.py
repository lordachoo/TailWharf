"""Tailnet service discovery: throttled port scan + multi-path HTTP fingerprinting."""
import asyncio
import json
import time

from . import db
from .tailscale import tailnet_status

# Common AI / self-hosted service ports (port -> hint kind when HTTP is silent)
SCAN_PORTS = {
    80: "http", 443: "http", 1234: "vllm", 3000: "openwebui", 3001: "http", 4000: "litellm",
    5000: "http", 5001: "docling", 5432: "tcp", 6333: "http", 6379: "tcp",
    7860: "http", 7861: "http", 8000: "vllm", 8001: "vllm", 8002: "vllm",
    8080: "http", 8081: "vllm", 8088: "http", 8090: "http", 8443: "http",
    8888: "searxng", 9000: "http", 9090: "http", 9443: "http",
    11235: "crawl4ai", 11434: "ollama", 28080: "http",
}

TLS_PORTS = {443, 8443, 9443}

# Windows allows ~330 concurrent outbound TCP connections; keep well under it.
_SEMAPHORE = asyncio.Semaphore(60)


async def _tcp_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _get(client, url: str, cap: int = 4000):
    """Return (status, text, server) or None."""
    try:
        r = await client.get(url)
        return r.status_code, r.text[:cap], r.headers.get("server", "")
    except Exception:
        return None


async def _classify(ip: str, port: int):
    """Probe several health paths and classify the service. Returns dict or None."""
    import httpx
    probe_paths = ["/", "/healthz", "/health", "/openapi.json"]
    if port in (5001, 5000):
        probe_paths.append("/version")
    if port in (1234, 8000, 8001, 8002, 8080, 8081, 28080, 4000):
        probe_paths.append("/v1/models")  # OpenAI-compat: vLLM, llama.cpp, cpgw, LiteLLM…
    if SCAN_PORTS.get(port) == "litellm":
        probe_paths.append("/health/liveliness")

    for scheme in (["https", "http"] if port in TLS_PORTS else ["http"]):
        base = f"{scheme}://{ip}:{port}"
        try:
            async with httpx.AsyncClient(timeout=2.0, verify=False) as client:
                got = await asyncio.gather(*(_get(client, base + p) for p in probe_paths))
        except Exception:
            continue
        if all(g is None for g in got):
            continue

        res = dict(zip(probe_paths, got))
        status, body, server = res["/"] or (None, "", "")

        title = None
        if res.get("/openapi.json"):
            try:
                title = json.loads(res["/openapi.json"][1]).get("info", {}).get("title")
            except (ValueError, AttributeError):
                pass

        kind = _kind(res, title, body, server, port)
        health_path = _pick_health(res, kind)
        return {
            "ip": ip, "port": port, "kind": kind, "title": title,
            "http_status": status, "server": server, "url": base,
            "health_path": health_path,
        }
    return None


def _kind(res, title, body, server, port) -> str:
    low = (body or "").lower()
    tlow = (title or "").lower()
    if "searx" in low or "searx" in server.lower():
        return "searxng"
    if "docling" in tlow or "docling" in low:
        return "docling"
    if port == 5001 and res.get("/version") and "docling-serve" in res["/version"][1]:
        return "docling"
    if "crawl4ai" in low:
        return "crawl4ai"
    if "litellm" in low or "litellm" in tlow:
        return "litellm"
    if "ollama" in low or (res.get("/health") and '"/version"' in (res["/health"][1] or "")):
        return "ollama"
    # llama-swap fronting one or more llama.cpp servers
    vm = res.get("/v1/models")
    vm_body = vm[1] if vm else ""
    if "llama-swap" in vm_body or "llama-swap" in low or "llamaswap" in low:
        return "llamaswap"
    # OpenAI-compat model server answered /v1/models (vLLM {"data":..} or llama.cpp {"models":..})
    if vm and vm[0] == 200 and ('"data"' in vm[1] or '"models"' in vm[1]):
        return "vllm"
    if "open webui" in low or "open-webui" in low:
        return "openwebui"
    if port in (8000, 8001, 8002, 1234, 8081) or "vllm" in tlow:
        return "vllm"
    hint = SCAN_PORTS.get(port)
    return hint if hint and hint != "http" else "http"


def _pick_health(res, kind) -> str | None:
    """Choose the best working health path for this service."""
    for p in ("/health/liveliness", "/healthz", "/health"):
        r = res.get(p)
        if r and r[0] == 200:
            return p
    vm = res.get("/v1/models")
    if kind == "vllm" and vm and vm[0] in (200, 401, 403):
        return "/v1/models"
    r = res.get("/")
    if r and r[0] and r[0] < 400:
        return "/"
    return None


async def _probe_one(ip: str, hostname: str, port: int):
    async with _SEMAPHORE:
        if not await _tcp_open(ip, port):
            return None
        got = await _classify(ip, port)
        if got is None:
            # TCP-only survivor (e.g. gRPC, DB, hung HTTP)
            return {"ip": ip, "hostname": hostname, "port": port,
                    "kind": SCAN_PORTS.get(port) or "tcp", "http_status": None,
                    "server": "tls?" if port in TLS_PORTS else "",
                    "url": f"https://{ip}:{port}" if port in TLS_PORTS else f"http://{ip}:{port}",
                    "title": None, "health_path": None}
        got["hostname"] = hostname
        return got


def _parse_models(body: str) -> list:
    """Model names from OpenAI-compat (data[].id / models[].name) or Ollama (models[].name)."""
    try:
        j = json.loads(body)
    except ValueError:
        return []
    if not isinstance(j, dict):
        return []
    out = []
    for key in ("data", "models"):
        for m in j.get(key, []) or []:
            out.append(m if isinstance(m, str)
                       else (m.get("id") or m.get("name") or ""))
    seen, res = set(), []
    for name in out:
        if name and name not in seen:
            seen.add(name)
            res.append(name)
    return res[:30]


async def inspect_endpoint(url: str) -> dict:
    """Live deep-probe of one endpoint: identity, version, models, path map."""
    import httpx
    from urllib.parse import urlparse
    url = url.rstrip("/")
    u = urlparse(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    # Normalize: always probe from scheme://host:port, never from a path-baked URL
    origin = f"{u.scheme}://{u.netloc}"
    paths = ["/", "/healthz", "/health", "/health/liveliness", "/openapi.json",
             "/version", "/v1/models", "/api/version", "/api/tags"]
    try:
        async with httpx.AsyncClient(timeout=4.0, verify=False,
                                     follow_redirects=True) as client:
            big = ("/v1/models", "/api/tags", "/openapi.json")
            got = await asyncio.gather(
                *[_get(client, origin + p, 60000 if p in big else 4000) for p in paths])
    except Exception as e:
        return {"url": origin, "ok": False, "error": f"{type(e).__name__}: {e}"}
    res = {p: g for p, g in zip(paths, got) if g}
    if not res:
        return {"url": url, "ok": False, "error": "no HTTP response on any known path"}
    title = version = None
    if "/openapi.json" in res:
        try:
            info = json.loads(res["/openapi.json"][1]).get("info", {})
            title, version = info.get("title"), info.get("version")
        except ValueError:
            pass
    root = res.get("/")
    body = root[1] if root else ""
    server = (root[2] if root else "") or ""
    kind = _kind(res, title, body, server, port)
    models: list = []
    for p in ("/v1/models", "/api/tags"):
        if p in res and res[p][0] == 200:
            models = _parse_models(res[p][1])
            if models:
                break
    return {
        "url": origin, "ok": True, "kind": kind,
        "http_status": root[0] if root else None,
        "server": server, "title": title, "version": version,
        "health_path": _pick_health(res, kind),
        "models": models,
        "paths": [{"path": p, "status": g[0]} for p, g in res.items()],
    }


STATE = {"running": False, "last_run": None, "last_count": 0}


async def run_discovery() -> int:
    st = await tailnet_status()
    targets = [(p["ip"], p["hostname"]) for p in st["peers"] if p["online"] and p["ip"]]
    jobs = [_probe_one(ip, hn, port)
            for ip, hn in targets for port in sorted(SCAN_PORTS)]
    results = [r for r in await asyncio.gather(*jobs) if r]
    for r in results:
        db.upsert_discovery(r)
    STATE.update(running=False, last_run=time.time(), last_count=len(results))
    return len(results)


async def start_discovery() -> bool:
    if STATE["running"]:
        return False
    STATE["running"] = True
    asyncio.create_task(_guarded_run())
    return True


async def _guarded_run():
    try:
        await run_discovery()
    except Exception:
        STATE["running"] = False
