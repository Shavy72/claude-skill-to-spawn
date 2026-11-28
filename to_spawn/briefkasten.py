"""Entscheidungs-Briefkasten (Tür B, #278; Lastenheft L8 A45–A48).

Einziges Modul, das Ort, Dateimuster, Format und Ablage der Fragen kennt (Hauptbaum, gitignored
``.to-spawn/``). Aufrufer fragen nur:

- :func:`einwerfen` — die Session wirft eine Frage mit eigenem Vorschlag ein und bekommt deren
  ``id``. Die Klasse ordnet der Briefkasten selbst ein (:func:`befugnis.ist_klasse_2`); eine
  genannte Klasse macht nur strenger. Nur das eigene Ticket (``TO_SPAWN_TICKET``).
- :func:`lesen` — :class:`Antwort` (nur zur passenden ``id``, auch die des Nutzers),
  :data:`WARTET`, :data:`NUTZER` oder :data:`UNBEKANNT` (``id`` nicht im Briefkasten → neu fragen).
- :func:`antworten` — der Aufseher antwortet auf eine offene Frage über ihre ``id``; verweigert
  (:class:`Verweigert`) in einer Bau-Session und solange das Ticket auf den Nutzer wartet.
- :func:`nutzer_antworten` — Antwort des Nutzers (auch Klasse 2); verweigert in einer Bau-Session
  und verlangt bei eingerichteten Handy-Schlüsseln (``klasse_2.oeffentliche_schluessel`` / System-Datei)
  einen kryptographischen Ed25519-Beweis (#312).
- :func:`beweis_anfrage` — liefert die Prüf-Anforderung (``ticket``, ``id``, ``challenge``,
  ``frage_sha256``) einer offenen Frage zum Signieren auf dem Handy (#312).
- :func:`beweis_einreichen` — prüft ein vom Handy signiertes ``TO-SPAWN-K2-V1``-Token gegen die
  vertrauenswürdigen öffentlichen Schlüssel und schreibt die Antwort über :func:`nutzer_antworten` (#312).
- :func:`offene` — alle offenen Fragen (je Ticket, ``id``, Klasse) samt Sperre durch kaputte Datei.
- :func:`alle_tickets` — alle Tickets mit Briefkasten-Datei (auch nur ``.kaputt-*``).
- :func:`liste` — die eine Liste für die Gesamtabnahme: Klasse-1-Einträge aus dem Bau-Log plus alle
  Briefkasten-Antworten (wer, was, warum, Beleg); keine eigene Datei.
- :func:`datei_relativ` — Ort der Datei für Texte (Start-Auftrag), relativ zum Hauptbaum.

Eine Datei je Ticket hält alle Fragen in Reihenfolge. Klasse 2 gibt nur der Nutzer frei; eine
offene Klasse-2-Frage (ohne Antwort ``von`` :data:`NUTZER_VON`) sperrt das ganze Ticket. Eine
vorhandene, aber nicht nutzbare Datei (halb, ungültig, falsche Form, Lesefehler) sperrt sofort
genauso: :data:`NUTZER`, sichtbar in :func:`offene` (sie könnte eine offene Klasse-2-Frage
enthalten; Entscheidung an einer Stelle, :func:`_sperrgruende`). Ungültigen Inhalt legt
:func:`einwerfen` als ``entscheidung-<N>.json.kaputt-<stempel>`` beiseite, die sperrt weiter;
geklärt ist es, wenn der Nutzer sie geprüft und gelöscht hat. Ein Lesefehler (``OSError``)
verschiebt nichts — die Datei kann gültig sein. Jedes Lesen-Ändern-Schreiben läuft unter :func:`sperre.halte` auf
``entscheidung-<N>.lock``.

Schutz des Nutzer-Wegs, ehrlich: Ohne eingerichtete Handy-Schlüssel (``klasse_2.oeffentliche_schluessel``
oder System-Schlüsseldatei) verhindert der lokale Weg (``TO_SPAWN_TICKET``, ``--von``, Terminal /
``hook-ja-sperre``) versehentliche Selbst-Freigabe, nicht aber direktes Überschreiben der Datei.
Mit eingerichteten öffentlichen Ed25519-Schlüsseln (#312, Variante a) liegt der private Schlüssel
ausschließlich auf dem Handy: jede Klasse-2-Antwort wird beim Schreiben UND bei jedem Lesen
(:func:`_gilt`, :func:`lesen`, :func:`offene`, :func:`liste`) kryptographisch geprüft — selbst eine
Session mit allen Rechten des Besitzer-Kontos, umgebauter Umgebung und direktem Dateizugriff kann
keine gültige Signatur fälschen. Einen unsignierten Kommentar-Weg gibt es weiterhin bewusst nicht.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shlex
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from to_spawn import bau_log, befugnis, config, klasse2_beweis, manifest, sperre

log = logging.getLogger("to_spawn.briefkasten")

WARTET = "WARTET"
NUTZER = "NUTZER"
#: Die ``id`` steht nicht (mehr) im Briefkasten (Datei gelöscht oder beiseitegelegt) → Frage neu stellen.
UNBEKANNT = "UNBEKANNT"
#: ``von`` einer Antwort, die eine Klasse-2-Frage schließt (nie über :func:`antworten` schreibbar).
NUTZER_VON = "nutzer"
#: Einziges ``von`` für :func:`antworten` (feste Auswahl, CLI ``--von``).
AUFSEHER_VON = ("aufseher",)
_KLASSEN = (2, 3)  # Klasse 1 braucht keine Frage
_NUMMER = re.compile(r"[0-9]+")  # nur ASCII-Ziffern (int() nähme auch „٧“)
_ORDNER = ".to-spawn"
_PRAEFIX = "entscheidung-"
#: So lange wartet ein Schreiber auf die Sperre, dann ``TimeoutError``.
SPERRE_WARTEN_S = 10.0


class Verweigert(Exception):
    """Der Briefkasten nimmt diese Handlung nicht an (Klasse 2, Bau-Session, fremdes Ticket)."""


@dataclass(frozen=True)
class Antwort:
    """Verbindliche Antwort des Aufsehers auf eine Frage."""

    wahl: str
    grund: str
    belege: list[str]
    von: str
    ts: str


def datei_relativ(n: str | int) -> str:
    """Ort der Entscheidungs-Datei für Ticket ``n``, relativ zum Hauptbaum (ohne Prüfung, nur Text)."""
    return f"{_ORDNER}/{_PRAEFIX}{n}.json"


def _nummer(n: str | int) -> int:
    """Ticket-Nummer als ``int``; ``ValueError`` bei allem außer ASCII-Ziffern (Trust-Grenze)."""
    nummer = str(n).strip()
    if not _NUMMER.fullmatch(nummer):  # Nummer wird Teil des Dateinamens
        raise ValueError(f"Ticket-Nummer {n!r} ist keine Zahl.")
    return int(nummer)


def _eigenes_ticket() -> int | None:
    """Nummer aus ``TO_SPAWN_TICKET`` (setzt ``bau.py`` = Bau-Session) oder ``None`` (fehlt ganz).

    Gesetzt, aber leer oder unlesbar (``TO_SPAWN_TICKET=``) gilt als Bau-Session und passt zu
    keinem Ticket (-1). Der Aufseher-Takt entfernt die Variable ganz (``waechter_takt._kind_env``).
    """
    wert = os.environ.get("TO_SPAWN_TICKET")
    if wert is None:
        return None
    try:
        return _nummer(wert)
    except ValueError:
        return -1


def _hauptbaum(hauptbaum: Path | None) -> Path:
    """Vorgabe: Hauptbaum aus der Umgebung (``bau.py`` setzt ihn), sonst die Git-Wurzel."""
    return hauptbaum or bau_log.log_rueckfall() or config.repo_wurzel()


def _datei(n: str | int, hauptbaum: Path | None) -> Path:
    return _hauptbaum(hauptbaum) / datei_relativ(_nummer(n))


@contextmanager
def _gesperrt(datei: Path) -> Iterator[None]:
    """Sperre für Lesen-Ändern-Schreiben; ``TimeoutError``, wenn sie belegt bleibt."""
    with sperre.halte(datei.with_suffix(".lock"), warten=SPERRE_WARTEN_S) as gehalten:
        if not gehalten:
            raise TimeoutError(f"Briefkasten {datei} bleibt gesperrt.")
        yield


@dataclass(frozen=True)
class _Inhalt:
    """Ergebnis von :func:`_lies`: ``daten`` nur bei gültigem Inhalt, sonst ``None``.

    ``stoerung`` (leer = keine) nennt, warum eine vorhandene Datei nicht nutzbar ist — sie sperrt
    das Ticket (:func:`_sperrgruende`). ``beiseitelegen`` nur bei ungültigem Inhalt, nie bei einem
    Lesefehler (die Datei selbst kann gültig sein).
    """

    daten: dict[str, Any] | None
    stoerung: str = ""
    beiseitelegen: bool = False


def _lies(datei: Path) -> _Inhalt:
    """Datei lesen; unterscheidet fehlt (kein Sperrgrund), ungültig (auch ungültige Bytes) und
    Lesefehler (``OSError``)."""
    kaputt = _Inhalt(None, f"Briefkasten-Datei kaputt: {datei.name} — prüfen, reparieren oder löschen", True)
    try:
        text = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _Inhalt(None)
    except OSError as fehler:
        log.warning("Briefkasten %s nicht lesbar (%s) — wartet auf den Nutzer.", datei, fehler)
        return _Inhalt(None, f"Briefkasten-Datei {datei.name} nicht lesbar ({fehler}) — Zugriff prüfen")
    except UnicodeDecodeError as fehler:
        log.warning("Briefkasten %s mit ungültigen Bytes (%s) — wartet auf den Nutzer.", datei, fehler)
        return kaputt
    try:
        daten = json.loads(text)
    except ValueError as fehler:
        log.warning("Briefkasten %s ungültig (%s) — wartet auf den Nutzer.", datei, fehler)
        return kaputt
    if not isinstance(daten, dict) or not isinstance(daten.get("fragen"), list):
        return kaputt
    if not all(isinstance(f, dict) for f in daten["fragen"]):
        return kaputt
    return _Inhalt(daten)


def _lade(datei: Path) -> dict[str, Any] | None:
    """Gültiger Inhalt oder ``None`` (fehlt, halb, kaputt, falsche Form, Lesefehler).

    Eine vorhandene, aber nicht nutzbare Datei wird nicht still übersprungen: ``log.warning``.
    """
    inhalt = _lies(datei)
    if inhalt.stoerung:
        log.warning("Briefkasten %s übersprungen: %s", datei.name, inhalt.stoerung)
    return inhalt.daten


def _jetzt() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ist_klasse_2(f: Mapping[str, Any]) -> bool:
    """Klasse 2, wenn gespeichert ODER der Text anschlägt — eine von Hand gesenkte Klasse zählt nicht."""
    return f.get("klasse") != 3 or befugnis.ist_klasse_2(
        f.get("frage"), f.get("vorschlag"), f.get("ziel"), f.get("art")
    )


#: Live-Zielwörter, die eine Abnahme-Freigabe („passt“) deckt — alle übrigen Klasse-2-Wörter bleiben gesperrt (#924).
_LIVE_ZIEL_WOERTER = frozenset({"live", "prod"})
_LIVE_ART_WOERTER = frozenset(
    {"deploy", "deployen", "ausrollen", "live", "schalten", "freigabe", "freigeben", "starten"}
)
_LOESCH_WOERTER = frozenset({"lösch", "loesch", "delete", "entfern", "drop", "truncate", "destroy"})
_NICHT_LIVE_WOERTER = (befugnis._KLASSE_2_WOERTER - {"live", "prod", "vps"}) | _LOESCH_WOERTER


def _enthaelt_nicht_live_klasse_2(f: Mapping[str, Any]) -> bool:
    """True, sobald Frage, Vorschlag, Ziel oder Art ein Nicht-Live-Klasse-2-Wort trägt (Kosten, Konto, Löschen…)."""
    for feld in (f.get("frage"), f.get("vorschlag"), f.get("ziel"), f.get("art")):
        norm = befugnis._norm(feld)  # einzige Normalisierung aus befugnis
        if not norm:
            continue
        teile = re.split(r"\W+", norm)
        if any(w in norm for w in _NICHT_LIVE_WOERTER) or any(w in teile for w in befugnis._KLASSE_2_GANZWORT):
            return True
    return False


def ist_live_frage(f: Mapping[str, Any]) -> bool:
    """True, wenn ``f`` eine reine Live-Deploy-Frage ist (kein Kosten-/Konto-/Zugangs-/Lösch-Thema, #924)."""
    if not str(f.get("id") or "").strip() or not _ist_klasse_2(f):
        return False
    if _enthaelt_nicht_live_klasse_2(f):
        return False
    ziel = befugnis._norm(f.get("ziel"))
    art = befugnis._norm(f.get("art"))
    frage = befugnis._norm(f.get("frage"))
    ziel_live = any(w in ziel for w in _LIVE_ZIEL_WOERTER)
    art_teile = set(re.split(r"\W+", art)) if art else set()
    art_live = bool(art_teile & _LIVE_ART_WOERTER)
    if ziel_live and (not art or art_live):
        return True
    return art_live and any(w in frage for w in _LIVE_ZIEL_WOERTER)


def hat_offene_live_fragen(tickets: Iterable[str | int], *, hauptbaum: Path | None = None) -> bool:
    """Rein lokal: liegt für mindestens ein Ticket in ``tickets`` eine offene Live-Frage im Briefkasten? (#924)"""
    basis = _hauptbaum(hauptbaum)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if konfig_fehler:
        return False
    for t in tickets:
        try:
            nummer = _nummer(t)
            datei = _datei(nummer, basis)
        except ValueError:
            continue
        inhalt = _lies(datei)
        if _sperrgruende(datei, inhalt):
            continue
        if any(
            not _gilt(f, ticket=str(nummer), schluessel=schluessel) and ist_live_frage(f)
            for f in (inhalt.daten or {}).get("fragen", [])
        ):
            return True
    return False


def live_fragen_freigeben(
    tickets: Iterable[str | int],
    abnahme_ticket: int,
    beleg_id: str | int = "",
    *,
    hauptbaum: Path | None = None,
) -> list[tuple[str, str]]:
    """Beantwortet offene Live-Fragen der Spec nach Abnahme-„passt“ mit „ja“ über :func:`nutzer_antworten` (#924).

    Nicht-Live-Klasse-2-Fragen (Kosten, Konto, Zugang, Löschen) und kaputte Dateien bleiben unberührt.
    In einer Bau-Session (``TO_SPAWN_TICKET``) verweigert :func:`nutzer_antworten`.
    """
    basis = _hauptbaum(hauptbaum)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if konfig_fehler:
        return []
    gesehen: set[int] = set()
    beantwortet: list[tuple[str, str]] = []
    beleg = (
        f"Abnahme-Freigabe #{abnahme_ticket} (id {beleg_id})"
        if str(beleg_id).strip()
        else f"Abnahme-Freigabe #{abnahme_ticket}"
    )
    grund = f"Abnahme „passt“ an #{abnahme_ticket} gilt als Live-Freigabe (#924)"
    for t in tickets:
        try:
            nummer = _nummer(t)
        except ValueError:
            continue
        if nummer in gesehen:
            continue
        gesehen.add(nummer)
        datei = _datei(nummer, basis)
        inhalt = _lies(datei)
        if _sperrgruende(datei, inhalt):
            continue
        offen_ids = [
            str(f["id"])
            for f in (inhalt.daten or {}).get("fragen", [])
            if not _gilt(f, ticket=str(nummer), schluessel=schluessel) and ist_live_frage(f)
        ]
        for fid in offen_ids:
            try:
                nutzer_antworten(nummer, fid, "ja", [beleg], grund, hauptbaum=basis)
            except ValueError:
                continue  # zeitgleich bereits beantwortet
            beantwortet.append((str(nummer), fid))
    return beantwortet


def _schluessel_stand(basis: Path) -> tuple[tuple[klasse2_beweis.OeffentlicherSchluessel, ...], str]:
    """Lädt vertrauenswürdige Handy-Schlüssel für ``basis``; Fehler liefern einen Sperrgrund (fail-closed)."""
    try:
        return klasse2_beweis.vertraute_schluessel(basis), ""
    except klasse2_beweis.BeweisKonfigFehler as fehler:
        log.warning("Klasse-2-Schlüssel-Konfiguration ungültig (%s) — fail-closed gesperrt.", fehler)
        return (), f"Klasse-2-Schlüssel-Konfiguration ungültig ({fehler}) — nur der Nutzer klärt das"


def _hat_antwort(f: dict[str, Any]) -> bool:
    antwort = f.get("antwort")
    return isinstance(antwort, dict) and bool(str(antwort.get("wahl") or "").strip())


def _gilt(
    f: dict[str, Any],
    *,
    ticket: str = "",
    schluessel: Sequence[klasse2_beweis.OeffentlicherSchluessel] = (),
) -> bool:
    """Hat ``f`` eine gültige Antwort? Klasse 2 nur ``von`` :data:`NUTZER_VON` — und bei
    eingerichteten Handy-Schlüsseln gilt jede Antwort mit ``von == NUTZER_VON`` (unabhängig
    von der Frage-Klasse) nur mit kryptographisch gültigem Ed25519-Beweis (#312)."""
    if not _hat_antwort(f):
        return False
    antwort = f["antwort"]
    von_nutzer = antwort.get("von") == NUTZER_VON
    if _ist_klasse_2(f) and not von_nutzer:
        return False
    if not (schluessel and von_nutzer):
        return True
    token = antwort.get("beweis")
    if not isinstance(token, str) or not token.strip():
        return False
    try:
        klasse2_beweis.beweis_pruefen(
            token,
            schluessel,
            ticket=ticket,
            frage_id=str(f.get("id") or ""),
            frage=str(f.get("frage") or ""),
            challenge=str(f.get("challenge") or ""),
            wahl=str(antwort.get("wahl") or ""),
            grund=str(antwort.get("grund") or ""),
        )
    except klasse2_beweis.BeweisFehler:
        return False
    return True


def _klasse_2_offen(
    fragen: list[dict[str, Any]],
    *,
    ticket: str = "",
    schluessel: Sequence[klasse2_beweis.OeffentlicherSchluessel] = (),
) -> bool:
    """Irgendeine Klasse-2-Frage ohne gültige Antwort des Nutzers? Dann ist das Ticket gesperrt."""
    return any(_ist_klasse_2(f) and not _gilt(f, ticket=ticket, schluessel=schluessel) for f in fragen)


def _kaputte(datei: Path) -> list[Path]:
    """Beiseitegelegte unlesbare Fassungen (``.kaputt-*``) — jede sperrt das Ticket."""
    return sorted(datei.parent.glob(f"{datei.name}.kaputt-*"))


def _sperrgruende(datei: Path, inhalt: _Inhalt) -> list[str]:
    """Die eine Stelle für „Ticket durch kaputte Datei gesperrt?“ — Gründe, leer = nicht gesperrt.

    Jede ``.kaputt-*``-Datei und eine vorhandene, aber nicht nutzbare Hauptdatei (ungültig oder
    Lesefehler) sperren sofort, ohne dass erst ein Einwurf kommen muss: Klasse 2, wartet auf den
    Nutzer (sie könnte eine offene Klasse-2-Frage enthalten).
    """
    gruende = [f"Briefkasten-Datei kaputt: {k.name} — prüfen und löschen" for k in _kaputte(datei)]
    if inhalt.stoerung:
        gruende.append(inhalt.stoerung)
    return gruende


def einwerfen(
    n: str | int,
    frage: str,
    vorschlag: str,
    klasse: int | None,
    belege: Iterable[str],
    *,
    ziel: str = "",
    art: str = "",
    hauptbaum: Path | None = None,
) -> str:
    """Frage der Session einwerfen; liefert ihre ``id`` (für :func:`lesen`).

    ``klasse`` ``None``/3/2 macht nur strenger: Klasse 2, sobald sie genannt ist oder Frage,
    Vorschlag, ``ziel`` oder ``art`` an :func:`befugnis.ist_klasse_2` anschlagen. ``Verweigert``
    bei fremdem Ticket (≠ ``TO_SPAWN_TICKET``), ``ValueError`` bei Klasse außer 2/3.
    """
    if klasse is not None and klasse not in _KLASSEN:
        raise ValueError(f"Klasse {klasse!r}: in den Briefkasten gehören nur Klasse 2 und 3.")
    nummer = _nummer(n)
    eigen = _eigenes_ticket()
    if eigen is not None and eigen != nummer:
        raise Verweigert(f"Ticket {nummer}: diese Session gehört zu Ticket {eigen}.")
    streng = klasse == 2 or befugnis.ist_klasse_2(frage, vorschlag, ziel, art)
    frage_id = secrets.token_hex(4)
    datei = _datei(nummer, hauptbaum)
    with _gesperrt(datei):
        inhalt = _lies(datei)
        daten = inhalt.daten
        if inhalt.stoerung and not inhalt.beiseitelegen:
            # Lesefehler: die Datei kann gültig sein — nie verschieben, nie überschreiben.
            raise OSError(f"Ticket {nummer}: {inhalt.stoerung}")
        if inhalt.beiseitelegen:
            # Nie überschreiben, was nicht lesbar ist (Datenverlust): beiseitelegen.
            stempel = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            datei.replace(datei.with_name(f"{datei.name}.kaputt-{stempel}"))
            log.warning("Kaputter Briefkasten %s nach .kaputt-%s verschoben.", datei, stempel)
        daten = daten or {"ticket": str(nummer), "fragen": []}
        daten["fragen"].append(
            {
                "id": frage_id,
                "ts": _jetzt(),
                "challenge": secrets.token_hex(16),
                "frage": frage,
                "vorschlag": vorschlag,
                "ziel": ziel,
                "art": art,
                "klasse": 2 if streng else 3,
                "belege": list(belege),
                "antwort": None,
            }
        )
        manifest.schreibe_atomar(datei, daten)
    return frage_id


def lesen(n: str | int, frage_id: str | None = None, *, hauptbaum: Path | None = None) -> Antwort | str:
    """Stand der Frage ``frage_id``: :class:`Antwort`, :data:`WARTET`, :data:`NUTZER` oder :data:`UNBEKANNT`.

    :data:`NUTZER`, solange das Ticket durch eine kaputte Datei oder ungültige Schlüssel-Konfig gesperrt ist
    (``.kaputt-*``, unlesbare Datei, :func:`_sperrgruende`) oder irgendeine Klasse-2-Frage des
    Tickets ohne gültige Antwort des Nutzers ist (bei eingerichteten Handy-Schlüsseln inkl.
    Ed25519-Beweisprüfung, #312). Die Antwort des Nutzers kommt als :class:`Antwort` (``von``
    :data:`NUTZER_VON`). Ohne passende ``id`` nie eine :class:`Antwort`: :data:`UNBEKANNT`, wenn eine
    ``frage_id`` genannt ist, die nicht (mehr) im Briefkasten steht (sonst wartete die Session für
    immer); ohne ``frage_id`` :data:`WARTET`.
    """
    basis = _hauptbaum(hauptbaum)
    nummer = _nummer(n)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if konfig_fehler:
        return NUTZER
    datei = _datei(nummer, basis)
    inhalt = _lies(datei)
    if _sperrgruende(datei, inhalt):
        return NUTZER
    fragen = (inhalt.daten or {}).get("fragen") or []
    if fragen and _klasse_2_offen(fragen, ticket=str(nummer), schluessel=schluessel):
        return NUTZER
    treffer = [f for f in fragen if frage_id and f.get("id") == frage_id]
    if not treffer:
        return UNBEKANNT if frage_id else WARTET
    f = treffer[-1]
    if not _gilt(f, ticket=str(nummer), schluessel=schluessel):
        return WARTET
    antwort = f["antwort"]
    belege = antwort.get("belege")
    return Antwort(
        wahl=str(antwort["wahl"]),
        grund=str(antwort.get("grund", "")),
        belege=[str(b) for b in belege] if isinstance(belege, list) else [],
        von=str(antwort.get("von", "")),
        ts=str(antwort.get("ts", "")),
    )


def antworten(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str = AUFSEHER_VON[0],
    *,
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort des Aufsehers auf die offene Frage ``frage_id`` schreiben.

    ``von`` nur aus :data:`AUFSEHER_VON` (``ValueError`` sonst; der Nutzer antwortet über
    :func:`nutzer_antworten`). ``ValueError`` bei leerer ``wahl``, ohne nicht leeren Beleg
    (Fundstelle Pflicht, #365) oder ohne offene Frage mit dieser ``id``; ``Verweigert`` in einer
    Bau-Session und solange das Ticket auf den Nutzer wartet (offene Klasse-2-Frage,
    ``.kaputt-*``-Datei oder unlesbare/kaputte Hauptdatei).
    """
    if von not in AUFSEHER_VON:
        raise ValueError(f"von {von!r}: der Aufseher schreibt nur als {', '.join(AUFSEHER_VON)}.")
    return _schreibe_antwort(n, frage_id, wahl, belege, grund, von, hauptbaum)


def nutzer_antworten(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    *,
    beweis: str = "",
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort des Nutzers (Mensch) auf die offene Frage ``frage_id``, auch Klasse 2.

    Einziger Weg zu ``von`` :data:`NUTZER_VON`. ``Verweigert`` in einer Bau-Session sowie bei
    eingerichteten Handy-Schlüsseln ohne gültigen Ed25519-Beweis (#312); eine ``.kaputt-*``-Datei
    sperrt weiter, bis der Nutzer sie gelöscht hat, eine unlesbare/kaputte Hauptdatei, bis sie
    geklärt ist. Grenzen des Schutzes: Modul-Docstring.
    """
    return _schreibe_antwort(n, frage_id, wahl, belege, grund, NUTZER_VON, hauptbaum, beweis=beweis)


def beweis_anfrage(n: str | int, frage_id: str, *, hauptbaum: Path | None = None) -> dict[str, str]:
    """Prüf-Anforderung einer offenen Frage zum Signieren auf dem Handy (#312).

    Liefert ``ticket``, ``id``, ``klasse``, ``frage``, ``vorschlag``, ``challenge`` und
    ``frage_sha256``. ``Verweigert`` bei kaputter Datei oder fehlender Challenge; ``ValueError``
    wenn keine passende offene Frage vorliegt.
    """
    basis = _hauptbaum(hauptbaum)
    nummer = _nummer(n)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if konfig_fehler:
        raise Verweigert(f"Ticket {nummer}: {konfig_fehler}")
    datei = _datei(nummer, basis)
    inhalt = _lies(datei)
    gruende = _sperrgruende(datei, inhalt)
    if gruende:
        raise Verweigert(f"Ticket {nummer}: wartet auf den Nutzer ({gruende[0]}).")
    fragen = (inhalt.daten or {}).get("fragen") or []
    treffer = [f for f in fragen if f.get("id") == frage_id]
    if not treffer or _gilt(treffer[-1], ticket=str(nummer), schluessel=schluessel):
        raise ValueError(f"Ticket {nummer}: keine offene Frage {frage_id!r} im Briefkasten.")
    f = treffer[-1]
    challenge = str(f.get("challenge") or "").strip()
    if len(challenge) < klasse2_beweis.MIN_CHALLENGE_LAENGE:
        raise Verweigert(f"Ticket {nummer} Frage {frage_id}: keine gültige Challenge — bitte Frage neu stellen.")
    frage_text = str(f.get("frage") or "")
    return {
        "ticket": str(nummer),
        "id": str(f.get("id") or ""),
        "klasse": "2" if _ist_klasse_2(f) else "3",
        "frage": frage_text,
        "vorschlag": str(f.get("vorschlag") or ""),
        "challenge": challenge,
        "frage_sha256": klasse2_beweis.frage_digest(frage_text),
    }


def beweis_einreichen(
    n: str | int,
    token: str,
    *,
    belege: Iterable[str] = (),
    hauptbaum: Path | None = None,
) -> Path:
    """Reicht ein vom Handy signiertes ``TO-SPAWN-K2-V1``-Beweis-Token ein (#312).

    Fail-closed: ohne mindestens einen eingerichteten vertrauenswürdigen öffentlichen Schlüssel
    wird jede Einreichung verweigert. Die eigentliche Ablage läuft vollständig über
    :func:`nutzer_antworten` (erneute atomare Prüfung unter Dateisperre inkl. Challenge und
    Fragetext-Hash).
    """
    basis = _hauptbaum(hauptbaum)
    nummer = _nummer(n)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if konfig_fehler:
        raise Verweigert(f"Ticket {nummer}: {konfig_fehler}")
    if not schluessel:
        raise Verweigert(
            "Kein vertrauenswürdiger öffentlicher Handy-Schlüssel eingerichtet (klasse_2.oeffentliche_schluessel)."
        )
    try:
        geprueft = klasse2_beweis.beweis_pruefen(token, schluessel, ticket=str(nummer))
    except klasse2_beweis.BeweisFehler as fehler:
        raise Verweigert(f"Ticket {nummer}: Handy-Beweis abgelehnt ({fehler}).") from fehler
    belege_liste = [str(b).strip() for b in belege if str(b).strip()]
    if not belege_liste:
        belege_liste = [f"Handy-Beweis:{geprueft.schluessel_kennung}"]
    return nutzer_antworten(
        nummer,
        geprueft.frage_id,
        geprueft.wahl,
        belege_liste,
        geprueft.grund,
        beweis=geprueft.token,
        hauptbaum=basis,
    )


#: Name des PreToolUse-Hooks (``to_spawn.py hook-ja-sperre``), der Ja-Befehle aus Werkzeug-Aufrufen ablehnt (#531).
JA_SPERRE_HOOK = "hook-ja-sperre"
#: Der eine Befehl für das Live-Ja, auf jeder Maschine gleich (``install.sh`` legt ihn dorthin, #531).
JA_BEFEHL = "~/.claude/bin/ja"


def ja_aufforderung(n: str | int = "<N>") -> str:
    """Der eine Text, mit dem der Nutzer zum Live-Ja aufgefordert wird: ``! ~/.claude/bin/ja 5`` (#531)."""
    return f"`! {JA_BEFEHL} {n}`"


def sperre_eingetragen() -> bool:
    """Steht der Hook :data:`JA_SPERRE_HOOK` (PreToolUse, Matcher mit Bash) in ``~/.claude/settings.json``?"""
    try:
        einstellungen = json.loads((Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return sperre_eingetragen_in(einstellungen)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


_ENV_ZUWEISUNG = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^`$]*")
_PYTHON_NAME = re.compile(r"python(\d+(\.\d+)*)?(\.exe)?")
_PYTHON_FLAG_EINFACH = re.compile(r"-[uBEsIOqP]+")
_PYTHON_FLAG_WERT = re.compile(r"[A-Za-z0-9_.,:=-]+")


def _zerlege_sperre_befehl(befehl: str) -> list[str] | None:
    """Befehl wie die Shell in Wörter und Operatoren (``;``, ``|``, ``&``, ``<``, ``>``, ``()``) zerlegen."""
    try:
        lex = shlex.shlex(befehl, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None


def sperre_eingetragen_in(einstellungen: dict[str, Any]) -> bool:
    """Wie :func:`sperre_eingetragen`, für schon gelesene Einstellungen (falsche Form = nicht da)."""
    if not isinstance(einstellungen, dict):
        return False
    hooks = einstellungen.get("hooks")
    if not isinstance(hooks, dict):
        return False
    blocks = hooks.get("PreToolUse")
    if not isinstance(blocks, list):
        return False
    return any(
        isinstance(b, dict)
        and isinstance(b.get("matcher"), str)
        and "Bash" in b["matcher"].split("|")
        and isinstance(b.get("hooks"), list)
        and any(isinstance(h, dict) and ist_sperre_befehl(h.get("command"), nur_aktiv=True) for h in b["hooks"])
        for b in blocks
    )


def ist_sperre_befehl(befehl: object, *, nur_aktiv: bool = False) -> bool:
    """Die eine Erkennung der Ja-Sperre in einem Hook-Befehl (#599, #632).

    Standard (breit, für ``ja_einrichten``): jeder Eintrag ``<…>to_spawn.py … hook-ja-sperre`` —
    auch tote (Datei fehlt), alte oder entschärfte, damit Einrichten sie ersetzt statt verdoppelt.
    ``nur_aktiv=True`` (eng, für den Nachweis :func:`sperre_eingetragen_in`): ein einzelner Befehl
    ohne Shell-Anhängsel, Zeilenumbrüche oder Umleitungen, bei dem ``to_spawn.py`` (direkt oder über
    Python mit harmlosen Schaltern) das ausgeführte Programm ist, die Datei existiert und
    :data:`JA_SPERRE_HOOK` das einzige Argument dahinter ist.
    Kein Text (``None``, leer, kein ``str``) oder nicht zerlegbar → ``False``.
    """
    if not isinstance(befehl, str):
        return False
    woerter = _zerlege_sperre_befehl(befehl)
    if not woerter:
        return False
    if JA_SPERRE_HOOK not in woerter or not any(w.replace("\\", "/").endswith("to_spawn.py") for w in woerter):
        return False
    if not nur_aktiv:
        return True
    if "\n" in befehl.strip() or "\r" in befehl.strip():
        return False
    i = 0
    while i < len(woerter) and _ENV_ZUWEISUNG.fullmatch(woerter[i]):
        i += 1
    if i >= len(woerter):
        return False
    name = woerter[i].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name == "to_spawn.py":
        skript = woerter[i]
        i += 1
    elif _PYTHON_NAME.fullmatch(name):
        i += 1
        while i < len(woerter):
            wort = woerter[i]
            if _PYTHON_FLAG_EINFACH.fullmatch(wort):
                i += 1
            elif wort in ("-X", "-W") and i + 1 < len(woerter) and _PYTHON_FLAG_WERT.fullmatch(woerter[i + 1]):
                i += 2
            elif len(wort) > 2 and wort[:2] in ("-X", "-W") and _PYTHON_FLAG_WERT.fullmatch(wort[2:]):
                i += 1
            else:
                break
        if i >= len(woerter):
            return False
        skript = woerter[i]
        if skript.replace("\\", "/").rsplit("/", 1)[-1].lower() != "to_spawn.py":
            return False
        i += 1
    else:
        return False
    return Path(skript).is_file() and woerter[i:] == [JA_SPERRE_HOOK]


def menschen_nachweis(ist_tty: bool) -> None:
    """Wer darf das Ja schreiben? Mit Terminal: ja. Ohne (``!``-Eingabefeld): nur wenn die Sperre eingetragen ist.

    Fail-closed (#531): ohne eingetragene Sperre wäre ein Bash-Werkzeug-Aufruf einer Session
    nicht vom Eingabefeld zu unterscheiden — also ``Verweigert``. Die Regel steht nur hier.
    """
    if not ist_tty and not sperre_eingetragen():
        raise Verweigert(
            "Ja ohne Terminal nur mit eingetragener Sperre: Skill installieren (install.sh) "
            "oder in einem echten Terminal ausführen."
        )


def ja(n: str | int, *, hauptbaum: Path | None = None) -> Path:
    """Kurzform des Nutzer-Ja: die einzige offene Frage des Tickets mit Wahl „ja“ beantworten (#531).

    ``ValueError`` bei 0 oder mehreren offenen Fragen (Text sagt, wie viele); sonst wie
    :func:`nutzer_antworten`. Wer das darf, entscheidet der Aufrufer über :func:`menschen_nachweis`.
    """
    offen = offene([n], hauptbaum=hauptbaum)
    if len(offen) > 1:
        raise ValueError(_mehrere_fragen_text(n, offen))
    if len(offen) != 1:
        raise ValueError(f"Ticket {n}: {len(offen)} offene Fragen (nötig: genau 1) — `offene {n}` zeigt sie.")
    if not offen[0]["id"]:
        raise Verweigert(f"Ticket {n}: {offen[0]['frage']} — nur der Nutzer löst die Sperre.")
    return nutzer_antworten(n, offen[0]["id"], "ja", [], "Live-Ja des Nutzers (ja)", hauptbaum=hauptbaum)


def _mehrere_fragen_text(n: str | int, offen: list[dict[str, str]]) -> str:
    """Meldung, wenn ``ja`` nicht weiß, welche Frage gemeint ist (#539): je Frage Kennung, erste Zeile, Befehl.

    Eine Zeile (die CLI meldet Fehler einzeilig); der Befehl ist fertig zum Kopieren ins Eingabefeld.
    Fragen ohne Kennung (kaputte Datei) bekommen keinen Befehl — die löst nur der Nutzer an der Datei.
    """
    skript = (Path(__file__).resolve().parent.parent / "to_spawn.py").as_posix()
    teile = []
    for o in offen:
        zeile = (o["frage"].strip().splitlines() or [""])[0]
        if not o["id"]:
            teile.append(f"gesperrt: {zeile}")
            continue
        # ponytail: POSIX-Quoting (shlex), unter Windows-cmd ggf. anders zu quoten
        befehl = shlex.join(
            [Path(sys.executable).as_posix(), skript, "nutzer-antwort", str(n), "--id", o["id"]]
            + ["--wahl", "ja", "--grund", "Live-Ja des Nutzers"]
        )
        teile.append(f"Frage {o['id']} „{zeile}“: `! {befehl}`")
    return (
        f"Ticket {n}: {len(offen)} offene Fragen, `ja` weiß nicht, welche gemeint ist. "
        f"Für die gemeinte Frage diesen Befehl kopieren und ins Eingabefeld setzen — " + " · ".join(teile)
    )


def _schreibe_antwort(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str,
    hauptbaum: Path | None,
    *,
    beweis: str = "",
) -> Path:
    """Antwort schreiben (gemeinsamer Weg von :func:`antworten` und :func:`nutzer_antworten`).

    Offen ist eine Frage, solange ihre Antwort nicht gilt (:func:`_gilt`). Steht dort eine
    ungültige Antwort (z. B. von Hand eingetragene Aufseher-Antwort an einer Klasse-2-Frage), blockt
    sie die gültige nicht: die neue Antwort ersetzt sie, die alte bleibt in der Liste
    ``verworfen`` der Frage nachvollziehbar erhalten. ``Verweigert`` bei unlesbarer Hauptdatei
    (auch für den Nutzer: erst die Datei klären), bei ungültiger Schlüssel-Konfig, bei fehlendem
    oder ungültigem Handy-Beweis (sobald Schlüssel eingerichtet sind, #312) und für den Aufseher
    bei jeder Sperre.
    """
    if _eigenes_ticket() is not None:
        raise Verweigert("Eine Bau-Session beantwortet keine Briefkasten-Fragen (TO_SPAWN_TICKET gesetzt).")
    if not wahl.strip():
        raise ValueError("Leere Wahl: die Antwort braucht eine Entscheidung.")
    basis = _hauptbaum(hauptbaum)
    nummer = _nummer(n)
    datei = _datei(nummer, basis)
    with _gesperrt(datei):
        schluessel, konfig_fehler = _schluessel_stand(basis)
        if konfig_fehler:
            raise Verweigert(f"Ticket {nummer}: {konfig_fehler}")
        inhalt = _lies(datei)
        gruende = _sperrgruende(datei, inhalt)
        if inhalt.stoerung:
            raise Verweigert(f"Ticket {nummer}: wartet auf den Nutzer ({inhalt.stoerung}).")
        daten = inhalt.daten
        treffer = [f for f in (daten or {}).get("fragen", []) if f.get("id") == frage_id]
        if not daten or not treffer or _gilt(treffer[-1], ticket=str(nummer), schluessel=schluessel):
            raise ValueError(f"Ticket {nummer}: keine offene Frage {frage_id!r} im Briefkasten.")
        if von != NUTZER_VON and (
            gruende or _klasse_2_offen(daten["fragen"], ticket=str(nummer), schluessel=schluessel)
        ):
            raise Verweigert(f"Ticket {nummer}: wartet auf den Nutzer (Klasse 2 oder kaputte Datei).")
        belege_liste = [str(b).strip() for b in belege if str(b).strip()]
        if von != NUTZER_VON and not belege_liste:
            raise ValueError(
                "Beleg fehlt (--beleg): eine Antwort des Aufsehers braucht mindestens eine nicht leere Fundstelle."
            )
        frage = treffer[-1]
        beweis_sauber = str(beweis or "").strip()
        gepruefter_beweis: klasse2_beweis.GepruefterBeweis | None = None
        if von == NUTZER_VON and (schluessel or beweis_sauber):
            try:
                gepruefter_beweis = klasse2_beweis.beweis_pruefen(
                    beweis_sauber,
                    schluessel,
                    ticket=str(nummer),
                    frage_id=str(frage.get("id") or ""),
                    frage=str(frage.get("frage") or ""),
                    challenge=str(frage.get("challenge") or ""),
                    wahl=wahl,
                    grund=grund,
                )
            except klasse2_beweis.BeweisFehler as fehler:
                raise Verweigert(f"Ticket {nummer}: gültiger Handy-Beweis erforderlich ({fehler}).") from fehler
        if frage.get("antwort") is not None:
            alt = frage.get("verworfen")
            frage["verworfen"] = [*(alt if isinstance(alt, list) else []), frage["antwort"]]
        neue_antwort: dict[str, Any] = {
            "ts": _jetzt(),
            "wahl": wahl,
            "grund": grund,
            "belege": belege_liste,
            "von": von,
        }
        if gepruefter_beweis is not None:
            neue_antwort["beweis"] = gepruefter_beweis.token
            neue_antwort["beweis_schluessel"] = gepruefter_beweis.schluessel_kennung
        frage["antwort"] = neue_antwort
        manifest.schreibe_atomar(datei, daten)
    return datei


def _sortier_nummer(t: str) -> int:
    try:
        return _nummer(t)
    except ValueError:
        return -1


def offene(tickets: Iterable[str | int] | None = None, *, hauptbaum: Path | None = None) -> list[dict[str, str]]:
    """Alle offenen Fragen (``tickets`` None = alle), älteste zuerst je Ticket.

    Je Frage ``ticket``, ``id``, ``klasse`` (``"2"`` = nur der Nutzer), ``frage``, ``vorschlag``,
    ``ts``. Jeder Sperrgrund durch kaputte Datei (``.kaputt-*`` oder vorhandene, aber unlesbare
    Datei, :func:`_sperrgruende`) erscheint als Klasse-2-Eintrag mit leerer ``id``. Offen ist eine
    Frage, solange ihre Antwort nicht gilt (:func:`_gilt`).
    """
    basis = _hauptbaum(hauptbaum)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    if tickets is None:
        tickets = alle_tickets(hauptbaum=basis)
    ergebnis: list[dict[str, str]] = []
    for t in tickets:
        try:
            datei = _datei(t, basis)
        except ValueError:
            continue
        nummer = str(_nummer(t))
        inhalt = _lies(datei)
        if konfig_fehler:
            ergebnis.append(
                {"ticket": nummer, "id": "", "klasse": "2", "frage": konfig_fehler, "vorschlag": "", "ts": ""}
            )
        for grund in _sperrgruende(datei, inhalt):
            ergebnis.append({"ticket": nummer, "id": "", "klasse": "2", "frage": grund, "vorschlag": "", "ts": ""})
        for f in (inhalt.daten or {}).get("fragen", []):
            if not konfig_fehler and _gilt(f, ticket=nummer, schluessel=schluessel):
                continue
            ergebnis.append(
                {
                    "ticket": nummer,
                    "id": str(f.get("id", "")),
                    "klasse": "2" if _ist_klasse_2(f) else "3",
                    "frage": str(f.get("frage", "")),
                    "vorschlag": str(f.get("vorschlag", "")),
                    "ts": str(f.get("ts", "")),
                }
            )
    return ergebnis


def alle_tickets(*, hauptbaum: Path | None = None) -> list[str]:
    """Alle Tickets mit Briefkasten-Datei (auch nur ``.kaputt-*``), aufsteigend."""
    ordner = _hauptbaum(hauptbaum) / _ORDNER
    namen = {d.name.split(".json", 1)[0].removeprefix(_PRAEFIX) for d in ordner.glob(f"{_PRAEFIX}*.json*")}
    return sorted(namen, key=_sortier_nummer)


#: Sichtbarer Platzhalter, wenn eine Entscheidung keine Fundstelle hat (#364): nie still leer.
KEINE_QUELLE = "keine Quelle"


#: Höchstlänge der Frage in ``was``; die Wahl wird nie gekürzt (sie ist die Entscheidung).
_FRAGE_MAX = 120


def _was(frage: Any, wahl: Any) -> str:
    """``Frage → Wahl`` in einer Zeile; nur die Frage wird gekürzt."""
    kurz = " ".join(str(frage or "").split())
    if len(kurz) > _FRAGE_MAX:
        kurz = kurz[: _FRAGE_MAX - 1] + "…"
    return " → ".join(t for t in (kurz, " ".join(str(wahl or "").split())) if t)


def eintrag_aus_bau_log(ticket: str | int, z: dict[str, Any]) -> dict[str, str]:
    """Ein Listen-Eintrag (Schlüssel wie :func:`liste`) aus einer Bau-Log-Zeile ``entscheidung``.

    Rückfall auf alte Felder (``text``/``entscheidungen``) liegt in :func:`bau_log.entscheidung_felder`.
    Auch der Zwischenstand des Aufsehers (``capo.entscheidung_anhaengen``) baut seine Zeile hierüber,
    damit Zwischen- und Abschluss-Datei dieselben Spalten zeigen.
    """
    nummer = str(ticket).strip()
    frage, wahl, grund = bau_log.entscheidung_felder(z)
    return {
        "ticket": nummer,
        "ts": str(z.get("zeit") or z.get("ts") or ""),
        "wer": "Bau-Session",
        "was": _was(frage, wahl),
        "warum": grund,
        "beleg": f"Bau-Log #{nummer} {z.get('zeit') or z.get('ts') or ''}".rstrip(),
        "ueberholt_durch": str(z.get("ueberholt_durch") or ""),  # #529: Rundschau-Grafik
    }


def _log_entscheidungen(repo: Path, nummer: str, basis: Path, ref: str | None) -> list[dict[str, Any]]:
    """``entscheidung``-Zeilen: Arbeitsbaum + Laufdateien, mit ``ref`` dazu der Stand dort (ohne Doppel)."""
    zeilen = bau_log.lese(repo, nummer, hauptbaum=basis)
    if ref and nummer.isdigit():
        gesehen = {json.dumps(z, sort_keys=True) for z in zeilen}
        zeilen += [z for z in bau_log.log_vom_ref(repo, ref, nummer) if json.dumps(z, sort_keys=True) not in gesehen]
    return sorted(
        (z for z in zeilen if z.get("typ") == "entscheidung"),
        key=lambda z: str(z.get("zeit") or z.get("ts") or ""),
    )


def liste(
    tickets: Iterable[str | int],
    *,
    log_repo: Path | None = None,
    hauptbaum: Path | None = None,
) -> list[dict[str, str]]:
    """Alle Entscheidungen je Ticket — der eine Sammler (#278, #364).

    Quellen: Bau-Log-Zeilen ``entscheidung`` (Klasse 1, auch Annahmen des Aufsehers) und die
    geltenden Briefkasten-Antworten (Aufseher oder Nutzer). Wer die Entscheidungen zeigt
    (``capo.uebersicht``, CLI ``entscheidungen``), fragt nur hier und kennt die Quellen nicht.
    Das versionierte Bau-Log zählt zusätzlich so, wie es auf ``origin/<Hauptzweig>`` liegt
    (``bau_log.haupt_ref``) — der Hauptbaum kann dahinter stehen. Nur hier wird der Stand gewählt.

    Je Eintrag feste Schlüssel ``ticket``, ``ts``, ``wer``, ``was``, ``warum``, ``beleg``;
    ``beleg`` ist nie leer (sonst :data:`KEINE_QUELLE`).
    """
    basis = _hauptbaum(hauptbaum)
    repo = log_repo or basis
    ref = bau_log.haupt_ref(repo)
    schluessel, konfig_fehler = _schluessel_stand(basis)
    zeilen: list[dict[str, str]] = []
    for t in tickets:
        nummer = str(t).strip()
        zeilen += [eintrag_aus_bau_log(nummer, z) for z in _log_entscheidungen(repo, nummer, basis, ref)]
        try:
            daten = _lade(_datei(nummer, basis))
        except ValueError:
            continue
        for f in (daten or {}).get("fragen", []):
            antwort_roh = f.get("antwort")
            if konfig_fehler and (
                _ist_klasse_2(f) or (isinstance(antwort_roh, dict) and antwort_roh.get("von") == NUTZER_VON)
            ):
                continue  # Schlüssel-Konfig kaputt: Klasse-2- und Nutzer-Antworten nie still durchlassen
            if not _gilt(f, ticket=nummer, schluessel=schluessel):
                continue  # Klasse 2 gilt nur mit gültiger Antwort des Nutzers
            antwort = f["antwort"]
            zeilen.append(
                {
                    "ticket": nummer,
                    "ts": str(antwort.get("ts", "")),
                    "wer": str(antwort.get("von", "")),
                    "was": _was(f.get("frage"), antwort.get("wahl")),
                    "warum": str(antwort.get("grund", "")),
                    "beleg": ", ".join(str(b) for b in antwort.get("belege") or []) or KEINE_QUELLE,
                }
            )
    return zeilen
