---
name: stand
description: "Kurzer Ticket-Stand der Spec, in deren Session /stand läuft (Aufseher, Bau-Session, Klon ~/duoplus-<S>): ein Satz „x von y fertig, bauen gerade …“, danach Session-Check und vollautonomes Klären von allem, was den Bau aufhält. Lokal und auf dem Bau-Server gleich. Trigger: /stand, „wie ist der Stand“, „wie weit ist Spec X“, „wie viele Tickets sind zu“."
---

# /stand — ein Satz Stand, dann Sessions sauber halten

Aufruf: `/stand` (Spec dieser Session) · `/stand <Spec-Nr>` · alte Tabellen nur auf Wunsch: `--details` (eine Spec) bzw. `--alle`.

## Ablauf

1. Skript ausführen (Bash, im aktuellen Ordner; Bau-Server `python3`):
   ```bash
   PYTHONIOENCODING=utf-8 python ~/.claude/skills/stand/stand.py [<Spec-Nr>] 2>/dev/null
   ```
   Spec-Erkennung im Skript: Argument → `TO_SPAWN_WACHE_SPEC` (Aufseher) → `TO_SPAWN_SPEC` (Bau) → Ordner `duoplus-<S>` → tmux `spec-<S>`. Läuft hier keine Session der Spec, prüft es per ssh den Bau-Server.
2. Den **ersten Satz** (fett) unverändert als Zeile 1 an David. Keine Tabellen, keine anderen Specs.
   Steht eine Zeile `Hindernisse (24 h): …` darunter (Hindernis-Log des Hausmeisters, z. B. Platte voll, #1433), sie unverändert als Zeile 2 mitgeben. Nur anzeigen — keine Mail, keine Benachrichtigung.
3. Steht danach `Sessions: ✅` → fertig, eine Zeile „Sessions laufen sauber.“
4. Steht `⚠️` → **ohne Rückfrage** jeden Fund klären, bis der Bau weiterläuft (`ort=` im Kommentar sagt wo; Bau-Server = `ssh bau-server`):
   - **Aufseher läuft nicht / Ticket frei, aber keine Session:** Bau-Server → `python3 ~/.claude/skills/to-spawn/skripte/aufpasser.py` einmal laufen lassen (startet fehlende Fenster, weckt stille, mit Sicherung). PC → `wache <S>` bzw. `bau <N>` laut Skill `to-spawn` (vorher `sessions <S>`, nie Duplikat).
   - **VERWAIST:** nie killen ohne `sessions <S>`; Aufpasser setzt per `--resume` fort. Sonst Regeln aus Skill `to-spawn`.
   - **Ticket zu, Session noch an:** Fenster schließen erst, wenn `sessions <S>` nicht `läuft` zeigt (Aufpasser macht das).
   - **🟥 hängt (Gate rot, Mensch nötig, Blockiert):** letzten Ticket-Kommentar + Fenster lesen (`tmux capture-pane -pt spec-<S>:'bau <N>'`), Ursache beheben (z. B. fremd-roter Test → Ursache suchen, Session per tmux anstoßen mit konkretem Auftrag, Text und Enter getrennt senden). Nur das Wurzelproblem, kein Pflaster.
   - Danach Skript erneut ausführen = Beweis.
5. Zum Schluss je geklärtem Fund 1 Zeile „was war, was getan“. Bleibt etwas offen → 1 Zeile, warum.

## Grenzen (wann doch David)
- Live-Freigabe, Löschen, Kosten → nie selbst; 1 Satz + fertiger Befehl.
- `wartet auf dich` (👤, Label `checkpoint:human`) ist kein Fehler → nur im Satz nennen.
- Nie `bau.py` killen ohne `sessions <S>`, nie fremde Spec anfassen.

## Pflege
Quelle = Skill-Repo `Shavy72/claude-skill-to-spawn`, Ordner `skills/stand/` (seit #1433). `install.sh`/`install.ps1` bringen ihn nach `~/.claude/skills/stand/` (lokal und Bau-Server gleich). Nie im installierten Ordner ändern.

## Davids Anpassungen
- 2026-11-17: angelegt (Übersicht + Tabellen).
- 2026-11-18: nur noch die Spec der aktuellen Session in einem Satz („x von y fertig, welche bauen“), danach Session-Check und vollautonomes Klären. Tabellen nur noch mit `--details`/`--alle`.
- 2026-11-21: Hindernis-Zeile (offene Hindernisse der letzten 24 h, eine Zeile) und Umzug ins Skill-Repo (#1433).
