"""Reproduzierbare 14-Tage-Server-Messung und Entscheidungsvorlage (#905 / App #1401).

Liest ausschließlich lokale Protokolldateien (nur Standardbibliothek, Linux und Windows):
- Fahrtenbuch (``fahrtenbuch.jsonl``): Gate-Dauer je Ergebnis-Art und Rot-Quote je Rot-Art (#1382).
- Nachtlauf-Laufzeiten (``laufzeit.jsonl``): langsamste Testdateien im Messfenster.
- Ereignis-Protokoll (``ereignis_protokoll.jsonl``): 15-Minuten-Auslastungsverlauf (#1399)
  mit freier Platte, freiem Speicher und Sessions (inkl. Sessions ohne Ticket).

Verdichtet alle Kennzahlen deterministisch (gleiche Eingabe → gleiche Ausgabe) zu einer
Entscheidungsvorlage für den Nutzer mit drei Optionen:
``groesserer_server``, ``eigene_staging_maschine`` oder ``nichts_kaufen``.
Es wird nichts gekauft und Staging nicht umgezogen — das Skript rechnet und berichtet nur.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("server_messung_1401")

#: Ergebnisse im Fahrtenbuch, die als Gate-Urteil zählen.
URTEIL_ERGEBNISSE: frozenset[str] = frozenset(
    {
        "ticket-gruen",
        "ticket-rot",
        "ticket-gate-fehler",
        "ticket-konflikt",
        "buendel-gruen",
        "buendel-rot",
        "buendel-gate-fehler",
        "gruen",
        "rot",
    }
)

#: Ergebnisse im Fahrtenbuch, die als rotes Gate zählen.
ROTE_ERGEBNISSE: frozenset[str] = frozenset(
    {
        "ticket-rot",
        "ticket-gate-fehler",
        "ticket-konflikt",
        "buendel-rot",
        "buendel-gate-fehler",
        "rot",
    }
)

#: Feste Reihenfolge der Rot-Arten aus #1382 plus Fallback für fehlendes Feld.
ROT_ARTEN: tuple[str, ...] = (
    "eigener_fehler",
    "master_geerbt",
    "wackler",
    "technik",
    "unbekannt",
)

ROT_ART_TITEL: dict[str, str] = {
    "eigener_fehler": "Eigener Fehler des Tickets",
    "master_geerbt": "Von master geerbt",
    "wackler": "Wackler (Quarantäne)",
    "technik": "Technik (Zeitlimit, Abbruch, kein Urteil)",
    "unbekannt": "Ohne Rot-Art (Altformat / offen)",
}

#: Ereignis-Typen der 15-Minuten-Auslastungszeile (#1399).
AUSLASTUNG_TYPEN: frozenset[str] = frozenset({"auslastung", "server_auslastung", "auslastung_15m"})

#: Erwartete 15-Minuten-Takte je Tag.
TAKTE_JE_TAG: int = 96


@dataclass(frozen=True)
class Schwellen:
    """Einzige Quelle für die Entscheidungsschwellen der Hardware-Empfehlung."""

    ram_engpass_mib: float = 2048.0
    ram_kritisch_mib: float = 1024.0
    ram_engpass_anteil_pct: float = 15.0
    platte_engpass_gib: float = 10.0
    platte_kritisch_gib: float = 5.0
    platte_engpass_anteil_pct: float = 15.0
    sessions_hoch: float = 6.0
    sessions_hoch_anteil_pct: float = 20.0
    gate_lang_p90_s: float = 600.0
    technik_rot_anteil_pct: float = 20.0


STANDARD_SCHWELLEN = Schwellen()


@dataclass(frozen=True)
class Messfenster:
    """Start- und Endzeitpunkt des ausgewerteten Zeitraums."""

    start: datetime
    ende: datetime
    tage: int


@dataclass(frozen=True)
class GateStatistik:
    """Zusammenfassung von Gate-Laufzeiten in Sekunden."""

    anzahl: int
    median_s: float | None
    p90_s: float | None
    max_s: float | None


@dataclass(frozen=True)
class FahrtenbuchAuswertung:
    """Verdichtete Kennzahlen aus dem Fahrtenbuch im Messfenster."""

    zeilen_gesamt: int
    urteile_gesamt: int
    rote_gesamt: int
    rot_quote_pct: float | None
    rot_nach_art: dict[str, int]
    gate_gesamt: GateStatistik
    gate_je_ergebnis: dict[str, GateStatistik]
    urteile_je_tag: dict[str, int]
    staging_laeufe: int


@dataclass(frozen=True)
class TestDateiStatistik:
    """Laufzeit-Statistik einer einzelnen Testdatei aus dem Nachtlauf."""

    datei: str
    anzahl: int
    median_s: float
    p90_s: float
    max_s: float


@dataclass(frozen=True)
class NachtlaufAuswertung:
    """Verdichtete Nachtlauf-Langsamläufer im Messfenster."""

    laeufe_gesamt: int
    dateien_gesamt: int
    langsamste: tuple[TestDateiStatistik, ...]


@dataclass(frozen=True)
class WertVerlauf:
    """Verteilungskennzahlen einer Messreihe (z. B. freier Speicher oder Platte)."""

    anzahl: int
    min_wert: float | None
    p10_wert: float | None
    median_wert: float | None
    p90_wert: float | None
    max_wert: float | None


@dataclass(frozen=True)
class TagesAuslastung:
    """Tagesweise Spitzenwerte aus dem 15-Minuten-Verlauf."""

    tag: str
    messpunkte: int
    platte_min_gib: float | None
    ram_min_mib: float | None
    sessions_max: float | None


@dataclass(frozen=True)
class AuslastungsAuswertung:
    """Verdichteter 15-Minuten-Auslastungsverlauf aus dem Ereignis-Protokoll."""

    messpunkte: int
    erwartete_messpunkte: int
    abdeckung_pct: float | None
    platte_frei_gib: WertVerlauf
    ram_frei_mib: WertVerlauf
    sessions: WertVerlauf
    sessions_ohne_ticket: WertVerlauf
    ram_engpass_punkte: int
    ram_engpass_anteil_pct: float | None
    platte_engpass_punkte: int
    platte_engpass_anteil_pct: float | None
    sessions_hoch_punkte: int
    sessions_hoch_anteil_pct: float | None
    tagesverlauf: tuple[TagesAuslastung, ...]


@dataclass(frozen=True)
class EntscheidungsOption:
    """Beschreibung einer Hardware-Option mit Trade-off in Alltagsworten."""

    schluessel: str
    titel: str
    wann_sinnvoll: str
    trade_off: str


@dataclass(frozen=True)
class Empfehlung:
    """Ergebnis der Entscheidungslogik für den Nutzer."""

    option: str
    titel: str
    begruendung: tuple[str, ...]
    optionen: tuple[EntscheidungsOption, ...]


@dataclass(frozen=True)
class GesamtBefund:
    """Vollständiges, deterministisches Auswerte-Ergebnis."""

    fenster: Messfenster
    fahrtenbuch: FahrtenbuchAuswertung
    nachtlauf: NachtlaufAuswertung
    auslastung: AuslastungsAuswertung
    empfehlung: Empfehlung
    schwellen: Schwellen


# ---------------------------------------------------------------- Hilfsfunktionen


def _perzentil(werte: list[float], p: float) -> float | None:
    """Berechnet das p-Perzentil (0..100) identisch zu ``messung/vorher_zeiten.py``."""
    if not werte:
        return None
    sortiert = sorted(werte)
    idx = min(len(sortiert) - 1, max(0, round(p / 100.0 * (len(sortiert) - 1) + 0.4999)))
    return float(sortiert[idx])


def _gate_statistik(werte: list[float]) -> GateStatistik:
    if not werte:
        return GateStatistik(anzahl=0, median_s=None, p90_s=None, max_s=None)
    return GateStatistik(
        anzahl=len(werte),
        median_s=float(statistics.median(werte)),
        p90_s=_perzentil(werte, 90.0),
        max_s=float(max(werte)),
    )


def _wert_verlauf(werte: list[float]) -> WertVerlauf:
    if not werte:
        return WertVerlauf(anzahl=0, min_wert=None, p10_wert=None, median_wert=None, p90_wert=None, max_wert=None)
    return WertVerlauf(
        anzahl=len(werte),
        min_wert=float(min(werte)),
        p10_wert=_perzentil(werte, 10.0),
        median_wert=float(statistics.median(werte)),
        p90_wert=_perzentil(werte, 90.0),
        max_wert=float(max(werte)),
    )


def _parse_zeit(wert: Any) -> datetime | None:
    """Parst einen ISO-Zeitstempel; naive Zeiten werden als UTC behandelt."""
    if not isinstance(wert, str) or not wert.strip():
        return None
    text = wert.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _zeit_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _stichtag_ende(stichtag: str) -> datetime:
    text = stichtag.strip()
    if "T" not in text and " " not in text:
        return datetime.fromisoformat(f"{text}T23:59:59+01:00")
    dt = _parse_zeit(text)
    if dt is None:
        raise ValueError(f"Ungültiger Stichtag: {stichtag!r}")
    return dt


def _lese_jsonl(pfad: Path | None) -> list[dict[str, Any]]:
    """Liest JSONL robust ein; fehlende Dateien oder defekte Zeilen werden protokolliert."""
    if pfad is None:
        return []
    if not pfad.is_file():
        log.info("Eingabedatei nicht vorhanden: %s", pfad)
        return []
    zeilen: list[dict[str, Any]] = []
    for nr, roh in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        text = roh.strip()
        if not text:
            continue
        try:
            eintrag = json.loads(text)
        except ValueError:
            log.debug("Ungültige JSON-Zeile übersprungen (%s:%d)", pfad.name, nr)
            continue
        if isinstance(eintrag, dict):
            zeilen.append(eintrag)
    return zeilen


def _ermittle_zeit(eintrag: dict[str, Any]) -> datetime | None:
    for feld in ("zeit", "ts", "timestamp", "erstellt"):
        dt = _parse_zeit(eintrag.get(feld))
        if dt is not None:
            return dt
    return None


def _im_fenster(dt: datetime | None, fenster: Messfenster) -> bool:
    if dt is None:
        return False
    t = _zeit_utc(dt)
    return _zeit_utc(fenster.start) <= t <= _zeit_utc(fenster.ende)


def _zahl(eintrag: dict[str, Any], *felder: str) -> float | None:
    for feld in felder:
        wert = eintrag.get(feld)
        if isinstance(wert, (int, float)) and not isinstance(wert, bool):
            return float(wert)
    return None


def _normalisiere_rot_art(wert: Any) -> str:
    if not isinstance(wert, str) or not wert.strip():
        return "unbekannt"
    roh = wert.strip().lower().replace("-", "_").replace(" ", "_")
    alias = {
        "eigener": "eigener_fehler",
        "eigener_fehler": "eigener_fehler",
        "ticket": "eigener_fehler",
        "master": "master_geerbt",
        "master_geerbt": "master_geerbt",
        "von_master_geerbt": "master_geerbt",
        "geerbt": "master_geerbt",
        "wackler": "wackler",
        "flaky": "wackler",
        "technik": "technik",
        "gate_fehler": "technik",
        "timeout": "technik",
        "zeitlimit": "technik",
    }
    return alias.get(roh, roh)


# ---------------------------------------------------------------- Einzel-Auswertungen


def _werte_fahrtenbuch_aus(zeilen: list[dict[str, Any]], fenster: Messfenster) -> FahrtenbuchAuswertung:
    im_zeitraum: list[tuple[datetime, dict[str, Any]]] = []
    for z in zeilen:
        dt = _ermittle_zeit(z)
        if _im_fenster(dt, fenster):
            assert dt is not None
            im_zeitraum.append((dt, z))

    gate_alle: list[float] = []
    gate_je_art: dict[str, list[float]] = collections.defaultdict(list)
    rot_zaehler: collections.Counter[str] = collections.Counter()
    urteile_tag: collections.Counter[str] = collections.Counter()
    urteile_gesamt = 0
    rote_gesamt = 0
    staging_laeufe = 0

    for dt, z in im_zeitraum:
        ergebnis = str(z.get("ergebnis", "")).strip()
        if ergebnis == "staging":
            staging_laeufe += 1

        gate_s = _zahl(z, "gate_s", "dauer_s")
        if gate_s is not None and gate_s >= 0.0 and ergebnis:
            gate_alle.append(gate_s)
            gate_je_art[ergebnis].append(gate_s)

        if ergebnis in URTEIL_ERGEBNISSE:
            urteile_gesamt += 1
            urteile_tag[dt.date().isoformat()] += 1
            if ergebnis in ROTE_ERGEBNISSE:
                rote_gesamt += 1
                rot_art = _normalisiere_rot_art(z.get("rot_art") or z.get("art_des_rots"))
                rot_zaehler[rot_art] += 1

    rot_nach_art: dict[str, int] = {art: rot_zaehler.get(art, 0) for art in ROT_ARTEN}
    for extra in sorted(set(rot_zaehler) - set(ROT_ARTEN)):
        rot_nach_art[extra] = rot_zaehler[extra]

    rot_quote_pct = (100.0 * rote_gesamt / urteile_gesamt) if urteile_gesamt > 0 else None
    return FahrtenbuchAuswertung(
        zeilen_gesamt=len(im_zeitraum),
        urteile_gesamt=urteile_gesamt,
        rote_gesamt=rote_gesamt,
        rot_quote_pct=rot_quote_pct,
        rot_nach_art=rot_nach_art,
        gate_gesamt=_gate_statistik(gate_alle),
        gate_je_ergebnis={k: _gate_statistik(gate_je_art[k]) for k in sorted(gate_je_art)},
        urteile_je_tag={k: urteile_tag[k] for k in sorted(urteile_tag)},
        staging_laeufe=staging_laeufe,
    )


def _werte_nachtlauf_aus(zeilen: list[dict[str, Any]], fenster: Messfenster, top_n: int = 20) -> NachtlaufAuswertung:
    zeiten_je_datei: dict[str, list[float]] = collections.defaultdict(list)
    laeufe = 0

    for z in zeilen:
        dt = _ermittle_zeit(z)
        if dt is not None and not _im_fenster(dt, fenster):
            continue

        gefunden = False
        dateien_map = z.get("dateien")
        if isinstance(dateien_map, dict):
            for pfad_str, dauer in dateien_map.items():
                if isinstance(pfad_str, str) and isinstance(dauer, (int, float)) and not isinstance(dauer, bool):
                    zeiten_je_datei[pfad_str].append(float(dauer))
                    gefunden = True

        langsam_liste = z.get("langsam")
        if isinstance(langsam_liste, list):
            for eintrag in langsam_liste:
                if not isinstance(eintrag, dict):
                    continue
                datei = eintrag.get("datei")
                dauer = _zahl(eintrag, "dauer_s", "sekunden", "laufzeit_s")
                if isinstance(datei, str) and dauer is not None:
                    zeiten_je_datei[datei].append(dauer)
                    gefunden = True

        einzel_datei = z.get("datei")
        einzel_dauer = _zahl(z, "dauer_s", "sekunden", "laufzeit_s")
        if isinstance(einzel_datei, str) and einzel_dauer is not None:
            zeiten_je_datei[einzel_datei].append(einzel_dauer)
            gefunden = True

        if gefunden:
            laeufe += 1

    statistiken: list[TestDateiStatistik] = []
    for datei, werte in zeiten_je_datei.items():
        p90 = _perzentil(werte, 90.0)
        statistiken.append(
            TestDateiStatistik(
                datei=datei,
                anzahl=len(werte),
                median_s=float(statistics.median(werte)),
                p90_s=float(p90 if p90 is not None else max(werte)),
                max_s=float(max(werte)),
            )
        )

    statistiken.sort(key=lambda s: (-s.max_s, -s.median_s, s.datei))
    return NachtlaufAuswertung(
        laeufe_gesamt=laeufe,
        dateien_gesamt=len(statistiken),
        langsamste=tuple(statistiken[: max(0, top_n)]),
    )


def _ist_auslastungszeile(eintrag: dict[str, Any]) -> bool:
    typ = eintrag.get("typ")
    if isinstance(typ, str) and typ.strip():
        return typ.strip().lower() in AUSLASTUNG_TYPEN
    return any(
        feld in eintrag
        for feld in (
            "platte_frei_gib",
            "platte_frei_mib",
            "frei_platte_mib",
            "ram_frei_mib",
            "frei_mib",
            "speicher_frei_mib",
            "sessions",
            "claude_sessions",
        )
    )


def _platte_frei_gib(eintrag: dict[str, Any]) -> float | None:
    gib = _zahl(eintrag, "platte_frei_gib", "frei_platte_gib", "disk_frei_gib")
    if gib is not None:
        return gib
    mib = _zahl(eintrag, "platte_frei_mib", "frei_platte_mib", "disk_frei_mib")
    if mib is not None:
        return mib / 1024.0
    return None


def _werte_auslastung_aus(
    zeilen: list[dict[str, Any]], fenster: Messfenster, schwellen: Schwellen
) -> AuslastungsAuswertung:
    platte_werte: list[float] = []
    ram_werte: list[float] = []
    session_werte: list[float] = []
    ohne_ticket_werte: list[float] = []

    ram_engpass = 0
    platte_engpass = 0
    sessions_hoch = 0
    messpunkte = 0

    je_tag: dict[str, dict[str, list[float]]] = collections.defaultdict(
        lambda: {"platte": [], "ram": [], "sessions": []}
    )
    tag_anzahl: collections.Counter[str] = collections.Counter()

    for z in zeilen:
        if not _ist_auslastungszeile(z):
            continue
        dt = _ermittle_zeit(z)
        if not _im_fenster(dt, fenster):
            continue
        assert dt is not None
        messpunkte += 1
        tag = dt.date().isoformat()
        tag_anzahl[tag] += 1

        platte = _platte_frei_gib(z)
        if platte is not None:
            platte_werte.append(platte)
            je_tag[tag]["platte"].append(platte)
            if platte < schwellen.platte_engpass_gib:
                platte_engpass += 1

        ram = _zahl(z, "ram_frei_mib", "frei_mib", "speicher_frei_mib")
        if ram is not None:
            ram_werte.append(ram)
            je_tag[tag]["ram"].append(ram)
            if ram < schwellen.ram_engpass_mib:
                ram_engpass += 1

        sess = _zahl(z, "sessions", "claude_sessions", "sessions_gesamt")
        if sess is not None:
            session_werte.append(sess)
            je_tag[tag]["sessions"].append(sess)
            if sess >= schwellen.sessions_hoch:
                sessions_hoch += 1

        ohne_t = _zahl(z, "sessions_ohne_ticket", "ohne_ticket")
        if ohne_t is not None:
            ohne_ticket_werte.append(ohne_t)

    erwartet = max(1, fenster.tage) * TAKTE_JE_TAG
    abdeckung = (100.0 * messpunkte / erwartet) if messpunkte > 0 else None
    tagesverlauf = tuple(
        TagesAuslastung(
            tag=tag,
            messpunkte=tag_anzahl[tag],
            platte_min_gib=min(je_tag[tag]["platte"]) if je_tag[tag]["platte"] else None,
            ram_min_mib=min(je_tag[tag]["ram"]) if je_tag[tag]["ram"] else None,
            sessions_max=max(je_tag[tag]["sessions"]) if je_tag[tag]["sessions"] else None,
        )
        for tag in sorted(tag_anzahl)
    )

    return AuslastungsAuswertung(
        messpunkte=messpunkte,
        erwartete_messpunkte=erwartet,
        abdeckung_pct=abdeckung,
        platte_frei_gib=_wert_verlauf(platte_werte),
        ram_frei_mib=_wert_verlauf(ram_werte),
        sessions=_wert_verlauf(session_werte),
        sessions_ohne_ticket=_wert_verlauf(ohne_ticket_werte),
        ram_engpass_punkte=ram_engpass,
        ram_engpass_anteil_pct=(100.0 * ram_engpass / len(ram_werte)) if ram_werte else None,
        platte_engpass_punkte=platte_engpass,
        platte_engpass_anteil_pct=(100.0 * platte_engpass / len(platte_werte)) if platte_werte else None,
        sessions_hoch_punkte=sessions_hoch,
        sessions_hoch_anteil_pct=(100.0 * sessions_hoch / len(session_werte)) if session_werte else None,
        tagesverlauf=tagesverlauf,
    )


# ---------------------------------------------------------------- Entscheidungslogik


def _standard_optionen() -> tuple[EntscheidungsOption, ...]:
    return (
        EntscheidungsOption(
            schluessel="groesserer_server",
            titel="Größerer Server",
            wann_sinnvoll=(
                "Wenn Arbeitsspeicher (RAM) regelmäßig knapp wird, viele Sessions parallel laufen "
                "und lange Gate-Laufzeiten oder Technik-Rots den Bau bremsen."
            ),
            trade_off=(
                "Höhere laufende Monatskosten, aber nur ein System zu pflegen und sofort mehr Luft "
                "für RAM, CPU und Platte gleichzeitig."
            ),
        ),
        EntscheidungsOption(
            schluessel="eigene_staging_maschine",
            titel="Eigene Maschine für Staging",
            wann_sinnvoll=(
                "Wenn vor allem die Platte knapp wird oder Staging-Betrieb und Bau-Läufe sich auf "
                "derselben Maschine gegenseitig stören, während RAM für Bau-Sessions noch reicht."
            ),
            trade_off=(
                "Günstiger als ein großer Einzelserver und saubere Trennung von Bau und Staging, "
                "dafür eine zweite Maschine im Betrieb und einmaliger Umzugsaufwand."
            ),
        ),
        EntscheidungsOption(
            schluessel="nichts_kaufen",
            titel="Nichts kaufen",
            wann_sinnvoll=(
                "Wenn Speicher, Platte und Gate-Zeiten im 14-Tage-Verlauf stabil im grünen Bereich "
                "bleiben und Engpässe eher durch einzelne langsame Tests oder Code-Fehler entstehen."
            ),
            trade_off=(
                "Keine Zusatzkosten und kein Umzug; Verbesserungen kommen gezielt über schnellere "
                "Testdateien und weniger Wackler statt über neue Hardware."
            ),
        ),
    )


def _entscheide(
    fahrtenbuch: FahrtenbuchAuswertung,
    auslastung: AuslastungsAuswertung,
    schwellen: Schwellen,
) -> Empfehlung:
    optionen = _standard_optionen()
    titel_map = {opt.schluessel: opt.titel for opt in optionen}

    if fahrtenbuch.urteile_gesamt == 0 and auslastung.messpunkte == 0:
        return Empfehlung(
            option="nichts_kaufen",
            titel=titel_map["nichts_kaufen"],
            begruendung=(
                "Im gewählten Messfenster liegen weder Fahrtenbuch-Urteile noch 15-Minuten-Auslastungsdaten vor.",
                "Ohne belastbare 14-Tage-Messdaten ist kein Hardware-Kauf begründbar.",
            ),
            optionen=optionen,
        )

    ram_eng = (
        auslastung.ram_engpass_anteil_pct is not None
        and auslastung.ram_engpass_anteil_pct >= schwellen.ram_engpass_anteil_pct
    ) or (
        auslastung.ram_frei_mib.min_wert is not None and auslastung.ram_frei_mib.min_wert < schwellen.ram_kritisch_mib
    )

    platte_eng = (
        auslastung.platte_engpass_anteil_pct is not None
        and auslastung.platte_engpass_anteil_pct >= schwellen.platte_engpass_anteil_pct
    ) or (
        auslastung.platte_frei_gib.min_wert is not None
        and auslastung.platte_frei_gib.min_wert < schwellen.platte_kritisch_gib
    )

    sessions_eng = (
        auslastung.sessions_hoch_anteil_pct is not None
        and auslastung.sessions_hoch_anteil_pct >= schwellen.sessions_hoch_anteil_pct
    )

    gate_lang = fahrtenbuch.gate_gesamt.p90_s is not None and fahrtenbuch.gate_gesamt.p90_s >= schwellen.gate_lang_p90_s

    technik_rot_pct = (
        (100.0 * fahrtenbuch.rot_nach_art.get("technik", 0) / fahrtenbuch.rote_gesamt)
        if fahrtenbuch.rote_gesamt > 0
        else 0.0
    )
    technik_hoch = technik_rot_pct >= schwellen.technik_rot_anteil_pct

    gruende: list[str] = []
    if ram_eng or (sessions_eng and (gate_lang or technik_hoch)):
        wahl = "groesserer_server"
        if ram_eng:
            gruende.append(
                f"Freier Arbeitsspeicher unterschritt {schwellen.ram_engpass_mib:.0f} MiB in "
                f"{_fmt_pct(auslastung.ram_engpass_anteil_pct)} der Messpunkte "
                f"(Minimum: {_fmt_zahl(auslastung.ram_frei_mib.min_wert, 'MiB')})."
            )
        if sessions_eng:
            gruende.append(
                f"Hohe Session-Last (≥ {schwellen.sessions_hoch:.0f} Sessions) in "
                f"{_fmt_pct(auslastung.sessions_hoch_anteil_pct)} der Messpunkte."
            )
        if gate_lang:
            gruende.append(
                f"Gate-Dauer P90 liegt bei {_fmt_zahl(fahrtenbuch.gate_gesamt.p90_s, 's')} "
                f"(über Schwelle {schwellen.gate_lang_p90_s:.0f} s)."
            )
        if technik_hoch:
            gruende.append(f"Technik-Rots machen {technik_rot_pct:.0f} % aller roten Gates aus.")
    elif platte_eng:
        wahl = "eigene_staging_maschine"
        gruende.append(
            f"Freie Platte unterschritt {schwellen.platte_engpass_gib:.0f} GiB in "
            f"{_fmt_pct(auslastung.platte_engpass_anteil_pct)} der Messpunkte "
            f"(Minimum: {_fmt_zahl(auslastung.platte_frei_gib.min_wert, 'GiB', nachkomma=1)})."
        )
        gruende.append(
            "Der Arbeitsspeicher bleibt dabei im unkritischen Bereich — eine Trennung von Staging und "
            "Bau-Server entlastet gezielt die Platte ohne großen Einzelserver."
        )
    else:
        wahl = "nichts_kaufen"
        gruende.append("Weder Arbeitsspeicher noch Platte zeigen anhaltende Engpässe im 14-Tage-Verlauf.")
        if gate_lang:
            gruende.append(
                f"Die Gate-Dauer P90 liegt bei {_fmt_zahl(fahrtenbuch.gate_gesamt.p90_s, 's')}; "
                "ohne RAM-/Platten-Engpass lohnt sich zuerst das Beschleunigen der langsamsten Testdateien."
            )
        else:
            gruende.append(
                f"Gate-Dauer (Median {_fmt_zahl(fahrtenbuch.gate_gesamt.median_s, 's')}, "
                f"P90 {_fmt_zahl(fahrtenbuch.gate_gesamt.p90_s, 's')}) liegt im Zielbereich."
            )

    return Empfehlung(
        option=wahl,
        titel=titel_map[wahl],
        begruendung=tuple(gruende),
        optionen=optionen,
    )


# ---------------------------------------------------------------- Haupt-Auswertung


def _bestimme_fenster(
    alle_zeilen: list[dict[str, Any]],
    stichtag: str | None,
    tage: int,
) -> Messfenster:
    if tage <= 0:
        raise ValueError(f"tage muss positiv sein, erhalten: {tage}")
    if stichtag:
        ende = _stichtag_ende(stichtag)
    else:
        zeiten = [dt for z in alle_zeilen if (dt := _ermittle_zeit(z)) is not None]
        ende = max(zeiten, key=_zeit_utc) if zeiten else datetime.now(UTC)
    start = ende - timedelta(days=tage)
    return Messfenster(start=start, ende=ende, tage=tage)


def auswerten(
    *,
    fahrtenbuch_pfad: Path | None,
    nachtlauf_pfad: Path | None,
    ereignis_pfad: Path | None,
    stichtag: str | None = None,
    tage: int = 14,
    top_n: int = 20,
    schwellen: Schwellen = STANDARD_SCHWELLEN,
) -> GesamtBefund:
    """Führt die vollständige 14-Tage-Auswertung rein lesend und deterministisch aus."""
    fb_zeilen = _lese_jsonl(fahrtenbuch_pfad)
    nl_zeilen = _lese_jsonl(nachtlauf_pfad)
    ep_zeilen = _lese_jsonl(ereignis_pfad)

    fenster = _bestimme_fenster(fb_zeilen + nl_zeilen + ep_zeilen, stichtag=stichtag, tage=tage)
    fb = _werte_fahrtenbuch_aus(fb_zeilen, fenster)
    nl = _werte_nachtlauf_aus(nl_zeilen, fenster, top_n=top_n)
    aus = _werte_auslastung_aus(ep_zeilen, fenster, schwellen)
    empf = _entscheide(fb, aus, schwellen)

    return GesamtBefund(
        fenster=fenster,
        fahrtenbuch=fb,
        nachtlauf=nl,
        auslastung=aus,
        empfehlung=empf,
        schwellen=schwellen,
    )


# ---------------------------------------------------------------- Formatierung


def _fmt_zahl(wert: float | None, einheit: str = "", nachkomma: int = 0) -> str:
    if wert is None:
        return "–"
    text = f"{wert:.{nachkomma}f}"
    return f"{text} {einheit}".strip()


def _fmt_pct(wert: float | None) -> str:
    if wert is None:
        return "–"
    return f"{wert:.1f} %"


def formatiere_markdown(befund: GesamtBefund) -> str:
    """Erzeugt eine reproduzierbare Markdown-Entscheidungsvorlage für den Nutzer."""
    f = befund.fenster
    fb = befund.fahrtenbuch
    nl = befund.nachtlauf
    aus = befund.auslastung
    emp = befund.empfehlung
    s = befund.schwellen

    zeilen: list[str] = [
        "# Server-Messung (14 Tage) und Entscheidungsvorlage",
        "",
        f"- **Messfenster ({f.tage} Tage):** {f.start:%Y-%m-%d %H:%M} bis {f.ende:%Y-%m-%d %H:%M}",
        f"- **Fahrtenbuch:** {fb.zeilen_gesamt} Zeilen, {fb.urteile_gesamt} Urteile ({fb.staging_laeufe} Staging-Läufe)",
        f"- **Nachtlauf:** {nl.laeufe_gesamt} Läufe, {nl.dateien_gesamt} gemessene Testdateien",
        (
            f"- **Auslastungsverlauf (15-Min-Takt):** {aus.messpunkte} Messpunkte "
            f"(Abdeckung: {_fmt_pct(aus.abdeckung_pct)})"
        ),
        "",
        "## 1. Gate-Dauer im Messfenster",
        "",
    ]

    if fb.gate_gesamt.anzahl == 0:
        zeilen.append("Keine Gate-Laufzeiten im Messfenster vorhanden.")
    else:
        zeilen.extend(
            [
                "| Ergebnis | Anzahl | Median (s) | P90 (s) | Max (s) |",
                "|---|---:|---:|---:|---:|",
                (
                    f"| **Gesamt** | {fb.gate_gesamt.anzahl} | {_fmt_zahl(fb.gate_gesamt.median_s)} | "
                    f"{_fmt_zahl(fb.gate_gesamt.p90_s)} | {_fmt_zahl(fb.gate_gesamt.max_s)} |"
                ),
            ]
        )
        for art, stat in fb.gate_je_ergebnis.items():
            zeilen.append(
                f"| {art} | {stat.anzahl} | {_fmt_zahl(stat.median_s)} | "
                f"{_fmt_zahl(stat.p90_s)} | {_fmt_zahl(stat.max_s)} |"
            )

    zeilen.extend(["", "## 2. Rot-Quote je Art", ""])
    if fb.urteile_gesamt == 0:
        zeilen.append("Keine Gate-Urteile im Messfenster vorhanden.")
    else:
        zeilen.extend(
            [
                f"Gesamt-Rot-Quote: **{fb.rote_gesamt}/{fb.urteile_gesamt} ({_fmt_pct(fb.rot_quote_pct)})**",
                "",
                "| Rot-Art | Beschreibung | Anzahl | Anteil an Urteilen | Anteil an Rot |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for art, anzahl in fb.rot_nach_art.items():
            anteil_urteile = (100.0 * anzahl / fb.urteile_gesamt) if fb.urteile_gesamt else None
            anteil_rot = (100.0 * anzahl / fb.rote_gesamt) if fb.rote_gesamt else None
            titel = ROT_ART_TITEL.get(art, art)
            zeilen.append(f"| `{art}` | {titel} | {anzahl} | {_fmt_pct(anteil_urteile)} | {_fmt_pct(anteil_rot)} |")

    zeilen.extend(["", "## 3. Nachtlauf-Langsamläufer", ""])
    if not nl.langsamste:
        zeilen.append("Keine Nachtlauf-Laufzeiten im Messfenster vorhanden.")
    else:
        zeilen.extend(
            [
                "| Rang | Datei | Messungen | Median (s) | P90 (s) | Max (s) |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for rang, eintrag in enumerate(nl.langsamste, 1):
            zeilen.append(
                f"| {rang} | `{eintrag.datei}` | {eintrag.anzahl} | "
                f"{_fmt_zahl(eintrag.median_s, nachkomma=1)} | "
                f"{_fmt_zahl(eintrag.p90_s, nachkomma=1)} | "
                f"{_fmt_zahl(eintrag.max_s, nachkomma=1)} |"
            )

    zeilen.extend(["", "## 4. 15-Minuten-Auslastungsverlauf", ""])
    if aus.messpunkte == 0:
        zeilen.append("Keine 15-Minuten-Auslastungszeilen im Messfenster vorhanden.")
    else:
        zeilen.extend(
            [
                "| Kennzahl | Min | P10 | Median | P90 | Max | Engpass-Anteil |",
                "|---|---:|---:|---:|---:|---:|---:|",
                (
                    f"| Freie Platte (GiB) | {_fmt_zahl(aus.platte_frei_gib.min_wert, nachkomma=1)} | "
                    f"{_fmt_zahl(aus.platte_frei_gib.p10_wert, nachkomma=1)} | "
                    f"{_fmt_zahl(aus.platte_frei_gib.median_wert, nachkomma=1)} | "
                    f"{_fmt_zahl(aus.platte_frei_gib.p90_wert, nachkomma=1)} | "
                    f"{_fmt_zahl(aus.platte_frei_gib.max_wert, nachkomma=1)} | "
                    f"{_fmt_pct(aus.platte_engpass_anteil_pct)} (< {s.platte_engpass_gib:.0f} GiB) |"
                ),
                (
                    f"| Freier Speicher (MiB) | {_fmt_zahl(aus.ram_frei_mib.min_wert)} | "
                    f"{_fmt_zahl(aus.ram_frei_mib.p10_wert)} | "
                    f"{_fmt_zahl(aus.ram_frei_mib.median_wert)} | "
                    f"{_fmt_zahl(aus.ram_frei_mib.p90_wert)} | "
                    f"{_fmt_zahl(aus.ram_frei_mib.max_wert)} | "
                    f"{_fmt_pct(aus.ram_engpass_anteil_pct)} (< {s.ram_engpass_mib:.0f} MiB) |"
                ),
                (
                    f"| Sessions | {_fmt_zahl(aus.sessions.min_wert)} | "
                    f"{_fmt_zahl(aus.sessions.p10_wert)} | "
                    f"{_fmt_zahl(aus.sessions.median_wert)} | "
                    f"{_fmt_zahl(aus.sessions.p90_wert)} | "
                    f"{_fmt_zahl(aus.sessions.max_wert)} | "
                    f"{_fmt_pct(aus.sessions_hoch_anteil_pct)} (≥ {s.sessions_hoch:.0f}) |"
                ),
                (
                    f"| Sessions ohne Ticket | {_fmt_zahl(aus.sessions_ohne_ticket.min_wert)} | "
                    f"{_fmt_zahl(aus.sessions_ohne_ticket.p10_wert)} | "
                    f"{_fmt_zahl(aus.sessions_ohne_ticket.median_wert)} | "
                    f"{_fmt_zahl(aus.sessions_ohne_ticket.p90_wert)} | "
                    f"{_fmt_zahl(aus.sessions_ohne_ticket.max_wert)} | – |"
                ),
            ]
        )
        if aus.tagesverlauf:
            zeilen.extend(
                [
                    "",
                    "### Tagesübersicht (Engpass-Spitzen)",
                    "",
                    "| Tag | Messpunkte | Min. Platte (GiB) | Min. Speicher (MiB) | Max. Sessions |",
                    "|---|---:|---:|---:|---:|",
                ]
            )
            for tag in aus.tagesverlauf:
                zeilen.append(
                    f"| {tag.tag} | {tag.messpunkte} | "
                    f"{_fmt_zahl(tag.platte_min_gib, nachkomma=1)} | "
                    f"{_fmt_zahl(tag.ram_min_mib)} | "
                    f"{_fmt_zahl(tag.sessions_max)} |"
                )

    zeilen.extend(
        [
            "",
            "## 5. Entscheidungsvorlage für den Nutzer",
            "",
            f"**Empfehlung:** {emp.titel} (`{emp.option}`)",
            "",
            "### Begründung aus den Messdaten",
            "",
        ]
    )
    for grund in emp.begruendung:
        zeilen.append(f"- {grund}")

    zeilen.extend(
        [
            "",
            "### Optionen und Trade-off im Vergleich",
            "",
            "| Option | Wann sinnvoll | Trade-off in Alltagsworten |",
            "|---|---|---|",
        ]
    )
    for opt in emp.optionen:
        marke = " *(empfohlen)*" if opt.schluessel == emp.option else ""
        zeilen.append(f"| **{opt.titel}**{marke} | {opt.wann_sinnvoll} | {opt.trade_off} |")

    zeilen.extend(
        [
            "",
            (
                "> Hinweis: Dieses Skript wertet ausschließlich bestehende Protokolle aus. "
                "Es wird keine Hardware gekauft und Staging wird nicht umgezogen; der Nutzer entscheidet."
            ),
            "",
        ]
    )
    return "\n".join(zeilen)


def befund_als_dict(befund: GesamtBefund) -> dict[str, Any]:
    """Serialisiert den Befund deterministisch in ein JSON-fähiges Dictionary."""
    return {
        "fenster": {
            "start": befund.fenster.start.isoformat(),
            "ende": befund.fenster.ende.isoformat(),
            "tage": befund.fenster.tage,
        },
        "fahrtenbuch": {
            "zeilen_gesamt": befund.fahrtenbuch.zeilen_gesamt,
            "urteile_gesamt": befund.fahrtenbuch.urteile_gesamt,
            "rote_gesamt": befund.fahrtenbuch.rote_gesamt,
            "rot_quote_pct": befund.fahrtenbuch.rot_quote_pct,
            "rot_nach_art": befund.fahrtenbuch.rot_nach_art,
            "gate_gesamt": {
                "anzahl": befund.fahrtenbuch.gate_gesamt.anzahl,
                "median_s": befund.fahrtenbuch.gate_gesamt.median_s,
                "p90_s": befund.fahrtenbuch.gate_gesamt.p90_s,
                "max_s": befund.fahrtenbuch.gate_gesamt.max_s,
            },
            "gate_je_ergebnis": {
                art: {
                    "anzahl": st.anzahl,
                    "median_s": st.median_s,
                    "p90_s": st.p90_s,
                    "max_s": st.max_s,
                }
                for art, st in befund.fahrtenbuch.gate_je_ergebnis.items()
            },
            "urteile_je_tag": befund.fahrtenbuch.urteile_je_tag,
            "staging_laeufe": befund.fahrtenbuch.staging_laeufe,
        },
        "nachtlauf": {
            "laeufe_gesamt": befund.nachtlauf.laeufe_gesamt,
            "dateien_gesamt": befund.nachtlauf.dateien_gesamt,
            "langsamste": [
                {
                    "datei": e.datei,
                    "anzahl": e.anzahl,
                    "median_s": e.median_s,
                    "p90_s": e.p90_s,
                    "max_s": e.max_s,
                }
                for e in befund.nachtlauf.langsamste
            ],
        },
        "auslastung": {
            "messpunkte": befund.auslastung.messpunkte,
            "erwartete_messpunkte": befund.auslastung.erwartete_messpunkte,
            "abdeckung_pct": befund.auslastung.abdeckung_pct,
            "platte_frei_gib": {
                "anzahl": befund.auslastung.platte_frei_gib.anzahl,
                "min": befund.auslastung.platte_frei_gib.min_wert,
                "p10": befund.auslastung.platte_frei_gib.p10_wert,
                "median": befund.auslastung.platte_frei_gib.median_wert,
                "p90": befund.auslastung.platte_frei_gib.p90_wert,
                "max": befund.auslastung.platte_frei_gib.max_wert,
            },
            "ram_frei_mib": {
                "anzahl": befund.auslastung.ram_frei_mib.anzahl,
                "min": befund.auslastung.ram_frei_mib.min_wert,
                "p10": befund.auslastung.ram_frei_mib.p10_wert,
                "median": befund.auslastung.ram_frei_mib.median_wert,
                "p90": befund.auslastung.ram_frei_mib.p90_wert,
                "max": befund.auslastung.ram_frei_mib.max_wert,
            },
            "sessions": {
                "anzahl": befund.auslastung.sessions.anzahl,
                "min": befund.auslastung.sessions.min_wert,
                "p10": befund.auslastung.sessions.p10_wert,
                "median": befund.auslastung.sessions.median_wert,
                "p90": befund.auslastung.sessions.p90_wert,
                "max": befund.auslastung.sessions.max_wert,
            },
            "sessions_ohne_ticket": {
                "anzahl": befund.auslastung.sessions_ohne_ticket.anzahl,
                "min": befund.auslastung.sessions_ohne_ticket.min_wert,
                "p10": befund.auslastung.sessions_ohne_ticket.p10_wert,
                "median": befund.auslastung.sessions_ohne_ticket.median_wert,
                "p90": befund.auslastung.sessions_ohne_ticket.p90_wert,
                "max": befund.auslastung.sessions_ohne_ticket.max_wert,
            },
            "ram_engpass_punkte": befund.auslastung.ram_engpass_punkte,
            "ram_engpass_anteil_pct": befund.auslastung.ram_engpass_anteil_pct,
            "platte_engpass_punkte": befund.auslastung.platte_engpass_punkte,
            "platte_engpass_anteil_pct": befund.auslastung.platte_engpass_anteil_pct,
            "sessions_hoch_punkte": befund.auslastung.sessions_hoch_punkte,
            "sessions_hoch_anteil_pct": befund.auslastung.sessions_hoch_anteil_pct,
            "tagesverlauf": [
                {
                    "tag": t.tag,
                    "messpunkte": t.messpunkte,
                    "platte_min_gib": t.platte_min_gib,
                    "ram_min_mib": t.ram_min_mib,
                    "sessions_max": t.sessions_max,
                }
                for t in befund.auslastung.tagesverlauf
            ],
        },
        "empfehlung": {
            "option": befund.empfehlung.option,
            "titel": befund.empfehlung.titel,
            "begruendung": list(befund.empfehlung.begruendung),
        },
    }


# ---------------------------------------------------------------- CLI


def _baue_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="14-Tage-Server-Messung und Hardware-Entscheidungsvorlage (#905 / App #1401)."
    )
    parser.add_argument("--schlange", type=Path, help="Optionaler Schlangen-Ordner (fahrtenbuch.jsonl, laeufe/)")
    parser.add_argument("--fahrtenbuch", type=Path, help="Pfad zu fahrtenbuch.jsonl")
    parser.add_argument("--nachtlauf", type=Path, help="Pfad zu laufzeit.jsonl oder Nachtlauf-JSONL")
    parser.add_argument("--ereignis-protokoll", type=Path, help="Pfad zur Ereignis-Protokoll-JSONL (#1399)")
    parser.add_argument("--stichtag", help="Enddatum JJJJ-MM-TT oder ISO-Zeitstempel (Standard: letzter Eintrag)")
    parser.add_argument("--tage", type=int, default=14, help="Länge des Messfensters in Tagen (Standard: 14)")
    parser.add_argument("--top-n", type=int, default=20, help="Anzahl langsamster Testdateien (Standard: 20)")
    parser.add_argument("--aus", type=Path, help="Ausgabepfad für Markdown-Befund (Standard: stdout)")
    parser.add_argument("--json-aus", type=Path, help="Optionaler Ausgabepfad für JSON-Zusammenfassung")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstiegspunkt; schreibt Markdown nach ``--aus`` oder stdout."""
    args = _baue_parser().parse_args(argv)
    fb_pfad = args.fahrtenbuch or (args.schlange / "fahrtenbuch.jsonl" if args.schlange else None)
    nl_pfad = args.nachtlauf or (args.schlange / "laeufe" / "laufzeit.jsonl" if args.schlange else None)

    befund = auswerten(
        fahrtenbuch_pfad=fb_pfad,
        nachtlauf_pfad=nl_pfad,
        ereignis_pfad=args.ereignis_protokoll,
        stichtag=args.stichtag,
        tage=args.tage,
        top_n=args.top_n,
    )
    markdown = formatiere_markdown(befund)

    if args.aus is not None:
        args.aus.parent.mkdir(parents=True, exist_ok=True)
        args.aus.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)

    if args.json_aus is not None:
        args.json_aus.parent.mkdir(parents=True, exist_ok=True)
        args.json_aus.write_text(
            json.dumps(befund_als_dict(befund), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
