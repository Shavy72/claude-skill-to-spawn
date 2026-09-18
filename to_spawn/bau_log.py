"""Bau-Log: zwei JSONL-Dateien je Ticket.

- Laufdatei ``.to-spawn/bau_log/<N>.jsonl`` (unversioniert, per ``.gitignore``
  ausgenommen): hier schreiben Hooks und Starter während der Session. So bleibt der
  Worktree sauber, ``git rebase`` und das Deploy-Gate brechen nicht ab (Fixrunde #204).
- Versionierte Datei ``docs/agents/bau_log/<N>.jsonl``: schreibt nur der CLI-Befehl
  ``eintrag``. Er überträgt dabei alle Laufdatei-Zeilen, die dort noch fehlen; danach
  committet die Session die Datei.

Nur anhängen, nie umschreiben — so wandert die Datei konfliktfrei über Git
zwischen Laptop und Bau-Server. Zahlen schreiben die Hooks, Worte schreibt die
Session. ``lese()`` vereint beide Dateien.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    # Wächter (#213): Live-Beweis blockiert (Feld ``grund``) und Modell-Wechsel beim Limit.
    "blockiert",
    "waechter_modell",
    "zusammenfassung",
)

LOG_ORDNER = Path("docs") / "agents" / "bau_log"
LAUF_ORDNER = Path(".to-spawn") / "bau_log"
_WT_MUSTER = re.compile(r"wt-(\d+)")


def log_pfad(repo: Path, ticket: str | int) -> Path:
    """Versionierte Datei (schreibt nur ``eintrag``)."""
    return repo / LOG_ORDNER / f"{ticket}.jsonl"


def lauf_pfad(repo: Path, ticket: str | int) -> Path:
    """Unversionierte Laufdatei (schreiben Hooks und Starter)."""
    return repo / LAUF_ORDNER / f"{ticket}.jsonl"


def hat_log(repo: Path, ticket: str | int) -> bool:
    return log_pfad(repo, ticket).is_file() or lauf_pfad(repo, ticket).is_file()


def log_repo(fallback: Path | None = None) -> Path | None:
    """Wohin Hooks und ``eintrag`` schreiben (#204).

    ``TO_SPAWN_LOG_REPO`` gesetzt (setzt ``bau.py`` auf den Ticket-Worktree) → genau
    dieser Ordner, aber nur wenn er existiert. Fehlt er (noch), gibt es ``None`` —
    nie in den geteilten Hauptbaum ausweichen: eine unversionierte
    ``docs/agents/bau_log/<N>.jsonl`` dort blockiert später jeden ``git pull``.
    Ohne Variable gilt ``fallback`` bzw. die Git-Wurzel des aktuellen Ordners.
    """
    ziel = os.environ.get("TO_SPAWN_LOG_REPO", "").strip()
    if ziel:
        pfad = Path(ziel).expanduser()
        if pfad.is_dir():
            return pfad.resolve()
        log.info(
            "TO_SPAWN_LOG_REPO %s existiert (noch) nicht — keine Log-Zeile, "
            "kein Ausweichen in den Hauptbaum.",
            pfad,
        )
        return None
    if fallback is not None:
        return fallback
    from . import config

    return config.repo_wurzel()


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


def _neue_zeile(ticket: str | int, typ: str, felder: dict[str, Any]) -> dict[str, Any]:
    if typ not in TYPEN:
        log.warning("Unbekannter Zeilen-Typ %r — wird trotzdem geschrieben.", typ)
    zeile: dict[str, Any] = {"ts": jetzt(), "typ": typ, "ticket": str(ticket)}
    zeile.update({k: v for k, v in felder.items() if v is not None})
    return zeile


def _haenge_an(datei: Path, rohzeilen: Iterable[str]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8", newline="\n") as fh:
        for roh in rohzeilen:
            fh.write(roh + "\n")


def _rohzeilen(datei: Path) -> list[str]:
    if not datei.is_file():
        return []
    return [z.strip() for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def _fehlende(fest: list[str], lauf: list[str]) -> list[str]:
    """Laufdatei-Zeilen, die in der versionierten Datei fehlen (exakt je Zeile).

    Gezählt wie eine Mehrfachmenge: steht eine Zeile zweimal in der Laufdatei und
    einmal versioniert, fehlt sie genau einmal.
    """
    vorrat = Counter(fest)
    fehlend: list[str] = []
    for roh in lauf:
        if vorrat[roh] > 0:
            vorrat[roh] -= 1
        else:
            fehlend.append(roh)
    return fehlend


def schreibe(repo: Path, ticket: str | int, typ: str, **felder: Any) -> dict[str, Any]:
    """Eine Zeile an die unversionierte Laufdatei anhängen und zurückgeben."""
    zeile = _neue_zeile(ticket, typ, felder)
    _haenge_an(lauf_pfad(repo, ticket), [json.dumps(zeile, ensure_ascii=False)])
    return zeile


def eintrag_schreiben(repo: Path, ticket: str | int, typ: str, **felder: Any) -> dict[str, Any]:
    """Nur für den CLI-Befehl ``eintrag``: fehlende Laufdatei-Zeilen und die neue
    Zeile an die versionierte Datei anhängen (danach committet die Session sie)."""
    zeile = _neue_zeile(ticket, typ, felder)
    fest = log_pfad(repo, ticket)
    fehlend = _fehlende(_rohzeilen(fest), _rohzeilen(lauf_pfad(repo, ticket)))
    if fehlend:
        log.info("Bau-Log #%s: %d Zeile(n) aus der Laufdatei übertragen.", ticket, len(fehlend))
    _haenge_an(fest, [*fehlend, json.dumps(zeile, ensure_ascii=False)])
    return zeile


def _lese_datei(datei: Path) -> list[tuple[str, dict[str, Any]]]:
    paare: list[tuple[str, dict[str, Any]]] = []
    for roh in _rohzeilen(datei):
        try:
            eintrag = json.loads(roh)
        except ValueError:
            log.warning("Kaputte Log-Zeile in %s übersprungen.", datei)
            continue
        if isinstance(eintrag, dict):
            paare.append((roh, eintrag))
    return paare


def _sortier_zeit(zeile: dict[str, Any]) -> datetime:
    return _zeitpunkt(zeile.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)


def lese(repo: Path, ticket: str | int) -> list[dict[str, Any]]:
    """Alle Zeilen eines Tickets: versioniert ∪ Laufdatei, nach ``ts`` sortiert.

    Zeilen, die exakt gleich in beiden Dateien stehen, zählen einmal. Kaputte Zeilen
    werden übersprungen.
    """
    fest = _lese_datei(log_pfad(repo, ticket))
    lauf = _lese_datei(lauf_pfad(repo, ticket))
    fehlend = Counter(_fehlende([roh for roh, _ in fest], [roh for roh, _ in lauf]))
    zeilen = [eintrag for _, eintrag in fest]
    for roh, eintrag in lauf:
        if fehlend[roh] > 0:
            fehlend[roh] -= 1
            zeilen.append(eintrag)
    return sorted(zeilen, key=_sortier_zeit)  # stabil: gleiche Zeit behält Reihenfolge


def alle_tickets(repo: Path) -> list[str]:
    nummern: set[str] = set()
    for ordner in (repo / LOG_ORDNER, repo / LAUF_ORDNER):
        if ordner.is_dir():
            nummern.update(p.stem for p in ordner.glob("*.jsonl") if p.stem.isdigit())
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


def _juengste_je_session(zeilen: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nur die jüngste Zeile je ``session_id`` (Hooks schreiben kumulierte Summen).

    Claude Code feuert den Stop-Hook am Ende jeder Runde; jede Zeile trägt die
    Summe des ganzen Transkripts bis dahin. Zeilen ohne ``session_id`` (ältere
    Logs) zählen einzeln.
    """
    je_id: dict[str, dict[str, Any]] = {}
    ohne_id: list[dict[str, Any]] = []
    for zeile in zeilen:
        kennung = zeile.get("session_id")
        if kennung:
            je_id[str(kennung)] = zeile  # spätere Zeile überschreibt frühere
        else:
            ohne_id.append(zeile)
    return [*je_id.values(), *ohne_id]


def _dauer(zeile: dict[str, Any]) -> int:
    try:
        return int(float(zeile.get("dauer_s") or 0))
    except (TypeError, ValueError):
        return 0


def zusammenfassung(repo: Path, ticket: str | int) -> dict[str, Any]:
    """Kennzahlen eines Tickets für Tabelle und Lernstoff."""
    zeilen = lese(repo, ticket)
    enden = _juengste_je_session(z for z in zeilen if z.get("typ") == "session_ende")
    subs = _juengste_je_session(z for z in zeilen if z.get("typ") == "subagent_ende")
    starts = [z for z in zeilen if z.get("typ") == "session_start"]
    staffeln = [int(z.get("staffel") or 1) for z in zeilen if z.get("staffel") is not None]
    auftrag = next((z for z in zeilen if z.get("typ") == "auftrag"), {})
    dauer = sum(_dauer(z) for z in enden)
    kennungen = {
        str(z["session_id"])
        for z in zeilen
        if z.get("typ") in ("session_start", "session_ende") and z.get("session_id")
    }
    enden_ohne_id = sum(1 for z in enden if not z.get("session_id"))
    starts_ohne_id = sum(1 for z in starts if not z.get("session_id"))
    # Der Starter (bau_loop) schreibt session_start ohne Kennung, die Hooks mit —
    # das Maximum zählt dieselbe Session nicht doppelt.
    sessions = max(len(kennungen) + enden_ohne_id, starts_ohne_id)
    klartext = next((z for z in reversed(zeilen) if z.get("typ") == "zusammenfassung"), {})
    return {
        "ticket": str(ticket),
        "schaetzung_k": auftrag.get("schaetzung_k"),
        "umfang": auftrag.get("umfang") or auftrag.get("text"),
        "title": auftrag.get("title"),
        "sessions": sessions,
        "staffel": max(staffeln) if staffeln else sessions,
        "subagenten": len(subs),
        "ist_k": round((_summe(enden) + _summe(subs)) / 1000, 1),
        "dauer_s": dauer,
        "handoffs": sum(1 for z in zeilen if z.get("typ") == "handoff"),
        "entscheidungen": sum(1 for z in zeilen if z.get("typ") == "entscheidung"),
        # Klartext der jüngsten ``zusammenfassung``-Zeile (Fixrunde #204).
        "umfang_ist": klartext.get("umfang"),
        "schwierigkeiten": klartext.get("schwierigkeiten"),
        "entscheidungen_text": klartext.get("entscheidungen"),
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
                f"{schaetzung:g}" if ist_schaetzung(schaetzung) else "—",
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


#: Umfang-Arten für die Faustregel „Sessions je Art“ (Kleinschrift). Wortstamm am
#: Wortanfang: „Skripte“, „Tests“, „Tabellen“ zählen mit; kurze Kürzel wie „ui“,
#: „css“, „sql“, „cli“, „api“ nur als ganzes Wort.
UMFANG_ARTEN: dict[str, re.Pattern[str]] = {
    "Datenbank": re.compile(r"\b(datenbank\w*|tabelle\w*|migration\w*|sql|spalte\w*)\b"),
    "Oberfläche": re.compile(
        r"\b(oberfläche\w*|ui|seite\w*|template\w*|css|knopf\w*|knöpf\w*|button\w*"
        r"|dialog\w*|ansicht\w*)\b"
    ),
    "Hook": re.compile(r"\bhook\w*"),
    "Skript": re.compile(r"\b(skript\w*|script\w*|cli)\b"),
    "Doku": re.compile(r"\b(doku\w*|docs|readme\w*)\b"),
    "Deploy": re.compile(r"\b(deploy\w*|gate\w*|server\w*)\b"),
    "Test": re.compile(r"\b(test\w*|beweis\w*)\b"),
    "API": re.compile(r"\b(api|endpoint\w*|route\w*)\b"),
}


def ist_schaetzung(wert: Any) -> bool:
    """Echte Schätzung = Zahl über 0; ``True``/``False`` zählen nie."""
    return isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert > 0


def _komma(wert: float) -> str:
    """Eine Nachkommastelle mit deutschem Dezimalkomma."""
    return f"{wert:.1f}".replace(".", ",")


def _zeitpunkt(text: Any) -> datetime | None:
    try:
        wert = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def _juengster(repo: Path, ticket: str) -> datetime:
    zeiten = [z for z in (_zeitpunkt(r.get("ts")) for r in lese(repo, ticket)) if z]
    return max(zeiten) if zeiten else datetime.min.replace(tzinfo=timezone.utc)


def _manifest_eintraege(repo: Path) -> dict[str, dict[str, Any]]:
    """Ticket → Manifest-Eintrag aus allen ``spec-<Zahl>.json`` (Fallback-Quelle)."""
    from . import manifest

    eintraege: dict[str, dict[str, Any]] = {}
    for datei in manifest.alle_manifeste(repo).values():
        try:
            daten, _ = manifest.lies_json(datei)
        except (OSError, ValueError) as fehler:
            log.warning("Manifest %s unlesbar: %s", datei, fehler)
            continue
        tickets = daten.get("tickets") if isinstance(daten, dict) else None
        if isinstance(tickets, dict):
            for nummer, eintrag in tickets.items():
                if isinstance(eintrag, dict):
                    eintraege[str(nummer)] = eintrag
    return eintraege


def umfang_art(text: str) -> str:
    """Umfang-Art eines Tickets, z. B. „Datenbank+Oberfläche“ (sonst „Sonstiges“)."""
    klein = (text or "").lower()
    treffer = sorted(name for name, muster in UMFANG_ARTEN.items() if muster.search(klein))
    return "+".join(treffer) or "Sonstiges"


def _faustregeln(zeilen: list[dict[str, Any]], grenze: float) -> list[str]:
    regeln = ["Faustregeln für den Schnitt:"]
    faktoren = [
        z["ist_k"] / z["schaetzung_k"]
        for z in zeilen
        if ist_schaetzung(z["schaetzung_k"]) and z["ist_k"]
    ]
    if faktoren:
        mittel = _komma(sum(faktoren) / len(faktoren))
        regeln.append(
            f"- Schätzungen lagen im Mittel bei Faktor {mittel} — Schätzung × {mittel} "
            f"muss unter {grenze:g}k bleiben."
        )
    je_art: dict[str, list[int]] = {}
    for z in zeilen:
        if z["sessions"] >= 1:
            art = umfang_art(f"{z['umfang'] or ''} {z['title'] or ''}")
            je_art.setdefault(art, []).append(z["sessions"])
    for art, anzahl in sorted(
        je_art.items(), key=lambda paar: (-sum(paar[1]) / len(paar[1]), paar[0])
    ):
        regeln.append(
            f"- {art} brauchte im Mittel {_komma(sum(anzahl) / len(anzahl))} Sessions "
            f"(n={len(anzahl)})"
        )
    staffel = [z["ticket"] for z in zeilen if z["staffel"] > 1]
    if staffel:
        regeln.append(
            f"- Staffel > 1 bei {len(staffel)} Tickets: "
            + ", ".join(f"#{t}" for t in staffel)
            + " — diese waren zu groß für eine Session."
        )
    else:
        regeln.append("- Kein Ticket brauchte eine zweite Staffel.")
    return regeln


def lernstoff(repo: Path, letzte: int = 30, grenze_k: float | None = None) -> str:
    """Die jüngsten Tickets (nach Zeitstempel) als Lernstoff für ``/to-tickets``."""
    tickets = sorted(alle_tickets(repo), key=lambda t: _juengster(repo, t), reverse=True)
    tickets = tickets[: max(letzte, 0)]
    if not tickets:
        return "Kein Bau-Log vorhanden — noch kein Lernstoff."
    if grenze_k is None:
        from . import config

        grenze_k = float(config.lade(repo).get("staffel", {}).get("grenze_k", 200))
    aus_manifest = _manifest_eintraege(repo)
    zeilen = ["Lernstoff aus dem Bau-Log (Schätzung → Ist, Sessions je Ticket, jüngste zuerst):"]
    daten: list[dict[str, Any]] = []
    for ticket in tickets:
        z = zusammenfassung(repo, ticket)
        eintrag = aus_manifest.get(str(ticket), {})
        if z["schaetzung_k"] is None:
            z["schaetzung_k"] = eintrag.get("schaetzung_k")
        z["title"] = z.get("title") or eintrag.get("title")
        if not z["umfang"]:
            z["umfang"] = eintrag.get("umfang") or z["title"]
        daten.append(z)
        schaetzung = z["schaetzung_k"]
        hat_schaetzung = ist_schaetzung(schaetzung)
        soll = f"{schaetzung:g}k" if hat_schaetzung else "ohne Schätzung"
        faktor = (
            f" (Faktor {_komma(z['ist_k'] / schaetzung)})" if hat_schaetzung and z["ist_k"] else ""
        )
        umfang = str(z["umfang"] or "").replace("\n", " ")[:110]
        zeilen.append(
            f"- #{z['ticket']}: {soll} geschätzt → {z['ist_k']:g}k ist{faktor}, "
            f"{z['sessions']} Session(s), {z['subagenten']} Subagent(en)"
            + (f" · {umfang}" if umfang else "")
        )
        schwer = str(z["schwierigkeiten"] or "").replace("\n", " ")[:110]
        if schwer:
            zeilen.append(f"  Schwierigkeiten: {schwer}")
    zeilen.append("")
    zeilen += _faustregeln(daten, grenze_k)
    return "\n".join(zeilen)
