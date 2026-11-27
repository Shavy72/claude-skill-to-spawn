"""Gesprächs-ID je Fenster im Repo merken: ``<repo>/.to-spawn/sessions/<name>.json``.

Schreiben ``bau.py`` (``<N>.json``, jede Staffel-Runde neu) und der Aufseher
(``wache-<S>.json``); lesen tut der Aufpasser (Fixrunde 2 #236, R2): fehlt ein
Fenster oder lebt im Pane nur noch eine Shell, setzt er die Session mit
``--resume <session_id>`` fort, statt frisch zu starten — sofern das Transkript
``~/.claude/projects/<cwd-slug>/<session_id>.jsonl`` noch da ist. ``.to-spawn/*``
ist im Nutzer-Repo gitignored (nur ``config.json`` ist versioniert).

:class:`Sicherung` merkt den Stand der Datei vor einem Schritt; ``respawn`` legt ihn bei
Abbruch zurück, sonst setzt der Aufpasser eine verworfene Session fort.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("to_spawn.sessions_datei")


def pfad(repo: Path, name: str) -> Path:
    return repo / ".to-spawn" / "sessions" / f"{name}.json"


def schreiben(repo: Path, name: str, session_id: str, cwd: Path, runde: int) -> Path | None:
    """Datei schreiben (Ordner anlegen); Fehler nur loggen — der Start darf daran
    nie scheitern."""
    datei = pfad(repo, name)
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        datei.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "cwd": str(cwd),
                    "runde": runde,
                    "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except OSError as fehler:
        log.warning("Sessions-Datei %s nicht geschrieben: %s", datei, fehler)
        return None
    return datei


def lesen(repo: Path, name: str) -> dict | None:
    """Inhalt der Datei oder None (fehlt, unlesbar, ohne ``session_id``)."""
    datei = pfad(repo, name)
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(roh, dict) or not isinstance(roh.get("session_id"), str):
        return None
    if not roh["session_id"]:
        return None
    return roh


@dataclass
class Sicherung:
    """Inhalt einer Datei vor einem Schritt — ``zuruecklegen`` stellt ihn wieder her.

    ``inhalt is None`` heißt: vorher gab es die Datei nicht → zurücklegen löscht sie.
    ``lesbar=False``: der alte Stand ist unbekannt → zurücklegen fasst nichts an und
    meldet False, damit der Abbruch „Handarbeit nötig“ sagt statt still zu schweigen.
    """

    pfad: Path
    inhalt: bytes | None
    lesbar: bool = True

    @classmethod
    def merken(cls, repo: Path, name: str) -> Sicherung:
        """Sicherung der Sessions-Datei ``name`` in ``repo``; unlesbar → ``lesbar=False`` (geloggt)."""
        datei = pfad(repo, name)
        try:
            return cls(datei, datei.read_bytes())
        except FileNotFoundError:
            return cls(datei, None)
        except OSError:
            log.exception("%s nicht lesbar — wird bei Abbruch nicht zurückgesetzt.", datei)
            return cls(datei, None, lesbar=False)

    def zuruecklegen(self) -> bool:
        """Alten Stand wiederherstellen; False bei Dateifehler oder unbekanntem Stand (geloggt)."""
        if not self.lesbar:
            log.error("%s war vorher unlesbar — nicht zurückgesetzt.", self.pfad)
            return False
        try:
            if self.inhalt is None:
                self.pfad.unlink(missing_ok=True)
            elif not self.pfad.exists() or self.pfad.read_bytes() != self.inhalt:
                self.pfad.parent.mkdir(parents=True, exist_ok=True)
                self.pfad.write_bytes(self.inhalt)
        except OSError:
            log.exception("%s nicht zurückgesetzt.", self.pfad)
            return False
        return True
