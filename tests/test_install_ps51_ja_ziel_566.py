"""#566: install.ps1 lesbar für Windows PowerShell 5.1; Ja-Programm/Hook zeigen auf den installierten Skill."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import ja_einrichten


def test_ps1_dateien_sind_fuer_ps51_lesbar() -> None:
    # 5.1 liest BOM-lose Dateien als ANSI: Umlaute zerbrechen Zeichenketten -> BOM oder reines ASCII.
    dateien = list(SKILL.glob("*.ps1"))
    assert dateien
    for datei in dateien:
        roh = datei.read_bytes()
        assert roh.startswith(b"\xef\xbb\xbf") or roh.isascii(), datei.name


def _hook_befehle(claude: Path) -> list[list[str]]:
    daten = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    return [
        shlex.split(h["command"])
        for b in daten["hooks"]["PreToolUse"]
        for h in b["hooks"]
        if "hook-ja-sperre" in h["command"]
    ]


def test_ja_einrichten_cli_ziel_kommt_in_hook_und_befehl(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    cli = tmp_path / "installiert" / "to_spawn.py"
    code = ja_einrichten.main(["--claude-dir", str(claude), "--python", sys.executable, "--cli", str(cli)])
    assert code == 0
    assert [b[1] for b in _hook_befehle(claude)] == [cli.as_posix()]
    assert cli.as_posix() in (claude / "bin" / "ja").read_text(encoding="utf-8")
    assert cli.as_posix() in (claude / "bin" / "ja.cmd").read_text(encoding="utf-8")


def test_neuinstallation_schreibt_klon_eintrag_auf_installiertes_ziel_um(
    tmp_path: Path,
) -> None:
    claude = tmp_path / ".claude"
    klon = tmp_path / "klon" / "to_spawn.py"
    neu = tmp_path / "skills" / "to-spawn" / "to_spawn.py"
    ja_einrichten.sperre_eintragen(claude, sys.executable, klon)
    assert ja_einrichten.sperre_eintragen(claude, sys.executable, neu) is True
    assert [b[1] for b in _hook_befehle(claude)] == [neu.as_posix()]
    assert ja_einrichten.sperre_eintragen(claude, sys.executable, neu) is False


def test_toter_klon_eintrag_wird_ersetzt_fremde_hooks_bleiben(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    claude.mkdir()
    fremd = {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo x"}]}
    tot = tmp_path / "geloescht" / "to_spawn.py"  # existiert nie
    alt = {
        "matcher": "Bash|PowerShell",
        "hooks": [{"type": "command", "command": f"python {tot.as_posix()} hook-ja-sperre"}],
    }
    daten = {"hooks": {"PreToolUse": [fremd, alt, fremd]}, "andere": 1}
    (claude / "settings.json").write_text(json.dumps(daten), encoding="utf-8")
    neu = tmp_path / "skills" / "to_spawn.py"
    ja_einrichten.sperre_eintragen(claude, sys.executable, neu)
    ergebnis = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    bloecke = ergebnis["hooks"]["PreToolUse"]
    assert bloecke[0] == fremd and bloecke[2] == fremd and len(bloecke) == 3
    assert ergebnis["andere"] == 1
    assert [b[1] for b in _hook_befehle(claude)] == [neu.as_posix()]


def test_zielpfad_mit_leerzeichen_ruft_richtiges_programm(tmp_path: Path) -> None:
    claude = tmp_path / ".claude"
    cli = tmp_path / "mein ordner" / "to_spawn.py"
    py = Path(sys.executable).as_posix()
    ja_einrichten.sperre_eintragen(claude, sys.executable, cli)
    ja_einrichten.befehl_anlegen(claude, sys.executable, cli)
    assert _hook_befehle(claude) == [[py, cli.as_posix(), "hook-ja-sperre"]]
    cmd = (claude / "bin" / "ja.cmd").read_text(encoding="utf-8")
    assert cmd.startswith(f'@"{py}" "{cli.as_posix()}" ')
    zeile = (claude / "bin" / "ja").read_text(encoding="utf-8").splitlines()[1]
    assert shlex.split(zeile)[1:3] == [py, cli.as_posix()]


def test_beide_installer_uebergeben_das_ziel() -> None:
    erwartet = {
        "install.sh": '--cli "$ZIEL/to_spawn.py"',
        "install.ps1": '--cli (Join-Path $ziel "to_spawn.py")',
    }
    for name, wert in erwartet.items():
        zeilen = [
            z for z in (SKILL / name).read_text(encoding="utf-8-sig").splitlines() if "to_spawn.ja_einrichten" in z
        ]
        assert zeilen
        assert all(z.rstrip().endswith(wert) for z in zeilen), name
