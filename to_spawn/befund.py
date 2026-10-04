"""Befunde des Aufsehers: gelb → Folge-Ticket, nur rot öffnet wieder (#438).

Einzige Stelle der Regel, was ein Befund an einem geschlossenen Ticket auslöst:

* **rot** (Akzeptanz nicht erfüllt, Beleg fehlt): Ticket mit Kommentar
  ``Aufseher: <regel> — <text>`` wieder öffnen. capo nutzt :func:`wieder_oeffnen`
  für seine Maschinen-Verstöße (Commit/Beweis/Tests/VPS) — dieselben Zeilen.
* **gelb** (Hinweis, Kette darf weiter): neues Folge-Ticket auf GitHub anlegen und
  im Manifest der Spec eintragen (``gelb_von``, ``schaetzung_k``, ``umfang``,
  ``title``). Das Ticket selbst bleibt zu. Das Folge-Ticket wird bewusst kein
  Sub-Issue der Spec — sonst hielte ein verschobenes Ticket „Spec fertig“ auf.

:func:`melde` liefert ein :class:`Ergebnis` (``ok`` + Ausgabezeilen); der Exit-Code
der CLI hängt nur an ``ok``, nie an Textsuche in den Zeilen.

Für die Gesamtabnahme liefert :func:`gelbe_folgen` die Folge-Tickets mit Status
(gebaut / verworfen / verschoben / offen / unbekannt), :func:`abschnitt` die fertigen
Ausgabezeilen — ein kaputtes Manifest wird dort als FEHLER-Zeile sichtbar.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

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

#: Abruf eines Issues: Nummer → GitHub-Daten (``state``, ``state_reason``, ``labels``)
#: oder ``None``, wenn der Abruf scheitert.
Abruf: TypeAlias = Callable[[int], dict[str, Any] | None]


@dataclass(frozen=True)
class Ergebnis:
    """Ausgang von :func:`melde`: ``ok`` = Befund vollständig verarbeitet."""

    ok: bool
    zeilen: list[str]


@dataclass(frozen=True)
class GelbeFolge:
    """Ein gelbes Folge-Ticket der Spec."""

    nummer: int
    gelb_von: int
    titel: str
    status: str  # gebaut | verworfen | verschoben | offen | unbekannt


def melde(
    repo: Path,
    gh_repo: str,
    spec: int | str,
    ticket: int | str,
    stufe: str,
    text: str,
    dry_run: bool = False,
) -> Ergebnis:
    """Befund an Ticket ``ticket`` melden; das Modul entscheidet die Aktion.

    ``ok=False`` heißt: der Befund ist nicht vollständig verarbeitet — die Zeilen
    sagen, was fehlt (ggf. was von Hand nachzutragen ist). Ein schon gemeldeter
    gleicher gelber Befund ist ``ok`` (kein zweites Folge-Ticket).
    ``ValueError`` bei unbekannter Stufe.
    """
    if stufe not in STUFEN:
        raise ValueError(f"Stufe {stufe!r} unbekannt — erlaubt: {', '.join(STUFEN)}")
    n = int(ticket)
    if stufe == "rot":
        return _oeffne(n, gh_repo, [(REGEL_ROT, text)], dry_run)
    return _folge_ticket(repo, gh_repo, int(spec), n, text, dry_run)


def wieder_oeffnen(
    n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool
) -> list[str]:
    """Ticket einmal wieder öffnen; letzte Zeile ``#N wieder geöffnet`` = geklappt.

    ``gruende`` sind Paare (Regel, Text) — je Paar eine Kommentarzeile. Zeilen-API
    für capo (prüft den Zeilentext); :func:`melde` nutzt dieselbe Regel mit ok-Flag.
    """
    return _oeffne(n, gh_repo, gruende, dry_run).zeilen


def _oeffne(
    n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool
) -> Ergebnis:
    if dry_run:
        return Ergebnis(ok=True, zeilen=[f"#{n} [Probe] würde wieder öffnen"])
    kommentar = "\n".join(f"{KOPF} {regel} — {text}" for regel, text in gruende)
    code, _ = gh.lauf(
        ["issue", "reopen", str(n), "--repo", gh_repo, "--comment", kommentar]
    )
    if code == 0:
        return Ergebnis(ok=True, zeilen=[f"#{n} wieder geöffnet"])
    return Ergebnis(ok=False, zeilen=[f"#{n} FEHLER: wieder öffnen gescheitert"])


def zuruecknehmen(n: int, gh_repo: str, text: str, dry_run: bool) -> list[str]:
    """Eigenes Fehl-Reopen zurücknehmen: Ticket wieder schließen, mit Kommentar (#448).

    Letzte Zeile ``#N Fehl-Reopen zurückgenommen`` = geklappt; eine ``FEHLER``-Zeile =
    gescheitert (der nächste Tick versucht es wieder). Ob zurückgenommen werden darf,
    entscheidet der Aufrufer (capo).
    """
    if dry_run:
        return [f"#{n} [Probe] würde Fehl-Reopen zurücknehmen"]
    kommentar = f"{KOPF} Fehl-Reopen zurückgenommen — {text}"
    code, _ = gh.lauf(
        ["issue", "close", str(n), "--repo", gh_repo, "--comment", kommentar]
    )
    if code == 0:
        return [f"#{n} Fehl-Reopen zurückgenommen"]
    log.warning("Fehl-Reopen #%s nicht zurückgenommen (gh Exit %s).", n, code)
    return [f"#{n} FEHLER: Fehl-Reopen zurücknehmen gescheitert"]


def _titel(ticket: int, text: str) -> str:
    erste = (text.strip().splitlines() or [""])[0]
    kurz = erste if len(erste) <= 60 else erste[:59] + "…"
    return f"Gelb aus #{ticket}: {kurz}"


def _gelb_von(eintrag: Any) -> int | None:
    """``gelb_von`` eines Manifest-Eintrags als Zahl; ``None`` = kein/kaputter Wert."""
    if not isinstance(eintrag, dict) or "gelb_von" not in eintrag:
        return None
    try:
        return int(eintrag["gelb_von"])
    except (TypeError, ValueError):
        return None


def _schon_gemeldet(tickets: dict[str, Any], ticket: int, umfang: str) -> str | None:
    """Nummer eines vorhandenen Folge-Tickets mit gleichem Ursprung und Befundtext."""
    for nummer, eintrag in tickets.items():
        if _gelb_von(eintrag) != ticket:
            continue
        if str(eintrag.get("umfang", "")).strip() == umfang:
            return nummer
    return None


def _schreibe_atomar(datei: Path, daten: dict[str, Any]) -> None:
    """JSON über eine Zwischendatei im selben Ordner + ``os.replace`` schreiben.

    Ein Abbruch lässt die alte Datei ganz stehen; die Zwischendatei wird aufgeräumt.
    ``OSError`` geht an den Aufrufer.
    """
    inhalt = json.dumps(daten, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=datei.parent, prefix=f".{datei.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(inhalt)
        os.replace(tmp, datei)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _folge_ticket(
    repo: Path, gh_repo: str, spec: int, ticket: int, text: str, dry_run: bool
) -> Ergebnis:
    titel = _titel(ticket, text)
    umfang = text.strip()
    datei = manifest.manifest_pfad(repo, spec)
    try:
        daten, _ = manifest.lies_json(datei)
    except (OSError, ValueError) as fehler:
        return Ergebnis(
            ok=False,
            zeilen=[
                f"#{ticket} FEHLER: Manifest {datei.name} nicht lesbar ({fehler}) — kein Folge-Ticket"
            ],
        )
    if not isinstance(daten, dict) or not isinstance(daten.get("tickets"), dict):
        return Ergebnis(
            ok=False,
            zeilen=[
                f"#{ticket} FEHLER: Manifest {datei.name} ohne Ticket-Tabelle — kein Folge-Ticket"
            ],
        )
    vorhanden = _schon_gemeldet(daten["tickets"], ticket, umfang)
    if vorhanden is not None:
        return Ergebnis(
            ok=True,
            zeilen=[f"#{ticket} gelb schon gemeldet: Folge-Ticket #{vorhanden}"],
        )
    if dry_run:
        return Ergebnis(
            ok=True,
            zeilen=[f"#{ticket} [Probe] gelb → würde Folge-Ticket „{titel}“ anlegen"],
        )

    body = (
        f"Teil von Spec #{spec}, gelber Befund aus #{ticket} (Aufseher).\n\n"
        f"{umfang}\n\n"
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
        return Ergebnis(
            ok=False,
            zeilen=[
                f"#{ticket} FEHLER: gelbes Folge-Ticket nicht angelegt (gh issue create)"
            ],
        )
    neu = treffer.group(1)
    daten["tickets"][neu] = {
        "title": titel,
        "schaetzung_k": SCHAETZUNG_GELB_K,
        "umfang": umfang,
        "gelb_von": ticket,
    }
    try:
        _schreibe_atomar(datei, daten)
    except OSError as fehler:
        log.error(
            "Manifest %s nicht geschrieben (Folge-Ticket #%s): %s", datei, neu, fehler
        )
        return Ergebnis(
            ok=False,
            zeilen=[
                (
                    f"#{ticket} FEHLER: Folge-Ticket #{neu} angelegt, Manifest nicht "
                    f"geschrieben — von Hand eintragen ({datei.name}, gelb_von: {ticket})"
                )
            ],
        )
    return Ergebnis(
        ok=True,
        zeilen=[
            f"#{ticket} gelb → Folge-Ticket #{neu} angelegt, im Manifest {datei.name}"
        ],
    )


def _status(daten: dict[str, Any] | None) -> str:
    if not isinstance(daten, dict):
        return "unbekannt"
    if str(daten.get("state", "")).lower() == "closed":
        grund = str(daten.get("state_reason") or "").lower()
        return "verworfen" if grund == "not_planned" else "gebaut"
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


class ManifestKaputt(ValueError):
    """Manifest der Spec ist da, aber nicht lesbar (kaputtes JSON, keine Ticket-Tabelle)."""


def gelbe_folgen(
    repo: Path, spec: int | str, abruf: Abruf | None = None
) -> list[GelbeFolge]:
    """Gelbe Folge-Tickets der Spec mit Status; leer ohne Manifest.

    ``abruf`` holt je Nummer die GitHub-Daten (Vorgabe: ``gh api issues/<n>``).
    :class:`ManifestKaputt`, wenn das Manifest da, aber nicht lesbar ist.
    Einträge mit kaputtem ``gelb_von`` werden mit Warnung übersprungen.
    """
    datei = manifest.manifest_pfad(repo, spec)
    try:
        daten, _ = manifest.lies_json(datei)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as fehler:
        raise ManifestKaputt(f"{datei.name} nicht lesbar ({fehler})") from fehler
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    if not isinstance(tickets, dict):
        raise ManifestKaputt(f"{datei.name} ohne Ticket-Tabelle")
    holen = abruf or _abruf_github(repo)
    folgen: list[GelbeFolge] = []
    for nummer in sorted(tickets, key=manifest.ticket_schluessel):
        eintrag = tickets[nummer]
        if not isinstance(eintrag, dict) or "gelb_von" not in eintrag:
            continue
        von = _gelb_von(eintrag)
        if von is None or not nummer.isdigit():
            log.warning(
                "Manifest spec-%s: Eintrag %s mit kaputtem gelb_von %r — übersprungen",
                spec,
                nummer,
                eintrag.get("gelb_von"),
            )
            continue
        n = int(nummer)
        github = holen(n)
        if github is None:
            log.warning("Status von Folge-Ticket #%s nicht abrufbar — unbekannt", n)
        folgen.append(
            GelbeFolge(
                nummer=n,
                gelb_von=von,
                titel=str(eintrag.get("title", "")),
                status=_status(github),
            )
        )
    return folgen


def abschnitt(repo: Path, spec: int | str, abruf: Abruf | None = None) -> list[str]:
    """Ausgabezeilen für die Gesamtabnahme.

    Leer, wenn es keine gelben Folgen (oder kein Manifest) gibt; ein kaputtes
    Manifest erscheint als FEHLER-Zeile statt als leere Liste.
    """
    try:
        folgen = gelbe_folgen(repo, spec, abruf=abruf)
    except ManifestKaputt as fehler:
        log.warning("Gelbe Folgen spec-%s: %s", spec, fehler)
        return [UEBERSCHRIFT, f"FEHLER: Manifest {fehler} — gelbe Folgen unbekannt"]
    if not folgen:
        return []
    return [UEBERSCHRIFT] + [
        f"#{f.nummer} {f.status:<10} aus #{f.gelb_von}  {f.titel}" for f in folgen
    ]
