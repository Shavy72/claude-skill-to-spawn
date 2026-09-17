# /to-spawn local — alle Ticket-Sessions einer Spec auf einmal starten (Windows Terminal, ein Fenster).
# Aufruf (Repo-Wurzel):  pwsh -File ~/.claude/skills/to-spawn/spawn_local.ps1 -Spec 182 [-Tickets 188,189,190] [-OhneWache] [-Window 1] [-DryRun]
# Liest docs/agents/manifests/spec-<S>.json (Ticket-Nummern), öffnet je Ticket einen Tab `bau <N>` und
# (sofern nicht -OhneWache) zuerst einen Tab `wache <S>`. Prüft danach per scripts/sessions_stand.py.
param(
    [Parameter(Mandatory = $true)][int]$Spec,
    [int[]]$Tickets = @(),
    [switch]$OhneWache,
    [int]$Window = -1,      # -1 = neues Fenster, sonst wt-Fenster-ID (z. B. 1 = bestehendes erstes Fenster)
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = (Get-Location).Path
$manifest = Join-Path $repo "docs/agents/manifests/spec-$Spec.json"
if (-not (Test-Path "./scripts/bau.py")) { throw "Kein ./scripts/bau.py — im Repo-Wurzelordner ausführen." }
if (-not (Get-Command wt -ErrorAction SilentlyContinue)) { throw "wt (Windows Terminal) nicht gefunden." }

if ($Tickets.Count -eq 0) {
    if (-not (Test-Path $manifest)) { throw "Manifest fehlt: $manifest — erst /to-tickets." }
    $m = Get-Content $manifest -Raw | ConvertFrom-Json -AsHashtable
    $Tickets = @($m.tickets.Keys | ForEach-Object { [int]$_ } | Sort-Object)
}

# Schon laufende Prozesse nie doppelt starten (Duplikat auf demselben Worktree = Gefahr, 17.09.2026 #187).
$laufend = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='claude.exe'" |
    Where-Object { $_.CommandLine -match 'bau\.py (\d+)|wache\.py (\d+)|duoplus-bau[\\/](\d+)-' } |
    ForEach-Object { [int]($Matches[1] + $Matches[2] + $Matches[3]) } | Sort-Object -Unique
# Geschlossene Tickets nicht mehr starten (bau.py würde sofort enden, der Tab bliebe als Leiche).
$zu = @()
if (Get-Command gh -ErrorAction SilentlyContinue) {
    $zu = @(gh issue list --state closed --limit 200 --search "Spec #$Spec in:body" --json number --jq '.[].number' 2>$null | ForEach-Object { [int]$_ })
}
$cmds = @()
if (-not $OhneWache -and ($laufend -notcontains $Spec)) { $cmds += "wache $Spec" }
foreach ($n in $Tickets) {
    if ($zu -contains $n) { Write-Host "#$n ist geschlossen — übersprungen" -ForegroundColor DarkGray; continue }
    if ($laufend -contains $n) { Write-Host "#$n läuft schon — übersprungen" -ForegroundColor Yellow; continue }
    $cmds += "bau $n"
}
if ($cmds.Count -eq 0) { Write-Host "Nichts zu starten."; exit 0 }

# WICHTIG: keine Umgebungs-Variablen und keine ';' in den -Command-Text (wt liest ';' als Tab-Trenner).
# Env (Transkript-Persistenz) setzt bau.py/wache.py selbst.
$parts = @()
foreach ($c in $cmds) {
    $parts += @("new-tab", "--title", "`"$c`"", "-d", "`"$repo`"", "pwsh", "-NoExit", "-Command", "`"$c`"")
    $parts += ";"
}
$parts = $parts[0..($parts.Count - 2)]
$argLine = ($parts -join " ")
if ($Window -ge 0) { $argLine = "-w $Window " + $argLine }

Write-Host ("Starte {0} Tabs: {1}" -f $cmds.Count, ($cmds -join ", "))
if ($DryRun) { Write-Host "wt $argLine"; exit 0 }
Start-Process wt -ArgumentList $argLine
Start-Sleep -Seconds 10
$env:PYTHONIOENCODING = "utf-8"
python ./scripts/sessions_stand.py $Spec
