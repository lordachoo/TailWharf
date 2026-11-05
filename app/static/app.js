/* Tailnet AI Dashboard - vanilla JS SPA */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = { services: [], meta: {}, editing: null, pollTimer: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ago = (ts) => {
  if (!ts) return "never";
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
};

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch {}
    toast(`Error: ${msg}`);
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 3200);
}

/* ---------- sparkline ---------- */
function sparkline(history, w = 110, h = 26) {
  if (!history || history.length < 2) return "";
  const lat = history.map((c) => c.latency_ms ?? 0);
  const max = Math.max(...lat, 1);
  const pts = lat.map((v, i) =>
    `${(i / (lat.length - 1)) * w},${h - 3 - (v / max) * (h - 6)}`).join(" ");
  const dots = history.map((c, i) => {
    if (c.ok) return "";
    const x = (i / (lat.length - 1)) * w;
    const y = h - 3 - (lat[i] / max) * (h - 6);
    return `<circle cx="${x}" cy="${y}" r="2.4" fill="var(--down)"/>`;
  }).join("");
  const color = history[history.length - 1].ok ? "var(--up)" : "var(--down)";
  return `<svg class="sparkline" width="${w}" height="${h}">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".85"/>
    ${dots}</svg>`;
}

/* ---------- tabs ---------- */
$$(".tab").forEach((b) => b.addEventListener("click", () => {
  $$(".tab").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  clearInterval(state.mcpTimer);
  ["dashboard", "discovery", "tailnet", "mcp"].forEach((t) =>
    $(`#tab-${t}`).classList.toggle("hidden", t !== b.dataset.tab));
  if (b.dataset.tab === "discovery") loadDiscoveries();
  if (b.dataset.tab === "tailnet") loadTailnet();
  if (b.dataset.tab === "mcp") loadMCP();
  if (b.dataset.tab === "dashboard") loadServices();
}));

/* ---------- overview chips ---------- */
async function loadOverview() {
  const o = await api("/api/overview");
  $("#chips").innerHTML = `
    <span class="chip g"><b>${o.up}</b> up</span>
    <span class="chip ${o.down ? "r" : ""}"><b>${o.down}</b> down</span>
    <span class="chip"><b>${o.unknown}</b> new</span>
    <span class="chip"><b>${o.devices_online}</b>/${o.devices_total} devices</span>
    <span class="chip y"><b>${o.discoveries}</b> discovered</span>`;
  $("#tailnet-sub").textContent = o.tailscale_available
    ? "tailscale CLI ✓ · auto-refresh" : "tailscale CLI not found";
  $("#disc-badge").textContent = o.discoveries || "";
}

/* ---------- services ---------- */
function svcCard(s) {
  const lc = s.last_check;
  const st = !lc ? "unknown" : lc.ok ? "ok" : "fail";
  const label = !lc ? "…" : lc.ok ? "UP" : "DOWN";
  const typeLabel = (state.meta.presets?.[s.type]?.label || s.type);
  return `<div class="card svc ${st}" data-id="${s.id}">
    <div class="svc-top">
      <span class="svc-name" onclick="openDrawer(${s.id})">${esc(s.name)}</span>
      <span class="type-tag">${esc(typeLabel)}</span>
    </div>
    <div class="svc-url"><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>${s.hostname ? ` <span class="hostname">@${esc(s.hostname)}</span>` : ""}</div>
    <div class="svc-stats">
      <span><span class="dot ${st}"></span><b>${label}</b></span>
      <span>${lc?.latency_ms != null ? `<b>${lc.latency_ms.toFixed(0)} ms</b>` : ""}</span>
      <span>uptime <b>${s.uptime24 != null ? s.uptime24 + "%" : "—"}</b></span>
      <span class="spark">${sparkline(s.spark)}</span>
    </div>
    ${lc?.detail ? `<div class="svc-detail" title="${esc(lc.detail)}">${esc(lc.detail)}</div>` : ""}
    ${quickLinks(s)}
    <div class="svc-actions">
      <button class="btn small" onclick="checkNow(${s.id}, this)">Check now</button>
      <button class="btn small" onclick="inspectUrl('${esc(s.url)}', ${s.id})" title="Deep-probe this endpoint">⌕</button>
      ${["vllm", "llamaswap", "ollama", "litellm"].includes(s.type) && lc?.status_code === 200
        ? `<button class="btn small accent" onclick="harnessConfig('${esc(s.url)}','${esc(s.name)}', ${s.id})" title="URL + model names for AI harnesses">⚙ Harness config</button>` : ""}
      <button class="btn small" onclick="editSvc(${s.id})">Edit</button>
      <button class="btn small" onclick="delSvc(${s.id}, '${esc(s.name)}')">Delete</button>
      <span style="margin-left:auto;color:var(--muted);font-size:11px">${s.enabled ? "" : "disabled · "}checked ${ago(lc?.ts)}</span>
    </div>
  </div>`;
}

function quickLinks(s) {
  const links = [];
  try {
    const origin = new URL(s.url).origin;
    for (const p of state.meta.link_presets?.[s.type] || [])
      links.push({ label: p.label, url: origin + p.path });
  } catch { /* bad url, skip preset links */ }
  for (const c of s.links || []) links.push(c);
  if (!links.length) return "";
  return `<div class="linkrow">${links.map((l) =>
    `<a class="linkchip" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join("")}</div>`;
}

async function loadServices() {
  state.services = await api("/api/services");
  const el = $("#services");
  el.innerHTML = state.services.length
    ? state.services.map(svcCard).join("")
    : `<div class="empty">No services yet — add one or run Discovery.</div>`;
}

async function checkNow(id, btn) {
  btn.disabled = true; btn.textContent = "…";
  const r = await api(`/api/services/${id}/check`, { method: "POST" });
  toast(`${r.ok ? "✓ UP" : "✗ DOWN"} ${r.status_code ?? ""} ${r.latency_ms?.toFixed(0) ?? "?"} ms`);
  await loadServices(); await loadOverview();
}

async function delSvc(id, name) {
  if (!confirm(`Delete "${name}"? Its history is removed too.`)) return;
  await api(`/api/services/${id}`, { method: "DELETE" });
  toast("Service deleted");
  await loadServices(); await loadOverview();
}

function editSvc(id) {
  const s = state.services.find((x) => x.id === id);
  if (!s) return;
  state.editing = id;
  $("#form-title").textContent = "Edit service";
  $("#f-name").value = s.name;
  $("#f-type").value = s.type;
  $("#f-url").value = s.url;
  $("#f-method").value = s.method;
  $("#f-status").value = s.expected_status;
  $("#f-hname").value = s.header_name || "";
  $("#f-hval").value = "";
  $("#f-notes").value = s.notes || "";
  $("#f-links").value = (s.links || []).map((l) => `${l.label} | ${l.url}`).join("\n");
  updatePreview();
  $("#add-form").classList.remove("hidden");
}

function closeForm() {
  state.editing = null;
  $("#add-form").classList.add("hidden");
  ["f-name", "f-url", "f-hname", "f-hval", "f-notes", "f-links"].forEach((i) => ($(`#${i}`).value = ""));
  $("#f-status").value = 200; $("#f-method").value = "GET";
}

function parseLinks(text) {
  const out = [];
  for (const line of (text || "").split("\n")) {
    if (!line.trim()) continue;
    const i = line.indexOf("|");
    const label = (i > 0 ? line.slice(0, i) : "").trim();
    const url = (i > 0 ? line.slice(i + 1) : line).trim();
    if (!url) continue;
    out.push({ label: label || "link", url });
  }
  return out.length ? out : null;
}

async function saveSvc() {
  const body = {
    name: $("#f-name").value.trim(),
    type: $("#f-type").value,
    url: $("#f-url").value.trim(),
    method: $("#f-method").value,
    expected_status: parseInt($("#f-status").value || "200", 10),
    header_name: $("#f-hname").value.trim() || null,
    header_value: $("#f-hval").value || null,
    enabled: true,
    notes: $("#f-notes").value.trim() || null,
    links: parseLinks($("#f-links").value),
  };
  if (!body.name || !body.url) return toast("Name and URL are required");
  if (state.editing) {
    await api(`/api/services/${state.editing}`, { method: "PUT", body });
  } else {
    await api("/api/services", { method: "POST", body });
  }
  toast("Saved");
  closeForm();
  await loadServices(); await loadOverview();
}

/* ---------- drawer ---------- */
async function openDrawer(id) {
  const s = await api(`/api/services/${id}`);
  $("#d-title").textContent = s.name;
  const rows = (s.history || []).slice(-80).reverse().map((h) => `
    <tr>
      <td>${new Date(h.ts * 1000).toLocaleTimeString()}</td>
      <td style="color:${h.ok ? "var(--up)" : "var(--down)"}">${h.ok ? "UP" : "DOWN"}</td>
      <td>${h.status_code ?? "—"}</td>
      <td>${h.latency_ms != null ? h.latency_ms.toFixed(0) + " ms" : "—"}</td>
      <td class="svc-detail" title="${esc(h.detail)}">${esc((h.detail || "").slice(0, 60))}</td>
    </tr>`).join("");
  $("#d-body").innerHTML = `
    <div class="svc-url">${esc(s.url)} (${esc(s.method)})
      <button class="btn small" onclick="inspectUrl('${esc(s.url)}', ${s.id})">⌕ Inspect endpoint</button></div>
    ${quickLinks(s)}
    <div class="svc-stats" style="margin:10px 0">
      <span>24h uptime <b>${s.uptime24 != null ? s.uptime24 + "%" : "—"}</b></span>
      <span>${sparkline(s.history, 200, 40)}</span>
    </div>
    <div class="hist"><table><thead><tr><th>Time</th><th>State</th><th>Code</th><th>Latency</th><th>Detail</th></tr></thead>
    <tbody>${rows || `<tr><td colspan=5>No checks yet</td></tr>`}</tbody></table></div>`;
  $("#drawer").classList.remove("hidden");
}
$("#d-close").addEventListener("click", () => $("#drawer").classList.add("hidden"));

/* ---------- endpoint inspector ---------- */
async function inspectUrl(url, svcId) {
  $("#d-title").textContent = "Endpoint info";
  $("#d-body").innerHTML = `<div class="hint">Probing ${esc(url)} …</div>`;
  $("#drawer").classList.remove("hidden");
  const r = await api(`/api/inspect?url=${encodeURIComponent(url)}` + (svcId ? `&svc=${svcId}` : ""));
  if (!r.ok) {
    $("#d-body").innerHTML = `<div class="svc-url">${esc(url)}</div>
      <div class="hint" style="color:var(--down);margin-top:10px">✗ ${esc(r.error || "unreachable")}</div>`;
    return;
  }
  const badge = (t) => `<span class="type-tag" style="margin:2px 4px 2px 0;display:inline-block">${esc(t)}</span>`;
  $("#d-body").innerHTML = `
    <div class="svc-url">${esc(r.url)}</div>
    <div class="svc-stats" style="margin:12px 0">
      <span>kind <b>${esc(r.kind)}</b></span>
      <span>http <b style="color:${(r.http_status ?? 999) < 400 ? "var(--up)" : "var(--down)"}">${r.http_status ?? "no /"}</b></span>
      ${r.server ? `<span>server <b>${esc(r.server)}</b></span>` : ""}
      ${r.title ? `<span>app <b>${esc(r.title)}</b></span>` : ""}
      ${r.version ? `<span>version <b>${esc(r.version)}</b></span>` : ""}
      ${r.health_path ? `<span>health <b><code>${esc(r.health_path)}</code></b></span>` : ""}
    </div>
    ${(r.models || []).length ? `<div class="sect">Models served (${r.models.length})</div>
      <div>${r.models.map(badge).join("")}</div>` : ""}
    <div class="sect">Paths probed</div>
    <div>${(r.paths || []).map((p) =>
      `<span class="hint" style="margin-right:12px">${esc(p.path)} <b style="color:${p.status < 400 ? "var(--up)" : "var(--down)"}">${p.status}</b></span>`).join("")}</div>`;
}

/* ---------- harness config generator ---------- */
async function harnessConfig(url, name, svcId) {
  $("#d-title").textContent = `⚙ Harness config — ${name || url}`;
  $("#d-body").innerHTML = `<div class="hint">Probing endpoint for model names…</div>`;
  $("#drawer").classList.remove("hidden");
  const r = await api(`/api/harness-config?url=${encodeURIComponent(url)}` + (svcId ? `&svc=${svcId}` : ""));
  if (!r.ok) {
    $("#d-body").innerHTML = `<div class="hint" style="color:var(--down)">✗ ${esc(r.error || "probe failed")}</div>`;
    return;
  }
  if (!r.models.length && r.http_status !== 200) {
    $("#d-body").innerHTML = `<div class="hint">This endpoint does not appear to serve models
      (no model list answered — /v1/models returned ${r.http_status ?? "nothing"}).
      It is likely a gateway or plain health endpoint — monitor it as type <code>http</code> or <code>tcp</code> instead.</div>`;
    return;
  }
  state.hc = r;
  state.hcModel = r.models[0] || "MODEL-NAME";
  renderHC();
}

function renderHC() {
  const r = state.hc, m = state.hcModel;
  const keyline = r.api_key || (r.kind === "ollama" ? "ollama" : "YOUR-KEY-or-not-needed");
  const body = JSON.stringify({ model: m, messages: [{ role: "user", content: "hello" }] });
  const blocks = {
    "Any OpenAI-compatible harness (env vars)":
      `OPENAI_BASE_URL=${r.openai_base}\nOPENAI_API_KEY=${keyline}\nOPENAI_MODEL=${m}`,
    "curl smoke test":
      `curl ${r.openai_base}/chat/completions \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`,
    "Python (openai package)":
      `from openai import OpenAI\nclient = OpenAI(base_url="${r.openai_base}", api_key="${keyline}")\nresp = client.chat.completions.create(model="${m}",\n    messages=[{"role": "user", "content": "hello"}])`,
  };
  if (r.kind === "ollama") blocks["Ollama CLI"] = `ollama run ${m}`;
  if (r.kind === "llamaswap") blocks["llama-swap note"] =
    "Model names are llama-swap aliases (its /ui shows them). Backend API keys stay inside llama-swap.";
  if (r.kind === "vllm") blocks["vLLM note"] =
    "Model name = exactly what /v1/models reports (usually the --model arg). Needs the --api-key value if one was set.";
  if (r.kind === "litellm") blocks["LiteLLM note"] =
    "API key = a LiteLLM virtual key (create in Admin UI → API Keys, or the master key).\nModel name = a model alias from litellm_config.yaml model_groups / Admin UI → Models.\n/v1/models needs the key too, so grab the exact alias there with: curl -H 'Authorization: Bearer KEY' " + r.openai_base + "/models";
  $("#d-body").innerHTML = `
    <div class="svc-url">${esc(r.url)}${r.hostname ? ` <span class="hostname">@${esc(r.hostname)}</span>` : ""}</div>
    <div class="hcline"><span class="hint">Base URL</span> <code class="big">${esc(r.openai_base)}</code>
      <button class="btn small" onclick="navigator.clipboard.writeText('${esc(r.openai_base)}').then(()=>toast('Base URL copied') )">⧉</button></div>
    ${r.api_key ? `<div class="hint" style="margin:2px 0 6px">🔑 using the auth key stored on this service (masked in the registry, included in snippets below)</div>` : ""}
    ${(r.models || []).length ? `
    <div class="sect">Model name <span class="hint">(click to switch configs)</span></div>
    <div>${r.models.map((mm, i) =>
      `<span class="type-tag modelchip ${mm === m ? "on" : ""}" onclick="state.hcModel=state.hc.models[${i}];renderHC()">${esc(mm)}</span>`).join(" ")}</div>` :
      `<div class="hint" style="margin-top:8px">No models answered /v1/models — fill the model name manually in the snippets below.</div>`}
    ${Object.entries(blocks).map(([title, code], i) => `
      <div class="sect">${esc(title)}</div>
      <pre class="console snip">${esc(code)}<button class="btn small snipcopy" onclick="copySnip(this)">⧉ copy</button></pre>`).join("")}`;
}

function copySnip(btn) {
  const code = btn.previousSibling.textContent.replace(/⧉ copy$/, "");
  navigator.clipboard.writeText(code).then(() => toast("Copied"));
}

/* ---------- discovery ---------- */
async function loadDiscoveries() {
  const d = await api("/api/discovery");
  const st = d.state;
  $("#disc-status").textContent = st.running ? "scanning…"
    : st.last_run ? `last scan ${ago(st.last_run)} · ${st.last_count} open ports` : "not run yet";
  $("#disc-badge").textContent = d.items.length || "";
  const el = $("#discoveries");
  el.innerHTML = d.items.length ? d.items.map((x) => `
    <div class="card">
      <div class="svc-top">
        <span class="svc-name">${esc(x.hostname || x.ip)}:${x.port}</span>
        <span class="type-tag">${esc(x.kind || "?")}</span>
      </div>
      <div class="svc-url"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.url)}</a></div>
      <div class="svc-stats">
        <span>http <b>${x.http_status ?? "no response"}</b></span>
        ${x.title ? `<span>app <b>${esc(x.title)}</b></span>` : ""}
        ${x.server ? `<span>server <b>${esc(x.server)}</b></span>` : ""}
      </div>
      ${x.health_path ? `<div class="svc-detail">health: <code>${esc(x.health_path)}</code></div>` : ""}
      <div class="svc-actions">
        ${x.already_added
          ? `<span class="hint">✓ already tracked</span>`
          : `<button class="btn small primary" onclick="addDisc(${x.id})">＋ Add to dashboard</button>`}
        <button class="btn small" onclick="inspectUrl('${esc(x.url)}')">⌕ Info</button>
        <button class="btn small" onclick="dismissDisc(${x.id})">Dismiss</button>
      </div>
    </div>`).join("")
    : `<div class="empty">Nothing discovered yet. Run a scan.</div>`;
}

async function addDisc(id) {
  await api(`/api/discovery/${id}/add`, { method: "POST" });
  toast("Added — running first check");
  await loadDiscoveries(); await loadOverview();
}
async function dismissDisc(id) {
  await api(`/api/discovery/${id}`, { method: "DELETE" });
  await loadDiscoveries(); await loadOverview();
}

$("#btn-scan").addEventListener("click", async () => {
  const r = await api("/api/discovery/run", { method: "POST" });
  toast(r.started ? "Scan started (~5s)" : r.message);
  const iv = setInterval(async () => {
    await loadDiscoveries();
    const d = await api("/api/discovery");
    if (!d.state.running) { clearInterval(iv); toast(`Scan done — ${d.state.last_count} open ports`); }
  }, 1500);
});

/* ---------- tailnet ---------- */
async function loadTailnet() {
  const t = await api("/api/tailnet");
  if (!t.available) {
    $("#tailnet-info").innerHTML = `<div class="card">⚠ tailscale CLI not found. ${esc(t.error || "")}</div>`;
    return;
  }
  const me = t.self;
  $("#tailnet-info").innerHTML = `<p class="hint">This machine: <b>${esc(me?.hostname)}</b> (${esc(me?.ip)}) · ${t.peers.length} peers</p>`;
  const row = (p) => `<tr class="${p.online ? "" : "offline"}">
    <td><span class="dot ${p.online ? "ok" : "fail"}"></span>${p.online ? "online" : "offline"}</td>
    <td><b>${esc(p.hostname)}</b>${p.is_self ? " <span class='hint'>(this machine)</span>" : ""}</td>
    <td>${esc(p.ip)}</td><td>${esc(p.os)}</td><td>${esc(p.dns_name)}</td><td>${esc(p.relay || "—")}</td></tr>`;
  $("#tailnet-table tbody").innerHTML =
    (me ? row(me) : "") + t.peers.map(row).join("");
}

/* ---------- MCP hub ---------- */
async function loadMCP() {
  const m = await api("/api/mcp");
  $("#mcp-endpoint").innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="btn-mcp-copy">Copy client config</button>
      <span class="hint">${m.token_configured
        ? "🔒 bearer token required — replace <code>&lt;MCP_TOKEN&gt;</code> in the snippet"
        : "⚠ open access — set <code>MCP_TOKEN</code> in .env to require a bearer token"}</span>
    </div>
    <pre class="console">${esc(m.client_snippet)}</pre>`;
  $("#btn-mcp-copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText(m.client_snippet);
    toast("Config copied");
  });
  $("#btn-copy-policy").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#policy-pre").textContent);
    toast("Agent policy copied — paste into AGENTS.md / system prompt");
  });

  $("#mcp-tools tbody").innerHTML = m.tools.map((t) => `<tr>
    <td><input type="checkbox" ${t.enabled ? "checked" : ""} onchange="mcpToggle('${t.name}')"></td>
    <td><b>${esc(t.name)}</b></td>
    <td class="hint">${esc(t.description)}</td>
    <td><button class="btn small" onclick="mcpTry('${t.name}')">Test</button></td></tr>`).join("");

  $("#mcp-test-tool").innerHTML = m.tools
    .map((t) => `<option>${esc(t.name)}</option>`).join("");

  $("#mcp-log tbody").innerHTML = (m.call_log || []).map((c) => `<tr${c.ok ? "" : ' class="rowerr"'}>
    <td title="${new Date(c.ts * 1000).toLocaleTimeString()}">${ago(c.ts)}</td>
    <td><b>${esc(c.tool)}</b></td><td>${esc(c.client_name || c.client || "")}</td>
    <td class="svc-detail" title="${esc(c.args_summary)}">${esc((c.args_summary || "").slice(0, 60))}</td>
    <td style="color:${c.ok ? "var(--up)" : "var(--down)"}">${c.ok ? "ok" : "ERR"}</td>
    <td>${c.ms ?? "—"}</td></tr>`).join("")
    || `<tr><td colspan="6" class="hint">No MCP calls yet — ask an agent to use a tool 🙂</td></tr>`;
  // live refresh while the MCP tab is visible
  clearInterval(state.mcpTimer);
  state.mcpTimer = setInterval(() => {
    if (!$("#tab-mcp").classList.contains("hidden")) loadMCPLog();
    else clearInterval(state.mcpTimer);
  }, 4000);
}

async function loadMCPLog() {
  const m = await api("/api/mcp");
  $("#mcp-log tbody").innerHTML = (m.call_log || []).map((c) => `<tr${c.ok ? "" : ' class="rowerr"'}>
    <td title="${new Date(c.ts * 1000).toLocaleTimeString()}">${ago(c.ts)}</td>
    <td><b>${esc(c.tool)}</b></td><td>${esc(c.client_name || c.client || "")}</td>
    <td class="svc-detail" title="${esc(c.args_summary)}">${esc((c.args_summary || "").slice(0, 60))}</td>
    <td style="color:${c.ok ? "var(--up)" : "var(--down)"}">${c.ok ? "ok" : "ERR"}</td>
    <td>${c.ms ?? "—"}</td></tr>`).join("") || "";
}

async function mcpToggle(name) {
  const r = await api(`/api/mcp/tools/${name}/toggle`, { method: "POST" });
  toast(`${name} ${r.enabled ? "enabled" : "disabled"}`);
  loadMCP();
}

function mcpTry(name) {
  $("#mcp-test-tool").value = name;
  const defaults = {
    web_search: '{"query": "tailscale", "n": 3}',
    web_fetch: '{"url": "https://example.com", "max_chars": 2000}',
    check_service: '{"service_id": 1}',
  };
  $("#mcp-test-args").value = defaults[name] || "{}";
  $("#mcp-test-out").scrollIntoView?.({ behavior: "smooth" });
}

async function mcpRunTest() {
  const out = $("#mcp-test-out");
  out.classList.remove("hidden");
  out.textContent = "running…";
  const r = await api("/api/mcp/test", {
    method: "POST",
    body: { name: $("#mcp-test-tool").value, args: $("#mcp-test-args").value },
  });
  out.textContent = r.output;
}

/* ---------- export / import ---------- */
async function doExport(secrets) {
  const r = await fetch(`/api/export?secrets=${secrets ? "true" : "false"}`);
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = secrets ? "tailnet-dash-services-SECRET.json" : "tailnet-dash-services.json";
  a.click();
  if (secrets) toast("⚠ exported file contains auth secrets — store it safely");
}

async function doImport(file) {
  const data = JSON.parse(await file.text());
  const services = data.services || [];
  const mode = confirm(`Import ${services.length} services.\n\nOK = merge (skip existing URLs)\nCancel = abort`)
    ? "merge" : null;
  if (!mode) return;
  const r = await api("/api/import", { method: "POST", body: { services, mode } });
  toast(`Imported: ${r.added} added, ${r.skipped} skipped`);
  await loadServices(); await loadOverview();
}

/* ---------- init ---------- */
function updatePreview() {
  const type = $("#f-type").value, url = $("#f-url").value.trim();
  const spec = state.meta.presets?.[type];
  const prev = spec?.path && url && spec.path !== "/" ? `${url.replace(/\/$/, "")}${spec.path}` : "";
  $("#f-url-preview").textContent = prev ? `will check: ${prev}` : "";
}

async function init() {
  state.meta = await api("/api/meta");
  $("#f-type").innerHTML = Object.entries(state.meta.presets)
    .map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("");
  $("#f-type").addEventListener("change", updatePreview);
  $("#f-url").addEventListener("input", updatePreview);
  $("#btn-add").addEventListener("click", () => { closeForm(); $("#add-form").classList.remove("hidden"); });
  $("#btn-cancel").addEventListener("click", closeForm);
  $("#btn-save").addEventListener("click", saveSvc);
  $("#btn-checkall").addEventListener("click", async () => {
    toast("Checking all…");
    await Promise.all(state.services.map((s) => api(`/api/services/${s.id}/check`, { method: "POST" })));
    await loadServices(); await loadOverview();
  });
  $("#poll-hint").textContent = `auto-check every ${state.meta.poll_seconds}s`;
  $("#btn-export").addEventListener("click", () => doExport(false));
  $("#btn-export-secret").addEventListener("click", () => doExport(true));
  $("#btn-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", (e) => e.target.files[0] && doImport(e.target.files[0]));
  $("#mcp-test-run").addEventListener("click", mcpRunTest);
  await Promise.all([loadOverview(), loadServices()]);
  state.pollTimer = setInterval(async () => {
    if (!document.hidden && !$("#tab-dashboard").classList.contains("hidden")) {
      await Promise.all([loadOverview(), loadServices()]);
    }
  }, (state.meta.poll_seconds || 30) * 1000);
}
init();
