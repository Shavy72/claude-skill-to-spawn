# Installer für /to-spawn: Skill nach ~/.claude/skills/to-spawn (inkl. skripte/, to_spawn/, to_spawn.py),
# Alias-Skills (aliase/* → ~/.claude/skills/<alias>), Profil-Funktionen bau/aufseher/wache/sessions/leitstand,
# optional die Repo-Weiterleitungen (repo-scripts/ → <Repo>/scripts, nur fehlende) + .to-spawn/config.json.
# Jeder vorhandene Skill-Ordner (to-spawn, Alias, Kette) wird nie gelöscht, sondern nach <Skills>\_alt\<name>-<zeit> verschoben.
# Dazu die Kette: kette/skills/* → <Skills>/<name>, Regel-Blöcke kette/global/*.md → globale CLAUDE.md.
# Linux/macOS: install.sh.
# Aufruf: pwsh -File install.ps1 [-Repo <Pfad-zum-Repo>] [-Skills <Ordner>] [-ClaudeMd <Datei>]
#   -Skills    Zielordner der Skills (Vorgabe %USERPROFILE%\.claude\skills)
#   -ClaudeMd  globale CLAUDE.md für die Regel-Blöcke (Vorgabe %USERPROFILE%\.claude\CLAUDE.md)
param([string]$Repo = "", [string]$Skills = "", [string]$ClaudeMd = "")
$ErrorActionPreference = "Stop"
# Ausgabe immer UTF-8 (Umlaute in Meldungen): sonst schreibt PowerShell umgeleitet in der OEM-Codepage.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$hier = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Skills) { $Skills = Join-Path $env:USERPROFILE ".claude\skills" }
if (-not $ClaudeMd) { $ClaudeMd = Join-Path $env:USERPROFILE ".claude\CLAUDE.md" }
$ziel = Join-Path $Skills "to-spawn"
$skills = $Skills
if ((Test-Path $ziel) -and ((Resolve-Path $ziel).Path -eq (Resolve-Path $hier).Path)) {
    throw "install.ps1 läuft aus dem Zielordner ($ziel) — aus dem Git-Klon starten."
}
# Python vor dem ersten Kopieren prüfen: sonst bliebe ein halber Stand (kein STAND.json, alte Alias-Skills).
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Abbruch: python fehlt — erst Python installieren, dann install.ps1 erneut starten (nichts kopiert)."
}
$zeit = Get-Date -Format "yyyyMMdd-HHmmss"
$caches = @("__pycache__", ".pytest_cache", ".ruff_cache")
# Sichere-UndKopiere <name> <quellen>: alten Ordner <Skills>\<name> nach _alt\<name>-<zeit> verschieben
# (nie löschen), dann jede vorhandene Quelle hinein kopieren. Dateien, die es nur im alten Stand gibt,
# werden gemeldet (liegen weiter in der Sicherung); STAND.json nicht, den stempelt der Installer danach neu.
function Sichere-UndKopiere([string]$name, [string[]]$quellen) {
    $zielOrdner = Join-Path $skills $name
    $sicherung = ""
    if (Test-Path $zielOrdner) {
        $alt = Join-Path $skills "_alt"
        New-Item -ItemType Directory -Force $alt | Out-Null
        $sicherung = Join-Path $alt ($name + "-" + $zeit)
        while (Test-Path $sicherung) { $sicherung = $sicherung + "-1" }
        Move-Item $zielOrdner $sicherung
        Write-Host "Alter Stand gesichert: $sicherung"
    }
    New-Item -ItemType Directory -Force $zielOrdner | Out-Null
    foreach ($quelle in $quellen) {
        if (Test-Path $quelle) { Copy-Item $quelle $zielOrdner -Recurse -Force }
    }
    Get-ChildItem $zielOrdner -Recurse -Directory -Force | Where-Object { $_.Name -in $caches } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    if ($sicherung) {
        $basis = (Resolve-Path $sicherung).Path
        foreach ($datei in Get-ChildItem $sicherung -Recurse -File -Force) {
            $rel = $datei.FullName.Substring($basis.Length).TrimStart('\', '/')
            if ($rel -split '[\\/]' | Where-Object { $_ -in $caches }) { continue }
            if ($rel -eq "STAND.json") { continue }
            if (-not (Test-Path (Join-Path $zielOrdner $rel))) { Write-Host "Hinweis: nur im alten Stand (liegt in $sicherung): $rel" }
        }
    }
}
$quellen = foreach ($f in @("SKILL.md", "README.md", "LICENSE", "to_spawn.py", "install.sh", "install.ps1", "spawn_local.ps1", "spawn_srv.ps1",
                           "to_spawn", "skripte", "repo-scripts", "aliase", "tests", "docs", "nest", "leitstand", "kette", "review-hook", "review", "probe")) { Join-Path $hier $f }
Sichere-UndKopiere "to-spawn" $quellen
$env:PYTHONIOENCODING = "utf-8"
python (Join-Path (Join-Path $ziel "to_spawn") "skill_stand.py") --stempel $ziel --klon $hier
if ($LASTEXITCODE -ne 0) { throw "Skill-Stand nicht gestempelt: $ziel" }
Write-Host "Skill installiert: $ziel"
foreach ($aliasOrdner in Get-ChildItem (Join-Path $hier "aliase") -Directory) {
    Sichere-UndKopiere $aliasOrdner.Name @(Get-ChildItem $aliasOrdner.FullName -Force | ForEach-Object { $_.FullName })
    Write-Host "Alias installiert: $(Join-Path $skills $aliasOrdner.Name)"
}
# Kette: Skills (grill-with-docs, to-spec, ...) + globale Regel-Blöcke; alter Ordner nach _alt/, nie gelöscht.
foreach ($ketteOrdner in Get-ChildItem (Join-Path (Join-Path $hier "kette") "skills") -Directory) {
    Sichere-UndKopiere $ketteOrdner.Name @(Get-ChildItem $ketteOrdner.FullName -Force | ForEach-Object { $_.FullName })
    Write-Host "Kette-Skill installiert: $(Join-Path $skills $ketteOrdner.Name)"
}
$env:PYTHONIOENCODING = "utf-8"
python (Join-Path (Join-Path $ziel "to_spawn") "globale_regeln.py") --ziel $ClaudeMd
if ($LASTEXITCODE -ne 0) { throw "Regel-Blöcke nicht geschrieben: $ClaudeMd" }
# Ja-Sperre (PreToolUse-Hook in settings.json neben der CLAUDE.md) + Befehl `ja` nach bin/ daneben (#531)
$env:PYTHONPATH = $ziel
python -m to_spawn.ja_einrichten --claude-dir (Split-Path -Parent $ClaudeMd) --python (Get-Command python).Source
if ($LASTEXITCODE -ne 0) { throw "Ja-Sperre nicht eingetragen" }
# Review-Hook (review-hook/ → ~/.claude/hooks/review, #214) + Review-Werkzeug (review/ → ~/.claude/review, #329); Überschreib-Regel in to_spawn/review_hook.py.
python (Join-Path (Join-Path $ziel "to_spawn") "review_hook.py") --klon $hier  # Klon: nur versionierte Dateien
if ($LASTEXITCODE -ne 0) { throw "Review-Hook nicht installiert" }

# PowerShell-Profil: bau / wache / sessions (nur ergänzen, nie überschreiben)
$profilPfad = $PROFILE.CurrentUserAllHosts
if (-not (Test-Path $profilPfad)) { New-Item -ItemType File -Force $profilPfad | Out-Null }
$profil = Get-Content $profilPfad -Raw
$block = @'

# --- to-spawn (github.com/Shavy72/claude-skill-to-spawn): läuft in jedem Repo mit ./scripts/bau.py ---
function bau {
    # Rückfall ohne Repo-Weiterleitung: Skill-Skript direkt (L1 P4b).
    if (-not (Test-Path "./scripts/bau.py") -and (Test-Path "$env:USERPROFILE\.claude\skills\to-spawn\skripte\bau.py")) { python "$env:USERPROFILE\.claude\skills\to-spawn\skripte\bau.py" @args; return }
    if (-not (Test-Path "./scripts/bau.py")) { Write-Error "bau: kein ./scripts/bau.py im aktuellen Ordner"; return }
    if ($args.Count -eq 0) { Write-Host "Usage: bau <TicketNr> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>]"; return }
    python "./scripts/bau.py" @args
}
function wache {
    # Rückfall ohne Repo-Weiterleitung: Skill-Skript direkt (L1 P4b).
    if (-not (Test-Path "./scripts/wache.py") -and (Test-Path "$env:USERPROFILE\.claude\skills\to-spawn\skripte\wache.py")) { python "$env:USERPROFILE\.claude\skills\to-spawn\skripte\wache.py" @args; return }
    if (-not (Test-Path "./scripts/wache.py")) { Write-Error "wache: kein ./scripts/wache.py im aktuellen Ordner"; return }
    if ($args.Count -eq 0) { Write-Host "Usage: wache <SpecNr> (Alias von aufseher) [--dry-run] [--print-prompt] [--model <m>] [--takt <s>]"; return }
    python "./scripts/wache.py" @args
}
function sessions {
    # Rückfall ohne Repo-Weiterleitung: Skill-Skript direkt (L1 P4b).
    if (-not (Test-Path "./scripts/sessions_stand.py") -and (Test-Path "$env:USERPROFILE\.claude\skills\to-spawn\skripte\sessions_stand.py")) { $env:PYTHONIOENCODING = "utf-8"; python "$env:USERPROFILE\.claude\skills\to-spawn\skripte\sessions_stand.py" @args; return }
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
# aufseher <S>: startet den Aufseher der Spec (#428, gleiches Ziel wie wache — wache bleibt Alias).
# Eigener Block, weil Profile mit bau/wache/sessions sonst nichts Neues bekommen.
$profil = Get-Content $profilPfad -Raw
if ($profil -match "function aufseher\b") {
    Write-Host "Profil: function aufseher existiert schon — unverändert"
} else {
    Add-Content -Path $profilPfad -Value @'

# --- to-spawn Aufseher: aufseher <S> startet den Aufseher der Spec (wache <S> = alter Alias) ---
function aufseher {
    # Rückfall ohne Repo-Weiterleitung: Skill-Skript direkt (L1 P4b).
    if (-not (Test-Path "./scripts/wache.py") -and (Test-Path "$env:USERPROFILE\.claude\skills\to-spawn\skripte\wache.py")) { python "$env:USERPROFILE\.claude\skills\to-spawn\skripte\wache.py" @args; return }
    if (-not (Test-Path "./scripts/wache.py")) { Write-Error "aufseher: kein ./scripts/wache.py im aktuellen Ordner"; return }
    if ($args.Count -eq 0) { Write-Host "Usage: aufseher <SpecNr> [--dry-run] [--print-prompt] [--model <m>] [--takt <s>]"; return }
    python "./scripts/wache.py" @args
}
'@
    Write-Host "Profil ergänzt: function aufseher"
}
# leitstand <S> [unterbefehl]: Bau-Leitstand-Session der Spec in diesem Fenster (eigener Block, weil
# Profile mit bau/wache/sessions sonst nichts Neues bekommen).
$profil = Get-Content $profilPfad -Raw
if ($profil -match "function leitstand\b") {
    Write-Host "Profil: function leitstand existiert schon — unverändert"
} else {
    Add-Content -Path $profilPfad -Value @'

# --- to-spawn Bau-Leitstand: leitstand <S> startet die Leitstand-Session (Live-Seite) in diesem Fenster ---
function leitstand {
    if ($args.Count -eq 0) { Write-Host "Usage: leitstand <SpecNr> [sitzung|anweisung|url|mail|aktiv|...]"; return }
    $skill = if ($env:TO_SPAWN_HOME) { $env:TO_SPAWN_HOME } else { Join-Path $HOME ".claude/skills/to-spawn" }
    [string[]]$rest = @($args | Select-Object -Skip 1)
    if ($rest.Count -eq 0) { $rest = @("sitzung") }
    $env:PYTHONIOENCODING = "utf-8"; python (Join-Path $skill "skripte/leitstand.py") $args[0] @rest
}
'@
    Write-Host "Profil ergänzt: function leitstand"
}

# Repo-Weiterleitungen (die Logik bleibt im Skill unter skripte/)
if ($Repo) {
    $scripts = Join-Path (Resolve-Path $Repo) "scripts"
    New-Item -ItemType Directory -Force $scripts | Out-Null
    foreach ($f in Get-ChildItem (Join-Path $hier "repo-scripts") -File | Where-Object { $_.Name -ne "_default.json" }) {
        $zielDatei = Join-Path $scripts $f.Name
        if (Test-Path $zielDatei) { Write-Host "übersprungen (existiert): $zielDatei"; continue }
        Copy-Item $f.FullName $zielDatei
        Write-Host "kopiert: $zielDatei"
    }
    $man = Join-Path (Resolve-Path $Repo) "docs\agents\manifests"
    # Manifest-Ordner aus der Repo-Konfig (pfade.manifeste, L1 P4b); scheitert der Aufruf, bleibt der Wert oben.
    $manPy = 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config; print(config.pfad(config.lade(Path(sys.argv[2])), sys.argv[3]))'
    $manOrdner = python -c $manPy $ziel (Resolve-Path $Repo).Path manifeste
    if ($LASTEXITCODE -eq 0 -and $manOrdner) { $man = "$manOrdner".Trim() }
    New-Item -ItemType Directory -Force $man | Out-Null
    # Keine Regel-Kopie _default.json: Start-Text kommt aus der Skill-Vorlage; Repo-Regeln über start_text.zusatz_datei (#274 P1).
    # Ohne doppelte Anführungszeichen im -c-Text: Windows PowerShell 5.1 würde sie beim Aufruf verschlucken.
    # Konfig anlegen (+ .gitignore-Block) und Checkpoint-Label im GitHub-Repo sicherstellen (#257).
    $py = 'import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from to_spawn import config, gh; r = Path(sys.argv[2]); print(config.sicherstellen(r)); l = str(config.lade(r).get("regularien", {}).get("checkpoint_label") or "checkpoint:human"); print("Label " + l + (": vorhanden" if gh.label_sicherstellen(r, l) else ": nicht angelegt - von Hand: gh label create " + l))'
    $konfig = python -c $py $ziel (Resolve-Path $Repo).Path
    Write-Host "Konfig: $konfig"
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
# Hausmeister (#1373): tägliche Windows-Aufgabe räumt fertige Arbeitsordner auf (idempotent).
# Fehler nur melden — die übrige Einrichtung bleibt gültig.
# try/catch: unter $ErrorActionPreference = "Stop" wirft PowerShell 5.1 bei stderr-Ausgabe mit 2>&1.
try {
    $hm = python (Join-Path $ziel "to_spawn.py") aufraeumen --aufgabe-einrichten 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "$hm" } else { Write-Warning "Hausmeister-Aufgabe nicht eingerichtet: $hm" }
} catch { Write-Warning "Hausmeister-Aufgabe nicht eingerichtet: $_" }
Write-Host "Fertig. Neues Terminal öffnen (Profil neu laden), dann im Repo: /to-spawn <SpecNr> (fragt lokal/Server) · /to-spawn-local <S> · /to-spawn-remote <S>"
