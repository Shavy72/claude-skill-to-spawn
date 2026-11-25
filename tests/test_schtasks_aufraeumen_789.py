"""Windows: Tests lassen keine schtasks-Aufgaben zurück (#789).

Vorfall (Windows-PC, 25.11.2026): In der Windows-Aufgabenplanung blieb die deaktivierte
Aufgabe „to-spawn Hausmeister Test 71ce15“ als Rest eines Testlaufs stehen.

Regel (#789, nur Windows — Linux bleibt unverändert):
- Jeder Test, der per ``schtasks`` eine Aufgabe anlegt, nutzt ein eindeutiges Test-Präfix
  samt PID und räumt sie im Fixture-Teardown (``finally``), per Abbruch-Schutz und beim
  Session-Ende wieder ab.
- Verwaiste Test-Aufgaben abgestürzter Läufe (tote PID oder Alt-Präfix
  „to-spawn Hausmeister Test “) werden erkannt und geräumt, ohne je die echte Aufgabe
  „to-spawn Hausmeister“ oder Aufgaben anderer noch laufender Test-Prozesse anzufassen.
"""

from __future__ import annotations

import ast
import inspect
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest
from to_spawn import prozessbaum

SKILL = Path(__file__).resolve().parent.parent
TESTS = SKILL / "tests"


class _FakeSchtasks:
    """Stellt die Windows-Aufgabenplanung im Speicher nach (Query CSV, Query /TN, Create, Delete)."""

    def __init__(self, anfang: dict[str, str] | None = None, *, loeschen_scheitert: set[str] | None = None) -> None:
        #: Aufgabenname (ohne führenden Backslash) → Status-Spalte (z. B. "Bereit", "Deaktiviert").
        self.aufgaben: dict[str, str] = dict(anfang or {})
        self.loeschen_scheitert: set[str] = set(loeschen_scheitert or ())
        self.aufrufe: list[list[str]] = []

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        befehl = ["schtasks", *args]
        self.aufrufe.append(befehl)
        if "/Query" in args and "/FO" in args:
            zeilen = [f'"\\{name}","Nicht zutreffend","{status}"' for name, status in sorted(self.aufgaben.items())]
            return subprocess.CompletedProcess(befehl, 0, "\r\n".join(zeilen) + ("\r\n" if zeilen else ""), "")
        if "/Query" in args and "/TN" in args:
            name = args[args.index("/TN") + 1].removeprefix("\\")
            if name in self.aufgaben:
                return subprocess.CompletedProcess(befehl, 0, f"Aufgabenname: \\{name}\r\n", "")
            return subprocess.CompletedProcess(
                befehl, 1, "", "FEHLER: Das System kann die angegebene Datei nicht finden.\r\n"
            )
        if "/Delete" in args and "/TN" in args:
            name = args[args.index("/TN") + 1].removeprefix("\\")
            if name in self.loeschen_scheitert:
                return subprocess.CompletedProcess(befehl, 1, "", "FEHLER: Zugriff verweigert.\r\n")
            if name in self.aufgaben:
                del self.aufgaben[name]
                return subprocess.CompletedProcess(befehl, 0, "ERFOLGREICH\r\n", "")
            return subprocess.CompletedProcess(
                befehl, 1, "", "FEHLER: Das System kann die angegebene Datei nicht finden.\r\n"
            )
        if "/Create" in args and "/TN" in args:
            name = args[args.index("/TN") + 1].removeprefix("\\")
            self.aufgaben[name] = "Bereit"
            return subprocess.CompletedProcess(befehl, 0, "ERFOLGREICH\r\n", "")
        return subprocess.CompletedProcess(befehl, 0, "", "")


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(stash=pytest.Stash()),
        exitstatus=0,
    )


def _schtasks_test_modul():
    import schtasks_test  # liegt in ``tests/hilfen`` (#789)

    return schtasks_test


# --- Rot-Beweise & Kernregeln (plattformübergreifend über Naht) --------------------


def test_echt_windows_test_nutzt_schtasks_fixture_und_keinen_rohen_aufruf() -> None:
    """``test_echt_windows_zweimal_einrichten`` nutzt die zentrale ``schtasks_aufgabe``-Fixture (#789)."""
    import test_hausmeister_1373_aufgabe

    fn = test_hausmeister_1373_aufgabe.test_echt_windows_zweimal_einrichten
    params = inspect.signature(fn).parameters
    assert "schtasks_aufgabe" in params, (
        "test_echt_windows_zweimal_einrichten muss die Fixture schtasks_aufgabe nutzen (#789)"
    )

    quelle = inspect.getsource(fn)
    assert "to-spawn Hausmeister Test " not in quelle, (
        "Kein hartcodiertes Aufgaben-Präfix im Einzeltest — Namen kommen aus schtasks_aufgabe (#789)"
    )
    baum = ast.parse(quelle)
    rohe_schtasks = [
        k.lineno
        for k in ast.walk(baum)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "run"
        and k.args
        and isinstance(k.args[0], (ast.List, ast.Tuple))
        and k.args[0].elts
        and isinstance(k.args[0].elts[0], ast.Constant)
        and k.args[0].elts[0].value == "schtasks"
    ]
    assert rohe_schtasks == [], (
        f"Roher subprocess.run(['schtasks', ...]) in test_echt_windows_zweimal_einrichten: {rohe_schtasks}"
    )


def test_sessionstart_raeumt_verwaiste_und_alt_test_aufgaben_unter_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vor dem Lauf liegende Reste (deaktivierter Alt-Rest + tote PID) werden unter Windows geräumt (#789).

    Die echte Produktiv-Aufgabe „to-spawn Hausmeister“ und Aufgaben eines noch laufenden
    Nachbar-Prozesses (z. B. zweiter xdist-Worker) bleiben unangetastet.
    """
    st = _schtasks_test_modul()
    echte = "to-spawn Hausmeister"
    alt_rest = "to-spawn Hausmeister Test 71ce15"
    tote_pid_rest = "to-spawn-test-p999991-hausmeister a1b2c3d4"
    fremd_aktiv = "to-spawn-test-p424242-hausmeister deadbeef"

    fake = _FakeSchtasks(
        {
            echte: "Bereit",
            alt_rest: "Deaktiviert",
            tote_pid_rest: "Bereit",
            fremd_aktiv: "Bereit",
        }
    )
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)
    monkeypatch.setattr(st, "_win_mutex_nehmen", lambda _name: st.WaechterSperre(lambda: None))
    monkeypatch.setattr(prozessbaum, "lebt", lambda pid: pid == 424242)

    vorher_verwaist = st.verwaiste_aufgaben()
    assert len(vorher_verwaist) == 2 and alt_rest in vorher_verwaist and tote_pid_rest in vorher_verwaist

    session = _session()
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]

    assert st.verwaiste_aufgaben() == [], f"Nach Session-Start blieben verwaiste Aufgaben stehen: {fake.aufgaben}"
    assert echte in fake.aufgaben, "Echte Hausmeister-Aufgabe darf nie gelöscht werden"
    assert fremd_aktiv in fake.aufgaben, "Aufgabe eines noch laufenden Test-Prozesses darf nicht gelöscht werden"
    assert alt_rest not in fake.aufgaben
    assert tote_pid_rest not in fake.aufgaben


def test_sessionfinish_raeumt_und_meldet_liegengebliebene_test_aufgabe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lässt ein Test unter Windows eine Test-Aufgabe offen, räumt ``pytest_sessionfinish`` sie ab und meldet Fehler."""
    st = _schtasks_test_modul()
    leck = st.neuer_name("leck")
    fake = _FakeSchtasks({"to-spawn Hausmeister": "Bereit"})
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)
    monkeypatch.setattr(st, "_win_mutex_nehmen", lambda _name: st.WaechterSperre(lambda: None))

    session = _session()
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]
    # Simuliert einen Test, der ohne Fixture eine Test-Aufgabe angelegt und vergessen hat.
    fake.aufgaben[leck] = "Bereit"
    assert len(st.verwaiste_aufgaben()) == 1

    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert st.verwaiste_aufgaben() == [], "Session-Ende muss liegengebliebene Test-Aufgabe aufräumen"
    assert leck not in fake.aufgaben
    assert "to-spawn Hausmeister" in fake.aufgaben
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_linux_verhalten_unveraendert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unter Linux startet die schtasks-Sicherung keinen einzigen Unterprozess (#789)."""
    st = _schtasks_test_modul()
    aufrufe: list[tuple[str, ...]] = []
    monkeypatch.setattr(st, "ist_windows", lambda: False)
    monkeypatch.setattr(st, "_lauf", lambda *args: aufrufe.append(args) or None)

    session = _session()
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]
    assert st.offene_test_aufgaben() == []
    assert st.verwaiste_raeumen() == []
    assert st.existiert(st.neuer_name("linux")) is False
    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert aufrufe == [], f"Unter Linux darf schtasks nie aufgerufen werden: {aufrufe}"


def test_verschachtelter_session_lauf_raeumt_und_meldet_nichts_wenn_waechter_belegt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hält ein äußerer pytest-Lauf schon den Windows-Wächter, fasst ein Kind-pytest keine Aufgaben an (#789).

    Stellt das Windows-CI-Wettrennen nach: Worker gw3 hat nach hartem Abbruch eine tote-PID-Aufgabe
    stehen, während gw2 (z. B. ``test_pt_reste_177``) einen verschachtelten Kind-pytest-Lauf beendet.
    """
    st = _schtasks_test_modul()
    fremde_waise = "to-spawn-test-p2184-kill 54a60881"
    fake = _FakeSchtasks({fremde_waise: "Bereit"})
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)
    monkeypatch.setattr(prozessbaum, "lebt", lambda _pid: False)
    monkeypatch.setattr(st, "_win_mutex_nehmen", lambda _name: None)

    session = _session()
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]
    assert fremde_waise in fake.aufgaben, "Verschachtelter Lauf darf fremde Test-Aufgabe beim Start nicht löschen"

    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert fremde_waise in fake.aufgaben, "Verschachtelter Lauf darf fremde Test-Aufgabe beim Ende nicht löschen"
    assert session.exitstatus == 0, "Verschachtelter Lauf darf wegen fremder Test-Aufgabe nicht rot werden"
    assert fake.aufrufe == [], f"Ohne Wächter-Sperre darf kein schtasks-Aufruf laufen: {fake.aufrufe}"


def test_collect_only_aktiviert_keinen_schtasks_waechter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bei ``--collect-only`` laufen keine Tests → kein Wächter-Start, kein schtasks-Aufruf (#789)."""
    st = _schtasks_test_modul()
    fremde_waise = "to-spawn-test-p2184-kill 54a60881"
    fake = _FakeSchtasks({fremde_waise: "Bereit"})
    genommen: list[str] = []
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)
    monkeypatch.setattr(prozessbaum, "lebt", lambda _pid: False)
    monkeypatch.setattr(st, "_win_mutex_nehmen", lambda name: genommen.append(name) or st.WaechterSperre(lambda: None))

    session = SimpleNamespace(
        config=SimpleNamespace(stash=pytest.Stash(), option=SimpleNamespace(collectonly=True)),
        exitstatus=0,
    )
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]
    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert genommen == [], "Bei --collect-only darf die Wächter-Sperre nicht belegt werden"
    assert fake.aufrufe == [], f"Bei --collect-only darf schtasks nicht laufen: {fake.aufrufe}"
    assert fremde_waise in fake.aufgaben
    assert session.exitstatus == 0


def test_sessionfinish_gibt_waechter_sperre_auch_bei_fund_wieder_frei(monkeypatch: pytest.MonkeyPatch) -> None:
    """``pytest_sessionfinish`` gibt die Wächter-Sperre immer frei — auch wenn Reste gemeldet werden (#789)."""
    st = _schtasks_test_modul()
    leck = st.neuer_name("freigabe")
    fake = _FakeSchtasks()
    freigegeben: list[str] = []
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)
    monkeypatch.setattr(
        st,
        "_win_mutex_nehmen",
        lambda name: st.WaechterSperre(lambda: freigegeben.append(name)),
    )

    session = _session()
    conftest.pytest_sessionstart(session)  # type: ignore[arg-type]
    assert freigegeben == []
    fake.aufgaben[leck] = "Bereit"

    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    assert freigegeben == [st.WAECHTER_MUTEX_NAME]
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert conftest._SCHTASKS_WAECHTER not in session.config.stash


def test_praefix_erkennung_und_csv_parser() -> None:
    """Eindeutiges Test-Präfix, Alt-Präfix-Erkennung, Schutz der echten Aufgabe, CSV-Ausgabe (DE/EN)."""
    st = _schtasks_test_modul()
    name = st.neuer_name("hausmeister")
    assert name.startswith(st.PRAEFIX)
    assert f"p{os.getpid()}-" in name
    assert " " in name  # Leerzeichen bleibt erhalten, damit Quoting wie bei „to-spawn Hausmeister“ geprüft wird
    assert st.ist_test_aufgabe(name)
    assert st.ist_test_aufgabe("\\" + name)
    assert st.aufgabe_pid(name) == os.getpid()

    assert st.ist_test_aufgabe("to-spawn Hausmeister Test 71ce15")
    assert st.ist_test_aufgabe("\\to-spawn Hausmeister Test 71ce15")
    assert st.aufgabe_pid("to-spawn Hausmeister Test 71ce15") is None

    # Produktiv-Aufgabe und fremde Aufgaben dürfen NIE als Test-Aufgabe gelten.
    for fremd in (
        "to-spawn Hausmeister",
        "\\to-spawn Hausmeister",
        "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag",
        "\\Unterordner\\to-spawn-test-p1-x aaaa1111",
        "Aufgabenname",
        "",
    ):
        assert not st.ist_test_aufgabe(fremd), f"Fälschlich als Test-Aufgabe erkannt: {fremd!r}"

    # Sicherheits-Sperre: loeschen() verweigert jede Nicht-Test-Aufgabe.
    with pytest.raises(ValueError, match="Test-Aufgabe"):
        st.loeschen("to-spawn Hausmeister")

    # CSV-Ausgabe (Deutsch + Englisch, deaktivierte Aufgaben, doppelte Trigger-Zeilen).
    csv_text = (
        '"Aufgabenname","Nächste Laufzeit","Status"\r\n'
        '"\\to-spawn Hausmeister","26.11.2026 03:30:00","Bereit"\r\n'
        '"\\to-spawn Hausmeister Test 71ce15","Nicht zutreffend","Deaktiviert"\r\n'
        '"\\to-spawn Hausmeister Test 71ce15","Nicht zutreffend","Deaktiviert"\r\n'
        f'"\\{name}","11/26/2026 3:30:00 AM","Disabled"\r\n'
        '"\\Microsoft\\Windows\\UpdateOrchestrator\\Schedule Scan","N/A","Ready"\r\n'
    )
    assert st.csv_test_aufgaben(csv_text) == sorted([name, "to-spawn Hausmeister Test 71ce15"])


def test_fixture_raeumt_bei_normal_fehler_und_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``aufgaben_raum`` / ``schtasks_aufgabe`` löscht Aufgaben nach Erfolg, Fehler und KeyboardInterrupt (#789)."""
    st = _schtasks_test_modul()
    fake = _FakeSchtasks()
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)

    # 1. Normaler Lauf
    with st.aufgaben_raum() as fabrik:
        name_ok = fabrik("ok")
        fake("/Create", "/TN", name_ok)
        assert name_ok in fake.aufgaben
    assert name_ok not in fake.aufgaben
    assert name_ok not in st._AKTIVE_AUFGABEN

    # 2. Fehlschlag im Test (AssertionError)
    with pytest.raises(AssertionError, match="kaputt"), st.aufgaben_raum() as fabrik:
        name_err = fabrik("fehler")
        fake("/Create", "/TN", name_err)
        raise AssertionError("kaputt")
    assert name_err not in fake.aufgaben
    assert name_err not in st._AKTIVE_AUFGABEN

    # 3. Abbruch (KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt), st.aufgaben_raum() as fabrik:
        name_int = fabrik("abbruch")
        fake("/Create", "/TN", name_int)
        raise KeyboardInterrupt()
    assert name_int not in fake.aufgaben
    assert name_int not in st._AKTIVE_AUFGABEN

    # 4. Aufgabe wurde vor Fehler noch gar nicht angelegt → Teardown wirft keinen Folgefehler
    with st.aufgaben_raum() as fabrik:
        name_nie = fabrik("nie")
    assert name_nie not in st._AKTIVE_AUFGABEN

    # 5. Löschen schlägt fehl während Aufgabe noch existiert → Teardown meldet laut Fehler
    with pytest.raises(AssertionError, match="blieben nach Teardown stehen"), st.aufgaben_raum() as fabrik:
        name_klemmt = fabrik("klemmt")
        fake("/Create", "/TN", name_klemmt)
        fake.loeschen_scheitert.add(name_klemmt)
    fake.loeschen_scheitert.clear()
    assert st.loeschen(name_klemmt) is True


def test_conftest_fixture_schtasks_aufgabe_verfuegbar(
    monkeypatch: pytest.MonkeyPatch,
    schtasks_aufgabe,
) -> None:
    """Die Pytest-Fixture ``schtasks_aufgabe`` aus ``conftest.py`` vergibt Namen mit Präfix und registriert sie."""
    st = _schtasks_test_modul()
    fake = _FakeSchtasks()
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)

    name = schtasks_aufgabe("probe")
    assert st.ist_test_aufgabe(name)
    assert name in st._AKTIVE_AUFGABEN
    fake("/Create", "/TN", name)
    assert st.existiert(name)


def test_abbruch_schutz_raeumt_bei_sigterm_und_stellt_handler_wieder_her(monkeypatch: pytest.MonkeyPatch) -> None:
    """``abbruch_schutz`` räumt aktive Test-Aufgaben bei SIGTERM ab und stellt den alten Signal-Handler her."""
    st = _schtasks_test_modul()
    fake = _FakeSchtasks()
    monkeypatch.setattr(st, "ist_windows", lambda: True)
    monkeypatch.setattr(st, "_lauf", fake)

    name = st.registrieren(st.neuer_name("sigterm"))
    fake("/Create", "/TN", name)

    alter_handler = signal.getsignal(signal.SIGTERM)
    weitergeleitet: list[int] = []

    def merken(signum: int, _frame: object) -> None:
        weitergeleitet.append(signum)

    signal.signal(signal.SIGTERM, merken)
    try:
        with st.abbruch_schutz():
            aktuell = signal.getsignal(signal.SIGTERM)
            assert callable(aktuell) and aktuell is not merken
            aktuell(signal.SIGTERM, None)
        assert signal.getsignal(signal.SIGTERM) is merken
    finally:
        signal.signal(signal.SIGTERM, alter_handler)

    assert name not in fake.aufgaben, "SIGTERM-Handler muss aktive Test-Aufgabe löschen"
    assert name not in st._AKTIVE_AUFGABEN
    assert weitergeleitet == [signal.SIGTERM]


def test_schtasks_helfer_nutzt_fensterlos_und_zeitlimit() -> None:
    """Alle ``subprocess.run``-Stellen in ``tests/hilfen/schtasks_test.py`` sind fensterlos mit Zeitlimit."""
    datei = TESTS / "hilfen" / "schtasks_test.py"
    assert datei.is_file(), f"Zentrale schtasks-Test-Hilfe fehlt: {datei}"
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    starts = [
        k
        for k in ast.walk(baum)
        if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute) and k.func.attr == "run"
    ]
    assert starts, "Erwarte subprocess.run in tests/hilfen/schtasks_test.py"
    for aufruf in starts:
        hat_timeout = any(kw.arg == "timeout" for kw in aufruf.keywords)
        hat_fensterlos = any(
            kw.arg is None
            and isinstance(kw.value, ast.Call)
            and isinstance(kw.value.func, ast.Attribute)
            and kw.value.func.attr == "fensterlos"
            for kw in aufruf.keywords
        )
        assert hat_timeout, f"timeout fehlt in {datei}:{aufruf.lineno}"
        assert hat_fensterlos, f"**prozessbaum.fensterlos() fehlt in {datei}:{aufruf.lineno}"


# --- Echte Windows-Belege (GitHub-Windows-Läufer / Windows-PC) ---------------------


def _kind_pytest_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(SKILL), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


@pytest.mark.skipif(sys.platform != "win32", reason="schtasks gibt es nur unter Windows")
def test_windows_echt_fixture_hinterlaesst_null_aufgaben_bei_erfolg_fehler_und_abbruch(tmp_path: Path) -> None:
    """Echter Windows-Kind-Lauf: Erfolg, Fehlschlag und KeyboardInterrupt lassen 0 Test-Aufgaben zurück (#789)."""
    st = _schtasks_test_modul()
    kind_conftest = tmp_path / "conftest.py"
    kind_conftest.write_text(
        "import importlib.util\n"
        f"_spec = importlib.util.spec_from_file_location('_echt_conftest_789', {str(TESTS / 'conftest.py')!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "for _name in ('pytest_sessionstart', 'pytest_sessionfinish', 'schtasks_aufgabe'):\n"
        "    globals()[_name] = getattr(_mod, _name)\n",
        encoding="utf-8",
    )
    kind_test = tmp_path / "test_schtasks_kind_789.py"
    bericht = tmp_path / "namen.txt"
    kind_test.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "from to_spawn import hausmeister, hausmeister_aufgabe\n"
        "\n"
        "def _anlegen(monkeypatch, tmp_path, schtasks_aufgabe, zweck):\n"
        "    name = schtasks_aufgabe(zweck)\n"
        "    with Path(os.environ['BERICHT']).open('a', encoding='utf-8') as f:\n"
        "        f.write(name + '\\n')\n"
        "    monkeypatch.setattr(hausmeister_aufgabe, 'AUFGABE_NAME', name)\n"
        "    monkeypatch.setattr(hausmeister, 'altbestand_festhalten', lambda o: tmp_path / 'a.json', raising=False)\n"
        "    assert 'eingerichtet' in hausmeister_aufgabe.aufgabe_einrichten()\n"
        "    return name\n"
        "\n"
        "def test_erfolg(monkeypatch, tmp_path, schtasks_aufgabe):\n"
        "    _anlegen(monkeypatch, tmp_path, schtasks_aufgabe, 'ok')\n"
        "\n"
        "def test_fehler(monkeypatch, tmp_path, schtasks_aufgabe):\n"
        "    _anlegen(monkeypatch, tmp_path, schtasks_aufgabe, 'fail')\n"
        "    raise AssertionError('absichtlich rot')\n"
        "\n"
        "def test_abbruch(monkeypatch, tmp_path, schtasks_aufgabe):\n"
        "    _anlegen(monkeypatch, tmp_path, schtasks_aufgabe, 'int')\n"
        "    raise KeyboardInterrupt()\n",
        encoding="utf-8",
    )
    env = _kind_pytest_env()
    env["BERICHT"] = str(bericht)
    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            str(kind_test),
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        **prozessbaum.fensterlos(),
    )
    # Erwartet: 1 passed, 1 failed, 1 interrupted → Exit != 0, aber ALLE angelegten Aufgaben müssen weg sein!
    assert bericht.is_file(), (
        f"Kind-Lauf meldete keine Aufgaben (Exit {lauf.returncode}):\n{lauf.stdout}\n{lauf.stderr}"
    )
    namen = [z.strip() for z in bericht.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert len(namen) == 3, f"Erwartete 3 Aufgaben-Namen, bekam {namen}:\n{lauf.stdout}\n{lauf.stderr}"
    try:
        offen = set(st.offene_test_aufgaben())
        noch_da = [n for n in namen if n in offen]
        assert noch_da == [], f"Nach Kind-Lauf blieben Test-Aufgaben stehen: {noch_da}"
    finally:
        for name in namen:
            st.loeschen(name)


@pytest.mark.skipif(sys.platform != "win32", reason="schtasks gibt es nur unter Windows")
def test_windows_echt_harter_abbruch_und_altrest_werden_aufgeraeumt(tmp_path: Path) -> None:
    """Echter Windows-Beleg: hart getöteter Kind-Prozess (≥1 offen) + Alt-Rest-Name → verwaiste_raeumen() → 0 (#789)."""
    st = _schtasks_test_modul()
    bereit = tmp_path / "bereit.txt"
    altbestand = tmp_path / "altbestand.json"
    skript = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(SKILL)!r})\n"
        f"sys.path.insert(0, {str(TESTS / 'hilfen')!r})\n"
        "import schtasks_test\n"
        "from to_spawn import hausmeister, hausmeister_aufgabe\n"
        "name = schtasks_test.neuer_name('kill')\n"
        "hausmeister_aufgabe.AUFGABE_NAME = name\n"
        f"hausmeister.altbestand_festhalten = lambda o: Path({str(altbestand)!r})\n"
        "res = hausmeister_aufgabe.aufgabe_einrichten()\n"
        "assert 'eingerichtet' in res, res\n"
        f"Path({str(bereit)!r}).write_text(name, encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", skript],
        cwd=str(SKILL),
        env=_kind_pytest_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    kill_name: str | None = None
    alt_name = f"{st.ALT_PRAEFIX}{uuid.uuid4().hex[:6]}"
    try:
        ende = time.monotonic() + 60
        while not bereit.exists() and time.monotonic() < ende:
            if proc.poll() is not None:
                aus, err = proc.communicate()
                pytest.fail(f"Kind-Prozess brach vorzeitig ab ({proc.returncode}): {aus}\n{err}")
            time.sleep(0.1)
        assert bereit.exists(), "Kind-Prozess meldete Aufgaben-Namen nicht rechtzeitig"
        kill_name = bereit.read_text(encoding="utf-8").strip()

        # Solange das Kind noch lebt, gilt seine Aufgabe NICHT als verwaist (xdist-Schutz).
        assert kill_name in st.offene_test_aufgaben()
        assert kill_name not in st.verwaiste_aufgaben()

        # Harter Abbruch (TerminateProcess — kein finally/atexit möglich): Rest bleibt zunächst stehen (≥1).
        proc.kill()
        proc.communicate(timeout=15)
        assert kill_name in st.offene_test_aufgaben()
        assert kill_name in st.verwaiste_aufgaben()

        # Paralleler verschachtelter Kind-pytest-Lauf (wie test_pt_reste_177) darf weder die fremde
        # Waise stehlen noch selbst wegen ihr scheitern (#789 Fixrunde 2).
        kind_pt = tmp_path / "kind_pt"
        kind_pt.mkdir()
        (kind_pt / "conftest.py").write_text(
            "import importlib.util\n"
            f"_spec = importlib.util.spec_from_file_location('_echt_conftest_789_pt', {str(TESTS / 'conftest.py')!r})\n"
            "_mod = importlib.util.module_from_spec(_spec)\n"
            "_spec.loader.exec_module(_mod)\n"
            "for _name in ('pytest_configure', 'pytest_sessionstart', 'pytest_sessionfinish', 'pytest_unconfigure'):\n"
            "    if hasattr(_mod, _name):\n"
            "        globals()[_name] = getattr(_mod, _name)\n",
            encoding="utf-8",
        )
        (kind_pt / "test_harmlos.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        kind_lauf = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", str(kind_pt)],
            cwd=str(kind_pt),
            env=_kind_pytest_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            **prozessbaum.fensterlos(),
        )
        assert kind_lauf.returncode == 0, (
            f"Verschachtelter Kind-pytest brach ab:\n{kind_lauf.stdout}\n{kind_lauf.stderr}"
        )
        assert kill_name in st.verwaiste_aufgaben(), "Verschachtelter Kind-pytest hat fremde Waise abgeräumt"

        # Zusätzlich Alt-Präfix-Rest wie „to-spawn Hausmeister Test 71ce15“ anlegen.
        alt_erg = st._lauf(
            "/Create",
            "/F",
            "/SC",
            "DAILY",
            "/ST",
            "03:30",
            "/TN",
            alt_name,
            "/TR",
            f'"{sys.executable}" -c pass',
        )
        assert alt_erg is not None and alt_erg.returncode == 0, alt_erg
        assert alt_name in st.offene_test_aufgaben()

        # Aufräum-Lauf: beide Reste (tote PID + Alt-Präfix) verschwinden → 0 offen.
        st.verwaiste_raeumen()
        offen_danach = set(st.offene_test_aufgaben())
        assert kill_name not in offen_danach, f"Hart abgebrochene Aufgabe nicht geräumt: {kill_name}"
        assert alt_name not in offen_danach, f"Alt-Präfix-Aufgabe nicht geräumt: {alt_name}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=15)
        if kill_name:
            st.loeschen(kill_name)
        st.loeschen(alt_name)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Kernel-Mutex gibt es nur unter Windows")
def test_windows_echt_waechter_mutex_exklusiv() -> None:
    """Echter Windows-Kernel-Mutex: nur ein Prozess hält den Wächter, nach Freigabe wieder frei (#789)."""
    st = _schtasks_test_modul()
    mutex_name = f"Local\\to-spawn-schtasks-probe-{uuid.uuid4().hex[:12]}"
    sperre = st.waechter_nehmen(mutex_name)
    assert sperre is not None
    try:
        # Zweiter Versuch im selben Prozess und in einem Kindprozess liefert None.
        assert st.waechter_nehmen(mutex_name) is None
        probe_code = (
            f"import sys; sys.path.insert(0, {str(SKILL)!r}); sys.path.insert(0, {str(TESTS / 'hilfen')!r}); "
            f"import schtasks_test; assert schtasks_test.waechter_nehmen({mutex_name!r}) is None"
        )
        probe = subprocess.run(
            [sys.executable, "-c", probe_code],
            cwd=str(SKILL),
            env=_kind_pytest_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            **prozessbaum.fensterlos(),
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
    finally:
        sperre.freigeben()

    erneut = st.waechter_nehmen(mutex_name)
    assert erneut is not None, "Nach freigeben() muss der Mutex wieder frei sein"
    erneut.freigeben()
