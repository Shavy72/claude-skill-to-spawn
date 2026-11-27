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
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, capo, gh, pc_fenster, prozessbaum, spawn, sperre, startstelle, taktgeber

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

    def __init__(self) -> None:
        self.aufgaben: dict[str, str] = {}
        self.aufrufe: list[tuple[list[str], dict[str, Any]]] = []

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

    # Eine Aufgabe beim Anmelden (ONLOGON) und eine im Takt (MINUTE /MO 5)
    anmelde_cmd = next(c for c in creates if "ONLOGON" in c)
    takt_cmd = next(c for c in creates if "MINUTE" in c)
    assert anmelde_cmd[anmelde_cmd.index("/TN") + 1] == taktgeber.windows_aufgabe_name(293, anmelden=True)
    assert takt_cmd[takt_cmd.index("/TN") + 1] == taktgeber.windows_aufgabe_name(293, anmelden=False)
    assert takt_cmd[takt_cmd.index("/MO") + 1] == "5"

    for cmd in (anmelde_cmd, takt_cmd):
        tr = cmd[cmd.index("/TR") + 1]
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
