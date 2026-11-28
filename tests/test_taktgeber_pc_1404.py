"""#907 (App #1404): Zustandsloser Taktgeber am PC (Windows).

Weg-Tests analog ``tests/test_pc_fenster_501.py`` und ``tests/test_taktgeber_1402.py``:

- Idempotente Einrichtung als Windows-Aufgaben beim Anmelden (``ONLOGON``) + im Takt
  (``MINUTE /MO 5``) mit ``pythonw.exe`` und fensterlosem ``schtasks``-Aufruf
  (``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP``, nie ``DETACHED_PROCESS``).
- CLI ``taktgeber-einrichten`` richtet am PC (Windows) die Windows-Aufgaben ein statt
  mit Exit 2 abzulehnen.
- Wecken wiedereröffneter Tickets am PC über :mod:`to_spawn.pc_fenster` +
  :mod:`to_spawn.startstelle` als sichtbarer Windows-Terminal-Tab ``bau <N>``
  (``wt`` selbst fensterlos; Start-Sperre verhindert doppelte Tabs).
- Echter Windows-``schtasks``-Lauf (nur unter Windows, mit Wegwerf-Aufgabennamen
  über die Fixture ``schtasks_aufgabe``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_taktgeber_1402 import (
    _commit_mit_beleg,
    _gh_stand,
    _git,
    _issue_aus_stand,
    _vorlage_issue,
    _vorlage_kommentar,
    _welt_bauen,
)

from to_spawn import (
    bau_log,
    capo,
    gh,
    pc_fenster,
    prozessbaum,
    spawn,
    sperre,
    starter,
    startstelle,
    taktgeber,
    ticket_zustand,
)

WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def _schtasks_xml(
    name: str, befehl: str, argumente: str, *, intervall: str | None = None, anmelden: bool = False
) -> str:
    """Typische Ausgabe von ``schtasks /Query /TN <name> /XML`` für Takt- oder Anmelde-Aufgabe."""
    argumente_esc = argumente.replace("&", "&amp;").replace('"', "&quot;")
    if anmelden:
        trigger = "    <LogonTrigger>\r\n      <Enabled>true</Enabled>\r\n    </LogonTrigger>\r\n"
    else:
        trigger = (
            "    <TimeTrigger>\r\n"
            "      <StartBoundary>2026-11-27T12:00:00</StartBoundary>\r\n"
            "      <Repetition>\r\n"
            f"        <Interval>{intervall or 'PT5M'}</Interval>\r\n"
            "      </Repetition>\r\n"
            "    </TimeTrigger>\r\n"
        )
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        f"  <RegistrationInfo>\r\n    <URI>\\{name}</URI>\r\n  </RegistrationInfo>\r\n"
        f"  <Triggers>\r\n{trigger}  </Triggers>\r\n"
        '  <Actions Context="Author">\r\n    <Exec>\r\n'
        f'      <Command>"{befehl}"</Command>\r\n'
        f"      <Arguments>{argumente_esc}</Arguments>\r\n"
        "    </Exec>\r\n  </Actions>\r\n</Task>\r\n"
    )


class _SchtasksNaht:
    """Naht für ``subprocess.run`` bei ``schtasks``-Aufrufen (speichert XML je Aufgabenname)."""

    def __init__(self, *, create_fehler: str | None = None) -> None:
        self.aufgaben: dict[str, str] = {}
        self.aufrufe: list[tuple[list[str], dict[str, Any]]] = []
        self.xml_roh: dict[str, bytes] = {}
        self.xml_pfade: list[Path] = []
        self.create_fehler = create_fehler

    def __call__(self, args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append((list(args), dict(kw)))
        name = args[args.index("/TN") + 1] if "/TN" in args else ""
        if "/Query" in args:
            if name not in self.aufgaben:
                return subprocess.CompletedProcess(
                    args,
                    1,
                    "",
                    "FEHLER: Das System kann die angegebene Datei nicht finden.\r\n",
                )
            return subprocess.CompletedProcess(args, 0, self.aufgaben[name], "")
        if "/Create" in args:
            if "/XML" in args:
                xml_pfad = Path(args[args.index("/XML") + 1])
                self.xml_pfade.append(xml_pfad)
                roh = xml_pfad.read_bytes()
                self.xml_roh[name] = roh
                if self.create_fehler:
                    return subprocess.CompletedProcess(args, 1, "", self.create_fehler)
                self.aufgaben[name] = roh.decode("utf-16")
                return subprocess.CompletedProcess(
                    args,
                    0,
                    f'ERFOLGREICH: Die geplante Aufgabe "{name}" wurde erstellt.\r\n',
                    "",
                )
            if self.create_fehler:
                return subprocess.CompletedProcess(args, 1, "", self.create_fehler)
            tr = args[args.index("/TR") + 1]
            befehl, _, rest = tr[1:].partition('" ')
            sc = args[args.index("/SC") + 1].upper()
            if sc == "ONLOGON":
                self.aufgaben[name] = _schtasks_xml(name, befehl, rest, anmelden=True)
            else:
                mo = int(args[args.index("/MO") + 1])
                intervall = "PT1H" if mo == 60 else f"PT{mo}M"
                self.aufgaben[name] = _schtasks_xml(name, befehl, rest, intervall=intervall)
            return subprocess.CompletedProcess(
                args,
                0,
                f'ERFOLGREICH: Die geplante Aufgabe "{name}" wurde erstellt.\r\n',
                "",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    def creates(self) -> list[list[str]]:
        return [a for a, _ in self.aufrufe if "/Create" in a]


class _WtAufnahme:
    """Zeichnet ``wt``-Aufrufe über ``pc_fenster.subprocess.run`` auf (analog ``test_pc_fenster_501``)."""

    def __init__(self, exit_code: int = 0) -> None:
        self.aufrufe: list[tuple[list[str], dict[str, Any]]] = []
        self.exit_code = exit_code

    def __call__(self, befehl: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append((list(befehl), dict(kw)))
        return subprocess.CompletedProcess(befehl, self.exit_code, "", "" if not self.exit_code else "0x80070002")


def _simuliere_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_fenster, "am_pc", lambda: True)
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP, raising=False)


def _wt_attrappe() -> Path:
    """Legt eine ``wt``-Attrappe in ``TO_SPAWN_TEST_BIN`` an (Start-Tür #1406)."""
    attrappe = Path(os.environ["TO_SPAWN_TEST_BIN"]) / "wt"
    attrappe.write_text("", encoding="utf-8")
    return attrappe


def _fake_python_mit_pythonw(tmp_path: Path) -> str:
    py_dir = tmp_path / "py"
    py_dir.mkdir(exist_ok=True)
    py = py_dir / "python.exe"
    pyw = py_dir / "pythonw.exe"
    py.write_text("", encoding="utf-8")
    pyw.write_text("", encoding="utf-8")
    return str(py)


# --- 1. Windows-Aufgabenplanung (beim Anmelden + im Takt, idempotent, fensterlos) ---


def test_windows_aufgaben_einrichten_legt_anmelde_und_takt_aufgabe_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    text = taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    creates = naht.creates()
    assert len(creates) == 2, creates

    # Eine Aufgabe beim Anmelden (/XML mit LogonTrigger) und eine im Takt (MINUTE /MO 5)
    anmelde_cmd = next(c for c in creates if "/XML" in c)
    takt_cmd = next(c for c in creates if "MINUTE" in c)
    anmelde_name = taktgeber.windows_aufgabe_name(293, anmelden=True)
    assert anmelde_cmd[anmelde_cmd.index("/TN") + 1] == anmelde_name
    assert takt_cmd[takt_cmd.index("/TN") + 1] == taktgeber.windows_aufgabe_name(293, anmelden=False)
    assert takt_cmd[takt_cmd.index("/MO") + 1] == "5"

    anmelde_xml = naht.aufgaben[anmelde_name]
    assert "<LogonTrigger>" in anmelde_xml
    assert "pythonw.exe" in anmelde_xml
    assert "taktgeber 293" in anmelde_xml
    assert str(repo.resolve()) in anmelde_xml

    tr = takt_cmd[takt_cmd.index("/TR") + 1]
    assert "pythonw.exe" in tr
    assert "taktgeber 293" in tr
    assert str(repo.resolve()) in tr

    # Jeder schtasks-Aufruf fensterlos mit CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nie DETACHED_PROCESS
    for _, kw in naht.aufrufe:
        flags = int(kw.get("creationflags") or 0)
        assert flags & CREATE_NO_WINDOW
        assert flags & CREATE_NEW_PROCESS_GROUP
        assert not (flags & DETACHED_PROCESS)
        assert kw.get("timeout") == taktgeber.SCHTASKS_ZEITLIMIT_S

    assert "eingerichtet" in text


def test_windows_anmelde_aufgabe_nutzt_xml_ohne_admin_mit_escape_cleanup_und_idempotenz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#907: Anmelde-Aufgabe per ``/XML`` (UTF-16, ``LeastPrivilege``, XML-Escape, Cleanup, Idempotenz)."""
    _simuliere_windows(monkeypatch)
    monkeypatch.setenv("USERDOMAIN", "TEST-PC")
    monkeypatch.setenv("USERNAME", "nutzer&<1>")
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    # Windows verbietet < und > in Ordnernamen ([WinError 123]); & ist plattformübergreifend zulässig.
    repo = tmp_path / "repo & test"
    repo.mkdir()

    text1 = taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    assert "eingerichtet" in text1

    creates = naht.creates()
    anmelde_name = taktgeber.windows_aufgabe_name(293, anmelden=True)
    anmelde_cmd = next(c for c in creates if c[c.index("/TN") + 1] == anmelde_name)
    assert "/XML" in anmelde_cmd, f"Anmelde-Aufgabe muss /XML statt /SC ONLOGON nutzen: {anmelde_cmd}"
    assert "ONLOGON" not in anmelde_cmd
    assert len(naht.xml_pfade) == 1
    assert not naht.xml_pfade[0].exists(), "Temp-XML-Datei muss in finally gelöscht werden"

    roh = naht.xml_roh[anmelde_name]
    assert roh.startswith((b"\xff\xfe", b"\xfe\xff")), "XML-Datei muss als UTF-16 mit BOM geschrieben werden"
    xml_text = roh.decode("utf-16")
    # XML muss wohlgeformt sein (Escape von & und < in UserId und Repo-Pfad)
    wurzel = ET.fromstring(xml_text)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert wurzel.findtext(".//t:LogonTrigger/t:Enabled", namespaces=ns) == "true"
    assert wurzel.findtext(".//t:LogonTrigger/t:UserId", namespaces=ns) == r"TEST-PC\nutzer&<1>"
    assert wurzel.findtext(".//t:Principal[@id='Author']/t:UserId", namespaces=ns) == r"TEST-PC\nutzer&<1>"
    assert wurzel.findtext(".//t:Principal[@id='Author']/t:LogonType", namespaces=ns) == "InteractiveToken"
    assert wurzel.findtext(".//t:Principal[@id='Author']/t:RunLevel", namespaces=ns) == "LeastPrivilege"
    assert wurzel.findtext(".//t:Exec/t:Command", namespaces=ns) == str(Path(py).with_name("pythonw.exe"))
    assert wurzel.findtext(".//t:Exec/t:Arguments", namespaces=ns) == taktgeber.windows_aufgabe_argumente(
        293, repo.resolve()
    )

    # XML-Escape für <, > und & auch in Command und Arguments (unter Windows nicht als Ordnername anlegbar)
    xml_sonder = taktgeber.windows_anmelde_xml(
        r"C:\py & <bin>\pythonw.exe",
        '"to_spawn.py" taktgeber 293 --repo-dir "C:\\repo & <test>"',
        benutzer=r"TEST-PC\nutzer&<1>",
    )
    wurzel_sonder = ET.fromstring(xml_sonder)
    assert wurzel_sonder.findtext(".//t:Exec/t:Command", namespaces=ns) == r"C:\py & <bin>\pythonw.exe"
    assert (
        wurzel_sonder.findtext(".//t:Exec/t:Arguments", namespaces=ns)
        == '"to_spawn.py" taktgeber 293 --repo-dir "C:\\repo & <test>"'
    )

    # Idempotenz: zweiter Lauf erkennt die per XML angelegte Anmelde-Aufgabe als unverändert
    text2 = taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    assert len(naht.creates()) == 2
    assert "Anmelde-Aufgabe unverändert" in text2

    # Fehlerfall: Temp-XML wird auch bei schtasks-Fehler sauber gelöscht
    naht_fehler = _SchtasksNaht(create_fehler="FEHLER: Zugriff verweigert")
    monkeypatch.setattr(taktgeber.subprocess, "run", naht_fehler)
    with pytest.raises(RuntimeError, match="Zugriff verweigert"):
        taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    assert len(naht_fehler.xml_pfade) == 1
    assert not naht_fehler.xml_pfade[0].exists(), "Temp-XML-Datei muss auch bei Fehler gelöscht werden"


def test_windows_aufgaben_einrichten_ist_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    creates_nach_1 = len(naht.creates())
    assert creates_nach_1 == 2

    text2 = taktgeber.windows_aufgaben_einrichten(293, repo, python=py)
    assert len(naht.creates()) == creates_nach_1
    assert "unverändert" in text2


def test_windows_aufgaben_einrichten_aktualisiert_bei_abweichung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    taktgeber.windows_aufgaben_einrichten(293, repo, takt_min=5, python=py)
    assert len(naht.creates()) == 2

    # Nur der Takt ändert sich → nur die Takt-Aufgabe wird neu erstellt, Anmelde-Aufgabe bleibt unverändert
    text = taktgeber.windows_aufgaben_einrichten(293, repo, takt_min=10, python=py)
    assert len(naht.creates()) == 3
    assert "MINUTE" in naht.creates()[-1] and "10" in naht.creates()[-1]
    assert "unverändert" in text and "eingerichtet" in text


def test_windows_aufgaben_trocken_schreibt_nichts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    text = taktgeber.windows_aufgaben_einrichten(293, repo, trocken=True, python=py)
    assert naht.creates() == []
    assert "[trocken]" in text


def test_windows_aufgaben_ohne_pythonw_wirft_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    nackt = tmp_path / "nackt" / "python.exe"
    nackt.parent.mkdir()
    nackt.write_text("", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(RuntimeError, match="pythonw.exe"):
        taktgeber.windows_aufgaben_einrichten(293, repo, python=str(nackt))


def test_windows_aufgaben_schtasks_fehlt_klare_meldung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)

    def _fehlt(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(taktgeber.subprocess, "run", _fehlt)
    py = _fake_python_mit_pythonw(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(RuntimeError, match="schtasks"):
        taktgeber.windows_aufgaben_einrichten(293, repo, python=py)


def test_cli_taktgeber_einrichten_nutzt_windows_aufgaben_am_pc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _simuliere_windows(monkeypatch)
    naht = _SchtasksNaht()
    monkeypatch.setattr(taktgeber.subprocess, "run", naht)
    py = _fake_python_mit_pythonw(tmp_path)
    monkeypatch.setattr(taktgeber.sys, "executable", py)
    repo = tmp_path / "repo"
    repo.mkdir()

    args = argparse.Namespace(befehl="taktgeber-einrichten", spec=293, repo_dir=str(repo), trocken=True)
    code = taktgeber.cli_lauf(args)
    ausgabe = capsys.readouterr().out
    assert code == 0, ausgabe
    assert "[trocken]" in ausgabe


# --- 2. Sichtbarer Windows-Terminal-Tab `bau <N>` über pc_fenster + startstelle ---


def test_pc_wecke_ticket_oeffnet_sichtbaren_wt_tab_bau_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    attrappe = _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    repo.mkdir()

    assert pc_fenster.wt_verfuegbar() is True
    ok, meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Klickweg fehlt")
    assert ok is True, meldung
    assert "bau 530" in meldung

    assert len(wt.aufrufe) == 1
    befehl, kw = wt.aufrufe[0]
    auftrag = "Wiedereröffnet (Klickweg fehlt) — Befund beheben, testen und neu einreichen."
    soll = spawn.wt_tab_befehl(repo, 530, auftrag, "--remote-control")
    assert soll[0] == "wt" and befehl == [str(attrappe), *soll[1:]]
    assert "--title" in befehl and "bau 530" in befehl
    assert kw.get("cwd") == str(repo)

    # wt selbst ohne aufpoppendes Konsolenfenster (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    flags = int(kw.get("creationflags") or 0)
    assert flags & CREATE_NO_WINDOW
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert not (flags & DETACHED_PROCESS)


def test_pc_wecke_ticket_dry_run_oeffnet_keinen_tab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    repo.mkdir()

    ok, meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Befund", dry_run=True)
    assert ok is True
    assert "[Probe]" in meldung and "bau 530" in meldung
    assert wt.aufrufe == []


def test_pc_wecke_ticket_sperre_verhindert_doppelten_tab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    repo.mkdir()
    haupt_sperre = startstelle.sperr_datei("530", "test-org/beispiel-app")

    with sperre.halte(haupt_sperre) as frei:
        assert frei is True
        ok, meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Befund")

    assert ok is False
    assert "Start-Sperre" in meldung
    assert wt.aufrufe == [], "Bei belegter Start-Sperre darf kein zweiter Tab geöffnet werden"


def test_pc_wecke_ticket_wt_fehler_meldet_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _simuliere_windows(monkeypatch)
    _wt_attrappe()
    monkeypatch.setattr(pc_fenster.subprocess, "run", _WtAufnahme(exit_code=1))
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    repo.mkdir()

    ok, meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Befund")
    assert ok is False
    assert "wt Exit 1" in meldung


def test_pc_taktgeber_lauf_weckt_wiedereroeffnetes_ticket_als_tab_und_protokolliert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vollständiger ``taktgeber.lauf`` am PC: wiedereröffnetes Ticket → Tab ``bau 530`` + Bau-Log."""
    _simuliere_windows(monkeypatch)
    _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)

    issue = {
        "number": 530,
        "state": "open",
        "state_reason": "reopened",
        "closed_at": "2026-11-27T09:50:00Z",
        "labels": [],
    }
    kommentare = [{"body": "Aufseher: Belegseite fehlt", "created_at": "2026-11-27T10:00:00Z"}]
    monkeypatch.setattr(capo, "kinder", lambda *_a, **_kw: [issue])
    monkeypatch.setattr(capo, "haupt_ref", lambda *_a, **_kw: "origin/master")
    monkeypatch.setattr(capo, "alle_commits", lambda *_a, **_kw: [])
    monkeypatch.setattr(capo, "issue_kommentare", lambda *_a, **_kw: kommentare)
    monkeypatch.setattr(gh, "hauptzweig", lambda *_a, **_kw: "master")

    erg = taktgeber.lauf(repo, 293, "test-org/beispiel-app", konfig={"hauptzweig": "master", "live": {"ohne": True}})
    assert erg.ok is True
    assert len(erg.aktionen) == 1
    assert erg.aktionen[0].aktion == "geweckt"
    assert "bau 530" in erg.aktionen[0].grund
    assert len(wt.aufrufe) == 1

    # Ereignis im Bau-Log festgehalten
    zeilen = bau_log.lese(repo, 530)
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "geweckt" for z in zeilen), zeilen

    # Mit aktiver Start-Sperre weckt der nächste Lauf nicht doppelt
    haupt_sperre = startstelle.sperr_datei("530", "test-org/beispiel-app")
    with sperre.halte(haupt_sperre) as frei:
        assert frei is True
        erg2 = taktgeber.lauf(
            repo, 293, "test-org/beispiel-app", konfig={"hauptzweig": "master", "live": {"ohne": True}}
        )
    assert erg2.ok is True
    assert erg2.aktionen[0].aktion == "unveraendert"
    assert len(wt.aufrufe) == 1


# --- 2b. Lauf-Log unter pythonw.exe, Isolation von pc_fenster.subprocess.run & Anmelde-Weg-Test ---


def test_cli_lauf_schreibt_lauf_log_bei_pythonw_ohne_stdout_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#907: Unter ``pythonw.exe`` (``sys.stdout = sys.stderr = None``) schreibt ``cli_lauf()`` nach ``log_datei(spec)``."""
    log_ordner = tmp_path / "taktgeber-logs"
    monkeypatch.setenv("TO_SPAWN_TAKTGEBER_LOG", str(log_ordner))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    repo = tmp_path / "repo"
    repo.mkdir()

    # 1) Normaler Lauf mit Aktionen + Trockenlauf mit 0 Tickets
    monkeypatch.setattr(
        taktgeber,
        "lauf",
        lambda *_a, **_kw: taktgeber.LaufErgebnis(
            True,
            [taktgeber.TicketAktion(530, "geweckt", "#530 geweckt im Tab „bau 530“")],
        ),
    )
    args_ok = argparse.Namespace(
        befehl="taktgeber", spec=907, repo_dir=str(repo), gh_repo="test-org/beispiel-app", dry_run=False
    )
    assert taktgeber.cli_lauf(args_ok) == 0

    log_pfad = log_ordner / "907.log"
    assert log_pfad.is_file(), "pythonw.exe-Lauf hat kein Lauf-Log unter log_datei(spec) erzeugt"
    inhalt = log_pfad.read_text(encoding="utf-8")
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", inhalt), inhalt
    assert "#907" in inhalt
    assert "#530: geweckt" in inhalt

    # 2) Leerer Trockenlauf erzeugt ebenfalls einen nachvollziehbaren Log-Eintrag
    monkeypatch.setattr(taktgeber, "lauf", lambda *_a, **_kw: taktgeber.LaufErgebnis(True, []))
    args_probe = argparse.Namespace(
        befehl="taktgeber", spec=907, repo_dir=str(repo), gh_repo="test-org/beispiel-app", dry_run=True
    )
    assert taktgeber.cli_lauf(args_probe) == 0
    inhalt_probe = log_pfad.read_text(encoding="utf-8")
    assert "Probe" in inhalt_probe or "dry-run" in inhalt_probe, inhalt_probe

    # 3) Lauf-Fehler (erg.ok == False) steht mit Zeitstempel im Lauf-Log
    monkeypatch.setattr(
        taktgeber,
        "lauf",
        lambda *_a, **_kw: taktgeber.LaufErgebnis(False, [], "Sub-Issues nicht lesbar (gh)"),
    )
    assert taktgeber.cli_lauf(args_ok) == 1
    inhalt_fehler = log_pfad.read_text(encoding="utf-8")
    assert "FEHLER" in inhalt_fehler and "Sub-Issues nicht lesbar (gh)" in inhalt_fehler, inhalt_fehler

    # 4) Fehlendes GitHub-Repo (Exit 2) steht ebenfalls im Lauf-Log
    monkeypatch.setattr(gh, "repo_aus_origin", lambda *_a, **_kw: "")
    args_ohne_repo = argparse.Namespace(befehl="taktgeber", spec=907, repo_dir=str(repo), gh_repo="", dry_run=False)
    assert taktgeber.cli_lauf(args_ohne_repo) == 2
    assert "GitHub-Repo unbekannt" in log_pfad.read_text(encoding="utf-8")

    # 5) Unerwartete Ausnahme (z. B. RuntimeError) geht nicht still verloren, sondern landet im Lauf-Log
    def _kaputt(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("Netzwerk abgebrochen")

    monkeypatch.setattr(taktgeber, "lauf", _kaputt)
    assert taktgeber.cli_lauf(args_ok) == 1
    assert "Netzwerk abgebrochen" in log_pfad.read_text(encoding="utf-8")


def test_pc_wecke_ticket_ohne_wt_kein_unsichtbarer_hintergrundstart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#907: Am PC ohne ``wt`` darf ``wecke_ticket()`` nicht unsichtbar im Hintergrund starten."""
    _simuliere_windows(monkeypatch)
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))

    repo = tmp_path / "repo"
    skript = repo / "scripts" / "bau.py"
    skript.parent.mkdir(parents=True)
    skript.write_text("# test-bau\n", encoding="utf-8")

    gestartet: list[list[str]] = []
    monkeypatch.setattr(starter, "starte", lambda cmd, **_kw: gestartet.append(list(cmd)))

    assert pc_fenster.am_pc() is True
    assert pc_fenster.wt_verfuegbar() is False

    ok, meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Befund")
    assert ok is False, f"Ohne wt am PC darf kein Hintergrundstart erfolgen: {meldung}"
    assert "wt" in meldung.lower(), meldung
    assert gestartet == [], f"Unsichtbarer Hintergrundstart trotz fehlendem wt: {gestartet}"

    ok_probe, meldung_probe = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Befund", dry_run=True)
    assert ok_probe is False, meldung_probe
    assert "wt" in meldung_probe.lower(), meldung_probe
    assert gestartet == []


def test_pc_fenster_subprocess_patch_isoliert_git_und_bau_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#907: Patch auf ``pc_fenster.subprocess.run`` bleibt lokal — ``git``/``bau_log.lese()`` zählen nicht als ``wt``."""
    _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)

    repo = tmp_path / "git-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    bau_log.schreibe(
        "taktgeber",
        repo=repo,
        ticket=530,
        wer="taktgeber",
        was="Ticket #530 geweckt",
        session="taktgeber",
        commit="-",
        grund="Test",
        kontextstand=0,
        aktion="geweckt",
    )

    # bau_log.lese() ruft über config.repo_wurzel() echte git-Subprozesse auf — diese dürfen nie bei wt landen
    zeilen = bau_log.lese(repo, 530)
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "geweckt" for z in zeilen), zeilen
    assert wt.aufrufe == [], f"git-Subprozesse wurden fälschlich als wt-Aufruf gezählt: {wt.aufrufe}"

    # Erst der echte Tab-Start über pc_fenster landet bei wt
    pc_fenster.tab_starten(repo, 530, "Befund")
    assert len(wt.aufrufe) == 1


def test_pc_anmelde_lauf_holt_liegengebliebene_tickets_nach_mit_gh_aufzeichnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#907 Weg-Test: Anmelde-Lauf mit aufgezeichneten GitHub-Antworten holt alle liegengebliebenen Zustände nach.

    - #601: 4 Regeln + live gemeldet → geschlossen
    - #602: 4 Regeln + nur Staging → Label ``staging:fertig``, bleibt offen
    - #603: wiedereröffnet → genau ein sichtbarer ``wt``-Tab ``bau 603`` (fensterlos gestartet)
    - Zweiter Lauf bei aktiver Start-Sperre für #603 → kein zweiter Tab, keine weiteren GitHub-Schreibaufrufe
    """
    welt = _welt_bauen(
        tmp_path,
        issues=[
            _vorlage_issue(601),
            _vorlage_issue(602),
            _vorlage_issue(
                603,
                state="open",
                state_reason="reopened",
                closed_at="2026-11-27T09:50:00Z",
            ),
        ],
        kommentare={},
        konfig={"hauptzweig": "master", "live": {"ohne": False, "befehl": "python deploy.py live"}},
    )
    sha_601 = _commit_mit_beleg(welt["repo"], 601, mit_beleg=True, mit_nummer=True)
    sha_602 = _commit_mit_beleg(welt["repo"], 602, mit_beleg=True, mit_nummer=True)
    sha_603_alt = _commit_mit_beleg(welt["repo"], 603, mit_beleg=True, mit_nummer=True)

    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "601": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_601}"),
            _vorlage_kommentar(f"Deploy-Schlange: ist live · {sha_601}", created_at="2026-11-27T10:05:00Z"),
        ],
        "602": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_602}"),
            _vorlage_kommentar("Hinweis: fertig auf Staging, wartet auf Live.", created_at="2026-11-27T10:01:00Z"),
        ],
        "603": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_603_alt}", created_at="2026-11-27T09:40:00Z"),
            _vorlage_kommentar(
                "Aufseher: Belegseite — Klickweg nach Abmeldung nachholen", created_at="2026-11-27T10:00:00Z"
            ),
        ],
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    # PC (Windows) + aufgezeichneter gh-Stub + isolierte wt-Aufnahme + eigenes Lauf-Log
    _simuliere_windows(monkeypatch)
    attrappe = _wt_attrappe()
    wt = _WtAufnahme()
    monkeypatch.setattr(pc_fenster.subprocess, "run", wt)
    log_ordner = tmp_path / "taktgeber-logs"
    monkeypatch.setenv("TO_SPAWN_GH_STUB", welt["env"]["TO_SPAWN_GH_STUB"])
    monkeypatch.setenv("GH_TAKTGEBER_ZUSTAND", welt["env"]["GH_TAKTGEBER_ZUSTAND"])
    monkeypatch.setenv("TO_SPAWN_START_SPERREN", welt["env"]["TO_SPAWN_START_SPERREN"])
    monkeypatch.setenv("TO_SPAWN_TEST_FERN_SPERRE_AUS", "1")
    monkeypatch.setenv("TO_SPAWN_TAKTGEBER_LOG", str(log_ordner))

    args = argparse.Namespace(
        befehl="taktgeber",
        spec=293,
        repo_dir=str(welt["repo"]),
        gh_repo="test-org/beispiel-app",
        dry_run=False,
    )

    # Lauf 1 (Anmelde-Lauf): holt alle drei liegengebliebenen Zustände auf einmal nach
    assert taktgeber.cli_lauf(args) == 0

    nach1 = _gh_stand(welt)
    assert _issue_aus_stand(nach1, 601)["state"] == "closed"
    assert _issue_aus_stand(nach1, 602)["state"] == "open"
    assert ticket_zustand.STAGING_FERTIG_LABEL in ticket_zustand.label_namen(_issue_aus_stand(nach1, 602))
    assert _issue_aus_stand(nach1, 603)["state"] == "open"

    # Genau ein sichtbarer wt-Tab für #603, fensterlos gestartet
    assert len(wt.aufrufe) == 1, wt.aufrufe
    befehl, kw = wt.aufrufe[0]
    assert befehl[0] == str(attrappe)
    assert "--title" in befehl and "bau 603" in befehl
    assert "\ufffd" not in " ".join(befehl), f"Kaputte Kodierung im Tab-Befehl: {befehl}"
    flags = int(kw.get("creationflags") or 0)
    assert flags & CREATE_NO_WINDOW
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert not (flags & DETACHED_PROCESS)

    # Alle drei Handlungen im Bau-Log; bau_log.lese() zählt nicht als wt-Aufruf
    assert any(
        z.get("wer") == "taktgeber" and z.get("aktion") == "geschlossen" for z in bau_log.lese(welt["repo"], 601)
    )
    assert any(
        z.get("wer") == "taktgeber" and z.get("aktion") == "label_gesetzt" for z in bau_log.lese(welt["repo"], 602)
    )
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "geweckt" for z in bau_log.lese(welt["repo"], 603))
    assert len(wt.aufrufe) == 1

    # Lauf-Log des Windows-Anmeldelaufs enthält alle drei Tickets mit Zeitstempel
    log_text = (log_ordner / "293.log").read_text(encoding="utf-8")
    assert "#601: geschlossen" in log_text
    assert "#602: label_gesetzt" in log_text
    assert "#603: geweckt" in log_text

    aufrufe_nach1 = list(nach1["aufrufe"])

    # Lauf 2 (nächster Takt) bei aktiver Start-Sperre für #603: kein zweiter Tab, keine neuen GitHub-Schreibaufrufe
    haupt_sperre = startstelle.sperr_datei("603", "test-org/beispiel-app")
    with sperre.halte(haupt_sperre) as frei:
        assert frei is True
        assert taktgeber.cli_lauf(args) == 0

    nach2 = _gh_stand(welt)
    assert len(wt.aufrufe) == 1, "Zweiter Lauf bei laufender Session darf keinen zweiten Tab öffnen"
    assert nach2["aufrufe"] == aufrufe_nach1


def test_gh_taktgeber_stub_liefert_utf8_trotz_cp1252_pipe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#907: ``gh_taktgeber_1402.py`` schreibt wie echtes ``gh`` immer UTF-8, auch ohne ``PYTHONIOENCODING=utf-8``."""
    welt = _welt_bauen(
        tmp_path,
        issues=[_vorlage_issue(610)],
        kommentare={
            "610": [
                _vorlage_kommentar("Deploy-Schlange: grün auf master · " + "a" * 40),
                _vorlage_kommentar("Aufseher: Belegseite — Klickweg prüfen"),
            ]
        },
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", welt["env"]["TO_SPAWN_GH_STUB"])
    monkeypatch.setenv("GH_TAKTGEBER_ZUSTAND", welt["env"]["GH_TAKTGEBER_ZUSTAND"])
    # Simuliert Windows-Pipe ohne gesetztes PYTHONIOENCODING=utf-8 (dort Standard cp1252):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("PYTHONUTF8", "0")

    kommentare = capo.issue_kommentare("test-org/beispiel-app", 610)
    assert kommentare is not None
    texte = [str(k.get("body") or "") for k in kommentare]
    assert any("grün auf master · " + "a" * 40 in t for t in texte), texte
    assert any("Belegseite — Klickweg prüfen" in t for t in texte), texte
    assert all("\ufffd" not in t for t in texte), texte


# --- 3. Echter schtasks-Lauf unter Windows (CI / PC) ---


@pytest.mark.skipif(not WINDOWS, reason="schtasks gibt es nur unter Windows")
def test_echt_windows_taktgeber_aufgaben_idempotent(tmp_path: Path, schtasks_aufgabe: Callable[..., str]) -> None:
    """Legt echte Wegwerf-Aufgaben (Anmelden + Takt) per ``schtasks`` an und prüft Idempotenz."""
    import schtasks_test  # liegt in ``tests/hilfen`` (#789)

    takt_name = schtasks_aufgabe("taktgeber-takt")
    anmelden_name = schtasks_aufgabe("taktgeber-anmelden")
    repo = tmp_path / "repo"
    repo.mkdir()

    text1 = taktgeber.windows_aufgaben_einrichten(293, repo, takt_name=takt_name, anmelden_name=anmelden_name)
    assert "eingerichtet" in text1
    assert schtasks_test.existiert(takt_name)
    assert schtasks_test.existiert(anmelden_name)

    text2 = taktgeber.windows_aufgaben_einrichten(293, repo, takt_name=takt_name, anmelden_name=anmelden_name)
    assert "unverändert" in text2
