"""Gemeinsamer Helfer der Weiterleitungen ``scripts/{bau,wache,capo,sessions_stand,spec_stand}.py``.

Die Logik liegt im Skill ``to-spawn`` (``$TO_SPAWN_HOME``, Vorgabe
``~/.claude/skills/to-spawn``) unter ``skripte/<name>.py`` — hier nur der Sprung
dorthin (#205). Das Repo wird als ``TO_SPAWN_REPO`` = Ordner über ``scripts/``
weitergereicht, immer (nie ``setdefault``), damit eine geerbte Variable aus einem
anderen Repo nie gewinnt.

* Als Programm (``python scripts/bau.py 205``): Umgebung setzen, Skill-Modul
  laden, ``sys.exit(modul.main())``. Fehlt der Skill: Meldung + Exit 3.
* Als Import (``from scripts import bau`` oder Laden per Dateipfad): das
  Skill-Modul ersetzt die Weiterleitung in ``sys.modules`` und füllt ihre
  Namen — ``monkeypatch.setattr("scripts.bau.x", …)`` wirkt so direkt in den
  Skill-Funktionen. ``TO_SPAWN_REPO`` gilt dabei nur während des Ladens.
  Fehlt der Skill: ``ImportError`` mit derselben Meldung.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger("to_spawn.weiterleitung")

EXIT_SKILL_FEHLT = 3
QUELLE = "github.com/Shavy72/claude-skill-to-spawn"
#: Diese Namen gehören der Weiterleitung selbst und werden nie überschrieben.
EIGENE_NAMEN = frozenset(
    {"__name__", "__spec__", "__loader__", "__package__", "__builtins__", "__file__", "__cached__"}
)


def skill_ordner() -> Path:
    """``$TO_SPAWN_HOME`` oder ``~/.claude/skills/to-spawn``."""
    return Path(os.environ.get("TO_SPAWN_HOME") or Path.home() / ".claude" / "skills" / "to-spawn")


def _lade(name: str, datei: Path) -> ModuleType:
    modulname = f"_to_spawn_skripte_{name}"
    spec = importlib.util.spec_from_file_location(modulname, datei)
    if spec is None or spec.loader is None:
        raise ImportError(f"Skill-Skript nicht ladbar: {datei}")
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul  # @dataclass braucht das Modul in sys.modules
    try:
        spec.loader.exec_module(modul)
    except BaseException:
        sys.modules.pop(modulname, None)
        raise
    return modul


def weiterleiten(name: str, modulname: str, ziel: dict[str, Any]) -> None:
    """Springt von ``scripts/<name>.py`` in ``<Skill>/skripte/<name>.py``."""
    repo = Path(ziel["__file__"]).resolve().parent.parent
    datei = skill_ordner() / "skripte" / f"{name}.py"
    if not datei.is_file():
        meldung = (
            f"WEIGERUNG: Skill to-spawn fehlt ({datei}) — Skill to-spawn installieren "
            f"({QUELLE}, install.sh bzw. install.ps1), dann erneut."
        )
        if modulname == "__main__":
            print(meldung, file=sys.stderr)
            sys.exit(EXIT_SKILL_FEHLT)
        raise ImportError(meldung)

    if modulname == "__main__":
        os.environ["TO_SPAWN_REPO"] = str(repo)
        sys.exit(_lade(name, datei).main())

    vorher = os.environ.get("TO_SPAWN_REPO")
    os.environ["TO_SPAWN_REPO"] = str(repo)
    try:
        modul = _lade(name, datei)
    finally:
        if vorher is None:
            os.environ.pop("TO_SPAWN_REPO", None)
        else:
            os.environ["TO_SPAWN_REPO"] = vorher
    sys.modules[modulname] = modul
    ziel.update({k: v for k, v in vars(modul).items() if k not in EIGENE_NAMEN})
    log.debug("Weiterleitung %s → %s (Repo %s)", modulname, datei, repo)
