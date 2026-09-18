"""CLI-Tests für ``to_spawn.py deploy-status`` (duoplus-management#207).

Echt laufen: CLI, Statusdatei, Bau-Log, Git-Repo. Gestellt ist nichts — die
Statusdatei schreibt hier der Test selbst, im Format von ``safe_deploy_vps.sh``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
TICKET = "977"


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init", "-q", str(arbeit)], check=True, capture_output=True)
    for name in ("TO_SPAWN_TICKET", "BAU_TICKET", "DEPLOY_STATUS_DATEI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _ts(vor_min: float = 0) -> str:
    zeit = datetime.now(timezone.utc).astimezone() - timedelta(minutes=vor_min)
    return zeit.isoformat(timespec="seconds")


def _zeile(
    phase: str, sek: int, marker: str = "", lauf: str = "100-1", **extra: Any
) -> str:
    daten: dict[str, Any] = {
        "ts": extra.pop("ts", _ts()),
        "lauf": lauf,
        "phase": phase,
        "sek": sek,
        "marker": marker or phase,
    }
    daten.update(extra)
    return json.dumps(daten, ensure_ascii=False)


def _status(repo: Path, *zeilen: str) -> Path:
    datei = repo / ".deploy_status.jsonl"
    datei.write_text("".join(z + "\n" for z in zeilen), encoding="utf-8")
    return datei


def _laeuft(lauf: str = "100-1", vor_min: float = 0) -> tuple[str, ...]:
    return (
        _zeile(
            "start",
            0,
            "Deploy-Start",
            lauf=lauf,
            sha="abc",
            args="--skip-ci",
            ts=_ts(vor_min),
        ),
        _zeile("pre-flight", 0, "Pre-Flight lokal", lauf=lauf, ts=_ts(vor_min)),
        _zeile(
            "pytest",
            12,
            "Gate 2/2: pytest Stufe KERN parallel",
            lauf=lauf,
            ts=_ts(vor_min),
        ),
    )


def _cli(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "deploy-status", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **(env or {})},
        check=False,
    )


def _log_zeilen(repo: Path, ticket: str = TICKET) -> list[dict[str, Any]]:
    # Seit #204 schreiben Hooks und deploy-status in die unversionierte Laufdatei.
    datei = repo / ".to-spawn" / "bau_log" / f"{ticket}.jsonl"
    if not datei.is_file():
        return []
    return [
        json.loads(z)
        for z in datei.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def test_laufender_deploy_meldet_exit_3_und_die_phase(repo: Path) -> None:
    _status(repo, *_laeuft())
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert "pytest" in ergebnis.stdout
    assert "Gate 2/2" in ergebnis.stdout
    assert "100-1" in ergebnis.stdout
    assert len(ergebnis.stdout.strip().splitlines()) <= 3
    phasen = [z["phase"] for z in _log_zeilen(repo) if z["typ"] == "deploy_phase"]
    assert phasen == ["start", "pre-flight", "pytest"]


def test_gruenes_ende_meldet_exit_0(repo: Path) -> None:
    _status(
        repo,
        *_laeuft(),
        _zeile(
            "ende",
            700,
            "Deploy-Ende grün",
            ergebnis="gruen",
            exit=0,
            grund="Deploy fertig — VPS auf abc",
        ),
    )
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "grün" in ergebnis.stdout
    assert len(ergebnis.stdout.strip().splitlines()) <= 3
    ende = _log_zeilen(repo)[-1]
    assert ende["phase"] == "ende" and ende["ergebnis"] == "gruen" and ende["exit"] == 0


def test_rotes_ende_meldet_exit_1_und_den_grund(repo: Path) -> None:
    _status(
        repo,
        *_laeuft(),
        _zeile(
            "ende",
            30,
            "Deploy-Ende rot",
            ergebnis="rot",
            exit=5,
            grund="ruff-Gate rot — Deploy abgebrochen, VPS unverändert.\n→ Fehler oben beheben",
        ),
    )
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 1, ergebnis.stdout + ergebnis.stderr
    assert "rot: ruff-Gate rot" in ergebnis.stdout
    assert len(ergebnis.stdout.strip().splitlines()) <= 3
    ende = _log_zeilen(repo)[-1]
    assert ende["grund"].startswith("ruff-Gate rot") and ende["exit"] == 5


def test_stiller_lauf_ueber_der_grenze_meldet_exit_4(repo: Path) -> None:
    _status(repo, *_laeuft(vor_min=75))
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 4, ergebnis.stdout + ergebnis.stderr
    assert "Keine neue Phase seit 75 min" in ergebnis.stdout
    assert "Prozess prüfen" in ergebnis.stdout
    # eigene Grenze: 90 min → noch kein Hänger
    assert _cli(repo, "--ticket", TICKET, "--still-min", "90").returncode == 3


def test_ohne_statusdatei_exit_2(repo: Path) -> None:
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert _log_zeilen(repo) == []
    (repo / ".deploy_status.jsonl").write_text("", encoding="utf-8")
    assert _cli(repo, "--ticket", TICKET).returncode == 2


def test_kaputte_zeile_wird_uebersprungen(repo: Path) -> None:
    start, vor, pytest_zeile = _laeuft()
    _status(repo, start, "{kaputt", vor, "", pytest_zeile)
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert "übersprungen" in ergebnis.stderr
    assert [z["phase"] for z in _log_zeilen(repo)] == ["start", "pre-flight", "pytest"]


def test_wiederholter_aufruf_schreibt_nichts_doppelt(repo: Path) -> None:
    datei = _status(repo, *_laeuft())
    _cli(repo, "--ticket", TICKET)
    _cli(repo, "--ticket", TICKET)
    assert len(_log_zeilen(repo)) == 3
    # neue Phase im selben Lauf → genau eine neue Zeile
    with datei.open("a", encoding="utf-8") as fh:
        fh.write(_zeile("diff", 400, "Diff-Detection gegen VPS-HEAD") + "\n")
    _cli(repo, "--ticket", TICKET)
    _cli(repo, "--ticket", TICKET)
    assert [z["phase"] for z in _log_zeilen(repo)] == [
        "start",
        "pre-flight",
        "pytest",
        "diff",
    ]


def test_neuer_lauf_schreibt_seine_phasen_neu(repo: Path) -> None:
    _status(repo, *_laeuft(lauf="100-1"))
    _cli(repo, "--ticket", TICKET)
    _status(repo, *_laeuft(lauf="200-2"))
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert "200-2" in ergebnis.stdout
    laeufe = [z["lauf"] for z in _log_zeilen(repo)]
    assert laeufe == ["100-1"] * 3 + ["200-2"] * 3


def test_datei_aus_umgebung_und_ohne_ticket_nur_anzeigen(
    repo: Path, tmp_path: Path
) -> None:
    anderswo = tmp_path / "woanders.jsonl"
    anderswo.write_text("".join(z + "\n" for z in _laeuft()), encoding="utf-8")
    ergebnis = _cli(repo, env={"DEPLOY_STATUS_DATEI": str(anderswo)})
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert "kein Ticket" in ergebnis.stderr
    assert not (repo / "docs" / "agents" / "bau_log").exists()
    assert not (repo / ".to-spawn" / "bau_log").exists()


# --- Fixrunde nach dem Prüfpanel -------------------------------------------


def _tote_pid() -> int:
    """PID eines echten, schon beendeten und eingesammelten Prozesses."""
    kind = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    kind.send_signal(signal.SIGKILL)
    kind.wait(timeout=10)
    return kind.pid


def test_getoeteter_gate_prozess_ohne_ende_meldet_rot(repo: Path) -> None:
    start, vor, pytest_zeile = _laeuft()
    daten = json.loads(start)
    pid = _tote_pid()
    daten["pid"] = pid
    _status(repo, json.dumps(daten), vor, pytest_zeile)
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 1, ergebnis.stdout + ergebnis.stderr
    assert f"rot: Gate-Prozess {pid} ohne Ende-Zeile beendet" in ergebnis.stdout
    assert len(ergebnis.stdout.strip().splitlines()) <= 3


def test_lebender_gate_prozess_laeuft_weiter(repo: Path) -> None:
    start, vor, pytest_zeile = _laeuft()
    daten = json.loads(start)
    daten["pid"] = os.getpid()
    _status(repo, json.dumps(daten), vor, pytest_zeile)
    assert _cli(repo, "--ticket", TICKET).returncode == 3


def test_unlesbarer_zeitstempel_meldet_exit_4(repo: Path) -> None:
    start, vor, _ = _laeuft()
    kaputt = _zeile("pytest", 12, "Gate 2/2", ts="")
    _status(repo, start, vor, kaputt)
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 4, ergebnis.stdout + ergebnis.stderr
    assert "Zeitstempel" in ergebnis.stdout


def test_vorgabe_grenze_ist_60_minuten(repo: Path) -> None:
    _status(repo, *_laeuft(vor_min=45))
    assert _cli(repo, "--ticket", TICKET).returncode == 3
    _status(repo, *_laeuft(lauf="300-3", vor_min=65))
    assert _cli(repo, "--ticket", TICKET).returncode == 4


def test_datei_im_fremden_repo_bestimmt_bau_log_und_ticket(
    repo: Path, tmp_path: Path
) -> None:
    """--datei zeigt ins Worktree ``wt-555``; Aufruf aus einem anderen Repo."""
    wt = tmp_path / "wt-555"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True, capture_output=True)
    datei = wt / ".deploy_status.jsonl"
    datei.write_text("".join(z + "\n" for z in _laeuft()), encoding="utf-8")
    ergebnis = _cli(repo, "--datei", str(datei))
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert len(_log_zeilen(wt, "555")) == 3
    assert not (repo / "docs").exists()


def test_bau_ticket_aus_der_umgebung(repo: Path) -> None:
    _status(repo, *_laeuft())
    ergebnis = _cli(repo, env={"BAU_TICKET": "556"})
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert len(_log_zeilen(repo, "556")) == 3


def test_kaputte_bytes_werden_ersetzt_statt_abbruch(repo: Path) -> None:
    datei = repo / ".deploy_status.jsonl"
    start, vor, pytest_zeile = _laeuft()
    datei.write_bytes(
        (start + "\n").encode()
        + b"\xff\xfe kaputt\n"
        + (vor + "\n" + pytest_zeile + "\n").encode()
    )
    ergebnis = _cli(repo, "--ticket", TICKET)
    assert ergebnis.returncode == 3, ergebnis.stdout + ergebnis.stderr
    assert [z["phase"] for z in _log_zeilen(repo)] == ["start", "pre-flight", "pytest"]
