"""#474: Ein Bau-Ticket fährt nie die Komplett-Prüfung — je Ticket nur die schnelle Prüfung.

Vorgabe des Auftraggebers: Komplett-Prüfung genau einmal je Spec (ohne Staging vor dem
Live-Schritt, mit Staging sobald alle Tickets auf Staging sind). Probe-Lauf 2 (F13): ``bau 13``
rief ``skripte/pruefen_komplett.py`` von Hand am Stufen-Protokoll vorbei.

Echte Abläufe ohne Attrappen: die echte Probe-Vorlage und ein echter kleiner Prüf-Befehl, der
jede Ausführung in eine Zähldatei schreibt. Bau-Session = ``TO_SPAWN_TICKET`` gesetzt (wie
``bau.py`` es setzt).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn import config, komplett_lauf
from to_spawn.pruefplan import Moment

SKILL = Path(__file__).resolve().parent.parent
VORLAGE = SKILL / "probe" / "gemeinsam"
SPEC = 474


def _konfig(zaehl: Path, *, staging: bool) -> dict[str, Any]:
    """Eingerichtetes Repo; ``komplett`` = echter Befehl, der je Lauf eine Zeile in ``zaehl`` schreibt."""
    code = f"import pathlib;pathlib.Path({str(zaehl)!r}).open('a').write('lauf\\n')"
    eigen: dict[str, Any] = {
        "staging_start": "",
        "staging": {"url": "http://127.0.0.1:8001/" if staging else "", "ohne": not staging},
        "pruefung": {"schnell": "true", "komplett": f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"},
    }
    return config._mische(config.DEFAULTS, eigen)


def _laeufe(zaehl: Path) -> int:
    return len(zaehl.read_text(encoding="utf-8").splitlines()) if zaehl.exists() else 0


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def klon(tmp_path: Path) -> Path:
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "user.name=Bau", "-c", "user.email=bau@example.invalid", "commit", "-q", "-m", "a")
    return repo


def _probe_skript(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    app = tmp_path / "app-repo"
    shutil.copytree(VORLAGE, app)
    return subprocess.run(
        [sys.executable, "skripte/pruefen_komplett.py"],
        cwd=app,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


@pytest.mark.parametrize("ticket", ["13", ""])
def test_probe_skript_lehnt_bau_ticket_ab(tmp_path: Path, ticket: str) -> None:
    env = {**os.environ, "TO_SPAWN_TICKET": ticket}
    raus = _probe_skript(tmp_path, env)
    text = raus.stdout + raus.stderr
    assert raus.returncode != 0, text
    assert "schnelle Prüfung" in text and "--moment vor-live" in text, text
    assert "Rauchtest:" not in text, "abgelehnt heißt: weder Tests noch Rauchtest laufen"


def test_probe_skript_ohne_bau_ticket_laeuft_weiter(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_TICKET"}
    raus = _probe_skript(tmp_path, env)
    assert raus.returncode == 0, raus.stdout + raus.stderr
    assert "Rauchtest:" in raus.stdout


@pytest.mark.parametrize("moment", [Moment.VOR_LIVE, Moment.ALLE_AUF_STAGING])
def test_skill_tuer_lehnt_bau_ticket_ab(
    klon: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: Moment
) -> None:
    zaehl = tmp_path / "zaehl.txt"
    monkeypatch.setenv("TO_SPAWN_TICKET", "13")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, moment, _konfig(zaehl, staging=moment is Moment.ALLE_AUF_STAGING))
    assert not erg.ok and not erg.frei and not erg.gelaufen
    assert "schnelle Prüfung" in erg.grund and "--moment vor-live" in erg.grund
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_regulaerer_weg_ohne_bau_ticket_bleibt(klon: Path, tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, _konfig(zaehl, staging=False))
    assert erg.ok and erg.gelaufen
    assert _laeufe(zaehl) == 1
