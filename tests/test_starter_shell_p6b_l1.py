"""L1 Etappe 1 P6b: ps1-Dateien durch den echten PowerShell-Parser, kaputte Repo-Konfig im Starter.

Ergänzt ``test_starter_shell_l1.py`` (dort nur Textfragmente der ps1-Dateien). Ohne ``pwsh``
wird der Parser-Test übersprungen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_starter_shell_l1 import VORGABE, _repo, _spawn_srv

SKILL = Path(__file__).resolve().parents[1]
PS1_DATEIEN = sorted(p for p in SKILL.rglob("*.ps1") if ".git" not in p.parts)

_PARSER = (
    "$fehler = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
    "$env:PS1_DATEI, [ref]$null, [ref]$fehler); "
    "if ($fehler) { $fehler | ForEach-Object { $_.ToString() }; exit 1 }"
)


def _parsen(datei: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", _PARSER],
        env={**os.environ, "PS1_DATEI": str(datei)},
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_ps1_dateien_gefunden() -> None:
    assert {p.name for p in PS1_DATEIEN} >= {"install.ps1", "spawn_local.ps1", "spawn_srv.ps1"}


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh fehlt")
@pytest.mark.parametrize("datei", PS1_DATEIEN, ids=lambda p: p.relative_to(SKILL).as_posix())
def test_ps1_parser_ohne_fehler(datei: Path) -> None:
    lauf = _parsen(datei)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh fehlt")
def test_ps1_parser_erkennt_kaputte_datei(tmp_path: Path) -> None:
    """Gegenprobe: der Parser-Aufruf meldet einen Syntaxfehler wirklich."""
    kaputt = tmp_path / "kaputt.ps1"
    kaputt.write_text('if ($true) { Write-Host "offen"\n', encoding="utf-8")
    lauf = _parsen(kaputt)
    assert lauf.returncode == 1 and lauf.stdout.strip(), lauf.stdout + lauf.stderr


@pytest.mark.parametrize("inhalt", ["{kaputt", "", "[1, 2]"])
def test_spawn_srv_kaputte_konfig_vorgabe(tmp_path: Path, inhalt: str) -> None:
    repo = _repo(tmp_path, "egal")
    (repo / ".to-spawn" / "config.json").write_text(inhalt, encoding="utf-8")
    lauf = _spawn_srv(tmp_path, repo)
    assert lauf.returncode == 2, lauf.stderr
    assert f"{VORGABE}/spec-9.json" in lauf.stderr
