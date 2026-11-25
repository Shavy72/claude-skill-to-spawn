"""#190: Einheitliche Ruff-Formatierung (Zeilenlänge 120) und Prüf-Test gegen Rausch-Diffs.

Entscheidung 22.11.2026: Das ganze Repo ist mit fester ``ruff.toml`` (Zeilenlänge 120)
formatiert; dieser Prüf-Test hält den Stand mit ``ruff format --check``. Der
Auto-Format-Hook bleibt unverändert und erzeugt bei Einzelzeilen-Edits kein Format-Rauschen.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from to_spawn import prozessbaum
from to_spawn.pruef import sofort

SKILL = Path(__file__).resolve().parents[1]
RUFF_TOML = SKILL / "ruff.toml"

# In CI (CI gesetzt) ist ruff Pflicht (wie test_pruef_sofort_3b): dort scheitert der Test,
# statt still übersprungen zu werden.
_OHNE_RUFF = pytest.mark.skipif(
    sofort._ruff(SKILL) is None and not os.environ.get("CI"),
    reason="ruff nicht im PATH",
)


def _ruff_befehl() -> str:
    programm = sofort._ruff(SKILL)
    assert programm is not None, "ruff fehlt im PATH (in CI Pflicht)"
    return programm


def _ruff_format(wurzel: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_ruff_befehl(), "format", "--no-cache", *args],
        cwd=wurzel,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        **prozessbaum.fensterlos(),
    )


def test_ruff_toml_setzt_zeilenlaenge_120() -> None:
    """``ruff.toml`` im Repo-Wurzelordner legt ``line-length = 120`` fest."""
    assert RUFF_TOML.is_file(), f"ruff.toml fehlt unter {RUFF_TOML}"
    konfig = tomllib.loads(RUFF_TOML.read_text(encoding="utf-8"))
    assert konfig.get("line-length") == 120, konfig


@_OHNE_RUFF
def test_repo_ist_mit_ruff_formatiert() -> None:
    """Alle Python-Dateien im Repo entsprechen ``ruff format --check`` (mit ``ruff.toml``)."""
    assert RUFF_TOML.is_file(), f"ruff.toml fehlt unter {RUFF_TOML}"
    lauf = _ruff_format(SKILL, "--check", "--diff", ".")
    ausgabe = (lauf.stdout + lauf.stderr).strip()
    assert lauf.returncode == 0, f"Unformatierte Python-Dateien im Repo (ruff format . ausführen):\n{ausgabe[:4000]}"


@_OHNE_RUFF
def test_pruefung_bricht_bei_unformatierter_datei(tmp_path: Path) -> None:
    """Gegenprobe: ``ruff format --check`` ist grün bei sauberer Datei (auch CRLF) und rot bei unformatierter."""
    assert RUFF_TOML.is_file(), f"ruff.toml fehlt unter {RUFF_TOML}"
    shutil.copy2(RUFF_TOML, tmp_path / "ruff.toml")

    sauber = tmp_path / "sauber.py"
    sauber.write_text("WERT = 1 + 2\n", encoding="utf-8")
    assert _ruff_format(tmp_path, "--check", ".").returncode == 0

    # Windows-Checkout (CRLF) bleibt grün, weil Ruff ``line-ending = "auto"`` nutzt.
    crlf = tmp_path / "windows_crlf.py"
    crlf.write_bytes(b"WERT = 1 + 2\r\n")
    assert _ruff_format(tmp_path, "--check", ".").returncode == 0

    unsauber = tmp_path / "unsauber.py"
    unsauber.write_text("WERT=1+2\n", encoding="utf-8")
    rot = _ruff_format(tmp_path, "--check", "--diff", ".")
    assert rot.returncode != 0
    assert "unsauber.py" in (rot.stdout + rot.stderr)


@_OHNE_RUFF
def test_einzelzeilen_edit_in_capo_erzeugt_kein_format_rauschen(tmp_path: Path) -> None:
    """Abnahme #190: Edit einer Zeile in ``to_spawn/capo.py`` plus ``ruff format`` ändert nur diese Zeile."""
    assert RUFF_TOML.is_file(), f"ruff.toml fehlt unter {RUFF_TOML}"
    shutil.copy2(RUFF_TOML, tmp_path / "ruff.toml")
    ziel = tmp_path / "to_spawn" / "capo.py"
    ziel.parent.mkdir(parents=True)
    original = (SKILL / "to_spawn" / "capo.py").read_text(encoding="utf-8")
    alt, neu = "SPERRE_S = 30.0", "SPERRE_S = 31.0"
    assert original.count(alt) == 1, f"Ankerzeile {alt!r} in to_spawn/capo.py nicht eindeutig"
    ziel.write_text(original.replace(alt, neu, 1), encoding="utf-8")

    lauf = _ruff_format(tmp_path, "to_spawn/capo.py")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr

    nachher = ziel.read_text(encoding="utf-8")
    aenderungen = [
        zeile
        for zeile in difflib.unified_diff(original.splitlines(), nachher.splitlines(), lineterm="", n=0)
        if zeile.startswith(("+", "-")) and not zeile.startswith(("+++", "---"))
    ]
    assert aenderungen == [f"-{alt}", f"+{neu}"], f"Fremde Format-Änderungen in capo.py: {aenderungen[:20]}"
