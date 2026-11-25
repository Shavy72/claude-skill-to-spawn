"""#637: PowerShell-Skripte (``spawn_local.ps1``, ``install.ps1``) laufen ohne ``-AsHashtable`` (PS 5.1 + 7).

Windows PowerShell 5.1 kennt ``ConvertFrom-Json -AsHashtable`` nicht (erst ab PowerShell 7).
Die Tests prüfen:
- Keine ``*.ps1``-Datei im Skill nutzt ``-AsHashtable``.
- Das Manifest-Lesen in ``spawn_local.ps1`` und die Glocke in ``install.ps1`` funktionieren
  ohne ``-AsHashtable`` (über ``PSObject.Properties`` bzw. ``Add-Member``) in echtem PowerShell.
- ``tests/test_spawn_local_lauf_313.py`` überspringt sauber, wenn ``python``/``python3`` fehlt.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def test_keine_ps1_nutzt_as_hashtable() -> None:
    """#637: ``ConvertFrom-Json -AsHashtable`` bricht unter Windows PowerShell 5.1 ab."""
    funde: list[str] = []
    for datei in sorted(SKILL.glob("*.ps1")):
        text = datei.read_text(encoding="utf-8-sig")
        for nr, zeile in enumerate(text.splitlines(), 1):
            ohne_kommentar = zeile.split("#", 1)[0]
            if re.search(r"-AsHashtable\b", ohne_kommentar, re.IGNORECASE):
                funde.append(f"{datei.name}:{nr}: {zeile.strip()}")
    assert funde == [], f"-AsHashtable gefunden (bricht PowerShell 5.1): {funde}"


def test_spawn_local_lauf_313_hat_skipif_fuer_fehlendes_python() -> None:
    """#637 Hinweis aus PR-633-Prüfung: ``test_spawn_local_lauf_313.py`` überspringt ohne python/python3."""
    text = (SKILL / "tests" / "test_spawn_local_lauf_313.py").read_text(encoding="utf-8")
    assert 'shutil.which("python")' in text
    assert 'shutil.which("python3")' in text
    assert "skipif" in text


@pytest.mark.skipif(not PWSH, reason="weder pwsh noch powershell vorhanden")
def test_spawn_local_manifest_lesen_ohne_as_hashtable(tmp_path: Path) -> None:
    """#637: Die Manifest-Zeilen aus ``spawn_local.ps1`` lesen Ticket-Nummern sortiert und verkraften leere Tickets."""
    quelltext = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8-sig")
    block = re.search(
        r"(if \(\$Tickets\.Count -eq 0\) \{.*?\n\})",
        quelltext,
        re.DOTALL,
    )
    assert block, "Manifest-Block in spawn_local.ps1 nicht gefunden"
    assert "-AsHashtable" not in block.group(1)

    manifest = tmp_path / "spec-9100.json"
    manifest.write_text(
        json.dumps(
            {
                "spec": 9100,
                "feature": "Probe",
                "tickets": {"9105": {"title": "B"}, "9101": {"title": "A"}, "9102": {"title": "C"}},
            }
        ),
        encoding="utf-8",
    )
    leer = tmp_path / "spec-leer.json"
    leer.write_text(json.dumps({"spec": 9101, "feature": "Leer", "tickets": {}}), encoding="utf-8")

    ps_code = (
        f"$ErrorActionPreference = 'Stop'\n"
        f"$manifest = '{manifest}'\n"
        f"$Tickets = @()\n"
        f"{block.group(1)}\n"
        f"Write-Output ('OK:' + ($Tickets -join ','))\n"
        f"$manifest = '{leer}'\n"
        f"$Tickets = @()\n"
        f"{block.group(1)}\n"
        f"Write-Output ('LEER:' + $Tickets.Count)\n"
    )
    lauf = subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-Command", ps_code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "OK:9101,9102,9105" in lauf.stdout, lauf.stdout
    assert "LEER:0" in lauf.stdout, lauf.stdout


@pytest.mark.skipif(not PWSH, reason="weder pwsh noch powershell vorhanden")
def test_install_ps1_glocke_ohne_as_hashtable_setzt_terminal_bell(tmp_path: Path) -> None:
    """#637: Der Glocke-Block aus ``install.ps1`` schreibt ``preferredNotifChannel`` ohne ``-AsHashtable``."""
    quelltext = (SKILL / "install.ps1").read_text(encoding="utf-8-sig")
    block = re.search(
        r"(\$cj = Join-Path \$env:USERPROFILE \"\.claude\.json\".*?^\})",
        quelltext,
        re.DOTALL | re.MULTILINE,
    )
    assert block, "Glocke-Block in install.ps1 nicht gefunden"
    assert "-AsHashtable" not in block.group(1)

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"theme": "dark", "numStartups": 3}), encoding="utf-8")

    ps_code = f"$ErrorActionPreference = 'Stop'\n$env:USERPROFILE = '{tmp_path}'\n{block.group(1)}\n"
    lauf = subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-Command", ps_code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(claude_json.read_text(encoding="utf-8-sig"))
    assert daten["preferredNotifChannel"] == "terminal_bell"
    assert daten["theme"] == "dark"
    assert daten["numStartups"] == 3
    assert (tmp_path / ".claude.json.bak-to-spawn").is_file()
