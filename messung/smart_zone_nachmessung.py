"""Reproduzierbare Nachmessung der Smart-Zone-Grenzen aus Session-Transcripts (#875).

Berechnet aus JSONL-Transcripts und Staffel-Dateien (``st-*.json``):
- Messgröße A: gepaarte echte Tool-Fehlerquote 250–330k vs. 100–175k in denselben Sessions
  (ohne Hook-Blocks und Nutzer-Ablehnungen); Schaden bei Quote oben > 1,5× unten und >= 300
  Tool-Aufrufen oben.
- Messgröße B: Anteil Code-Edits (ohne ``.md`` / Handoff / Memory) ab 250k mit Re-Edit
  derselben Datei innerhalb von <= 10 Runden; Schaden bei > 60 %.
- Nebenkennzahlen: Übergaben je Woche (gegen Referenz ~7/Woche) und ``cache_read`` je
  interaktiver Session.
- 14-Tage-Frist-Prüfung gegen das Änderungsdatum aus ``smart-zone.json`` oder ``--umstellung``.

Läuft ohne Zusatzpakete (reine Standardbibliothek) identisch unter Linux und Windows.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import logging
import math
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("smart_zone_nachmessung")

#: Kontextbänder in k-Token (von inklusive, bis exklusive; None = offen nach oben).
KONTEXT_BAENDER: tuple[tuple[int, int | None], ...] = (
    (0, 100),
    (100, 175),
    (175, 250),
    (250, 300),
    (300, 330),
    (330, None),
)

#: Untere und obere Vergleichsspanne für Messgröße A (in Token).
BAND_UNTEN_VON = 100_000
BAND_UNTEN_BIS = 175_000
BAND_OBEN_VON = 250_000
BAND_OBEN_BIS = 330_000

#: Schwellen für Schadens-Erkennung (vorab festgelegt).
MIN_TOOLS_OBEN_SCHADEN = 300
SCHADEN_FAKTOR_A = 1.5
SCHADEN_SCHWELLE_B = 0.60
RE_EDIT_MAX_RUNDEN = 10
RE_EDIT_AB_TOKENS = 250_000
REFERENZ_UEBERGABEN_JE_WOCHE = 7.0
MINDEST_FRIST_TAGE = 14

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
KEINE_ECHTEN_FEHLER = frozenset({"reject", "hook"})
HANDOFF_WOERTER = ("handoff", ".claude-handoff", "übergabe", "staffel")
DATUM_MUSTER = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


@dataclasses.dataclass(frozen=True)
class ToolAufruf:
    """Einzelner Tool-Aufruf aus einer Assistenten-Runde."""

    name: str
    dateipfad: str | None
    tool_id: str | None


@dataclasses.dataclass(frozen=True)
class Runde:
    """Deduplizierte Assistenten-Runde mit Kontextstand und Tool-Aufrufen."""

    zeitstempel: str | None
    kontext_tokens: int
    cache_read_tokens: int
    modell: str | None
    tools: tuple[ToolAufruf, ...]
    output_tokens: int
    cache_creation_tokens: int


@dataclasses.dataclass(frozen=True)
class SessionDaten:
    """Geparste Kennwerte einer Session aus einer JSONL-Transcript-Datei."""

    session_id: str
    projekt_ordner: str
    runden: tuple[Runde, ...]
    fehler: dict[str, str | None]
    compacts: int
    handoff_start: bool
    bau: bool
    modell_familie: str


@dataclasses.dataclass(frozen=True)
class BandStatistik:
    """Aggregierte Qualitäts- und Edit-Kennzahlen je Kontextband."""

    bezeichnung: str
    von_k: int
    bis_k: int | None
    sessions: int
    runden: int
    tools: int
    fehler_gesamt: int
    fehler_echt: int
    kategorien: dict[str, int]
    edits_gesamt: int
    re_edits_gesamt: int
    code_edits: int
    code_re_edits: int


@dataclasses.dataclass(frozen=True)
class GepaarteSession:
    """Gepaarte Tool-Zähler einer Session für 100–175k vs. 250–330k."""

    session_id: str
    tools_unten: int
    fehler_echt_unten: int
    tools_oben: int
    fehler_echt_oben: int


@dataclasses.dataclass(frozen=True)
class MessgroesseA:
    """Messgröße A: gepaarte echte Tool-Fehlerquote 250–330k vs. 100–175k."""

    sessions_gepaart: int
    tools_unten: int
    fehler_echt_unten: int
    quote_unten: float | None
    tools_oben: int
    fehler_echt_oben: int
    quote_oben: float | None
    verhaeltnis: float | None
    median_unten: float | None
    median_oben: float | None
    schlechter_anzahl: int
    min_tools_oben: int
    schaden_faktor: float
    stichprobe_ausreichend: bool
    schaden_erkannt: bool


@dataclasses.dataclass(frozen=True)
class MessgroesseB:
    """Messgröße B: Re-Edit-Anteil derselben Code-Datei <= 10 Runden ab 250k."""

    code_edits_ab_250k: int
    code_re_edits_le_10: int
    anteil_code_re_edit: float | None
    md_edits_ab_250k: int
    gesamt_edits_ab_250k: int
    dateiarten: dict[str, int]
    schaden_schwelle: float
    schaden_erkannt: bool


@dataclasses.dataclass(frozen=True)
class UebergabenStatistik:
    """Nebenkennzahl: Staffel-Übergaben je Woche und cache_read je interaktiver Session."""

    staffel_gesamt: int
    staffel_im_zeitraum: int
    zeitraum_tage: float
    uebergaben_je_woche: float | None
    status_verteilung: dict[str, int]
    referenz_je_woche: float
    cache_read_interaktiv_median_m: float | None
    interaktive_sessions: int


@dataclasses.dataclass(frozen=True)
class FristStatus:
    """Prüfung der 14-Tage-Wartezeit nach Umstellung der Opus-Grenzen."""

    umstellung_datum: str | None
    stichtag: str
    tage_seit_umstellung: int | None
    mindest_tage: int
    frist_erfuellt: bool


@dataclasses.dataclass(frozen=True)
class Auswertung:
    """Vollständiges Auswertungsergebnis der Smart-Zone-Nachmessung."""

    zeitraum_ab: str
    zeitraum_bis: str
    modell_filter: str
    sessions_gesamt: int
    sessions_modell: int
    compacts_anzahl: int
    frist: FristStatus
    baender: tuple[BandStatistik, ...]
    messgroesse_a: MessgroesseA
    messgroesse_b: MessgroesseB
    uebergaben: UebergabenStatistik
    empfehlung_code: str
    empfehlung_text: str
    hinweise: tuple[str, ...]


def quantil(werte: Sequence[float], anteil: float) -> float | None:
    """Berechnet ein einfaches Quantil deterministisch (None bei leerer Folge)."""
    if not werte:
        return None
    sortiert = sorted(werte)
    idx = min(len(sortiert) - 1, round(anteil * (len(sortiert) - 1)))
    return float(sortiert[idx])


def erkenne_modell_familie(modell: str | None) -> str:
    """Ordnet einen Modellnamen seiner Familie zu (opus, sonnet, fable oder Rohwert)."""
    roh = (modell or "").strip()
    klein = roh.lower()
    if "opus" in klein:
        return "opus"
    if "sonnet" in klein:
        return "sonnet"
    if "fable" in klein:
        return "fable"
    return roh or "?"


def klassifiziere_fehler(text: str) -> str:
    """Teilt eine Tool-Fehlermeldung in die Kategorien aus der Voruntersuchung ein."""
    tl = text[:400].lower()
    if "doesn't want" in tl or "user rejected" in tl or "interrupted" in tl:
        return "reject"
    if "hook" in tl or "blocked" in tl or "blockiert" in tl or "permission" in tl:
        return "hook"
    if (
        "string to replace not found" in tl
        or "has not been read" in tl
        or "modified since read" in tl
        or "file has been" in tl
    ):
        return "stringmatch"
    if "exit code" in tl:
        return "exit"
    return "andere"


def normalisiere_dateipfad(pfad: str) -> str:
    """Normalisiert Windows- und POSIX-Pfade für Re-Edit-Vergleiche innerhalb einer Session."""
    norm = pfad.strip().replace("\\", "/")
    if len(norm) >= 2 and norm[1] == ":":
        return norm.lower()
    return norm


def klassifiziere_dateiart(pfad: str) -> str:
    """Trennt Code-Dateien von Handoff-, Markdown- und Memory-Dateien."""
    klein = pfad.replace("\\", "/").lower()
    if "handoff" in klein:
        return "handoff"
    if klein.endswith(".md"):
        return "doku_md"
    if "memory" in klein:
        return "memory"
    return "code"


def _extrahieren_text(inhalt: Any) -> str:
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        teile: list[str] = []
        for eintrag in inhalt:
            if isinstance(eintrag, dict):
                txt = eintrag.get("text")
                if isinstance(txt, str):
                    teile.append(txt)
        return " ".join(teile)
    return ""


def _ist_bau_ordner(ordner_name: str) -> bool:
    klein = ordner_name.lower()
    return "-wt-" in klein or klein.startswith("wt-")


def lade_transcript(pfad: Path, projekt_ordner: str) -> SessionDaten | None:
    """Liest eine einzelne JSONL-Transcript-Datei und dedupliziert Assistenten-Runden."""
    gesehene_ids: set[str] = set()
    runden_roh: list[list[Any]] = []
    fehler: dict[str, str | None] = {}
    compacts = 0
    erster_nutzer_text: str | None = None

    try:
        with pfad.open("r", encoding="utf-8", errors="replace") as fh:
            for zeilen_nr, zeile in enumerate(fh, 1):
                zeile = zeile.strip()
                if not zeile:
                    continue
                try:
                    datensatz = json.loads(zeile)
                except ValueError:
                    log.debug("Ungültige JSON-Zeile übersprungen: %s:%d", pfad.name, zeilen_nr)
                    continue
                if not isinstance(datensatz, dict):
                    continue

                typ = datensatz.get("type")
                if datensatz.get("isCompactSummary") or (
                    typ == "system" and datensatz.get("subtype") == "compact_boundary"
                ):
                    compacts += 1

                if typ == "user":
                    nachricht = datensatz.get("message")
                    inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
                    if erster_nutzer_text is None and not datensatz.get("isMeta"):
                        txt = _extrahieren_text(inhalt).strip()
                        if txt:
                            erster_nutzer_text = txt.lower()[:3000]
                    if isinstance(inhalt, list):
                        for block in inhalt:
                            if not isinstance(block, dict) or block.get("type") != "tool_result":
                                continue
                            tid = block.get("tool_use_id")
                            if not isinstance(tid, str):
                                continue
                            if bool(block.get("is_error")):
                                kat: str | None = klassifiziere_fehler(_extrahieren_text(block.get("content")))
                            else:
                                kat = None
                            fehler[tid] = kat

                elif typ == "assistant":
                    nachricht = datensatz.get("message")
                    if not isinstance(nachricht, dict):
                        continue
                    usage = nachricht.get("usage")
                    msg_id = nachricht.get("id")
                    roh_inhalt = nachricht.get("content")
                    tools: list[ToolAufruf] = []
                    if isinstance(roh_inhalt, list):
                        for block in roh_inhalt:
                            if not isinstance(block, dict) or block.get("type") != "tool_use":
                                continue
                            eingabe = block.get("input")
                            dateipfad: str | None = None
                            if isinstance(eingabe, dict):
                                roh_pfad = eingabe.get("file_path") or eingabe.get("notebook_path")
                                if isinstance(roh_pfad, str) and roh_pfad.strip():
                                    dateipfad = roh_pfad
                            name = block.get("name")
                            tid = block.get("id")
                            tools.append(
                                ToolAufruf(
                                    name=str(name) if name is not None else "",
                                    dateipfad=dateipfad,
                                    tool_id=str(tid) if tid is not None else None,
                                )
                            )
                    if isinstance(msg_id, str) and msg_id in gesehene_ids:
                        if tools and runden_roh:
                            runden_roh[-1][4].extend(tools)
                        continue
                    if isinstance(msg_id, str):
                        gesehene_ids.add(msg_id)
                    if not isinstance(usage, dict) or not usage:
                        continue
                    modell = nachricht.get("model")
                    if modell == "<synthetic>":
                        continue
                    inp = int(usage.get("input_tokens") or 0)
                    cache_read = int(usage.get("cache_read_input_tokens") or 0)
                    cache_create = int(usage.get("cache_creation_input_tokens") or 0)
                    out_tok = int(usage.get("output_tokens") or 0)
                    kontext = inp + cache_read + cache_create
                    ts = datensatz.get("timestamp")
                    runden_roh.append(
                        [
                            str(ts) if isinstance(ts, str) else None,
                            kontext,
                            cache_read,
                            str(modell) if isinstance(modell, str) else None,
                            tools,
                            out_tok,
                            cache_create,
                        ]
                    )
    except OSError as exc:
        log.warning("Transcript nicht lesbar (%s): %s", pfad, exc)
        return None

    if not runden_roh:
        return None

    runden = tuple(
        Runde(
            zeitstempel=r[0],
            kontext_tokens=r[1],
            cache_read_tokens=r[2],
            modell=r[3],
            tools=tuple(r[4]),
            output_tokens=r[5],
            cache_creation_tokens=r[6],
        )
        for r in runden_roh
    )
    familien = collections.Counter(erkenne_modell_familie(r.modell) for r in runden)
    haupt_familie = familien.most_common(1)[0][0]
    fu = erster_nutzer_text or ""

    return SessionDaten(
        session_id=pfad.stem,
        projekt_ordner=projekt_ordner,
        runden=runden,
        fehler=fehler,
        compacts=compacts,
        handoff_start=any(wort in fu for wort in HANDOFF_WOERTER),
        bau=("to_spawn_ticket" in fu) or _ist_bau_ordner(projekt_ordner),
        modell_familie=haupt_familie,
    )


def _datum_prefix(zeitstempel: str | None) -> str | None:
    if not zeitstempel:
        return None
    treffer = DATUM_MUSTER.search(zeitstempel)
    return treffer.group(1) if treffer else None


def _ab_zeitstempel_epoch(ab_datum: str) -> float | None:
    try:
        dt = datetime.fromisoformat(f"{ab_datum}T00:00:00+00:00")
    except ValueError:
        return None
    return dt.timestamp()


def lade_sessions(
    projekt_wurzeln: Sequence[Path],
    *,
    ab_datum: str,
    bis_datum: str,
    min_runden: int = 5,
    projekt_filter: str | None = None,
) -> list[SessionDaten]:
    """Lädt alle passenden Sessions deterministisch aus einem oder mehreren Projekt-Ordnern."""
    filter_muster = re.compile(projekt_filter, re.IGNORECASE) if projekt_filter else None
    ab_epoch = _ab_zeitstempel_epoch(ab_datum)
    jetzt = time.time()
    nutze_mtime_optimierung = ab_epoch is not None and ab_epoch < jetzt

    gefunden: dict[str, SessionDaten] = {}
    dateien_geprueft = 0
    dateien_uebersprungen_mtime = 0

    for wurzel in sorted(projekt_wurzeln, key=lambda p: str(p)):
        if not wurzel.is_dir():
            log.warning("Projekt-Ordner existiert nicht: %s", wurzel)
            continue
        unterordner = sorted(
            (p for p in wurzel.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
        for ordner in unterordner:
            if "pytest" in ordner.name.lower():
                continue
            if filter_muster and not filter_muster.search(ordner.name):
                continue
            for datei in sorted(ordner.glob("*.jsonl"), key=lambda p: p.name):
                if nutze_mtime_optimierung and ab_epoch is not None:
                    try:
                        if datei.stat().st_mtime < ab_epoch - 86400:
                            dateien_uebersprungen_mtime += 1
                            continue
                    except OSError:
                        continue
                dateien_geprueft += 1
                session = lade_transcript(datei, ordner.name)
                if session is None or len(session.runden) < min_runden:
                    continue
                erste_zeit = _datum_prefix(session.runden[0].zeitstempel)
                if erste_zeit is None:
                    continue
                if erste_zeit < ab_datum or erste_zeit > bis_datum:
                    continue
                # Bei doppelter Session-ID aus mehreren Quellen die längere Version behalten
                alt = gefunden.get(session.session_id)
                if alt is None or len(session.runden) > len(alt.runden):
                    gefunden[session.session_id] = session

    log.info(
        "Sessions geladen: %d (geprüft: %d Dateien, vorab per mtime übersprungen: %d)",
        len(gefunden),
        dateien_geprueft,
        dateien_uebersprungen_mtime,
    )
    return [gefunden[sid] for sid in sorted(gefunden)]


def _band_name(von_k: int, bis_k: int | None) -> str:
    if bis_k is None:
        return f">={von_k}k"
    return f"{von_k}-{bis_k}k"


def _ordne_band_zu(kontext_tokens: int) -> str:
    k = kontext_tokens / 1000.0
    for von_k, bis_k in KONTEXT_BAENDER:
        if bis_k is None:
            if k >= von_k:
                return _band_name(von_k, bis_k)
        elif von_k <= k < bis_k:
            return _band_name(von_k, bis_k)
    return _band_name(KONTEXT_BAENDER[-1][0], KONTEXT_BAENDER[-1][1])


def lese_umstellung_datum(smart_zone_pfad: Path | None) -> str | None:
    """Liest das Änderungsdatum (JJJJ-MM-TT) aus ``smart-zone.json`` (Feld ``_aenderung``)."""
    if smart_zone_pfad is None or not smart_zone_pfad.is_file():
        return None
    try:
        daten = json.loads(smart_zone_pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("smart-zone.json nicht lesbar (%s): %s", smart_zone_pfad, exc)
        return None
    if not isinstance(daten, dict):
        return None
    aenderung = daten.get("_aenderung")
    if isinstance(aenderung, str):
        treffer = DATUM_MUSTER.search(aenderung)
        if treffer:
            return treffer.group(1)
    return None


def pruefe_frist(
    umstellung_datum: str | None,
    stichtag: str,
    *,
    mindest_tage: int = MINDEST_FRIST_TAGE,
) -> FristStatus:
    """Prüft, ob seit Umstellung der Opus-Grenzen mindestens 14 Tage vergangen sind."""
    if not umstellung_datum:
        return FristStatus(
            umstellung_datum=None,
            stichtag=stichtag,
            tage_seit_umstellung=None,
            mindest_tage=mindest_tage,
            frist_erfuellt=False,
        )
    try:
        start = date.fromisoformat(umstellung_datum)
        ende = date.fromisoformat(stichtag)
    except ValueError:
        log.warning("Ungültiges Datum bei Fristprüfung: umstellung=%s stichtag=%s", umstellung_datum, stichtag)
        return FristStatus(
            umstellung_datum=umstellung_datum,
            stichtag=stichtag,
            tage_seit_umstellung=None,
            mindest_tage=mindest_tage,
            frist_erfuellt=False,
        )
    tage = (ende - start).days
    return FristStatus(
        umstellung_datum=umstellung_datum,
        stichtag=stichtag,
        tage_seit_umstellung=tage,
        mindest_tage=mindest_tage,
        frist_erfuellt=tage >= mindest_tage,
    )


def lade_staffel_uebergaben(
    staffel_ordner: Sequence[Path],
    *,
    ab_datum: str,
    bis_datum: str,
) -> tuple[int, int, dict[str, int]]:
    """Liest ``st-*.json`` aus Staffel-Ordnern und zählt Übergaben im Zeitraum."""
    gesehene_ids: set[str] = set()
    gesamt = 0
    im_zeitraum = 0
    status_zaehler: collections.Counter[str] = collections.Counter()

    for ordner in sorted(staffel_ordner, key=lambda p: str(p)):
        if not ordner.is_dir():
            log.warning("Staffel-Ordner existiert nicht: %s", ordner)
            continue
        for datei in sorted(ordner.glob("st-*.json"), key=lambda p: p.name):
            try:
                daten = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.debug("Staffel-Datei übersprungen (%s): %s", datei.name, exc)
                continue
            if not isinstance(daten, dict):
                continue
            sid = str(daten.get("staffel_id") or datei.stem)
            if sid in gesehene_ids:
                continue
            gesehene_ids.add(sid)
            gesamt += 1
            tag = _datum_prefix(daten.get("ts") if isinstance(daten.get("ts"), str) else None)
            if tag is not None and ab_datum <= tag <= bis_datum:
                im_zeitraum += 1
                status = str(daten.get("status") or "unbekannt")
                status_zaehler[status] += 1

    return gesamt, im_zeitraum, dict(sorted(status_zaehler.items()))


def _berechne_zeitraum_tage(ab_datum: str, bis_datum: str) -> float:
    try:
        start = date.fromisoformat(ab_datum)
        ende = date.fromisoformat(bis_datum)
    except ValueError:
        return 0.0
    differenz = (ende - start).days + 1
    return float(max(0, differenz))


def berechne_empfehlung(
    frist: FristStatus,
    messgroesse_a: MessgroesseA,
    messgroesse_b: MessgroesseB,
) -> tuple[str, str]:
    """Leitet aus Friststatus und Schadens-Schwellen eine klare Empfehlung ab."""
    if not frist.frist_erfuellt:
        fruehestens = ""
        if frist.umstellung_datum:
            try:
                ziel = date.fromisoformat(frist.umstellung_datum) + timedelta(days=frist.mindest_tage)
                fruehestens = f" (frühestens ab {ziel.isoformat()})"
            except ValueError:
                fruehestens = ""
        return (
            "abwarten",
            f"14-Tage-Frist noch nicht abgelaufen{fruehestens}; Nachmessung nach Fristablauf wiederholen.",
        )

    if messgroesse_a.schaden_erkannt or messgroesse_b.schaden_erkannt:
        gruende: list[str] = []
        if messgroesse_a.schaden_erkannt:
            gruende.append("Messgröße A (gepaarte echte Fehlerquote > 1,5× bei >= 300 Tools oben)")
        if messgroesse_b.schaden_erkannt:
            gruende.append("Messgröße B (Code-Re-Edit-Anteil ab 250k > 60 %)")
        return (
            "zurueck",
            "Schadens-Schwelle gerissen (" + "; ".join(gruende) + "); Opus-Grenzen zurücksetzen.",
        )

    if not messgroesse_a.stichprobe_ausreichend:
        return (
            "beobachten",
            (
                "Keine Schadens-Schwelle gerissen, aber Stichprobe oben (< 300 Tool-Aufrufe in 250–330k) "
                "zu klein für ein belastbares Urteil; Grenzen vorerst belassen und weiter beobachten."
            ),
        )

    return (
        "bleiben_oder_anheben",
        (
            "Keine Schadens-Schwelle gerissen bei ausreichender Stichprobe (>= 300 Tool-Aufrufe oben); "
            "Opus-Grenzen (300k/330k) beibehalten oder vorsichtig weiter anheben."
        ),
    )


def auswerten(
    *,
    projekt_wurzeln: Sequence[Path],
    staffel_ordner: Sequence[Path],
    ab_datum: str,
    stichtag: str,
    umstellung_datum: str | None = None,
    smart_zone_pfad: Path | None = None,
    modell_filter: str = "opus",
    min_runden: int = 5,
    projekt_filter: str | None = None,
) -> Auswertung:
    """Führt die vollständige Smart-Zone-Nachmessung reproduzierbar aus."""
    effektive_umstellung = umstellung_datum or lese_umstellung_datum(smart_zone_pfad)
    frist = pruefe_frist(effektive_umstellung, stichtag)

    alle_sessions = lade_sessions(
        projekt_wurzeln,
        ab_datum=ab_datum,
        bis_datum=stichtag,
        min_runden=min_runden,
        projekt_filter=projekt_filter,
    )
    ziel_sessions = [s for s in alle_sessions if s.modell_familie == modell_filter]
    compacts_anzahl = sum(1 for s in ziel_sessions if s.compacts > 0)

    band_zaehler: dict[str, collections.Counter[str]] = {
        _band_name(v, b): collections.Counter() for v, b in KONTEXT_BAENDER
    }
    band_sessions: dict[str, set[str]] = {_band_name(v, b): set() for v, b in KONTEXT_BAENDER}

    gepaarte: list[GepaarteSession] = []
    dateiarten_ab_250k: collections.Counter[str] = collections.Counter()
    code_edits_ab_250k = 0
    code_re_edits_le_10 = 0
    md_edits_ab_250k = 0
    gesamt_edits_ab_250k = 0

    for session in ziel_sessions:
        letzte_runde_datei: dict[str, int] = {}
        tools_unten = 0
        fehler_echt_unten = 0
        tools_oben = 0
        fehler_echt_oben = 0

        for idx, runde in enumerate(session.runden):
            bname = _ordne_band_zu(runde.kontext_tokens)
            bz = band_zaehler[bname]
            bz["runden"] += 1
            band_sessions[bname].add(session.session_id)

            for tool in runde.tools:
                if tool.tool_id is not None and tool.tool_id in session.fehler:
                    kat = session.fehler[tool.tool_id]
                    bz["tools"] += 1
                    ist_echt = kat is not None and kat not in KEINE_ECHTEN_FEHLER
                    if kat is not None:
                        bz["fehler_gesamt"] += 1
                        bz[f"kat_{kat}"] += 1
                    if ist_echt:
                        bz["fehler_echt"] += 1

                    if BAND_UNTEN_VON <= runde.kontext_tokens < BAND_UNTEN_BIS:
                        tools_unten += 1
                        if ist_echt:
                            fehler_echt_unten += 1
                    elif BAND_OBEN_VON <= runde.kontext_tokens < BAND_OBEN_BIS:
                        tools_oben += 1
                        if ist_echt:
                            fehler_echt_oben += 1

                if tool.name in EDIT_TOOLS and tool.dateipfad:
                    norm_pfad = normalisiere_dateipfad(tool.dateipfad)
                    ist_re_edit = norm_pfad in letzte_runde_datei and (
                        idx - letzte_runde_datei[norm_pfad] <= RE_EDIT_MAX_RUNDEN
                    )
                    letzte_runde_datei[norm_pfad] = idx
                    art = klassifiziere_dateiart(tool.dateipfad)

                    bz["edits_gesamt"] += 1
                    if ist_re_edit:
                        bz["re_edits_gesamt"] += 1
                    if art == "code":
                        bz["code_edits"] += 1
                        if ist_re_edit:
                            bz["code_re_edits"] += 1

                    if runde.kontext_tokens >= RE_EDIT_AB_TOKENS:
                        gesamt_edits_ab_250k += 1
                        dateiarten_ab_250k[art] += 1
                        if art == "code":
                            code_edits_ab_250k += 1
                            if ist_re_edit:
                                code_re_edits_le_10 += 1
                        elif art in ("doku_md", "handoff"):
                            md_edits_ab_250k += 1

        if tools_unten > 0 and tools_oben > 0:
            gepaarte.append(
                GepaarteSession(
                    session_id=session.session_id,
                    tools_unten=tools_unten,
                    fehler_echt_unten=fehler_echt_unten,
                    tools_oben=tools_oben,
                    fehler_echt_oben=fehler_echt_oben,
                )
            )

    sum_tools_u = sum(g.tools_unten for g in gepaarte)
    sum_err_u = sum(g.fehler_echt_unten for g in gepaarte)
    sum_tools_o = sum(g.tools_oben for g in gepaarte)
    sum_err_o = sum(g.fehler_echt_oben for g in gepaarte)

    quote_u = (sum_err_u / sum_tools_u) if sum_tools_u > 0 else None
    quote_o = (sum_err_o / sum_tools_o) if sum_tools_o > 0 else None
    if quote_u is not None and quote_o is not None:
        if quote_u > 0:
            verhaeltnis: float | None = quote_o / quote_u
        elif quote_o > 0:
            verhaeltnis = math.inf
        else:
            verhaeltnis = 1.0
    else:
        verhaeltnis = None

    raten_u = [g.fehler_echt_unten / g.tools_unten for g in gepaarte]
    raten_o = [g.fehler_echt_oben / g.tools_oben for g in gepaarte]
    schlechter_anzahl = sum(1 for ru, ro in zip(raten_u, raten_o) if ro > ru)

    stichprobe_a_ok = sum_tools_o >= MIN_TOOLS_OBEN_SCHADEN
    schaden_a = (
        stichprobe_a_ok
        and quote_u is not None
        and quote_o is not None
        and ((quote_u == 0.0 and quote_o > 0.0) or (quote_u > 0.0 and quote_o > SCHADEN_FAKTOR_A * quote_u))
    )
    messgroesse_a = MessgroesseA(
        sessions_gepaart=len(gepaarte),
        tools_unten=sum_tools_u,
        fehler_echt_unten=sum_err_u,
        quote_unten=quote_u,
        tools_oben=sum_tools_o,
        fehler_echt_oben=sum_err_o,
        quote_oben=quote_o,
        verhaeltnis=verhaeltnis,
        median_unten=quantil(raten_u, 0.5),
        median_oben=quantil(raten_o, 0.5),
        schlechter_anzahl=schlechter_anzahl,
        min_tools_oben=MIN_TOOLS_OBEN_SCHADEN,
        schaden_faktor=SCHADEN_FAKTOR_A,
        stichprobe_ausreichend=stichprobe_a_ok,
        schaden_erkannt=schaden_a,
    )

    anteil_b = (code_re_edits_le_10 / code_edits_ab_250k) if code_edits_ab_250k > 0 else None
    schaden_b = anteil_b is not None and anteil_b > SCHADEN_SCHWELLE_B
    messgroesse_b = MessgroesseB(
        code_edits_ab_250k=code_edits_ab_250k,
        code_re_edits_le_10=code_re_edits_le_10,
        anteil_code_re_edit=anteil_b,
        md_edits_ab_250k=md_edits_ab_250k,
        gesamt_edits_ab_250k=gesamt_edits_ab_250k,
        dateiarten=dict(sorted(dateiarten_ab_250k.items())),
        schaden_schwelle=SCHADEN_SCHWELLE_B,
        schaden_erkannt=schaden_b,
    )

    staffel_gesamt, staffel_im_zeitraum, status_verteilung = lade_staffel_uebergaben(
        staffel_ordner, ab_datum=ab_datum, bis_datum=stichtag
    )
    zeitraum_tage = _berechne_zeitraum_tage(ab_datum, stichtag)
    uebergaben_woche = (staffel_im_zeitraum / (zeitraum_tage / 7.0)) if zeitraum_tage > 0 else None
    interaktive_cache_m = [sum(r.cache_read_tokens for r in s.runden) / 1_000_000.0 for s in ziel_sessions if not s.bau]
    uebergaben = UebergabenStatistik(
        staffel_gesamt=staffel_gesamt,
        staffel_im_zeitraum=staffel_im_zeitraum,
        zeitraum_tage=zeitraum_tage,
        uebergaben_je_woche=uebergaben_woche,
        status_verteilung=status_verteilung,
        referenz_je_woche=REFERENZ_UEBERGABEN_JE_WOCHE,
        cache_read_interaktiv_median_m=quantil(interaktive_cache_m, 0.5),
        interaktive_sessions=len(interaktive_cache_m),
    )

    baender = tuple(
        BandStatistik(
            bezeichnung=_band_name(von_k, bis_k),
            von_k=von_k,
            bis_k=bis_k,
            sessions=len(band_sessions[_band_name(von_k, bis_k)]),
            runden=band_zaehler[_band_name(von_k, bis_k)]["runden"],
            tools=band_zaehler[_band_name(von_k, bis_k)]["tools"],
            fehler_gesamt=band_zaehler[_band_name(von_k, bis_k)]["fehler_gesamt"],
            fehler_echt=band_zaehler[_band_name(von_k, bis_k)]["fehler_echt"],
            kategorien={
                kat: band_zaehler[_band_name(von_k, bis_k)][f"kat_{kat}"]
                for kat in ("reject", "hook", "stringmatch", "exit", "andere")
            },
            edits_gesamt=band_zaehler[_band_name(von_k, bis_k)]["edits_gesamt"],
            re_edits_gesamt=band_zaehler[_band_name(von_k, bis_k)]["re_edits_gesamt"],
            code_edits=band_zaehler[_band_name(von_k, bis_k)]["code_edits"],
            code_re_edits=band_zaehler[_band_name(von_k, bis_k)]["code_re_edits"],
        )
        for von_k, bis_k in KONTEXT_BAENDER
    )

    empfehlung_code, empfehlung_text = berechne_empfehlung(frist, messgroesse_a, messgroesse_b)

    hinweise: list[str] = []
    if not frist.frist_erfuellt:
        hinweise.append(
            f"14-Tage-Frist nicht erfüllt (Umstellung: {frist.umstellung_datum or 'unbekannt'}, "
            f"Stichtag: {frist.stichtag}, vergangen: {frist.tage_seit_umstellung} Tage)."
        )
    if not messgroesse_a.stichprobe_ausreichend:
        hinweise.append(
            f"Messgröße A unter Stichproben-Schwelle: {messgroesse_a.tools_oben} von "
            f"{messgroesse_a.min_tools_oben} nötigen Tool-Aufrufen in 250–330k."
        )
    if messgroesse_b.code_edits_ab_250k == 0:
        hinweise.append("Messgröße B: keine Code-Edits ab 250k im Zeitraum.")
    if compacts_anzahl > 0:
        hinweise.append(
            f"{compacts_anzahl} Sessions mit Compaction im Zeitraum (Spitzen-Kontext potenziell unterschätzt)."
        )

    return Auswertung(
        zeitraum_ab=ab_datum,
        zeitraum_bis=stichtag,
        modell_filter=modell_filter,
        sessions_gesamt=len(alle_sessions),
        sessions_modell=len(ziel_sessions),
        compacts_anzahl=compacts_anzahl,
        frist=frist,
        baender=baender,
        messgroesse_a=messgroesse_a,
        messgroesse_b=messgroesse_b,
        uebergaben=uebergaben,
        empfehlung_code=empfehlung_code,
        empfehlung_text=empfehlung_text,
        hinweise=tuple(hinweise),
    )


def _fmt_prozent(wert: float | None) -> str:
    if wert is None:
        return "–"
    return f"{wert * 100:.1f} %"


def _fmt_verhaeltnis(wert: float | None) -> str:
    if wert is None:
        return "–"
    if math.isinf(wert):
        return "∞"
    return f"{wert:.2f}×"


def formatiere_markdown(
    auswertung: Auswertung,
    *,
    quellen_hinweis: str | None = None,
) -> str:
    """Formatiert das Auswertungsergebnis als deterministischen Markdown-Befund."""
    ma = auswertung.messgroesse_a
    mb = auswertung.messgroesse_b
    ue = auswertung.uebergaben
    fr = auswertung.frist

    frist_label = (
        f"erfüllt ({fr.tage_seit_umstellung} Tage seit {fr.umstellung_datum})"
        if fr.frist_erfuellt
        else f"NICHT erfüllt ({fr.tage_seit_umstellung if fr.tage_seit_umstellung is not None else '?'} "
        f"von {fr.mindest_tage} Tagen seit {fr.umstellung_datum or 'unbekannt'})"
    )

    zeilen: list[str] = [
        "# Smart-Zone-Nachmessung (Ticket #875 / App #1391)",
        "",
        (f"- **Zeitraum:** {auswertung.zeitraum_ab} bis {auswertung.zeitraum_bis} ({ue.zeitraum_tage:.0f} Tage)"),
        f"- **Modell-Filter:** `{auswertung.modell_filter}`",
        f"- **14-Tage-Frist:** {frist_label}",
        (
            f"- **Stichprobengröße:** {auswertung.sessions_modell} `{auswertung.modell_filter}`-Sessions "
            f"(von {auswertung.sessions_gesamt} Sessions gesamt, >= 5 Runden; {auswertung.compacts_anzahl} mit Compaction)"
        ),
    ]
    if quellen_hinweis:
        zeilen.append(f"- **Datenquellen:** {quellen_hinweis}")

    zeilen.extend(
        [
            "",
            "## Empfehlung an den Nutzer",
            "",
            f"- **Urteil (`{auswertung.empfehlung_code}`):** {auswertung.empfehlung_text}",
            "",
            "## Messgröße A: Gepaarte echte Tool-Fehlerquote (250–330k vs. 100–175k)",
            "",
            (
                f"Schadens-Schwelle: Quote oben > {ma.schaden_faktor:.1f}× Quote unten bei "
                f">= {ma.min_tools_oben} Tool-Aufrufen oben (ohne Hook-Blocks und Nutzer-Ablehnungen)."
            ),
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| Gepaarte Sessions (Tools in beiden Bändern) | {ma.sessions_gepaart} |",
            (
                f"| Unten (100–175k): echte Fehler / Tools | {ma.fehler_echt_unten} / {ma.tools_unten} "
                f"({_fmt_prozent(ma.quote_unten)}) |"
            ),
            (
                f"| Oben (250–330k): echte Fehler / Tools | {ma.fehler_echt_oben} / {ma.tools_oben} "
                f"({_fmt_prozent(ma.quote_oben)}) |"
            ),
            f"| Verhältnis (oben / unten) | {_fmt_verhaeltnis(ma.verhaeltnis)} |",
            (
                f"| Session-Median unten vs. oben | {_fmt_prozent(ma.median_unten)} vs. {_fmt_prozent(ma.median_oben)} "
                f"(schlechter oben in {ma.schlechter_anzahl}/{ma.sessions_gepaart}) |"
            ),
            f"| Stichprobe >= {ma.min_tools_oben} Tools oben | {'ja' if ma.stichprobe_ausreichend else 'nein'} |",
            f"| Schaden erkannt | {'JA' if ma.schaden_erkannt else 'nein'} |",
            "",
            "## Messgröße B: Code-Re-Edit-Anteil ab 250k (<= 10 Runden)",
            "",
            (
                f"Schadens-Schwelle: > {mb.schaden_schwelle * 100:.0f} % Code-Edits (ohne `.md`, Handoff, Memory), "
                f"die dieselbe Datei innerhalb von <= {RE_EDIT_MAX_RUNDEN} Runden erneut anfassen."
            ),
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| Code-Edits ab 250k | {mb.code_edits_ab_250k} |",
            (
                f"| Davon Re-Edits <= {RE_EDIT_MAX_RUNDEN} Runden | {mb.code_re_edits_le_10} "
                f"({_fmt_prozent(mb.anteil_code_re_edit)}) |"
            ),
            f"| Markdown-/Handoff-Edits ab 250k (ausgeklammert) | {mb.md_edits_ab_250k} |",
            f"| Edits ab 250k gesamt | {mb.gesamt_edits_ab_250k} |",
            f"| Dateiarten ab 250k | {json.dumps(mb.dateiarten, ensure_ascii=False)} |",
            f"| Schaden erkannt | {'JA' if mb.schaden_erkannt else 'nein'} |",
            "",
            "## Nebenkennzahlen: Übergaben je Woche & Cache-Read",
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| Staffel-Übergaben im Zeitraum (gesamt gefunden) | {ue.staffel_im_zeitraum} ({ue.staffel_gesamt}) |",
            (
                f"| Übergaben je Woche (Referenz vorher: ~{ue.referenz_je_woche:.0f}/Woche) | "
                f"{f'{ue.uebergaben_je_woche:.1f}' if ue.uebergaben_je_woche is not None else '–'} |"
            ),
            f"| Staffel-Status im Zeitraum | {json.dumps(ue.status_verteilung, ensure_ascii=False)} |",
            f"| Interaktive `{auswertung.modell_filter}`-Sessions | {ue.interaktive_sessions} |",
            (
                f"| Median `cache_read` je interaktiver Session | "
                f"{f'{ue.cache_read_interaktiv_median_m:.2f} M' if ue.cache_read_interaktiv_median_m is not None else '–'} |"
            ),
            "",
            f"## Kontext-Bänder ({auswertung.modell_filter})",
            "",
            (
                "| Band | Sessions | Runden | Tools | Fehler gesamt | Fehler echt (±95 %-KI) | "
                "Kategorien (rej/hook/str/exit/sonst) | Code-Edits (Re-Edit) |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
    )

    for b in auswertung.baender:
        n = b.tools
        p_ges = (b.fehler_gesamt / n) if n > 0 else None
        p_echt = (b.fehler_echt / n) if n > 0 else None
        if p_echt is not None and n > 0:
            ki = 1.96 * math.sqrt(p_echt * (1.0 - p_echt) / n)
            echt_txt = f"{p_echt * 100:.1f} % ± {ki * 100:.1f}"
        else:
            echt_txt = "–"
        kat_txt = (
            f"{b.kategorien['reject']}/{b.kategorien['hook']}/{b.kategorien['stringmatch']}/"
            f"{b.kategorien['exit']}/{b.kategorien['andere']}"
        )
        code_txt = f"{b.code_edits} ({b.code_re_edits / b.code_edits * 100:.0f} %)" if b.code_edits > 0 else "0 (–)"
        zeilen.append(
            f"| {b.bezeichnung} | {b.sessions} | {b.runden} | {b.tools} | "
            f"{_fmt_prozent(p_ges)} | {echt_txt} | {kat_txt} | {code_txt} |"
        )

    if auswertung.hinweise:
        zeilen.extend(["", "## Ehrliche Aussagekraft & Hinweise", ""])
        for hinweis in auswertung.hinweise:
            zeilen.append(f"- {hinweis}")

    return "\n".join(zeilen) + "\n"


def als_dict(auswertung: Auswertung) -> dict[str, Any]:
    """Wandelt das Ergebnis in ein JSON-serialisierbares Dictionary um."""
    roh = dataclasses.asdict(auswertung)
    verhaeltnis = roh["messgroesse_a"]["verhaeltnis"]
    if isinstance(verhaeltnis, float) and math.isinf(verhaeltnis):
        roh["messgroesse_a"]["verhaeltnis"] = "inf"
    return roh


def _standard_pfade() -> tuple[Path, Path, Path]:
    claude_dir = Path.home() / ".claude"
    return (
        claude_dir / "projects",
        claude_dir / "data" / "staffel",
        claude_dir / "smart-zone.json",
    )


def baue_parser() -> argparse.ArgumentParser:
    """Erzeugt den CLI-Parser für die Smart-Zone-Nachmessung."""
    ap = argparse.ArgumentParser(
        description="Reproduzierbare Smart-Zone-Nachmessung aus Session-Transcripts und Staffel-Dateien."
    )
    ap.add_argument(
        "--projekte",
        type=Path,
        action="append",
        help="Projekt-Transcript-Wurzel (wiederholbar, Standard: ~/.claude/projects)",
    )
    ap.add_argument(
        "--staffel",
        type=Path,
        action="append",
        help="Staffel-Ordner mit st-*.json (wiederholbar, Standard: ~/.claude/data/staffel)",
    )
    ap.add_argument(
        "--smart-zone",
        type=Path,
        help="Pfad zu smart-zone.json (Standard: ~/.claude/smart-zone.json)",
    )
    ap.add_argument(
        "--ab",
        dest="ab_datum",
        help="Startdatum JJJJ-MM-TT inklusiv (Standard: Umstellungsdatum oder Stichtag minus 14 Tage)",
    )
    ap.add_argument(
        "--stichtag",
        help="Enddatum JJJJ-MM-TT inklusiv (Standard: heutiges UTC-Datum)",
    )
    ap.add_argument(
        "--umstellung",
        help="Umstellungsdatum JJJJ-MM-TT (überschreibt _aenderung aus smart-zone.json)",
    )
    ap.add_argument(
        "--modell",
        default="opus",
        help="Ziel-Modellfamilie (Standard: opus)",
    )
    ap.add_argument(
        "--min-runden",
        type=int,
        default=5,
        help="Mindestanzahl Assistenten-Runden je Session (Standard: 5)",
    )
    ap.add_argument(
        "--projekt-filter",
        help="Optionales Regex-Muster für Projekt-Ordnernamen",
    )
    ap.add_argument(
        "--quellen-hinweis",
        help="Freitext-Hinweis zu den verwendeten Datenquellen im Markdown-Kopf",
    )
    ap.add_argument(
        "--ausgabe",
        type=Path,
        help="Zielpfad für die Markdown- oder JSON-Ausgabe (sonst stdout)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        dest="als_json",
        help="Ausgabe als JSON statt Markdown",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Ausführliche Debug-Protokollierung auf stderr",
    )
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt."""
    ap = baue_parser()
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    std_projekte, std_staffel, std_sz = _standard_pfade()
    projekt_wurzeln = args.projekte if args.projekte else [std_projekte]
    staffel_ordner = args.staffel if args.staffel else ([std_staffel] if std_staffel.is_dir() else [])
    smart_zone_pfad = args.smart_zone if args.smart_zone else (std_sz if std_sz.is_file() else None)

    stichtag = args.stichtag or datetime.now(UTC).date().isoformat()
    umstellung = args.umstellung or lese_umstellung_datum(smart_zone_pfad)
    if args.ab_datum:
        ab_datum = args.ab_datum
    elif umstellung:
        ab_datum = umstellung
    else:
        ab_datum = (date.fromisoformat(stichtag) - timedelta(days=MINDEST_FRIST_TAGE)).isoformat()

    ergebnis = auswerten(
        projekt_wurzeln=projekt_wurzeln,
        staffel_ordner=staffel_ordner,
        ab_datum=ab_datum,
        stichtag=stichtag,
        umstellung_datum=umstellung,
        smart_zone_pfad=smart_zone_pfad,
        modell_filter=args.modell,
        min_runden=args.min_runden,
        projekt_filter=args.projekt_filter,
    )

    text = (
        json.dumps(als_dict(ergebnis), ensure_ascii=False, indent=2) + "\n"
        if args.als_json
        else formatiere_markdown(ergebnis, quellen_hinweis=args.quellen_hinweis)
    )

    if args.ausgabe:
        args.ausgabe.parent.mkdir(parents=True, exist_ok=True)
        args.ausgabe.write_text(text, encoding="utf-8")
        log.info("Ausgabe geschrieben: %s", args.ausgabe)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
