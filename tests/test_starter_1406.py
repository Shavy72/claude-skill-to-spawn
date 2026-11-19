"""#1406: Unter Test startet die Start-Tür nur Attrappen — nie die echte Claude.

Vorfall: Die Attrappe ``claude`` ohne Endung lag vorn im PATH, Windows' ``shutil.which`` fand
trotzdem die echte ``claude.EXE`` und ``test_wache_wechselt_bei_limit_auf_ausweich_modell``
startete eine echte Aufseher-Session. Kein Test hier startet etwas Echtes: PATH zeigt auf
leere Ordner, ``subprocess.Popen`` ist im Sperr-Fall zusätzlich abgefangen.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import starter  # noqa: E402

ATTRAPPE = """#!/usr/bin/env python3
import sys
from pathlib import Path
Path(sys.argv[1]).write_text("gestartet", encoding="utf-8")
"""


def test_conftest_setzt_test_schutz() -> None:
    assert os.environ.get(starter.TESTLAUF_ENV) == "1"
    assert Path(os.environ[starter.TEST_BIN_ENV]).is_dir()
    assert starter.unter_test()


def test_tuer_sperrt_echten_start_unter_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    leer = tmp_path / "leer"
    leer.mkdir()
    monkeypatch.setenv("PATH", str(leer))  # selbst ohne Sperre fände sich nichts Echtes

    def nie(*_a: object, **_k: object) -> None:
        raise AssertionError("Popen trotz Sperre aufgerufen")

    monkeypatch.setattr(subprocess, "Popen", nie)
    with pytest.raises(starter.StartGesperrt, match="echter Start im Test verboten"):
        starter.starte(["claude", "--version"], cwd=tmp_path)
    monkeypatch.delenv(starter.TEST_BIN_ENV)
    with pytest.raises(starter.StartGesperrt, match="echter Start im Test verboten"):
        starter.starte([sys.executable, "-c", "pass"], cwd=tmp_path)


def test_tuer_startet_attrappe_ohne_endung_aus_test_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attrappen = Path(os.environ[starter.TEST_BIN_ENV])
    (attrappen / "claude").write_text(ATTRAPPE, encoding="utf-8")
    (attrappen / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))  # leer: nur die Attrappe ist erreichbar
    gefunden = starter.finde("claude")
    assert Path(gefunden).parent == attrappen, gefunden
    beleg = tmp_path / "beleg.txt"
    lauf = starter.starte([gefunden, str(beleg)], cwd=tmp_path, warten=True, timeout=60)
    assert lauf.returncode == 0
    assert beleg.read_text(encoding="utf-8") == "gestartet"


def test_sperre_gilt_im_kind_prozess_ohne_pytest_merker(tmp_path: Path) -> None:
    """``wache.py``/``bau.py`` laufen als Kind-Prozess: dort zählt nur die geerbte Umgebung."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env["PATH"] = str(tmp_path)
    code = "from to_spawn import starter; starter.starte(['claude', '--version'])"
    lauf = subprocess.run(
        [sys.executable, "-c", code], cwd=str(SKILL), env=env, capture_output=True, text=True, check=False
    )
    assert lauf.returncode != 0
    assert "echter Start im Test verboten" in lauf.stderr, lauf.stderr


# --- Prüf-Test: keine Start-Stelle außerhalb der Tür -------------------------------------

#: Start-Stellen, die Teil 1 von #1406 noch NICHT auf ``starter.starte`` umgestellt hat.
#: Teil 2 stellt sie um und streicht sie hier; danach ist die Liste leer.
REST_START = frozenset(
    {
        ("to_spawn/umzug.py", "starte_mit_umzug_wache"),  # Bau-Session-Claude (bau.py) + Shell-Notnagel
        ("to_spawn/bau_loop.py", "run_ticket"),  # Claude-Lauf je Ticket
        ("to_spawn/probesitz.py", "session_fahren"),  # Probesitz-Claude
        ("to_spawn/waechter_takt.py", "starte_claude"),  # Takt-Claude
        ("to_spawn/waechter_takt.py", "starte_abgeloest"),  # Hintergrund-Prozess (Ablösung)
        ("to_spawn/capo.py", "_starte_folge_runde"),  # Folge-Runde (tmux/lokal)
    }
)

#: Treffer der Suche, die nichts Echtes starten: Lese-/Prüfbefehle und Kind-Skripte des Skills
#: (deren eigene Starts gehen wieder durch die Tür). Neue Einträge nur mit Grund.
KEIN_START = frozenset(
    {
        ("to_spawn/prozessbaum.py", "session_beenden"),  # beendet Prozesse
        ("to_spawn/setup.py", "pruefe_start"),  # Versions-Probe
        ("skripte/abschluss_paket.py", "wirkungskreis"),  # Repo-Skript, Ausgabe gelesen
        ("skripte/bau.py", "ticket_from_gh"),  # gh
        ("skripte/bau.py", "_gh_json"),  # gh
        ("skripte/leitstand.py", "starte"),  # Kind-Prozess mit gefangener Ausgabe
        ("skripte/spec_stand.py", "sh"),  # Lese-Befehle
        ("skripte/hooks/staffel_stop.py", "ticket_offen"),  # gh
        ("to_spawn/aufpasser.py", "Aufpasser.sh"),  # Lese-Befehle (tmux list/capture); start=True → Tür
        ("to_spawn/aufseher_stand.py", "_tmux_echt"),  # tmux lesen
        ("to_spawn/capo.py", "vps_kopf"),  # ssh lesen
        ("to_spawn/capo.py", "_staging_zeilen"),  # ssh lesen
        ("to_spawn/capo.py", "tmux_fenster"),  # tmux list-windows
        ("to_spawn/capo.py", "_abschluss_lauf"),  # abschluss_paket.py
        ("to_spawn/gh.py", "lauf"),  # gh
        ("to_spawn/handoff_blick.py", "EchteNaht.leiter"),  # Skill-Skript, Ausgabe gelesen
        ("to_spawn/inventur.py", "_fuehre_aus"),  # Prüfbefehle
        ("to_spawn/melder.py", "melden"),  # Mail-Befehl
        ("to_spawn/nest.py", "_bws_geheimnisse"),  # Bitwarden lesen
        ("to_spawn/probesitz.py", "laeufer_standard"),  # Kind-Prozess ohne Terminal, Ausgabe gelesen
        ("to_spawn/staging_schalter.py", "schalte_an"),  # Repo-Skript
        ("to_spawn/stand.py", "_lauf"),  # Lese-Befehle
        ("to_spawn/startklar.py", "_standard_laeufer"),  # Prüfbefehle
        ("to_spawn/startklar.py", "_laeuft"),  # Python-Probe
        ("to_spawn/startklar.py", "_ausfuehren"),  # Prüfbefehle
        ("to_spawn/startklar.py", "_fehlende_pakete"),  # Python-Probe
        ("to_spawn/tmux_aufruf.py", "aufrufen"),  # tmux-Hilfe; start=True → Tür
    }
)

_TMUX_START = {"new-window", "new-tab", "split-window", "new-session"}
_LAUF = {"run", "call", "check_call", "check_output"}
_OS_START = {"system", "startfile", "execv", "execvp", "execvpe", "spawnv", "spawnvp", "spawnl", "popen"}


def _name(knoten: ast.expr) -> str:
    if isinstance(knoten, ast.Attribute):
        return knoten.attr
    return knoten.id if isinstance(knoten, ast.Name) else ""


def _ist_start(aufruf: ast.Call) -> bool:
    """Popen, os-Start, tmux/wt-Fenster-Konstante oder ``subprocess.run`` mit dynamischem Befehl."""
    if any(k.arg == "start" and isinstance(k.value, ast.Constant) and k.value.value is True for k in aufruf.keywords):
        return False  # ``start=True``: Helfer leitet über die Tür (tmux_aufruf.aufrufen, Aufpasser.sh)
    fn = _name(aufruf.func)
    traeger = _name(aufruf.func.value) if isinstance(aufruf.func, ast.Attribute) else ""
    if fn == "Popen" or fn.startswith("create_subprocess") or (traeger == "os" and fn in _OS_START):
        return True
    if any(isinstance(a, ast.Constant) and a.value in _TMUX_START for a in aufruf.args):
        return True
    if traeger == "subprocess" and fn in _LAUF and aufruf.args:
        erstes = aufruf.args[0]
        fest = isinstance(erstes, (ast.List, ast.Tuple)) and erstes.elts and isinstance(erstes.elts[0], ast.Constant)
        return not fest  # fester Literal-Befehl (git, gh, …) = Lese-Befehl
    return False


def _start_stellen() -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    dateien = [*SKILL.joinpath("to_spawn").rglob("*.py"), *SKILL.joinpath("skripte").rglob("*.py")]
    for datei in dateien:
        rel = datei.relative_to(SKILL).as_posix()
        if rel == "to_spawn/starter.py":
            continue

        def besuche(knoten: ast.AST, ort: str) -> None:
            for kind in ast.iter_child_nodes(knoten):
                hier = ort
                if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    hier = f"{ort}.{kind.name}" if ort else kind.name
                if isinstance(kind, ast.Call) and _ist_start(kind):
                    funde.add((rel, hier or "<modul>"))  # noqa: B023 — sofort ausgewertet
                besuche(kind, hier)

        besuche(ast.parse(datei.read_text(encoding="utf-8")), "")
    return funde


def test_keine_start_stelle_ausserhalb_der_tuer() -> None:
    funde = _start_stellen() - KEIN_START
    neu = sorted(funde - REST_START)
    weg = sorted(REST_START - funde)
    assert not neu, f"Neue Start-Stelle an der Tür vorbei — über to_spawn.starter.starte starten: {neu}"
    assert not weg, f"Umgestellt oder verschwunden — aus REST_START streichen: {weg}"
    assert not sorted(KEIN_START - _start_stellen()), "KEIN_START enthält Einträge ohne Treffer"
