"""Gemeinsame Test-Einstellungen.

Die Wächter-Tests aus dem ersten Bau von #213 (``test_waechter_213.py`` und der
Weg-Test) prüfen die Regeln selbst: Ticket schließen, sofort ein Tick, Ergebnis.
Seit der Fixrunde wartet capo 15 min nach dem Schließen (Karenz) und nimmt beim
ersten Tick alle geschlossenen Tickets als Ausgangsstand. Diese beiden
Zeitgrenzen schaltet ``TO_SPAWN_WAECHTER_SOFORT=1`` ab — nur für diese beiden
Dateien. Die Zeitgrenzen selbst prüft ``test_waechter_213_fix.py`` ohne Schalter.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path, PurePath
from typing import Any

import pytest

# Kein Bytecode im Quellbaum: Tests laden u. a. ``repo-scripts/*.py`` per Dateipfad (#155).
sys.dont_write_bytecode = True

# Test-Helfer (``tests/hilfen``) einmal importierbar machen — z. B. ``context_mode_attrappe`` (#237).
_HILFEN = str(Path(__file__).resolve().parent / "hilfen")
if _HILFEN not in sys.path:
    sys.path.insert(0, _HILFEN)

from git_bash import git_bash  # noqa: E402 — liegt in ``tests/hilfen`` (Pfad oben)

from to_spawn import bash_wahl, hausmeister_windows, prozessbaum  # noqa: E402 — Pfad setzt ``git_bash``

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


def braucht_fehlt(name: str, plattform: str = sys.platform, finde=None) -> str | None:
    """Grund, warum der Test-Bedarf ``name`` hier fehlt (``None`` = vorhanden), #68 P7b.

    Einzige Stelle dieses Wissens: ``posix`` fehlt unter Windows; ``bash`` zählt unter
    Windows nur als echte Shell (Git-Bash), nicht als WSL-Stub aus ``System32``/
    ``WindowsApps`` (startet ohne Distribution nicht); sonst Programm im PATH.
    """
    if name in _NUR_POSIX:
        return "nur POSIX (Linux/macOS)" if plattform == "win32" else None
    if name == "proc":  # Prozess-Dateisystem (Linux), fehlt unter Windows und macOS
        return None if Path("/proc/self/stat").exists() else "/proc fehlt"
    if name == "bash" and finde is None:  # echte Suche: die eine Tür ``bash_wahl``
        return None if git_bash() else "keine bash gefunden (Windows: nur WSL-Stub oder gar keine)"
    pfad = (finde or shutil.which)(name)
    if pfad is None:
        return f"{name} fehlt im PATH"
    if name == "bash" and plattform == "win32" and bash_wahl.ist_wsl_stub(pfad):
        return f"bash ist nur der WSL-Stub ({pfad})"
    return None


#: Von :func:`attrappe` angelegte Dateien (normalisierte Pfade): nur diese löst :func:`_shebang_aufloesen` auf.
_ATTRAPPEN: set[str] = set()


def stub_pfad(skript: Path) -> Path:
    """Pfad für ``TO_SPAWN_GH_STUB``-artige Env-Variablen: Windows ``name.py`` (Produktcode startet es über den Interpreter), sonst ``skript``."""
    return skript.with_name(skript.name + ".py") if os.name == "nt" else skript


def shell_befehl(skript: PurePath, interpreter: str | PurePath = sys.executable) -> str:
    """Shell-Befehlszeile für eine :func:`attrappe`, z. B. ``remote.origin.uploadpack`` (#643).

    ``git`` startet solche Konfig-Werte über ``sh -c`` (unter Windows Git-Bash/MSYS2):
    Backslashes im Windows-Pfad würden dort als Escape-Zeichen geschluckt, und ein
    Shebang trägt unter Windows weder Backslashes noch Leerzeichen im Interpreter-Pfad.
    Vorwärtsschrägstriche plus ``shlex.quote`` mit dem laufenden Interpreter starten
    die Attrappe unter Linux, macOS und Windows gleich.
    """
    interp = interpreter if isinstance(interpreter, PurePath) else Path(interpreter)
    return f"{shlex.quote(interp.as_posix())} {shlex.quote(skript.as_posix())}"


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
    text = f"#!{sys.executable}\n{textwrap.dedent(python_code)}"
    skript.write_text(text, encoding="utf-8")
    skript.chmod(skript.stat().st_mode | 0o111)
    _ATTRAPPEN.add(os.path.normcase(str(skript)))
    if os.name == "nt":
        # ``name.py`` für Produktcode, der einen Stub nur mit ``.py`` über den Interpreter startet (:func:`stub_pfad`, #553)
        skript.with_name(f"{name}.py").write_text(text, encoding="utf-8")
        (ordner / f"{name}.cmd").write_text(f'@"{sys.executable}" "{skript}" %*\r\n', encoding="utf-8")
    return skript


#: Ordner von Werkzeugen, kein Repo-Inhalt — Rückfall, wenn git nicht greift (#491, #532).
WERKZEUG_ORDNER = frozenset({".git", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache"})


def git_dateien(wurzel: Path) -> list[str] | None:
    """Relativpfade unter ``wurzel``, die git kennt (``git ls-files``); ``None`` ohne git bzw. Repo (#491, #532)."""
    try:
        lauf = subprocess.run(
            ["git", "-C", str(wurzel), "-c", "core.quotepath=false", "ls-files", "-z"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [rel for rel in lauf.stdout.split("\0") if rel]


def repo_dateien(ordner: Path) -> list[Path]:
    """Dateien unter ``ordner``, die zum Repo gehören — ohne ``__pycache__``/Caches (#491, #532).

    Im Git-Repo liefert ``git ls-files`` nur erfasste Dateien (übrig gebliebene
    ``__pycache__/*.pyc`` oder ``.ruff_cache`` fallen heraus). Ohne git: Rückfall auf
    ``ordner.rglob("*")`` ohne :data:`WERKZEUG_ORDNER`.
    """
    bekannt = git_dateien(ordner)
    kandidaten = ordner.rglob("*") if bekannt is None else (ordner / rel for rel in bekannt)
    return sorted(
        p
        for p in kandidaten
        if p.is_file() and not (WERKZEUG_ORDNER & set(p.relative_to(ordner).parts))
    )


#: Eigener Temp-Ordner dieses Laufs (#349), nur gesetzt, wenn ``pytest_configure`` ihn anlegte.
_EIGENER_BASETEMP = pytest.StashKey[Path]()

#: Marker-Datei im eigenen Temp-Ordner (#177): PID des Leit-Prozesses. Nur Ordner mit
#: diesem Marker räumt :func:`_reste_raeumen` — nie nach Namen raten (#136).
_MARKER = ".pt-lauf"

#: Schonfrist für Reste toter Läufe (#177): so lange bleibt ein roter Lauf zum Nachsehen.
_LOG = logging.getLogger(__name__)

#: ponytail: feste Frist, Einstellung über ``temp_ordner``/Maschinen-Liste, wenn eine Maschine anders braucht.
_RESTE_ALTER_S = 3600

#: Ab diesem Inode-Anteil der Temp-Wurzel gilt nur noch :data:`_RESTE_ALTER_KNAPP_S` (#355):
#: eine volle Suite hinterlässt rot ~220k Einträge, ``/tmp`` als tmpfs hat oft nur 1 Mio.
_INODE_GRENZE = 0.70

#: Mindestalter toter Reste bei Inode-Knappheit (#355) — nie ein gerade startender Lauf.
_RESTE_ALTER_KNAPP_S = 600


@pytest.hookimpl(tryfirst=True)  # vor ``_pytest.tmpdir``, das ``--basetemp`` in seinem configure liest
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        'braucht(*namen): Test braucht Programm im PATH (z. B. "tmux", "bash"), "posix" oder "proc"; fehlt es → skip (#68)',
    )
    _eigener_basetemp(config)


def _eigener_basetemp(config: pytest.Config) -> None:
    """Jeder Lauf ohne ``--basetemp`` bekommt einen eigenen, kurzen Temp-Ordner (#349).

    pytest legt sonst alle Läufe des Nutzers unter ``<temp>/pytest-of-<nutzer>`` ab; parallele
    Läufe nahmen sich dort gegenseitig den Ordner weg (``could not create numbered dir``).
    Hier: ``mkdtemp`` direkt unter dem System-Temp (``tempfile.gettempdir()``, folgt ``TMPDIR``,
    also #136), Name ``pt-`` + 8 Zeichen — kurz für tmux-/Unix-Sockets darunter. Ein
    ausdrückliches ``--basetemp`` gewinnt. Nur der Leit-Prozess: xdist-Worker bekommen
    ``<basetemp>/popen-gwN`` vom Leit-Prozess (``workerinput``).
    """
    if getattr(config.option, "basetemp", None) or hasattr(config, "workerinput"):
        return
    _reste_raeumen(Path(tempfile.gettempdir()))
    ordner = Path(tempfile.mkdtemp(prefix="pt-"))
    config.option.basetemp = str(ordner)
    config.stash[_EIGENER_BASETEMP] = ordner


def _inode_anteil(temp: Path) -> float | None:
    """Belegter Inode-Anteil (0..1) des Laufwerks unter ``temp`` (#355); ``None`` = unbekannt.

    Unbekannt heißt: kein ``os.statvfs`` (Windows), Fehler, oder das Dateisystem meldet keine
    feste Inode-Zahl (``f_files == 0``, z. B. btrfs). Dann gilt die normale Schonfrist.
    """
    statvfs = getattr(os, "statvfs", None)
    if statvfs is None:
        return None
    try:
        werte = statvfs(temp)
    except OSError:
        return None
    if werte.f_files <= 0:
        return None
    return 1 - werte.f_ffree / werte.f_files


def _reste_raeumen(temp: Path, inode_anteil: Callable[[Path], float | None] = _inode_anteil) -> None:
    """Alte Reste eigener Läufe unter ``temp`` löschen (#177).

    Nur ``pt-*``-Ordner mit Marker, deren Leit-Prozess nicht mehr lebt und deren Marker älter
    als :data:`_RESTE_ALTER_S` ist — bei Inode-Knappheit (``inode_anteil`` ≥ :data:`_INODE_GRENZE`)
    nur älter als :data:`_RESTE_ALTER_KNAPP_S` (#355). Ohne Marker, unlesbar, PID außerhalb
    ``0 < pid < 2**31`` oder lebende PID: bleibt liegen. Eine wiederverwendete PID lässt einen
    Rest nur länger liegen, nie einen Lauf verlieren. Jeder Fehler beim Räumen wird geloggt,
    nie ein roter Lauf.
    """
    try:
        _reste_raeumen_roh(temp, inode_anteil)
    except Exception as fehler:  # noqa: BLE001 — Räumen ist Komfort, darf nie einen Lauf kippen
        _LOG.warning("pt-Räumer abgebrochen unter %s: %r", temp, fehler)


def _pt_ordner_loeschen(ordner: Path) -> None:
    """Löscht einen ``pt-*``-Ordner; unter Windows erst Schreibschutz lösen (``.git/objects``, #655).

    Einzige Lösch-Stelle für ``pt-*``-Ordner (``_reste_raeumen_roh`` und ``pytest_sessionfinish``).
    Nutzt :func:`to_spawn.hausmeister_windows.schreibschutz_weg` (eine Stelle für den
    Schreibschutz-Handler). Unter POSIX bleibt ``shutil.rmtree`` ohne Handler: dort sperrt
    ein Nur-Lese-Bit an Dateien das Löschen nicht, und fehlende Ordnerrechte bleiben sichtbar.
    """
    if not _WINDOWS:
        shutil.rmtree(ordner)
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(ordner, onexc=hausmeister_windows.schreibschutz_weg)
    else:
        shutil.rmtree(ordner, onerror=hausmeister_windows.schreibschutz_weg)


def _reste_raeumen_roh(temp: Path, inode_anteil: Callable[[Path], float | None]) -> None:
    anteil = inode_anteil(temp)
    knapp = anteil is not None and anteil >= _INODE_GRENZE
    grenze = time.time() - (_RESTE_ALTER_KNAPP_S if knapp else _RESTE_ALTER_S)
    try:
        kandidaten = [p for p in temp.iterdir() if p.name.startswith("pt-")]
    except OSError as fehler:
        _LOG.warning("pt-Räumer: %s nicht lesbar: %s", temp, fehler)
        return
    for ordner in kandidaten:
        marker = ordner / _MARKER
        try:
            if ordner.is_symlink() or marker.stat().st_mtime > grenze:
                continue
            pid = int(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue  # ohne Marker/unlesbar: nicht von uns, kein Log-Rauschen
        if not 0 < pid < 2**31:  # 0/negativ gilt unter Linux als „tot“, riesig läuft unter Windows über
            continue
        if prozessbaum.lebt(pid):
            continue
        try:
            _pt_ordner_loeschen(ordner)
        except OSError as fehler:
            _LOG.warning("pt-Räumer: Rest %s nicht löschbar: %s", ordner, fehler)


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Eigenen Temp-Ordner markieren (#177): PID des Leit-Prozesses in :data:`_MARKER`.

    Erst hier, weil pytest einen gegebenen ``--basetemp`` beim ersten Zugriff leert
    (``getbasetemp``); der Aufruf hier legt ihn endgültig an, der Marker überlebt.
    """
    ordner = session.config.stash.get(_EIGENER_BASETEMP, None)
    if ordner is None:
        return
    fabrik = getattr(session.config, "_tmp_path_factory", None)  # privat; fehlt z. B. bei ``-p no:tmpdir``
    if fabrik is None:
        return
    try:
        fabrik.getbasetemp()  # leert + legt an, einmal je Lauf
        (ordner / _MARKER).write_text(f"{os.getpid()}\n", encoding="utf-8")
    except (AttributeError, OSError) as fehler:
        _LOG.warning("pt-Marker in %s nicht geschrieben, Lauf geht ohne weiter: %s", ordner, fehler)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Grüner Lauf: den selbst angelegten Temp-Ordner löschen (#349); rot bleibt er zum Nachsehen.

    Nur der in ``_eigener_basetemp`` angelegte Pfad, nie ein Muster und nie ein fremdes
    ``--basetemp``. Reste roter Läufe räumt der Temp-Räumer (#177).
    """
    ordner = session.config.stash.get(_EIGENER_BASETEMP, None)
    if ordner is not None and exitstatus == 0:
        try:
            _pt_ordner_loeschen(ordner)
        except FileNotFoundError:
            pass
        except OSError as fehler:
            _LOG.warning("pt-Ordner %s nach grünem Lauf nicht löschbar: %s", ordner, fehler)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Leeren eigenen Temp-Ordner löschen (#177): ``--help``/``--markers`` starten keine Session."""
    ordner = config.stash.get(_EIGENER_BASETEMP, None)
    if ordner is not None:
        try:
            ordner.rmdir()  # nur leer; ein markierter Ordner bleibt für den Räumer
        except OSError:
            pass


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


def _shebang_aufloesen(args: Any) -> Any:
    """Windows führt kein Shebang-Skript aus (``WinError 193``), Attrappen aber sind genau das (#553).

    Liste/Tupel, deren erstes Element eine von :func:`attrappe` angelegte Datei ist,
    startet über ``sys.executable`` (Shebang-Interpreter der Attrappe); alles
    andere geht unverändert durch. Einzige Stelle: wirkt für jeden Test, der eine Attrappe
    per Pfad startet (``Popen``, ``run``, ``check_output``).
    """
    if not isinstance(args, (list, tuple)) or not args or not isinstance(args[0], (str, os.PathLike)):
        return args
    if os.path.normcase(str(args[0])) not in _ATTRAPPEN:
        return args
    interpreter = sys.executable  # so schreibt :func:`attrappe` die Shebang-Zeile
    return type(args)([interpreter, *args])


@pytest.fixture(autouse=True)
def _kommentar_sperre_leeren() -> None:
    """#530: die Doppel-Sperre in ``befund.kommentiere`` merkt je Prozess — jeder Test startet leer."""
    from to_spawn import befund

    befund._gesendet.clear()


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
            args = _shebang_aufloesen(_bash_aufloesen(args))
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
    # #460: Session-Status und vergebene Plätze ebenfalls aus der Attrappe, nie vom echten Rechner.
    (ordner / "sessions").mkdir()
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(ordner / "sessions"))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(ordner / "platz.json"))


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
def _arbeitsstand_im_test(monkeypatch: pytest.MonkeyPatch, _keine_to_spawn_umgebung: None) -> None:
    """Arbeitsordner-Stand (#283) gilt in Tests als aktuell — Wegwerf-Repos haben kein echtes origin.

    Erbt sich in Kind-Prozesse (``bau.py``). ``tests/test_arbeitsstand_283.py`` löscht die
    Variable und prüft mit echtem git.
    """
    monkeypatch.setenv("TO_SPAWN_TEST_ARBEITSSTAND_OK", "1")


def _eigene_nachfahren(alle: dict[int, tuple[int, int]], wurzel: int) -> dict[int, tuple[int, int]]:
    """Filtert eine Windows-Momentaufnahme auf Nachfahren von ``wurzel`` (#649).

    So sieht die Hausmeister-Prüfung in Tests nie fremde Rechner-Prozesse (die gerade starten
    oder enden und ohne cwd-Griff im PEB einen Test zufällig sperren), wohl aber jeden vom
    Test selbst gestarteten Kindprozess samt Enkeln (z. B. ``sleep``, ``git``, MSYS-Kind).
    Recycelte Eltern-PIDs fallen heraus wie in :func:`prozessbaum._kinder`.
    """
    kinder: dict[int, list[int]] = {}
    for pid, (eltern, start) in alle.items():
        if pid == eltern:
            continue
        vorfahr = alle.get(eltern)
        if vorfahr is None:
            if eltern != wurzel:
                continue
        elif vorfahr[1] and start and vorfahr[1] > start:
            continue
        kinder.setdefault(eltern, []).append(pid)
    eigene: set[int] = set()
    warteschlange = list(kinder.get(wurzel, ()))
    while warteschlange:
        pid = warteschlange.pop()
        if pid not in eigene:
            eigene.add(pid)
            warteschlange.extend(kinder.get(pid, ()))
    return {pid: alle[pid] for pid in eigene}


@pytest.fixture(autouse=True)
def _hausmeister_nur_im_test(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Hausmeister (#1369) räumt in Tests nie echte Ordner auf und sieht keine Fremdprozesse (#649).

    Start-Check (``to_spawn.py speicher``, ``bau.py``) und Aufpasser rufen
    ``hausmeister.aufraeumen_alles`` — ohne diese Fixture liefe das mit
    ``loeschen=True`` über die echten ``~/wt/*`` der Testmaschine. Wurzeln zeigen
    auf einen leeren Ordner, Logs in ``tmp``, Platte gilt als frei. Unter Windows
    bleibt die Prozessprüfung bei eigenen Kindprozessen (kein echtes PowerShell,
    keine fremden Rechner-Prozesse im PEB). Tests mit eigener Welt
    (``test_hausmeister_1368/1369``) setzen die Variablen neu; PowerShell-Attrappen
    (``_POWERSHELL != "powershell"``) und direkte ``_prozesse()``-Tests laufen normal.
    """
    ordner = tmp_path_factory.mktemp("hausmeister")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(ordner / "keine" / "*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(ordner / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(ordner / "hindernisse.jsonl"))
    monkeypatch.setenv("TO_SPAWN_INSTALL_LOG", str(ordner / "install_fehler.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "1")
    # #1371: keine echten GitHub-Remotes — sonst löschte ein Test alte sicherung/*-Zweige.
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
    # #1439: Zweig-Lauf fasst keine echten Hauptrepos an (leer = keine).
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "")
    # #1372: Docker-Reste nie echt — „true“ statt docker, keine Staging-Sperre.
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_DOCKER", shutil.which("true") or "/bin/true")
    monkeypatch.setenv("TO_SPAWN_STAGING_SPERRE", str(ordner / "keine-staging-sperre"))
    # #136: Temp-Ordner nie echt (kein ~/tmp-bau).
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ordner / "temp"))
    # #349: ``bau.main``/``wache.main`` setzen TMPDIR/TEMP/TMP per ``os.environ.update`` auf
    # diesen Ordner. Ein Test stellt per ``monkeypatch`` oft nur TMPDIR zurück — TEMP/TMP
    # blieben stehen und erbten an spätere Unterprozesse. ``bau.main`` setzt zudem
    # CLAUDE_CODE_FORCE_SESSION_PERSISTENCE (bau.py:1081). Hier merkt sich monkeypatch alle vier
    # (setenv auf den Ist-Wert bzw. setenv+delenv), damit sie nach jedem Test zurückgestellt sind.
    for name in ("TMPDIR", "TEMP", "TMP", "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    # #145: Sicherung (Rundgang, Aufseher-Ablösung) schreibt nur in tmp, lädt keine echten Zweige hoch.
    monkeypatch.setenv("TO_SPAWN_SICHERUNG_ZIEL", str(ordner / "sicherung"))
    monkeypatch.setenv("TO_SPAWN_SICHERUNG_ZWEIG_REPOS", "")

    # #649: Windows-Prozessprüfung des Hausmeisters sieht nie fremde Rechner-Prozesse.
    from to_spawn import hausmeister_windows

    echt_kein_prozess = hausmeister_windows.kein_prozess
    echt_prozesse = hausmeister_windows._prozesse

    def _kein_prozess_im_test(ziel: Path) -> None:
        if hausmeister_windows._POWERSHELL == "powershell" and hausmeister_windows._prozesse is echt_prozesse:
            hausmeister_windows._prozesse = list
            try:
                echt_kein_prozess(ziel)
            finally:
                hausmeister_windows._prozesse = echt_prozesse
            return
        echt_kein_prozess(ziel)

    monkeypatch.setattr(hausmeister_windows, "kein_prozess", _kein_prozess_im_test)

    echt_arbeitsordner = prozessbaum.arbeitsordner_in_windows

    @functools.wraps(echt_arbeitsordner)
    def _arbeitsordner_nur_eigene(ziel: str | Path) -> dict[int, prozessbaum.ArbeitsordnerBefund]:
        echt_momentaufnahme = prozessbaum._momentaufnahme_windows
        ich = os.getpid()

        def _nur_eigene_momentaufnahme() -> dict[int, tuple[int, int]]:
            return _eigene_nachfahren(echt_momentaufnahme(), ich)

        prozessbaum._momentaufnahme_windows = _nur_eigene_momentaufnahme
        try:
            return echt_arbeitsordner(ziel)
        finally:
            prozessbaum._momentaufnahme_windows = echt_momentaufnahme

    monkeypatch.setattr(prozessbaum, "arbeitsordner_in_windows", _arbeitsordner_nur_eigene)


@pytest.fixture(autouse=True)
def _eigenes_tmux(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener, leerer tmux-Server je Test (#479): kein Test liest tmux-Sitzungen des Rechners.

    Ohne diese Fixture fragte ``hausmeister._keine_spec_sitzung`` das echte tmux ab — lief auf
    dem Rechner zufällig ``spec-11``, blieb im Test ein Ordner stehen, der weg sollte. ``$TMUX``
    geht weg (sonst nähme tmux den Server der umgebenden Sitzung), ``$TMUX_TMPDIR`` zeigt in
    einen kurzen Ordner des Testlaufs (Unix-Socket-Pfade sind auf ~108 Zeichen begrenzt). Erbt
    sich in Kind-Prozesse. Tests, die tmux-Verhalten prüfen, setzen eigene Werte darüber.
    """
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path_factory.mktemp("tx")))


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


@pytest.fixture(autouse=True)
def _pruef_cache_im_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Prüf-Cache (#102) liegt je Test unter ``tmp_path``, nie im echten Nutzer-Cache.

    Probe-Repos gleichen Inhalts haben denselben Tree-SHA: ohne eigene Ablage je Test träfe ein
    Test den grünen Eintrag eines anderen (z. B. „pytest fehlt“ würde grün).
    """
    ablage = str(tmp_path / "nutzer-cache")
    monkeypatch.setenv("XDG_CACHE_HOME", ablage)
    monkeypatch.setenv("LOCALAPPDATA", ablage)


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def app_repo_komplett() -> Callable[..., Path]:
    """Fabrik ``(tmp_path, zaehl, mit_staging=False)``: App-Repo mit eigenem Komplett-Skript (#496/#504).

    Das Skript kennt keine Sperre und schreibt jede Ausführung in ``zaehl``.
    """

    def bauen(tmp_path: Path, zaehl: Path, mit_staging: bool = False) -> Path:
        repo = tmp_path / "app"
        (repo / "skripte").mkdir(parents=True)
        (repo / "skripte" / "alles_pruefen.sh").write_text(
            f"#!/usr/bin/env bash\necho lauf >> '{zaehl.as_posix()}'\n", encoding="utf-8"
        )
        (repo / ".to-spawn").mkdir()
        konfig: dict[str, Any] = {
            "staging": {"url": "https://staging.example.test"} if mit_staging else {"ohne": True},
            "pruefung": {"schnell": "true", "komplett": "bash skripte/alles_pruefen.sh"},
        }
        (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
        (repo / ".gitignore").write_text(".to-spawn/pruef/\n", encoding="utf-8")
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "tag", "base")
        return repo

    return bauen


@pytest.fixture
def komplett_laeufe() -> Callable[[Path], int]:
    """``(zaehl) -> Anzahl``: wie oft das Komplett-Skript aus :func:`app_repo_komplett` lief."""

    def zaehlen(zaehl: Path) -> int:
        return len(zaehl.read_text(encoding="utf-8").splitlines()) if zaehl.exists() else 0

    return zaehlen
