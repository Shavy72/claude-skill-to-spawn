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

ATTRAPPE = f"""#!{sys.executable}
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
REST_START: frozenset[tuple[str, str]] = frozenset(
    {
        # 1:1-Übernahme, Angleichung folgt in #214 Teil B (Review-Hook startet noch roh)
        ("review-hook/_hookbasis.py", "befehl"),
        ("review-hook/tests/test_hooks.py", "Umgebung.hook"),
        ("review-hook/tests/test_hooks.py", "Umgebung.route"),
        ("review-hook/tests/test_schutz_runde6.py", "_innen_laufen"),
        ("review-hook/tests/test_stop_runde5.py", "_beleg_echt"),
        ("review-hook/tests/test_stop_runde5.py", "test_neue_datei_beleg_ueberlebt_commit"),
        ("review-hook/tests/test_stop_runde5.py", "test_pruefen_cli_findet_beleg_nach_commit"),
        ("review-hook/tests/test_wochenbericht.py", "_lauf"),
        ("review-hook/wochenbericht.py", "bericht_erzeugen"),
        # 1:1-Übernahme des Review-Werkzeugs (#329 P1): git/pytest/Skripte mit gefangener Ausgabe
        ("review/_gemeinsam.py", "_blast_radius"),
        ("review/rot_probe.py", "pruefen"),
        ("review/rot_probe.py", "sammeln"),
        ("review/tests/review_hilfen.py", "lauf"),
        ("review/tests/test_rot_probe_platte.py", "toter_pid"),
    }
)

#: Treffer der Suche, die nichts Echtes starten: Lese-/Prüfbefehle und Kind-Skripte des Skills
#: (deren eigene Starts gehen wieder durch die Tür). Neue Einträge nur mit Grund.
KEIN_START = frozenset(
    {
        ("to_spawn/prozessbaum.py", "session_beenden"),  # beendet Prozesse
        ("to_spawn/hausmeister_windows.py", "_prozesse"),  # powershell -Command: CIM-Prozessliste lesen (#1373)
        ("skripte/sessions_stand.py", "prozesse_lesen"),  # powershell -Command: WMI-Prozessliste lesen
        ("to_spawn/setup.py", "pruefe_start"),  # Versions-Probe
        (
            "to_spawn/konsole.py",
            "lauf_daten",
        ),  # Lese-Helfer, gefangene Ausgabe (docker prune #1372, git rev-parse, ssh git rev-parse)
        ("to_spawn/docker_reste.py", "_docker"),  # docker image/builder prune über konsole.lauf_daten (#1372, #608)
        ("skripte/abschluss_paket.py", "wirkungskreis"),  # Repo-Skript, Ausgabe gelesen
        ("skripte/bau.py", "ticket_from_gh"),  # gh
        ("skripte/bau.py", "_gh_json"),  # gh
        ("skripte/leitstand.py", "starte"),  # Kind-Prozess mit gefangener Ausgabe
        ("skripte/spec_stand.py", "sh"),  # Lese-Befehle
        ("to_spawn/aufpasser.py", "Aufpasser.sh"),  # Lese-Befehle (tmux list/capture); start=True → Tür
        ("to_spawn/aufseher_stand.py", "_tmux_echt"),  # tmux lesen
        # bash-Tür (#186): einzige subprocess-Stelle für bash; Aufrufer startklar._standard_laeufer
        # (Hook-/Trockenlauf-Probe, Ausgabe gelesen) und stand._lauf (Stand-Check/Push), keine Session
        ("to_spawn/bash_wahl.py", "lauf"),  # bash Probe-/Prüf-/Push-Befehle, wartet, keine Session
        ("to_spawn/capo.py", "vps_kopf"),  # ssh git rev-parse HEAD über konsole.lauf_daten (#608)
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
        ("to_spawn/startklar.py", "_laeuft"),  # Python-Probe
        ("to_spawn/startklar.py", "_ausfuehren"),  # Prüfbefehle
        ("to_spawn/startklar.py", "_fehlende_pakete"),  # Python-Probe
        ("to_spawn/tmux_aufruf.py", "aufrufen"),  # tmux-Hilfe; start=True → Tür
        # messung/: Mess-Skripte Prüftor-Vergleich (PR #82), nicht installiert, starten keine Session
        ("messung/fehler_reihe.py", "run"),  # Popen pytest/node-Testlauf, RAM-Messung, Ausgabe gelesen
        ("messung/rueckblick_auswahl_duoplus.py", "gate_auswahl"),  # gate_auswahl.py, Ausgabe gelesen
        ("messung/rueckblick_auswahl_duoplus.py", "impacted"),  # impacted-tests, Ausgabe gelesen
        # to_spawn/pruef/: Prüf-Tür (3b-1a, PR #117) fährt den Testläufer blockierend, keine Session
        ("to_spawn/pruef/ausfuehren.py", "lauf"),  # subprocess.run pytest/node, wartet, Ausgabe gelesen
    }
)

_TMUX_START = {
    *("new-window", "new-tab", "split-window", "new-session", "respawn-window", "respawn-pane"),
    *("neww", "splitw", "new", "respawnw", "respawnp"),  # tmux-Kurzformen (Nachrunde #1406, B2)
    *("run-shell", "if-shell", "pipe-pane", "display-popup"),  # starten selbst ein Programm
}
_START_PROGRAMM = {"claude", "claude.exe", "claude.cmd", "wt", "wt.exe"}
#: ``ssh`` startet immer etwas auf dem Ziel: den Fern-Befehl oder eine Login-Shell (#1406, B1).
_FERN_PROGRAMM = {"ssh", "ssh.exe"}
_LAUF = {"run", "call", "check_call", "check_output"}
_KONSOLE_MODUL = {"konsole", "to_spawn.konsole"}
_SHELL = {"powershell", "pwsh", "cmd", "bash", "sh", "zsh"}
_SHELL_BEFEHL = {"-c", "-lc", "-command", "/c", "/k"}


def _traeger_fn(knoten: ast.expr, aliase: dict[str, str]) -> tuple[str, str]:
    """``(Modul, Funktion)`` eines Namens/Attributs, Aliase aufgelöst."""
    if isinstance(knoten, ast.Attribute):
        return aliase.get(_name(knoten.value), _name(knoten.value)), knoten.attr
    traeger, _, fn = aliase.get(_name(knoten), _name(knoten)).rpartition(".")
    return traeger, fn


def _ist_startfunktion(traeger: str, fn: str) -> bool:
    if traeger == "os":
        return fn in {"system", "startfile", "popen"} or fn.startswith(("spawn", "exec", "posix_spawn"))
    if traeger == "pty":
        return fn == "spawn"
    if traeger == "subprocess" and fn in {"getoutput", "getstatusoutput"}:
        return True  # Shell-String
    return fn == "Popen" or fn.startswith("create_subprocess")


def _name(knoten: ast.expr) -> str:
    if isinstance(knoten, ast.Attribute):
        return knoten.attr
    return knoten.id if isinstance(knoten, ast.Name) else ""


def _aliase(baum: ast.AST) -> dict[str, str]:
    """Lokaler Name → echter Name: ``import subprocess as sp`` → ``sp: subprocess``,
    ``from subprocess import run as r`` → ``r: subprocess.run``,
    ``from to_spawn import konsole as k`` → ``k: konsole``."""
    namen: dict[str, str] = {}
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for teil in knoten.names:
                namen[teil.asname or teil.name] = teil.name
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.module in ("subprocess", "os", "asyncio", "pty"):
                for teil in knoten.names:
                    namen[teil.asname or teil.name] = f"{knoten.module}.{teil.name}"
            elif knoten.module in ("to_spawn.konsole", "konsole"):
                for teil in knoten.names:
                    namen[teil.asname or teil.name] = f"konsole.{teil.name}"
            elif knoten.module in ("to_spawn", None):
                for teil in knoten.names:
                    if teil.name == "konsole":
                        namen[teil.asname or teil.name] = "konsole"
    return namen


def _konstanten(knoten: ast.expr) -> list[object]:
    if isinstance(knoten, (ast.List, ast.Tuple)):
        return [e.value for e in knoten.elts if isinstance(e, ast.Constant)]
    return [knoten.value] if isinstance(knoten, ast.Constant) else []


def _ist_start(aufruf: ast.Call, aliase: dict[str, str] | None = None) -> bool:
    """Popen, os-Start, tmux/wt-Fenster-Konstante, Claude/wt als Programm oder ``subprocess.run``
    bzw. ``konsole.lauf_daten`` mit dynamischem Befehl. ``aliase`` löst ``import … as`` und
    ``from … import`` auf."""
    if any(k.arg == "start" and isinstance(k.value, ast.Constant) and k.value.value is True for k in aufruf.keywords):
        return False  # ``start=True``: Helfer leitet über die Tür (tmux_aufruf.aufrufen, Aufpasser.sh)
    aliase = aliase or {}
    traeger, fn = _traeger_fn(aufruf.func, aliase)
    if _ist_startfunktion(traeger, fn):
        return True
    # ``args=`` / ``befehl=`` zählt wie das erste Positionsargument
    werte = [*aufruf.args, *(k.value for k in aufruf.keywords if k.arg in ("args", "befehl"))]
    if any(k in _TMUX_START for a in werte for k in _konstanten(a)):
        return True
    if ((traeger == "subprocess" and fn in _LAUF) or (traeger in _KONSOLE_MODUL and fn == "lauf_daten")) and werte:
        liste = werte[0]
        if (
            not isinstance(liste, (ast.List, ast.Tuple))
            or not liste.elts
            or not isinstance(liste.elts[0], ast.Constant)
        ):
            return True  # dynamischer Befehl
        programm = liste.elts[0].value
        if not isinstance(programm, str):
            return False
        name = Path(programm).name.lower()
        if name in _START_PROGRAMM or name in _FERN_PROGRAMM:
            return True
        # Shell als Programm mit Befehlsschalter: ``bash -lc …``, ``powershell -c …``, ``cmd /c …``
        rest = [k.lower() for k in _konstanten(liste)[1:] if isinstance(k, str)]
        return name.removesuffix(".exe") in _SHELL and any(k in _SHELL_BEFEHL for k in rest)
    return False


def _funde_in(quelle: str, rel: str) -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    baum = ast.parse(quelle)
    aliase = _aliase(baum)
    aufgerufen = {id(k.func) for k in ast.walk(baum) if isinstance(k, ast.Call)}
    # Typ-Hinweise (``prozess: subprocess.Popen[Any]``) referenzieren nur den Typ
    hinweise = [getattr(k, f, None) for k in ast.walk(baum) for f in ("annotation", "returns")]
    aufgerufen |= {id(n) for h in hinweise if isinstance(h, ast.AST) for n in ast.walk(h)}

    def referenz(kind: ast.AST) -> bool:
        """Start-Funktion als Wert statt Aufruf: ``lauf = subprocess.Popen``, ``lauf = run``."""
        if id(kind) in aufgerufen or not isinstance(kind, (ast.Attribute, ast.Name)):
            return False
        if not isinstance(kind.ctx, ast.Load):
            return False
        traeger, fn = _traeger_fn(kind, aliase)
        return (
            _ist_startfunktion(traeger, fn)
            or (traeger == "subprocess" and fn in _LAUF)
            or (traeger in _KONSOLE_MODUL and fn == "lauf_daten")
        )

    def besuche(knoten: ast.AST, ort: str) -> None:
        for kind in ast.iter_child_nodes(knoten):
            hier = ort
            if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hier = f"{ort}.{kind.name}" if ort else kind.name
            if (isinstance(kind, ast.Call) and _ist_start(kind, aliase)) or referenz(kind):
                funde.add((rel, hier or "<modul>"))
            besuche(kind, hier)

    besuche(baum, "")
    return funde


#: Werkzeug-Ordner überall ausgenommen; ``tests/`` und ``probe/`` nur auf der obersten Ebene.
# ``probe``: Vorlagen-App für Probe-Läufe (#426 Teil F), läuft im Lauf-Repo ohne Skill und kann
# die Tür ``to_spawn.starter`` dort nicht importieren (#447: enger auf parts[0] == "probe").
_AUSSER = {".git", ".review", ".ruff_cache", "__pycache__", ".venv", "node_modules"}
_AUSSER_OBEN = {"tests", "probe"}


def _start_stellen(wurzel: Path = SKILL) -> set[tuple[str, str]]:
    funde: set[tuple[str, str]] = set()
    for datei in wurzel.rglob("*.py"):  # ganzes Repo außer Tests, Probe-Vorlagen und Werkzeug-Ordnern
        rel = datei.relative_to(wurzel).as_posix()
        teile = Path(rel).parts
        if teile[0] in _AUSSER_OBEN or _AUSSER & set(teile):
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
        # Fixrunde #1406 (Review 2b/Abschluss): je Lücke ein Fall, der vorher durchging
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "respawn-window", "-k", "-t", "x"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "respawn-pane", "-k"])',
        'import os\ndef f():\n    os.spawnve(os.P_NOWAIT, "x", ["x"], {})',
        'import os\ndef f():\n    os.execlp("x", "x")',
        'from os import posix_spawn\ndef f():\n    posix_spawn("x", ["x"], {})',
        'import subprocess\ndef f(z):\n    subprocess.run(["powershell", "-c", z])',
        'import subprocess\ndef f(z):\n    subprocess.run(["pwsh", "-NoProfile", "-Command", z])',
        'import subprocess\ndef f(z):\n    subprocess.run(["bash", "-lc", z])',
        'import subprocess\ndef f():\n    subprocess.run(["cmd", "/c", "start", "x"])',
        "import subprocess\ndef f(befehl):\n    subprocess.run(args=befehl)",
        'import subprocess\ndef f():\n    subprocess.run(args=["claude", "-p", "x"])',
        'import subprocess\ndef f():\n    lauf = subprocess.Popen\n    lauf(["x"])',
        'import subprocess\ndef f(w):\n    lauf = subprocess.run if w else subprocess.Popen\n    lauf(["x"])',
        'from subprocess import run\ndef f():\n    lauf = run\n    lauf(["x"])',
        # Nachrunde #1406 (Review Fixrunde B2): tmux-Kurzformen und startende Unterbefehle
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "neww", "bash"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "splitw", "bash"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "new", "-s", "x"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "respawnw", "-k"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "respawnp", "-k"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "run-shell", "claude"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "if-shell", "true", "neww"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "pipe-pane", "-o", "cat"])',
        'import subprocess\ndef f():\n    subprocess.run(["tmux", "display-popup", "claude"])',
        # Mini-Runde #1406 (End-Review B1/G1): ssh mit Fern-Befehl, Shell-String-Helfer, pty
        'import subprocess\ndef f(ziel, befehl):\n    subprocess.run(["ssh", "-o", "BatchMode=yes", ziel, befehl])',
        'import subprocess\ndef f():\n    subprocess.check_output(["ssh.exe", "bau-server", "bash x.sh"])',
        "import subprocess\ndef f(z):\n    subprocess.getoutput(z)",
        "from subprocess import getstatusoutput\ndef f(z):\n    getstatusoutput(z)",
        'import pty\ndef f():\n    pty.spawn(["bash"])',
        'from pty import spawn\ndef f():\n    spawn(["bash"])',
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


def test_pruef_test_sucht_auch_in_unter_ordnern_namens_tests(tmp_path: Path) -> None:
    """Nur der oberste ``tests/`` ist ausgenommen, nicht etwa ``to_spawn/tests/``."""
    for rel in ("tests/probe.py", "to_spawn/tests/probe.py"):
        datei = tmp_path / rel
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text('import subprocess\ndef f():\n    subprocess.Popen(["x"])\n', encoding="utf-8")
    assert _start_stellen(tmp_path) == {("to_spawn/tests/probe.py", "f")}


def test_pruef_test_sucht_auch_in_unter_ordnern_namens_probe(tmp_path: Path) -> None:
    """#447: Nur der oberste ``probe/`` ist ausgenommen, nicht etwa ``to_spawn/probe/``."""
    for rel in ("probe/x.py", "to_spawn/probe/x.py"):
        datei = tmp_path / rel
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text('import subprocess\ndef f():\n    subprocess.Popen(["x"])\n', encoding="utf-8")
    assert _start_stellen(tmp_path) == {("to_spawn/probe/x.py", "f")}


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


_CMD_ATTRAPPE = '@"{python}" "{skript}" %*\r\n'


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe-Notnagel gibt es nur unter Windows")
@pytest.mark.parametrize(
    "argument",
    ['a\\"&echo PWN>pwn.txt', 'a"&echo PWN>pwn.txt', "zeile1\nzeile2", "zeile1\rzeile2"],
)
def test_shell_notnagel_lehnt_umbruch_und_anfuehrung_bei_cmd_ziel_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    """Abschluss-Review 1/2: Bei einem ``.cmd``-Ziel (wie der npm-Shim ``claude.cmd``) wertet
    cmd.exe ``%*`` ein zweites Mal aus — ``a\\"&…`` liefe dort als eigener Befehl (BatBadBut),
    ein Zeilenumbruch schnitte das Argument still ab. Beides muss laut scheitern, ohne Start."""
    attrappen = Path(os.environ[starter.TEST_BIN_ENV])
    skript = attrappen / "zeig_argv.py"
    skript.write_text(
        "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    shim = attrappen / "zeig.cmd"
    shim.write_text(_CMD_ATTRAPPE.format(python=sys.executable, skript=skript), encoding="utf-8")
    beleg = tmp_path / "argv.json"
    echt = subprocess.run

    def ohne_direktstart(befehl: object, **kw: object) -> object:
        if not kw.get("shell"):
            raise FileNotFoundError("Direktstart simuliert gescheitert")
        return echt(befehl, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", ohne_direktstart)
    with pytest.raises(ValueError, match="Notnagel"):
        starter.starte([str(shim), str(beleg), argument], cwd=tmp_path, warten=True, timeout=60)
    assert not (tmp_path / "pwn.txt").exists(), "zweiter Befehl lief"
    assert not beleg.exists(), "Ziel lief trotz Ablehnung"


def _zeig_cmd_ziel() -> tuple[Path, Path]:
    """``.cmd``-Attrappe (wie npm-Shim ``claude.cmd``), die ihr argv als JSON ablegt."""
    attrappen = Path(os.environ[starter.TEST_BIN_ENV])
    skript = attrappen / "zeig_argv.py"
    skript.write_text(
        "import json, pathlib, sys\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    shim = attrappen / "zeig.cmd"
    shim.write_text(_CMD_ATTRAPPE.format(python=sys.executable, skript=skript), encoding="utf-8")
    return shim, attrappen / "argv.json"


@pytest.mark.skipif(os.name != "nt", reason="Batch-Ziele (.cmd/.bat) gibt es nur unter Windows")
@pytest.mark.parametrize("argument", ["a&echo.>pwn.txt", "x|echo.>pwn.txt"])
def test_direktstart_cmd_ziel_schleust_keinen_zweiten_befehl_ein(tmp_path: Path, argument: str) -> None:
    """Review Fixrunde B1: ``finde("claude")`` liefert beim npm-Shim ``claude.CMD``; der
    Direktstart reichte ``a&echo.>…`` (ohne Leerzeichen, also ohne Anführung durch
    ``list2cmdline``) unentschärft an cmd.exe — ein zweiter Befehl lief."""
    shim, beleg = _zeig_cmd_ziel()
    lauf = starter.starte([str(shim), str(beleg), argument], cwd=tmp_path, warten=True, timeout=60)
    assert not (tmp_path / "pwn.txt").exists(), "zweiter Befehl lief"
    assert lauf.returncode == 0
    assert json.loads(beleg.read_text(encoding="utf-8")) == [argument]


@pytest.mark.skipif(os.name != "nt", reason="Batch-Ziele (.cmd/.bat) gibt es nur unter Windows")
@pytest.mark.parametrize("weg", ["direkt", "notnagel"])
def test_cmd_ziel_bekommt_sonderzeichen_als_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, weg: str) -> None:
    """Review Fixrunde B3: bei ``.cmd``-Ziel kommen ``& | ^ % < > ( )`` (ohne ``"``) auf beiden
    Wegen unverändert als Text an und starten keinen Befehl."""
    shim, beleg = _zeig_cmd_ziel()
    args = ["a&echo.>pwn.txt", "x|y", "<z>", "^q", "%PATH%", "(k)", "!x!", "mit leer", "ende\\"]
    if weg == "notnagel":
        echt = subprocess.run

        def ohne_direktstart(befehl: object, **kw: object) -> object:
            if not kw.get("shell"):
                raise FileNotFoundError("Direktstart simuliert gescheitert")
            return echt(befehl, **kw)  # type: ignore[call-overload]

        monkeypatch.setattr(subprocess, "run", ohne_direktstart)
    lauf = starter.starte([str(shim), str(beleg), *args], cwd=tmp_path, warten=True, timeout=60)
    assert lauf.returncode == 0
    assert not (tmp_path / "pwn.txt").exists(), "zweiter Befehl lief"
    assert json.loads(beleg.read_text(encoding="utf-8")) == args


@pytest.mark.skipif(os.name != "nt", reason="Batch-Ziele (.cmd/.bat) gibt es nur unter Windows")
def test_direktstart_cmd_ziel_lehnt_anfuehrung_ab(tmp_path: Path) -> None:
    """Review Fixrunde B1: ``a"&echo.>…`` lief im Direktstart als zweiter Befehl; jetzt gilt
    die Notnagel-Regel (BatBadBut) auch hier — laut scheitern, ohne Start."""
    shim, beleg = _zeig_cmd_ziel()
    with pytest.raises(ValueError, match="BatBadBut"):
        starter.starte([str(shim), str(beleg), 'a"&echo.>pwn.txt'], cwd=tmp_path, warten=True, timeout=60)
    assert not (tmp_path / "pwn.txt").exists(), "zweiter Befehl lief"
    assert not beleg.exists(), "Ziel lief trotz Ablehnung"


@pytest.mark.parametrize("ziel", ["x.cmd.", "x.bat ", "x.CMD. .", "x.cmd"])
def test_batch_ziel_mit_punkt_oder_leerzeichen_am_ende(ziel: str) -> None:
    """End-Review G4: Windows schneidet ``.``/Leerzeichen am Ende ab und startet doch über cmd."""
    assert starter._endung(ziel) in {".cmd", ".bat"}


@pytest.mark.parametrize(
    "quelle",
    [
        'from to_spawn import konsole\ndef f():\n    konsole.lauf_daten(["claude", "-p", "x"])',
        'from to_spawn import konsole\ndef f():\n    konsole.lauf_daten(["wt", "new-tab", "x"])',
        'from to_spawn import konsole\ndef f(z):\n    konsole.lauf_daten(["ssh", "bau-server", z])',
        'from to_spawn import konsole\ndef f(z):\n    konsole.lauf_daten(["bash", "-lc", z])',
        "from to_spawn import konsole\ndef f(befehl):\n    konsole.lauf_daten(befehl)",
        'from to_spawn import konsole\ndef f():\n    konsole.lauf_daten(befehl=["claude"])',
        'from to_spawn import konsole as k\ndef f():\n    k.lauf_daten(["claude"])',
        "from . import konsole as k\ndef f(befehl):\n    k.lauf_daten(befehl)",
        'from to_spawn.konsole import lauf_daten\ndef f():\n    lauf_daten(["claude"])',
        'from .konsole import lauf_daten as ld\ndef f():\n    ld(["ssh", "h", "x"])',
        'import to_spawn.konsole as k\ndef f():\n    k.lauf_daten(["claude"])',
        'from to_spawn import konsole\ndef f():\n    lauf = konsole.lauf_daten\n    lauf(["x"])',
    ],
)
def test_pruef_test_meldet_start_ueber_konsole_lauf_daten(quelle: str) -> None:
    """#608 (2): ``konsole.lauf_daten`` ist nur für Lese-Befehle; Starts/dynamische Befehle fallen auf."""
    assert _funde_in(quelle, "probe.py") == {("probe.py", "f")}


def test_pruef_test_laesst_lese_befehl_ueber_konsole_lauf_daten_durch() -> None:
    """#608 (2): Fester Lese-Befehl über ``konsole.lauf_daten`` bleibt erlaubt."""
    quelle = 'from to_spawn import konsole\ndef f():\n    konsole.lauf_daten(["git", "rev-parse", "HEAD"])\n'
    assert _funde_in(quelle, "probe.py") == set()
