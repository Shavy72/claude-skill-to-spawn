"""#560 Weg-Test: nach „wartet auf Platz“ starten die wartenden Fenster ohne Eingriff nach.

1. Rechner fremd voll → ``spawn_srv.sh`` öffnet nur ``wache <S>`` und vermerkt ``bau 901`` in der
   Warte-Datei → solange voll, bleibt alles liegen → Platz wird frei → der Nachstarter aus
   ``wache.py`` startet ``bau 901`` im nächsten Takt (Sekunden), die Warte-Datei ist weg.
2. ``wache.py`` stirbt während des Wartens → das Fenster startet es neu, mit derselben Warte-Datei.

Attrappen nur an der Prozess-Grenze wie in ``test_obergrenze_rechner_460_fix1_spawn``
(``/proc``, meminfo, tmux; in 2. der ``wache.py``-Prozess); ``spawn_srv.sh``, ``speicher``,
``sessions_stand`` und der Nachstarter aus ``wache.py`` sind echt.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest
import test_spec_rand_e2 as e2
from test_obergrenze_rechner_460_fix1_spawn import _spawn

from to_spawn import bash_wahl

SKILL = Path(__file__).resolve().parents[1]


def _wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_560", SKILL / "skripte" / "wache.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _spawn_fremd_voll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    """Fremd voller Rechner (7 fremde + 2 eigene, Grenze 9); liefert Lauf, Umgebung, Repo."""
    mitschnitt: dict = {}
    echt = bash_wahl.lauf

    def lauf(argumente, **optionen):
        mitschnitt.setdefault("env", dict(optionen["env"]))
        mitschnitt.setdefault("cwd", optionen["cwd"])
        return echt(argumente, **optionen)

    monkeypatch.setattr(bash_wahl, "lauf", lauf)
    ergebnis, _ = _spawn(tmp_path, max_sessions=6, belegt=7, belegt_eigen=2)
    return ergebnis, mitschnitt["env"], Path(mitschnitt["cwd"])


def _gestartet(tmp_path: Path) -> str:
    return (tmp_path / "bin" / "tmux_aufrufe.txt").read_text(encoding="utf-8")


@pytest.mark.braucht("bash", "posix")
def test_fremd_voll_platz_frei_wartende_fenster_starten_ohne_eingriff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0 and "wartet auf Platz: bau 901" in ausgabe, ausgabe
    assert "-n bau 901" not in _gestartet(tmp_path)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    assert datei.read_text(encoding="utf-8").strip() == "901"
    assert f"TO_SPAWN_WARTE_DATEI={datei}" in _gestartet(tmp_path)

    wache = _wache()
    assert wache.NACHSTART_TAKT_S <= 30  # Sekunden nach „Platz frei“, nicht Takt-Minuten
    # Noch voll: ein Takt, nichts startet, die Datei bleibt.
    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=1)
    assert laeufe == 1 and datei.is_file()
    assert "-n bau 901" not in _gestartet(tmp_path)

    # Platz wird frei: die fremden Sessions enden.
    proc = Path(umgebung["TO_SPAWN_SPEICHER_PROC"])
    sessions = Path(umgebung["TO_SPAWN_SPEICHER_SESSIONS"])
    for pid in range(201, 208):
        for teil in (proc / str(pid)).iterdir():
            teil.unlink()
        (proc / str(pid)).rmdir()
        (sessions / f"{pid}.json").unlink()
    geschlafen: list[float] = []
    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=geschlafen.append, max_laeufe=3)
    assert laeufe == 1 and not geschlafen, (laeufe, geschlafen)
    assert "-n bau 901" in _gestartet(tmp_path)
    assert not datei.exists()


_WACHE_ABSTURZ = """
import os, signal, sys
from pathlib import Path
zaehler = Path(sys.argv[0]).with_name("laeufe.txt")
n = int(zaehler.read_text()) + 1 if zaehler.exists() else 1
zaehler.write_text(str(n))
Path(sys.argv[0]).with_name(f"warte_{n}.txt").write_text(os.environ.get("TO_SPAWN_WARTE_DATEI", ""))
if n == 1:
    os.kill(os.getpid(), signal.SIGKILL)  # Absturz mitten im Warten
"""


@pytest.mark.braucht("bash", "posix")
def test_wache_stirbt_beim_warten_fenster_startet_sie_neu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lauf, _umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    zeile = next(z for z in _gestartet(tmp_path).splitlines() if f"-n wache {e2.SPEC} " in z)
    befehl = zeile.split(" bash -lc ", 1)[1]
    attrappe = tmp_path / "absturz" / "wache.py"
    attrappe.parent.mkdir()
    attrappe.write_text(_WACHE_ABSTURZ, encoding="utf-8")
    echt = str(SKILL / "skripte" / "wache.py")
    assert echt in befehl, befehl
    fenster = subprocess.run(
        ["bash", "-c", befehl.replace(echt, str(attrappe))],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"},
    )
    assert fenster.returncode == 0, fenster.stderr
    assert (attrappe.parent / "laeufe.txt").read_text() == "2"
    assert "Neustart 1/3" in fenster.stderr, fenster.stderr
    datei = str(repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}")
    assert (attrappe.parent / "warte_2.txt").read_text() == datei


# --- Prüfer-Funde (#560 Fixrunde 1) ------------------------------------------------


def _platz_frei(umgebung: dict) -> None:
    """Die 7 fremden Sessions aus ``_spawn_fremd_voll`` enden — der Rechner hat wieder Platz."""
    proc = Path(umgebung["TO_SPAWN_SPEICHER_PROC"])
    sessions = Path(umgebung["TO_SPAWN_SPEICHER_SESSIONS"])
    for pid in range(201, 208):
        for teil in (proc / str(pid)).iterdir():
            teil.unlink()
        (proc / str(pid)).rmdir()
        (sessions / f"{pid}.json").unlink()


@pytest.mark.braucht("bash", "posix")
def test_warte_datei_erst_nach_wache_start_wird_trotzdem_nachgestartet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pflicht 1: ``spawn_srv.sh`` schreibt die Datei erst nach der Staffel-Pause, die Wache läuft da schon."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    inhalt = datei.read_text(encoding="utf-8")
    datei.unlink()  # Stand beim Start der Wache: noch keine Warte-Datei
    _platz_frei(umgebung)
    wache = _wache()
    geschlafen: list[float] = []

    def schlafen(sekunden: float) -> None:
        geschlafen.append(sekunden)
        if len(geschlafen) == 1:
            datei.write_text(inhalt, encoding="utf-8")  # erscheint einen Takt später

    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=schlafen, max_laeufe=2)
    assert laeufe == 1 and geschlafen == [wache.NACHSTART_TAKT_S], (laeufe, geschlafen)
    assert "-n bau 901" in _gestartet(tmp_path)
    assert not datei.exists()


@pytest.mark.braucht("bash", "posix")
def test_nachstarter_faden_aus_wache_start_startet_wartende(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 3: echter Faden über ``_nachstarter_starten`` (wie ``main``), Datei kommt später."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    datei.unlink()
    _platz_frei(umgebung)
    wache = _wache()
    monkeypatch.setattr(wache, "NACHSTART_TAKT_S", 0.05)
    for schluessel, wert in umgebung.items():
        monkeypatch.setenv(schluessel, wert)
    monkeypatch.setenv(wache.WARTE_ENV, str(datei))

    faden = wache._nachstarter_starten(e2.SPEC, repo)
    assert faden is not None
    time.sleep(0.5)
    assert faden.is_alive(), "Faden endete ohne Warte-Datei — der Rest bliebe liegen (#560)"
    datei.write_text("901\n", encoding="utf-8")
    frist = time.monotonic() + 90
    while datei.exists() and time.monotonic() < frist:
        time.sleep(0.1)
    assert not datei.exists() and "-n bau 901" in _gestartet(tmp_path)
    assert faden.is_alive(), "Faden muss für spätere Warte-Dateien weiterlaufen"

    # main startet genau diesen Faden — nach dem Warten auf Platz, vor dem Claude-Start.
    quelle = inspect.getsource(wache.main)
    stelle = quelle.index("_nachstarter_starten(a.spec, REPO_ORDNER)")
    assert quelle.index("auf_speicher_warten(konfig") < stelle < quelle.index("vertrauen.still_sicherstellen")


@pytest.mark.braucht("bash", "posix")
def test_drei_fehlversuche_nachstarter_bleibt_dran(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 4: ``spawn_srv.sh`` scheitert 3-mal → Datei bleibt, nächster Takt versucht weiter."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    _platz_frei(umgebung)
    manifest = next(repo.rglob(f"spec-{e2.SPEC}.json"))
    weg = manifest.with_name("weg.json")
    manifest.rename(weg)  # spawn_srv.sh: „Manifest fehlt“, Exit 2
    wache = _wache()
    geschlafen: list[float] = []
    laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=geschlafen.append, max_laeufe=3)
    assert laeufe == 3 and len(geschlafen) == 3
    assert datei.read_text(encoding="utf-8").strip() == "901"
    assert "-n bau 901" not in _gestartet(tmp_path)
    weg.rename(manifest)  # Fehler behoben → der nächste Takt startet nach
    assert wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=geschlafen.append) == 1
    assert "-n bau 901" in _gestartet(tmp_path) and not datei.exists()


_WACHE_PLAN = """
import os, signal, sys
from pathlib import Path
zaehler = Path(sys.argv[0]).with_name("laeufe.txt")
n = int(zaehler.read_text()) + 1 if zaehler.exists() else 1
zaehler.write_text(str(n))
plan = os.environ["WACHE_PLAN"].split(",")
code = plan[min(n, len(plan)) - 1]
if code == "kill":
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(int(code))
"""


@pytest.mark.braucht("bash", "posix")
@pytest.mark.parametrize(
    ("plan", "laeufe"),
    [("130", 1), ("2", 1), ("143", 1), ("0", 1), ("1,0", 2), ("kill", 4)],
    ids=["strg-c", "konfig-fehler", "sigterm", "normal", "traceback-dann-ok", "absturz-immer"],
)
def test_wache_neustart_nur_nach_absturz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan: str, laeufe: int) -> None:
    """Pflicht 2: gewollter Abbruch → kein Neustart; Absturz → Neustart, höchstens 3-mal."""
    lauf, _umgebung, _repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    zeile = next(z for z in _gestartet(tmp_path).splitlines() if f"-n wache {e2.SPEC} " in z)
    befehl = zeile.split(" bash -lc ", 1)[1]
    attrappe = tmp_path / "plan" / "wache.py"
    attrappe.parent.mkdir()
    attrappe.write_text(_WACHE_PLAN, encoding="utf-8")
    echt = str(SKILL / "skripte" / "wache.py")
    fenster = subprocess.run(
        ["bash", "-c", befehl.replace(echt, str(attrappe))],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "WACHE_PLAN": plan,
            "TO_SPAWN_WACHE_NEUSTART_S": "0",
        },
    )
    assert (attrappe.parent / "laeufe.txt").read_text() == str(laeufe), fenster.stderr
    assert fenster.stderr.count("Neustart") == laeufe - 1, fenster.stderr


def _sperre(repo: Path) -> Path:
    return repo / ".to-spawn" / f"spawn-{e2.SPEC}.sperre"


@pytest.mark.braucht("bash", "posix")
def test_paralleler_spawn_lauf_startet_nichts_doppelt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 5: hält ein anderer ``spawn_srv.sh``-Lauf (z. B. der Nachstarter) die Sperre, startet dieser nichts."""
    import fcntl

    echt = bash_wahl.lauf

    def lauf(argumente, **optionen):
        sperre = _sperre(Path(optionen["cwd"]))
        sperre.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(sperre, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)  # der andere Lauf ist mitten im Prüfen-und-Starten
        try:
            return echt(argumente, **dict(optionen, env=dict(optionen["env"], TO_SPAWN_SPERRE_WARTEN_S="1")))
        finally:
            os.close(fd)

    monkeypatch.setattr(bash_wahl, "lauf", lauf)
    ergebnis, aufrufe = _spawn(tmp_path, max_sessions=6)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 10 and "anderer spawn_srv.sh-Lauf" in ausgabe, ausgabe
    assert "bau 901" not in aufrufe and "wache" not in aufrufe, aufrufe


_TMUX_FD_ZEUGE = """#!/usr/bin/env bash
case "$1" in new-session|new-window) [ -e /proc/$$/fd/9 ] && echo "fd9 offen: $*" >> {beleg!r} ;; esac
exec {echt!r} "$@"
"""


@pytest.mark.braucht("bash", "posix")
def test_sperre_vererbt_sich_nicht_an_fenster(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 5: tmux-Fenster laufen lange — sie dürfen die Sperre nie mitnehmen, sonst hängt jeder Folgelauf."""
    echt = bash_wahl.lauf
    zeuge = tmp_path / "zeuge"
    beleg = tmp_path / "fd9.txt"

    def lauf(argumente, **optionen):
        if not zeuge.exists():
            zeuge.mkdir()
            ziel = tmp_path / "bin" / "tmux"
            (zeuge / "tmux").write_text(_TMUX_FD_ZEUGE.format(beleg=str(beleg), echt=str(ziel)), encoding="utf-8")
            (zeuge / "tmux").chmod(0o755)
        env = dict(optionen["env"], PATH=f"{zeuge}{os.pathsep}{optionen['env']['PATH']}")
        return echt(argumente, **dict(optionen, env=env))

    monkeypatch.setattr(bash_wahl, "lauf", lauf)
    ergebnis, aufrufe = _spawn(tmp_path, max_sessions=6)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "-n bau 901" in aufrufe
    assert not beleg.exists(), beleg.read_text(encoding="utf-8")


# --- Prüfer-Funde (#560 Fixrunde 2) ------------------------------------------------


@pytest.mark.braucht("bash", "posix")
@pytest.mark.parametrize("wann", ["waehrend", "danach"])
def test_neue_warteliste_waehrend_nachstart_geht_nicht_verloren(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wann: str
) -> None:
    """Pflicht 1: ein zweiter ``spawn_srv.sh``-Lauf vermerkt L2, während der Nachstarter mit L1 läuft.

    Nach Exit 0 muss L2 stehen bleiben und im nächsten Takt nachstarten — nicht erst beim Aufpasser.
    """
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    _platz_frei(umgebung)
    echt = bash_wahl.lauf
    l2 = str(e2.CHECKPOINT)

    def lauf(argumente, **optionen):
        if wann == "waehrend" and l2 not in argumente:
            datei.write_text(f"{l2}\n", encoding="utf-8")
        ergebnis = echt(argumente, **optionen)
        if wann == "danach" and l2 not in argumente:
            datei.write_text(f"{l2}\n", encoding="utf-8")
        return ergebnis

    monkeypatch.setattr(bash_wahl, "lauf", lauf)
    zwischenstand: list[str] = []
    wache = _wache()
    laeufe = wache.wartende_nachstarten(
        datei,
        e2.SPEC,
        repo,
        env=umgebung,
        schlafen=lambda _s: zwischenstand.append(datei.read_text(encoding="utf-8") if datei.exists() else "weg"),
        max_laeufe=3,
    )
    assert zwischenstand == [f"{l2}\n"], zwischenstand
    assert laeufe == 2
    assert "-n bau 901" in _gestartet(tmp_path) and f"-n bau {l2}" in _gestartet(tmp_path)
    assert not datei.exists()


@pytest.mark.braucht("bash", "posix")
def test_leere_warte_datei_ist_kein_auftrag_zum_loeschen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 2: leerer Inhalt (Leser mitten im Schreiben) → kein Lauf, kein Löschen, nächster Takt."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    datei.write_text("", encoding="utf-8")
    _platz_frei(umgebung)
    da: list[bool] = []

    def schlafen(_s: float) -> None:
        da.append(datei.exists())
        datei.write_text("901\n", encoding="utf-8")  # Schreiber fertig

    laeufe = _wache().wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=schlafen, max_laeufe=1)
    assert da == [True] and laeufe == 1, (da, laeufe)
    assert "-n bau 901" in _gestartet(tmp_path) and not datei.exists()


@pytest.mark.braucht("bash", "posix")
def test_warte_datei_wird_in_einem_zug_ersetzt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht 2: ``spawn_srv.sh`` ersetzt die Datei per ``mv`` — wer sie offen hat, sieht nie leeren Inhalt."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    alt = datei.stat().st_ino
    with datei.open(encoding="utf-8") as leser:
        erneut = bash_wahl.lauf(
            [str(SKILL / "skripte" / "spawn_srv.sh"), str(e2.SPEC), "--tickets", "901"],
            cwd=str(repo),
            env=umgebung,
            capture_output=True,
            text=True,
        )
        assert erneut.returncode == 0 and "wartet auf Platz" in erneut.stdout, erneut.stdout + erneut.stderr
        assert leser.read() == "901\n"  # alte Fassung bleibt für den Leser ganz
    assert datei.stat().st_ino != alt and datei.read_text(encoding="utf-8") == "901\n"
    assert not list(datei.parent.glob("*.neu"))
