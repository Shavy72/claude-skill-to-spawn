"""#1410: „Spec fertig“ und Abschluss-Mail kommen nur aus capo, nie aus der Aufseher-Session.

Zwei Wege daran vorbei werden geprüft:

1. Prompt-Text: der Aufseher-Takt-Prompt überlässt der Session kein eigenes Fertig-Urteil,
   die Abschluss-Kette hängt nur an der capo-Zeile „#S SPEC FERTIG — Abschluss-Kette“.
2. Sperre im Skript: ``abschluss_paket.py ablegen/nachsehen`` verweigert (Exit 4), solange capos
   Zustand die Spec führt, aber nicht fertig meldet (kein ``spec_fertig_seit``, z. B. Staging
   nicht lesbar, #1407). Mit capo-Fertig geht der Aufruf durch.

Echt laufen das CLI und capos Zustandsdatei (echte Dateien im Temp-Ordner); nichts gestellt.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import capo, waechter_takt  # noqa: E402

SPEC = 1410
FERTIG_ZEILE = f"#{SPEC} SPEC FERTIG — Abschluss-Kette"


def _prompt() -> str:
    return waechter_takt.PROMPT.format(S=SPEC, REPO="x/y", SKILL="/skill", LISTE="-", NOTIZ="-")


def test_prompt_laesst_session_kein_eigenes_fertig_urteil() -> None:
    text = _prompt()
    # Alte Sätze, mit denen die Session selbst „fertig“ entschied.
    for verboten in ("alle Bau-Tickets zu", "Bau fertig, bereit zur Abnahme", "Kette … durch"):
        assert verboten not in text, f"Prompt überlässt der Session das Fertig-Urteil: „{verboten}“"
    assert "nur capo" in text
    # Die Abschluss-Kette hängt an genau der Zeile, die pruefe() bei tick.fertig in die Liste setzt.
    quelle = Path(waechter_takt.__file__).read_text(encoding="utf-8")
    assert 'f"#{spec} SPEC FERTIG — Abschluss-Kette' in quelle
    assert FERTIG_ZEILE in text
    # Exit 4 der Sperre ist im Prompt erklärt (nicht umgehen).
    assert re.search(r"Exit 4[^.]*capo", text)


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
        "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "projekt"
    (r / ".git").mkdir(parents=True)
    return r


def _capo_zustand(repo: Path, tmp_path: Path, zustand: dict[str, object]) -> Path:
    alt = os.environ.get("TO_SPAWN_WAECHTER_ORDNER")
    os.environ["TO_SPAWN_WAECHTER_ORDNER"] = str(tmp_path / "w")
    try:
        datei = capo._zustand_datei(repo, "", SPEC)
    finally:
        if alt is None:
            os.environ.pop("TO_SPAWN_WAECHTER_ORDNER", None)
        else:
            os.environ["TO_SPAWN_WAECHTER_ORDNER"] = alt
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    return datei


def _abschluss(befehl: str, repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "abschluss_paket.py"), befehl, str(SPEC), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(tmp_path),
        timeout=120,
        check=False,
    )


_LINKS = ("--stage", "https://stage.example", "--rundschau", "fehlt", "--tests", "fehlt")


def test_session_ablegen_ohne_capo_fertig_wird_abgelehnt(repo: Path, tmp_path: Path) -> None:
    # capo führt die Spec, sagt aber nicht fertig (Staging nicht lesbar, #1407).
    _capo_zustand(repo, tmp_path, {"erledigt": [], "staging_unlesbar_takte": 2})
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 4, lauf.stdout + lauf.stderr
    assert "capo" in lauf.stderr
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()
    nach = _abschluss("nachsehen", repo, tmp_path)
    assert nach.returncode == 4, nach.stdout + nach.stderr


def test_mit_capo_fertig_geht_ablegen_durch(repo: Path, tmp_path: Path) -> None:
    _capo_zustand(repo, tmp_path, {"erledigt": [], "spec_fertig_seit": "2026-11-21T10:00:00+01:00"})
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").is_file()
    nach = _abschluss("nachsehen", repo, tmp_path, "--dry-run")
    assert nach.returncode != 4, nach.stdout + nach.stderr
