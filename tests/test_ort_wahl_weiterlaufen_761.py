"""Ort-Wahl Teil 2/2 (#761, Beschluss E34): Weiterlaufen nach Rundenende + Hinweis im Repo.

Deckt alle Akzeptanzpunkte ab:
- Werden Bau-Tickets durch eine große Runde auf dem Linux-Bau-Server gebremst (``speicher``)
  oder angehalten (``SIGSTOP``), laufen sie nach dem Ende der Runde automatisch weiter —
  ohne menschlichen Eingriff.
- Das Weiterlaufen gilt genauso bei Runden-Abbruch (Fehler/Exit != 0, toter Runden-Prozess)
  und bei Übergabe an GitHub (Freigabe sofort bei Übergabe, noch bevor GitHub fertig ist).
- Die Bereitschafts-Prüfung (:mod:`to_spawn.inventur`) prüft über :mod:`to_spawn.ort_wahl`,
  ob ein Repo eine große Runde hat und ob sie die Ort-Wahl nutzt: fehlende Runde oder fester
  Server-Lauf ohne Ort-Wahl erzeugen einen Hinweis; eine Runde mit Ort-Wahl (oder geplanter
  GitHub-Lauf) erzeugt keinen Hinweis. Der Skill verändert das Repo dabei nicht.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, inventur, ort_wahl, speicher, starter


def _test_skript(name: str, inhalt: str) -> Path:
    """Legt ein Test-Skript im Attrappen-Ordner ``TO_SPAWN_TEST_BIN`` ab (#1406)."""
    skript = Path(os.environ[starter.TEST_BIN_ENV]) / name
    skript.write_text(inhalt, encoding="utf-8")
    return skript


def _ruhig(zeit_s: float | None = None) -> ort_wahl.Messpunkt:
    return ort_wahl.Messpunkt(frei_mib=8192, last_1m=0.35, bau_sessions=0, zeit_s=zeit_s)


def _ruhige_reihe(minuten: int = 30, takt_s: int = 60) -> list[ort_wahl.Messpunkt]:
    anzahl = (minuten * 60) // takt_s + 1
    return [_ruhig(zeit_s=float(i * takt_s)) for i in range(anzahl)]


def _warte_auf_datei(pfad: Path, frist_s: float = 5.0) -> bool:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if pfad.is_file():
            return True
        time.sleep(0.05)
    return pfad.is_file()


def _starte_pausierbare_session(tmp_path: Path, name: str) -> tuple[subprocess.Popen[bytes], Path, Path]:
    """Startet einen echten Kindprozess, der nach Bereit-Marker kurz wartet und dann fertig meldet."""
    bereit = tmp_path / f"{name}_bereit.txt"
    fertig = tmp_path / f"{name}_fertig.txt"
    skript = _test_skript(
        f"{name}.py",
        (
            "import pathlib, time\n"
            f"pathlib.Path({str(bereit)!r}).write_text('bereit', encoding='utf-8')\n"
            "time.sleep(0.25)\n"
            f"pathlib.Path({str(fertig)!r}).write_text('weiterlief', encoding='utf-8')\n"
        ),
    )
    proc = starter.starte([sys.executable, str(skript)], notnagel=None)
    assert _warte_auf_datei(bereit), f"Kindprozess {name} meldete sich nicht bereit"
    return proc, bereit, fertig


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _minimal_repo(tmp_path: Path, name: str = "test-repo") -> Path:
    arbeit = tmp_path / name
    arbeit.mkdir()
    _git(arbeit, "init")
    (arbeit / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(arbeit, "add", "-A")
    return arbeit


def _inventur_fuer(repo: Path, tmp_path: Path) -> inventur.Inventur:
    heim = tmp_path / f"heim_{repo.name}"
    (heim / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (heim / ".claude.json").write_text("{}", encoding="utf-8")
    return inventur.erstelle(
        repo,
        claude_home=heim / ".claude",
        home_json=heim / ".claude.json",
        home=heim,
        environ={"PATH": ""},
    )


# --- 1. Weiterlaufen angehaltener Sessions nach Rundenende (Linux) --------------


@pytest.mark.braucht("posix")
def test_angehaltene_session_laeuft_nach_rundenende_automatisch_weiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Akzeptanz #761: eine angehaltene Session läuft nach dem Rundenende ohne Eingriff weiter."""
    if sys.platform != "linux":
        pytest.skip("Weiterlaufen nach Rundenende gilt nur auf dem Linux-Bau-Server")
    runde_datei = tmp_path / "runde.json"
    monkeypatch.setenv(ort_wahl.RUNDE_ENV, str(runde_datei))

    session, _bereit, fertig = _starte_pausierbare_session(tmp_path, "bau_session_normal")
    try:
        os.kill(session.pid, signal.SIGSTOP)
        time.sleep(0.35)
        assert not fertig.exists(), "Angehaltene Session darf während SIGSTOP nicht weiterlaufen"

        server_skript = _test_skript("runde_normal.py", "import time\ntime.sleep(0.15)\n")
        github_skript = _test_skript("gh_unbenutzt.py", "pass\n")

        ergebnis = ort_wahl.ausfuehren(
            [sys.executable, str(server_skript)],
            [sys.executable, str(github_skript)],
            config.DEFAULTS,
            messreihe=_ruhige_reihe(30, 60),
            lauf_messreihe=[_ruhig(0.0)],
            schlafen=lambda _: None,
            plattform="linux",
            angehaltene_pids=(session.pid,),
        )

        assert ergebnis.ort == ort_wahl.ORT_SERVER
        assert ergebnis.exit_code == 0
        assert session.pid in ergebnis.fortgesetzt_pids
        assert ort_wahl.runde_aktiv() is None
        assert _warte_auf_datei(fertig, frist_s=5.0), "Angehaltene Session lief nach Rundenende nicht weiter"
        assert session.wait(timeout=5.0) == 0
    finally:
        if session.poll() is None:
            os.kill(session.pid, signal.SIGCONT)
            session.terminate()
            session.wait(timeout=5.0)


@pytest.mark.braucht("posix")
def test_angehaltene_session_laeuft_auch_bei_runden_abbruch_weiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bricht die große Runde mit Fehler ab, läuft die angehaltene Session trotzdem weiter."""
    if sys.platform != "linux":
        pytest.skip("Weiterlaufen nach Rundenende gilt nur auf dem Linux-Bau-Server")
    runde_datei = tmp_path / "runde_abbruch.json"
    monkeypatch.setenv(ort_wahl.RUNDE_ENV, str(runde_datei))

    session, _bereit, fertig = _starte_pausierbare_session(tmp_path, "bau_session_abbruch")
    try:
        os.kill(session.pid, signal.SIGSTOP)
        server_skript = _test_skript("runde_kaputt.py", "import sys, time\ntime.sleep(0.1)\nsys.exit(3)\n")
        github_skript = _test_skript("gh_unbenutzt_2.py", "pass\n")

        ergebnis = ort_wahl.ausfuehren(
            [sys.executable, str(server_skript)],
            [sys.executable, str(github_skript)],
            config.DEFAULTS,
            messreihe=_ruhige_reihe(30, 60),
            lauf_messreihe=[_ruhig(0.0)],
            schlafen=lambda _: None,
            plattform="linux",
            angehaltene_pids=(session.pid,),
        )

        assert ergebnis.ort == ort_wahl.ORT_SERVER
        assert ergebnis.exit_code == 3
        assert session.pid in ergebnis.fortgesetzt_pids
        assert ort_wahl.runde_aktiv() is None
        assert _warte_auf_datei(fertig, frist_s=5.0), "Session lief nach Runden-Abbruch nicht weiter"
        assert session.wait(timeout=5.0) == 0
    finally:
        if session.poll() is None:
            os.kill(session.pid, signal.SIGCONT)
            session.terminate()
            session.wait(timeout=5.0)


@pytest.mark.braucht("posix")
def test_angehaltene_session_laeuft_bei_uebergabe_an_github_sofort_weiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geht die Runde an GitHub über, läuft die angehaltene Session sofort weiter — noch bevor GitHub endet."""
    if sys.platform != "linux":
        pytest.skip("Weiterlaufen nach Rundenende gilt nur auf dem Linux-Bau-Server")
    runde_datei = tmp_path / "runde_github.json"
    monkeypatch.setenv(ort_wahl.RUNDE_ENV, str(runde_datei))

    session, _bereit, fertig = _starte_pausierbare_session(tmp_path, "bau_session_github")
    gh_sah_weiterlauf = tmp_path / "gh_sah_weiterlauf.txt"
    try:
        os.kill(session.pid, signal.SIGSTOP)
        server_skript = _test_skript("runde_langsam_kippt.py", "import time\ntime.sleep(30)\n")
        # GitHub-Befehl wartet selbst auf das Weiterlaufen der Session — Beweis, dass sie
        # schon BEI der Übergabe freigegeben wurde und nicht erst nach dem GitHub-Lauf.
        github_skript = _test_skript(
            "gh_prueft_freigabe.py",
            (
                "import pathlib, sys, time\n"
                f"ziel = pathlib.Path({str(fertig)!r})\n"
                "ende = time.monotonic() + 5.0\n"
                "while time.monotonic() < ende and not ziel.is_file():\n"
                "    time.sleep(0.05)\n"
                "if not ziel.is_file():\n"
                "    sys.exit(1)\n"
                f"pathlib.Path({str(gh_sah_weiterlauf)!r}).write_text('frei', encoding='utf-8')\n"
            ),
        )

        ergebnis = ort_wahl.ausfuehren(
            [sys.executable, str(server_skript)],
            [sys.executable, str(github_skript)],
            config.DEFAULTS,
            messreihe=_ruhige_reihe(30, 60),
            lauf_messreihe=[
                _ruhig(0.0),
                ort_wahl.Messpunkt(frei_mib=8192, last_1m=5.5, bau_sessions=0, zeit_s=30.0),
            ],
            schlafen=lambda _: None,
            plattform="linux",
            abbruch_frist_s=1.0,
            angehaltene_pids=(session.pid,),
        )

        assert ergebnis.ort == ort_wahl.ORT_GITHUB
        assert ergebnis.abgebrochen is True
        assert ergebnis.uebergeben is True
        assert ergebnis.exit_code == 0
        assert session.pid in ergebnis.fortgesetzt_pids
        assert gh_sah_weiterlauf.read_text(encoding="utf-8") == "frei"
        assert ort_wahl.runde_aktiv() is None
        assert session.wait(timeout=5.0) == 0
    finally:
        if session.poll() is None:
            os.kill(session.pid, signal.SIGCONT)
            session.terminate()
            session.wait(timeout=5.0)


@pytest.mark.braucht("posix")
def test_session_anhalten_und_toter_runden_prozess_setzt_automatisch_fort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stirbt der Runden-Prozess hart (ohne Aufräumen), räumt ``runde_aktiv`` ab und setzt fort."""
    if sys.platform != "linux":
        pytest.skip("Weiterlaufen nach Rundenende gilt nur auf dem Linux-Bau-Server")
    runde_datei = tmp_path / "runde_tot.json"
    monkeypatch.setenv(ort_wahl.RUNDE_ENV, str(runde_datei))

    # Kurzlebigen Prozess als Runden-Inhaber nutzen, dann sauber abwarten → garantierte tote PID.
    halter_skript = _test_skript("halter_kurz.py", "import time\ntime.sleep(0.2)\n")
    halter = starter.starte([sys.executable, str(halter_skript)], notnagel=None)
    session, _bereit, fertig = _starte_pausierbare_session(tmp_path, "bau_session_crash")
    try:
        ort_wahl.runde_beginnen(pid=halter.pid, grund="Test-Absturz")
        assert ort_wahl.session_anhalten(session.pid) is True
        assert speicher.platz_frei(config.DEFAULTS)[0] is False

        halter.wait(timeout=5.0)
        # Nach dem Tod des Runden-Prozesses erkennt runde_aktiv() das Ende und setzt fort.
        assert ort_wahl.runde_aktiv() is None
        assert not runde_datei.exists()
        assert _warte_auf_datei(fertig, frist_s=5.0), "Session blieb nach totem Runden-Prozess hängen"
        assert session.wait(timeout=5.0) == 0
        assert speicher.platz_frei(config.DEFAULTS)[0] is True
    finally:
        if halter.poll() is None:
            halter.terminate()
            halter.wait(timeout=5.0)
        if session.poll() is None:
            os.kill(session.pid, signal.SIGCONT)
            session.terminate()
            session.wait(timeout=5.0)


@pytest.mark.braucht("posix")
def test_gebremste_bau_session_in_auf_platz_warten_laeuft_nach_runde_weiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein Bau-Ticket in ``speicher.auf_platz_warten`` wartet während der Runde und läuft danach selbst weiter."""
    if sys.platform != "linux":
        pytest.skip("Weiterlaufen nach Rundenende gilt nur auf dem Linux-Bau-Server")
    runde_datei = tmp_path / "runde_warten.json"
    monkeypatch.setenv(ort_wahl.RUNDE_ENV, str(runde_datei))

    server_gestartet = tmp_path / "server_gestartet.txt"
    server_freigabe = tmp_path / "server_ende.txt"
    server_skript = _test_skript(
        "runde_blockiert_warter.py",
        (
            "import pathlib, time\n"
            f"pathlib.Path({str(server_gestartet)!r}).write_text('laeuft', encoding='utf-8')\n"
            f"ende = pathlib.Path({str(server_freigabe)!r})\n"
            "frist = time.monotonic() + 10.0\n"
            "while time.monotonic() < frist and not ende.is_file():\n"
            "    time.sleep(0.05)\n"
        ),
    )
    github_skript = _test_skript("gh_unbenutzt_3.py", "pass\n")

    lauf_ergebnis: list[ort_wahl.LaufErgebnis] = []

    def runde_fahren() -> None:
        lauf_ergebnis.append(
            ort_wahl.ausfuehren(
                [sys.executable, str(server_skript)],
                [sys.executable, str(github_skript)],
                config.DEFAULTS,
                messreihe=_ruhige_reihe(30, 60),
                lauf_messreihe=[_ruhig(0.0)],
                schlafen=lambda _: None,
                plattform="linux",
            )
        )

    thread = threading.Thread(target=runde_fahren, daemon=True)
    thread.start()
    try:
        assert _warte_auf_datei(server_gestartet), "Server-Runde startete nicht"
        # Warten, bis der Runden-Merker sichtbar ist (maximal 2 s).
        frist = time.monotonic() + 2.0
        while time.monotonic() < frist and ort_wahl.runde_aktiv() is None:
            time.sleep(0.02)

        frei_waehrend, grund_waehrend = speicher.platz_frei(config.DEFAULTS)
        assert frei_waehrend is False
        assert speicher.GROSSE_RUNDE_TEXT in grund_waehrend

        def schlafen_und_beenden(_: float) -> None:
            # Simuliert das Weiterlaufen der Zeit: beim ersten Wartetakt endet die Runde.
            server_freigabe.write_text("ende", encoding="utf-8")
            thread.join(timeout=5.0)

        zyklen = speicher.auf_platz_warten(
            config.DEFAULTS,
            wer="bau 761",
            schlafen=schlafen_und_beenden,
            takt_s=1,
        )
        assert zyklen == 1
        assert lauf_ergebnis and lauf_ergebnis[0].ort == ort_wahl.ORT_SERVER
    finally:
        server_freigabe.write_text("ende", encoding="utf-8")
        thread.join(timeout=5.0)


def test_windows_zweige_unberuehrt_bei_anhalten_und_runde_aktiv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unter Windows greifen weder ``runde_aktiv`` noch ``session_anhalten`` ein."""
    monkeypatch.setattr(ort_wahl.sys, "platform", "win32")
    assert ort_wahl.runde_aktiv() is None
    assert ort_wahl.session_anhalten(os.getpid()) is False


# --- 2. Hinweis in der Bereitschafts-Prüfung (inventur.py + ort_wahl.py) --------


def test_inventur_hinweis_bei_repo_ohne_grosse_runde_und_ohne_umbau(tmp_path: Path) -> None:
    """Akzeptanz #761: Repo ohne große Runde bekommt einen Hinweis; Skill ändert nichts im Repo."""
    repo = _minimal_repo(tmp_path, "ohne-runde")
    vorher = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)

    inv = _inventur_fuer(repo, tmp_path)

    runde = inv.projekt["grosse_runde"]
    assert runde["status"] == "fehlt"
    assert runde["ok"] is False
    assert runde["hinweis"]
    assert any("Große Testrunde" in z and "ort-wahl" in z for z in inv.tote_winkel)
    assert "Große Testrunde" in inv.text()

    # Der Skill baut nichts heimlich um.
    nachher = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if ".git" not in p.parts)
    assert nachher == vorher
    assert not (repo / ".to-spawn").exists()


def test_inventur_hinweis_bei_grosser_runde_fest_auf_server_ohne_ort_wahl(tmp_path: Path) -> None:
    """Läuft die große Runde fest auf dem Server ohne Ort-Wahl, bekommt der Nutzer einen Hinweis."""
    repo_cfg = _minimal_repo(tmp_path, "komplett-ohne-ort-wahl")
    (repo_cfg / ".to-spawn").mkdir()
    (repo_cfg / ".to-spawn" / "config.json").write_text(
        json.dumps({"pruefung": {"komplett": "pytest -q -n 4 tests/"}}),
        encoding="utf-8",
    )
    inv_cfg = _inventur_fuer(repo_cfg, tmp_path)
    assert inv_cfg.projekt["grosse_runde"]["status"] == "ohne_ort_wahl"
    assert any("ohne Ort-Wahl" in z for z in inv_cfg.tote_winkel)

    # Auch ein Nachtrunden-Skript mit reinem Kommentar „# ort-wahl“ gilt als ohne Ort-Wahl.
    repo_skript = _minimal_repo(tmp_path, "skript-ohne-ort-wahl")
    (repo_skript / "scripts").mkdir()
    (repo_skript / "scripts" / "nachtrunde.sh").write_text(
        "#!/usr/bin/env bash\n# TODO: später vielleicht ort-wahl nutzen\npytest -q tests/\n",
        encoding="utf-8",
    )
    inv_skript = _inventur_fuer(repo_skript, tmp_path)
    assert inv_skript.projekt["grosse_runde"]["status"] == "ohne_ort_wahl"
    assert any("ohne Ort-Wahl" in z and "nachtrunde.sh" in z for z in inv_skript.tote_winkel)


def test_inventur_kein_hinweis_bei_grosser_runde_mit_ort_wahl(tmp_path: Path) -> None:
    """Akzeptanz #761: Repo mit großer Runde + Ort-Wahl bekommt keinen Hinweis."""
    # Fall A: pruefung.komplett ruft to_spawn.py ort-wahl direkt auf.
    repo_direkt = _minimal_repo(tmp_path, "mit-ort-wahl-direkt")
    (repo_direkt / ".to-spawn").mkdir()
    (repo_direkt / ".to-spawn" / "config.json").write_text(
        json.dumps(
            {
                "pruefung": {
                    "komplett": (
                        "python3 to_spawn.py ort-wahl "
                        "--server-befehl 'pytest -q tests/' "
                        "--github-befehl 'gh workflow run tests.yml'"
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    inv_direkt = _inventur_fuer(repo_direkt, tmp_path)
    assert inv_direkt.projekt["grosse_runde"]["status"] == "mit_ort_wahl"
    assert inv_direkt.projekt["grosse_runde"]["ok"] is True
    assert not any("Große Testrunde" in z for z in inv_direkt.tote_winkel)

    # Fall B: Repo-Skript ruft to_spawn.py ort-wahl auf.
    repo_skript = _minimal_repo(tmp_path, "mit-ort-wahl-skript")
    (repo_skript / "scripts").mkdir()
    (repo_skript / "scripts" / "grosse_runde.sh").write_text(
        "#!/usr/bin/env bash\npython3 ~/.claude/skills/to-spawn/to_spawn.py ort-wahl --server-befehl 'pytest' --github-befehl 'gh workflow run ci.yml'\n",
        encoding="utf-8",
    )
    inv_skript = _inventur_fuer(repo_skript, tmp_path)
    assert inv_skript.projekt["grosse_runde"]["status"] == "mit_ort_wahl"
    assert inv_skript.projekt["grosse_runde"]["ok"] is True
    assert not any("Große Testrunde" in z for z in inv_skript.tote_winkel)

    # Fall C: pruefung.komplett verweist auf ein eigenes Skript, das ort_wahl nutzt.
    repo_indirekt = _minimal_repo(tmp_path, "mit-ort-wahl-indirekt")
    (repo_indirekt / "skripte").mkdir()
    (repo_indirekt / "skripte" / "vollpruefung.py").write_text(
        "from to_spawn import ort_wahl\nort_wahl.waehlen()\n",
        encoding="utf-8",
    )
    (repo_indirekt / ".to-spawn").mkdir()
    (repo_indirekt / ".to-spawn" / "config.json").write_text(
        json.dumps({"pruefung": {"komplett": "python3 skripte/vollpruefung.py"}}),
        encoding="utf-8",
    )
    inv_indirekt = _inventur_fuer(repo_indirekt, tmp_path)
    assert inv_indirekt.projekt["grosse_runde"]["status"] == "mit_ort_wahl"
    assert not any("Große Testrunde" in z for z in inv_indirekt.tote_winkel)


def test_inventur_geplanter_github_workflow_vs_self_hosted(tmp_path: Path) -> None:
    """Geplanter GitHub-Workflow (ubuntu-latest) braucht keinen Hinweis; self-hosted ohne Ort-Wahl schon."""
    repo_gh = _minimal_repo(tmp_path, "github-nightly")
    wf = repo_gh / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "nightly.yml").write_text(
        "name: Nightly\non:\n  schedule:\n    - cron: '0 2 * * *'\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8",
    )
    inv_gh = _inventur_fuer(repo_gh, tmp_path)
    assert inv_gh.projekt["grosse_runde"]["status"] == "github"
    assert not any("Große Testrunde" in z for z in inv_gh.tote_winkel)

    repo_self = _minimal_repo(tmp_path, "self-hosted-nightly")
    wf2 = repo_self / ".github" / "workflows"
    wf2.mkdir(parents=True)
    (wf2 / "nightly.yml").write_text(
        "name: Nightly\non:\n  schedule:\n    - cron: '0 2 * * *'\njobs:\n  test:\n    runs-on: self-hosted\n",
        encoding="utf-8",
    )
    inv_self = _inventur_fuer(repo_self, tmp_path)
    assert inv_self.projekt["grosse_runde"]["status"] == "ohne_ort_wahl"
    assert any("ohne Ort-Wahl" in z for z in inv_self.tote_winkel)
