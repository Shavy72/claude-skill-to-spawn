# /to-spawn srv — Bau-Sessions einer Spec per tmux auf dem Bau-Server starten (vom Laptop aus).
# Aufruf: pwsh -File ~/.claude/skills/to-spawn/spawn_srv.ps1 -Spec 192 [-Tickets "193,194,195"] [-OhneWache] [-DryRun] [-Zielserver bau-server]
# Hinweis: Parameter heisst -Zielserver statt -Host, weil $Host eine reservierte PowerShell-Variable ist.
# Ruft scripts/spawn_srv.sh auf dem Server auf (SSH), gibt dessen Ausgabe direkt aus.
param(
    [Parameter(Mandatory = $true)][int]$Spec,
    [string]$Tickets = "",
    [switch]$OhneWache,
    [switch]$DryRun,
    [string]$Zielserver = "bau-server"
)
$ErrorActionPreference = "Stop"

$sshArgs = @("scripts/spawn_srv.sh", "$Spec")
if ($Tickets -ne "") { $sshArgs += @("--tickets", $Tickets) }
if ($OhneWache) { $sshArgs += "--ohne-wache" }
if ($DryRun) { $sshArgs += "--dry-run" }

$remoteCmd = "cd ~/duoplus-management && bash " + ($sshArgs -join " ")
Write-Host ("ssh {0} `"{1}`"" -f $Zielserver, $remoteCmd)
ssh $Zielserver $remoteCmd
