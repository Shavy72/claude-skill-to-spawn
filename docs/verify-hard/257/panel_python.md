# Prüfpanel #257 — python-reviewer (Standards)

Worktree `/home/bau/wt/skill-257`, Zweig `ticket-257`, Basis `196aa73`.
Geprüft: `git diff 196aa73 -- '*.py'` + `git diff 196aa73 -- '*.sh'` (uncommittet) inkl. der vier
untracked neuen Dateien (`to_spawn/speicher.py`, `to_spawn/vertrauen.py`,
`tests/test_fremdrepo_257.py`, `tests/test_speicher_257.py`), plus `ruff check .`
(`docs/verify-hard/257/ruff.txt`, HEAD 101 → jetzt 107 Befunde).

## Ruff — die 6 neuen Befunde (alle in Dateien der Vor-Session, nicht dieser Fixrunde)

| Regel | Datei:Zeile | Beschreibung | Fix |
|---|---|---|---|
| RUF100 | `tests/test_fremdrepo_257.py:35` | `# noqa: E402` ungenutzt (Regel nicht aktiv in der Nutzerkonfig) | `noqa`-Kommentar entfernen oder Regel im Projekt aktivieren |
| PLW1510 | `tests/test_fremdrepo_257.py:241` | `subprocess.run(["bash", "-n", …])` ohne explizites `check=` | `check=False` ergänzen (Absicht sichtbar machen, `returncode` wird ohnehin geprüft) |
| I001 | `tests/test_speicher_257.py:28` | Import-Block unsortiert/unformatiert | `ruff check --fix` bzw. Imports sortieren |
| RUF100 | `tests/test_speicher_257.py:28` | `# noqa: E402` ungenutzt | entfernen |
| RUF100 | `tests/test_speicher_257.py:29` | `# noqa: E402` ungenutzt (Kommentar „fehlt vor Paket B — Datei dann rot“) | entfernen, Kommentar ist stale (siehe unten) |
| TRY004 | `to_spawn/vertrauen.py:31` | `raise ValueError(...)` bei `isinstance`-Prüfung — ruff bevorzugt `TypeError` | `TypeError` verwenden oder begründetes `# noqa: TRY004` (ValueError ist hier vertretbar: geprüft wird Dateiinhalt, kein Python-Typfehler) |

Eigene Änderungen dieser Fixrunde (speicher.py, config.py, aufpasser.py, conftest.py, to_spawn.py,
bau.py, wache.py, sessions_stand.py, gh.py, capo.py, manifest.py, probesitz.py, spawn.py,
waechter_lauf.py): 0 neue Ruff-Befunde.

## Befunde (Schwere · Datei:Zeile · Fix)

- **mittel** · `tests/test_speicher_257.py:1-11,29` — Moduldocstring + `noqa`-Kommentar behaupten
  noch „`to_spawn.speicher` existiert noch nicht … bewusst rot (Import-Fehler)“ / „fehlt vor Paket B
  — Datei dann rot“; das Modul `to_spawn/speicher.py` existiert längst (166 Zeilen, voll
  implementiert). Fix: Docstring + Kommentar auf den tatsächlichen (grünen) Stand aktualisieren,
  sonst verwirrt das jede künftige Session, die die Datei liest.
- **niedrig** · `to_spawn/vertrauen.py:31` — `raise ValueError(f"{claude_json} ist kein JSON-Objekt")`
  bei einer `isinstance`-Prüfung; ruff TRY004 will `TypeError`. Fix: Typ wechseln oder begründetes
  `# noqa: TRY004`.
- **niedrig** · `skripte/bau.py:344-350` (`build_prompt`) — Parameter `konfig: dict | None = None` ist
  ein nackter `dict` ohne Typparameter (Projektstandard: vollständige Type Hints, andere neue
  Funktionen in derselben Fixrunde nutzen durchweg `dict[str, Any]`). Fix: zu
  `dict[str, Any] | None` ändern.
- **niedrig** · `to_spawn/speicher.py:66-92` (`claude_sessions`) vs. `to_spawn/aufpasser.py:311-328`
  (`_argv`/`_prozess_kinder`) — eigener `/proc`-Leser (Verzeichnis iterieren, `cmdline` lesen,
  `argv[0]`-Basename bestimmen) dupliziert das Muster, das in `aufpasser.py` schon als Helfer
  existiert. Nur benannt, nicht bewertet ob Zusammenlegen Pflicht ist (Aufgabenstellung).

## Standards ohne Befund (geprüft, sauber)

- Type Hints: alle neuen/geänderten Funktionssignaturen (auch mehrzeilige) vollständig typisiert,
  bis auf den einen `dict`-Fall oben.
- Logging statt `print`: `print()` kommt nur in reiner CLI-Ausgabe vor (`speicher.cli`, `bau.py`/
  `wache.py`-Startmeldungen, `install.sh`-Label-Meldungen, Trockenlauf-Zeilen) — konform.
- Kein nacktes `except:`, kein `except Exception: pass` in den geänderten/neuen Dateien.
- Echte Umlaute überall geprüft (Grep auf `ae|oe|ue`-Muster in Kommentaren/Meldungen) — keine
  Ersatzschreibweise gefunden; `geaendert` als Bezeichner ist eine zulässige Identifier-Ausnahme.
- Docstrings mit Ticket-Nummer: neue Funktionen/Module tragen durchgängig `(#257)`.
- Keine toten Importe (ruff F401/F841 zeigt in den neuen/geänderten Dateien nichts).
- Pfade als `Path` (keine rohen String-Pfad-Verkettungen in den neuen Modulen).
- Shell: `install.sh`, `nest/nest_server.sh`, `skripte/spawn_srv.sh` behalten `set -euo pipefail`;
  `bash -n` auf allen drei Dateien fehlerfrei; Quoting durchgängig über `printf %q` (bash) bzw.
  `shlex.quote`/`shlex.join` (Python-Aufrufe von `spawn.py` Richtung SSH); der
  `[ -x ... ] && VAR=...`-Kurzschluss in `spawn_srv.sh` bricht unter `set -e` nicht ab (geprüft).
