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
- :func:`nutzer_antworten` — Antwort des Nutzers (auch Klasse 2); verweigert in einer Bau-Session.
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

Schutz des Nutzer-Wegs, ehrlich: Bau-Session (``TO_SPAWN_TICKET`` gesetzt, auch leer) und
Aufseher (``--von`` kennt nur :data:`AUFSEHER_VON`) haben über diese Schnittstelle keinen Weg,
``von`` :data:`NUTZER_VON` zu schreiben; die CLI ``nutzer-antwort`` verlangt zusätzlich ein
echtes Terminal. Das verhindert versehentliche und über die angebotene Schnittstelle mögliche
Selbst-Freigabe — nicht eine Session, die mit Absicht die Datei selbst schreibt oder die
Umgebung umbaut (``env -u``); auf derselben Maschine kann sie das technisch immer. Einen Weg
über Ticket-Kommentare gibt es bewusst nicht: Sessions kommentieren mit dem Konto des Besitzers,
ein Kommentar ist also nie ein Beleg für die Antwort des Nutzers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shlex
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from to_spawn import bau_log, befugnis, config, manifest, sperre

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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ist_klasse_2(f: dict[str, Any]) -> bool:
    """Klasse 2, wenn gespeichert ODER der Text anschlägt — eine von Hand gesenkte Klasse zählt nicht."""
    return f.get("klasse") != 3 or befugnis.ist_klasse_2(
        f.get("frage"), f.get("vorschlag"), f.get("ziel"), f.get("art")
    )


def _hat_antwort(f: dict[str, Any]) -> bool:
    antwort = f.get("antwort")
    return isinstance(antwort, dict) and bool(str(antwort.get("wahl") or "").strip())


def _gilt(f: dict[str, Any]) -> bool:
    """Hat ``f`` eine gültige Antwort? Klasse 2 nur ``von`` :data:`NUTZER_VON`."""
    return _hat_antwort(f) and (f["antwort"].get("von") == NUTZER_VON or not _ist_klasse_2(f))


def _klasse_2_offen(fragen: list[dict[str, Any]]) -> bool:
    """Irgendeine Klasse-2-Frage ohne Antwort des Nutzers? Dann ist das Ticket gesperrt."""
    return any(_ist_klasse_2(f) and not _gilt(f) for f in fragen)


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
            stempel = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            datei.replace(datei.with_name(f"{datei.name}.kaputt-{stempel}"))
            log.warning("Kaputter Briefkasten %s nach .kaputt-%s verschoben.", datei, stempel)
        daten = daten or {"ticket": str(nummer), "fragen": []}
        daten["fragen"].append(
            {
                "id": frage_id,
                "ts": _jetzt(),
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

    :data:`NUTZER`, solange das Ticket durch eine kaputte Datei gesperrt ist (``.kaputt-*`` oder
    vorhandene, aber unlesbare Datei, :func:`_sperrgruende`) oder irgendeine Klasse-2-Frage des
    Tickets ohne Antwort des Nutzers ist (eine Antwort anderer gibt Klasse 2 nie frei). Die
    Antwort des Nutzers kommt als :class:`Antwort` (``von`` :data:`NUTZER_VON`). Ohne passende
    ``id`` nie eine :class:`Antwort`: :data:`UNBEKANNT`, wenn eine ``frage_id`` genannt ist, die nicht
    (mehr) im Briefkasten steht (sonst wartete die Session für immer); ohne ``frage_id`` :data:`WARTET`.
    """
    datei = _datei(n, hauptbaum)
    inhalt = _lies(datei)
    if _sperrgruende(datei, inhalt):
        return NUTZER
    fragen = (inhalt.daten or {}).get("fragen") or []
    if fragen and _klasse_2_offen(fragen):
        return NUTZER
    treffer = [f for f in fragen if frage_id and f.get("id") == frage_id]
    if not treffer:
        return UNBEKANNT if frage_id else WARTET
    f = treffer[-1]
    if not _gilt(f):
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
    :func:`nutzer_antworten`). ``ValueError`` bei leerer ``wahl`` oder ohne offene Frage mit dieser
    ``id``; ``Verweigert`` in einer Bau-Session und solange das Ticket auf den Nutzer wartet
    (offene Klasse-2-Frage, ``.kaputt-*``-Datei oder unlesbare/kaputte Hauptdatei).
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
    hauptbaum: Path | None = None,
) -> Path:
    """Antwort des Nutzers (Mensch) auf die offene Frage ``frage_id``, auch Klasse 2.

    Einziger Weg zu ``von`` :data:`NUTZER_VON`. ``Verweigert`` in einer Bau-Session; eine
    ``.kaputt-*``-Datei sperrt weiter, bis der Nutzer sie gelöscht hat, eine unlesbare/kaputte
    Hauptdatei, bis sie geklärt ist. Grenzen des Schutzes:
    Modul-Docstring.
    """
    return _schreibe_antwort(n, frage_id, wahl, belege, grund, NUTZER_VON, hauptbaum)


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


def sperre_eingetragen_in(einstellungen: dict[str, Any]) -> bool:
    """Wie :func:`sperre_eingetragen`, für schon gelesene Einstellungen (``KeyError`` o. ä. = nicht da)."""
    try:
        blocks = einstellungen["hooks"]["PreToolUse"]
        return any(
            "Bash" in b.get("matcher", "").split("|") and _ruft_sperre_auf(h.get("command", ""))
            for b in blocks
            for h in b.get("hooks", [])
        )
    except (KeyError, TypeError, AttributeError):
        return False


def _ruft_sperre_auf(befehl: str) -> bool:
    """Ruft ``befehl`` eine vorhandene ``to_spawn.py`` mit dem Unterbefehl :data:`JA_SPERRE_HOOK` auf?

    ``echo hook-ja-sperre`` o. ä. zählt nicht: nur ``<…>/to_spawn.py hook-ja-sperre`` mit existierender Datei.
    """
    try:
        woerter = shlex.split(befehl)
    except ValueError:
        return False
    erstes = next((w for w in woerter if "=" not in w), "").replace("\\", "/").rsplit("/", 1)[-1]
    if erstes != "to_spawn.py" and not re.fullmatch(r"python[\d.]*(\.exe)?", erstes):
        return False  # Befehlswort muss Python oder die to_spawn.py selbst sein (nicht ``echo …``)
    return any(
        w.replace("\\", "/").rsplit("/", 1)[-1] == "to_spawn.py"
        and Path(w).is_file()
        and woerter[i + 1 : i + 2] == [JA_SPERRE_HOOK]
        for i, w in enumerate(woerter)
    )


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
    if len(offen) != 1:
        raise ValueError(f"Ticket {n}: {len(offen)} offene Fragen (nötig: genau 1) — `offene {n}` zeigt sie.")
    if not offen[0]["id"]:
        raise Verweigert(f"Ticket {n}: {offen[0]['frage']} — nur der Nutzer löst die Sperre.")
    return nutzer_antworten(n, offen[0]["id"], "ja", [], "Live-Ja des Nutzers (ja)", hauptbaum=hauptbaum)


def _schreibe_antwort(
    n: str | int,
    frage_id: str,
    wahl: str,
    belege: Iterable[str],
    grund: str,
    von: str,
    hauptbaum: Path | None,
) -> Path:
    """Antwort schreiben (gemeinsamer Weg von :func:`antworten` und :func:`nutzer_antworten`).

    Offen ist eine Frage, solange ihre Antwort nicht gilt (:func:`_gilt`). Steht dort eine
    ungültige Antwort (z. B. von Hand eingetragene Aufseher-Antwort an einer Klasse-2-Frage), blockt
    sie die gültige nicht: die neue Antwort ersetzt sie, die alte bleibt in der Liste
    ``verworfen`` der Frage nachvollziehbar erhalten. ``Verweigert`` bei unlesbarer Hauptdatei
    (auch für den Nutzer: erst die Datei klären) und für den Aufseher bei jeder Sperre.
    """
    if _eigenes_ticket() is not None:
        raise Verweigert("Eine Bau-Session beantwortet keine Briefkasten-Fragen (TO_SPAWN_TICKET gesetzt).")
    if not wahl.strip():
        raise ValueError("Leere Wahl: die Antwort braucht eine Entscheidung.")
    datei = _datei(n, hauptbaum)
    with _gesperrt(datei):
        inhalt = _lies(datei)
        gruende = _sperrgruende(datei, inhalt)
        if inhalt.stoerung:
            raise Verweigert(f"Ticket {n}: wartet auf den Nutzer ({inhalt.stoerung}).")
        daten = inhalt.daten
        treffer = [f for f in (daten or {}).get("fragen", []) if f.get("id") == frage_id]
        if not daten or not treffer or _gilt(treffer[-1]):
            raise ValueError(f"Ticket {n}: keine offene Frage {frage_id!r} im Briefkasten.")
        if von != NUTZER_VON and (gruende or _klasse_2_offen(daten["fragen"])):
            raise Verweigert(f"Ticket {n}: wartet auf den Nutzer (Klasse 2 oder kaputte Datei).")
        frage = treffer[-1]
        if frage.get("antwort") is not None:
            alt = frage.get("verworfen")
            frage["verworfen"] = [*(alt if isinstance(alt, list) else []), frage["antwort"]]
        frage["antwort"] = {
            "ts": _jetzt(),
            "wahl": wahl,
            "grund": grund,
            "belege": list(belege),
            "von": von,
        }
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
    if tickets is None:
        tickets = alle_tickets(hauptbaum=hauptbaum)
    ergebnis: list[dict[str, str]] = []
    for t in tickets:
        try:
            datei = _datei(t, hauptbaum)
        except ValueError:
            continue
        nummer = str(_nummer(t))
        inhalt = _lies(datei)
        for grund in _sperrgruende(datei, inhalt):
            ergebnis.append({"ticket": nummer, "id": "", "klasse": "2", "frage": grund, "vorschlag": "", "ts": ""})
        for f in (inhalt.daten or {}).get("fragen", []):
            if _gilt(f):
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

    Alte Felder (``text``/``entscheidungen``) gelten als Rückfall für Frage/Wahl. Auch der
    Zwischenstand des Aufsehers (``capo.entscheidung_anhaengen``) baut seine Zeile hierüber,
    damit Zwischen- und Abschluss-Datei dieselben Spalten zeigen.
    """
    nummer = str(ticket).strip()
    return {
        "ticket": nummer,
        "ts": str(z.get("ts", "")),
        "wer": "Bau-Session",
        "was": _was(z.get("frage") or z.get("text"), z.get("wahl") or z.get("entscheidungen")),
        "warum": str(z.get("grund") or ""),
        "beleg": f"Bau-Log #{nummer} {z.get('ts', '')}".rstrip(),
        "ueberholt_durch": str(z.get("ueberholt_durch") or ""),  # #529: Rundschau-Grafik
    }


def _log_entscheidungen(repo: Path, nummer: str, basis: Path, ref: str | None) -> list[dict[str, Any]]:
    """``entscheidung``-Zeilen: Arbeitsbaum + Laufdateien, mit ``ref`` dazu der Stand dort (ohne Doppel)."""
    zeilen = bau_log.lese(repo, nummer, hauptbaum=basis)
    if ref and nummer.isdigit():
        from to_spawn import (
            capo,  # spät: capo importiert briefkasten (Kreis-Import sonst)
        )

        gesehen = {json.dumps(z, sort_keys=True) for z in zeilen}
        zeilen += [z for z in capo.log_vom_ref(repo, ref, int(nummer)) if json.dumps(z, sort_keys=True) not in gesehen]
    return sorted((z for z in zeilen if z.get("typ") == "entscheidung"), key=lambda z: str(z.get("ts", "")))


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
    (``capo.haupt_ref``) — der Hauptbaum kann dahinter stehen. Nur hier wird der Stand gewählt.

    Je Eintrag feste Schlüssel ``ticket``, ``ts``, ``wer``, ``was``, ``warum``, ``beleg``;
    ``beleg`` ist nie leer (sonst :data:`KEINE_QUELLE`).
    """
    from to_spawn import capo  # spät: capo importiert briefkasten (Kreis-Import sonst)

    basis = _hauptbaum(hauptbaum)
    repo = log_repo or basis
    ref = capo.haupt_ref(repo)
    zeilen: list[dict[str, str]] = []
    for t in tickets:
        nummer = str(t).strip()
        zeilen += [eintrag_aus_bau_log(nummer, z) for z in _log_entscheidungen(repo, nummer, basis, ref)]
        try:
            daten = _lade(_datei(nummer, basis))
        except ValueError:
            continue
        for f in (daten or {}).get("fragen", []):
            if not _gilt(f):
                continue  # Klasse 2 gilt nur mit Antwort des Nutzers
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
