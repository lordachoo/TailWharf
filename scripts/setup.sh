#!/usr/bin/env bash
# Tailnet AI Dashboard — one-command deploy (Linux/macOS)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "python3 not found (set PYTHON=...)"; exit 1; }

echo "==> venv"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  # generate a random MCP token so the hub is never accidentally open
  TOKEN=$("$PY" -c "import secrets;print(secrets.token_hex(32))")
  if [[ "$OSTYPE" == darwin* ]]; then
    sed -i "" "s/^MCP_TOKEN=$/MCP_TOKEN=$TOKEN/" .env
  else
    sed -i "s/^MCP_TOKEN=$/MCP_TOKEN=$TOKEN/" .env
  fi
  echo "==> created .env with a random MCP_TOKEN (edit for CRAWL4AI_API_KEY etc.)"
fi

cat <<EOF

Deploy ready.

  Run now:   .venv/bin/python run.py
  Bind:      auto-detects this machine's tailscale IP (tailscale ip -4);
             override with DASH_HOST in .env

  Autostart (systemd, root):
    cp scripts/tailnet-dash.service /etc/systemd/system/
    sed -i "s|/opt/tailnet-dash|$PWD|" /etc/systemd/system/tailnet-dash.service
    systemctl daemon-reload && systemctl enable --now tailnet-dash
    # if the service user differs: chown -R <user> .env data
EOF
