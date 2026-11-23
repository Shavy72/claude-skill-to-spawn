# /to-spawn local — alle Ticket-Sessions einer Spec auf einmal starten (Windows Terminal, ein Fenster).
# Aufruf (Repo-Wurzel):  pwsh -File ~/.claude/skills/to-spawn/spawn_local.ps1 -Spec 182 [-Tickets "188,189,190"] [-OhneWache] [-Window 1] [-DryRun]
# Liest docs/agents/manifests/spec-<S>.json (Ticket-Nummern), öffnet je Ticket einen Tab `bau <N>` und
# (sofern nicht -OhneWache) zuerst einen Tab `wache <S>` und einen Tab `leitstand <S>` (Schalter leitstand.aktiv). Prüft danach per scripts/sessions_stand.py.
param(
    [Parameter(Mandatory = $true, Position = 0)][int]$Spec,   # nur Spec ist positional (#285), sonst landet die zweite Zahl einer Leerzeichen-Liste in $Window
    $Tickets = @(),   # bewusst ohne Typ (#37/#285): nie als Zahlenliste lesen lassen — unten an einer Stelle selbst zerlegt
    [switch]$OhneWache,
    [int]$Window = -1,      # -1 = neues Fenster, sonst wt-Fenster-ID (z. B. 1 = bestehendes erstes Fenster)
    [switch]$DryRun,
    [switch]$NurListe,   # #285: erkannte Ticket-Nummern ausgeben und enden (startet nichts)
    [Parameter(ValueFromRemainingArguments = $true)]$Rest = @()   # weitere Zahlen nach -Tickets (Leerzeichen-Schreibweise)
)
$ErrorActionPreference = "Stop"

# Ticket-Liste (#37, #285): die eine Stelle, die den Text in Zahlen zerlegt. Als Zahlenliste getippt
# läse die Landeseinstellung (de-DE) "1476,1477" als Tausenderzahl 14761477 (Git Bash, -File).
# Trenner: Komma, Leerraum, Semikolon; alles andere ist ein Fehler.
$Tickets = @(@($Tickets) + @($Rest) | ForEach-Object { "$_" -split '[,;\s]+' } | Where-Object { $_ -ne "" })
foreach ($t in $Tickets) {
    if ($t -notmatch '^\d+$') {
        Write-Host "FEHLER: -Tickets erwartet Zahlen, bekam '$t' (richtig: -Tickets ""51,52"")." -ForegroundColor Red
        exit 2
    }
}
$Tickets = @($Tickets | ForEach-Object { [int]$_ })  # untypisiert: bleiben Zahlen
if ($NurListe) { Write-Host ("Tickets: " + ($Tickets -join ",")); exit 0 }

# Regularien zuerst (#206): bei Weigerung startet kein einziger Tab. Eine Auswahl
# (-Tickets) geht mit in die Prüfung — jede Nummer muss im Manifest stehen.
# Kein 2>&1: unter PowerShell 5.1 mit ErrorActionPreference Stop bricht eine
# stderr-Zeile sonst das Skript ab. stderr läuft direkt durch, EAP nur hier locker.
$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
$pruefArgs = @("$PSScriptRoot/to_spawn.py", "regularien", "$Spec")
if ($Tickets.Count -gt 0) { $pruefArgs += @("--tickets", ($Tickets -join ",")) }
$eapVorher = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $py @pruefArgs
    $rc = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $eapVorher
}
if ($rc -eq 3) { Write-Host "WEIGERUNG — nichts gestartet." -ForegroundColor Red; exit 3 }
if ($rc -ne 0) { Write-Host "Regularien-Prüfer brach ab (Exit $rc) — nichts gestartet." -ForegroundColor Red; exit $rc }

$repo = (Get-Location).Path
$manifest = Join-Path $repo "docs/agents/manifests/spec-$Spec.json"
# Manifest-Ordner aus der Repo-Konfig (pfade.manifeste, L1 P4b); scheitert der Aufruf, bleibt der Wert oben.
$ErrorActionPreference = "Continue"
$manOrdner = & $py -c 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), sys.argv[3]))' $PSScriptRoot $repo manifeste
$manRc = $LASTEXITCODE
$ErrorActionPreference = $eapVorher
if ($manRc -eq 0 -and $manOrdner) { $manifest = Join-Path ("$manOrdner".Trim()) "spec-$Spec.json" }
# Ohne Repo-Weiterleitung startet bau/wache das Skill-Skript (L1 P4b, Rückfall in den Profil-Funktionen).
if (-not (Test-Path "./scripts/bau.py") -and -not (Test-Path "$PSScriptRoot/skripte/bau.py")) { throw "Kein ./scripts/bau.py — im Repo-Wurzelordner ausführen." }
if (-not (Get-Command wt -ErrorAction SilentlyContinue)) { throw "wt (Windows Terminal) nicht gefunden." }

if ($Tickets.Count -eq 0) {
    if (-not (Test-Path $manifest)) { throw "Manifest fehlt: $manifest — erst /to-tickets." }
    $m = Get-Content $manifest -Raw | ConvertFrom-Json -AsHashtable
    $Tickets = @($m.tickets.Keys | ForEach-Object { [int]$_ } | Sort-Object)
}

# Schon laufende Prozesse nie doppelt starten (Duplikat auf demselben Worktree = Gefahr, 17.09.2026 #187).
# Worktree-Name wt-<N> wie config.worktree_pfad (L1 P7b, #79).
$laufend = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='claude.exe'" |
    Where-Object { $_.CommandLine -match 'bau\.py (\d+)|wache\.py (\d+)|[\\/]wt-(\d+)\b' } |
    ForEach-Object { [int]($Matches[1] + $Matches[2] + $Matches[3]) } | Sort-Object -Unique
# Fertig gebaute Tickets nicht mehr starten (bau.py würde sofort enden, der Tab bliebe als Leiche).
# Regel nur in to_spawn/ticket_zustand.py (#279): `zu-bauen` nennt, was noch zu bauen ist; unbekannter Stand fehlt dort.
$ErrorActionPreference = "Continue"
$zuBauen = @(& $py "$PSScriptRoot/to_spawn.py" "zu-bauen" "$Spec" | ForEach-Object { [int]$_ })
$zuBauenRc = $LASTEXITCODE
$ErrorActionPreference = $eapVorher
if ($zuBauenRc -ne 0) { Write-Host "Ticket-Stand nicht für alle Tickets bestimmbar — diese werden nicht gestartet." -ForegroundColor Yellow }
$cmds = @()
if (-not $OhneWache -and ($laufend -notcontains $Spec)) { $cmds += "wache $Spec" }
# Bau-Leitstand (Live-Seite): Tab `leitstand <S>` (Profil-Funktion → skripte/leitstand.py <S> sitzung),
# nur mit Aufseher, bei leitstand.aktiv an (Exit 0) und wenn noch keine Leitstand-Session der Spec läuft.
if (-not $OhneWache) {
    $ErrorActionPreference = "Continue"
    & $py "$PSScriptRoot/skripte/leitstand.py" "$Spec" aktiv | Out-Null
    $lsAktiv = $LASTEXITCODE
    $ErrorActionPreference = $eapVorher
    $lsLaeuft = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "leitstand\.py`"? $Spec sitzung" }
    if ($lsAktiv -eq 0 -and -not $lsLaeuft) { $cmds += "leitstand $Spec" }
    elseif ($lsLaeuft) { Write-Host "leitstand $Spec läuft schon — übersprungen" -ForegroundColor Yellow }
}
foreach ($n in $Tickets) {
    if ($zuBauen -notcontains $n) { Write-Host "#$n fertig gebaut oder Stand unbekannt — übersprungen" -ForegroundColor DarkGray; continue }
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
if (-not (Test-Path "./scripts/sessions_stand.py")) { python "$PSScriptRoot/skripte/sessions_stand.py" $Spec; exit 0 }
python ./scripts/sessions_stand.py $Spec
