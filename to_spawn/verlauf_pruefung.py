"""Was hat seit der letzten weitergegebenen Frage kein Mensch geprüft? (#372, PR 374 Runde 9)

Eine Fenster-Frage ist Klasse 2, wenn sie selbst ein Klasse-2-Wort trägt
(:func:`befugnis.ist_klasse_2`, breit: Wortteile zählen) oder die Session seit der Stelle, die
der Leiter zuletzt weitergegeben hat, eine Klasse-2-HANDLUNG begangen oder angekündigt hat
(:func:`befugnis.handlung_klasse_2`) ODER die breite Erkennung von ``cb5e5f5`` anschlägt
(:func:`befugnis.ist_klasse_2_am_wortanfang` über Text und Werkzeug-Eingaben außer Datei-Inhalten;
Variante U, Sicherheit vor Quote). Quelle ist das Transkript der Session, nicht der
Fenster-Text: wer etwas ins Fenster getippt hat (Mensch, Aufpasser, Aufseher, Entscheider),
spielt keine Rolle — Eingaben sind nie eine Grenze und werden nicht durchsucht.

Durchsucht werden Einträge ``type=assistant`` ohne ``isApiErrorMessage`` (auch Unter-Agenten:
``isSidechain`` und ``<sessionId>/subagents/*.jsonl`` ab dem Zeitpunkt der Stelle); nie ``user``
(Eingaben, Anstupser, ``tool_result``) und nie ``thinking``. Je Block: ``text`` und
Task/Agent-``prompt`` als Text, bei allen übrigen Werkzeugen :data:`BEFEHL_FELDER` als Befehl, ``mcp__*``-Namen als Werkzeug und
``file_path`` von :data:`DATEI_WERKZEUGE` und Read als Pfad (Datei-Inhalte nie). Die Muster
selbst kennt nur :mod:`befugnis`.
Die Stelle (``<sessionId>:<uuid>``) gehört dem Leiter; unbekannte uuid ⇒ ab Dateianfang. Fremde
sessionId (Ablösung, ``/clear``) ⇒ neue Datei ab Anfang UND Rest der alten Datei ab der Stelle;
fehlt die alte Datei ⇒ Klasse 2. Keine Stelle ⇒ zusätzlich die übrigen Session-Dateien des
Ordners, die kurz vor dem Start oder danach geschrieben wurden (T7d, :func:`_vorgaengerinnen`). Eine kaputte Zeile mitten im Transkript nach der Stelle ⇒ Klasse 2.

Rückfall, wenn das Transkript nicht lesbar ist (keine Gesprächs-ID, Datei fehlt, keine Zeile
lesbar, letzte Zeile halb geschrieben): Fenster mit Verlauf (``capture-pane -J -S -2000``),
jede Zeile mit Klasse-2-Wort (:func:`befugnis.ist_klasse_2`, breit) zählt, ohne Grenze. Im
Zweifel geht die Frage zum Menschen.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from to_spawn import aufpasser, befugnis, waechter_lauf

if TYPE_CHECKING:
    from to_spawn.aufseher_stand import Quellen

log = logging.getLogger("to_spawn.verlauf_pruefung")

#: ``bis`` im Rückfall: keine Stelle im Transkript, der nächste Lauf prüft wieder alles.
RUECKFALL = "fenster"
#: So weit liest der Rückfall zurück (tmux ``history-limit`` steht auf dem Bau-Server höher).
VERLAUF_ZEILEN = 2000
#: Grund, wenn weder Transkript noch Verlauf lesbar sind — trägt selbst ein Klasse-2-Wort.
UNGEPRUEFT = "Verlauf nicht lesbar — Live-Bezug ungeprüft"
#: Grund für eine unlesbare Zeile mitten im Transkript (nicht: halbe letzte Zeile = Schreib-Verzug).
KAPUTT = "Transkript-Zeile kaputt — Live-Bezug ungeprüft"
#: Werkzeuge, deren Eingabe Datei-Inhalt ist: nur ``file_path`` zählt (Dauer-Fehlalarm, PR 374 R8).
DATEI_WERKZEUGE = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
#: Werkzeuge, deren ``prompt`` ein Auftrag an einen Unter-Agenten ist (zählt wie eigener Text).
AUFTRAG_WERKZEUGE = frozenset({"Task", "Agent"})
#: Felder, die bei jedem anderen Werkzeug ein Befehl sind (Bash/PowerShell ``command``,
#: ``mcp__*``-Fernaufrufe ``command``/``cmd``/``script``, Skill ``args``) — Windows-PC nicht blind.
BEFEHL_FELDER = ("command", "cmd", "script", "args")
#: So lange vor dem Start der Session darf eine Vorgängerin ohne Stelle zuletzt geschrieben haben.
#: ponytail: festes Fenster statt Ticket-Start, Ticket-Start übergeben wenn Fehlalarm stört.
VORGAENGER_FENSTER_S = 3600
#: Platzhalter für eine kaputte Zeile in der Eintrags-Liste (Position zählt, siehe :func:`_lies`).
_KAPUTTE_ZEILE: dict[str, Any] = {"type": "kaputt"}


@dataclass(frozen=True)
class Pruefung:
    """``grund``: erste Klasse-2-Zeile seit der Stelle (``None`` = keine); ``bis``: neue Stelle."""

    grund: str | None
    bis: str


def pruefen(ziel: str, q: Quellen, stelle: str | None) -> Pruefung:
    """Klasse-2-Grund der Session im Fenster ``ziel`` seit ``stelle`` (siehe Modul-Doc)."""
    gefunden = _transkript(ziel, q)
    eintraege = _lies(gefunden[1]) if gefunden else None
    if gefunden is None or not eintraege:
        return _rueckfall(ziel, q)
    sid, datei = gefunden
    eigene = [e for e in eintraege if _eigene_antwort(e)]
    bis = f"{sid}:{eigene[-1].get('uuid') or ''}" if eigene else f"{sid}:"
    sid_alt, trenner, uuid_alt = (stelle or "").partition(":")
    grund = None
    if trenner and sid_alt != sid:  # Ablösung oder /clear: Rest der Vorgängerin prüfen
        grund = _alte_session(datei.parent, sid_alt, uuid_alt)
    elif not trenner:  # keine Stelle: eine abgelöste Vorgängerin hat vielleicht nie gefragt (T7d)
        grund = _vorgaengerinnen(datei, eintraege)
    if grund is None:
        ab = uuid_alt if sid_alt == sid else ""
        grund = _session_grund(datei.parent, sid, eintraege, ab)
    return Pruefung(grund, bis)


def _alte_session(ordner: Path, sid: str, uuid_ab: str) -> str | None:
    """Grund in der abgelösten Session ``sid`` ab ``uuid_ab``; fehlt ihre Datei: :data:`UNGEPRUEFT`."""
    if not re.fullmatch(r"[0-9A-Za-z-]+", sid):  # Stelle ist kein Pfad
        return UNGEPRUEFT
    eintraege = _lies(ordner / f"{sid}.jsonl")
    if eintraege is None:
        return UNGEPRUEFT
    return _session_grund(ordner, sid, eintraege, uuid_ab)


def _vorgaengerinnen(datei: Path, eintraege: list[dict[str, Any]]) -> str | None:
    """Grund in den übrigen Session-Dateien des Ordners, die seit
    :data:`VORGAENGER_FENSTER_S` vor dem Start dieser Session geschrieben wurden (ganz, samt
    Unter-Agenten); eine unlesbare ist :data:`UNGEPRUEFT`."""
    start = next((z for e in eintraege if (z := _zeit(e)) is not None), None)
    try:
        ab = (start.timestamp() if start else datei.stat().st_mtime) - VORGAENGER_FENSTER_S
        andere = [(p, p.stat().st_mtime) for p in datei.parent.glob("*.jsonl") if p != datei]
    except OSError:
        return UNGEPRUEFT
    for p, mtime in sorted(andere, key=lambda x: -x[1]):
        if mtime < ab:
            continue
        unter = _lies(p)
        if unter is None:
            return UNGEPRUEFT
        if grund := _session_grund(datei.parent, p.stem, unter, ""):
            return grund
    return None


def _session_grund(ordner: Path, sid: str, eintraege: list[dict[str, Any]], uuid_ab: str) -> str | None:
    """Erster Grund nach ``uuid_ab`` (leer/unbekannt = ab Anfang), Unter-Agenten der Session
    ab dem Zeitpunkt dieser Stelle; eine unlesbare Unter-Agent-Datei ist :data:`UNGEPRUEFT`."""
    start = next(
        (i + 1 for i, e in enumerate(eintraege) if uuid_ab and e.get("uuid") == uuid_ab),
        0,
    )
    for e in eintraege[start:]:
        if grund := _grund(e):
            return grund
    ab = _zeit(eintraege[start - 1]) if start else None
    for datei in sorted((ordner / sid / "subagents").glob("*.jsonl")):
        unter = _lies(datei)
        if unter is None:
            return UNGEPRUEFT
        for e in unter:
            zeit = _zeit(e)
            if (ab is None or zeit is None or zeit >= ab) and (grund := _grund(e)):
                return grund
    return None


def _grund(e: dict[str, Any]) -> str | None:
    """Klasse-2-Handlung eines Eintrags (siehe Modul-Doc) oder ``None``."""
    if e is _KAPUTTE_ZEILE:
        return KAPUTT
    if e.get("type") != "assistant" or e.get("isApiErrorMessage"):
        return None
    inhalt = (e.get("message") or {}).get("content")
    if isinstance(inhalt, str):
        inhalt = [{"type": "text", "text": inhalt}]
    texte: list[str] = []
    befehle: list[str] = []
    werkzeuge: list[str] = []
    pfade: list[str] = []
    for b in inhalt if isinstance(inhalt, list) else ():
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            texte.append(str(b.get("text") or ""))
            continue
        if b.get("type") != "tool_use":
            continue
        name = str(b.get("name") or "")
        eingabe = b.get("input") if isinstance(b.get("input"), dict) else {}
        if name.startswith("mcp__"):
            werkzeuge.append(name)
        if name in AUFTRAG_WERKZEUGE:
            texte.append(str(eingabe.get("prompt") or ""))
        elif name in DATEI_WERKZEUGE or name == "Read":
            pfade.append(str(eingabe.get("file_path") or eingabe.get("notebook_path") or ""))
        else:
            befehle.extend(str(eingabe[f]) for f in BEFEHL_FELDER if eingabe.get(f))
            if name == "Skill":  # „/deploy live“: Name + args sind auch Text
                texte.append(f"{eingabe.get('skill') or ''} {eingabe.get('args') or ''}")
    # Variante U (#372): breite Erkennung von cb5e5f5 ODER Handlungs-Muster. Sicherheit vor Quote:
    # die engen Muster allein waren in zwei Review-Runden nie dicht (neue Formulierungen rutschten
    # durch); die Quote (~88 % Fehlalarm) löst eine Erlaubt-Liste im Folge-Issue, nicht ein Weglassen.
    breit = [
        b
        for b in (inhalt if isinstance(inhalt, list) else ())
        if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in DATEI_WERKZEUGE)
    ]
    return befugnis.handlung_klasse_2(texte=texte, befehle=befehle, werkzeuge=werkzeuge, pfade=pfade) or _erste_zeile(
        waechter_lauf._texte({"message": {"content": breit}}, werkzeug=True),
        befugnis.ist_klasse_2_am_wortanfang,
    )


def _zeit(e: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(e["timestamp"]))
    except (KeyError, ValueError):
        return None


def _transkript(ziel: str, q: Quellen) -> tuple[str, Path] | None:
    """(sessionId, Transkript-Datei) der Session im Pane ``ziel``; ``None`` = nicht zu finden."""
    raus = q.tmux(["display-message", "-p", "-t", ziel, "#{pane_pid} #{window_id} #{pane_id}"])
    teile = (raus or "").split()
    if len(teile) != 3 or not teile[0].isdigit():
        return None
    try:
        info = aufpasser.session_finden(int(teile[0]), teile[1], teile[2])
    except (OSError, ValueError) as fehler:
        log.warning("Session in %s nicht lesbar: %s", ziel, fehler)
        return None
    if info is None or info.cwd is None:
        return None
    return info.session_id, waechter_lauf.transkript_ordner(info.cwd) / f"{info.session_id}.jsonl"


def _lies(datei: Path) -> list[dict[str, Any]] | None:
    """Einträge der JSONL; eine kaputte Zeile mitten drin steht als :data:`_KAPUTTE_ZEILE` an ihrer
    Stelle. ``None``: Datei fehlt oder die letzte Zeile ist halb geschrieben (Schreib-Verzug:
    gerade ihr Text könnte fehlen)."""
    try:
        roh = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    eintraege: list[dict[str, Any]] = []
    zeilen = [z for z in roh.splitlines() if z.strip()]
    for nr, zeile in enumerate(zeilen):
        try:
            e = json.loads(zeile)
        except json.JSONDecodeError:
            if nr == len(zeilen) - 1:
                return None
            eintraege.append(_KAPUTTE_ZEILE)
            continue
        if isinstance(e, dict):
            eintraege.append(e)
    return eintraege


def _eigene_antwort(e: dict[str, Any]) -> bool:
    """Eigener Eintrag der Haupt-Session: nur er setzt die Stelle ``bis``."""
    return e.get("type") == "assistant" and not e.get("isSidechain") and not e.get("isApiErrorMessage")


def _erste_zeile(text: str, finde: Callable[..., bool] = befugnis.ist_klasse_2) -> str | None:
    """Erste Zeile, in der ``finde`` anschlägt (Rückfall: breite Liste; Verlauf: am Wortanfang);
    steht das Wort über Zeilen verteilt, der ganze Text."""
    if not finde(text):
        return None
    zeile = next((z for z in text.splitlines() if finde(z)), text)
    return " ".join(zeile.split())


def _rueckfall(ziel: str, q: Quellen) -> Pruefung:
    log.warning("Transkript von %s nicht lesbar — prüfe den ganzen Fenster-Verlauf", ziel)
    text = q.tmux(["capture-pane", "-p", "-J", "-S", f"-{VERLAUF_ZEILEN}", "-t", ziel])
    if text is None:
        return Pruefung(UNGEPRUEFT, RUECKFALL)
    return Pruefung(_erste_zeile(aufpasser.ohne_statuszeile(text)), RUECKFALL)


assert befugnis.ist_klasse_2(UNGEPRUEFT), "UNGEPRUEFT muss Klasse 2 tragen"
assert befugnis.ist_klasse_2(KAPUTT), "KAPUTT muss Klasse 2 tragen"
