"""#214 Teil A: Review-Hook (``review-hook/``) kommt mit dem Installer nach ``~/.claude/hooks/review``.

Regel: eine vorhandene Hook-Datei wird nur ersetzt, wenn sie dem zuletzt installierten Stand
entspricht oder ohne CR gleich der neuen ist; sonst wird sie vorher gesichert und gemeldet.
Alles läuft in einem Wegwerf-Heimordner, nie gegen das echte ``~/.claude``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import review_hook

QUELLE = SKILL / "review-hook"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _quell_dateien() -> list[str]:
    return sorted(
        p.relative_to(QUELLE).as_posix() for p in QUELLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    )


def _stand(ordner: Path) -> dict[str, bytes]:
    return {p.relative_to(ordner).as_posix(): p.read_bytes() for p in ordner.rglob("*") if p.is_file()}


def test_quelle_vollstaendig() -> None:
    namen = set(_quell_dateien())
    assert {"stop.py", "commit.py", "merker.py", "_hookbasis.py", "wochenbericht.py"} <= namen
    assert any(n.startswith("tests/test_") for n in namen)


def test_frische_installation_legt_dateien_an(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    bericht = review_hook.installiere(ziel)
    for rel in _quell_dateien():
        assert (ziel / rel).read_bytes() == (QUELLE / rel).read_bytes(), rel
    assert not (tmp_path / "hooks" / "_alt").exists()
    assert any("angelegt" in zeile for zeile in bericht)


def test_gleiche_datei_mit_crlf_gilt_als_gleich(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    ziel.mkdir(parents=True)
    (ziel / "stop.py").write_bytes((QUELLE / "stop.py").read_bytes().replace(b"\n", b"\r\n"))
    bericht = review_hook.installiere(ziel)
    assert (ziel / "stop.py").read_bytes() == (QUELLE / "stop.py").read_bytes()
    assert not (tmp_path / "hooks" / "_alt").exists()
    assert not any("gesichert" in zeile for zeile in bericht)


def test_abweichende_datei_wird_gesichert_und_gemeldet(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    ziel.mkdir(parents=True)
    (ziel / "merker.py").write_text("# eigene Änderung\n", encoding="utf-8")
    bericht = review_hook.installiere(ziel, zeit="20261122-010203")
    sicherung = tmp_path / "hooks" / "_alt" / "review-20261122-010203" / "merker.py"
    assert sicherung.read_text(encoding="utf-8") == "# eigene Änderung\n"
    assert (ziel / "merker.py").read_bytes() == (QUELLE / "merker.py").read_bytes()
    assert any("merker.py" in zeile and str(sicherung) in zeile for zeile in bericht), bericht


def test_zuletzt_installierte_datei_wird_ohne_sicherung_ersetzt(tmp_path: Path) -> None:
    """Stand der letzten Installation (Quelle hat sich seither geändert) → still ersetzen."""
    alte_quelle = tmp_path / "alt"
    shutil.copytree(QUELLE, alte_quelle)
    (alte_quelle / "stop.py").write_text("# alter Stand\n", encoding="utf-8")
    ziel = tmp_path / "hooks" / "review"
    review_hook.installiere(ziel, quelle=alte_quelle)
    bericht = review_hook.installiere(ziel)
    assert (ziel / "stop.py").read_bytes() == (QUELLE / "stop.py").read_bytes()
    assert not (tmp_path / "hooks" / "_alt").exists(), bericht


def test_zweiter_lauf_aendert_nichts(tmp_path: Path) -> None:
    ziel = tmp_path / "hooks" / "review"
    review_hook.installiere(ziel)
    (ziel / "nur_hier.txt").write_text("fremd\n", encoding="utf-8")
    vorher = _stand(tmp_path)
    review_hook.installiere(ziel)
    assert _stand(tmp_path) == vorher


def _umgebung(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {"HOME": str(heim), "USERPROFILE": str(heim), "PYTHONIOENCODING": "utf-8", "TO_SPAWN_PY": sys.executable}
    )
    return env


def _installer(art: str, heim: Path) -> subprocess.CompletedProcess[str]:
    if art == "sh":
        befehl = ["bash", str(SKILL / "install.sh")]
    else:
        befehl = [PWSH or "pwsh", "-NoProfile", "-File", str(SKILL / "install.ps1")]
    return subprocess.run(
        befehl, env=_umgebung(heim), capture_output=True, text=True, encoding="utf-8", check=False, timeout=300
    )


@pytest.mark.parametrize("art", ["sh", pytest.param("ps1", marks=pytest.mark.skipif(not PWSH, reason="pwsh fehlt"))])
def test_installer_legt_review_hook_an(art: str, tmp_path: Path) -> None:
    lauf = _installer(art, tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    ziel = tmp_path / ".claude" / "hooks" / "review"
    for rel in _quell_dateien():
        assert (ziel / rel).read_bytes() == (QUELLE / rel).read_bytes(), rel
    assert "Review-Hook installiert" in lauf.stdout + lauf.stderr
