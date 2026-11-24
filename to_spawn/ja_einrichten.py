"""``install.sh``/``install.ps1`` Schritt (#531): Ja-Sperre in ``~/.claude/settings.json`` + Befehl ``ja``.

* :func:`sperre_eintragen` — PreToolUse-Hook ``hook-ja-sperre`` (Bash) einfügen: JSON einlesen, mergen,
  atomar schreiben, vorher Sicherungskopie ``settings.json.bak-<Stempel>``; idempotent, fremde
  Einträge bleiben unangetastet; kaputtes JSON wird nie überschrieben (``ValueError``).
* :func:`befehl_anlegen` — ``~/.claude/bin/ja`` (+ ``ja.cmd``): dünne Hülle um ``to_spawn.py ja``.

Wer das Ja geben darf, entscheidet allein ``briefkasten`` (:func:`briefkasten.menschen_nachweis`).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import shutil
import sys
from datetime import datetime
from pathlib import Path

from to_spawn import briefkasten

log = logging.getLogger("to_spawn.ja_einrichten")

CLI = Path(__file__).resolve().parent.parent / "to_spawn.py"


def sperre_eintragen(
    claude_dir: Path, python: str, cli: Path = CLI, zeit: str | None = None
) -> bool:
    """Hook eintragen. True = geschrieben, False = stand schon drin."""
    datei = claude_dir / "settings.json"
    daten: dict = {}
    if datei.exists():
        daten = json.loads(datei.read_text(encoding="utf-8"))
        if not isinstance(daten, dict):
            raise ValueError(f"{datei}: kein JSON-Objekt — nicht überschrieben.")
    hooks = daten.setdefault("hooks", {})
    blocks = hooks.setdefault("PreToolUse", [])
    if any(
        b.get("matcher") == "Bash"
        and briefkasten.JA_SPERRE_HOOK in h.get("command", "")
        for b in blocks
        for h in b.get("hooks", [])
    ):
        return False
    befehl = " ".join(
        shlex.quote(t)
        for t in (Path(python).as_posix(), cli.as_posix(), briefkasten.JA_SPERRE_HOOK)
    )
    blocks.append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": befehl}]}
    )
    claude_dir.mkdir(parents=True, exist_ok=True)
    if datei.exists():
        stempel = zeit or datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(datei, datei.with_name(f"settings.json.bak-{stempel}"))
    tmp = datei.with_name("settings.json.neu")
    tmp.write_text(
        json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, datei)
    return True


def befehl_anlegen(claude_dir: Path, python: str, cli: Path = CLI) -> list[Path]:
    """``bin/ja`` und ``bin/ja.cmd`` schreiben (überschreibt nur diese zwei Dateien)."""
    ordner = claude_dir / "bin"
    ordner.mkdir(parents=True, exist_ok=True)
    py, skript = Path(python).as_posix(), cli.as_posix()
    sh = ordner / "ja"
    sh.write_text(
        f'#!/bin/sh\nexec {shlex.quote(py)} {shlex.quote(skript)} ja "$@"\n',
        encoding="utf-8",
    )
    sh.chmod(0o755)
    cmd = ordner / "ja.cmd"
    cmd.write_text(f'@"{py}" "{skript}" ja %*\r\n', encoding="utf-8", newline="")
    return [sh, cmd]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        neu = sperre_eintragen(args.claude_dir, args.python)
    except (OSError, ValueError) as fehler:
        log.error("Ja-Sperre nicht eingetragen: %s", fehler)
        return 1
    log.info("Ja-Sperre: %s", "eingetragen" if neu else "stand schon drin")
    for pfad in befehl_anlegen(args.claude_dir, args.python):
        log.info("Befehl: %s", pfad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
