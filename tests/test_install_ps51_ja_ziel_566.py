"""#566: install.ps1 lesbar für Windows PowerShell 5.1; Ja-Programm/Hook zeigen auf den installierten Skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import ja_einrichten


def test_install_ps1_ist_fuer_ps51_lesbar() -> None:
    # 5.1 liest BOM-lose Dateien als ANSI: Umlaute zerbrechen Zeichenketten -> BOM oder reines ASCII.
    roh = (SKILL / "install.ps1").read_bytes()
    assert roh.startswith(b"\xef\xbb\xbf") or roh.isascii()


def test_ja_einrichten_cli_ziel_kommt_in_hook_und_befehl(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    cli = tmp_path / "installiert" / "to_spawn.py"
    code = ja_einrichten.main(
        ["--claude-dir", str(claude), "--python", sys.executable, "--cli", str(cli)]
    )
    assert code == 0
    hook = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert cli.as_posix() in json.dumps(hook)
    assert cli.as_posix() in (claude / "bin" / "ja").read_text(encoding="utf-8")
    assert cli.as_posix() in (claude / "bin" / "ja.cmd").read_text(encoding="utf-8")


def test_beide_installer_uebergeben_das_ziel() -> None:
    for name in ("install.sh", "install.ps1"):
        zeilen = [
            z
            for z in (SKILL / name).read_text(encoding="utf-8").splitlines()
            if "to_spawn.ja_einrichten" in z
        ]
        assert zeilen
        assert all("--cli" in z for z in zeilen), name
