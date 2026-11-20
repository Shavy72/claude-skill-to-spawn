"""Hausmeister #1373 Teil B: tägliche Windows-Aufgabe, Ticket-Ende, Altbestand-Lösch-Liste.

schtasks läuft nur unter Windows; hier wird ``subprocess.run`` im Modul durch eine
Naht mit den Ausgaben ersetzt, die schtasks liefert (Query-XML vorhanden bzw.
„FEHLER: Das System kann die angegebene Datei nicht finden.“). Die Lösch-Liste läuft
mit echten Git-Ordnern aus ``test_hausmeister_1368`` (Fixture ``welt``, nur ``gh``
gestellt). Echte schtasks-Läufe nur auf Windows (skipif).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SKILL,
    Welt,
    welt,
)

from to_spawn import hausmeister, hausmeister_aufgabe

FEHLT = "FEHLER: Das System kann die angegebene Datei nicht finden.\r\n"


def _xml(befehl: str, argumente: str, uhrzeit: str) -> str:
    """So sieht ``schtasks /Query /TN <name> /XML`` für eine DAILY-Aufgabe aus."""
    argumente = argumente.replace("&", "&amp;").replace('"', "&quot;")
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        "  <RegistrationInfo>\r\n    <URI>\\to-spawn Hausmeister</URI>\r\n  </RegistrationInfo>\r\n"
        "  <Triggers>\r\n    <CalendarTrigger>\r\n"
        f"      <StartBoundary>2026-11-20T{uhrzeit}:00</StartBoundary>\r\n"
        "      <Enabled>true</Enabled>\r\n"
        "      <ScheduleByDay>\r\n        <DaysInterval>1</DaysInterval>\r\n      </ScheduleByDay>\r\n"
        "    </CalendarTrigger>\r\n  </Triggers>\r\n"
        '  <Actions Context="Author">\r\n    <Exec>\r\n'
        f"      <Command>{befehl}</Command>\r\n"
        f"      <Arguments>{argumente}</Arguments>\r\n"
        "    </Exec>\r\n  </Actions>\r\n</Task>\r\n"
    )


class Schtasks:
    """Naht für ``subprocess.run``: merkt Aufrufe, Query antwortet aus ``self.xml``."""

    def __init__(self) -> None:
        self.xml: str | None = None
        self.aufrufe: list[tuple[list[str], dict]] = []

    def __call__(
        self, args: list[str], **kw: object
    ) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append((list(args), dict(kw)))
        if "/Query" in args:
            if self.xml is None:
                return subprocess.CompletedProcess(args, 1, "", FEHLT)
            return subprocess.CompletedProcess(args, 0, self.xml, "")
        if "/Create" in args:
            tr = args[args.index("/TR") + 1]
            befehl, _, rest = tr[1:].partition('" ')
            self.xml = _xml(befehl, rest, args[args.index("/ST") + 1])
            return subprocess.CompletedProcess(
                args,
                0,
                'ERFOLGREICH: Die geplante Aufgabe "to-spawn Hausmeister" wurde erstellt.\r\n',
                "",
            )
        return subprocess.CompletedProcess(args, 0, "", "")

    def creates(self) -> list[list[str]]:
        return [a for a, _ in self.aufrufe if "/Create" in a]


@pytest.fixture
def win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Schtasks, str, list]:
    py = tmp_path / "Python" / "python.exe"
    py.parent.mkdir()
    py.write_text("", encoding="utf-8")
    (py.parent / "pythonw.exe").write_text("", encoding="utf-8")
    naht = Schtasks()
    festgehalten: list = []

    def festhalten(ordner):
        festgehalten.append(("festhalten", list(ordner), len(naht.creates())))
        return tmp_path / "altbestand.json"

    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: True)
    monkeypatch.setattr(hausmeister_aufgabe.subprocess, "run", naht)
    monkeypatch.setattr(hausmeister, "altbestand_festhalten", festhalten, raising=False)
    return naht, str(py), festgehalten


def test_neu_angelegt_mit_pythonw_ohne_fenster(win) -> None:
    naht, py, festgehalten = win
    text = hausmeister_aufgabe.aufgabe_einrichten(python=py)
    assert "eingerichtet" in text
    (create,) = naht.creates()
    assert create[:2] == ["schtasks", "/Create"] and "/F" in create
    assert create[create.index("/SC") + 1] == "DAILY"
    assert create[create.index("/ST") + 1] == "03:30"
    assert create[create.index("/TN") + 1] == "to-spawn Hausmeister"
    tr = create[create.index("/TR") + 1]
    pythonw = str(Path(py).with_name("pythonw.exe"))
    assert tr == f'"{pythonw}" "{SKILL / "to_spawn.py"}" aufraeumen --alles --loeschen'
    assert "python.exe" not in tr
    for _, kw in naht.aufrufe:  # jeder schtasks-Aufruf ohne Fenster, mit Zeitlimit
        assert int(kw["creationflags"]) & 0x08000000
        assert kw.get("timeout")
    assert festgehalten and festgehalten[0][2] == 0  # Altbestand VOR dem ersten Create


def test_zweiter_lauf_idempotent(win) -> None:
    naht, py, _ = win
    hausmeister_aufgabe.aufgabe_einrichten(python=py)
    text = hausmeister_aufgabe.aufgabe_einrichten(python=py)
    assert "schon eingerichtet" in text
    assert len(naht.creates()) == 1


def test_abweichende_uhrzeit_legt_neu_an(win) -> None:
    naht, py, _ = win
    hausmeister_aufgabe.aufgabe_einrichten(python=py)
    hausmeister_aufgabe.aufgabe_einrichten(python=py, uhrzeit="04:15")
    assert len(naht.creates()) == 2
    zweiter = naht.creates()[1]
    assert "/F" in zweiter and zweiter[zweiter.index("/ST") + 1] == "04:15"


def test_trocken_legt_nichts_an(win) -> None:
    naht, py, festgehalten = win
    text = hausmeister_aufgabe.aufgabe_einrichten(python=py, trocken=True)
    assert "[trocken]" in text and naht.creates() == [] and festgehalten == []


def test_ohne_pythonw_klarer_fehler(win, tmp_path: Path) -> None:
    naht, _, _ = win
    nackt = tmp_path / "nackt" / "python.exe"
    nackt.parent.mkdir()
    nackt.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="pythonw.exe"):
        hausmeister_aufgabe.aufgabe_einrichten(python=str(nackt))
    assert naht.creates() == []


def test_schtasks_fehlt_klare_meldung(win, monkeypatch: pytest.MonkeyPatch) -> None:
    _, py, _ = win

    def weg(*a, **kw):
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(hausmeister_aufgabe.subprocess, "run", weg)
    with pytest.raises(RuntimeError, match="schtasks"):
        hausmeister_aufgabe.aufgabe_einrichten(python=py)


def test_nicht_windows_tut_nichts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: False)
    aufrufe: list = []
    monkeypatch.setattr(
        hausmeister_aufgabe.subprocess, "run", lambda *a, **k: aufrufe.append(a)
    )
    assert "Windows" in hausmeister_aufgabe.aufgabe_einrichten()
    assert aufrufe == []


# --- Lösch-Liste ------------------------------------------------------------------


def _vier_ordner(welt: Welt) -> list[Path]:
    a = welt.worktree(31)  # zu > 24 h, sauber → löschbar
    welt.ticket(31, "CLOSED", 48)
    b = welt.worktree(32, schmutzig=True)  # zu > 24 h, ungesichert → sichern + löschen
    welt.ticket(32, "CLOSED", 48)
    c = welt.worktree(33)  # offen → bleibt
    welt.ticket(33, "OPEN")
    d = welt.klon("wt-ohne")  # keine Nummer → für David
    return [a, b, c, d]


def _ordner_stand(welt: Welt) -> list[str]:
    return sorted(p.name for p in (welt.heim / "wt").iterdir()) + sorted(
        p.name for p in welt.heim.iterdir()
    )


def test_loesch_liste_vier_gruppen_loescht_nichts(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordner = _vier_ordner(welt)
    vorher = _ordner_stand(welt)
    festgehalten: list = []
    monkeypatch.setattr(
        hausmeister,
        "altbestand_festhalten",
        lambda o: festgehalten.append(list(o)) or welt.tmp / "altbestand.json",
        raising=False,
    )
    ziel = welt.tmp / "liste" / "altbestand.md"
    assert hausmeister_aufgabe.loesch_liste(ziel) == ziel
    assert _ordner_stand(welt) == vorher and all(o.is_dir() for o in ordner)
    text = ziel.read_text(encoding="utf-8")
    assert "Gelöscht wird erst nach Davids Ja" in text
    teile = {
        k: text.split(f"## {k}")[1].split("\n## ")[0]
        for k in (
            "Löschbar",
            "Sichern + löschen",
            "Bleibt (mit Grund)",
            "Ohne Ticket-Nummer",
        )
    }
    assert "wt-31" in teile["Löschbar"] and "(1)" in teile["Löschbar"]
    assert "wt-32" in teile["Sichern + löschen"]
    assert (
        "wt-33" in teile["Bleibt (mit Grund)"] and "#33" in teile["Bleibt (mit Grund)"]
    )
    assert "wt-ohne" in teile["Ohne Ticket-Nummer"]
    assert sum(1 for z in text.splitlines() if z.startswith("- ")) == 4
    assert festgehalten and sorted(p.name for p in festgehalten[0]) == [
        "wt-31",
        "wt-32",
        "wt-33",
        "wt-ohne",
    ]


def test_loesch_liste_gescheitert_meldet_fehler(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hausmeister, "aufraeumen_alles", lambda **kw: None)
    with pytest.raises(RuntimeError, match="Sperre"):
        hausmeister_aufgabe.loesch_liste(tmp_path / "x.md")
    assert not (tmp_path / "x.md").exists()


def test_cli_altbestand_liste_im_unterprozess(welt: Welt) -> None:
    ordner = _vier_ordner(welt)
    vorher = _ordner_stand(welt)
    ziel = welt.tmp / "cli-liste.md"
    # Teil A (altbestand_festhalten) kommt per Merge; bis dahin stellt der Test sie.
    code = (
        "import runpy, sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
        "from to_spawn import hausmeister\n"
        "if not hasattr(hausmeister, 'altbestand_festhalten'):\n"
        "    hausmeister.altbestand_festhalten = lambda o: Path(sys.argv[2])\n"
        "sys.argv = [sys.argv[3]] + sys.argv[4:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    lauf = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(SKILL),
            str(welt.tmp / "altbestand.json"),
            str(SKILL / "to_spawn.py"),
            "aufraeumen",
            "--altbestand-liste",
            str(ziel),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TO_SPAWN_REPO": str(welt.haupt)},
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert str(ziel) in lauf.stdout
    assert _ordner_stand(welt) == vorher and all(o.is_dir() for o in ordner)
    assert "## Löschbar (1)" in ziel.read_text(encoding="utf-8")


def test_cli_ordner_weiter_unveraendert(welt: Welt) -> None:
    wt = welt.worktree(34)
    welt.ticket(34, "OPEN")
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "aufraeumen", str(wt)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TO_SPAWN_REPO": str(welt.haupt)},
    )
    assert lauf.returncode == 0, lauf.stderr
    assert "bleibt" in lauf.stdout and "[trocken]" in lauf.stdout and wt.is_dir()


# --- Ticket-Ende ------------------------------------------------------------------


def test_ticket_ende_windows_ohne_fenster_nie_detached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gestartet: list = []
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: True)
    monkeypatch.setattr(
        hausmeister_aufgabe.subprocess,
        "Popen",
        lambda args, **kw: gestartet.append((args, kw)),
    )
    hausmeister_aufgabe.ticket_ende_anstossen()
    ((args, kw),) = gestartet
    assert args[1:] == [
        str(SKILL / "to_spawn.py"),
        "aufraeumen",
        "--alles",
        "--loeschen",
    ]
    assert kw["creationflags"] == 0x08000000 | 0x00000200
    assert not kw["creationflags"] & 0x00000008  # DETACHED_PROCESS
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    assert "start_new_session" not in kw


def test_ticket_ende_server_nur_aufpasser(monkeypatch: pytest.MonkeyPatch) -> None:
    gestartet: list = []
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: False)
    monkeypatch.setattr(
        hausmeister_aufgabe.subprocess, "Popen", lambda *a, **k: gestartet.append(a)
    )
    hausmeister_aufgabe.ticket_ende_anstossen()
    assert gestartet == []  # auf dem Server räumt der Aufpasser-Cron


def test_ticket_ende_fehler_nur_geloggt(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: True)

    def kaputt(*a, **k):
        raise OSError("kein Start")

    monkeypatch.setattr(hausmeister_aufgabe.subprocess, "Popen", kaputt)
    hausmeister_aufgabe.ticket_ende_anstossen()
    assert "kein Start" in caplog.text


def test_bau_py_stoesst_am_ende_an_und_install_richtet_ein() -> None:
    bau = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    assert "hausmeister_aufgabe.ticket_ende_anstossen()" in bau
    install = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert (
        "aufraeumen --aufgabe-einrichten" in install
        or "--aufgabe-einrichten" in install
    )


def test_kein_detached_process_in_neuen_dateien() -> None:
    for datei in (SKILL / "to_spawn" / "hausmeister_aufgabe.py",):
        assert "DETACHED" + "_PROCESS" not in datei.read_text(encoding="utf-8")


# --- Echt unter Windows -----------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32", reason="schtasks gibt es nur unter Windows"
)
def test_echt_windows_zweimal_einrichten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    name = f"to-spawn Hausmeister Test {uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(hausmeister_aufgabe, "AUFGABE_NAME", name)
    monkeypatch.setattr(
        hausmeister,
        "altbestand_festhalten",
        lambda o: tmp_path / "a.json",
        raising=False,
    )
    try:
        assert "eingerichtet" in hausmeister_aufgabe.aufgabe_einrichten()
        assert "schon eingerichtet" in hausmeister_aufgabe.aufgabe_einrichten()
        abfrage = subprocess.run(
            ["schtasks", "/Query", "/TN", name],
            capture_output=True,
            text=True,
            check=False,
        )
        assert abfrage.returncode == 0, abfrage.stderr
    finally:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, check=False
        )
