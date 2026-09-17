"""Bau-Log: eine JSONL-Datei je Ticket unter ``docs/agents/bau_log/<N>.jsonl``.

Nur anhängen, nie umschreiben — so wandert die Datei konfliktfrei über Git
zwischen Laptop und Bau-Server. Zahlen schreiben die Hooks, Worte schreibt die
Session.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("to_spawn.bau_log")

#: Erlaubte Zeilen-Typen (``staffel_limit`` meldet das Ende der Staffel-Kette).
TYPEN = (
    "auftrag",
    "session_start",
    "entscheidung",
    "deploy_phase",
    "handoff",
    "session_ende",
    "subagent_ende",
    "staffel_limit",
)

LOG_ORDNER = Path("docs") / "agents" / "bau_log"
_WT_MUSTER = re.compile(r"wt-(\d+)")


def log_pfad(repo: Path, ticket: str | int) -> Path:
    return repo / LOG_ORDNER / f"{ticket}.jsonl"


def ticket_aus_umgebung(cwd: Path | None = None) -> str | None:
    """Ticket-Nummer aus ``TO_SPAWN_TICKET`` oder aus einem Worktree-Pfad ``wt-<N>``."""
    aus_env = os.environ.get("TO_SPAWN_TICKET")
    if aus_env and aus_env.strip().isdigit():
        return aus_env.strip()
    pfad = (cwd or Path.cwd()).resolve()
    for teil in [pfad.name, *(eltern.name for eltern in pfad.parents)]:
        treffer = _WT_MUSTER.fullmatch(teil)
        if treffer:
            return treffer.group(1)
    return None


def jetzt() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def schreibe(repo: Path, ticket: str | int, typ: str, **felder: Any) -> dict[str, Any]:
    """Eine Zeile anhängen und zurückgeben."""
    if typ not in TYPEN:
        log.warning("Unbekannter Zeilen-Typ %r — wird trotzdem geschrieben.", typ)
    zeile: dict[str, Any] = {"ts": jetzt(), "typ": typ, "ticket": str(ticket)}
    zeile.update({k: v for k, v in felder.items() if v is not None})
    datei = log_pfad(repo, ticket)
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    return zeile


def lese(repo: Path, ticket: str | int) -> list[dict[str, Any]]:
    """Alle Zeilen eines Tickets (kaputte Zeilen werden übersprungen)."""
    datei = log_pfad(repo, ticket)
    if not datei.is_file():
        return []
    zeilen: list[dict[str, Any]] = []
    for roh in datei.read_text(encoding="utf-8").splitlines():
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            log.warning("Kaputte Log-Zeile in %s übersprungen.", datei)
            continue
        if isinstance(eintrag, dict):
            zeilen.append(eintrag)
    return zeilen


def alle_tickets(repo: Path) -> list[str]:
    ordner = repo / LOG_ORDNER
    if not ordner.is_dir():
        return []
    nummern = [p.stem for p in ordner.glob("*.jsonl") if p.stem.isdigit()]
    return sorted(nummern, key=int)


def _summe(zeilen: Iterable[dict[str, Any]], feld: str = "gesamt") -> int:
    gesamt = 0
    for zeile in zeilen:
        tokens = zeile.get("tokens") or {}
        if isinstance(tokens, dict):
            wert = tokens.get(feld)
            if isinstance(wert, (int, float)):
                gesamt += int(wert)
    return gesamt


def zusammenfassung(repo: Path, ticket: str | int) -> dict[str, Any]:
    """Kennzahlen eines Tickets für Tabelle und Lernstoff."""
    zeilen = lese(repo, ticket)
    enden = [z for z in zeilen if z.get("typ") == "session_ende"]
    subs = [z for z in zeilen if z.get("typ") == "subagent_ende"]
    starts = [z for z in zeilen if z.get("typ") == "session_start"]
    staffeln = [int(z.get("staffel") or 1) for z in zeilen if z.get("staffel") is not None]
    auftrag = next((z for z in zeilen if z.get("typ") == "auftrag"), {})
    dauer = sum(int(z.get("dauer_s") or 0) for z in enden)
    return {
        "ticket": str(ticket),
        "schaetzung_k": auftrag.get("schaetzung_k"),
        "umfang": auftrag.get("umfang") or auftrag.get("text"),
        "sessions": len(starts) or len(enden),
        "staffel": max(staffeln) if staffeln else (len(starts) or len(enden)),
        "subagenten": len(subs),
        "ist_k": round((_summe(enden) + _summe(subs)) / 1000, 1),
        "dauer_s": dauer,
        "handoffs": sum(1 for z in zeilen if z.get("typ") == "handoff"),
        "entscheidungen": sum(1 for z in zeilen if z.get("typ") == "entscheidung"),
    }


def tabelle(repo: Path, tickets: Iterable[str | int]) -> str:
    """Gesamt-Tabelle als Klartext (feste Spaltenbreiten, ohne Fremd-Bibliothek)."""
    kopf = ("Ticket", "Schätz.k", "Ist k", "Sess.", "Staffel", "Subag.", "Dauer", "Entsch.")
    reihen: list[tuple[str, ...]] = []
    for ticket in tickets:
        z = zusammenfassung(repo, ticket)
        schaetzung = z["schaetzung_k"]
        reihen.append(
            (
                f"#{z['ticket']}",
                f"{schaetzung:g}" if isinstance(schaetzung, (int, float)) else "—",
                f"{z['ist_k']:g}" if z["ist_k"] else "—",
                str(z["sessions"]),
                str(z["staffel"]),
                str(z["subagenten"]),
                f"{z['dauer_s'] // 60} min" if z["dauer_s"] else "—",
                str(z["entscheidungen"]),
            )
        )
    if not reihen:
        return "Bau-Log ist leer (noch kein Lauf)."
    breiten = [max(len(kopf[i]), *(len(r[i]) for r in reihen)) for i in range(len(kopf))]
    linien = [
        "  ".join(kopf[i].ljust(breiten[i]) for i in range(len(kopf))),
        "  ".join("-" * breiten[i] for i in range(len(kopf))),
    ]
    linien += ["  ".join(r[i].ljust(breiten[i]) for i in range(len(kopf))) for r in reihen]
    return "\n".join(linien)


def lernstoff(repo: Path, letzte: int = 30) -> str:
    """Die letzten Tickets als Lernstoff-Zeilen für ``/to-tickets``."""
    tickets = alle_tickets(repo)[-letzte:]
    if not tickets:
        return "Kein Bau-Log vorhanden — noch kein Lernstoff."
    zeilen = ["Lernstoff aus dem Bau-Log (Schätzung → Ist, Sessions je Ticket):"]
    for ticket in tickets:
        z = zusammenfassung(repo, ticket)
        schaetzung = z["schaetzung_k"]
        soll = f"{schaetzung:g}k" if isinstance(schaetzung, (int, float)) else "ohne Schätzung"
        umfang = (z["umfang"] or "").replace("\n", " ")[:110]
        zeilen.append(
            f"- #{z['ticket']}: {soll} geschätzt → {z['ist_k']:g}k ist, "
            f"{z['sessions']} Session(s), {z['subagenten']} Subagent(en)"
            + (f" · {umfang}" if umfang else "")
        )
    return "\n".join(zeilen)
