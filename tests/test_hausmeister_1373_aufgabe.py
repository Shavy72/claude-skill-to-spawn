"""Hausmeister #1373 Teil B: tägliche Windows-Aufgabe, Ticket-Ende, Altbestand-Lösch-Liste.

schtasks läuft nur unter Windows; hier wird ``subprocess.run`` im Modul durch eine
Naht mit den Ausgaben ersetzt, die schtasks liefert (Query-XML vorhanden bzw.
„FEHLER: Das System kann die angegebene Datei nicht finden.“). Die Lösch-Liste läuft
mit echten Git-Ordnern aus ``test_hausmeister_1368`` (Fixture ``welt``, nur ``gh``
gestellt). Echte schtasks-Läufe nur auf Windows (skipif).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import logging
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
    d = welt.klon("wt-ohne")  # keine Nummer → für den Menschen
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
    assert "Gelöscht wird erst nach dem Ja des Menschen" in text
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


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl nur unter Unix")
def test_loesch_liste_sperre_belegt_meldet_fehler(welt: Welt, tmp_path: Path) -> None:
    import fcntl

    wt = welt.worktree(35)  # ein Kandidat: leeres Ergebnis heißt dann „kein Lauf“
    sperre = hausmeister.altbestand_pfad().with_name(hausmeister.SPERR_DATEI)
    sperre.parent.mkdir(parents=True, exist_ok=True)
    with sperre.open("a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # anderer Lauf hält sie
        with pytest.raises(RuntimeError, match="Sperre"):
            hausmeister_aufgabe.loesch_liste(tmp_path / "x.md")
    assert not (tmp_path / "x.md").exists() and wt.is_dir()


def test_cli_altbestand_liste_im_unterprozess(welt: Welt) -> None:
    ordner = _vier_ordner(welt)
    vorher = _ordner_stand(welt)
    ziel = welt.tmp / "cli-liste.md"
    lauf = subprocess.run(
        [
            sys.executable,
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hm" / "h.jsonl"))
    gestartet: list = []
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: True)
    # Popen ist ersetzt, also startet nichts Echtes; die Tür soll bis Popen durchreichen.
    monkeypatch.setattr(hausmeister_aufgabe.starter, "unter_test", lambda: False)
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
    assert kw["stdout"] is subprocess.DEVNULL
    # stderr landet neben dem Hausmeister-Log (Startfehler sonst unsichtbar)
    assert kw["stderr"].name == str(tmp_path / "hm" / "hausmeister-start.err")
    assert kw["stderr"].closed  # Elternprozess schließt seine Kopie
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

    monkeypatch.setattr(hausmeister_aufgabe.starter, "unter_test", lambda: False)
    monkeypatch.setattr(hausmeister_aufgabe.subprocess, "Popen", kaputt)
    hausmeister_aufgabe.ticket_ende_anstossen()
    assert "kein Start" in caplog.text


def test_ticket_ende_geht_durch_die_start_tuer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Unter Test sperrt die Start-Tür (#1406) den echten Start — nur Log, kein Absturz."""
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hm" / "h.jsonl"))
    monkeypatch.setattr(hausmeister_aufgabe, "_ist_windows", lambda: True)
    hausmeister_aufgabe.ticket_ende_anstossen()
    assert "nicht gestartet" in caplog.text


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


# --- Fixrunde 1 -----------------------------------------------------------------


def test_loesch_liste_mit_echter_altbestand_datei_zeigt_echte_gruppen(
    welt: Welt,
) -> None:
    ordner = _vier_ordner(welt)
    pfad = hausmeister.altbestand_festhalten(ordner)  # echte Datei, keine Attrappe
    ziel = welt.tmp / "liste" / "altbestand.md"
    hausmeister_aufgabe.loesch_liste(ziel)
    text = ziel.read_text(encoding="utf-8")
    assert "## Löschbar (1)" in text and "## Sichern + löschen (1)" in text
    assert "## Bleibt (mit Grund) (1)" in text and "## Ohne Ticket-Nummer (1)" in text
    assert all(o.is_dir() for o in ordner)
    assert sorted(json.loads(pfad.read_text(encoding="utf-8"))["ordner"]) == sorted(
        o.resolve().as_posix() for o in ordner
    )


def test_altbestand_festhalten_ergaenzt_nie_entfernt(welt: Welt) -> None:
    a, b = welt.worktree(36), welt.worktree(37)
    pfad = hausmeister.altbestand_festhalten([a])
    hausmeister.altbestand_festhalten([b])
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    assert daten["ordner"] == [a.resolve().as_posix(), b.resolve().as_posix()]
    assert daten["freigegeben"] is False


def test_altbestand_freigegeben_bleibt_unveraendert(
    welt: Welt, caplog: pytest.LogCaptureFixture
) -> None:
    a, b = welt.worktree(38), welt.worktree(39)
    pfad = hausmeister.altbestand_festhalten([a])
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    daten["freigegeben"] = True
    pfad.write_text(json.dumps(daten), encoding="utf-8")
    vorher = pfad.read_text(encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        hausmeister.altbestand_festhalten([b])
    assert pfad.read_text(encoding="utf-8") == vorher
    assert "freigegeben" in caplog.text


def test_fuer_david_mit_ticket_eigene_gruppe() -> None:
    e = hausmeister.Ergebnis(
        Path("/x/wt-77"), "bleibt", "Geheimnis", 77, fuer_david=True
    )
    ohne = hausmeister.Ergebnis(Path("/x/wt-ohne"), "bleibt", "keine Nummer", None)
    assert hausmeister_aufgabe._gruppe(e) == "Für den Menschen (selbst prüfen)"
    assert hausmeister_aufgabe._gruppe(ohne) == "Ohne Ticket-Nummer"


def _bau_modul(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import importlib.util

    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    name = f"bau_1373_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SKILL / "skripte" / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    sys.modules[name] = bau
    try:
        spec.loader.exec_module(bau)
    finally:
        sys.modules.pop(name, None)
    return bau


def test_bau_ticket_ende_nur_nach_echtem_lauf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bau = _bau_modul(monkeypatch, tmp_path)
    angestossen: list[int] = []
    monkeypatch.setattr(
        hausmeister_aufgabe, "ticket_ende_anstossen", lambda: angestossen.append(1)
    )
    assert bau._ECHTER_LAUF is False  # --dry-run / --print-prompt setzen ihn nie
    bau.ticket_ende_anstossen()
    assert angestossen == []
    monkeypatch.setattr(bau, "_ECHTER_LAUF", True)
    bau.ticket_ende_anstossen()
    assert angestossen == [1]


def test_bau_ticket_ende_fehler_nur_warnung(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bau = _bau_modul(monkeypatch, tmp_path)

    def kaputt() -> None:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(hausmeister_aufgabe, "ticket_ende_anstossen", kaputt)
    monkeypatch.setattr(bau, "_ECHTER_LAUF", True)
    with caplog.at_level(logging.WARNING):
        bau.ticket_ende_anstossen()  # wirft nicht, Exit-Code von main() bleibt
    assert "kaputt" in caplog.text
