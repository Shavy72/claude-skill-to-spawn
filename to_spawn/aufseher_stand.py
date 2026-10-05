"""Aufseher-Stand (#430): je Ticket der Spec genau eine Kurz-Zeile + Stand-Datei je Tick.

Der Aufseher braucht je Tick einen Blick auf alle Tickets, ohne Pane-Text in seinen
Kontext zu kippen (E19). Dieses Modul liest alle Quellen, verdichtet sie und hängt
je Aufruf genau eine Zeile an die Stand-Datei (E21) — sie ist das Gedächtnis des
Aufsehers, ein Nachfolger startet mit :func:`letzter_stand`.

Schnittstelle für Aufrufer: :func:`stand` (alles in einem Zug) und
:func:`letzter_stand`. Tests geben über :class:`Quellen` Fake-tmux und Fake-GitHub
hinein (E25); Bau-Log und Stand-Datei bleiben echt.

Kurz-Zeile (≤ 200 Zeichen)::

    #<N> offen|zu|— · arbeitet|still <M> min|Rückfrage <M> min|kein Fenster
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
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from to_spawn import aufpasser, bau_log, capo, config, gh, pc_fenster

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
STILL = "still"
RUECKFRAGE = "Rückfrage"
KEIN_FENSTER = "kein Fenster"
#: Fenster-Zustand, wenn tmux bzw. die Sitzung ``spec-<S>`` nicht lesbar ist.
FENSTER_UNBEKANNT = STRICH
TMUX_HINWEIS = "tmux nicht lesbar"


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
        repo=repo, kinder=lambda s: capo.kinder(gh_repo, s), tmux=_tmux_echt, pc_still=pc_still
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
    offen: bool | None
    fenster: str  # ARBEITET | STILL | RUECKFRAGE | KEIN_FENSTER | FENSTER_UNBEKANNT
    still_min: int | None = None
    kontext: str = STRICH
    phase: str = STRICH
    aussage: str = STRICH
    neue_kommentare: int | None = None


def _kuerzen(text: str, laenge: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= laenge else text[: laenge - 1].rstrip() + "…"


def kurz_zeile(lage: TicketLage) -> str:
    """Eine Zeile ≤ :data:`ZEILE_MAX` Zeichen, nie Pane-Text."""
    zustand = {True: "offen", False: "zu", None: STRICH}[lage.offen]
    fenster = lage.fenster
    if fenster in (STILL, RUECKFRAGE) and lage.still_min is not None:
        fenster = f"{fenster} {lage.still_min} min"
    aussage = (
        STRICH if lage.aussage == STRICH else f"„{_kuerzen(lage.aussage, AUSSAGE_MAX)}“"
    )
    kommentare = STRICH if lage.neue_kommentare is None else str(lage.neue_kommentare)
    teile = [
        f"#{lage.nummer} {zustand}",
        fenster,
        f"Kontext {lage.kontext}",
        f"Phase {_kuerzen(lage.phase, 30)}",
        aussage,
        f"{kommentare} neue Kommentare",
    ]
    return _kuerzen(" · ".join(teile), ZEILE_MAX)


def fingerabdruck(lagen: list[TicketLage]) -> str:
    """Hash der Kurz-Zeilen ohne Minuten und Kommentar-Zähler (die laufen immer weiter)."""
    roh = "\n".join(
        kurz_zeile(replace(x, still_min=None, neue_kommentare=None)) for x in lagen
    )
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
        kommentare={
            str(k): int(v) for k, v in (letzte.get("kommentare") or {}).items()
        },
        fenster=dict(letzte.get("fenster") or {}),
    )


def _fenster_liste(spec: int, q: Quellen) -> dict[int, tuple[str, float]] | None:
    """Ticket → (tmux-Ziel, window_activity); ``None`` = tmux/Sitzung nicht lesbar."""
    raus = q.tmux(
        [
            "list-windows",
            "-t",
            f"=spec-{spec}",
            "-F",
            "#{window_index}\t#{window_name}\t#{window_activity}",
        ]
    )
    if raus is None:
        log.warning("tmux-Sitzung spec-%s nicht lesbar — Fenster-Zustand „—“.", spec)
        return None
    liste: dict[int, tuple[str, float]] = {}
    for zeile in raus.splitlines():
        teile = zeile.split("\t")
        if len(teile) != 3 or not teile[1].startswith("bau "):
            continue
        try:
            liste[int(teile[1][4:])] = (
                f"=spec-{spec}:{teile[0]}",
                float(teile[2] or 0),
            )
        except ValueError:
            continue
    return liste


def _fenster_lage(
    n: int, ziel: str, aktiv: float, q: Quellen, vorher: _Vorher, jetzt: float
) -> tuple[str, int | None, dict[str, Any] | None]:
    """(Zustand, Still-Minuten, Merker für die Stand-Datei)."""
    text = q.tmux(["capture-pane", "-p", "-t", ziel])
    if text is None:
        log.warning("Pane von #%s (%s) nicht lesbar.", n, ziel)
        return KEIN_FENSTER, None, None
    h = aufpasser.pane_hash(text)
    alt = vorher.fenster.get(str(n)) or {}
    # tmux-Aktivität zählt auch die tickende Uhr; gleicher Hash = still seit dem alten Wert.
    seit = min(float(alt.get("seit", aktiv)), aktiv) if alt.get("hash") == h else aktiv
    merker = {"hash": h, "seit": seit}
    minuten = max(0, int((jetzt - seit) // 60))
    if aufpasser.rueckfrage(text):
        return RUECKFRAGE, minuten, merker
    if aufpasser.arbeitet(text):
        return ARBEITET, None, merker
    return STILL, minuten, merker


def _log_lage(n: int, repo: Path) -> tuple[str, str, str] | None:
    """(Kontext, Phase, letzte Aussage) aus dem Bau-Log; ``—`` je fehlendem Teil.

    ``None`` = kein Bau-Log (Ticket noch nicht gestartet) — der Aufrufer warnt gesammelt.
    """
    try:
        ort = bau_log.log_ort(n, Path(config.worktree_pfad(n, repo)).expanduser(), repo)
        if ort is None:
            return None
        zeilen = bau_log.lese(ort, n, hauptbaum=repo)
        spitze_k = bau_log.zusammenfassung(ort, n, hauptbaum=repo)["spitze_k"]
    except (OSError, ValueError, KeyError) as fehler:
        log.warning("Bau-Log #%s unlesbar: %s", n, fehler)
        return STRICH, STRICH, STRICH
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
    return kontext, phase, aussage


def _offen(kind: dict[str, Any]) -> bool | None:
    state = str(kind.get("state") or "").lower()
    return {"open": True, "closed": False}.get(state)


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
        raise RuntimeError(
            f"Spec #{spec} hat keine Sub-Issues — Spec-Nummer prüfen (gh)"
        )
    jetzt = q.jetzt()
    fenster = None if q.pc_still else _fenster_liste(spec, q)
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
        if q.pc_still is not None:
            zustand, still_min = _pc_lage(q.pc_still(n))
        elif fenster is None:
            zustand, still_min = FENSTER_UNBEKANNT, None
        elif n in fenster:
            zustand, still_min, m = _fenster_lage(n, *fenster[n], q, vorher, jetzt)
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
            log_lage = (STRICH, STRICH, STRICH)
        kontext, phase, aussage = log_lage
        lagen.append(
            TicketLage(
                n, _offen(kind), zustand, still_min, kontext, phase, aussage, neue
            )
        )
    if ohne_log:
        # Eine Zeile statt einer je Ticket: der Aufseher liest die Ausgabe in seinen Kontext.
        log.warning("Kein Bau-Log (Kontext/Phase/Aussage „—“): %s", ", ".join(ohne_log))
    return _Ergebnis(lagen, kommentare, merker)


class TicketBlick(NamedTuple):
    """Ein Ticket aus Sicht des Aufseher-Stands (``None`` = unbekannt)."""

    lage: TicketLage | None  # ``None``: kein Sub-Issue der Spec
    ziel: str | None  # tmux-Ziel des Fensters ``bau <N>``


def ticket_lage(
    spec: int, ticket: int, q: Quellen, ordner: Path | None = None
) -> TicketBlick:
    """Lage eines Tickets wie :func:`stand` sie sieht, plus tmux-Ziel — nur lesen.

    Die Stand-Datei bleibt unberührt (schreiben tut sie nur der Aufseher-Tick).
    :class:`RuntimeError` wie :func:`sammeln`, wenn gh nichts liefert. Nutzer:
    Eingriffs-Leiter (#432).
    """
    erg = sammeln(spec, q, _vorher(spec, ordner))
    lage = next((t for t in erg.lagen if t.nummer == ticket), None)
    fenster = {} if q.pc_still else (_fenster_liste(spec, q) or {})
    eintrag = fenster.get(ticket)  # (tmux-Ziel, window_activity); am PC kein Ziel
    return TicketBlick(lage, eintrag[0] if eintrag else None)


def kopf_zeile(spec: int, lagen: list[TicketLage], jetzt: float, noop: bool) -> str:
    def zahl(bedingung: Callable[[TicketLage], bool]) -> int:
        return sum(1 for x in lagen if bedingung(x))

    uhr = datetime.fromtimestamp(jetzt, tz=timezone.utc).astimezone().strftime("%H:%M")
    teile = [
        f"Aufseher-Stand Spec #{spec} · {uhr}",
        f"{zahl(lambda x: x.offen is True)} offen, {zahl(lambda x: x.offen is False)} zu",
        (
            f"{zahl(lambda x: x.fenster == ARBEITET)} arbeitet, "
            f"{zahl(lambda x: x.fenster == STILL)} still, "
            f"{zahl(lambda x: x.fenster == RUECKFRAGE)} Rückfrage"
        ),
    ]
    if any(x.fenster == FENSTER_UNBEKANNT for x in lagen):
        teile.append(f"{TMUX_HINWEIS} (Sitzung spec-{spec} fehlt oder tmux aus)")
    if noop:
        teile.append("unverändert (noop)")
    return " · ".join(teile)


def stand(
    spec: int, q: Quellen, *, ordner: Path | None = None, alle: bool = False
) -> str:
    """Kopfzeile + je Ticket eine Kurz-Zeile; hängt genau eine Zeile an die Stand-Datei.

    Ohne ``alle`` fehlen geschlossene Tickets ohne Fenster (der Kopf zählt sie mit).
    """
    vorher = _vorher(spec, ordner)
    erg = sammeln(spec, q, vorher)
    jetzt = q.jetzt()
    abdruck = fingerabdruck(erg.lagen)
    noop = abdruck == vorher.fingerabdruck and not any(
        x.neue_kommentare for x in erg.lagen
    )
    zeilen = [kurz_zeile(x) for x in erg.lagen]
    eintrag: dict[str, Any] = {
        "ts": datetime.fromtimestamp(jetzt, tz=timezone.utc).isoformat(
            timespec="seconds"
        ),
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
    sichtbar = [
        z
        for x, z in zip(erg.lagen, zeilen)
        if alle or x.offen is not False or x.fenster != KEIN_FENSTER
    ]
    return "\n".join([kopf_zeile(spec, erg.lagen, jetzt, noop), *sichtbar])


# --- CLI --------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser(
        "aufseher-stand",
        help="Aufseher: eine Kurz-Zeile je Ticket + Stand-Datei (#430)",
    )
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument(
        "--alle", action="store_true", help="auch geschlossene Tickets ohne Fenster"
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
        print(
            stand(
                args.spec,
                q,
                ordner=Path(args.stand_ordner).expanduser()
                if args.stand_ordner
                else None,
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
