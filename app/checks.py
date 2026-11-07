"""Health check engine: type presets + async HTTP/TCP checks."""
import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx

# type -> health path appended to base url (None = no path suggestion / TCP only)
PRESETS = {
    "searxng":   {"path": "/healthz",      "label": "SearXNG"},
    "crawl4ai":  {"path": "/health",       "label": "Crawl4AI"},
    "ollama":    {"path": "/api/version",  "label": "Ollama"},
    "vllm":      {"path": "/v1/models",    "label": "vLLM / OpenAI-compat"},
    "openwebui": {"path": "/health/checks","label": "Open WebUI"},
    "docling":   {"path": "/health",       "label": "IBM Docling Serve"},
    "llamaswap": {"path": "/health",       "label": "llama-swap"},
    "litellm":   {"path": "/health/liveliness", "label": "LiteLLM Proxy"},
    # content assertion: SPA returns 200-HTML for every path, so require
    # the app's own signature in the body or a broken backend looks UP
    "phoenix":   {"path": "/", "label": "Arize Phoenix (tracing)", "content": "phoenix"},
    "http":      {"path": "/",              "label": "Generic HTTP"},
    "health":    {"path": "",               "label": "Health endpoint"},
    "tcp":       {"path": None,             "label": "TCP only"},
}

# Default quick-links per service type (relative to the service origin).
# Rendered as chips on the service card; users can add more per service.
LINK_PRESETS = {
    "llamaswap": [
        {"label": "Web UI", "path": "/ui/"},
        {"label": "Models JSON", "path": "/v1/models"},
        {"label": "Metrics", "path": "/metrics"},
    ],
    "vllm": [
        {"label": "API docs", "path": "/docs"},
        {"label": "Models JSON", "path": "/v1/models"},
    ],
    "crawl4ai": [
        {"label": "Dashboard", "path": "/dashboard"},
    ],
    "searxng": [
        {"label": "Search UI", "path": "/"},
    ],
    "openwebui": [
        {"label": "Web UI", "path": "/"},
        {"label": "Admin", "path": "/admin"},
    ],
    "docling": [
        {"label": "API docs (ReDoc)", "path": "/docs"},
        {"label": "Version", "path": "/version"},
    ],
    "phoenix": [
        {"label": "🪱 Trace Viewer", "path": "/"},
        {"label": "🧪 Datasets", "path": "/datasets"},
        {"label": "✏️ Prompts", "path": "/prompts"},
    ],
    "ollama": [
        {"label": "Tags JSON", "path": "/api/tags"},
        {"label": "Version JSON", "path": "/api/version"},
    ],
    "litellm": [
        {"label": "Admin UI", "path": "/ui/"},
        {"label": "Cost tracking", "path": "/ui/cost-tracking/"},
        {"label": "Usage", "path": "/ui/usage/"},
        {"label": "Users", "path": "/ui/users/"},
        {"label": "Models JSON", "path": "/v1/models"},
        {"label": "Liveliness", "path": "/health/liveliness"},
    ],
    # health / http / tcp: no default quick-links (intentionally plain)
}


def apply_preset(url: str, stype: str) -> str:
    """If service type has a health path and URL doesn't already include a path, append it."""
    spec = PRESETS.get(stype)
    if not spec or spec["path"] is None:
        return url
    parts = urlsplit(url if "://" in url else "http://" + url)
    if parts.path in ("", "/"):
        base = url.rstrip("/")
        return base + spec["path"]
    return url


def _hostport(url: str, default_port: int):
    parts = urlsplit(url if "://" in url else "http://" + url)
    return parts.hostname, parts.port or default_port


async def check_service(svc: dict) -> dict:
    """Run one health check. Returns ok, status_code, latency_ms, detail."""
    stype = svc.get("type", "http")
    url = svc["url"]
    if stype == "tcp":
        return await _check_tcp(url)
    return await _check_http(svc)


async def _check_tcp(url: str) -> dict:
    host, port = _hostport(url, 80)
    t0 = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=4)
        latency = (time.perf_counter() - t0) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return {"ok": True, "status_code": None, "latency_ms": round(latency, 1), "detail": "tcp connect ok"}
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return {"ok": False, "status_code": None, "latency_ms": round(latency, 1), "detail": f"{type(e).__name__}: {e}"}


async def _check_http(svc: dict) -> dict:
    url = svc["url"]
    method = (svc.get("method") or "GET").upper()
    expect = int(svc.get("expected_status") or 200)
    headers = {"User-Agent": "tailnet-dash-health/1.0"}
    if svc.get("header_name"):
        headers[svc["header_name"]] = svc.get("header_value") or ""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True, max_redirects=5) as client:
            r = await client.request(method, url, headers=headers)
        latency = (time.perf_counter() - t0) * 1000
        detail = _summarize(svc.get("type", "http"), r)
        need = (PRESETS.get(svc.get("type") or "") or {}).get("content")
        content_ok = need is None or need in r.text[:8000].lower()
        if not content_ok:
            detail = f"200 OK but app signature '{need}' missing — wrong app on port?"
        return {
            "ok": r.status_code == expect and content_ok,
            "status_code": r.status_code,
            "latency_ms": round(latency, 1),
            "detail": detail,
        }
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        return {"ok": False, "status_code": None, "latency_ms": round(latency, 1),
                "detail": f"{type(e).__name__}: {e}"}


def _summarize(stype: str, r: httpx.Response) -> str:
    body = r.text[:200].strip()
    if stype == "vllm":
        try:
            j = r.json()
            # vLLM returns {"data":[{"id":..}]}, llama.cpp returns {"models":[{"name":..}]}
            items = j.get("data") or j.get("models") or []
            models = [m.get("id") or m.get("name") for m in items if isinstance(m, dict)]
            if models:
                return "models: " + ", ".join(m for m in models[:6] if m)
        except (ValueError, AttributeError):
            pass
    elif stype == "ollama":
        try:
            return f"version {r.json().get('version', '?')}"
        except ValueError:
            pass
    return body[:160]
