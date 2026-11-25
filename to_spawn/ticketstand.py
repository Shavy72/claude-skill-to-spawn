"""Ticketstand: kurze Tabelle aller Tickets einer Spec für jeden Aufseher-Tick.

Der Mensch will bei jedem Tick ohne Nachfragen sehen, wie weit die Spec ist. Dieses Modul
führt drei vorhandene Quellen zusammen und erzeugt daraus eine Tabelle — es baut
keine eigene Session- oder GitHub-Logik nach:

- Tickets + Kurztitel: Manifest ``docs/agents/manifests/spec-<S>.json`` (:mod:`manifest`)
  plus die Sub-Issues der Spec (:func:`capo.kinder`). Gelbe Folge-Tickets stehen nur
  im Manifest und werden einzeln nachgeladen.
- Offen/zu, Label ``checkpoint:human`` und Blocker-Zahl: GitHub (``issue_dependencies_summary``).
- Läuft die Session? Fenster-/Transkript-Lage aus :func:`aufseher_stand.sammeln` —
  dieselbe Quelle wie ``aufseher-stand`` (tmux auf dem Bau-Server, Transkript am PC).

Zustände (eine Regel, oben gewinnt):

``fertig`` Issue geschlossen · ``blockiert`` offene Blocker · ``checkpoint`` Label
``checkpoint:human`` (wartet auf den Menschen) · ``läuft`` Session-Fenster da · ``wartet``
sonst (noch kein Fenster).

Nur lesen: schreibt weder Stand-Datei noch Bau-Log.

    python to_spawn.py ticketstand 1254
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from to_spawn import aufseher_stand, gh, manifest, ticket_zustand

log = logging.getLogger("ticketstand")

FERTIG = "fertig"
#: Grün auf Staging, Ticket noch offen — wartet nur auf Live (#279).
FERTIG_GEBAUT = "fertig gebaut"
LAEUFT = "läuft"
WARTET = "wartet"
BLOCKIERT = "blockiert"
CHECKPOINT = "checkpoint"

CHECKPOINT_LABEL = "checkpoint:human"
TITEL_MAX = 48


@dataclass(frozen=True)
class Zeile:
    nummer: int
    titel: str
    zustand: str
    hinweis: str = ""


def kurztitel(titel: str, spec: int) -> str:
    """Titel ohne Spec-Vorsatz („Reel-Tresor #1254: …“), gekürzt auf :data:`TITEL_MAX`."""
    titel = " ".join(str(titel or "").split())
    titel = re.sub(rf"^.*?#{spec}\s*:\s*", "", titel, count=1)
    titel = titel.replace("|", "/")
    return titel if len(titel) <= TITEL_MAX else titel[: TITEL_MAX - 1].rstrip() + "…"


def _labels(issue: dict[str, Any]) -> set[str]:
    return {str(x.get("name") if isinstance(x, dict) else x) for x in issue.get("labels") or []}


def _offene_blocker(issue: dict[str, Any]) -> int:
    summe = issue.get("issue_dependencies_summary") or {}
    try:
        return int(summe.get("blocked_by") or 0)
    except (TypeError, ValueError):
        return 0


def zustand(
    issue: dict[str, Any] | None,
    lage: aufseher_stand.TicketLage | None,
    blocker: list[int] | None = None,
) -> tuple[str, str]:
    """(Zustand, Hinweis) eines Tickets — reine Rechenlogik."""
    if issue is None:
        return WARTET, "GitHub nicht lesbar"
    stufe = ticket_zustand.stufe(issue)
    if stufe == "zu":
        return FERTIG, ""
    if stufe == "verworfen":
        return FERTIG, "verworfen"
    if stufe == "fertig_gebaut":
        return FERTIG_GEBAUT, "wartet auf Live"
    # GitHubs Zähler kennt staging:fertig nicht — liegt die Liste vor, entscheidet sie (#1527).
    if _offene_blocker(issue) > 0 and (blocker is None or blocker):
        wer = ", ".join(f"#{b}" for b in blocker or [])
        return BLOCKIERT, f"wartet auf {wer}" if wer else f"{_offene_blocker(issue)} offene Vorgänger"
    if CHECKPOINT_LABEL in _labels(issue):
        return CHECKPOINT, "wartet auf Abnahme des Menschen"
    fenster = lage.fenster if lage else aufseher_stand.FENSTER_UNBEKANNT
    minuten = f" {lage.still_min} min" if lage and lage.still_min is not None else ""
    if fenster == aufseher_stand.BLOCKIERT:
        detail = (lage.blockiert_text if lage else "") or (
            f"blockiert{minuten}" if minuten else "Session meldet blockiert"
        )
        return BLOCKIERT, detail
    if fenster == aufseher_stand.ARBEITET:
        return LAEUFT, "arbeitet"
    if fenster == aufseher_stand.WARTET:
        return WARTET, f"wartet{minuten}" if minuten else "wartet"
    if fenster == aufseher_stand.STILL:
        return LAEUFT, f"still{minuten}"
    if fenster == aufseher_stand.RUECKFRAGE:
        return LAEUFT, f"Rückfrage{minuten}"
    if fenster == aufseher_stand.KEIN_FENSTER:
        return WARTET, "noch keine Session"
    return WARTET, "Session-Lage unbekannt"


def tabelle(spec: int, zeilen: list[Zeile], uhr: str) -> str:
    """Kopfzeile „X von Y fertig“ + Markdown-Tabelle (rendert im Claude-Fenster)."""
    fertig = sum(1 for z in zeilen if z.zustand == FERTIG)
    zaehler: dict[str, int] = {}
    for z in zeilen:
        zaehler[z.zustand] = zaehler.get(z.zustand, 0) + 1
    rest = " · ".join(
        f"{zaehler[k]} {k}" for k in (FERTIG_GEBAUT, LAEUFT, WARTET, BLOCKIERT, CHECKPOINT) if zaehler.get(k)
    )
    kopf = (
        f"**Ticketstand Spec #{spec}: {fertig} von {len(zeilen)} fertig**"
        + (f" · {rest}" if rest else "")
        + f" · {uhr}"
    )
    aus = [kopf, "", "| Ticket | Kurztitel | Zustand | Hinweis |", "|---|---|---|---|"]
    for z in zeilen:
        aus.append(f"| #{z.nummer} | {z.titel} | {z.zustand} | {z.hinweis or '—'} |")
    return "\n".join(aus)


@dataclass
class Quellen:
    """Außenquellen; Tests geben Attrappen hinein."""

    stand: aufseher_stand.Quellen
    #: Ein Issue einzeln (für Manifest-Tickets, die kein Sub-Issue sind), ``None`` = Fehler.
    issue: Callable[[int], dict[str, Any] | None]
    #: Nummern der offenen Blocker eines Tickets, ``None`` = Fehler.
    blocker: Callable[[int], list[int] | None]
    jetzt: Callable[[], datetime] = datetime.now


def sammeln(spec: int, repo: Path, q: Quellen, stand_ordner: Path | None = None) -> list[Zeile]:
    """Je Ticket (Sub-Issues ∪ Manifest) eine :class:`Zeile`, nach Nummer sortiert."""
    kinder = q.stand.kinder(spec)
    if kinder is None:
        raise RuntimeError(f"Sub-Issues von Spec #{spec} nicht lesbar (gh)")
    issues: dict[int, dict[str, Any] | None] = {}
    for k in kinder:
        try:
            issues[int(k["number"])] = k
        except (KeyError, TypeError, ValueError):
            log.warning("Sub-Issue ohne Nummer übersprungen: %r", k)
    try:
        mani = manifest.lade_manifest(repo, spec)
    except (OSError, ValueError) as fehler:
        log.warning("Manifest spec-%s nicht lesbar: %s", spec, fehler)
        mani = {}
    mani_tickets: dict[str, Any] = mani.get("tickets") or {}
    for roh in mani_tickets:
        try:
            n = int(roh)
        except (TypeError, ValueError):
            continue
        if n not in issues:
            issues[n] = q.issue(n)
    if not issues:
        raise RuntimeError(f"Spec #{spec} hat weder Sub-Issues noch Manifest-Tickets")

    lagen: dict[int, aufseher_stand.TicketLage] = {}
    vorhanden = [i for i in issues.values() if i is not None]
    # Bau-Log-Lücken sind für die Tabelle egal — Warnungen von aufseher-stand nicht in die Ausgabe.
    stand_log = logging.getLogger("to_spawn.aufseher_stand")
    alte_stufe = stand_log.level
    stand_log.setLevel(logging.ERROR)
    try:
        # Dieselbe Lage wie aufseher-stand; die Stand-Datei wird nur gelesen.
        erg = aufseher_stand.sammeln(
            spec,
            replace(q.stand, kinder=lambda _s: vorhanden),
            aufseher_stand._vorher(spec, stand_ordner),
        )
        lagen = {x.nummer: x for x in erg.lagen}
    except RuntimeError as fehler:
        log.warning("Session-Lage nicht lesbar: %s", fehler)
    finally:
        stand_log.setLevel(alte_stufe)

    zeilen: list[Zeile] = []
    for n in sorted(issues):
        issue = issues[n]
        blocker = q.blocker(n) if issue is not None and _offene_blocker(issue) > 0 else None
        z, hinweis = zustand(issue, lagen.get(n), blocker)
        titel = (issue or {}).get("title") or (mani_tickets.get(str(n)) or {}).get("title") or ""
        zeilen.append(Zeile(n, kurztitel(titel, spec), z, hinweis))
    return zeilen


def echte_quellen(repo: Path, gh_repo: str) -> Quellen:
    def issue(n: int) -> dict[str, Any] | None:
        daten = gh.json_lauf(["api", f"repos/{gh_repo}/issues/{n}"])
        return daten if isinstance(daten, dict) else None

    def blocker(n: int) -> list[int] | None:
        daten = gh.blocked_by(gh_repo, str(n))
        if daten is None:
            return None
        return gh.offene_blocker(daten)  # zu oder staging:fertig (#1527)

    return Quellen(stand=aufseher_stand.echte_quellen(repo, gh_repo), issue=issue, blocker=blocker)


# --- CLI --------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser("ticketstand", help="Ticketstand-Tabelle einer Spec (X von Y fertig)")
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument("--gh-repo", default="", help="owner/name (Vorgabe: aus origin)")


def lauf(args: Any, repo: Path, q: Quellen | None = None) -> int:
    gh_repo = args.gh_repo or gh.repo_aus_origin(repo, fallback="")
    if q is None and not gh_repo:
        print("FEHLER: GitHub-Repo unbekannt — --gh-repo owner/name angeben.", file=sys.stderr)
        return 2
    try:
        quellen = q or echte_quellen(repo, gh_repo)
        zeilen = sammeln(args.spec, repo, quellen)
    except aufseher_stand.GhFehlt as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 2
    except RuntimeError as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1
    print(tabelle(args.spec, zeilen, quellen.jetzt().strftime("%d.%m. %H:%M")))
    return 0
