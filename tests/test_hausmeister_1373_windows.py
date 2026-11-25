"""Hausmeister #1373: dieselben Regeln für Laptop-Ordner (``C:/dev/wt-*``).

Der Windows-Zweig läuft hier auf Linux über die Plattform-Naht
(``hausmeister._PLATTFORM``). PowerShell ist eine Attrappe, die eine echte
``Get-CimInstance``-Aufzeichnung ausgibt (Liste, Einzelobjekt, ``CommandLine`` null);
die Umbenennungs-Verweigerung von Windows wird über ``hausmeister._umbenennen``
nachgestellt. Git-Ordner, Remote und Löschen sind echt (Welt aus #1368).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import errno
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import attrappe
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    _klon_mit_nebenzweig_commit,
    git,
    welt,
)

from windows_attrappen import windows_starter

from to_spawn import hausmeister, hausmeister_windows, prozessbaum

# Aufzeichnung: Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine,
# ExecutablePath | ConvertTo-Json -Compress (gekürzt; System-Prozesse ohne Pfad).
AUFZEICHNUNG_LEER = [
    {"ProcessId": 0, "CommandLine": None, "ExecutablePath": None},
    {"ProcessId": 4, "CommandLine": None, "ExecutablePath": None},
    {
        "ProcessId": 6120,
        "CommandLine": "C:\\WINDOWS\\Explorer.EXE",
        "ExecutablePath": "C:\\WINDOWS\\Explorer.EXE",
    },
    {
        "ProcessId": 9932,
        "CommandLine": '"C:\\Program Files\\Git\\cmd\\git.exe" fetch',
        "ExecutablePath": "C:\\Program Files\\Git\\cmd\\git.exe",
    },
]

# Python statt ``sh``: unter Windows startet die Attrappe als ``.exe`` (``windows_starter``, #68).
PS_ATTRAPPE = """
import os, sys
with open(os.environ["PS_AUFZEICHNUNG"], encoding="utf-8") as datei:
    sys.stdout.write(datei.read())
"""


def _win(pfad: Path) -> str:
    """Pfad so, wie Windows ihn in CommandLine zeigt: Backslash, andere Schreibweise."""
    return str(pfad).replace("/", "\\").upper()


@pytest.fixture
def win(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "win32")
    ps = welt.tmp / "bin" / "powershell"
    attrappe(ps.parent, ps.name, PS_ATTRAPPE)
    monkeypatch.setattr(hausmeister_windows, "_POWERSHELL", str(windows_starter(ps)))
    aufzeichnung = welt.tmp / "ps.json"
    monkeypatch.setenv("PS_AUFZEICHNUNG", str(aufzeichnung))
    _prozesse(welt, AUFZEICHNUNG_LEER)
    # Unter Windows sieht die PEB-Prüfung sonst jeden echten Prozess des Runners
    # (z. B. parallele pytest-Worker mit wechselnder cwd oder ohne cwd-Griff, #649);
    # die ``win``-Tests stellen die Prozessliste über die PowerShell-Attrappe nach
    # (echte PEB-Prozesse prüft ``test_echt_windows_unterprozess_mit_cwd_im_ordner_bleibt``).
    monkeypatch.setattr(prozessbaum, "arbeitsordner_in_windows", lambda _ordner: {})
    # Laptop nach dem Einrichten: leere Altbestand-Liste (ohne Datei hielte der erste
    # echte Lauf alles fest, siehe test_win_ohne_altbestand_datei_haelt_erst_alles_fest).
    altbestand = hausmeister.altbestand_pfad()
    altbestand.parent.mkdir(parents=True, exist_ok=True)
    altbestand.write_text('{"freigegeben": false, "ordner": []}\n', encoding="utf-8")
    return welt


def _prozesse(welt: Welt, daten: object) -> None:
    (welt.tmp / "ps.json").write_text(json.dumps(daten, separators=(",", ":")), encoding="utf-8")


# --- Kern-Fälle im Windows-Zweig ------------------------------------------------


def test_win_sauber_wird_geloescht(win: Welt) -> None:
    wt = win.worktree(31)
    win.ticket(31, "CLOSED", 48)
    assert win.aufraeumen(wt).art == "gelöscht"
    r = win.aufraeumen(wt, loeschen=True)
    assert (r.art, r.trocken) == ("gelöscht", False)
    assert not wt.exists()


def test_win_geaendert_gesichert_und_geloescht(win: Welt) -> None:
    wt = win.worktree(32, schmutzig=True)
    win.ticket(32, "CLOSED", 48)
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not wt.exists()
    assert "geaendert.txt" in win.im_bare("ls-tree", "-r", "--name-only", "sicherung/32")


def test_win_ticket_offen_bleibt(win: Welt) -> None:
    wt = win.worktree(33)
    win.ticket(33, "OPEN")
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "offen" in r.grund
    assert wt.is_dir()


def test_win_unter_24h_bleibt(win: Welt) -> None:
    wt = win.worktree(34)
    win.ticket(34, "CLOSED", 2)
    assert win.aufraeumen(wt, loeschen=True).art == "bleibt"
    assert wt.is_dir()


def test_win_b16_nebenzweig_nie_ohne_sicherung(win: Welt) -> None:
    klon = _klon_mit_nebenzweig_commit(win, 1373)
    commit = git(klon, "rev-parse", "ticket/1373-probelauf").strip()
    win.ticket(1373, "CLOSED", 48)
    r = win.aufraeumen(klon, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not klon.exists()
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "sicherung/1373"],
        cwd=str(win.bare),
        check=True,
    )


# --- Prozess im Ordner (CIM-Liste + Umbenennungs-Probe) -------------------------


def test_win_prozess_commandline_im_ordner_bleibt(win: Welt) -> None:
    wt = win.worktree(35)
    win.ticket(35, "CLOSED", 48)
    _prozesse(
        win,
        [
            *AUFZEICHNUNG_LEER,
            {
                "ProcessId": 7711,
                "CommandLine": f'"C:\\Python313\\python.exe" {_win(wt)}\\unter\\x.py',
                "ExecutablePath": "C:\\Python313\\python.exe",
            },
        ],
    )
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "7711" in r.grund and not r.fehler
    assert wt.is_dir()


def test_win_prozess_exe_im_ordner_einzelobjekt_bleibt(win: Welt) -> None:
    """Bei nur einem Prozess liefert ConvertTo-Json ein Objekt statt einer Liste."""
    wt = win.worktree(36)
    win.ticket(36, "CLOSED", 48)
    _prozesse(
        win,
        {
            "ProcessId": 8001,
            "CommandLine": None,
            "ExecutablePath": f"{_win(wt)}\\a.exe",
        },
    )
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "8001" in r.grund
    assert wt.is_dir()


def test_win_nachbarordner_mit_laengerem_namen_zaehlt_nicht(win: Welt) -> None:
    wt = win.worktree(37)
    win.ticket(37, "CLOSED", 48)
    _prozesse(
        win,
        [
            {
                "ProcessId": 9,
                "CommandLine": f"code {_win(wt)}0\\x",
                "ExecutablePath": None,
            }
        ],
    )
    assert win.aufraeumen(wt).art == "gelöscht"


def test_win_umbenennen_verweigert_bleibt(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(38)
    win.ticket(38, "CLOSED", 48)

    def verweigert(alt: str | Path, neu: str | Path) -> None:
        raise PermissionError(32, "Der Prozess kann nicht auf die Datei zugreifen")

    monkeypatch.setattr(hausmeister_windows, "_umbenennen", verweigert)
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and not r.fehler and "in Benutzung" in r.grund
    assert wt.is_dir()


def test_win_rueckbenennen_scheitert_bleibt_laut(
    win: Welt, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    wt = win.worktree(39)
    win.ticket(39, "CLOSED", 48)
    echt = os.rename
    aufrufe: list[str] = []

    def erst_ja_dann_nein(alt: str | Path, neu: str | Path) -> None:
        aufrufe.append(str(neu))
        if len(aufrufe) == 1:
            echt(alt, neu)
            return
        raise PermissionError(5, "Zugriff verweigert")

    monkeypatch.setattr(hausmeister_windows, "_umbenennen", erst_ja_dann_nein)
    with caplog.at_level(logging.ERROR, logger=hausmeister.log.name):
        r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler
    probe = wt.with_name(wt.name + ".hausmeister-probe")
    assert probe.is_dir()  # nichts gelöscht, liegt unter dem Probe-Namen
    assert any(rec.levelno == logging.ERROR and str(probe) in rec.getMessage() for rec in caplog.records)


def test_win_probeziel_existiert_bleibt(win: Welt) -> None:
    wt = win.worktree(40)
    win.ticket(40, "CLOSED", 48)
    wt.with_name(wt.name + ".hausmeister-probe").mkdir()
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "Probe" in r.grund
    assert wt.is_dir()


@pytest.mark.parametrize("ausgabe", ["", "{kaputt", '"nur text"'])
def test_win_powershell_ausgabe_kaputt_bleibt_fehler(win: Welt, ausgabe: str) -> None:
    wt = win.worktree(41)
    win.ticket(41, "CLOSED", 48)
    (win.tmp / "ps.json").write_text(ausgabe, encoding="utf-8")
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler
    assert wt.is_dir()


def test_win_powershell_fehlt_bleibt_fehler(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(42)
    win.ticket(42, "CLOSED", 48)
    monkeypatch.setattr(hausmeister_windows, "_POWERSHELL", str(win.tmp / "gibt-es-nicht"))
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler and "PowerShell" in r.grund
    assert wt.is_dir()


def test_win_powershell_zeitlimit_bleibt_fehler(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(43)
    win.ticket(43, "CLOSED", 48)
    attrappe(win.tmp / "bin", "powershell", "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(hausmeister_windows, "PS_ZEITLIMIT_S", 0.5)
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler
    assert wt.is_dir()


# --- Löschen mit Schreibschutz --------------------------------------------------


def test_nur_lese_dateien_werden_geloescht(win: Welt) -> None:
    """Wie ``.git/objects`` unter Windows: schreibgeschützt, rmtree scheitert sonst."""
    klon = win.klon("duoplus-44")
    win.ticket(44, "CLOSED", 48)
    gesperrt = klon / ".git" / "objects" / "pack"
    for datei in gesperrt.iterdir():
        datei.chmod(0o444)
    gesperrt.chmod(0o555)
    try:
        r = win.aufraeumen(klon, loeschen=True)
    finally:
        if gesperrt.exists():
            gesperrt.chmod(0o755)
    assert (r.art, r.fehler) == ("gelöscht", False), r.grund
    assert not klon.exists()


# --- Platte der Wurzeln ---------------------------------------------------------


def test_platte_der_wurzel_gemessen(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_PLATTE", raising=False)
    gemessen: list[Path] = []

    def messen(pfad: str | Path) -> object:
        gemessen.append(Path(pfad))
        return type("Stand", (), {"total": 100, "free": 40})()

    monkeypatch.setattr(hausmeister.shutil, "disk_usage", messen)
    assert hausmeister._frei_anteil() == 0.4
    assert gemessen and all(p != Path.home() for p in gemessen)
    assert welt.heim / "wt" in gemessen


def test_platte_ohne_existierende_wurzel_misst_home(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_PLATTE", raising=False)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(welt.tmp / "nix" / "wt-*"))
    gemessen: list[Path] = []

    def messen(pfad: str | Path) -> object:
        gemessen.append(Path(pfad))
        return type("Stand", (), {"total": 100, "free": 50})()

    monkeypatch.setattr(hausmeister.shutil, "disk_usage", messen)
    assert hausmeister._frei_anteil() == 0.5
    assert gemessen == [Path.home()]


# --- Altbestand (E16) -----------------------------------------------------------


def test_altbestand_festhalten_schreibt_einmal(welt: Welt) -> None:
    a, b = welt.worktree(51), welt.worktree(52)
    hausmeister.altbestand_pfad().unlink(missing_ok=True)  # Windows-Welt legt sie an (#68)
    pfad = hausmeister.altbestand_festhalten([a])
    assert pfad == hausmeister.altbestand_pfad() == welt.log.parent / "altbestand.json"
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    assert daten["freigegeben"] is False and daten["erstellt"]
    assert daten["ordner"] == [a.resolve().as_posix()]
    assert hausmeister.altbestand_festhalten([a, b]) == pfad
    assert json.loads(pfad.read_text(encoding="utf-8"))["ordner"] == [
        a.resolve().as_posix(),
        b.resolve().as_posix(),  # ergänzt, nie entfernt (Fixrunde 1)
    ]


def test_altbestand_gelistet_bleibt(win: Welt) -> None:
    wt = win.worktree(53)
    win.ticket(53, "CLOSED", 48)
    liste = hausmeister.altbestand_festhalten([wt])
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and not r.fehler
    assert r.grund == f"Altbestand, wartet auf das Ja des Menschen (Liste {liste})"
    assert wt.is_dir()


def test_altbestand_nicht_gelistet_normale_regeln(win: Welt) -> None:
    wt, anderer = win.worktree(54), win.worktree(55)
    win.ticket(54, "CLOSED", 48)
    hausmeister.altbestand_festhalten([anderer])
    assert win.aufraeumen(wt, loeschen=True).art == "gelöscht"


def test_altbestand_freigegeben_normale_regeln(win: Welt) -> None:
    wt, offen = win.worktree(56), win.worktree(57)
    win.ticket(56, "CLOSED", 48)
    win.ticket(57, "OPEN")
    liste = hausmeister.altbestand_festhalten([wt, offen])
    daten = json.loads(liste.read_text(encoding="utf-8"))
    daten["freigegeben"] = True
    liste.write_text(json.dumps(daten), encoding="utf-8")
    assert win.aufraeumen(wt, loeschen=True).art == "gelöscht"
    r = win.aufraeumen(offen, loeschen=True)
    assert r.art == "bleibt" and "offen" in r.grund


@pytest.mark.parametrize("inhalt", ["{kaputt", "[]", '{"freigegeben": true}'])
def test_altbestand_kaputt_alles_bleibt(win: Welt, inhalt: str) -> None:
    wt = win.worktree(58)
    win.ticket(58, "CLOSED", 48)
    hausmeister.altbestand_pfad().parent.mkdir(parents=True, exist_ok=True)
    hausmeister.altbestand_pfad().write_text(inhalt, encoding="utf-8")
    r = win.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler and "Altbestand" in r.grund
    assert wt.is_dir()


def test_altbestand_fehlt_keine_wirkung_auf_server(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = welt.worktree(59)
    welt.ticket(59, "CLOSED", 48)
    hausmeister.altbestand_pfad().unlink(missing_ok=True)  # Windows-Welt legt sie an (#68)
    if hausmeister._ist_windows():
        # Server nachstellen: Linux-Regeln, die Prozess-Prüfung aber mit dem, was dieser
        # Rechner hat (kein /proc unter Windows).
        windows_pruefung = hausmeister._kein_prozess

        def pruefen(ordner: Path, *, probe: bool = False) -> None:
            hausmeister._PLATTFORM = "win32"
            try:
                windows_pruefung(ordner, probe=probe)
            finally:
                hausmeister._PLATTFORM = "linux"

        monkeypatch.setattr(hausmeister, "_PLATTFORM", "linux")
        monkeypatch.setattr(hausmeister, "_kein_prozess", pruefen)
    assert not hausmeister.altbestand_pfad().exists()
    assert welt.aufraeumen(wt, loeschen=True).art == "gelöscht"


# --- Nur auf echtem Windows ------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="echter Windows-Prozess nötig")
def test_echt_windows_unterprozess_mit_cwd_im_ordner_bleibt(welt: Welt) -> None:
    wt = welt.worktree(60)
    welt.ticket(60, "CLOSED", 48)
    (wt / "unter").mkdir()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=wt / "unter")
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and not r.fehler, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


# --- Fixrunde 1 -----------------------------------------------------------------


def test_win_ohne_altbestand_datei_haelt_erst_alles_fest(win: Welt) -> None:
    wt = win.worktree(51)  # sonst löschbar
    win.ticket(51, "CLOSED", 48)
    hausmeister.altbestand_pfad().unlink()
    erg = hausmeister.aufraeumen_alles(loeschen=True, jetzt=win.jetzt)
    assert wt.is_dir()
    assert [e.art for e in erg if e.ordner.name == "wt-51"] == ["bleibt"]
    daten = json.loads(hausmeister.altbestand_pfad().read_text(encoding="utf-8"))
    assert daten["freigegeben"] is False
    assert wt.resolve().as_posix() in daten["ordner"]


def test_win_trockenlauf_ohne_umbenennungs_probe(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(52)
    win.ticket(52, "CLOSED", 48)
    aufrufe: list[str] = []
    monkeypatch.setattr(hausmeister_windows, "_umbenennen", lambda alt, neu: aufrufe.append(str(neu)))
    assert win.aufraeumen(wt).art == "gelöscht"
    assert hausmeister.aufraeumen_alles(jetzt=win.jetzt)
    assert aufrufe == [] and wt.is_dir()


class _Msvcrt:
    """Naht für ``msvcrt`` (gibt es nur unter Windows); ``LK_NBLCK`` wie dort = 2."""

    LK_NBLCK = 2

    def __init__(self, fehler: OSError | None = None) -> None:
        self.fehler = fehler
        self.aufrufe: list[tuple[int, int]] = []

    def locking(self, fd: int, modus: int, laenge: int) -> None:
        self.aufrufe.append((modus, laenge))
        if self.fehler:
            raise self.fehler


def test_win_sperre_belegt_raeumt_nicht_auf(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(53)
    win.ticket(53, "CLOSED", 48)
    m = _Msvcrt(OSError(errno.EACCES, "Permission denied"))
    monkeypatch.setattr(hausmeister_windows, "_msvcrt", m, raising=False)
    assert hausmeister.aufraeumen_alles(loeschen=True, jetzt=win.jetzt) == []
    assert m.aufrufe == [(2, 1)] and wt.is_dir()


def test_win_sperre_frei_raeumt_auf(win: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = win.worktree(54)
    win.ticket(54, "CLOSED", 48)
    m = _Msvcrt()
    monkeypatch.setattr(hausmeister_windows, "_msvcrt", m, raising=False)
    erg = hausmeister.aufraeumen_alles(loeschen=True, jetzt=win.jetzt)
    assert m.aufrufe == [(2, 1)]
    assert [e.art for e in erg if e.ordner.name == "wt-54"] == ["gelöscht"]
    assert not wt.exists()


def test_win_fixture_entkoppelt_peb_prozessliste(win: Welt) -> None:
    """Die ``win``-Fixture simuliert Prozesse per PowerShell-Attrappe, nie über echte Runner-PEBs."""
    wt = win.worktree(55)
    assert prozessbaum.arbeitsordner_in_windows(wt) == {}
    hausmeister_windows._kein_arbeitsordner_drin(wt)


def test_arbeitsordner_griff_wiederholt_bei_kurzem_cwd_wechsel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein einmaliger cwd-Wechsel im fremden Prozess während der PEB-Probe wird wiederholt;
    dauerhafter Wechsel bleibt fail-closed und schließt alle kopierten Griffe."""
    geschlossen: list[int] = []
    naechster_griff = 500

    class _Kernel:
        def OpenProcess(self, _zugriff: int, _erbe: bool, _pid: int) -> int:
            return 100

        def GetCurrentProcess(self) -> int:
            return 200

        def GetExitCodeProcess(self, _griff: int, code_ptr: object) -> int:
            code_ptr._obj.value = prozessbaum._STILL_ACTIVE  # type: ignore[attr-defined]
            return 1

        def DuplicateHandle(
            self,
            _quelle: int,
            _fremd: int,
            _ziel: int,
            kopie_ptr: object,
            _zugriff: int,
            _erbe: bool,
            _optionen: int,
        ) -> int:
            nonlocal naechster_griff
            naechster_griff += 1
            kopie_ptr._obj.value = naechster_griff  # type: ignore[attr-defined]
            return 1

        def CloseHandle(self, griff: object) -> int:
            wert = getattr(griff, "value", griff)
            assert isinstance(wert, int)
            geschlossen.append(wert)
            return 1

    monkeypatch.setattr(prozessbaum, "_win32", lambda: (_Kernel(), object()))
    monkeypatch.setattr(prozessbaum.ctypes, "set_last_error", lambda _e: None, raising=False)
    monkeypatch.setattr(prozessbaum.ctypes, "get_last_error", lambda: 0, raising=False)

    # Fall 1: erster Versuch sieht cwd-Wechsel, zweiter Versuch ist stabil → gelingt, alter Griff zu.
    alt = b"A" * 16 + (111).to_bytes(8, "little")
    neu = b"B" * 16 + (222).to_bytes(8, "little")
    antworten = iter([alt, neu, neu, neu])
    monkeypatch.setattr(prozessbaum, "_curdir", lambda _p, _pid: next(antworten))
    griff = prozessbaum._arbeitsordner_griff_windows(816)
    assert griff == 502
    assert 501 in geschlossen and 100 in geschlossen

    # Fall 2: cwd wechselt bei jedem Versuch → bleibt fail-closed, alle kopierten Griffe zu.
    geschlossen.clear()
    zaehler = 0

    def _wechselnd(_p: int, _pid: int) -> bytes:
        nonlocal zaehler
        zaehler += 1
        return b"X" * 16 + zaehler.to_bytes(8, "little")

    monkeypatch.setattr(prozessbaum, "_curdir", _wechselnd)
    with pytest.raises(OSError, match="cwd wechselte"):
        prozessbaum._arbeitsordner_griff_windows(816)
    assert 503 in geschlossen and 504 in geschlossen and 505 in geschlossen and 100 in geschlossen
