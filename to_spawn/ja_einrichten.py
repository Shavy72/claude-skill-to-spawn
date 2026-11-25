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

#: Bash-Werkzeug und (Windows) PowerShell-Werkzeug; beide tragen den Befehl in ``tool_input.command``.
MATCHER = "Bash|PowerShell"
CLI = Path(__file__).resolve().parent.parent / "to_spawn.py"


def _ist_sperre(hook: object) -> bool:
    """Hook-Eintrag der Ja-Sperre, auch wenn die Datei nicht mehr existiert (Regel: :func:`briefkasten.ist_sperre_befehl`)."""
    return isinstance(hook, dict) and briefkasten.ist_sperre_befehl(hook.get("command"))


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
    if hooks is None:
        raise ValueError(f"{datei}: hooks ist null (erwartet ein Objekt) — nicht verändert.")
    blocks = hooks.setdefault("PreToolUse", []) if isinstance(hooks, dict) else None
    if blocks is None and isinstance(hooks, dict):
        raise ValueError(f"{datei}: hooks/PreToolUse ist null (erwartet eine Liste) — nicht verändert.")
    if not isinstance(blocks, list):
        raise ValueError(  # noqa: TRY004 — main fängt ValueError
            f"{datei}: hooks/PreToolUse hat eine unerwartete Form (PreToolUse muss eine Liste sein) — nicht verändert."
        )
    for nr, block in enumerate(blocks, 1):
        if not isinstance(block, dict):
            continue
        hooks_liste = block.get("hooks", [])
        if hooks_liste is None:
            raise ValueError(f"{datei}: PreToolUse-Block {nr}: hooks ist null (erwartet eine Liste) — nicht verändert.")
        if not isinstance(hooks_liste, list):
            raise ValueError(f"{datei}: PreToolUse-Block {nr}: hooks muss eine Liste sein — nicht verändert.")
        for h in hooks_liste:
            befehl = h.get("command") if isinstance(h, dict) else None
            if not isinstance(befehl, str) or not befehl.strip():
                log.warning(
                    "%s: PreToolUse-Block %s hat einen Hook-Eintrag ohne Befehl — bleibt unverändert, bitte prüfen.",
                    datei,
                    nr,
                )
    befehl = " ".join(
        shlex.quote(t)
        for t in (Path(python).as_posix(), cli.as_posix(), briefkasten.JA_SPERRE_HOOK)
    )
    neuer_block = {"matcher": MATCHER, "hooks": [{"type": "command", "command": befehl}]}
    vorher = json.dumps(blocks)
    # Genau ein Eintrag der Sperre: jeder bestehende (auch toter oder auf den Klon zeigende) wird entfernt,
    # der erste Platz bekommt den Soll-Eintrag; fremde Hooks/Blöcke und ihre Reihenfolge bleiben.
    ergebnis: list = []
    gesetzt = False
    for block in blocks:
        hooks_liste = block.get("hooks", []) if isinstance(block, dict) else []
        eigene = [h for h in hooks_liste if _ist_sperre(h)]
        if not eigene:
            ergebnis.append(block)
            continue
        fremde = [h for h in hooks_liste if not _ist_sperre(h)]
        if fremde:
            ergebnis.append({**block, "hooks": fremde})
        if not gesetzt:
            ergebnis.append(neuer_block)
            gesetzt = True
    if not gesetzt:
        ergebnis.append(neuer_block)
    blocks[:] = ergebnis
    if json.dumps(blocks) == vorher:
        return False
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
    # Installer geben den installierten Skill an: ``python -m`` aus dem Klon-Ordner lädt sonst das Klon-Modul (#566).
    parser.add_argument("--cli", type=Path, default=CLI)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        neu = sperre_eintragen(args.claude_dir, args.python, args.cli)
    except (OSError, ValueError) as fehler:
        log.error("Ja-Sperre nicht eingetragen: %s", fehler)
        return 1
    log.info("Ja-Sperre: %s", "eingetragen" if neu else "stand schon drin")
    for pfad in befehl_anlegen(args.claude_dir, args.python, args.cli):
        log.info("Befehl: %s", pfad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
