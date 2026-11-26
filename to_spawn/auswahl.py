"""Ticket-Auswahl eines Teil-Starts je Spec und Rechner (#271).

Einzige Quelle für die Frage „welche Tickets und ob ein Aufseher/Takt gehören zu diesem
Rechner?“:

- Gespeichert unter ``<repo>/.to-spawn/auswahl-<S>.json`` (lokal je Klon/Rechner,
  ``.to-spawn/*`` ist gitignored).
- Ohne Datei bleibt das Verhalten wie bisher (alle Tickets der Spec, Aufseher und Takt
  erlaubt).
- Ist eine Datei vorhanden, aber unlesbar/kaputt, gilt fail-closed: keine Tickets und kein
  Aufseher erlaubt — besser nichts anfassen als fremde Tickets doppelt bauen.
- Gehört die Datei laut Feld ``rechner`` einem anderen Rechner, sind auf diesem Rechner
  weder Tickets noch Aufseher/Takt erlaubt.

Aufrufer: ``spawn_srv.sh`` / ``spawn_local.ps1`` (über ``to_spawn.py auswahl``),
``aufpasser``, ``aufseher_stand``, ``sessions_stand``, ``spawn.neustart``, ``capo``,
``hooks`` und ``waechter_takt``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.auswahl")

#: Umgebungsvariable, die den Rechnernamen überschreibt (Tests / feste Kennung).
RECHNER_ENV = "TO_SPAWN_RECHNER"
#: Markiert fehlgeschlagene/unlesbare Auswahl-Dateien (fail-closed).
_KAPUTT = "__kaputt__"


def aktueller_rechner() -> str:
    """Rechnername für den Abgleich je Spec und Rechner."""
    vorgabe = os.environ.get(RECHNER_ENV, "").strip()
    if vorgabe:
        return vorgabe
    try:
        return socket.gethostname().strip() or "unbekannt"
    except OSError:
        return "unbekannt"


def gleicher_rechner(a: str, b: str | None = None) -> bool:
    """Vergleicht zwei Rechnernamen ohne Groß-/Kleinschreibung und Rand-Leerzeichen (#191, #271).

    Einzige Vergleichs-Stelle für Teil-Auswahl und maschinenübergreifende Start-Sperre.
    ``b is None`` vergleicht gegen :func:`aktueller_rechner`. Leere Namen sind nie gleich.
    """
    links = a.strip().lower()
    rechts = (b if b is not None else aktueller_rechner()).strip().lower()
    return bool(links) and links == rechts


def pfad(repo: Path, spec: int | str) -> Path:
    """Ablageort der Auswahl-Datei im Repo (gitignored unter ``.to-spawn/``)."""
    return Path(repo) / ".to-spawn" / f"auswahl-{spec}.json"


@dataclass(frozen=True)
class Auswahl:
    """Gespeicherte Teil-Start-Auswahl einer Spec auf einem Rechner."""

    spec: int
    #: ``None`` = alle Tickets der Spec (z. B. Voll-Start mit ``--ohne-wache``).
    tickets: tuple[int, ...] | None
    ohne_wache: bool = False
    rechner: str = ""
    zeit: str = ""
    kaputt: bool = False

    def ist_eigener_rechner(self, rechner: str | None = None) -> bool:
        """Gilt diese Auswahl für den angegebenen bzw. aktuellen Rechner?"""
        if self.kaputt:
            return False
        if not self.rechner:
            return True
        return gleicher_rechner(self.rechner, rechner)

    def ist_teilauswahl(self, rechner: str | None = None) -> bool:
        """Begrenzt diese Auswahl die Ticket-Menge der Spec (nicht die volle Spec)?"""
        if self.kaputt or not self.ist_eigener_rechner(rechner):
            return True
        return self.tickets is not None

    def wache_erlaubt(self, rechner: str | None = None) -> bool:
        """Darf auf diesem Rechner ein Aufseher / Aufseher-Takt für die Spec laufen?"""
        if self.kaputt or not self.ist_eigener_rechner(rechner):
            return False
        return not self.ohne_wache

    def enthaelt(self, ticket: int | str, rechner: str | None = None) -> bool:
        """Gehört ``ticket`` zur Auswahl auf diesem Rechner?"""
        if self.kaputt or not self.ist_eigener_rechner(rechner):
            return False
        if self.tickets is None:
            return True
        try:
            nummer = int(ticket)
        except (TypeError, ValueError):
            return False
        return nummer in self.tickets


def _kaputte_auswahl(spec: int | str) -> Auswahl:
    nummer = int(spec) if str(spec).isdigit() else 0
    return Auswahl(spec=nummer, tickets=(), ohne_wache=True, rechner=_KAPUTT, kaputt=True)


def lesen(repo: Path, spec: int | str) -> Auswahl | None:
    """Gespeicherte Auswahl lesen.

    Fehlt die Datei, liefert die Funktion ``None`` (Verhalten wie bisher, volle Spec).
    Existiert die Datei, ist aber kaputt/unlesbar, liefert sie fail-closed eine gesperrte
    Auswahl (keine Tickets, kein Aufseher).
    """
    datei = pfad(repo, spec)
    try:
        roh = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as fehler:
        log.error("Auswahl-Datei %s nicht lesbar (%s) — fail-closed (nichts anfassen).", datei, fehler)
        return _kaputte_auswahl(spec)
    try:
        daten = json.loads(roh)
    except json.JSONDecodeError as fehler:
        log.error("Auswahl-Datei %s ist kaputt (%s) — fail-closed (nichts anfassen).", datei, fehler)
        return _kaputte_auswahl(spec)
    if not isinstance(daten, dict):
        log.error("Auswahl-Datei %s ist kein JSON-Objekt — fail-closed.", datei)
        return _kaputte_auswahl(spec)

    roh_tickets = daten.get("tickets")
    tickets: tuple[int, ...] | None
    if roh_tickets is None:
        tickets = None
    elif isinstance(roh_tickets, list):
        geparst: list[int] = []
        for eintrag in roh_tickets:
            if isinstance(eintrag, bool):
                log.error("Auswahl-Datei %s enthält ungültige Ticket-Nummer %r — fail-closed.", datei, eintrag)
                return _kaputte_auswahl(spec)
            try:
                geparst.append(int(eintrag))
            except (TypeError, ValueError):
                log.error("Auswahl-Datei %s enthält ungültige Ticket-Nummer %r — fail-closed.", datei, eintrag)
                return _kaputte_auswahl(spec)
        tickets = tuple(sorted(set(geparst)))
    else:
        log.error("Auswahl-Datei %s: Feld 'tickets' ist weder Liste noch null — fail-closed.", datei)
        return _kaputte_auswahl(spec)

    ohne_wache = bool(daten.get("ohne_wache", False))
    rechner = str(daten.get("rechner") or "").strip()
    zeit = str(daten.get("zeit") or "").strip()
    spec_nr = int(daten["spec"]) if str(daten.get("spec", "")).isdigit() else (int(spec) if str(spec).isdigit() else 0)
    return Auswahl(
        spec=spec_nr,
        tickets=tickets,
        ohne_wache=ohne_wache,
        rechner=rechner,
        zeit=zeit,
    )


def _schreibe_atomar(datei: Path, inhalt: dict[str, Any]) -> Path:
    datei.parent.mkdir(parents=True, exist_ok=True)
    neu = datei.with_name(f"{datei.name}.{os.getpid()}.neu")
    neu.write_text(json.dumps(inhalt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(neu, datei)
    return datei


def loeschen(repo: Path, spec: int | str) -> bool:
    """Entfernt eine gespeicherte Auswahl-Datei (zurück auf volle Spec)."""
    datei = pfad(repo, spec)
    try:
        datei.unlink()
        return True
    except FileNotFoundError:
        return False


def speichern(
    repo: Path,
    spec: int | str,
    tickets: Sequence[int | str] | None = None,
    *,
    ohne_wache: bool = False,
    zusammenfuehren: bool = True,
    nur_wache: bool = False,
    umzug: bool = False,
    rechner: str | None = None,
) -> Path | None:
    """Auswahl eines Starts speichern (atomar) oder bei Voll-Start aufheben.

    - Voll-Start (``tickets is None``, ``ohne_wache=False``, ``nur_wache=False``):
      löscht die Auswahl-Datei und liefert ``None``.
    - ``nur_wache=True``: hebt ``ohne_wache`` auf und behält eine vorhandene
      Ticket-Auswahl dieses Rechners (war keine Ticket-Begrenzung aktiv, wird die
      Datei entfernt).
    - ``zusammenfuehren=True`` (Standard): weitere ``--tickets``-Starts auf demselben
      Rechner erweitern die bestehende Auswahl (z. B. Nachstart/Umzug einzelner Tickets).
    - ``umzug=True``: schaltet einen auf diesem Rechner bereits aktiven Aufseher nicht ab.
    """
    host = (rechner if rechner is not None else aktueller_rechner()).strip()
    datei = pfad(repo, spec)

    if not nur_wache and tickets is None and not ohne_wache:
        loeschen(repo, spec)
        return None

    alt = lesen(repo, spec)
    alt_eigen = alt if (alt is not None and not alt.kaputt and alt.ist_eigener_rechner(host)) else None

    if nur_wache:
        if alt_eigen is None or alt_eigen.tickets is None:
            loeschen(repo, spec)
            return None
        neu_tickets: list[int] | None = list(alt_eigen.tickets)
        neu_ohne_wache = False
    else:
        geparst: list[int] | None = None
        if tickets is not None:
            geparst = sorted({int(t) for t in tickets})
        if geparst is None:
            neu_tickets = None
        elif zusammenfuehren and alt_eigen is not None and alt_eigen.tickets is not None:
            neu_tickets = sorted(set(alt_eigen.tickets) | set(geparst))
        else:
            neu_tickets = geparst

        if umzug and alt_eigen is not None and not alt_eigen.ohne_wache:
            neu_ohne_wache = False
        else:
            neu_ohne_wache = bool(ohne_wache)

    if neu_tickets is None and not neu_ohne_wache:
        loeschen(repo, spec)
        return None

    spec_wert: int | str = int(spec) if str(spec).isdigit() else str(spec)
    inhalt: dict[str, Any] = {
        "spec": spec_wert,
        "rechner": host,
        "tickets": neu_tickets,
        "ohne_wache": neu_ohne_wache,
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return _schreibe_atomar(datei, inhalt)


def ticket_erlaubt(
    repo: Path,
    spec: int | str,
    ticket: int | str,
    *,
    auswahl: Auswahl | None = None,
    gelesen: bool = False,
    rechner: str | None = None,
) -> bool:
    """Darf ``ticket`` auf diesem Rechner angefasst werden?"""
    stand = auswahl if (auswahl is not None or gelesen) else lesen(repo, spec)
    if stand is None:
        return True
    return stand.enthaelt(ticket, rechner)


def wache_erlaubt(
    repo: Path,
    spec: int | str,
    *,
    auswahl: Auswahl | None = None,
    gelesen: bool = False,
    rechner: str | None = None,
) -> bool:
    """Darf auf diesem Rechner ein Aufseher / Aufseher-Takt für ``spec`` laufen?"""
    stand = auswahl if (auswahl is not None or gelesen) else lesen(repo, spec)
    if stand is None:
        return True
    return stand.wache_erlaubt(rechner)


def filtere_nummern(
    nummern: Iterable[int],
    auswahl: Auswahl | None,
    *,
    rechner: str | None = None,
) -> list[int]:
    """Behält nur Ticket-Nummern, die laut ``auswahl`` auf diesem Rechner erlaubt sind."""
    if auswahl is None:
        return list(nummern)
    return [n for n in nummern if auswahl.enthaelt(n, rechner)]


def filtere_issues(
    issues: Sequence[dict[str, Any]],
    auswahl: Auswahl | None,
    *,
    rechner: str | None = None,
) -> list[dict[str, Any]]:
    """Behält nur GitHub-Issue-Objekte innerhalb der Auswahl dieses Rechners."""
    if auswahl is None:
        return list(issues)
    ergebnis: list[dict[str, Any]] = []
    for issue in issues:
        try:
            nummer = int(issue["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if auswahl.enthaelt(nummer, rechner):
            ergebnis.append(issue)
    return ergebnis


def beschreibung(auswahl: Auswahl | None, *, rechner: str | None = None) -> str:
    """Kurze, menschenlesbare Beschreibung für Fehlermeldungen und Kopfzeilen."""
    if auswahl is None:
        return "volle Spec"
    if auswahl.kaputt:
        return "Auswahl-Datei kaputt (fail-closed)"
    if not auswahl.ist_eigener_rechner(rechner):
        return f"gehört Rechner {auswahl.rechner}"
    teile: list[str] = []
    if auswahl.tickets is None:
        teile.append("alle Tickets")
    else:
        liste = ", ".join(f"#{n}" for n in auswahl.tickets) if auswahl.tickets else "keine"
        teile.append(f"Tickets {liste}")
    if auswahl.ohne_wache:
        teile.append("ohne Aufseher")
    if auswahl.rechner:
        teile.append(f"Rechner {auswahl.rechner}")
    return ", ".join(teile)


# --- CLI ------------------------------------------------------------------------


def _parse_tickets(roh: str | None) -> list[int] | None:
    if roh is None:
        return None
    werte = [t.strip() for t in roh.replace(";", ",").split(",") if t.strip()]
    if not werte:
        return None
    ergebnis: list[int] = []
    for w in werte:
        if not w.isdigit():
            raise ValueError(f"Ungültige Ticket-Nummer: {w!r}")
        ergebnis.append(int(w))
    return ergebnis


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser(
        "auswahl",
        help="Teil-Start-Auswahl je Spec und Rechner speichern, anzeigen oder löschen (#271)",
    )
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument("--tickets", default=None, help="gewählte Tickets (Komma-Liste)")
    p.add_argument("--ohne-wache", action="store_true", help="keinen Aufseher/Takt auf diesem Rechner")
    p.add_argument("--nur-wache", action="store_true", help="Aufseher auf diesem Rechner freigeben")
    p.add_argument("--umzug", action="store_true", help="Umzug: aktiven Aufseher nicht abschalten")
    p.add_argument(
        "--ersetzen",
        action="store_true",
        help="bestehende Auswahl ersetzen statt zusammenführen",
    )
    p.add_argument("--loeschen", action="store_true", help="Auswahl löschen (zurück auf volle Spec)")
    p.add_argument("--zeigen", action="store_true", help="gespeicherte Auswahl anzeigen")


def lauf(args: argparse.Namespace, repo: Path) -> int:
    if args.zeigen:
        stand = lesen(repo, args.spec)
        print(f"Spec #{args.spec}: {beschreibung(stand)}")
        return 0
    if args.loeschen:
        loeschen(repo, args.spec)
        return 0
    try:
        tickets = _parse_tickets(args.tickets)
    except ValueError as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 2
    speichern(
        repo,
        args.spec,
        tickets,
        ohne_wache=bool(args.ohne_wache),
        zusammenfuehren=not bool(args.ersetzen),
        nur_wache=bool(args.nur_wache),
        umzug=bool(args.umzug),
    )
    return 0
