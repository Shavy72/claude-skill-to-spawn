# Messung Test-Auswahl und Schlange (Etappe 3a)

Alle Läufe mit `nice -n 10`. Nur Linux (nutzt git, nice, psutil). Pfade immer als Argument.

Voraussetzungen: Python 3.13, uv-venv mit pytest 9.2.0, pytest-impacted 0.38.1,
pytest-testmon 2.3.1, pytest-xdist 3.8.0, psutil, coverage. Für `node`: Node 22 + vitest 5.1.1.
`fehler_reihe.py` importiert psutil vor dem Argument-Parsen: auch `--help` läuft nur im venv mit psutil.

## Schlangen-Skripte (nur lesen, Standardbibliothek)
`<schlangen-ordner>` enthält `fahrtenbuch.jsonl`, `klon/`, `laeufe/`.
- `vorher_zeiten.py`: Zeiten der Schlange heute (Markdown).
  `python3 vorher_zeiten.py --schlange <schlangen-ordner> [JJJJ-MM-TT]`
- `schlange_sim.py`: spielt die letzten Tage mit S0 bis S3 und Vorprüfung nach (fester Seed).
  `nice -n 10 python3 schlange_sim.py --schlange <schlangen-ordner> [--bis <ISO-Zeit>] [--tage 14]`
- `rueckblick_auswahl_duoplus.py`: heutige gate_auswahl gegen pytest-impacted an echten Läufen.
  Braucht Klon des App-Repos, venv und Sammeldatei (`pytest --collect-only -q > collect.txt`).
  Fortsetzbar (fertige Läufe übersprungen), ca. 100 s je Lauf, Zeitdeckel `--max-s` je Aufruf.
  `nice -n 15 python3 rueckblick_auswahl_duoplus.py --schlange <schlangen-ordner> --repo <klon-app-repo> --venv <venv-ordner> --collect collect.txt --aus rueckblick.jsonl`

## Fehlerreihe auf Probe-Repos
- `gen_py.py` und `gen_node.py`: erzeugen `<probe-ordner>/py/` und `<probe-ordner>/node/` (Dateien, noch kein git).
- `fehler_reihe.py`: baut je Fehler einen Commit ein und misst Wandzeit, RAM und Ergebnis.
  Ergebnis: `<probe-ordner>/ergebnis_<modus>.json`.
- `fehler_reihe_2.py` (3b-2b, #104): zweite, vorher unbekannte Fehlerreihe (26 Fehler: 16 Python, 10 Node) gegen die
  eingefrorenen Rückfall-Regeln in `to_spawn/pruef/regeln.py` (`regeln@fd7e49f`). Läuft plattformübergreifend
  (Linux + Windows) gegen einen isolierten Temp-Klon des Probe-Ordners und prüft `voll rot ⇒ pruefen rot`
  (0 übersehen) plus Gegenprobe gegen rohe statische Auswahl:
  `python messung/fehler_reihe_2.py --ort <probe-ordner> [--nur py|node] [--aus ergebnis.json]`

Reihenfolge:
1. `python3 gen_py.py --ziel <probe-ordner>` und `python3 gen_node.py --ziel <probe-ordner>`
2. In `py/` und `node/` je `git init`, alles committen, Tag `base` setzen (`fehler_reihe.py` checkt `base` aus und räumt mit `git clean` auf).
3. venv in `<probe-ordner>/.venv` anlegen (Pakete oben, `uv pip install -r py/requirements.txt`); für Node in `node/` `npm install`.
4. Aufruf (Python aus dem venv): `<probe-ordner>/.venv/bin/python fehler_reihe.py --ort <probe-ordner> <modus>`
   - `faelle` zuerst (Kaltstart, Doku/Blatt/Konfig; legt `seed_testmon/` aus dem testmon-Grundlauf an)
   - `py`, danach `py -n 2 --dist worksteal`
   - `node`
