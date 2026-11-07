# ⬡ Tailnet AI Dashboard

Self-contained monitoring dashboard for AI services running on a
[Tailscale](https://tailscale.com) tailnet — SearXNG, Crawl4AI, llama-swap,
vLLM, llama.cpp, Ollama, Open WebUI, IBM Docling Serve, and anything else
speaking HTTP/TCP.

FastAPI backend + SQLite storage + dependency-free vanilla-JS SPA.
No Node build chain, no cloud account, no Tailscale API key required —
the local `tailscale` CLI is the device feed.

> ## ⚠ Security model — read first
>
> This dashboard has **no user accounts**. Its one security control is an
> optional **bearer token** (`MCP_TOKEN`) which guards the MCP endpoint and
> ALL privileged operations (MCP admin/test, registry import, export,
> harness-config key reveal). With no token set, **anyone who can reach the
> port fully administers the dashboard** and can use the host to probe URLs.
>
> **Bind only to a Tailscale `100.x` address or `127.0.0.1`** (the default
> auto-detect does this for you). Never `0.0.0.0`, never behind a reverse
> proxy / Tailscale Funnel / port-forward without an authenticating layer.
>
> Verify your exposure: printed bind URL is `100.x`/`127.x`; the firewall
> rule is scoped `remoteip=100.64.0.0/10`; Tailscale ACLs restrict `:8088`
> to your user/tag; a curl from a non-tailnet LAN host gets refused.

---

## Features

| | |
|---|---|
| **Service registry** | Add / edit / delete services; stored in SQLite with full check history |
| **Health checks** | Async background polling (default 30 s), per-type health endpoints, latency, 24 h uptime %, sparklines, red/green cards |
| **Type presets** | Correct health endpoint + quick links chosen per service type (see table) |
| **Quick links** | Every card shows one-click chips (open in new tab) to the service's real web UIs: llama-swap `/ui/`, Crawl4AI `/dashboard`, API docs, model JSON, metrics… plus unlimited custom links per service |
| **Tailnet panel** | Live device inventory from `tailscale status --json`: hostname, OS, IPs, relay, online/offline |
| **Discovery** | Port-scan of all *online* peers across 27 common AI ports with multi-path HTTP fingerprinting; one-click "add to dashboard" wired to the health endpoint that actually works |
| **History** | 7-day rolling check log per service, viewable in a detail drawer |
| **MCP hub** | Any agent on the tailnet (pi, Claude, Cursor…) connects to `POST /mcp` and gains 7 tools: web search/fetch + tailnet/service health — optional bearer-token auth, per-tool toggles, test console, call log |
| **Registry export/import** | One click download/upload of the service registry (with optional secrets) — full redeploy/migration in minutes |

## Service types

| Type | Health path | Default quick links |
|---|---|---|
| `searxng` | `/healthz` | Search UI |
| `crawl4ai` | `/health` | Dashboard (`/dashboard`) |
| `ollama` | `/api/version` | Tags JSON, Version JSON |
| `vllm` (any OpenAI-compat: vLLM, llama.cpp `llama-server`, cpgw…) | `/v1/models` | API docs, Models JSON |
| `openwebui` | `/health/checks` | Web UI, Admin |
| `docling` | `/health` | API docs (ReDoc), Version |
| `llamaswap` | `/health` | Web UI `/ui/`, Models JSON, Metrics |
| `http` | `/` | — |
| `tcp` | TCP connect only (no HTTP) | — |

Custom links are per-service (`Label | https://url`, one per line in the edit
form) and render alongside the type defaults.

## Quickstart

```powershell
# Windows
cd tailnet-dash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # then edit DASH_HOST etc.
.venv\Scripts\python run.py
```

```bash
# Linux / macOS
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then edit
.venv/bin/python run.py
```

Open the printed URL. Interactive API docs: **`/docs`** (Swagger).

## Configuration

All config via environment variables; a local `.env` file (gitignored) is
loaded automatically by `run.py` — real environment variables always win.

| Variable | Default | Purpose |
|---|---|---|
| `DASH_HOST` | *(empty = auto)* | **Leave empty to auto-detect** this machine's Tailscale IP (`tailscale ip -4`), falling back to `127.0.0.1`. Hardcode only when needed (`127.0.0.1`, `0.0.0.0`, or a specific IP). Bind always falls back to `127.0.0.1` if the preferred address can't be bound |
| `MCP_TOKEN` | *(empty = open)* | When set, MCP clients must send `Authorization: Bearer <token>` to `POST /mcp`. Set up scripts generate a random one automatically |
| `DASH_PORT` | `8088` | Port |
| `DASH_POLL_SECONDS` | `30` | Health-check interval |
| `DASH_DATA_DIR` | `./data` | SQLite file location |
| `SEARXNG_URL` / `CRAWL4AI_URL` / `CRAWL4AI_API_KEY` | see `.env.example` | AI Net containers used by the MCP `web_search` / `web_fetch` tools |

> **Firewall:** when binding to a Tailscale IP on Windows, other tailnet peers
> may need an inbound rule once:
> `netsh advfirewall firewall add rule name="tailnet-dash" dir=in action=allow protocol=TCP localport=8088 remoteip=100.64.0.0/10`

## MCP hub — your tailnet as an agent's brain

`POST /mcp` is a stateless **streamable-HTTP MCP server**. Any MCP client on
the tailnet gets infrastructure + web tools from one URL:

```json
{ "mcpServers": { "ainet": {
    "url": "http://<this-machine-tailnet-ip>:8088/mcp",
    "headers": { "Authorization": "Bearer <MCP_TOKEN>" }
} } }
```

The **MCP Hub tab** shows this snippet pre-filled with the live URL (copy
button), per-tool enable/disable toggles, a test console, and a call log
(who called what, when, how fast).

| Tool | What it gives the agent |
|---|---|
| `web_search` / `web_fetch` | Internet access via your SearXNG + Crawl4AI |
| `tailnet_status` | Device inventory, online/offline |
| `services_status` | All services with health, latency, 24h uptime |
| `check_service` | Run one health check on demand |
| `discovery_run` / `discovery_list` | Full tailnet service scan + results |

Security: set `MCP_TOKEN` (setup scripts generate one) — without it the hub
is open to anything that can reach the port. The MCP tab warns you which
mode you're in.

## Deployment

```bash
./scripts/setup.sh            # Linux/macOS: venv, deps, .env + random MCP_TOKEN
.\scripts\setup.ps1           # Windows equivalent
```

Autostart:

- **Linux (systemd):** `scripts/tailnet-dash.service` — install commands
  printed by `setup.sh`
- **Windows:** `.\scripts\autostart.ps1 register` — logon task + a
  tailnet-scoped firewall rule (`remoteip=100.64.0.0/10`)

### Migrating to a new host

1. Dashboard tab → **⬇ Export +secrets** (keep the file safe, delete after)
2. `git clone` on the new machine → `./scripts/setup.sh`
3. Put the Crawl4AI key / MCP_TOKEN in `.env` (or don't — import brings auth headers)
4. Dashboard tab → **⬆ Import** the JSON
5. Delete the exported file. Old host's history stays behind (copy
   `data/dash.db` instead if you want the 7-day check history too)

Bind auto-detects the new machine's tailnet IP — no edits required.

## Project layout

```
tailnet-dash/
├── run.py                # entry: .env load → tailnet-IP auto-detect → uvicorn
├── requirements.txt      # fastapi, uvicorn, httpx  (that's all)
├── scripts/
│   ├── setup.sh / setup.ps1          # one-command deploy (both OSes)
│   ├── tailnet-dash.service          # systemd unit
│   └── autostart.ps1                 # Windows task + firewall rule
├── tools/webtools.py     # CLI: web search (SearXNG) + page reader (Crawl4AI)
├── .env                  # local config — GITIGNORED, never committed
├── .env.example          # template
├── data/                 # SQLite DB + WAL — GITIGNORED
└── app/
    ├── main.py           # FastAPI routes; serves the SPA
    ├── db.py             # sqlite3 layer, schema, auto-migrations
    ├── checks.py         # type presets, link presets, async HTTP/TCP checks
    ├── discovery.py      # throttled port scan + fingerprinting
    ├── mcp_hub.py        # streamable-HTTP MCP server + hub admin API
    ├── poller.py         # background polling loop
    ├── tailscale.py      # `tailscale status --json` wrapper (no API key)
    └── static/           # SPA: index.html, app.js, style.css (no build step)
```

## API reference

| Method & path | Purpose |
|---|---|
| `GET /api/services` | All services + last check + uptime + spark history |
| `POST /api/services` | Create (type preset auto-appends health path) |
| `GET /api/services/{id}` | Detail + check history |
| `PUT /api/services/{id}` | Update (blank `header_value` keeps existing secret) |
| `DELETE /api/services/{id}` | Delete service + history |
| `POST /api/services/{id}/check` | Run one check immediately |
| `GET /api/tailnet` | Device inventory (self + peers, online state) |
| `GET /api/discovery` | Discovered endpoints + scan state |
| `POST /api/discovery/run` | Trigger background scan |
| `POST /api/discovery/{id}/add` | Convert discovery → tracked service (uses working health path) |
| `DELETE /api/discovery/{id}` | Dismiss a discovery |
| `GET /api/meta` | Type presets, link presets, poll interval |
| `GET /api/overview` | Dashboard summary counters |
| `GET /api/export?secrets=false` | Registry download (JSON attachment) |
| `POST /api/import` | `{services, mode: merge\|replace}` |
| `POST /mcp` | MCP streamable-HTTP endpoint (bearer if `MCP_TOKEN` set) |
| `GET /api/mcp` | Hub status, tools, snippet, call log (for the MCP tab) |
| `POST /api/mcp/tools/{name}/toggle` | Enable/disable a hub tool |
| `POST /api/mcp/test` | Run a hub tool from the dashboard test console |

## How discovery works (and why it has to)

Tailscale knows **machines**, never **ports** — there is no Tailscale API for
"what runs on a device". So discovery is:

1. Device list from the local `tailscale` CLI (online peers only).
2. Throttled TCP probe of 27 common AI/self-hosted ports
   (concurrency capped at 60 — Windows drops sockets past ~330, which causes
   false negatives if unthrottled).
3. Every open port gets multi-path HTTP fingerprinting:
   `/`, `/healthz`, `/health`, `/openapi.json` (+ `/version` on Docling-ish
   ports, `/v1/models` on model-server ports), and OpenAPI `info.title` is
   stored. TLS handshake attempt on 443/8443/9443.
4. Results classified per the type table; the **working health path** is
   saved and used when you add the service.

Known limitations:
* Protocols that accept TCP but never answer HTTP (vLLM/llama.cpp **gRPC/RPC**
  ports, e.g. a `:8000` backend) can only be tracked as `tcp` type.
* Services hidden behind a reverse-proxy path prefix are invisible to port
  scans — add them manually with their full URL.
* Discovery marks `UP` from HTTP status alone; a service returning a login
  page is "reachable", not necessarily "healthy" — point those services at an
  authenticated health endpoint via header auth if you need true app health.

## Web access via your own stack

Your SearXNG + Crawl4AI become a private search/read pipeline through the
MCP hub tools (`web_search`, `web_fetch`) — no third-party APIs; queries
never leave your infrastructure. Requires `SEARXNG_URL`, `CRAWL4AI_URL`,
`CRAWL4AI_API_KEY` in `.env` (gitignored; see `.env.example`). SearXNG must
have `format=json` enabled. The Crawl4AI key is only ever read from the
environment — it appears in no source file and no git object.

## Security

* **Trust boundary = the port.** Anyone who can reach it can read status;
  privileged operations (MCP hub + tools, import, export, harness-config)
  additionally require `Authorization: Bearer <MCP_TOKEN>` when one is set.
  The SPA prompts for the token once and remembers it. **Set MCP_TOKEN**
  (setup scripts generate a strong one).
* **Default exposure is deliberately minimal.** Auto-bind targets this
  machine's Tailscale IP; falls back to `127.0.0.1`, never `0.0.0.0`.
  Firewall guidance ships in `scripts/autostart.ps1`
  (`remoteip=100.64.0.0/10`). Remember: "your tailnet" means **every
  ACL-permitted device**, guest tags included — lock `:8088` down in
  Tailscale ACLs.
* **Secrets handling:**
  * No credentials hardcoded anywhere in source.
  * Per-service auth headers are stored in `data/dash.db` (gitignored).
    Normal service endpoints return only `has_auth: true`. The two places
    a stored key can surface — `harness-config` (key reveal for snippets)
    and `export?secrets=true` — are **token-gated**, and a stored key is
    only ever sent to probes of the **same origin** it is saved for
    (no credential-follows-the-URL exfil path).
  * Editing a service with a blank secret preserves the stored value.
  * Filesystem access to `data/dash.db` = access to stored keys (inherent
    to local storage): `chmod 600` on `data/` and `.env`.
* Bearer comparisons are constant-time (`hmac.compare_digest`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Card stuck DOWN, `ReadTimeout` on a port that "is up" | Port speaks gRPC/RPC, not HTTP → change type to `tcp` or point at the real HTTP front |
| Discovery misses a service | Its port/path isn't in the lists → add the port to `SCAN_PORTS` in `app/discovery.py`, or just add the service manually |
| Other tailnet device can't load dashboard | Bound to `127.0.0.1` (check printed URL) or Windows Firewall blocking inbound 8088 |
| `tailscale CLI not found` in UI | Install Tailscale or extend paths in `app/tailscale.py` |
| Discovery false-negatives on Windows | Concurrency — keep `Semaphore(60)` well under the OS limit |

## Development notes

* Python ≥ 3.11, three runtime dependencies. SQLite WAL mode, one short-lived
  connection per operation (thread-safe, no ORM).
* DB migrations are `ALTER TABLE … ADD COLUMN` guarded by
  `sqlite3.OperationalError` in `db.init_db()` — add columns to `SCHEMA` plus
  the migration tuple.
* The SPA is dependency-free vanilla JS; server computes uptime/spark data so
  the client stays dumb.
