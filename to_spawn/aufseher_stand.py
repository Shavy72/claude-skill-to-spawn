"""Aufseher-Stand (#430): je Ticket der Spec genau eine Kurz-Zeile + Stand-Datei je Tick.

Der Aufseher braucht je Tick einen Blick auf alle Tickets, ohne Pane-Text in seinen
Kontext zu kippen (E19). Dieses Modul liest alle Quellen, verdichtet sie und hängt
je Aufruf genau eine Zeile an die Stand-Datei (E21) — sie ist das Gedächtnis des
Aufsehers, ein Nachfolger startet mit :func:`letzter_stand`.

Schnittstelle für Aufrufer: :func:`stand` (alles in einem Zug) und
:func:`letzter_stand`. Tests geben über :class:`Quellen` Fake-tmux und Fake-GitHub
hinein (E25); Bau-Log und Stand-Datei bleiben echt.

Kurz-Zeile (≤ 200 Zeichen)::

    #<N> offen|fertig gebaut|zu|verworfen|— · arbeitet|still <M> min|Rückfrage <M> min|kein Fenster
         · Kontext <112,5k|—> · Phase <…|—> · „<letzte Aussage, ≤ 70 Zeichen>“
         · <k|—> neue Kommentare

Quellen:

- Fenster ``bau <N>`` in der tmux-Sitzung ``spec-<S>``: Zustand aus dem Pane-Text
  mit den Markern des Aufpassers (:func:`aufpasser.rueckfrage`,
  :func:`aufpasser.arbeitet`). Still-Minuten: seit wann der Pane-Hash ohne
  Statuszeile (:func:`aufpasser.pane_hash`, #429) gleich ist; beim ersten Blick
  ``window_activity``. tmux zählt die tickende Uhr der Statuszeile als Aktivität,
  deshalb merkt sich die Stand-Datei Hash + „still seit“ je Ticket.
- Offen/zu und Kommentar-Zahl: Sub-Issues der Spec (:func:`capo.kinder`). Neue
  Kommentare = Zahl jetzt minus Zahl beim letzten Aufruf (Stand-Datei); erster
  Aufruf ⇒ ``—``.
- Kontext: Spitzen-Kontext aus dem Bau-Log (:func:`bau_log.zusammenfassung`);
  Ort und Format kommen aus :func:`bau_log.log_ort` und :func:`bau_log.kontext_text`
  (dieselben wie bei ``sessions_stand.token_text``).
- Phase: Typ der jüngsten Bau-Log-Zeile mit bekanntem Typ (:data:`PHASEN`);
  ``deploy_phase`` zeigt zusätzlich ``phase``/``status`` der Zeile.
- Letzte Aussage: Feld ``text`` der jüngsten Bau-Log-Zeile, die eins hat.
- Abgelehnte Komplett-Prüfungen (#496): Zahl aus :func:`komplett_lauf.ablehnungen`; nur ab 1
  hängt ``<k>× Komplett abgelehnt`` an die Kurz-Zeile.

Fehlt eine Quelle für ein Ticket, steht ``—`` und es gibt eine Warnung; fehlt gh
ganz oder liefert die Sub-Issue-Liste nichts (Fehler oder leer), bricht :func:`lauf`
mit Exit ≠ 0 ab und schreibt keine Zeile in die Stand-Datei.

Fenster-Zustand ``—`` vs. ``kein Fenster``: Antwortet ``tmux list-windows`` für die
Sitzung ``spec-<S>`` nicht (tmux fehlt, Server aus oder Sitzung fehlt — tmux meldet
alle drei nur als Exit ≠ 0), ist über Fenster nichts bekannt: jede Zeile zeigt ``—``,
die Kopfzeile ``tmux nicht lesbar``. ``kein Fenster`` heißt dagegen: Sitzung lesbar,
aber kein Fenster ``bau <N>`` darin (bzw. dessen Pane nicht lesbar).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from to_spawn import (
    aufpasser,
    auswahl,
    bau_log,
    befugnis,
    briefkasten,
    capo,
    gh,
    komplett_lauf,
    pc_fenster,
    ticket_zustand,
    tmux_aufruf,
    verlauf_pruefung,
    wiederanlauf,
)
from to_spawn import prozessbaum

log = logging.getLogger("to_spawn.aufseher_stand")

#: Ordner der Stand-Dateien; ``TO_SPAWN_AUFSEHER_STAND`` überschreibt ihn.
STAND_ENV = "TO_SPAWN_AUFSEHER_STAND"
ZEILE_MAX = 200
AUSSAGE_MAX = 70
STRICH = "—"
TMUX_ZEIT_S = 15

#: Bau-Log-Typ → Phase in der Kurz-Zeile. Andere Typen (Aufseher-eigene wie
#: ``waechter_pause``) zählen nicht als Phase des Tickets.
PHASEN = {
    "auftrag": "Auftrag",
    "session_start": "Start",
    "entscheidung": "Entscheidung",
    "blockiert": "blockiert",
    "deploy_phase": "Deploy",
    "handoff": "Handoff",
    "subagent_ende": "Subagent",
    "session_ende": "Session-Ende",
    "staffel_limit": "Staffel-Limit",
    "vorfall": "Vorfall",
    "zusammenfassung": "Zusammenfassung",
    "ruecknahme": "Rücknahme",
}

ARBEITET = "arbeitet"
WARTET = "wartet"
BLOCKIERT = "blockiert"
FERTIG = "fertig"
STILL = "still"
RUECKFRAGE = "Rückfrage"
KEIN_FENSTER = "kein Fenster"
#: Fenster-Zustand, wenn tmux bzw. die Sitzung ``spec-<S>`` nicht lesbar ist.
FENSTER_UNBEKANNT = STRICH
TMUX_HINWEIS = "tmux nicht lesbar"

#: Vier Grund-Zustände einer Bau-Session für Stand-Abfrage und Beleg (#289).
Zustand = Literal["arbeitet", "wartet", "blockiert", "fertig"]
ZUSTAENDE: tuple[Zustand, ...] = (ARBEITET, WARTET, BLOCKIERT, FERTIG)
#: Maximale Länge der letzten Zeile im Beleg (voller Wortlaut inkl. Befehl bleibt sichtbar).
BELEG_ZEILE_MAX = 200

#: Sätze/Signale, mit denen eine Bau-Session meldet, dass sie blockiert ist (#289).
_BLOCKIERT_MUSTER = re.compile(
    r"(?:"
    r"\bblockiert\b"
    r"|darf\s+ich\s+nicht\s+selbst\s+starten"
    r"|braucht\s+die\s+Nachricht\s+von\s+außen"
    r"|Bitte\s+schick\s+hier\s+diese\s+Nachricht"
    r"|braucht\s+den\s+(?:Nutzer|Menschen)"
    r")",
    re.IGNORECASE,
)
#: Verneinungen, die trotz des Wortes „blockiert“ keinen Halt bedeuten.
_NICHT_BLOCKIERT_MUSTER = re.compile(
    r"\b(?:nicht(?:\s+mehr)?|kein\w*)\s+blockiert\b",
    re.IGNORECASE,
)
_ZEIT_MUSTER = r"(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?|\d{2}:\d{2}(?::\d{2})?)"
_BELEG_ZEILE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?Beleg\s+#(?P<ticket>\d+)\s*[·|]\s*"
    rf"(?P<zeit>{_ZEIT_MUSTER})\s*[·|]\s*"
    rf"(?P<zustand>{'|'.join(ZUSTAENDE)})\s*[·|]\s*"
    r'(?:„(?P<zeile_de>[^\n]+)“|"(?P<zeile_en>[^\n]+)")\s*$'
)
_NICHT_ARBEITET_MUSTER = re.compile(
    r"\b(?:nicht\s+(?:mehr\s+)?(?:arbeitet|arbeiten)|(?:arbeitet|arbeiten)\s+nicht)\b",
    re.IGNORECASE,
)
_BEHAUPTET_ARBEITET_MUSTER = re.compile(r"\b(?:arbeitet|arbeiten)\b", re.IGNORECASE)
_TICKET_NUMMER_RE = re.compile(r"#(\d+)\b")


@dataclass(frozen=True)
class StandBeleg:
    """Maschinell prüfbarer Beleg für den Stand einer Bau-Session (#289)."""

    ticket: int
    zeit: str
    zustand: Zustand
    letzte_zeile: str


class GhFehlt(RuntimeError):
    """``gh`` ist auf diesem Rechner nicht aufrufbar."""


@dataclass
class Quellen:
    """Außenquellen: GitHub (Sub-Issues) und tmux. Tests geben Fakes hinein."""

    repo: Path
    #: Sub-Issues der Spec (Dicts mit ``number``/``state``/``comments``), ``None`` = Fehler.
    kinder: Callable[[int], list[dict[str, Any]] | None]
    #: ``tmux <args>`` → stdout, ``None`` = tmux fehlt oder Exit ≠ 0.
    tmux: Callable[[list[str]], str | None]
    jetzt: Callable[[], float] = time.time
    #: Am PC (kein tmux, #501): Ticket → Sekunden seit dem letzten Transkript-Eintrag
    #: (``None`` = keine Session). Gesetzt = PC-Weg, tmux wird nicht gefragt.
    pc_still: Callable[[int], float | None] | None = None
    #: Wiederanlauf-Art eines fertig gebauten Tickets (#282 P3): ``(ticket, arbeitsbaum)`` →
    #: :func:`wiederanlauf.feststellen`. :func:`echte_quellen` setzt es immer; ``None`` nur in
    #: Test-Fakes ohne diese Frage (die Leiter entscheidet dann allein nach der Stufe).
    anlauf: Callable[[int, Path], wiederanlauf.Wiederanlauf | None] | None = None


def _tmux_echt(args: list[str]) -> str | None:
    try:
        fertig = subprocess.run(
            [*capo._tmux_befehl(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TMUX_ZEIT_S,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("tmux nicht nutzbar: %s", fehler)
        return None
    return fertig.stdout if fertig.returncode == 0 else None


def echte_quellen(repo: Path, gh_repo: str) -> Quellen:
    """Quellen des Rechners; :class:`GhFehlt`, wenn ``gh`` nicht da ist."""
    if gh.gh_befehl() is None:
        raise GhFehlt("gh nicht gefunden (PATH bzw. TO_SPAWN_GH_STUB)")
    pc_still = None
    if pc_fenster.am_pc():

        def pc_still(n: int) -> float | None:
            return pc_fenster.still_s(repo, n, time.time())

    return Quellen(
        repo=repo,
        kinder=lambda s: capo.kinder(gh_repo, s),
        tmux=_tmux_echt,
        pc_still=pc_still,
        anlauf=wiederanlauf.feststellen,
    )


#: Am PC gilt eine Session als arbeitend, wenn ihr Transkript jünger ist als das.
PC_ARBEITET_S = 120.0


def _pc_lage(still_s: float | None) -> tuple[str, int | None]:
    """Fenster-Zustand am PC aus der Transkript-Zeit (Rückfragen sind dort nicht sichtbar)."""
    if still_s is None:
        return KEIN_FENSTER, None
    if still_s < PC_ARBEITET_S:
        return ARBEITET, None
    return STILL, int(still_s // 60)


@dataclass(frozen=True)
class TicketLage:
    """Alles, was die Kurz-Zeile eines Tickets braucht (``None`` = unbekannt)."""

    nummer: int
    stufe: ticket_zustand.Stufe | None  # None = GitHub-Zustand unbekannt
    fenster: str  # ARBEITET | STILL | RUECKFRAGE | KEIN_FENSTER | FENSTER_UNBEKANNT
    still_min: int | None = None
    kontext: str = STRICH
    phase: str = STRICH
    aussage: str = STRICH
    neue_kommentare: int | None = None
    abgelehnt: int = 0  # #496: Komplett-Prüfungen, die die Bau-Ticket-Sperre abgelehnt hat
    letzte_zeile: str = STRICH  # #289: letzte aussagekräftige Zeile im Fenster (nur für Beleg)
    blockiert_text: str = ""  # #289: erkannter Wortlaut bei Zustand ``blockiert``

    @property
    def endgueltig_zu(self) -> bool:
        """Wie ``ticket_zustand.endgueltig_zu`` (zu oder verworfen); unbekannt gilt als nicht zu."""
        return self.stufe in ticket_zustand.ENDGUELTIG_ZU_STUFEN


def sitzungs_zustand(lage: TicketLage) -> Zustand:
    """Vier Grund-Zustände (``arbeitet``, ``wartet``, ``blockiert``, ``fertig``) aus :class:`TicketLage` (#289).

    ``fertig`` gilt nur für endgültig geschlossene Tickets (``staging:fertig`` ist ``wartet``);
    ``blockiert`` gilt bei erkannter Blockade-Meldung der Session; bloßes Vorhandensein eines
    Fensters zählt nie als ``arbeitet``.
    """
    if lage.endgueltig_zu:
        return FERTIG
    if lage.fenster == BLOCKIERT:
        return BLOCKIERT
    if lage.fenster == ARBEITET:
        return ARBEITET
    return WARTET


def _kuerzen(text: str, laenge: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= laenge else text[: laenge - 1].rstrip() + "…"


def kurz_zeile(lage: TicketLage) -> str:
    """Eine Zeile ≤ :data:`ZEILE_MAX` Zeichen, nie roher Pane-Text (außer erkanntem Blockade-Wortlaut)."""
    zustand = STRICH if lage.stufe is None else ticket_zustand.ANZEIGE[lage.stufe]
    fenster = lage.fenster
    if fenster in (STILL, RUECKFRAGE, BLOCKIERT, WARTET) and lage.still_min is not None:
        fenster = f"{fenster} {lage.still_min} min"
    aussage_text = lage.aussage
    if aussage_text == STRICH and lage.fenster == BLOCKIERT and lage.blockiert_text:
        aussage_text = lage.blockiert_text
    aussage = STRICH if aussage_text == STRICH else f"„{_kuerzen(aussage_text, AUSSAGE_MAX)}“"
    kommentare = STRICH if lage.neue_kommentare is None else str(lage.neue_kommentare)
    teile = [
        f"#{lage.nummer} {zustand}",
        fenster,
        f"Kontext {lage.kontext}",
        f"Phase {_kuerzen(lage.phase, 30)}",
        aussage,
        f"{kommentare} neue Kommentare",
    ]
    if lage.abgelehnt:
        teile.append(f"{lage.abgelehnt}× Komplett abgelehnt")
    return _kuerzen(" · ".join(teile), ZEILE_MAX)


def fingerabdruck(lagen: list[TicketLage]) -> str:
    """Hash der Kurz-Zeilen ohne Minuten und Kommentar-Zähler (die laufen immer weiter)."""
    roh = "\n".join(kurz_zeile(replace(x, still_min=None, neue_kommentare=None)) for x in lagen)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


# --- Stand-Datei ------------------------------------------------------------------


def stand_ordner(ordner: Path | None = None) -> Path:
    if ordner is not None:
        return ordner
    roh = os.environ.get(STAND_ENV, "").strip()
    if roh:
        return Path(roh).expanduser()
    return Path.home() / ".local" / "state" / "to-spawn" / "aufseher"


def stand_datei(spec: int, ordner: Path | None = None) -> Path:
    return stand_ordner(ordner) / f"stand-{spec}.jsonl"


def _stand_zeilen(spec: int, ordner: Path | None) -> list[dict[str, Any]]:
    datei = stand_datei(spec, ordner)
    if not datei.is_file():
        return []
    zeilen = []
    for roh in datei.read_text(encoding="utf-8").splitlines():
        try:
            wert = json.loads(roh)
        except ValueError:
            log.warning("Stand-Datei %s: unlesbare Zeile übersprungen", datei)
            continue
        if isinstance(wert, dict):
            zeilen.append(wert)
    return zeilen


def letzter_stand(spec: int, ordner: Path | None = None) -> dict[str, Any] | None:
    """Jüngste Nicht-noop-Zeile der Stand-Datei — Startstand eines Nachfolge-Aufsehers."""
    for zeile in reversed(_stand_zeilen(spec, ordner)):
        if not zeile.get("noop"):
            return zeile
    return None


# --- Sammeln ----------------------------------------------------------------------


@dataclass
class _Vorher:
    """Was die letzte Stand-Zeile für diesen Aufruf hergibt."""

    fingerabdruck: str | None = None
    kommentare: dict[str, int] = field(default_factory=dict)
    fenster: dict[str, dict[str, Any]] = field(default_factory=dict)


def _vorher(spec: int, ordner: Path | None) -> _Vorher:
    zeilen = _stand_zeilen(spec, ordner)
    if not zeilen:
        return _Vorher()
    letzte = zeilen[-1]
    return _Vorher(
        fingerabdruck=letzte.get("fingerabdruck"),
        kommentare={str(k): int(v) for k, v in (letzte.get("kommentare") or {}).items()},
        fenster=dict(letzte.get("fenster") or {}),
    )


def _fenster_liste(
    spec: int,
    tmux: Callable[[list[str]], str | None],
    repo: Path | None = None,
) -> dict[int, tuple[str, float]] | None:
    """Ticket → (tmux-Ziel per ``window_id``, window_activity); ``None`` = tmux/Sitzung nicht lesbar.

    Prüft zuerst eine bestehende eigene Alt-Session ``spec-<S>`` (Übergangszeit #457) und
    sonst ``spec-<kurz>-<S>``. Liefert tmux ``#{session_path}`` (4. Feld) eines anderen Repos,
    wird die fremde Session übersprungen — kein Repo sieht fremde Fenster.
    """
    kandidaten = [tmux_aufruf.sitzung_alt(spec)]
    if repo is not None:
        neu = tmux_aufruf.sitzung_name(repo, spec)
        if neu not in kandidaten:
            kandidaten.append(neu)
    for sitzung in kandidaten:
        raus = tmux(
            [
                "list-windows",
                "-t",
                f"={sitzung}",
                "-F",
                "#{window_id}\t#{window_name}\t#{window_activity}\t#{session_path}",
            ]
        )
        if raus is None:
            continue
        fremd = False
        liste: dict[int, tuple[str, float]] = {}
        for zeile in raus.splitlines():
            teile = zeile.split("\t")
            if len(teile) < 3:
                continue
            if (
                repo is not None
                and len(teile) >= 4
                and teile[3].strip()
                and tmux_aufruf.ordner_ist_fremd(Path(teile[3].strip()), repo)
            ):
                fremd = True
                break
            if not teile[1].startswith("bau "):
                continue
            try:
                liste[int(teile[1][4:])] = (
                    f"={sitzung}:{teile[0]}",
                    float(teile[2] or 0),
                )
            except ValueError:
                continue
        if fremd:
            continue
        return liste
    log.warning("tmux-Sitzung für Spec #%s nicht lesbar — Fenster-Zustand „—“.", spec)
    return None


def bau_fenster(spec: int, repo: Path | None = None) -> dict[int, str] | None:
    """Ticket → tmux-Ziel des Fensters ``bau <N>`` der Spec-Sitzung; ``None`` = tmux/Sitzung nicht lesbar."""
    liste = _fenster_liste(spec, _tmux_echt, repo=repo)
    return None if liste is None else {n: ziel for n, (ziel, _aktiv) in liste.items()}


def letzte_pane_zeile(text: str) -> str:
    """Letzte nicht-leere Ausgabe-Zeile eines Panes (ohne Statuszeile, Eingabefeld und Farben, #289)."""
    sauber = aufpasser.ohne_farben(text)
    zeilen = [z.strip() for z in unbeaufsichtigt(sauber) if z.strip()]
    if not zeilen:
        zeilen = [z.strip() for z in aufpasser._zeilen_am_prompt(sauber) if z.strip()]
    return " ".join(zeilen[-1].split()) if zeilen else STRICH


def _ist_blockiert_zeile(text: str) -> bool:
    if briefkasten.JA_BEFEHL in text:
        return True
    bereinigt = _NICHT_BLOCKIERT_MUSTER.sub("", text)
    return bool(_BLOCKIERT_MUSTER.search(bereinigt))


def blockiert_im_text(text: str) -> str:
    """Erkannter Blockade-Wortlaut der Session am Ende des Pane-Texts (#289); ``""`` = nicht blockiert.

    Betrachtet wird die Ausgabe seit der letzten Eingabezeile (:func:`unbeaufsichtigt`), bei
    ausgebliebener Antwort die Ausgabe vor dem Eingabefeld. Verneinungen („nicht mehr blockiert“)
    zählen nicht.
    """
    sauber = aufpasser.ohne_farben(text)
    roh = unbeaufsichtigt(sauber)
    if not any(z.strip() for z in roh):
        roh = aufpasser._zeilen_am_prompt(sauber)
    absatz: list[str] = []
    for z in reversed(roh):
        if not z.strip():
            if absatz:
                break
            continue
        absatz.insert(0, z.strip())
    if absatz:
        zusammen = " ".join(" ".join(absatz).split())
        if _ist_blockiert_zeile(zusammen):
            return zusammen
    nicht_leer = [z.strip() for z in roh if z.strip()][-6:]
    for z in reversed(nicht_leer):
        if _ist_blockiert_zeile(z):
            return " ".join(z.split())
    gemeldet = aufpasser.gemeldete_blocker("\n".join(roh))
    if gemeldet:
        return f"WARTET: {gemeldet}"
    return ""


def _fenster_lage(
    n: int, ziel: str, aktiv: float, q: Quellen, vorher: _Vorher, jetzt: float
) -> tuple[str, int | None, dict[str, Any] | None, str, str]:
    """(Zustand, Still-Minuten, Merker für die Stand-Datei, letzte Zeile, Blockade-Wortlaut)."""
    text = q.tmux(["capture-pane", "-p", "-t", ziel])
    if text is None:
        log.warning("Pane von #%s (%s) nicht lesbar.", n, ziel)
        return KEIN_FENSTER, None, None, STRICH, ""
    h = aufpasser.pane_hash(text)
    alt = vorher.fenster.get(str(n)) or {}
    # tmux-Aktivität zählt auch die tickende Uhr; gleicher Hash = still seit dem alten Wert.
    seit = min(float(alt.get("seit", aktiv)), aktiv) if alt.get("hash") == h else aktiv
    merker = {"hash": h, "seit": seit}
    minuten = max(0, int((jetzt - seit) // 60))
    letzte = letzte_pane_zeile(text)
    if aufpasser.rueckfrage(text):
        return RUECKFRAGE, minuten, merker, letzte, ""
    if any(m in text for m in aufpasser.ARBEITS_MARKER) or bool(aufpasser.LIMIT_TEXT.search(text)):
        return ARBEITET, None, merker, letzte, ""
    if aufpasser.BLOCKER_WARTEN.search(text):
        return WARTET, minuten, merker, letzte, ""
    grund = blockiert_im_text(text)
    if grund:
        return BLOCKIERT, minuten, merker, letzte, grund
    return STILL, minuten, merker, letzte, ""


def _log_lage(n: int, repo: Path) -> tuple[str, str, str, int] | None:
    """(Kontext, Phase, letzte Aussage, Komplett-Ablehnungen) aus dem Bau-Log; ``—`` je fehlendem Teil.

    ``None`` = kein Bau-Log (Ticket noch nicht gestartet) — der Aufrufer warnt gesammelt.
    """
    try:
        ort = bau_log.log_ort_fuer(n, repo)
        if ort is None:
            return None
        zeilen = bau_log.lese(ort, n, hauptbaum=repo)
        spitze_k = bau_log.zusammenfassung(ort, n, hauptbaum=repo)["spitze_k"]
    except (OSError, ValueError, KeyError) as fehler:
        log.warning("Bau-Log #%s unlesbar: %s", n, fehler)
        return STRICH, STRICH, STRICH, 0
    kontext = bau_log.kontext_text(spitze_k)
    phase = STRICH
    for z in reversed(zeilen):
        typ = str(z.get("typ") or "")
        if typ in PHASEN:
            phase = PHASEN[typ]
            if typ == "deploy_phase":
                extra = "/".join(str(z[k]) for k in ("phase", "status") if z.get(k))
                phase = f"{phase} {extra}".strip()
            break
    aussage = next(
        (str(z["text"]) for z in reversed(zeilen) if str(z.get("text") or "").strip()),
        STRICH,
    )
    return kontext, phase, aussage, komplett_lauf.ablehnungen(zeilen)


def _stufe(kind: dict[str, Any]) -> ticket_zustand.Stufe | None:
    """Stufe aus ``ticket_zustand`` (#279); ``None``, wenn GitHub keinen Zustand geliefert hat."""
    if str(kind.get("state") or "").strip().lower() not in ("open", "closed"):
        return None
    return ticket_zustand.stufe(kind)


@dataclass
class _Ergebnis:
    lagen: list[TicketLage]
    kommentare: dict[str, int]
    fenster: dict[str, dict[str, Any]]


def sammeln(spec: int, q: Quellen, vorher: _Vorher) -> _Ergebnis:
    """Lage jedes Sub-Issues der Spec; :class:`RuntimeError`, wenn gh nichts liefert.

    „Nichts“ heißt Fehler (``None``) oder leere Liste — eine Spec ohne Sub-Issues hat
    keinen Stand, der Aufrufer schreibt dann keine Zeile in die Stand-Datei.
    """
    kinder = q.kinder(spec)
    if kinder is None:
        raise RuntimeError(f"Sub-Issues von Spec #{spec} nicht lesbar (gh)")
    if not kinder:
        raise RuntimeError(f"Spec #{spec} hat keine Sub-Issues — Spec-Nummer prüfen (gh)")
    auswahl_stand = auswahl.lesen(q.repo, spec)
    if auswahl_stand is not None:
        kinder = auswahl.filtere_issues(kinder, auswahl_stand)
    jetzt = q.jetzt()
    fenster = None if q.pc_still else _fenster_liste(spec, q.tmux, repo=q.repo)
    lagen: list[TicketLage] = []
    kommentare: dict[str, int] = {}
    merker: dict[str, dict[str, Any]] = {}
    ohne_log: list[str] = []
    for kind in sorted(kinder, key=lambda k: int(k.get("number") or 0)):
        try:
            n = int(kind["number"])
        except (KeyError, TypeError, ValueError):
            log.warning("Sub-Issue ohne Nummer übersprungen: %r", kind)
            continue
        letzte = STRICH
        blockiert_text = ""
        if q.pc_still is not None:
            zustand, still_min = _pc_lage(q.pc_still(n))
        elif fenster is None:
            zustand, still_min = FENSTER_UNBEKANNT, None
        elif n in fenster:
            zustand, still_min, m, letzte, blockiert_text = _fenster_lage(
                n, *fenster[n], q, vorher, jetzt
            )
            if m:
                merker[str(n)] = m
        else:
            zustand, still_min = KEIN_FENSTER, None
        neue: int | None = None
        try:
            zahl = int(kind["comments"])
            kommentare[str(n)] = zahl
            if str(n) in vorher.kommentare:
                neue = max(0, zahl - vorher.kommentare[str(n)])
        except (KeyError, TypeError, ValueError):
            log.warning("Kommentar-Zahl von #%s fehlt.", n)
        log_lage = _log_lage(n, q.repo)
        if log_lage is None:
            ohne_log.append(f"#{n}")
            log_lage = (STRICH, STRICH, STRICH, 0)
        kontext, phase, aussage, abgelehnt = log_lage
        stufe = _stufe(kind)
        if stufe == "offen" and zustand == STILL and phase == "blockiert":
            zustand = BLOCKIERT
            if not blockiert_text and aussage != STRICH:
                blockiert_text = aussage
        lagen.append(
            TicketLage(
                n,
                stufe,
                zustand,
                still_min,
                kontext,
                phase,
                aussage,
                neue,
                abgelehnt,
                letzte,
                blockiert_text,
            )
        )
    if ohne_log:
        # Eine Zeile statt einer je Ticket: der Aufseher liest die Ausgabe in seinen Kontext.
        log.warning("Kein Bau-Log (Kontext/Phase/Aussage „—“): %s", ", ".join(ohne_log))
    return _Ergebnis(lagen, kommentare, merker)


#: Rückfrage-Dialog: so viele letzte Zeilen (Frage samt Befehl darüber) bilden die Frage.
FRAGE_ZEILEN = 8
#: Höchstens so viele Zeichen trägt eine Fenster-Frage im Briefkasten (:func:`frage_kurz`).
FRAGE_MAX = 600
#: Eingabezeile des Menschen im Verlauf (``> …``/``❯ …``) — nur ganz links, mit Text, nie eine
#: Auswahlzeile (``❯ 1. Yes``).
EINGABE_ZEILE = re.compile(r"^[>❯]\s+(?!\d+\.)\S")


def unbeaufsichtigt(text: str) -> list[str]:
    """Zeilen des Pane-Texts seit der letzten Eingabezeile, ohne Eingabefeld (#372).

    Nur für die Frage, die angezeigt wird (:func:`frage_im_text`) — die Klasse kommt aus dem
    Transkript (:mod:`verlauf_pruefung`), wer getippt hat, spielt dafür keine Rolle. Ohne
    erkennbare Eingabezeile alles. Als Eingabezeile zählt nur eine Zeile, die ganz links mit
    ``>``/``❯`` beginnt — Claude Code rückt Antwort-Text samt Markdown-Zitat ein.
    """
    zeilen = aufpasser.ohne_statuszeile(text).splitlines()
    linien = [i for i, z in enumerate(zeilen) if aufpasser.EINGABEFELD_LINIE.fullmatch(z)]
    if linien:
        zeilen = zeilen[: linien[-1]]  # Eingabefeld (``❯ …``) gehört nicht zur Ausgabe
    eingaben = [i for i, z in enumerate(zeilen) if EINGABE_ZEILE.match(z)]
    return zeilen[eingaben[-1] + 1 :] if eingaben else zeilen


def _grund_kurz(zeile: str, grenze: int) -> str:
    """Klasse-2-Zeile auf höchstens ``grenze`` Zeichen um das Klasse-2-Wort herum."""
    if len(zeile) <= grenze:
        return zeile
    w = zeile.split()
    a, b = 0, len(w)
    while b - a > 1 and befugnis.ist_klasse_2(" ".join(w[a + 1 : b])):
        a += 1
    while b - a > 1 and befugnis.ist_klasse_2(" ".join(w[a : b - 1])):
        b -= 1
    # Kern (kleinstes Wortfenster mit Klasse-2-Wort) abwechselnd um Nachbarwörter erweitern.
    while True:
        weiter = False
        if a > 0 and len(" ".join(w[a - 1 : b])) + 2 <= grenze:
            a, weiter = a - 1, True
        if b < len(w) and len(" ".join(w[a : b + 1])) + 2 <= grenze:
            b, weiter = b + 1, True
        if not weiter:
            break
    # ponytail: Kern allein über ``grenze`` bleibt ungekürzt, frage_kurz kann ihn dann anschneiden.
    return ("…" if a else "") + " ".join(w[a:b]) + ("…" if b < len(w) else "")


def frage_im_text(text: str) -> str:
    """Frage an den Menschen am Ende des Pane-Texts (#372), ``""`` = keine; ungekürzt.

    Betrachtet wird nur, was seit der letzten Eingabezeile kam (:func:`unbeaufsichtigt`).
    Rückfrage-Dialog (:func:`aufpasser.rueckfrage`): die letzten Zeilen samt Befehl
    darüber. Sonst der letzte Absatz, nur wenn er mit ``?`` endet — eine stille Session ohne
    solche Frage hängt (Stupser/Handoff wie bisher). Nur der Text der Frage; die Klasse
    bestimmt :func:`fenster_blick`.
    """
    zeilen = unbeaufsichtigt(text)
    if aufpasser.rueckfrage(text):
        teile = [z.strip() for z in zeilen if z.strip()][-FRAGE_ZEILEN:]
    else:
        absatz: list[str] = []
        for z in reversed(zeilen):
            if not z.strip():
                if absatz:
                    break
                continue
            absatz.insert(0, z.strip())
        teile = absatz if absatz and absatz[-1].endswith("?") else []
    return " ".join(" ".join(teile).split())


def mit_grund(frage: str, grund: str | None) -> str:
    """``frage`` mit der auslösenden Klasse-2-Zeile davor, wenn die Frage selbst keine trägt.

    Sonst schnitte :func:`frage_kurz` den Grund weg und der Briefkasten sähe Klasse 3.
    """
    if not frage or not grund or befugnis.ist_klasse_2(frage):
        return frage
    grund = _grund_kurz(grund, FRAGE_MAX // 2)
    rest = frage_kurz(frage, max(FRAGE_MAX - len(grund) - 3, FRAGE_MAX // 2))
    return " ".join(f"{grund} … {rest.lstrip('…')}".split())


def frage_kurz(frage: str, grenze: int = FRAGE_MAX) -> str:
    """``frage`` auf ``grenze`` Zeichen, vorn gekürzt (``…``): das Ende mit der Frage bleibt.

    Nur zum Anzeigen/Ablegen — die Klasse nie am gekürzten Text bestimmen (Review PR 374).
    """
    frage = " ".join(frage.split())
    return frage if len(frage) <= grenze else "…" + frage[len(frage) - grenze + 1 :]


def fenster_blick(ziel: str | None, q: Quellen, stelle: str | None) -> tuple[str, str]:
    """(Frage, bis) im Fenster ``ziel`` (#372); ohne Ziel/lesbares Pane/Frage ``("", "")``.

    Die Frage kommt aus dem sichtbaren Fenster (:func:`frage_im_text`), die Klasse aus dem
    Transkript seit ``stelle`` (:func:`verlauf_pruefung.pruefen`): trägt die Session dort ein
    Klasse-2-Wort, steht die Zeile vor der Frage (:func:`mit_grund`). ``bis`` ist die Stelle,
    die der Aufrufer merkt, sobald er die Frage weitergegeben hat.
    """
    if not ziel:
        return "", ""
    text = q.tmux(["capture-pane", "-p", "-t", ziel])
    frage = frage_im_text(text) if text else ""
    if not frage:
        return "", ""
    pruefung = verlauf_pruefung.pruefen(ziel, q, stelle)
    return mit_grund(frage, pruefung.grund), pruefung.bis


class TicketBlick(NamedTuple):
    """Ein Ticket aus Sicht des Aufseher-Stands (``None`` = unbekannt)."""

    lage: TicketLage | None  # ``None``: kein Sub-Issue der Spec
    ziel: str | None  # tmux-Ziel des Fensters ``bau <N>``


def ticket_lage(spec: int, ticket: int, q: Quellen, ordner: Path | None = None) -> TicketBlick:
    """Lage eines Tickets wie :func:`stand` sie sieht, plus tmux-Ziel — nur lesen.

    Die Stand-Datei bleibt unberührt (schreiben tut sie nur der Aufseher-Tick).
    :class:`RuntimeError` wie :func:`sammeln`, wenn gh nichts liefert. Nutzer:
    Eingriffs-Leiter (#432).
    """
    erg = sammeln(spec, q, _vorher(spec, ordner))
    lage = next((t for t in erg.lagen if t.nummer == ticket), None)
    fenster = {} if q.pc_still else (_fenster_liste(spec, q.tmux) or {})
    eintrag = fenster.get(ticket)  # (tmux-Ziel, window_activity); am PC kein Ziel
    return TicketBlick(lage, eintrag[0] if eintrag else None)


def _beleg_letzte_zeile(lage: TicketLage, zustand: Zustand) -> str:
    """Aussagekräftige letzte Zeile für :class:`StandBeleg` (nie leer und nie nur „—“)."""
    if zustand == BLOCKIERT:
        kandidat = lage.blockiert_text or (
            lage.letzte_zeile if lage.letzte_zeile != STRICH else ""
        ) or (lage.aussage if lage.aussage != STRICH else "") or "Session meldet blockiert"
    elif zustand == FERTIG:
        if lage.letzte_zeile != STRICH:
            kandidat = lage.letzte_zeile
        elif lage.aussage != STRICH:
            kandidat = lage.aussage
        else:
            kandidat = "Ticket verworfen" if lage.stufe == "verworfen" else "Ticket zu"
    elif lage.letzte_zeile != STRICH:
        kandidat = lage.letzte_zeile
    elif lage.aussage != STRICH:
        kandidat = lage.aussage
    elif lage.fenster == FENSTER_UNBEKANNT:
        kandidat = TMUX_HINWEIS
    else:
        kandidat = lage.fenster
    return _kuerzen(kandidat, BELEG_ZEILE_MAX)


def beleg_fuer(spec: int, ticket: int, q: Quellen, ordner: Path | None = None) -> StandBeleg:
    """Stand-Beleg (Zeit, Zustand, letzte Zeile) für ein Ticket — nur lesen (#289)."""
    erg = sammeln(spec, q, _vorher(spec, ordner))
    lage = next((t for t in erg.lagen if t.nummer == ticket), None)
    if lage is None:
        raise RuntimeError(f"#{ticket} ist kein Sub-Issue von Spec #{spec}")
    zustand = sitzungs_zustand(lage)
    zeit = datetime.fromtimestamp(q.jetzt(), tz=UTC).astimezone().strftime("%H:%M")
    return StandBeleg(
        ticket=ticket,
        zeit=zeit,
        zustand=zustand,
        letzte_zeile=_beleg_letzte_zeile(lage, zustand),
    )


def nach_anstoss_beleg(
    spec: int,
    ticket: int,
    q: Quellen,
    *,
    ordner: Path | None = None,
    warte_s: float = 0.0,
    schlafen: Callable[[float], None] = time.sleep,
) -> StandBeleg:
    """Prüft die Wirkung eines Anstoßes am Fenster/Stand nach kurzer Pause (#289)."""
    if warte_s > 0:
        schlafen(warte_s)
    return beleg_fuer(spec, ticket, q, ordner=ordner)


def beleg_zeile(beleg: StandBeleg) -> str:
    """Einzeiliger, maschinell prüfbarer Beleg: Zeit, Zustand und letzte Zeile (#289)."""
    return f"Beleg #{beleg.ticket} · {beleg.zeit} · {beleg.zustand} · „{beleg.letzte_zeile}“"


def parse_beleg_zeilen(text: str) -> list[StandBeleg]:
    """Alle syntaktisch vollständigen Beleg-Zeilen aus einer Aufseher-Meldung (#289)."""
    funde: list[StandBeleg] = []
    for zeile in text.splitlines():
        treffer = _BELEG_ZEILE_RE.match(zeile)
        if not treffer:
            continue
        letzte = (treffer.group("zeile_de") or treffer.group("zeile_en") or "").strip()
        if not letzte or letzte in (STRICH, "-"):
            continue
        zustand: Zustand = treffer.group("zustand")  # type: ignore[assignment]
        funde.append(
            StandBeleg(
                ticket=int(treffer.group("ticket")),
                zeit=treffer.group("zeit"),
                zustand=zustand,
                letzte_zeile=letzte,
            )
        )
    return funde


def _behauptet_arbeitet(zeile: str) -> bool:
    bereinigt = _NICHT_ARBEITET_MUSTER.sub("", zeile)
    return bool(_BEHAUPTET_ARBEITET_MUSTER.search(bereinigt))


def pruefe_meldung(text: str) -> str | None:
    """Prüft eine Aufseher-Meldung auf Beleg-Zeile und Widerspruchsfreiheit (#289).

    Rückgabe: ``None`` = gültig, sonst klarer Fehlertext. Einzige Prüfstelle für
    Aufseher-Takt und CLI.
    """
    if not text or not text.strip():
        return "Aufseher-Meldung ist leer — Beleg-Zeile fehlt (Zeit, letzte Zeile, Zustand)."
    belege = parse_beleg_zeilen(text)
    if not belege:
        return (
            "Aufseher-Meldung ohne gültige Beleg-Zeile "
            "(erwartet: Beleg #<N> · <Zeit> · <arbeitet|wartet|blockiert|fertig> · „<letzte Zeile>“)."
        )
    je_ticket: dict[int, StandBeleg] = {}
    for b in belege:
        if b.zustand == ARBEITET and blockiert_im_text(b.letzte_zeile):
            return (
                f"Widerspruch im Beleg #{b.ticket}: Zustand „arbeitet“, aber letzte Zeile "
                f"meldet blockiert („{b.letzte_zeile}“)."
            )
        je_ticket[b.ticket] = b
    alle_blockiert = all(b.zustand == BLOCKIERT for b in belege)
    for zeile in text.splitlines():
        if _BELEG_ZEILE_RE.match(zeile) or not _behauptet_arbeitet(zeile):
            continue
        nummern = [int(m) for m in _TICKET_NUMMER_RE.findall(zeile)]
        if nummern:
            for nr in nummern:
                b = je_ticket.get(nr)
                if b is not None and b.zustand == BLOCKIERT:
                    return (
                        f"Widerspruch in Aufseher-Meldung: #{nr} als „arbeitet“ gemeldet, "
                        f"Beleg zeigt „blockiert“ ({beleg_zeile(b)})."
                    )
        elif alle_blockiert:
            return (
                "Widerspruch in Aufseher-Meldung: nennt „arbeitet“, aber alle Belege zeigen "
                f"„blockiert“ ({beleg_zeile(belege[0])})."
            )
    return None


def beleg_hinweis(befehl: str, spec_platzhalter: str = "{S}") -> str:
    """Einzige Quelle der Beleg-Regel für beide Aufseher-Prompts (Dauer-Aufseher und Takt, #289)."""
    return (
        f"Kein Claim ohne Beweis: Nach jedem Anstoß an eine Bau-Session und vor jeder "
        f"Zustandsmeldung prüfst du die Wirkung am Fenster/Stand mit "
        f"`{befehl} aufseher-stand {spec_platzhalter} --ticket <N>` und nennst die Beleg-Zeile "
        f"(`Beleg #<N> · <Zeit> · <arbeitet|wartet|blockiert|fertig> · „<letzte Zeile>“`) wörtlich "
        f"in deiner Meldung — eine Meldung ohne Beleg-Zeile gilt im Aufseher-Takt als Fehler."
    )


def kopf_zeile(spec: int, lagen: list[TicketLage], jetzt: float, noop: bool) -> str:
    def zahl(bedingung: Callable[[TicketLage], bool]) -> int:
        return sum(1 for x in lagen if bedingung(x))

    uhr = datetime.fromtimestamp(jetzt, tz=UTC).astimezone().strftime("%H:%M")
    fenster_teile = [
        f"{zahl(lambda x: x.fenster == ARBEITET)} arbeitet",
        f"{zahl(lambda x: x.fenster == STILL)} still",
        f"{zahl(lambda x: x.fenster == RUECKFRAGE)} Rückfrage",
    ]
    blockiert_zahl = zahl(lambda x: x.fenster == BLOCKIERT)
    if blockiert_zahl:
        fenster_teile.append(f"{blockiert_zahl} blockiert")
    wartet_zahl = zahl(lambda x: x.fenster == WARTET)
    if wartet_zahl:
        fenster_teile.append(f"{wartet_zahl} wartet")
    teile = [
        f"Aufseher-Stand Spec #{spec} · {uhr}",
        (
            f"{zahl(lambda x: x.stufe == 'offen')} offen, "
            f"{zahl(lambda x: x.stufe == 'fertig_gebaut')} fertig gebaut, "
            f"{zahl(lambda x: x.endgueltig_zu)} zu"
        ),
        ", ".join(fenster_teile),
    ]
    if any(x.fenster == FENSTER_UNBEKANNT for x in lagen):
        teile.append(f"{TMUX_HINWEIS} (Sitzung spec-{spec} fehlt oder tmux aus)")
    if noop:
        teile.append("unverändert (noop)")
    return " · ".join(teile)


def stand(spec: int, q: Quellen, *, ordner: Path | None = None, alle: bool = False) -> str:
    """Kopfzeile + je Ticket eine Kurz-Zeile; hängt genau eine Zeile an die Stand-Datei.

    Ohne ``alle`` fehlen geschlossene Tickets ohne Fenster (der Kopf zählt sie mit).
    """
    vorher = _vorher(spec, ordner)
    erg = sammeln(spec, q, vorher)
    jetzt = q.jetzt()
    abdruck = fingerabdruck(erg.lagen)
    noop = abdruck == vorher.fingerabdruck and not any(x.neue_kommentare for x in erg.lagen)
    zeilen = [kurz_zeile(x) for x in erg.lagen]
    eintrag: dict[str, Any] = {
        "ts": datetime.fromtimestamp(jetzt, tz=UTC).isoformat(timespec="seconds"),
        "fingerabdruck": abdruck,
        "noop": noop,
    }
    if not noop:
        eintrag["zeilen"] = zeilen
    eintrag["kommentare"] = erg.kommentare
    eintrag["fenster"] = erg.fenster
    datei = stand_datei(spec, ordner)
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    sichtbar = [z for x, z in zip(erg.lagen, zeilen) if alle or not x.endgueltig_zu or x.fenster != KEIN_FENSTER]
    return "\n".join([kopf_zeile(spec, erg.lagen, jetzt, noop), *sichtbar])


# --- CLI --------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser(
        "aufseher-stand",
        help="Aufseher: eine Kurz-Zeile je Ticket + Stand-Datei (#430)",
    )
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument("--alle", action="store_true", help="auch geschlossene Tickets ohne Fenster")
    p.add_argument(
        "--ticket",
        type=int,
        default=None,
        help="nur ein Ticket prüfen und Beleg-Zeile (Zeit, Zustand, letzte Zeile) ausgeben (#289)",
    )
    p.add_argument(
        "--warte",
        type=float,
        default=0.0,
        help="Sekunden vor der --ticket-Beleg-Abfrage warten (nach Anstoß, Vorgabe 0)",
    )
    p.add_argument("--gh-repo", default="", help="owner/name (Vorgabe: aus origin)")
    p.add_argument(
        "--stand-ordner",
        default="",
        help=f"Ordner der Stand-Datei (Vorgabe: {STAND_ENV} bzw. ~/.local/state/to-spawn/aufseher)",
    )


def lauf(args: Any, repo: Path) -> int:
    gh_repo = args.gh_repo or gh.repo_aus_origin(repo, fallback="")
    if not gh_repo:
        print(
            "FEHLER: GitHub-Repo unbekannt — --gh-repo owner/name angeben.",
            file=sys.stderr,
        )
        return 2
    try:
        q = echte_quellen(repo, gh_repo)
        stand_pfad = Path(args.stand_ordner).expanduser() if args.stand_ordner else None
        if args.ticket is not None:
            beleg = nach_anstoss_beleg(
                args.spec, args.ticket, q, ordner=stand_pfad, warte_s=max(0.0, float(args.warte or 0.0))
            )
            print(beleg_zeile(beleg))
            return 0
        print(
            stand(
                args.spec,
                q,
                ordner=stand_pfad,
                alle=args.alle,
            )
        )
    except GhFehlt as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 2
    except RuntimeError as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1
    return 0
