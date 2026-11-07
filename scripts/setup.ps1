# Tailnet AI Dashboard — one-command deploy (Windows PowerShell)
Set-Location (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")

python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    # cryptographically random token (Get-Random is NOT suitable for secrets)
    $rngBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($rngBytes)
    $token = -join ($rngBytes | ForEach-Object { $_.ToString("x2") })
    (Get-Content .env) -replace '^MCP_TOKEN=$', "MCP_TOKEN=$token" | Set-Content .env
    Write-Host "==> created .env with a random MCP_TOKEN (edit CRAWL4AI_API_KEY etc.)"
}

Write-Host @"

Deploy ready.

  Run now:    .\.venv\Scripts\python run.py
  Bind:       auto-detects this machine's tailscale IP; override DASH_HOST in .env

  Autostart at logon (open an elevated prompt for the firewall rule):
    .\scripts\autostart.ps1 register
"@
