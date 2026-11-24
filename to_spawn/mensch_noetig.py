"""„Mensch nötig“-Einträge des Review-Stop-Hooks lesen (``~/.claude/hooks/review/stop.py``).

Der Hook hängt je roter Bau-Session nach max. Fixrunden eine JSON-Zeile an
(``session_id, repo, fingerprint, spec, ticket, zeit, fix_runde``); der Aufseher
meldet jeden Eintrag seiner Spec genau einmal. Welche Einträge zum eigenen Lauf gehören,
entscheidet nur :func:`eintraege_fuer_lauf` (#465, #498).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def pfad() -> Path:
    """Gleicher Pfad wie ``mensch_noetig_pfad()`` im Review-Stop-Hook."""
    return Path(os.environ.get("REVIEW_MENSCH_NOETIG") or Path.home() / ".claude" / ".review" / "mensch_noetig.jsonl")


def schluessel(eintrag: dict[str, Any]) -> str:
    """Eine Meldung je Session und Code-Stand — Folgezeilen desselben Stands (neue Zeit) nicht."""
    return f"{eintrag.get('session_id')}|{eintrag.get('fingerprint')}"


def _alter_schluessel(eintrag: dict[str, Any]) -> str:
    """Format bis Runde 5 (mit Zeit) — schon gemerkte Einträge nicht erneut melden."""
    return f"{schluessel(eintrag)}|{eintrag.get('zeit')}"


def eintraege_fuer_spec(spec: int, gesehen: set[str]) -> list[dict[str, Any]]:
    """Noch nicht gesehene Einträge dieser Spec; kaputte Zeilen werden übersprungen."""
    datei = pfad()
    try:
        text = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as fehler:
        log.warning("%s nicht lesbar: %s", datei, fehler)
        return []
    treffer: list[dict[str, Any]] = []
    for nr, zeile in enumerate(text.splitlines(), start=1):
        if not zeile.strip():
            continue
        try:
            eintrag = json.loads(zeile)
        except json.JSONDecodeError:
            log.warning("%s Zeile %d kaputt — übersprungen.", datei, nr)
            continue
        if not isinstance(eintrag, dict):
            log.warning("%s Zeile %d kein Objekt — übersprungen.", datei, nr)
            continue
        if str(eintrag.get("spec")) != str(spec):
            continue
        if schluessel(eintrag) in gesehen or _alter_schluessel(eintrag) in gesehen:
            continue
        gesehen = gesehen | {schluessel(eintrag)}  # gleiche Datei, gleicher Stand: nur erste Zeile
        treffer.append(eintrag)
    return treffer


def _norm(ort: str | Path) -> str:
    """Vergleichsform eines Pfads: ``~``, Symlinks, End-Schrägstrich und Groß/klein (Windows) egal."""
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(ort))))


def eintraege_fuer_lauf(
    spec: int,
    gesehen: set[str],
    repo: Path,
    worktree: Callable[[int], Path],
    fremd: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Wie :func:`eintraege_fuer_spec`, aber nur Einträge aus diesem Lauf (#465, #498).

    Eigen ist ein Eintrag, dessen ``repo`` der Hauptbaum ``repo`` oder der Worktree
    ``worktree(<Ticket>)`` ist (ohne ``ticket`` gilt die Spec-Nummer). Die Datei teilen alle
    Repos der Maschine; Spec- und Ticketnummer allein sind nicht eindeutig. Ohne ``repo``
    gilt der Eintrag als fremd. Jeder verworfene Eintrag wird
    einmal geloggt und in ``fremd`` (vom Aufrufer über Läufe gehalten) vermerkt.
    ponytail: ohne ``--wt-basis`` teilen Repos ``~/wt/wt-N`` — dann hilft nur Git-Common-Dir.
    """
    eigene: list[dict[str, Any]] = []
    fremd = fremd if fremd is not None else set()
    for eintrag in eintraege_fuer_spec(spec, gesehen | fremd):
        ort = str(eintrag.get("repo") or "")
        nummer = str(eintrag.get("ticket") or spec)
        orte = [repo] + ([worktree(int(nummer))] if nummer.isdigit() else [])
        if ort and _norm(ort) in {_norm(o) for o in orte}:
            eigene.append(eintrag)
            continue
        fremd.add(schluessel(eintrag))
        log.warning("mensch_noetig: fremder Eintrag #%s ignoriert (anderes Repo/Worktree: %s)", nummer, ort or "ohne repo")
    return eigene


FREMD_MAX = 500  # Obergrenze gemerkter fremder Schlüssel; älteste fliegen zuerst raus


def eintraege_mit_zustand(
    spec: int,
    zustand: dict[str, Any],
    sichern: Callable[[], None] | None,
    repo: Path,
    worktree: Callable[[int], Path],
) -> list[dict[str, Any]]:
    """Wie :func:`eintraege_fuer_lauf`, aber die gemerkten fremden Schlüssel liegen in
    ``zustand["mensch_noetig_fremd"]`` (Liste, älteste zuerst, max. ``FREMD_MAX``).

    Laden, Änderung erkennen, Kappen und Sichern (``sichern()``) macht allein dieses Modul;
    ein kaputter Wert (keine Liste, falsche Typen) zählt als leer. ``sichern=None`` (dry_run):
    ``zustand`` bleibt unberührt.
    ponytail: ändert sich ``--wt-basis`` zwischen Läufen, kann ein eigener Eintrag als fremd
    gemerkt werden — Zustand je wt-basis wäre der Upgrade-Pfad.
    """
    roh = zustand.get("mensch_noetig_fremd")
    alt = [s for s in roh if isinstance(s, str)] if isinstance(roh, list) else []
    fremd = set(alt)
    eigene = eintraege_fuer_lauf(spec, set(zustand.get("mensch_noetig_gesehen") or []), repo, worktree, fremd=fremd)
    neu = alt + sorted(fremd - set(alt))
    if sichern is not None and (neu != alt or roh != alt):
        zustand["mensch_noetig_fremd"] = neu[-FREMD_MAX:]
        sichern()
    return eigene
