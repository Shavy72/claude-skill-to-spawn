# Installer für /to-spawn: Skill nach ~/.claude/skills/to-spawn, Profil-Funktionen bau/wache/sessions,
# optional die Repo-Skripte (repo-scripts/ → <Repo>/scripts).
# Aufruf: pwsh -File install.ps1 [-Repo <Pfad-zum-Repo>]
param([string]$Repo = "")
$ErrorActionPreference = "Stop"
$hier = Split-Path -Parent $MyInvocation.MyCommand.Path
$ziel = Join-Path $env:USERPROFILE ".claude\skills\to-spawn"
New-Item -ItemType Directory -Force $ziel | Out-Null
foreach ($f in @("SKILL.md", "spawn_local.ps1")) { Copy-Item (Join-Path $hier $f) (Join-Path $ziel $f) -Force }
Write-Host "Skill installiert: $ziel"

# PowerShell-Profil: bau / wache / sessions (nur ergänzen, nie überschreiben)
$profilPfad = $PROFILE.CurrentUserAllHosts
if (-not (Test-Path $profilPfad)) { New-Item -ItemType File -Force $profilPfad | Out-Null }
$profil = Get-Content $profilPfad -Raw
$block = @'

# --- to-spawn (github.com/Shavy72/claude-skill-to-spawn): läuft in jedem Repo mit ./scripts/bau.py ---
function bau {
    if (-not (Test-Path "./scripts/bau.py")) { Write-Error "bau: kein ./scripts/bau.py im aktuellen Ordner"; return }
    if ($args.Count -eq 0) { Write-Host "Usage: bau <TicketNr> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>]"; return }
    python "./scripts/bau.py" @args
}
function wache {
    if (-not (Test-Path "./scripts/wache.py")) { Write-Error "wache: kein ./scripts/wache.py im aktuellen Ordner"; return }
    if ($args.Count -eq 0) { Write-Host "Usage: wache <SpecNr> [--dry-run] [--print-prompt] [--model <m>] [--takt <s>]"; return }
    python "./scripts/wache.py" @args
}
function sessions {
    if (-not (Test-Path "./scripts/sessions_stand.py")) { Write-Error "sessions: kein ./scripts/sessions_stand.py im aktuellen Ordner"; return }
    $env:PYTHONIOENCODING = "utf-8"; python "./scripts/sessions_stand.py" @args
}
'@
foreach ($fn in @("bau", "wache", "sessions")) {
    if ($profil -match "function $fn\b") { Write-Host "Profil: function $fn existiert schon — unverändert" }
}
if ($profil -notmatch "function bau\b" -or $profil -notmatch "function wache\b" -or $profil -notmatch "function sessions\b") {
    Add-Content -Path $profilPfad -Value $block
    Write-Host "Profil ergänzt: $profilPfad (fehlende Funktionen angehängt; doppelte Definitionen bitte von Hand bereinigen)"
}

# Repo-Skripte
if ($Repo) {
    $scripts = Join-Path (Resolve-Path $Repo) "scripts"
    New-Item -ItemType Directory -Force $scripts | Out-Null
    foreach ($f in Get-ChildItem (Join-Path $hier "repo-scripts") -File) {
        $zielDatei = Join-Path $scripts $f.Name
        if (Test-Path $zielDatei) { Write-Host "übersprungen (existiert): $zielDatei"; continue }
        Copy-Item $f.FullName $zielDatei
        Write-Host "kopiert: $zielDatei"
    }
    $man = Join-Path (Resolve-Path $Repo) "docs\agents\manifests"
    New-Item -ItemType Directory -Force $man | Out-Null
    $def = Join-Path $man "_default.json"
    if (-not (Test-Path $def)) { Copy-Item (Join-Path $hier "repo-scripts\_default.json") $def; Write-Host "kopiert: $def" }
}
# Glocke: Claude Code schlägt am Zug-Ende die Terminal-Glocke → 🔔 am Tab, wenn eine Session fertig ist.
$cj = Join-Path $env:USERPROFILE ".claude.json"
if (Test-Path $cj) {
    try {
        $roh = Get-Content $cj -Raw
        if ($roh -notmatch '"preferredNotifChannel"\s*:\s*"terminal_bell"') {
            Copy-Item $cj "$cj.bak-to-spawn" -Force
            $obj = $roh | ConvertFrom-Json -AsHashtable
            $obj["preferredNotifChannel"] = "terminal_bell"
            ($obj | ConvertTo-Json -Depth 50) | Set-Content $cj -Encoding utf8
            Write-Host "Glocke gesetzt: preferredNotifChannel = terminal_bell (Sicherung $cj.bak-to-spawn). Gilt für neue Sessions."
        } else { Write-Host "Glocke schon gesetzt." }
    } catch { Write-Warning "Glocke nicht gesetzt ($_) — von Hand: in einer Claude-Session /config → Notifications → terminal_bell" }
}
Write-Host "Fertig. Neues Terminal öffnen (Profil neu laden), dann im Repo: /to-spawn <SpecNr>"
