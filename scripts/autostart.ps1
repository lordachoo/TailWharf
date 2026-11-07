# Windows autostart + firewall helper for the Tailnet AI Dashboard.
# Usage (from repo root):
#   .\scripts\autostart.ps1 register    # scheduled task at logon + tailnet-scoped firewall rule
#   .\scripts\autostart.ps1 unregister
param([Parameter(Position=0)][string]$Action = "register")

$Root = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")).Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$TaskName = "tailnet-dash"

if ($Action -eq "register") {
    if (-not (Test-Path $Py)) { Write-Error "venv missing — run scripts\setup.ps1 first"; exit 1 }
    $taskAction = New-ScheduledTaskAction -Execute $Py -Argument "run.py" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Scheduled task '$TaskName' registered (starts at logon)."
    try {
        netsh advfirewall firewall delete rule name="tailnet-dash" 2>$null | Out-Null
        netsh advfirewall firewall add rule name="tailnet-dash" dir=in action=allow protocol=TCP localport=8088 remoteip=100.64.0.0/10 | Out-Null
        Write-Host "Firewall rule added (inbound 8088 from tailnet 100.64.0.0/10 only)."
    } catch { Write-Warning "Firewall rule needs an elevated prompt — rerun as Administrator." }
    Write-Host "Start now:  Start-ScheduledTask -TaskName $TaskName"
} elseif ($Action -eq "unregister") {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    netsh advfirewall firewall delete rule name="tailnet-dash" 2>$null | Out-Null
    Write-Host "Removed task and firewall rule."
} else {
    Write-Host "Usage: autostart.ps1 register|unregister"
}
