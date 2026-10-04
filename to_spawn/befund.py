"""Befunde des Aufsehers: gelb → Folge-Ticket, nur rot öffnet wieder (#438).

Einzige Stelle der Regel, was ein Befund an einem geschlossenen Ticket auslöst:

* **rot** (Akzeptanz nicht erfüllt, Beleg fehlt): Ticket mit Kommentar
  ``Aufseher: <regel> — <text>`` wieder öffnen. capo nutzt :func:`wieder_oeffnen`
  für seine Maschinen-Verstöße (Commit/Beweis/Tests/VPS) — dieselben Zeilen.
* **gelb** (Hinweis, Kette darf weiter): neues Folge-Ticket auf GitHub anlegen und
  im Manifest der Spec eintragen (``gelb_von``, ``schaetzung_k``, ``umfang``,
  ``title``). Das Ticket selbst bleibt zu. Das Folge-Ticket wird bewusst kein
  Sub-Issue der Spec — sonst hielte ein verschobenes Ticket „Spec fertig“ auf.

Für die Gesamtabnahme liefert :func:`gelbe_folgen` die Folge-Tickets mit Status
(gebaut / verschoben / offen), :func:`abschnitt` die fertigen Ausgabezeilen.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gh, manifest

log = logging.getLogger("to_spawn.befund")

#: Kopf jedes Aufseher-Kommentars auf GitHub.
KOPF = "Aufseher:"
STUFEN = ("gelb", "rot")
#: Label, mit dem David ein gelbes Folge-Ticket bewusst auf später schiebt.
LABEL_VERSCHOBEN = "verschoben"
#: Vorgabe-Schätzung (Tausend Token) für ein gelbes Folge-Ticket — klein, ein Hinweis.
SCHAETZUNG_GELB_K = 30
#: Regel-Name im Reopen-Kommentar eines roten Aufseher-Befunds.
REGEL_ROT = "Belegseite"
UEBERSCHRIFT = "Gelbe Folge-Tickets (Gesamtabnahme)"

_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")

#: Abruf eines Issues: Nummer → GitHub-Daten (``state``, ``labels``) oder ``None``.
Abruf = Callable[[int], "dict[str, Any] | None"]


@dataclass(frozen=True)
class GelbeFolge:
    """Ein gelbes Folge-Ticket der Spec."""

    nummer: int
    gelb_von: int
    titel: str
    status: str  # gebaut | verschoben | offen | unbekannt


def melde(
    repo: Path,
    gh_repo: str,
    spec: int | str,
    ticket: int | str,
    stufe: str,
    text: str,
    dry_run: bool = False,
) -> list[str]:
    """Befund an Ticket ``ticket`` melden; das Modul entscheidet die Aktion.

    Gibt Ausgabezeilen zurück; eine Zeile mit ``FEHLER`` heißt: nichts ist passiert
    (kein halber Zustand). ``ValueError`` bei unbekannter Stufe.
    """
    if stufe not in STUFEN:
        raise ValueError(f"Stufe {stufe!r} unbekannt — erlaubt: {', '.join(STUFEN)}")
    n = int(ticket)
    if stufe == "rot":
        return wieder_oeffnen(n, gh_repo, [(REGEL_ROT, text)], dry_run)
    return _folge_ticket(repo, gh_repo, int(spec), n, text, dry_run)


def wieder_oeffnen(
    n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool
) -> list[str]:
    """Ticket einmal wieder öffnen; letzte Zeile ``#N wieder geöffnet`` = geklappt.

    ``gruende`` sind Paare (Regel, Text) — je Paar eine Kommentarzeile.
    """
    if dry_run:
        return [f"#{n} [Probe] würde wieder öffnen"]
    kommentar = "\n".join(f"{KOPF} {regel} — {text}" for regel, text in gruende)
    code, _ = gh.lauf(
        ["issue", "reopen", str(n), "--repo", gh_repo, "--comment", kommentar]
    )
    if code == 0:
        return [f"#{n} wieder geöffnet"]
    return [f"#{n} FEHLER: wieder öffnen gescheitert"]


def _titel(ticket: int, text: str) -> str:
    erste = (text.strip().splitlines() or [""])[0]
    kurz = erste if len(erste) <= 60 else erste[:59] + "…"
    return f"Gelb aus #{ticket}: {kurz}"


def _folge_ticket(
    repo: Path, gh_repo: str, spec: int, ticket: int, text: str, dry_run: bool
) -> list[str]:
    titel = _titel(ticket, text)
    datei = manifest.manifest_pfad(repo, spec)
    try:
        daten, _ = manifest.lies_json(datei)
    except (OSError, ValueError) as fehler:
        return [
            f"#{ticket} FEHLER: Manifest {datei.name} nicht lesbar ({fehler}) — kein Folge-Ticket"
        ]
    if not isinstance(daten, dict) or not isinstance(daten.get("tickets"), dict):
        return [
            f"#{ticket} FEHLER: Manifest {datei.name} ohne Ticket-Tabelle — kein Folge-Ticket"
        ]
    if dry_run:
        return [f"#{ticket} [Probe] gelb → würde Folge-Ticket „{titel}“ anlegen"]

    body = (
        f"Teil von Spec #{spec}, gelber Befund aus #{ticket} (Aufseher).\n\n"
        f"{text.strip()}\n\n"
        f"Steht im Manifest spec-{spec}.json mit `gelb_von: {ticket}`; "
        f"Label `{LABEL_VERSCHOBEN}` = bewusst auf später geschoben."
    )
    code, ausgabe = gh.lauf(
        ["issue", "create", "--repo", gh_repo, "--title", titel, "--body", body]
    )
    treffer = _ISSUE_URL.search(ausgabe or "")
    if code != 0 or not treffer:
        log.warning(
            "gh issue create gescheitert (Code %s): %s", code, (ausgabe or "")[:200]
        )
        return [
            f"#{ticket} FEHLER: gelbes Folge-Ticket nicht angelegt (gh issue create)"
        ]
    neu = treffer.group(1)
    daten["tickets"][neu] = {
        "title": titel,
        "schaetzung_k": SCHAETZUNG_GELB_K,
        "umfang": text.strip(),
        "gelb_von": ticket,
    }
    datei.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return [f"#{ticket} gelb → Folge-Ticket #{neu} angelegt, im Manifest {datei.name}"]


def _status(daten: dict[str, Any] | None) -> str:
    if not isinstance(daten, dict):
        return "unbekannt"
    if str(daten.get("state", "")).lower() == "closed":
        return "gebaut"
    namen = {
        str(e.get("name")) for e in daten.get("labels") or [] if isinstance(e, dict)
    }
    return "verschoben" if LABEL_VERSCHOBEN in namen else "offen"


def _abruf_github(repo: Path) -> Abruf:
    slug = gh.repo_aus_origin(repo)

    def abruf(n: int) -> dict[str, Any] | None:
        if not slug:
            return None
        daten = gh.json_lauf(["api", f"repos/{slug}/issues/{n}"], cwd=repo)
        return daten if isinstance(daten, dict) else None

    return abruf


def gelbe_folgen(
    repo: Path, spec: int | str, abruf: Abruf | None = None
) -> list[GelbeFolge]:
    """Gelbe Folge-Tickets der Spec mit Status; leer ohne Manifest.

    ``abruf`` holt je Nummer die GitHub-Daten (Vorgabe: ``gh api issues/<n>``).
    """
    try:
        daten, _ = manifest.lies_json(manifest.manifest_pfad(repo, spec))
    except (OSError, ValueError) as fehler:
        log.warning("Manifest spec-%s nicht lesbar: %s", spec, fehler)
        return []
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    if not isinstance(tickets, dict):
        return []
    holen = abruf or _abruf_github(repo)
    folgen: list[GelbeFolge] = []
    for nummer in sorted(tickets, key=manifest.ticket_schluessel):
        eintrag = tickets[nummer]
        if (
            not isinstance(eintrag, dict)
            or "gelb_von" not in eintrag
            or not nummer.isdigit()
        ):
            continue
        n = int(nummer)
        folgen.append(
            GelbeFolge(
                nummer=n,
                gelb_von=int(eintrag["gelb_von"]),
                titel=str(eintrag.get("title", "")),
                status=_status(holen(n)),
            )
        )
    return folgen


def abschnitt(folgen: Sequence[GelbeFolge]) -> list[str]:
    """Ausgabezeilen für die Gesamtabnahme; leer, wenn es keine gelben Folgen gibt."""
    if not folgen:
        return []
    return [UEBERSCHRIFT] + [
        f"#{f.nummer} {f.status:<10} aus #{f.gelb_von}  {f.titel}" for f in folgen
    ]
