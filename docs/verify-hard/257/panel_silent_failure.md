# Prüfpanel #257 — silent-failure-hunter

Prüfgegenstand: `git diff 196aa73` (uncommittet) im Worktree `/home/bau/wt/skill-257`, Zweig `ticket-257`,
plus neue Dateien `to_spawn/speicher.py`, `to_spawn/vertrauen.py`, `skripte/hooks/staffel_stop.py`.
Nur gelesen, keine Tests gestartet. Live-Nachschau (nur lesend) auf dem Bau-Server: `pgrep -x claude` = 15 (ein
Prozess je Session, Zählung von `speicher.claude_sessions` trifft), `/run/systemd/transient/tmux-bau.service`
aktiv, keine dauerhafte Unit, `/swapfile` 16 GiB in fstab, DuoPlus-Label `checkpoint:human` = Farbe `fbca04`,
Beschreibung „Orchestrator stoppt hier und zeigt Diff + Screenshot“.

Datum: 2026-09-21 · Spezifikation: `docs/PLAN_257_fremdrepo.md` (B1–B10 + Nachtrag Paket B).

## Befunde

### S1 · mittel · `nest/nest_server.sh:296-301` — transiente Unit: Datei geschrieben, aber nie `enable` → nach dem Neustart kein geschützter tmux-Server
**Szenario:** Genau der Zustand des Bau-Servers heute (transienter `tmux-bau.service` per `systemd-run`, Unit-Datei fehlt).
Der Zweig schreibt `/etc/systemd/system/tmux-bau.service` + `daemon-reload` und meldet „dauerhafte Unit greift beim
nächsten Neustart“. Ohne `systemctl enable` gibt es keinen Symlink in `multi-user.target.wants/`; nach dem Neustart
startet nichts, der erste `tmux new` legt wieder einen ungeschützten Server an (OOMScoreAdjust 0, kein Restart).
`systemctl enable` geht in diesem Zustand nicht (systemd lehnt „transient or generated“ ab) — die Meldung ist falsch,
der Schutz ist auf dem Live-Server eine Einmal-Sache.
**Fix:** Im transienten Zweig den Symlink selbst legen (`install -d …/multi-user.target.wants && ln -sf "$TMUX_UNIT"
/etc/systemd/system/multi-user.target.wants/tmux-bau.service`, danach `daemon-reload`) und die Meldung darauf prüfen.

### S2 · mittel · `nest/nest_server.sh:302-307` — läuft schon ein normaler tmux-Server des Nutzers, meldet `enable --now` „aktiv“, der Dienst dreht aber im Neustart-Kreis
**Szenario:** Nest auf einem Rechner, wo `bau` schon Sessions in einem gewöhnlichen tmux-Server (ohne systemd) hat.
`tmux -D` verbindet sich mit dem vorhandenen Socket, hat kein Terminal, Exit 1 → `Restart=always`/`RestartSec=2`
= Neustart alle 2 s, `enable --now` liefert trotzdem 0, das Skript sagt „tmux-bau.service aktiv (OOMScoreAdjust=-900)“.
Die laufenden Sessions bleiben ungeschützt, niemand sieht es.
**Fix:** Vor dem Start prüfen, ob `tmux -S /tmp/tmux-<uid>/default list-sessions` bzw. `pgrep -u "$NUTZER" -x tmux`
einen fremden Server zeigt → nur `enable`, `warn` „läuft ungeschützt, greift nach `tmux kill-server` oder Neustart“.

### S3 · mittel · `to_spawn/gh.py:163` (+ `to_spawn.py:179-183`, `install.sh:76-88`, `install.ps1:84`) — `gh label create --force` überschreibt Farbe + Beschreibung eines vorhandenen Labels bei jedem `setup`/`install`
**Szenario:** `install.sh --repo /home/bau/duoplus-management` oder ein bloßes `to_spawn.py setup` (Anzeige-Pfad ruft
das Label ebenfalls jedes Mal) setzt das DuoPlus-Label `checkpoint:human` still von `fbca04` / „Orchestrator stoppt
hier …“ auf `d93f0b` / „Ticket wartet auf menschliche Abnahme (to-spawn)“. Kein Hinweis, keine Rückfrage; die
Rückgabe `True` heißt „vorhanden“, tatsächlich wurde geändert. stderr von `gh` wird geschluckt (`capture_output`).
**Fix:** Erst `gh label list --json name` (bzw. `gh label list --search`) — Label da → nichts tun, sonst `create`
ohne `--force`; bei Exit ≠ 0 die letzte stderr-Zeile in die Warnung.

### S4 · mittel · `skripte/bau.py:822-825` — Exit 5 nach stundenlangem Blocker-Warten: Fenster schließt sich, Grund verschwindet, Aufpasser meldet später „Start fehlgeschlagen“
**Szenario:** `spawn_srv.sh` und der Aufpasser prüfen den Speicher VOR dem Fenster, während `bau.py` noch keinen
`claude` gestartet hat (Blocker offen) — die Zählung sieht die wartenden Fenster nicht. Schließt der Blocker
Stunden später, prüft `bau.py` erneut, findet 6+ Sessions, druckt nach stderr und beendet sich; `bash -lc` schließt
das tmux-Fenster, die Zeile ist weg. Der Aufpasser startet im nächsten Tick neu (Regel 1); ist der Speicher zwischen
seiner Prüfung und der von `bau.py` wieder voll (`--resume`-Start, bau.py Exit 5 nach Sekunden), meldet er
„Start fehlgeschlagen (Fenster gleich wieder weg)“ (`aufpasser.py:1578-1586`) und nach `START_MAX` „startet nicht —
braucht David“ — beides ohne den echten Grund. Gleiches Muster in `wache.py:127-130`.
**Fix:** In `bau.py`/`wache.py` bei „voll“ nicht beenden, sondern wie `auf_blocker_warten` im Takt warten (0 Token,
Fenster bleibt, Grund alle N Minuten loggen), Exit 5 nur mit `--sofort`/Probesitz. Mindestens: den Grund in die
Sessions-Datei `.to-spawn/sessions/<N>.json` oder das Aufpasser-Log schreiben, damit die Meldung stimmt.

### S5 · mittel · `skripte/spawn_srv.sh:184-190` — Speicherprüfung: jeder Exit außer 5 gilt als „frei“
**Szenario:** `to_spawn.py speicher` stirbt mit Exit 1 (Import-Fehler, kaputte `config.json`, falsches `$PY`).
`|| rc=$?` fängt es, `[ "$rc" -eq 5 ]` greift nicht, das Fenster startet; die Fehlermeldung steckt in
`$SPEICHER_STAND` und wird nie gezeigt. Der Speicher-Schutz ist dann still aus — genau das Ausfallbild vom 21.09.
Gleiches Muster `spawn_srv.sh:67`: scheitert `hauptzweig`, gilt still `master` (Fehler nach `/dev/null`); im
`main`-Repo endet `git merge --ff-only origin/master` mit der irreführenden Meldung „eigene Commits oder dreckiger Baum“.
**Fix:** `if [ "$rc" -ne 0 ] && [ "$rc" -ne 5 ]; then echo "Speicherprüfung kaputt (Exit $rc): $SPEICHER_STAND" >&2; exit 2; fi`
(fail-closed, Fehler sichtbar). Für `hauptzweig`: Exit prüfen statt `|| echo master`, stderr durchreichen.

### S6 · mittel · `skripte/bau.py:190` + `repo-scripts/_default.json` (`{REPO}`) — Fremd-Repo ohne GitHub-Origin bekommt still „Shavy72/duoplus-management“ als maßgebliches Repo in den Prompt
**Szenario:** B10-Satz „maßgeblich ist das Repo im Arbeitsordner ({REPO})“ und `gh api repos/{REPO}/issues/{N}` werden
mit dem Rückfall `GH_REPO = repo_aus_origin("Shavy72/duoplus-management")` gefüllt, sobald `origin` fehlt oder nicht
auf github.com zeigt (GitLab, lokaler Bare-Klon). Die Session liest dann DuoPlus-Issues und arbeitet mit einem
falschen Repo-Bezug — das Gegenteil dessen, was B10 verhindern soll. Vorher stand DuoPlus fest im Text; neu ist,
dass der Rückfall so aussieht, als wäre er ermittelt.
**Fix:** Rückfall entfernen: ohne GitHub-Origin `log.error` + Exit 2 in `bau.py`/`wache.py` (oder `GH_REPO` aus
`.to-spawn/config.json` Feld `repo` lesen und erst dann abbrechen).

### S7 · mittel · `to_spawn/vertrauen.py:35-46, 49-75` — Lese-Ändere-Schreibe auf `~/.claude.json` ohne Sperre neben 6–15 laufenden Claude-Sessions
**Szenario:** `bau.py` liest die 79-KB-Datei, ergänzt `projects[wt-N].hasTrustDialogAccepted` und ersetzt sie per
`os.replace` — bei JEDEM neuen Ticket-Worktree. Claude Code schreibt dieselbe Datei laufend (Projekt-Verlauf,
`lastSessionId`, MCP-/OAuth-Stand). Schreibt eine Session zwischen `_lade` und `os.replace`, ist ihr Schreibvorgang
weg; niemand merkt es (still verlorene Einstellung, schlimmstenfalls ein Login-Stand). Gleiches Rennen zwischen zwei
gleichzeitig startenden `bau.py` (Aufpasser + Hand-Start).
**Fix:** `fcntl.flock` auf `~/.claude.json.lock` um Lesen + Schreiben, direkt vor dem Schreiben erneut laden und nur
den einen Schlüssel setzen; Schreiben bleibt atomar. Windows: `msvcrt.locking` oder Retry mit kurzer Wartezeit.

### S8 · mittel · `to_spawn/gh.py:145-147` — ohne `origin/HEAD` gewinnt `origin/master` vor `origin/main`
**Szenario:** Repo mit Hauptzweig `main`, das noch einen alten `master` auf origin hat, ohne `origin/HEAD`
(`git init` + `remote add`, Worktree-Klon, `git clone --single-branch`). `hauptzweig()` liefert `master`;
`blocker_offen` sucht die Blocker-Commits auf dem toten Zweig → jeder Blocker gilt als „kein Commit auf
origin/master“, alle Tickets warten ewig (fail-closed, aber mit falschem Grund); `spawn_srv.sh` spult auf den alten
Zweig vor oder bricht ab.
**Fix:** Stufe 2 ergänzen: `git ls-remote --symref origin HEAD` (fragt den Server) oder `git remote set-head origin -a`
einmalig in `config.sicherstellen`; Stufe 3 nur nehmen, wenn genau EINER von `master`/`main` existiert, sonst
Warnung + Konfig-Feld verlangen.

### S9 · niedrig · `to_spawn/bau_log.py:96-101` + `to_spawn/hooks.py:243-247, 320, 355` — Doppelte `session_start`/`handoff`-Zeilen, wenn die Laufdatei den Ort wechselt
**Szenario:** Erste Stop-Hook-Zeile landet im Worktree; der Launcher löscht den Worktree; die nächste Hook-Runde
schreibt in den Hauptbaum. `_hat_zeile(repo, …)` prüft nur den aktuellen Ort (Hauptbaum) und schreibt
`session_start`/`handoff` erneut. `lese(hauptbaum=…)` dedupliziert nur byte-gleiche Zeilen, die neuen haben andere
Zeitstempel → `zusammenfassung` zählt mehr `starts`. Verloren geht nichts (B9 erfüllt), aber Kennzahlen (Staffel-
Zähler, Probesitz Punkt 6 „Staffel 2 gestartet“) können zu hoch ausfallen.
**Fix:** `_hat_zeile` mit `bau_log.lese(repo, ticket, hauptbaum=bau_log.log_rueckfall())` lesen.

### S10 · niedrig · `to_spawn/bau_log.py:96-101` — Rückfall-Laufdatei im Hauptbaum wird nie mehr übertragen oder aufgeräumt
**Szenario:** Nach dem letzten `eintrag` löscht der Launcher den Worktree, die letzte `session_ende`-Zeile (Token)
landet in `<Hauptbaum>/.to-spawn/bau_log/<N>.jsonl`. Kein `eintrag` läuft mehr → die versionierte Datei bleibt ohne
diese Zeile, die Rückfall-Datei bleibt liegen (nur `sessions_stand`/`probesitz` lesen sie). Vorher fiel die Zeile
ganz weg, also Verbesserung — aber die versionierte Datei ist weiterhin unvollständig, ohne Hinweis.
**Fix:** `bau.py` beim Session-Ende (nach dem Worktree-Löschen) `eintrag_schreiben`-Äquivalent für die Rückfall-Zeilen
aufrufen oder die Rückfall-Datei im Bau-Log-Kommentar des Tickets nennen.

### S11 · niedrig · `to_spawn/config.py:223` — `sicherstellen` ändert bei jedem Start die versionierte `.gitignore` im Hauptbaum, Meldung nur als `log.info`
**Szenario:** Fremd-Repo ohne `.to-spawn/*`-Zeilen: der erste `bau <N>`/`install.sh --repo` hängt den Block an die
getrackte `.gitignore` → dreckiger Hauptbaum. Ändert origin später die `.gitignore`, verweigert
`git merge --ff-only` in `spawn_srv.sh` mit „dreckiger Baum … nicht stashen“, ohne dass der Nutzer weiß, wer die Datei
angefasst hat. Enthält die `.gitignore` bereits `.to-spawn/` (ganzer Ordner), bleibt `config.json` trotz
`!.to-spawn/config.json` ignoriert (git kann aus einem ignorierten Ordner nichts ausnehmen) — still unversioniert.
**Fix:** In `install.sh`/`setup` die Änderung als Zeile ausgeben („.gitignore ergänzt — bitte mit config.json
committen“); `gitignore_ergaenzen` warnt, wenn `.to-spawn/` oder `.to-spawn` schon als Ordner-Muster drinsteht.

### S12 · niedrig · `to_spawn/speicher.py:37-43` — Test-Türen `TO_SPAWN_SPEICHER_MEMINFO`/`_PROC` gelten überall, auch in Produktion
**Szenario:** Die Variablen werden aus jeder Umgebung übernommen; `bau.py` reicht sein Environment an die
Claude-Session weiter, die es an Hooks und Unterprozesse vererbt. Steht die Variable versehentlich in einer Shell
(z. B. nach einem Handtest), sieht jeder Starter „64 GiB frei“. `conftest.py` löscht `TO_SPAWN_*` nur innerhalb
pytest. Heute kein Weg gefunden, auf dem das in Produktion gesetzt wird → niedrig.
**Fix:** In `platz_frei` eine Zeile ins Log, sobald eine Tür-Variable gesetzt ist („Speicher: Attrappe aktiv“),
damit ein versehentlicher Übergriff im Fenster sichtbar wird.

### S13 · niedrig · `to_spawn/aufpasser.py:1552-1571` + `spawn_srv.sh:176-194` — Zählung sieht wartende Fenster nicht
**Szenario:** Fenster, deren `bau.py` noch auf Blocker wartet, haben keinen `claude`-Prozess; Aufpasser und
`spawn_srv.sh` starten so beliebig viele Fenster (Staffel-Pause hilft nur beim Sofort-Start). Die Obergrenze greift
erst im `bau.py` selbst — siehe S4. Kein eigener Fix nötig, wenn S4 (warten statt Exit) umgesetzt wird.

### S14 · niedrig · `nest/nest_server.sh:170-182` — awk-Ersetzung löscht bis Dateiende, wenn ein Start-Marker ohne End-Marker in der `.bashrc` steht
**Szenario:** Nutzer hat den Block von Hand gekürzt (End-Marker weg). `drin=1` wird nie zurückgesetzt, alles nach dem
Start-Marker fällt weg, `mv` überschreibt die `.bashrc` ohne Sicherung. Live-`.bashrc` hat beide Marker (Z. 130/136).
**Fix:** Vor dem `mv` prüfen, dass Start- und End-Marker gleich oft vorkommen, sonst `warn` + Block nur anhängen;
`.bashrc.vor-nest` als Sicherung behalten.

## Geprüft und in Ordnung
- `nest_server.sh` 5b: `/proc/swaps`-, fstab- (`^/swapfile[[:space:]]`) und `exit-empty`-Prüfungen sind idempotent;
  `tmux -D` setzt `exit-empty off` ohnehin; `set -euo pipefail` + alle `grep -q` stehen in `if`/`!`-Kontext.
- `speicher.platz_frei`: `frei_mib None` gilt nicht als frei, solange die Prozesszählung geht (nur beide `None` = frei);
  `claude -p`-Helfer werden ausgenommen; Live-Zählung stimmt (15 Prozesse = 15 Sessions).
- `probesitz`: `offene_punkte(ohne_nicht_konfiguriert=True)` wird nur in `pruefe_mail` benutzt, `alle_gruen`/Exit-Code
  behalten Punkt 3 rot; Grund-Text `staging.url fehlt` stimmt mit `_rot(3, …)` überein.
- `config.sicherstellen`: vorhandene Datei bleibt byte-gleich; `worktree_basis` nur beim Anlegen.
- `waechter_lauf._log_repo(versioniert=True)` und `eintrag` weichen nie in den Hauptbaum aus (versionierte Datei).
- `skripte/hooks/staffel_stop.py` byte-gleich mit der DuoPlus-Kopie; `install.sh` kopiert `skripte/` mit `cp -a` samt `hooks/`.
- `spawn.py` Unix-Pfad: fehlendes `server_repo` bricht mit klarer Meldung ab (kein blinder ssh); Exit 5 von
  `spawn_srv.sh` wird als Rückgabewert durchgereicht.
- `conftest.py`: `TO_SPAWN_*`-Reinigung + Speicher-Attrappe verhindern, dass die Suite in die laufende Session schreibt.
