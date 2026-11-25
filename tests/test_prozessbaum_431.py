"""#431 Fixrunde 4, Befunde 1+2: ein gemeinsames Prozessbaum-Modul.

respawn und aufpasser beendeten den Prozessbaum eines tmux-Panes mit je eigenem
Gerüst. ``to_spawn.prozessbaum`` hält dieses Wissen jetzt an genau einer Stelle;
die Tests laufen gegen echte Kindprozesse, nicht gegen Attrappen.
"""

from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL))

from to_spawn import aufpasser, prozessbaum

WINDOWS = sys.platform == "win32"
#: SIGKILL gibt es unter Windows nicht; dort beendet jedes Signal per ``TerminateProcess`` hart.
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
#: Interpreter für echte Prozessbäume. Unter Windows ist das ``python.exe`` einer venv nur ein
#: Starter, der das Basis-Python als zweiten Prozess startet: aus „Eltern + Kind“ würden vier
#: Prozesse, die erst nach und nach erscheinen — Tests sähen halbe Bäume und ließen beim
#: Aufräumen die späten Enkel stehen. Das Basis-Python startet ohne Zwischenprozess.
PYTHON = getattr(sys, "_base_executable", sys.executable) if WINDOWS else sys.executable


def _starten(befehl: list[str]) -> subprocess.Popen[bytes]:
    """Startet ``befehl`` und räumt ihn im Hintergrund ab — wie tmux es mit dem Pane
    tut. Sonst bliebe der beendete Prozess als Zombie in ``/proc`` und „lebte“."""
    proz = subprocess.Popen(befehl)
    threading.Thread(target=proz.wait, daemon=True).start()
    return proz


Starter = Callable[[list[str]], "subprocess.Popen[bytes]"]


def _familie(
    liste: dict[int, tuple[int, str, int]], familie: dict[int, int]
) -> tuple[dict[int, int], list[int], list[int]]:
    """Familie fortschreiben; Ergebnis: (Familie, zu beenden, nicht beendbar wegen Startzeit 0).

    ``familie`` merkt je Mitglied PID → Startzeit. Beendet wird nur, wer mit genau dieser
    Startzeit noch läuft — eine schon tote Familien-PID, die Windows neu vergab, gehört einem
    Fremden. Ein Kind zählt nur, wenn es nicht vor seinem Elternteil startete und die
    Eltern-PID nicht inzwischen einem Fremden gehört (sonst wären dessen Kinder „unsere“).
    Unbekannte Startzeit (0) heißt: nie beenden, sondern melden.
    """
    familie = dict(familie)

    def echt(pid: int) -> bool:
        return familie[pid] != 0 and liste[pid][2] == familie[pid]

    unklar = {pid for pid, beginn in familie.items() if beginn == 0}
    gewachsen = True
    while gewachsen:
        gewachsen = False
        for pid, (eltern, _, beginn) in liste.items():
            if pid in familie or eltern not in familie or not familie[eltern]:
                continue
            if eltern in liste and not echt(eltern):
                continue  # Eltern-PID gehört jetzt einem Fremden
            if not beginn:
                unklar.add(pid)
            elif beginn >= familie[eltern]:
                familie[pid] = beginn
                gewachsen = True
    lebende = [pid for pid in familie if pid in liste and echt(pid)]
    return familie, lebende, sorted(pid for pid in unklar if pid in liste)


def test_familie_verschont_recycelte_und_unbekannte_pids() -> None:
    """Abräumen trifft nur Prozesse mit gemerkter Startzeit; Fremde und Unklare bleiben stehen."""
    liste = {
        10: (1, "", 100),  # Wurzel, lebt
        11: (10, "", 150),  # echtes Kind
        12: (99, "", 300),  # Waise: Elternteil 12er-Familie 99 tot, später gestartet
        20: (1, "", 900),  # tote Familien-PID 20 neu vergeben an einen Fremden
        21: (20, "", 950),  # Kind des Fremden
        13: (10, "", 0),  # Kind ohne lesbare Startzeit
        14: (10, "", 50),  # älter als die Wurzel: recycelte Eltern-PID
    }
    familie, lebende, unklar = _familie(liste, {10: 100, 99: 120, 20: 130})
    assert sorted(lebende) == [10, 11, 12]
    assert unklar == [13]
    assert 21 not in familie and 14 not in familie
    # Wurzel ohne Startzeit: nie beenden, klar melden.
    _, lebende, unklar = _familie({30: (1, "", 0)}, {30: 0})
    assert lebende == [] and unklar == [30]


def _abraeumen(wurzel: int, start: int, frist: float = 5.0) -> tuple[list[int], list[int]]:
    """Beendet ``wurzel`` samt aller Nachfahren hart; Ergebnis: (lebt nach ``frist`` noch,
    blieb wegen unbekannter Startzeit unangetastet).

    Folgt den Eltern-PIDs aus ``prozessbaum.prozesse`` auch über schon tote Glieder: unter
    Windows ist Git-Bash-``sh.exe`` nur ein Starter für ``bash.exe``, und MSYS-Forks überleben
    ihren beendeten Elternteil. Fremde mit recycelter PID schützt ``_familie``.
    """
    # Eigener Lauf statt ``prozessbaum.baum``: ``baum`` lässt Waisen toter Eltern bewusst aus,
    # genau die müssen hier mit weg.
    familie = {wurzel: start}
    ende = time.monotonic() + frist
    while True:
        familie, lebende, unklar = _familie(prozessbaum.prozesse(), familie)
        if not lebende or time.monotonic() > ende:
            return lebende, unklar
        prozessbaum.senden(lebende, SIGKILL)  # Wurzel zuerst: danach entstehen keine neuen Forks
        time.sleep(0.05)


@pytest.fixture
def starten() -> Iterator[Starter]:
    """Startet Prozesse wie ``_starten`` und räumt ihren ganzen Baum danach hart ab — auch,
    wenn eine Zusicherung im Test fehlschlägt. Bleibt etwas übrig, wird der Test rot."""
    wurzeln: list[tuple[int, int]] = []

    def _start(befehl: list[str]) -> subprocess.Popen[bytes]:
        proz = _starten(befehl)
        wurzeln.append((proz.pid, prozessbaum.prozesse().get(proz.pid, (0, "", 0))[2]))
        return proz

    try:
        yield _start
    finally:
        ergebnisse = [_abraeumen(wurzel, start) for wurzel, start in wurzeln]
        reste = [pid for lebende, _ in ergebnisse for pid in lebende]
        unklar = [pid for _, offen in ergebnisse for pid in offen]
        assert not unklar, f"Startzeit unbekannt, aus Vorsicht nicht beendet: {unklar}"
        assert not reste, f"Kindprozesse überlebten das Abräumen: {reste}"


def _bereit_warten(marker: Path) -> None:
    """Wartet (max. 5 s), bis die Shell den Bereitschafts-Marker geschrieben hat."""
    ende = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < ende:
        time.sleep(0.02)
    assert marker.exists(), "Shell wurde nicht bereit"


@pytest.fixture
def eltern_mit_kind(starten: Starter) -> Iterator[subprocess.Popen[bytes]]:
    """Ein Elternprozess, der selbst einen schlafenden Prozess als Kind startet und wartet.

    Python statt ``sh -c "sleep 30 & wait"``: unter Windows hängen die Kinder einer
    Git-Bash-Shell (MSYS-``fork``) nicht am Windows-Elternteil, der Baum wäre dort nur die
    Shell. Mit Python entsteht auf beiden Systemen ein echter Baum aus zwei Prozessen.
    """
    kind = "import time; time.sleep(30)"
    eltern = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {kind!r}]).wait()"
    proz = starten([PYTHON, "-c", eltern])
    ende = time.monotonic() + 5
    while len(prozessbaum.baum(proz.pid)) < 2 and time.monotonic() < ende:
        time.sleep(0.05)
    yield proz


def test_baum_enthaelt_wurzel_und_kind(
    eltern_mit_kind: subprocess.Popen[bytes],
) -> None:
    baum = prozessbaum.baum(eltern_mit_kind.pid)
    assert baum[0] == eltern_mit_kind.pid
    assert len(baum) == 2


def test_lebt_echt_und_tot(starten: Starter) -> None:
    proz = starten(["sleep", "30"])
    assert prozessbaum.lebt(proz.pid)
    proz.kill()
    proz.wait(timeout=5)
    assert not prozessbaum.lebt(proz.pid)


def test_beenden_term_reicht(eltern_mit_kind: subprocess.Popen[bytes]) -> None:
    baum = prozessbaum.baum(eltern_mit_kind.pid)
    kind = baum[1]
    stufen = ((signal.SIGTERM, 5.0), (SIGKILL, 5.0))
    eskaliert: list[int] = []
    assert prozessbaum.beenden(list(reversed(baum)), stufen, eskalation=lambda sig, _: eskaliert.append(sig))
    eltern_mit_kind.wait(timeout=5)
    assert not prozessbaum.lebt(kind)
    assert eskaliert == []


@pytest.mark.skipif(WINDOWS, reason="Windows: SIGTERM ist TerminateProcess und lässt sich nicht ignorieren")
def test_beenden_eskaliert_zu_kill(tmp_path: Path, starten: Starter) -> None:
    """Ein Prozess, der SIGTERM ignoriert, stirbt erst an der nächsten Stufe."""
    marker = tmp_path / "bereit"
    proz = starten(
        [
            "sh",
            "-c",
            f"trap '' TERM; : > {marker.as_posix()}; while :; do sleep 0.1; done",
        ]
    )
    eskaliert: list[int] = []
    try:
        _bereit_warten(marker)
        assert prozessbaum.beenden(
            [proz.pid],
            ((signal.SIGTERM, 0.5), (SIGKILL, 5.0)),
            eskalation=lambda sig, lebende: eskaliert.append(sig),
        )
        assert eskaliert == [signal.SIGKILL]
    finally:
        proz.kill()
        proz.wait(timeout=5)


def test_beenden_wartet_nur_auf_ziel(tmp_path: Path, starten: Starter) -> None:
    """``warten_auf=[]``: Signal geht raus, gewartet wird nicht."""
    marker = tmp_path / "bereit"
    proz = starten(
        [
            "sh",
            "-c",
            f"trap '' TERM; : > {marker.as_posix()}; while :; do sleep 0.1; done",
        ]
    )
    try:
        _bereit_warten(marker)
        start = time.monotonic()
        assert prozessbaum.beenden([proz.pid], ((signal.SIGTERM, 30.0),), warten_auf=[])
        assert time.monotonic() - start < 2
    finally:
        proz.kill()
        proz.wait(timeout=5)


@pytest.mark.skipif(WINDOWS, reason="/proc gibt es nur unter Linux; Windows-Fall siehe unten")
def test_baum_proc_unlesbar_ist_fehler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ein nicht lesbares ``/proc`` heißt Fehler, nicht „keine Kinder“."""
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "fehlt")
    with pytest.raises(OSError):
        prozessbaum.baum(1)


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
def test_baum_windows_momentaufnahme_fehlt_ist_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: liefert der Kernel keine Prozessliste, heißt das Fehler, nicht „keine Kinder“."""
    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", dict)
    with pytest.raises(OSError):
        prozessbaum.baum(os.getpid())


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
def test_beenden_windows_erste_stufe_beendet_hart(tmp_path: Path, starten: Starter) -> None:
    """Windows: auch eine Shell, die SIGTERM ignorieren will, endet schon in der ersten Stufe."""
    marker = tmp_path / "bereit"
    proz = starten(["sh", "-c", f"trap '' TERM; : > {marker.as_posix()}; while :; do sleep 0.1; done"])
    eskaliert: list[int] = []
    try:
        _bereit_warten(marker)
        assert prozessbaum.beenden(
            [proz.pid], ((signal.SIGTERM, 5.0),), eskalation=lambda sig, _: eskaliert.append(sig)
        )
        assert eskaliert == []
        assert not prozessbaum.lebt(proz.pid)
    finally:
        proz.kill()
        proz.wait(timeout=5)


def _liste(monkeypatch: pytest.MonkeyPatch, eltern: dict[int, int], starts: dict[int, int]) -> None:
    """Kernel-Prozessliste als Attrappe: PID → (Eltern-PID, Startzeit; 0 = unbekannt) (#154)."""
    liste = {pid: (vater, starts.get(pid, 0)) for pid, vater in eltern.items()}
    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", lambda: liste)


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
def test_baum_windows_ohne_fremde_mit_recycelter_eltern_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: ein Prozess, der vor seinem angeblichen Elternteil startete, gehört nicht zum Baum."""
    eltern = {1: 0, 10: 1, 11: 10, 12: 10, 13: 12}
    starts = {1: 1, 10: 100, 11: 150, 12: 50, 13: 200}  # 12 ist älter als „Eltern“ 10
    _liste(monkeypatch, eltern, starts)
    assert prozessbaum.baum(10) == [10, 11]


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
def test_baum_windows_unbekannte_startzeit_zaehlt_als_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: ohne lesbare Startzeit (kein Zugriff) zählt die Eltern-PID als echt."""
    starts = {1: 1, 10: 100, 12: 50}  # 11 ohne Startzeit (0), 12 älter als „Eltern“ 10
    _liste(monkeypatch, {1: 1, 10: 1, 11: 10, 12: 10}, starts)
    assert prozessbaum.baum(10) == [10, 11]


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
def test_baum_windows_waisen_toter_eltern_pid_fehlen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: fehlt die Eltern-PID in der Momentaufnahme (Elternteil tot), hängt kein Waise daran."""
    _liste(monkeypatch, {10: 1, 14: 99, 15: 14}, {})
    assert prozessbaum.baum(99) == [99]
    assert prozessbaum.baum(14) == [14, 15]


@pytest.mark.skipif(not WINDOWS, reason="nur Windows")
@pytest.mark.parametrize("noch_lebend", [True, False])
def test_senden_windows_oserror_wirft_nicht(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, noch_lebend: bool
) -> None:
    """Windows: ``OSError`` beim Signal wirft nie; geloggt wird nur, wenn der Prozess noch lebt."""

    def kill_scheitert(_pid: int, _sig: int) -> None:
        raise OSError(87, "Falscher Parameter")

    monkeypatch.setattr(prozessbaum.os, "kill", kill_scheitert)
    monkeypatch.setattr(prozessbaum, "lebt", lambda _pid: noch_lebend)
    with caplog.at_level("WARNING", logger=prozessbaum.log.name):
        prozessbaum.senden([4711, 4712], signal.SIGTERM)
    warnungen = [r for r in caplog.records if "fehlgeschlagen" in r.getMessage()]
    assert len(warnungen) == (2 if noch_lebend else 0)
    if noch_lebend:
        assert "4711" in warnungen[0].getMessage()


def _importe(datei: Path) -> set[str]:
    namen: set[str] = set()
    for knoten in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
        if isinstance(knoten, ast.ImportFrom):
            namen.update(a.name for a in knoten.names)
            namen.add(knoten.module or "")
        elif isinstance(knoten, ast.Import):
            namen.update(a.name for a in knoten.names)
    return namen


@pytest.mark.parametrize("modul", ["respawn.py", "aufpasser.py"])
def test_respawn_und_aufpasser_nutzen_prozessbaum(modul: str) -> None:
    assert "prozessbaum" in _importe(WURZEL / "to_spawn" / modul)


def test_respawn_unter_1000_zeilen() -> None:
    zeilen = (WURZEL / "to_spawn" / "respawn.py").read_text(encoding="utf-8").count("\n")
    assert zeilen < 1000, f"respawn.py hat {zeilen} Zeilen"


def test_kein_zweites_geruest_in_respawn() -> None:
    """Befund 2: respawn hat kein eigenes Prozessbaum-Gerüst mehr."""
    quelle = (WURZEL / "to_spawn" / "respawn.py").read_text(encoding="utf-8")
    for alt in ("def _nachkommen(", "def _lebt(", "def _signal(", "def _ist_claude("):
        assert alt not in quelle, alt


def test_aufpasser_beenden_toetet_ganzen_baum(
    eltern_mit_kind: subprocess.Popen[bytes],
) -> None:
    """``Aufpasser._prozesse_beenden`` räumt gegen einen echten Prozessbaum ab."""
    baum = prozessbaum.baum(eltern_mit_kind.pid)
    assert len(baum) == 2
    aufpasser.Aufpasser._prozesse_beenden(object(), eltern_mit_kind.pid)  # type: ignore[arg-type]
    eltern_mit_kind.wait(timeout=5)
    assert not any(prozessbaum.lebt(pid) for pid in baum)


def test_aufpasser_beenden_stufen_und_reihenfolge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stufen HUP -> TERM -> KILL je 5 s, Kinder vor der Wurzel (reversed)."""
    aufrufe: list[tuple[object, ...]] = []
    monkeypatch.setattr(aufpasser, "prozess_baum", lambda pid: [pid, pid + 1, pid + 2])
    monkeypatch.setattr(
        prozessbaum,
        "beenden",
        lambda *a, **kw: aufrufe.append(a) or True,
    )
    aufpasser.Aufpasser._prozesse_beenden(object(), 100)  # type: ignore[arg-type]
    # Windows kennt weder SIGHUP noch SIGKILL: dort bleibt nur die SIGTERM-Stufe.
    stufen = (
        ((signal.SIGTERM, 5.0),) if WINDOWS else ((signal.SIGHUP, 5.0), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0))
    )
    assert aufrufe == [([102, 101, 100], stufen)]
