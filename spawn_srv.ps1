# /to-spawn srv — Bau-Sessions einer Spec per tmux auf dem Bau-Server starten (vom Laptop aus).
# Aufruf: pwsh -File ~/.claude/skills/to-spawn/spawn_srv.ps1 -Spec 192 [-Tickets "193,194,195"] [-OhneWache] [-DryRun] [-Zielserver bau-server]
# Hinweis: Parameter heißt -Zielserver statt -Host, weil $Host eine reservierte PowerShell-Variable ist.
# Liest server_repo, hauptzweig und ssh_ziel aus <git toplevel>/.to-spawn/config.json (kein fester Repo-Name).
# Synchronisiert den Server-Klon per fast-forward, ruft dann scripts/spawn_srv.sh auf dem Server auf (SSH)
# und gibt dessen Exit-Code weiter.
param(
    [Parameter(Mandatory = $true)][int]$Spec,
    [string]$Tickets = "",
    [switch]$OhneWache,
    [switch]$DryRun,
    [string]$Zielserver = ""
)
$ErrorActionPreference = "Stop"

$toplevel = (git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $toplevel) {
    [Console]::Error.WriteLine("FEHLER: kein Git-Repo im aktuellen Ordner — /to-spawn im Repo starten.")
    exit 2
}
$toplevel = "$toplevel".Trim()
$konfigPfad = Join-Path $toplevel ".to-spawn/config.json"
$konfig = $null
if (Test-Path -LiteralPath $konfigPfad) {
    $konfig = Get-Content -LiteralPath $konfigPfad -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Lies-Feld([object]$quelle, [string]$name) {
    if ($null -eq $quelle) { return "" }
    $wert = $quelle.PSObject.Properties[$name]
    if ($null -eq $wert -or $null -eq $wert.Value) { return "" }
    return ("" + $wert.Value).Trim()
}

$serverRepo = Lies-Feld $konfig "server_repo"
if (-not $serverRepo) {
    [Console]::Error.WriteLine("FEHLER: server_repo fehlt in $konfigPfad — Pfad des Klons auf dem Bau-Server eintragen, z. B. `"server_repo`": `"~/<repo>`".")
    exit 2
}

$hauptzweig = Lies-Feld $konfig "hauptzweig"
if (-not $hauptzweig) {
    $kopf = (git -C $toplevel symbolic-ref --short refs/remotes/origin/HEAD 2>$null)
    if ($LASTEXITCODE -eq 0 -and $kopf) {
        $hauptzweig = ("$kopf".Trim()) -replace '^origin/', ''
    } else {
        $hauptzweig = "master"
    }
}

if (-not $Zielserver) { $Zielserver = Lies-Feld $konfig "ssh_ziel" }
if (-not $Zielserver) { $Zielserver = "bau-server" }

$sshArgs = @("scripts/spawn_srv.sh", "$Spec")
if ($Tickets -ne "") { $sshArgs += @("--tickets", $Tickets) }
if ($OhneWache) { $sshArgs += "--ohne-wache" }
if ($DryRun) { $sshArgs += "--dry-run" }

# Bewusst ohne doppelte Anführungszeichen: Windows PowerShell 5.1 zerlegt sonst das SSH-Argument.
$sync = 'git fetch -q origin && [ $(git rev-parse --abbrev-ref HEAD) = {0} ] && git merge -q --ff-only origin/{0} || {{ echo FEHLER: Server-Klon nicht auf {0} oder nicht per fast-forward mit origin/{0} abgleichbar >&2; exit 4; }}' -f $hauptzweig
$remoteCmd = "cd $serverRepo && $sync && bash " + ($sshArgs -join " ")
Write-Host ("ssh {0} `"{1}`"" -f $Zielserver, $remoteCmd)
ssh $Zielserver $remoteCmd
exit $LASTEXITCODE
