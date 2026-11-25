"""Ort-Wahl (#802, Beschluss E35): jeder Testlauf, im Zweifel GitHub, Schwelle nach Hardware und OS.

Deckt alle Akzeptanzpunkte ab:
- Aufgezeichnete Messwerte einer kleinen Linux-Maschine (15 GB) mit 30 % frei ergeben ``github``,
  die gleichen Werte bei 64 GB ergeben ``lokal``.
- Die gleiche freie Menge ergibt unter Windows eher ``github`` als unter Linux (Test mit
  aufgezeichneten Werten beider Systeme).
- Fehlende, kaputte, unplausible oder stark schwankende Messung sowie Messfehler ergeben
  ``github`` mit Grund im Log.
- Kleine Prüfung je Ticket (``pruef.pruefen``) und große Läufe (``komplett_lauf`` / ``ort_wahl``)
  benutzen dieselbe Ort-Wahl (ein Aufruf, ein Ergebnis).
- Messung für Linux und Windows hinter derselben Schnittstelle (``speicher.system_speicher_mib`` /
  ``ort_wahl.messen_system``), Windows kann bei genug Speicher ``lokal`` ergeben.
- Hat ein Repo keinen GitHub-Lauf, läuft der Test lokal mit Hinweis-Zeile im Log, ohne Mail.
- Rechenregel nur an einer Stelle (``ort_wahl.berechne_schwelle``); kein ``msvcrt``/``fcntl``
  außerhalb ``to_spawn/sperre.py``.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, komplett_lauf, ort_wahl, prozessbaum, pruef, speicher
from to_spawn.pruefplan import Moment


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=Bau", "-c", "user.email=bau@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        **prozessbaum.fensterlos(),
    )
    return erg.stdout.strip()


def _probe_repo(ordner: Path, *, mit_github_workflow: bool = False) -> Path:
    """Minimales Python-Repo für kleine und komplette Prüfungen (mit/ohne GitHub-Workflow)."""
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text("def plus(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent("""\
            import pathlib, sys
            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from rechnen import plus

            def test_plus() -> None:
                assert plus(2, 3) == 5
        """),
        encoding="utf-8",
    )
    if mit_github_workflow:
        wf = ordner / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "tests.yml").write_text(
            "name: Tests\non: [push, pull_request]\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
            encoding="utf-8",
        )
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "basis")
    _git(ordner, "tag", "base")
    return ordner


def _meminfo_schreiben(pfad: Path, *, gesamt_mib: int, frei_mib: int) -> Path:
    pfad.write_text(
        f"MemTotal:        {gesamt_mib * 1024} kB\nMemAvailable:    {frei_mib * 1024} kB\n",
        encoding="utf-8",
    )
    return pfad


# --- 1. Akzeptanz 1: 15 GB (30 % frei) → github, 64 GB (30 % frei) → lokal ------


@pytest.mark.parametrize("kerne", [1, 4, 8])
def test_linux_15gb_30_prozent_frei_ergibt_github_64gb_ergibt_lokal(
    kerne: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kleine Linux-Maschine (15 GB, 30 % frei) → ``github``; 64 GB (30 % frei) → ``lokal``."""
    gesamt_klein = 15 * 1024  # 15 GiB = 15360 MiB
    frei_klein = int(gesamt_klein * 0.30)  # 4608 MiB (30 %)
    punkt_klein = ort_wahl.Messpunkt(
        frei_mib=frei_klein,
        gesamt_mib=gesamt_klein,
        kerne=kerne,
        plattform="linux",
        last_1m=0.2,
        bau_sessions=0,
    )

    gesamt_gross = 64 * 1024  # 64 GiB = 65536 MiB
    frei_gross = int(gesamt_gross * 0.30)  # 19660 MiB (30 %)
    punkt_gross = ort_wahl.Messpunkt(
        frei_mib=frei_gross,
        gesamt_mib=gesamt_gross,
        kerne=kerne,
        plattform="linux",
        last_1m=0.2,
        bau_sessions=0,
    )

    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        erg_klein = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt_klein)
        erg_gross = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt_gross)

    assert erg_klein.ort == ort_wahl.ORT_GITHUB
    assert "speicher" in erg_klein.grund.lower()
    assert erg_gross.ort == ort_wahl.ORT_LOKAL
    assert erg_klein.schwelle is not None and erg_gross.schwelle is not None
    # Anteilige Schwelle ist bei kleiner Maschine strenger (höherer Prozentanteil) als bei großer.
    assert (erg_klein.schwelle.min_frei_mib / gesamt_klein) > (erg_gross.schwelle.min_frei_mib / gesamt_gross)


# --- 2. Akzeptanz 2: Windows kippt bei gleicher freier Menge eher zu GitHub ------


@pytest.mark.parametrize(
    ("gesamt_mib", "kerne", "frei_mib"),
    [
        (16 * 1024, 4, 8192),  # 16 GB, 8 GB frei: Linux lokal, Windows github
        (32 * 1024, 8, 12000),  # 32 GB, ~11,7 GB frei: Linux lokal, Windows github
        (64 * 1024, 8, 19660),  # 64 GB, 30 % frei: Linux lokal, Windows github
    ],
)
def test_windows_kippt_bei_gleicher_freier_menge_eher_zu_github_als_linux(
    gesamt_mib: int,
    kerne: int,
    frei_mib: int,
) -> None:
    """Windows zieht höhere Grundlast ab und kippt bei gleicher freier Menge eher zu ``github``."""
    schwelle_linux = ort_wahl.berechne_schwelle(gesamt_mib, kerne, "linux", config.DEFAULTS)
    schwelle_win = ort_wahl.berechne_schwelle(gesamt_mib, kerne, "win32", config.DEFAULTS)
    assert schwelle_win.os_grundlast_mib > schwelle_linux.os_grundlast_mib
    assert schwelle_win.min_frei_mib > schwelle_linux.min_frei_mib

    punkt_linux = ort_wahl.Messpunkt(frei_mib=frei_mib, gesamt_mib=gesamt_mib, kerne=kerne, plattform="linux")
    punkt_win = ort_wahl.Messpunkt(frei_mib=frei_mib, gesamt_mib=gesamt_mib, kerne=kerne, plattform="win32")

    erg_linux = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt_linux)
    erg_win = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt_win)

    assert erg_linux.ort == ort_wahl.ORT_LOKAL
    assert erg_win.ort == ort_wahl.ORT_GITHUB
    assert "speicher" in erg_win.grund.lower()


def test_windows_ergibt_lokal_bei_genug_freiem_speicher() -> None:
    """Windows meldet nicht mehr blind ``github``: bei ausreichend Speicher ergibt die Ort-Wahl ``lokal``."""
    punkt_win_reichlich = ort_wahl.Messpunkt(
        frei_mib=28 * 1024,
        gesamt_mib=64 * 1024,
        kerne=8,
        plattform="win32",
    )
    erg = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt_win_reichlich)
    assert erg.ort == ort_wahl.ORT_LOKAL
    assert erg.grund


# --- 3. Akzeptanz 3: Fehlende, kaputte, schwankende Messung / Fehler → github ----


@pytest.mark.parametrize(
    ("punkt", "erwartetes_stichwort"),
    [
        (ort_wahl.Messpunkt(frei_mib=None, gesamt_mib=65536, kerne=8, plattform="linux"), "speicher"),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=None, kerne=8, plattform="linux"), "gesamt"),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=0, kerne=8, plattform="linux"), "gesamt"),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=65536, kerne=None, plattform="linux"), "kern"),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=65536, kerne=0, plattform="linux"), "kern"),
        (ort_wahl.Messpunkt(frei_mib=-100, gesamt_mib=65536, kerne=8, plattform="linux"), "ungültig"),
        (ort_wahl.Messpunkt(frei_mib=70000, gesamt_mib=65536, kerne=8, plattform="linux"), "unplausibel"),
        (
            ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=65536, kerne=8, plattform="linux", last_1m=float("nan")),
            "last",
        ),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=65536, kerne=8, plattform="linux", last_1m=-1.0), "last"),
        (ort_wahl.Messpunkt(frei_mib=32768, gesamt_mib=65536, kerne=8, plattform=""), "betriebssystem"),
    ],
)
def test_fehlende_oder_kaputte_messung_ergibt_github_mit_grund(
    punkt: ort_wahl.Messpunkt,
    erwartetes_stichwort: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Im Zweifel GitHub: jede fehlende oder kaputte Messung liefert ``github`` mit Grund im Log."""
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        erg = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messung=punkt)
    assert erg.ort == ort_wahl.ORT_GITHUB
    assert erg.grund.strip() != ""
    assert erwartetes_stichwort in erg.grund.lower()
    assert any("github" in r.getMessage().lower() for r in caplog.records)


def test_messfehler_exception_ergibt_github_mit_grund(caplog: pytest.LogCaptureFixture) -> None:
    """Wirft die Messquelle eine Ausnahme, fängt die Ort-Wahl sie ab und liefert ``github`` mit Grund."""

    def kaputte_messung() -> ort_wahl.Messpunkt:
        raise OSError("Kernel-Speicherabfrage fehlgeschlagen")

    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        erg = ort_wahl.entscheiden(
            config.DEFAULTS,
            art="klein",
            messen=kaputte_messung,
            schlafen=lambda _: None,
        )
    assert erg.ort == ort_wahl.ORT_GITHUB
    assert "fehler" in erg.grund.lower() or "fehlgeschlagen" in erg.grund.lower()
    assert any("github" in r.getMessage().lower() for r in caplog.records)


def test_stark_schwankende_messreihe_ergibt_github_mit_grund(caplog: pytest.LogCaptureFixture) -> None:
    """Schwankt der freie Speicher zwischen Messpunkten stark, gilt die Messung als unsicher → ``github``."""
    reihe = [
        ort_wahl.Messpunkt(frei_mib=13000, gesamt_mib=16384, kerne=4, plattform="linux"),
        ort_wahl.Messpunkt(frei_mib=9000, gesamt_mib=16384, kerne=4, plattform="linux"),
    ]
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        erg = ort_wahl.entscheiden(config.DEFAULTS, art="klein", messreihe=reihe)
    assert erg.ort == ort_wahl.ORT_GITHUB
    assert "schwankt" in erg.grund.lower()
    assert any("schwankt" in r.getMessage().lower() for r in caplog.records)


# --- 4. Eine Mess-Schnittstelle für Linux und Windows ----------------------------


def test_messen_system_linux_und_windows_hinter_derselben_schnittstelle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``speicher.system_speicher_mib`` und ``ort_wahl.messen_system`` bedienen Linux und Windows gleich."""
    meminfo = _meminfo_schreiben(tmp_path / "meminfo", gesamt_mib=32768, frei_mib=20480)
    loadavg = tmp_path / "loadavg"
    loadavg.write_text("0.45 0.40 0.35 1/200 42\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_LOADAVG", str(loadavg))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_KERNE", "8")

    punkt_linux = ort_wahl.messen_system(plattform="linux")
    assert punkt_linux.gesamt_mib == 32768
    assert punkt_linux.frei_mib == 20480
    assert punkt_linux.kerne == 8
    assert punkt_linux.plattform == "linux"
    assert punkt_linux.last_1m == pytest.approx(0.45)

    # Windows ohne MEMINFO-Override nutzt den Windows-Kernel-Pfad (im Test über Monkeypatch gestellt).
    monkeypatch.delenv("TO_SPAWN_SPEICHER_MEMINFO", raising=False)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (24576, 65536))
    punkt_win = ort_wahl.messen_system(plattform="win32")
    assert punkt_win.gesamt_mib == 65536
    assert punkt_win.frei_mib == 24576
    assert punkt_win.kerne == 8
    assert punkt_win.plattform == "win32"


# --- 5. Akzeptanz 4 & Punkt 5: Kleine Prüfung + große Läufe nutzen dieselbe Ort-Wahl


def test_pruef_und_komplett_fragen_dieselbe_ort_wahl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kleine Prüfung je Ticket (``pruef.pruefen``) und große Läufe (``komplett_lauf``) fragen beide ``ort_wahl.entscheiden``."""
    aufrufe: list[str] = []
    echt_entscheiden = ort_wahl.entscheiden

    def spy_entscheiden(*args: Any, **kwargs: Any) -> ort_wahl.WahlErgebnis:
        aufrufe.append(str(kwargs.get("art", "klein")))
        return echt_entscheiden(*args, **kwargs)

    monkeypatch.setattr(ort_wahl, "entscheiden", spy_entscheiden)

    # Kleine Prüfung je Ticket
    repo = _probe_repo(tmp_path / "ticket-repo", mit_github_workflow=False)
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 0
    assert urteil.zeile.get("ort") == ort_wahl.ORT_LOKAL

    # Große Komplett-Prüfung je Spec
    zaehl = tmp_path / "komplett.txt"
    code = f"import pathlib; pathlib.Path({str(zaehl)!r}).write_text('ok', encoding='utf-8')"
    befehl = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    konfig = config._mische(
        config.DEFAULTS,
        {"pruefung": {"schnell": "true", "komplett": befehl}, "staging": {"url": "", "ohne": True}},
    )
    erg = komplett_lauf.komplett_einmal(repo, 802, Moment.VOR_LIVE, konfig)
    assert erg.ok is True and erg.gelaufen is True
    assert zaehl.read_text(encoding="utf-8") == "ok"

    assert aufrufe == ["klein", "gross"]


def test_knapper_speicher_mit_github_workflow_stoppt_lokalen_lauf_oder_nutzt_github_befehl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knapper Speicher + Repo mit GitHub-Workflow: führt nicht lokal aus, sondern geht auf ``github``."""
    # 15 GB Maschine mit 30 % frei → github
    meminfo = _meminfo_schreiben(tmp_path / "meminfo_knapp", gesamt_mib=15 * 1024, frei_mib=int(15 * 1024 * 0.30))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_KERNE", "4")

    # Fall A: GitHub-Workflow vorhanden, aber kein lokaler github_befehl → kein lokaler Lauf (Gate-Fehler mit ort=github).
    repo_wf = _probe_repo(tmp_path / "mit-wf", mit_github_workflow=True)
    urteil_wf = pruef.pruefen(repo_wf, "base", "HEAD", art="schnell")
    assert urteil_wf.exit == 2
    assert urteil_wf.zeile.get("ort") == ort_wahl.ORT_GITHUB
    assert "github" in urteil_wf.zeile.get("fehler_kurz", "").lower()

    # Fall B: Repo hat einen konfigurierten github_befehl → führt diesen aus statt lokal.
    gh_marker = tmp_path / "gh_marker.txt"
    gh_code = f"import pathlib; pathlib.Path({str(gh_marker)!r}).write_text('ueber-github', encoding='utf-8')"
    gh_befehl = f"{shlex.quote(sys.executable)} -c {shlex.quote(gh_code)}"
    repo_cmd = _probe_repo(tmp_path / "mit-gh-cmd", mit_github_workflow=True)
    (repo_cmd / ".to-spawn").mkdir()
    (repo_cmd / ".to-spawn" / "config.json").write_text(
        json.dumps({"ort_wahl": {"github_befehl": gh_befehl}}),
        encoding="utf-8",
    )
    _git(repo_cmd, "add", ".to-spawn/config.json")
    _git(repo_cmd, "commit", "-qm", "konfig")

    urteil_cmd = pruef.pruefen(repo_cmd, "base", "HEAD", art="schnell")
    assert urteil_cmd.exit == 0
    assert urteil_cmd.zeile.get("ort") == ort_wahl.ORT_GITHUB
    assert gh_marker.read_text(encoding="utf-8") == "ueber-github"


def test_repo_ohne_github_lauf_faellt_lokal_zurueck_mit_hinweis_im_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Punkt 5: Hat das Repo keinen GitHub-Lauf, läuft der Test trotz knapper Lage lokal mit Hinweis im Log."""
    meminfo = _meminfo_schreiben(tmp_path / "meminfo_knapp", gesamt_mib=15 * 1024, frei_mib=int(15 * 1024 * 0.30))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_KERNE", "4")

    repo_ohne = _probe_repo(tmp_path / "ohne-github", mit_github_workflow=False)
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        wahl = ort_wahl.entscheiden(config.DEFAULTS, art="klein", repo=repo_ohne)
        urteil = pruef.pruefen(repo_ohne, "base", "HEAD", art="schnell")

    assert wahl.ort == ort_wahl.ORT_LOKAL
    assert wahl.fallback_lokal is True
    assert wahl.empfohlen == ort_wahl.ORT_GITHUB
    assert urteil.exit == 0
    assert urteil.zeile.get("ort") == ort_wahl.ORT_LOKAL
    assert any("keinen github-lauf" in r.getMessage().lower() for r in caplog.records)


# --- 6. CLI: ein Aufruf, ein Ergebnis (lokal / github) --------------------------


def test_cli_ort_wahl_art_klein_und_gross_mit_aufgezeichneten_werten(tmp_path: Path) -> None:
    """``to_spawn.py ort-wahl --art klein --messreihe …`` liefert ``lokal`` oder ``github``."""
    klein_15gb = tmp_path / "linux_15gb.json"
    klein_15gb.write_text(
        json.dumps([{"frei_mib": 4608, "gesamt_mib": 15360, "kerne": 4, "plattform": "linux"}]),
        encoding="utf-8",
    )
    gross_64gb = tmp_path / "linux_64gb.json"
    gross_64gb.write_text(
        json.dumps([{"frei_mib": 19660, "gesamt_mib": 65536, "kerne": 4, "plattform": "linux"}]),
        encoding="utf-8",
    )

    def aufruf(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), "ort-wahl", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )

    r_klein = aufruf("--art", "klein", "--messreihe", str(klein_15gb))
    assert r_klein.returncode == 0, r_klein.stdout + r_klein.stderr
    assert r_klein.stdout.strip() == "github"

    r_gross = aufruf("--art", "klein", "--messreihe", str(gross_64gb), "--json")
    assert r_gross.returncode == 0, r_gross.stdout + r_gross.stderr
    daten: dict[str, Any] = json.loads(r_gross.stdout)
    assert daten["ort"] == "lokal"
    assert daten["schwelle"]["min_frei_mib"] > 0


# --- 7. Komplett-Lauf mit knappem Speicher, Windows-Ausfall & eine Rechenregel ---


def test_komplett_lauf_knapper_speicher_mit_github_workflow_und_github_befehl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``komplett_lauf`` nutzt dieselbe Ort-Wahl: bei knappem RAM + GitHub-Workflow läuft nichts lokal."""
    meminfo = _meminfo_schreiben(tmp_path / "meminfo_knapp", gesamt_mib=15 * 1024, frei_mib=int(15 * 1024 * 0.30))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_KERNE", "4")

    lokal_marker = tmp_path / "lokal_darf_nicht.txt"
    lokal_code = f"import pathlib; pathlib.Path({str(lokal_marker)!r}).write_text('falsch', encoding='utf-8')"
    lokal_cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(lokal_code)}"

    # Fall A: Repo mit GitHub-Workflow, ohne github_befehl → kein lokaler Lauf (ok=False, Gate-Fehler).
    repo_a = _probe_repo(tmp_path / "komplett-wf", mit_github_workflow=True)
    konfig_a = config._mische(
        config.DEFAULTS,
        {"pruefung": {"schnell": "true", "komplett": lokal_cmd}, "staging": {"url": "", "ohne": True}},
    )
    erg_a = komplett_lauf.komplett_einmal(repo_a, 802, Moment.VOR_LIVE, konfig_a)
    assert erg_a.ok is False
    assert not lokal_marker.exists()
    assert "github" in erg_a.grund.lower()

    # Fall B: Mit konfiguriertem github_befehl_komplett → führt diesen aus statt lokal.
    gh_marker = tmp_path / "gh_komplett.txt"
    gh_code = f"import pathlib; pathlib.Path({str(gh_marker)!r}).write_text('gh-komplett', encoding='utf-8')"
    gh_cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(gh_code)}"
    konfig_b = config._mische(
        konfig_a,
        {"ort_wahl": {"github_befehl_komplett": gh_cmd}},
    )
    erg_b = komplett_lauf.komplett_einmal(repo_a, 802, Moment.VOR_LIVE, konfig_b)
    assert erg_b.ok is True and erg_b.gelaufen is True
    assert not lokal_marker.exists()
    assert gh_marker.read_text(encoding="utf-8") == "gh-komplett"


def test_windows_messung_ausfall_ergibt_github_mit_grund(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scheitert ``GlobalMemoryStatusEx`` unter Windows, liefert die Ort-Wahl ``github`` mit Grund."""
    monkeypatch.delenv("TO_SPAWN_SPEICHER_MEMINFO", raising=False)
    monkeypatch.setattr(speicher, "windows_speicher_mib", lambda: (None, None))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_KERNE", "8")
    with caplog.at_level(logging.INFO, logger="to_spawn.ort_wahl"):
        erg = ort_wahl.entscheiden(config.DEFAULTS, art="klein", plattform="win32", schlafen=lambda _: None)
    assert erg.ort == ort_wahl.ORT_GITHUB
    assert erg.grund.strip() != ""
    assert any("github" in r.getMessage().lower() for r in caplog.records)


def test_rechenregel_nur_an_einer_stelle() -> None:
    """Die Schwellen-Rechenregel (``os_grundlast_*``) lebt nur in ``to_spawn/ort_wahl.py`` (Defaults in ``config.py``)."""
    treffer: list[str] = []
    for datei in sorted((SKILL / "to_spawn").rglob("*.py")):
        rel = datei.relative_to(SKILL).as_posix()
        text = datei.read_text(encoding="utf-8")
        if "os_grundlast_windows_mib" in text or "berechne_schwelle" in text:
            treffer.append(rel)
    assert treffer == ["to_spawn/config.py", "to_spawn/ort_wahl.py"]
