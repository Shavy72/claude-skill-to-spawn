# Plan #431 — Skill `respawn`: Bau-Session nach fester SOP ablösen (a–e)

Quelle: duoplus-management#431 (Spec #399, Beschlüsse E2, E6, E7, E16). Nur Bau-Server (tmux).

## Design (Kästen und Türen)

Ein tiefes Modul `to_spawn/respawn.py` mit genau einer Tür: `abloesen(repo, spec, ticket, konfig, *, werkzeug=None, warte_max=…) -> Ergebnis`.
`Ergebnis(exit: int, zeile: str)` — `zeile` ist immer genau eine Zeile (für den Ablöse-Subagenten, E16).
Alles Wissen über tmux, Prozesse, Dateinamen, Wartezeiten und die Reihenfolge a–e lebt nur dort.

Naht für Tests: `werkzeug` (Protokoll `Werkzeug`) bündelt alle Außenwelt-Zugriffe:
- `fenster_liste() -> list[FensterInfo]` (alle tmux-Fenster aller Sitzungen: sitzung, name, ziel, pane_pid)
- `bau_prozesse() -> list[str]` (Kommandozeilen aller laufenden `bau.py`-Prozesse)
- `fenster_starten(sitzung, name, cwd, befehl) -> str` (Ziel `=spec-S:name` o. ä.)
- `tippen(ziel, text)` — Text per `send-keys -l`, kurze Pause, dann `Enter` getrennt (Regel: Text und Enter getrennt)
- `bildschirm(ziel) -> str` (capture-pane)
- `fenster_umbenennen(ziel, name)`, `fenster_schliessen(ziel)`
- `alte_session_beenden(pane_pid) -> bool` (Claude-Prozess unter dem Pane per SIGTERM, Nachweis weg)
- `jetzt() -> float`, `schlafen(s)`
Echte Umsetzung `TmuxWerkzeug`. Tests nutzen eine Fake-Klasse, die jeden Aufruf in eine Liste schreibt.

Variante B (verworfen): nur SKILL.md, Subagent tippt tmux-Befehle selbst — nicht testbar, Reihenfolge nicht erzwingbar.

## Ablauf (Reihenfolge ist Gesetz)

0. **Duplikat-Prüfung (A5):** Fenster `bau <N>` muss genau einmal existieren (alte Session). Gibt es schon ein Fenster `bau <N> neu`, mehr als ein `bau <N>`, oder mehr als einen `bau.py`-Prozess für Ticket N → Exit 3, nichts anfassen.
1. **a)** `seit = jetzt()`. In das alte Fenster tippen: Handoff-Auftrag (Skill handoff, Bau-Variante) mit festen Pfaden im Ticket-Worktree (`config.worktree_pfad`):
   `docs/handoffs/HANDOFF_<JJJJ-MM-TT>_<N>.md` und Start-Prompt `docs/handoffs/START_<JJJJ-MM-TT>_<N>.txt`, beide committen, danach nichts mehr tun.
2. **b)** gleich danach neues Fenster `bau <N> neu` in Sitzung `spec-<S>`, cwd = Ticket-Worktree, Befehl nur
   `env TO_SPAWN_TICKET=N TO_SPAWN_SPEC=S BAU_TICKET=N TO_SPAWN_LOG_REPO=<wt> TO_SPAWN_LOG_RUECKFALL=<repo> claude --model <modelle.ticket> --effort <effort.ticket>`
   (Vorgabe Opus 5.5, mittel). **Nie ein Start-Prompt als Argument (V4).**
3. **c)** warten, bis der Bildschirm des neuen Fensters bereit ist (Marker, max. 120 s; sonst Exit 1, neues Fenster schließen), dann `/remote-control` tippen (E7, A12).
4. **d)** warten (bis `warte_max`, Vorgabe 1800 s), bis Handoff UND Start-Prompt existieren, nicht leer sind und nach `seit` entstanden (G5: nie mit altem Handoff). Fehlt eins → Exit 2, neues Fenster schließen, alte Session unangetastet.
5. **e)** Inhalt der Start-Prompt-Datei ins neue Fenster tippen (Text, dann Enter getrennt).
6. **Bildschirm prüfen (G4):** neues Fenster zeigt Arbeit (Bildschirm geändert, Pane lebt). Sonst Exit 1, alte Session NICHT beenden.
7. Erst dann alte Session beenden, neues Fenster in `bau <N>` umbenennen.
8. Ergebnis-Zeile: `respawn #N: ok — neue Session im Fenster bau N, Handoff <pfad>`.

Exit-Codes: 0 abgelöst · 1 neue Session nicht bewiesen · 2 Handoff/Start-Prompt fehlt · 3 Duplikat/alte Session fehlt.

## CLI

`to_spawn.py respawn <S> <N> [--warte-max s] [--dry-run]` — stdout genau eine Zeile (Ergebnis), Logs nach stderr. `--dry-run` zeigt nur die Duplikat-Prüfung + geplante Befehle als eine Zeile.

## Skill

`aliase/respawn/SKILL.md` (a–e, Ablöse-Subagent Sonnet, Antwort genau 1 Zeile), verlinkt in `SKILL.md` des to-spawn-Skills und im Aufseher-Prompt (`skripte/wache.py`, Befehle C/D).
