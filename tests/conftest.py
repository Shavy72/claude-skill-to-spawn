"""Gemeinsame Test-Einstellungen.

Die Wächter-Tests aus dem ersten Bau von #213 (``test_waechter_213.py`` und der
Weg-Test) prüfen die Regeln selbst: Ticket schließen, sofort ein Tick, Ergebnis.
Seit der Fixrunde wartet capo 15 min nach dem Schließen (Karenz) und nimmt beim
ersten Tick alle geschlossenen Tickets als Ausgangsstand. Diese beiden
Zeitgrenzen schaltet ``TO_SPAWN_WAECHTER_SOFORT=1`` ab — nur für diese beiden
Dateien. Die Zeitgrenzen selbst prüft ``test_waechter_213_fix.py`` ohne Schalter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

import pytest

# Kein Bytecode im Quellbaum: Tests laden u. a. ``repo-scripts/*.py`` per Dateipfad (#155).
sys.dont_write_bytecode = True

# Test-Helfer (``tests/hilfen``) einmal importierbar machen — z. B. ``context_mode_attrappe`` (#237).
_HILFEN = str(Path(__file__).resolve().parent / "hilfen")
if _HILFEN not in sys.path:
    sys.path.insert(0, _HILFEN)

from git_bash import git_bash  # noqa: E402 — liegt in ``tests/hilfen`` (Pfad oben)

_WINDOWS = sys.platform == "win32"

#: Zeitgrenze je Test in Sekunden (#68): ein hängender Test (unter Windows z. B.
#: ``test_aufseher_abloesung_436``) darf die Suite nicht unbegrenzt aufhalten. Gilt nur,
#: wenn ``pytest-timeout`` installiert ist (``tests/requirements.txt``) und weder
#: ``--timeout`` noch ``timeout``-Einstellung gesetzt ist; eigene ``@pytest.mark.timeout``
#: gehen vor. 600 s liegt weit über dem längsten Einzeltest auf dem ausgelasteten Bau-Server.
ZEITGRENZE_S = 600


#: Plattform-Bedarf, der kein Programm im PATH ist (``@pytest.mark.braucht("posix")``):
#: POSIX-Dateirechte, ``signal.SIGKILL``, ``/proc`` — unter Windows nicht vorhanden.
_NUR_POSIX = frozenset({"posix"})


def braucht_fehlt(name: str, plattform: str = sys.platform, finde=shutil.which) -> str | None:
    """Grund, warum der Test-Bedarf ``name`` hier fehlt (``None`` = vorhanden), #68 P7b.

    Einzige Stelle dieses Wissens: ``posix`` fehlt unter Windows; ``bash`` zählt unter
    Windows nur als echte Shell (Git-Bash), nicht als WSL-Stub aus ``System32``/
    ``WindowsApps`` (startet ohne Distribution nicht); sonst Programm im PATH.
    """
    if name in _NUR_POSIX:
        return "nur POSIX (Linux/macOS)" if plattform == "win32" else None
    pfad = finde(name)
    if pfad is None:
        return f"{name} fehlt im PATH"
    if name == "bash" and plattform == "win32":
        teile = {t.lower() for t in PureWindowsPath(pfad).parts}
        if "system32" in teile or "windowsapps" in teile:
            return f"bash ist nur der WSL-Stub ({pfad})"
    return None


def attrappe(ordner: Path, name: str, python_code: str) -> Path:
    """Ausführbare Programm-Attrappe ``ordner/name`` (z. B. ``gh``, ``ssh``), #93.

    Einzige Stelle des Wissens, wie eine Attrappe je Betriebssystem startbar wird:
    POSIX ein Python-Skript mit Shebang auf den laufenden Interpreter
    (``sys.executable``, kein festes ``python3``), Windows zusätzlich ``name.cmd``
    als Hülle (PATHEXT findet sie). Das Verhalten (Ausgabe, Exit-Code, Protokoll,
    stdin, Antwort je Argument) steht allein in ``python_code``; er wird
    ``textwrap.dedent``-bereinigt. ``ordner`` wird bei Bedarf angelegt.
    Rückgabe: Pfad des Skripts.
    """
    ordner.mkdir(parents=True, exist_ok=True)
    skript = ordner / name
    skript.write_text(f"#!{sys.executable}\n{textwrap.dedent(python_code)}", encoding="utf-8")
    skript.chmod(skript.stat().st_mode | 0o111)
    if os.name == "nt":
        (ordner / f"{name}.cmd").write_text(f'@"{sys.executable}" "{skript}" %*\r\n', encoding="utf-8")
    return skript


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        'braucht(*namen): Test braucht Programm im PATH (z. B. "tmux", "bash") oder "posix"; fehlt es → skip (#68)',
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _braucht_ueberspringen(items)
    _zeitgrenze_setzen(config, items)


def _braucht_ueberspringen(items: list[pytest.Item]) -> None:
    gruende: dict[str, str | None] = {}
    for item in items:
        for marke in item.iter_markers("braucht"):
            for name in marke.args:
                if name not in gruende:
                    gruende[name] = braucht_fehlt(name)
                if gruende[name]:
                    item.add_marker(pytest.mark.skip(reason=f"braucht {name}: {gruende[name]}"))


def _zeitgrenze_setzen(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.pluginmanager.hasplugin("timeout"):
        return
    if config.getoption("timeout", None) is not None or config.getini("timeout"):
        return
    for item in items:
        if item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(ZEITGRENZE_S))


def altbestand_wie_eingerichtet() -> Path | None:
    """Legt unter Windows die Altbestand-Liste an, wie ein eingerichteter Laptop sie hat (#68).

    Aufruf in Hausmeister-Welten, nachdem ``TO_SPAWN_HAUSMEISTER_LOG`` gesetzt ist. Grund:
    Fehlt die Liste unter Windows, hält der erste echte Lauf absichtlich alle Ordner fest
    (#1373, E16) — die Regel-Tests setzen aber den eingerichteten Zustand voraus. Geschrieben
    wird die leere, nicht freigegebene Liste (``{"freigegeben": false, "ordner": []}``):
    sie hält keinen Ordner fest, und ``altbestand_festhalten`` ergänzt sie weiter wie echt.
    Pfad kommt aus ``hausmeister.altbestand_pfad()`` (eine Stelle). Linux/macOS: nichts,
    Rückgabe ``None`` (dort hat die fehlende Liste keine Wirkung).
    """
    if not _WINDOWS:
        return None
    from to_spawn import hausmeister

    pfad = hausmeister.altbestand_pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text('{"freigegeben": false, "ordner": []}\n', encoding="utf-8")
    return pfad


#: Programmnamen, die unter Windows auf Git-Bash zeigen müssen (#68): ``CreateProcess``
#: sucht ``System32`` VOR ``PATH`` ab, ein blankes ``"bash"`` träfe dort immer den WSL-Stub.
_BASH_NAMEN = frozenset({"bash", "bash.exe"})


def _bash_aufloesen(args: Any) -> Any:
    """Ersetzt unter Windows ein blankes ``bash`` als Programm durch Git-Bash.

    Nur Listen/Tupel, deren erstes Element genau ``bash``/``bash.exe`` ist (kein Pfad);
    alles andere geht unverändert durch. Fehlt Git-Bash, überspringt der laufende Test mit
    Grund — die geprüfte Sache braucht eine echte Bash, und der WSL-Stub ist keine.
    """
    if not isinstance(args, (list, tuple)) or not args:
        return args
    if str(args[0]).lower() not in _BASH_NAMEN:
        return args
    bash = git_bash()
    if bash is None:
        pytest.skip("braucht bash: keine Git-Bash gefunden (nur WSL-Stub oder gar keine)")
    return type(args)([bash, *args[1:]])


@pytest.fixture(autouse=True)
def _git_bash_statt_wsl_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unter Windows startet ein blankes ``bash`` aus Tests Git-Bash, nie den WSL-Stub (#68).

    Zwei Wege, beide nur unter ``win32``: (1) ``subprocess.Popen`` (trägt ``run``,
    ``check_output``, ``call``) löst ``["bash", …]`` über ``_bash_aufloesen`` auf;
    (2) der Git-Bash-Ordner steht vorn in ``PATH`` — für Kindprozesse, die ``bash`` per
    ``shutil.which`` suchen. Linux/macOS: keine Wirkung.
    """
    if not _WINDOWS:
        return
    original = subprocess.Popen.__init__

    def _init(self: subprocess.Popen, args: Any, *rest: Any, **kw: Any) -> None:
        if kw.get("executable") is None:
            args = _bash_aufloesen(args)
        original(self, args, *rest, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", _init)
    bash = git_bash()
    if bash is not None:
        ordner = str(Path(bash).parent)
        monkeypatch.setenv("PATH", f"{ordner}{os.pathsep}{os.environ.get('PATH', '')}")


class _HeimMonkeyPatch(pytest.MonkeyPatch):
    """``MonkeyPatch``, das unter Windows ``HOME`` nach ``USERPROFILE`` mitzieht (#68).

    ``os.path.expanduser``/``Path.home()`` lesen unter Windows ``USERPROFILE``, nicht
    ``HOME``. Tests biegen den Heimordner per ``setenv("HOME", …)`` um; diese eine Stelle
    zieht ``USERPROFILE`` mit (``delenv("HOME")`` lässt ``USERPROFILE`` stehen). Ein
    ausdrückliches ``setenv("USERPROFILE", …)`` danach gewinnt. Linux/macOS: wie das
    eingebaute ``MonkeyPatch``.
    """

    def setenv(self, name: str, value: str, prepend: str | None = None) -> None:
        super().setenv(name, value, prepend)
        if _WINDOWS and name == "HOME" and prepend is None:
            super().setenv("USERPROFILE", value)


@pytest.fixture
def monkeypatch() -> Iterator[pytest.MonkeyPatch]:
    """Ersetzt das eingebaute ``monkeypatch`` durch ``_HeimMonkeyPatch`` (Rückbau wie dort)."""
    mp = _HeimMonkeyPatch()
    yield mp
    mp.undo()


_SOFORT_MODULE = frozenset({"test_waechter_213", "test_waechter_213_weg"})


@pytest.fixture(autouse=True)
def _keine_to_spawn_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Löscht vor jedem Test alle ``TO_SPAWN_*``-Variablen.

    Die Fremd-Repo-Suite darf nie in die laufende Bau-Session schreiben (#257):
    ohne diese Fixture erbt jeder Testlauf ``TO_SPAWN_LOG_REPO``/``TO_SPAWN_REPO``
    der Bau-Session, und Hook-Zeilen landeten so in
    ``wt-257/.to-spawn/bau_log/901.jsonl`` statt im Test-``tmp_path``. Läuft vor
    ``_waechter_sofort`` (Parameter-Reihenfolge unten), die danach gezielt
    ``TO_SPAWN_WAECHTER_SOFORT`` setzt.
    """
    for name in list(os.environ):
        if name.startswith("TO_SPAWN_"):
            monkeypatch.delenv(name, raising=False)
    # Seit #257 Fixrunde 1 (F5) brechen bau.py/wache.py ohne GitHub-Origin mit Exit 2 ab
    # (kein stiller DuoPlus-Rückfall mehr). Die Weg-Tests arbeiten mit lokalen bare-Origins
    # → die ausdrückliche Vorgabe ``TO_SPAWN_GH_REPO`` gilt nur, wenn kein GitHub-Origin
    # da ist; Tests mit github.com-Origin sehen weiter ihren eigenen Slug.
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test-org/test-repo")


@pytest.fixture(autouse=True)
def _speicher_immer_frei(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Speicher-Schutz (#257 Paket B) sieht in Tests immer „frei“.

    Die Starter (``bau``, ``wache``, ``spawn_srv.sh``, Aufpasser) fragen vor jedem
    Claude-Start ``to_spawn.speicher``. Ohne diese Fixture hinge das Ergebnis der
    ganzen Suite an den Sessions, die gerade auf der Testmaschine laufen (12 Claude-
    Prozesse → jeder Start-Test rot). Die Test-Tür des Moduls zeigt deshalb auf eine
    Attrappe mit 64 GiB frei und leerer Prozessliste. ``test_speicher_257`` stellt
    für seine Fälle eigene Attrappen (knapp/voll) über dieselben Variablen.
    """
    ordner = tmp_path_factory.mktemp("speicher-frei")
    meminfo = ordner / "meminfo"
    meminfo.write_text(f"MemAvailable:    {64 * 1024 * 1024} kB\n", encoding="utf-8")
    (ordner / "proc").mkdir()
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(ordner / "proc"))


@pytest.fixture(autouse=True)
def _waechter_sofort(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    if request.module.__name__ in _SOFORT_MODULE:
        monkeypatch.setenv("TO_SPAWN_WAECHTER_SOFORT", "1")
    else:
        monkeypatch.delenv("TO_SPAWN_WAECHTER_SOFORT", raising=False)


@pytest.fixture(autouse=True)
def _start_nur_attrappen(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Start-Schutz (#1406): ``to_spawn.starter`` startet unter Test nur Attrappen.

    Setzt nur die beiden Variablen; sie erben sich in Kind-Prozesse (``wache.py``,
    ``bau.py``), wo monkeypatch nicht hinreicht. Attrappen gehören in den Ordner
    ``TO_SPAWN_TEST_BIN``. Läuft nach ``_keine_to_spawn_umgebung`` (löscht ``TO_SPAWN_*``).
    """
    monkeypatch.setenv("TO_SPAWN_TESTLAUF", "1")
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(tmp_path_factory.mktemp("start-attrappen")))


@pytest.fixture(autouse=True)
def _start_stelle_im_test(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Start-Stelle (#97): Sperren je Test in eigenem Ordner, Tickets gelten als offen.

    Ohne den Ordner teilten parallele Tests mit gleicher Ticket-Nummer eine Sperre;
    ohne ``TO_SPAWN_TEST_TICKET_OFFEN`` fragte jeder ``bau.main``-Test echtes gh.
    Tests der Start-Stelle selbst löschen die zweite Variable (``tests/test_startstelle_97.py``).
    """
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path_factory.mktemp("start-sperren")))
    monkeypatch.setenv("TO_SPAWN_TEST_TICKET_OFFEN", "1")


@pytest.fixture(autouse=True)
def _hausmeister_nur_im_test(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Hausmeister (#1369) räumt in Tests nie echte Ordner auf.

    Start-Check (``to_spawn.py speicher``, ``bau.py``) und Aufpasser rufen
    ``hausmeister.aufraeumen_alles`` — ohne diese Fixture liefe das mit
    ``loeschen=True`` über die echten ``~/wt/*`` der Testmaschine. Wurzeln zeigen
    auf einen leeren Ordner, Logs in ``tmp``, Platte gilt als frei. Tests mit
    eigener Welt (``test_hausmeister_1368/1369``) setzen die Variablen neu.
    """
    ordner = tmp_path_factory.mktemp("hausmeister")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(ordner / "keine" / "*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(ordner / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(ordner / "hindernisse.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "1")
    # #1371: keine echten GitHub-Remotes — sonst löschte ein Test alte sicherung/*-Zweige.
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
    # #1439: Zweig-Lauf fasst keine echten Hauptrepos an (leer = keine).
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "")
    # #1372: Docker-Reste nie echt — „true“ statt docker, keine Staging-Sperre.
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_DOCKER", shutil.which("true") or "/bin/true")
    monkeypatch.setenv("TO_SPAWN_STAGING_SPERRE", str(ordner / "keine-staging-sperre"))
    # #145: Sicherung (Rundgang, Aufseher-Ablösung) schreibt nur in tmp, lädt keine echten Zweige hoch.
    monkeypatch.setenv("TO_SPAWN_SICHERUNG_ZIEL", str(ordner / "sicherung"))
    monkeypatch.setenv("TO_SPAWN_SICHERUNG_ZWEIG_REPOS", "")


@pytest.fixture(autouse=True)
def _maschine_liste_im_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Maschinen-Liste (L1 #46) liegt in Tests immer unter ``tmp_path``.

    Ohne diese Fixture läse/schriebe ``config.maschine``/``maschine_eintragen`` die echte
    ``~/.config/to-spawn/maschine.json`` der Testmaschine. Die Datei wird nicht angelegt;
    Tests mit eigenem ``setenv("TO_SPAWN_MASCHINE", …)`` überschreiben den Wert.
    """
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(tmp_path / "to-spawn-maschine" / "maschine.json"))
