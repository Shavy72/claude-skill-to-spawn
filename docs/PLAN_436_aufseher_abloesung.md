# Plan #436 — Aufsicht erzwingt Aufseher-Ablösung + Rückkehr auf Opus nach Limit

Quelle: Ticket duoplus-management#436 (Spec #399, E17, E2, E1). Basis: Branch `ticket/431-respawn-sop` (Skill `respawn`, noch nicht in `main`).

## Ziel (Akzeptanz)
1. Rot-Beweis: Test „Limit vorbei → Modell wieder Opus“ ist vorher rot.
2. Test: Kontext über Grenze → Ablösung startet ohne Zutun des Aufsehers; Nachfolger startet mit Handoff + Stand-Datei.
3. Ablösung läuft über den Skill `respawn` (kein zweiter Weg).

## Design (Kästen und Türen)
- **Kasten `to_spawn/respawn.py`** bekommt eine zweite Tür `aufseher_abloesen(repo, spec, ziel, *, werkzeug=None, warte_max=…) -> AufseherErgebnis` (Ergebnis = exit, zeile, handoff: Path|None, start_prompt: str). Sie fährt dieselben SOP-Schritte wie `abloesen` für den Aufseher:
  a) Handoff-Auftrag (gleicher Text-Baustein `_handoff_auftrag`, Aufseher-Pfade `docs/HANDOFF_<tag>_waechter_<S>.md` + `docs/handoffs/START_<tag>_waechter_<S>.txt`) per `Werkzeug.tippen` in das Pane des Aufsehers (`ziel` = `$TMUX_PANE` des wache.py-Prozesses);
  d) warten, bis beide Dateien frisch, nicht leer und stabil sind (gleiche Prüfung wie `_warte_dateien` / `_finde` — generalisieren über einen Schlüssel statt Ticket-Nummer, nicht kopieren).
  b/c/e übernimmt die Aufsicht selbst (siehe unten), weil der Aufseher als Kind von `wache.py` läuft (Limit-Aufsicht braucht `Popen`).
  Abbruch (Exit 2, Dateien fehlen nach `warte_max`): `WEITER_AUFTRAG`-Gegenstück ins Pane tippen („Ablösung abgebrochen, arbeite weiter“), alter Aufseher bleibt.
- **Kasten `to_spawn/waechter_lauf.py` (`fahre`)**:
  - Aufsicht-Faden misst den Kontext aus den neuen Transkript-Zeilen (Wiederverwendung `hooks.kontext`-Logik: input + cache_read + cache_creation je Aufruf; nicht neu erfinden).
  - Grenze = Handoff-Grenze aus `~/.claude/smart-zone.json` (`haupt.handoff_k` für Opus-Modelle, `haupt_nicht_opus.handoff_k` sonst; Rückfall 250/200 k). Parameter `grenze: int | None` an `fahre` als Test-Naht.
  - Kontext ≥ Grenze → genau einmal je Session `respawn.aufseher_abloesen(...)`. Exit 0 → Ablöse-Datei (Pfad aus `TO_SPAWN_WACHE_ABLOESUNG`) mit `{"handoff": …, "start": …}` schreiben; die vorhandene Abbruch-Bedingung in `wache.py` beendet die Session und startet den Nachfolger. Bau-Log-Zeile `aufseher_abloesung` (kontext, grenze, exit, zeile).
  - Ohne tmux (`TMUX_PANE` fehlt) → eine Warnung + Bau-Log-Zeile, keine Ablösung (respawn ist nur Bau-Server, Windows-Pendant eigenes Ticket).
  - **Rückkehr auf Opus:** beim Wechsel aufs Ausweich-Modell `rueckkehr_ab` merken = Reset-Zeitpunkt der Limit-Zeile + Puffer (unbekannt → jetzt + `RUECKKEHR_VORGABE_S` = 5 h). Läuft der Aufseher auf dem Ausweich-Modell und `rueckkehr_ab` ist erreicht → Session beenden, Bau-Log `waechter_modell` (von Ausweich, nach Haupt, grund „Limit vorbei“), `--resume` mit Haupt-Modell. Nach einer Limit-Pause geht es ebenfalls mit dem Haupt-Modell weiter (Limit ist offen).
- **Kasten `skripte/wache.py`**: `--abloesen` (Selbstablösung) und erzwungene Ablösung münden in denselben Nachfolger-Start. `abloese_prompt` nimmt zusätzlich die jüngste Nicht-noop-Zeile der Stand-Datei (`aufseher_stand.letzter_stand`) und — wenn vorhanden — den Start-Prompt auf. Nachfolger startet frisch mit `a.model` (Opus).

## Verworfen
- V-B: Aufseher beenden und `claude --resume <sid> -p "<Handoff-Auftrag>"` headless — zweiter Transportweg neben respawn, widerspricht Akzeptanz 3.

## Tests (mp-tdd, nur addieren)
- Rot zuerst: `fahre` mit Fake-Claude: Haupt-Modell → Limit-Zeile mit Reset in Vergangenheit/kurz → Ausweich → nach Reset wieder Haupt-Modell (`--model <haupt>` im nächsten Befehl).
- Kontext über Grenze: Fake-Transkript mit usage über `grenze` → `respawn.aufseher_abloesen` wird mit Fake-Werkzeug aufgerufen (Tippen ins Pane), Dateien entstehen → Ablöse-Datei geschrieben → Session endet; `wache.py`-Schleife startet Nachfolger, dessen Prompt Handoff-Inhalt + Stand-Zeile enthält.
- respawn-Tür: Exit 0 / Exit 2 (Dateien fehlen → Weiter-Auftrag getippt) mit Fake-Werkzeug.
- Bestehende Tests grün (volle Reihe des Skills).
