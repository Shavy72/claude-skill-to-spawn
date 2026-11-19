"""#1406: Unter Test startet die Start-Tür nur Attrappen — nie die echte Claude.

Vorfall: Die Attrappe ``claude`` ohne Endung lag vorn im PATH, Windows' ``shutil.which`` fand
trotzdem die echte ``claude.EXE`` und ``test_wache_wechselt_bei_limit_auf_ausweich_modell``
startete eine echte Aufseher-Session. Kein Test hier startet etwas Echtes: PATH zeigt auf
leere Ordner, ``subprocess.Popen`` ist im Sperr-Fall zusätzlich abgefangen.
"""

from __future__ import annotations

import ast
import json
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


def test_tuer_laesst_skill_python_nur_mit_test_merker_durch(tmp_path: Path) -> None:
    """Teil 2a: ``[sys.executable, <Skill-Skript>]`` (z. B. ``to_spawn.py takt``) darf starten,
    wenn das Kind den Test-Merker erbt; ``-c`` und eine Umgebung ohne Merker bleiben gesperrt."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / "kind.py"
    skript.write_text("import sys; sys.exit(7)\n", encoding="utf-8")
    lauf = starter.starte([sys.executable, str(skript)], cwd=tmp_path, warten=True, timeout=60)
    assert lauf.returncode == 7
    with pytest.raises(starter.StartGesperrt):
        starter.starte([sys.executable, "-c", "pass"], cwd=tmp_path, warten=True)
    ohne = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", starter.TESTLAUF_ENV)}
    with pytest.raises(starter.StartGesperrt):
        starter.starte([sys.executable, str(skript)], cwd=tmp_path, warten=True, env=ohne)


def test_tuer_sperrt_anderes_skill_skript_unter_test(tmp_path: Path) -> None:
    """Review 2a (d): nur ``to_spawn.py takt`` und ``skripte/bau.py`` aus dem Skill dürfen starten."""
    with pytest.raises(starter.StartGesperrt):
        starter.starte([sys.executable, str(SKILL / "skripte" / "leitstand.py")], cwd=tmp_path, warten=True)
    with pytest.raises(starter.StartGesperrt):
        starter.starte([sys.executable, str(SKILL / "to_spawn.py"), "spawn", "1"], cwd=tmp_path, warten=True)


# --- Prüf-Test: keine Start-Stelle außerhalb der Tür -------------------------------------

#: Start-Stellen, die noch NICHT über ``starter.starte`` laufen. Seit Teil 2 von #1406 leer —
#: neue Einträge nur als Übergang mit Ticket.
REST_START: frozenset[tuple[str, str]] = frozenset()

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
_START_PROGRAMM = {"claude", "claude.exe", "claude.cmd", "wt", "wt.exe"}
_LAUF = {"run", "call", "check_call", "check_output"}
_OS_START = {"system", "startfile", "execv", "execvp", "execvpe", "spawnv", "spawnvp", "spawnl", "popen"}


def _name(knoten: ast.expr) -> str:
    if isinstance(knoten, ast.Attribute):
        return knoten.attr
    return knoten.id if isinstance(knoten, ast.Name) else ""


def _aliase(baum: ast.AST) -> dict[str, str]:
    """Lokaler Name → echter Name: ``import subprocess as sp`` → ``sp: subprocess``,
    ``from subprocess import run as r`` → ``r: subprocess.run``."""
    namen: dict[str, str] = {}
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for teil in knoten.names:
                namen[teil.asname or teil.name] = teil.name
        elif isinstance(knoten, ast.ImportFrom) and knoten.module in ("subprocess", "os", "asyncio"):
            for teil in knoten.names:
                namen[teil.asname or teil.name] = f"{knoten.module}.{teil.name}"
    return namen


def _konstanten(knoten: ast.expr) -> list[object]:
    if isinstance(knoten, (ast.List, ast.Tuple)):
        return [e.value for e in knoten.elts if isinstance(e, ast.Constant)]
    return [knoten.value] if isinstance(knoten, ast.Constant) else []


def _ist_start(aufruf: ast.Call, aliase: dict[str, str] | None = None) -> bool:
    """Popen, os-Start, tmux/wt-Fenster-Konstante, Claude/wt als Programm oder ``subprocess.run``
    mit dynamischem Befehl. ``aliase`` löst ``import … as`` und ``from … import`` auf."""
    if any(k.arg == "start" and isinstance(k.value, ast.Constant) and k.value.value is True for k in aufruf.keywords):
        return False  # ``start=True``: Helfer leitet über die Tür (tmux_aufruf.aufrufen, Aufpasser.sh)
    aliase = aliase or {}
    func = aufruf.func
    if isinstance(func, ast.Attribute):
        traeger, fn = aliase.get(_name(func.value), _name(func.value)), func.attr
    else:
        traeger, _, fn = aliase.get(_name(func), _name(func)).rpartition(".")
    if fn == "Popen" or fn.startswith("create_subprocess") or (traeger == "os" and fn in _OS_START):
        return True
    konstanten = [k for a in aufruf.args for k in _konstanten(a)]
    if any(k in _TMUX_START for k in konstanten):
        return True
    if traeger == "subprocess" and fn in _LAUF and aufruf.args:
        liste = aufruf.args[0]
        if (
            not isinstance(liste, (ast.List, ast.Tuple))
            or not liste.elts
            or not isinstance(liste.elts[0], ast.Constant)
        ):
            return True  # dynamischer Befehl
        programm = liste.elts[0].value
        return isinstance(programm, str) and Path(programm).name.lower() in _START_PROGRAMM
    return False


def _funde_in(quelle: str, rel: str) -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    baum = ast.parse(quelle)
    aliase = _aliase(baum)

    def besuche(knoten: ast.AST, ort: str) -> None:
        for kind in ast.iter_child_nodes(knoten):
            hier = ort
            if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hier = f"{ort}.{kind.name}" if ort else kind.name
            if isinstance(kind, ast.Call) and _ist_start(kind, aliase):
                funde.add((rel, hier or "<modul>"))
            besuche(kind, hier)

    besuche(baum, "")
    return funde


_AUSSER = {"tests", ".git", ".review", ".ruff_cache", "__pycache__", ".venv", "node_modules"}


def _start_stellen() -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    for datei in SKILL.rglob("*.py"):  # ganzes Repo außer Tests und Werkzeug-Ordnern
        rel = datei.relative_to(SKILL).as_posix()
        if _AUSSER & set(Path(rel).parts):
            continue
        if rel != "to_spawn/starter.py":
            funde |= _funde_in(datei.read_text(encoding="utf-8"), rel)
    return funde


@pytest.mark.parametrize(
    "quelle",
    [
        'import subprocess\ndef f():\n    subprocess.run(["claude", "-p", "x"])',
        'import subprocess\ndef f():\n    subprocess.run(["wt", "new-tab", "x"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "new-window", "-n", "x"])',
        "import subprocess as sp\ndef f(befehl):\n    sp.run(befehl)",
        'import subprocess as sp\ndef f():\n    sp.check_call(["claude"])',
        'from subprocess import Popen\ndef f():\n    Popen(["x"])',
        "from subprocess import run as r\ndef f(befehl):\n    r(befehl)",
        'from os import system\ndef f():\n    system("x")',
    ],
)
def test_pruef_test_meldet_versteckte_start(quelle: str) -> None:
    assert _funde_in(quelle, "probe.py") == {("probe.py", "f")}


@pytest.mark.parametrize(
    "quelle",
    [
        'import subprocess\ndef f():\n    subprocess.run(["git", "status"])',
        'import subprocess as sp\ndef f():\n    sp.run(["tmux", "list-windows"])',
        'from subprocess import run\ndef f():\n    run(["gh", "issue", "view"])',
    ],
)
def test_pruef_test_laesst_lese_befehle_durch(quelle: str) -> None:
    assert _funde_in(quelle, "probe.py") == set()


def test_keine_start_stelle_ausserhalb_der_tuer() -> None:
    funde = _start_stellen() - KEIN_START
    neu = sorted(funde - REST_START)
    weg = sorted(REST_START - funde)
    assert not neu, f"Neue Start-Stelle an der Tür vorbei — über to_spawn.starter.starte starten: {neu}"
    assert not weg, f"Umgestellt oder verschwunden — aus REST_START streichen: {weg}"
    assert not sorted(KEIN_START - _start_stellen()), "KEIN_START enthält Einträge ohne Treffer"


def test_shell_notnagel_entschaerft_cmd_sonderzeichen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review Teil 1: ``& | < > ^ %`` ohne Leerzeichen darf die Shell nicht auswerten."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / "zeig_argv.py"
    skript.write_text(
        "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    beleg = tmp_path / "argv.json"
    args = ["a&b", "x|y", "<z>", "^q", "%PATH%", 'zit"at', "ende\\", "(k)", "!x!", "mit leer", ""]
    echt = subprocess.run

    def ohne_direktstart(befehl: object, **kw: object) -> object:
        if not kw.get("shell"):
            raise FileNotFoundError("Direktstart simuliert gescheitert")
        return echt(befehl, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", ohne_direktstart)
    lauf = starter.starte([sys.executable, str(skript), str(beleg), *args], cwd=tmp_path, warten=True, timeout=60)
    assert lauf.returncode == 0
    assert json.loads(beleg.read_text(encoding="utf-8")) == args
