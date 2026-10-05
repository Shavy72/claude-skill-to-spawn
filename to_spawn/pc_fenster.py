"""Bau-Sessions am PC (Windows): finden, still-Zeit lesen, beenden, im neuen Tab starten (#501, E10).

Am PC gibt es kein tmux. Jede Bau-Session läuft in einem Windows-Terminal-Tab ``bau <N>``
(``pwsh`` → ``bau.py <N>`` → ``claude``). In ein Fenster tippen oder seinen Bildschirm
lesen geht hier nicht — deshalb kennt der PC kein Anstupsen: Ablösung heißt alte Session
samt ``bau.py`` beenden und einen neuen Tab mit dem Handoff-Auftrag öffnen.

Einzige Stelle der Plattform-Weiche ist :func:`am_pc` (für das Werkzeug:
:func:`werkzeug` hinter ``respawn.werkzeug_fuer_rechner``); ``leiter`` fragt :func:`kann_tippen`. Die Ablösung am
PC ist :func:`abloesen`, die Tür ``respawn.abloesen`` fängt ihre Fehler. Alles Windows-Wissen (Prozessliste,
``taskkill``, ``wt``-Befehl, Transkript-Zeit) steht hier.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypeVar

from to_spawn import config, prozessbaum, sessions_datei

if TYPE_CHECKING:
    from to_spawn.respawn import Auftrag, Ergebnis

log = logging.getLogger(__name__)

#: Höchste Wartezeit, bis ein beendeter Prozessbaum wirklich weg ist.
BEENDEN_MAX_S = 15.0
_TAKT_S = 0.3
#: Takt, in dem die Ablösung in der Prozessliste nach der neuen Session sucht.
BELEG_TAKT_S = 3.0
#: Höchstens so lange (nie länger als ``--warte-max``) wartet die Ablösung auf die neue
#: Session — ``wt`` meldet einen nicht geöffneten Tab nicht (Befund 04.10.2026).
BELEG_MAX_S = 300.0


def am_pc() -> bool:
    """Läuft der Skill am PC (Windows, kein tmux)? Die eine Plattform-Weiche."""
    return sys.platform == "win32"


class Alte(NamedTuple):
    """Laufende Bau-Session eines Tickets am PC (``None`` = nicht gefunden)."""

    bau_pid: int | None  # ``bau.py <N>`` — wird mitbeendet, sonst startet es eine Folge-Runde
    session_pid: int | None  # ``claude`` unter ``bau.py`` (oder verwaist ohne ``bau.py``)


def alte_session(repo: Path, spec: int, ticket: int) -> Alte:
    """``bau.py`` + Claude-Session von ``ticket`` aus der Prozessliste (wie ``sessions <S>``)."""
    from to_spawn import spawn  # spät: spawn zieht capo/probesitz nach

    os.environ["TO_SPAWN_REPO"] = str(repo)
    eintrag = spawn.ticket_eintrag(spec, ticket)
    return Alte(_pid(eintrag.pid), _pid(eintrag.session_pid))


def _pid(wert: object) -> int | None:
    try:
        return int(wert) if wert else None  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def baum_beenden(pid: int) -> bool:
    """Beendet ``pid`` mit allen Kindern (``taskkill /T /F``) und wartet, bis er weg ist.

    True = weg (auch: war schon weg). Ohne Fenster gestartet (CREATE_NO_WINDOW).
    """
    if not prozessbaum._lebt_windows(pid):
        return True
    erg = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
        **prozessbaum.ohne_fenster(),
    )
    log.info("taskkill /T /F %d → Exit %d %s", pid, erg.returncode, (erg.stderr or "").strip())
    ende = time.monotonic() + BEENDEN_MAX_S
    while time.monotonic() < ende:
        if not prozessbaum._lebt_windows(pid):
            return True
        time.sleep(_TAKT_S)
    return False


def tab_befehl(repo: Path, ticket: int, auftrag: str) -> list[str]:
    """argv für einen neuen Tab ``bau <N>`` wie ``neustart`` am PC, mit Remote Control."""
    from to_spawn import spawn

    return spawn.wt_tab_befehl(repo, ticket, auftrag, "--remote-control")


def tab_starten(repo: Path, ticket: int, auftrag: str) -> None:
    """Öffnet den Tab ``bau <N>`` (der Tab ist gewollt sichtbar, ``wt`` selbst ohne Konsole).

    :class:`OSError`, wenn ``wt`` fehlt oder mit Exit ≠ 0 endet.
    """
    befehl = tab_befehl(repo, ticket, auftrag)
    log.info("Neuer Tab: %s", befehl)
    erg = subprocess.run(
        befehl, cwd=str(repo), capture_output=True, text=True, check=False, **prozessbaum.ohne_fenster()
    )
    if erg.returncode != 0:
        raise OSError(f"wt Exit {erg.returncode}: {(erg.stderr or '').strip() or 'ohne Meldung'}")


#: Nachkommen der Session, die so lange nach ihr starten, sind Werkzeug-Arbeit (Gate, pytest,
#: Subagent-Shell). MCP-Server starten mit der Session und zählen nicht.
WERKZEUG_NACH_S = 60.0
_FILETIME_ZU_UNIX = 11644473600.0
#: Prozesse, die ein Tab neben ``bau.py`` tragen darf und die mit ihm gehen.
_TAB_BEIWERK = ("conhost.exe", "openconsole.exe")


class Lauf(NamedTuple):
    """Momentaufnahme von ``bau <N>`` am PC (``None`` = nicht gefunden)."""

    tab_pid: int | None  # ``pwsh`` des Tabs, nur wenn er allein diese Session trägt
    bau_pid: int | None
    session_pid: int | None
    start: float | None  # Startzeit der Claude-Session (Unix-Sekunden)
    werkzeug_arbeitet: bool  # ein Nachkomme läuft, der ≥ WERKZEUG_NACH_S nach ihr startete


def _unix(filetime: int) -> float | None:
    return filetime / 1e7 - _FILETIME_ZU_UNIX if filetime else None


def lauf_aus(prozesse: dict[int, tuple[int, str, int]], ticket: int) -> Lauf:
    """:class:`Lauf` aus einer Prozessliste (PID → Eltern-PID, Kommandozeile, FILETIME-Start).

    Reine Logik; Kinder, die vor ihrem Eltern starteten, sind recycelte PIDs und zählen nicht.
    """
    kinder: dict[int, list[int]] = {}
    for pid, (eltern, _, start) in prozesse.items():
        if eltern in prozesse and not (start and prozesse[eltern][2] > start):
            kinder.setdefault(eltern, []).append(pid)

    def nachkommen(pid: int) -> list[int]:
        alle, offen = [], list(kinder.get(pid, []))
        while offen:
            k = offen.pop()
            alle.append(k)
            offen.extend(kinder.get(k, []))
        return alle

    muster = re.compile(rf"bau\.py[\"']?\s+{ticket}(\s|$)")
    treffer = {p for p, (_, zeile, _) in prozesse.items() if muster.search(zeile) and "python" in zeile.lower()}
    # Die Tab-Zeile ``pwsh -Command "python '…bau.py' <N>"`` trifft das Muster auch; ``bau.py``
    # ist der Treffer ohne Treffer-Kind (sonst hinge es an der Reihenfolge der Prozessliste).
    for bau in (p for p in treffer if not treffer.intersection(kinder.get(p, []))):
        for pid in nachkommen(bau):
            if not prozessbaum.ist_session_zeile(prozesse[pid][1]):
                continue
            start = _unix(prozesse[pid][2])
            arbeitet = start is not None and any(
                (t := _unix(prozesse[k][2])) is not None and t - start >= WERKZEUG_NACH_S for k in nachkommen(pid)
            )
            eltern = prozesse[bau][0]
            neben = [
                k
                for k in kinder.get(eltern, [])
                # Kommandozeile wie ``\??\C:\…\conhost.exe 0x4``: Programmname zählt, nicht das Zeilenende.
                if k != bau and not any(b in prozesse[k][1].lower() for b in _TAB_BEIWERK)
            ]
            tab = eltern if muster.search(prozesse.get(eltern, (0, "", 0))[1]) and not neben else None
            return Lauf(tab, bau, pid, start, arbeitet)
    return Lauf(None, None, None, None, False)


def lauf(ticket: int) -> Lauf:
    """:func:`lauf_aus` über die echte Prozessliste."""
    return lauf_aus(prozessbaum._prozesse_windows(), ticket)


def still_s(repo: Path, ticket: int, jetzt: float) -> float | None:
    """Sekunden Stille der Session von ``bau <N>``; ``None`` = keine lebende Session.

    Tot (kein Claude unter ``bau.py <N>``) → ``None``, also „kein Fenster“, nie Ablösung.
    Läuft ein Werkzeug (:attr:`Lauf.werkzeug_arbeitet`) → 0. Sonst die Zeit seit dem
    jüngsten Eintrag in ``~/.claude/projects/<cwd>/<id>.jsonl`` oder einem
    Subagent-Transkript ``<id>/subagents/*.jsonl`` (Session-ID + cwd aus
    ``.to-spawn/sessions/<N>.json``, schreibt ``bau.py``).
    """
    from to_spawn.waechter_lauf import transkript_ordner  # spät: Kreis über respawn

    jetzt_lauf = lauf(ticket)
    if jetzt_lauf.session_pid is None:
        return None
    if jetzt_lauf.werkzeug_arbeitet:
        return 0.0
    daten = sessions_datei.lesen(repo, str(ticket))
    if not daten or not daten.get("session_id") or not daten.get("cwd"):
        return None
    ordner = transkript_ordner(Path(str(daten["cwd"])))
    sid = str(daten["session_id"])
    zeiten = []
    for datei in [ordner / f"{sid}.jsonl", *(ordner / sid / "subagents").glob("*.jsonl")]:
        try:
            zeiten.append(datei.stat().st_mtime)
        except OSError:
            continue
    return max(0.0, jetzt - max(zeiten)) if zeiten else None


class PcWerkzeug:
    """Werkzeug am PC (#501): nichts tippen, nichts lesen — finden, beenden, Tab starten."""

    def alte_session(self, repo: Path, spec: int, ticket: int) -> Alte:
        return alte_session(repo, spec, ticket)

    def lauf(self, ticket: int) -> Lauf:
        return lauf(ticket)

    def baum_beenden(self, pid: int) -> bool:
        return baum_beenden(pid)

    def tab_starten(self, repo: Path, ticket: int, auftrag: str) -> None:
        tab_starten(repo, ticket, auftrag)

    def jetzt(self) -> float:
        return time.time()

    def schlafen(self, s: float) -> None:
        time.sleep(s)


class _FensterListe(Protocol):
    def fenster_liste(self) -> object: ...


_T = TypeVar("_T", bound=_FensterListe)


def werkzeug(tmux: _T) -> _T | PcWerkzeug:
    """``tmux`` — außer am PC, wo tmux nicht antwortet: dann :class:`PcWerkzeug`.

    Probe ist eine Fensterliste: am Bau-Server nie gefragt, am PC mit tmux (z. B. WSL-Brücke)
    bleibt der tmux-Weg. Fehlendes tmux ist am PC der Normalfall — dessen Fehler-Log schweigt.
    """
    if not am_pc():
        return tmux
    tmux_log = logging.getLogger("to_spawn.respawn")
    vorher, tmux_log.disabled = tmux_log.disabled, True
    try:
        tmux.fenster_liste()
    except RuntimeError:  # TmuxFehler: tmux nicht aufrufbar (#501)
        return PcWerkzeug()
    finally:
        tmux_log.disabled = vorher
    return tmux


def kann_tippen(w: object) -> bool:
    """Kann das Werkzeug in ein Fenster tippen? Am PC nicht (kein Anstupsen, #501)."""
    return not isinstance(w, PcWerkzeug)


def handoff_datei(wt: Path, ticket: int, seit: float | None) -> Path | None:
    """Jüngster Handoff des Tickets (sonst Start-Prompt); mit ``seit`` nur frische."""
    from to_spawn import respawn

    ordner = wt / respawn.HANDOFF_ORDNER
    for muster in (f"HANDOFF_*_{ticket}.md", f"START_*_{ticket}.txt"):
        kandidaten = [p for p in ordner.glob(muster) if respawn._frisch(p, seit or 0.0)]
        if kandidaten:
            return max(kandidaten, key=lambda p: p.stat().st_mtime)
    return None


def abloesen(a: Auftrag, w: PcWerkzeug) -> Ergebnis:
    """Ablösung am PC (#501 E10): alte Session samt ``bau.py`` beenden, neuer Tab, Beleg.

    Beleg = eine andere Claude-Session für ``bau <N>`` in der Prozessliste (≤ ``BELEG_MAX_S``).
    Ausnahmen fängt die Tür ``respawn.abloesen``.
    """
    from to_spawn import respawn, spawn  # spät: respawn importiert dieses Modul

    alt = w.alte_session(a.repo, a.spec, a.ticket)
    opfer = alt.bau_pid or alt.session_pid
    jetzt_lauf = w.lauf(a.ticket)
    if jetzt_lauf.tab_pid and jetzt_lauf.bau_pid == alt.bau_pid:
        opfer = jetzt_lauf.tab_pid  # Tab-pwsh trägt nur diese Session → alter Tab geht mit
    # Ohne gemerkten Eingriff zählen nur Handoffs nach dem Start der alten Session
    # (unbekannt → keiner, also normaler Start statt eines alten Handoffs).
    seit = a.handoff_seit if a.handoff_seit is not None else (jetzt_lauf.start or w.jetzt())
    if alt.session_pid is None or opfer is None:
        return respawn.Ergebnis(
            respawn.EXIT_DUPLIKAT,
            f"{a.kopf}: alte Session fehlt — für „{a.name_alt}“ läuft kein Claude, nichts angefasst",
        )
    wt = Path(config.worktree_pfad(a.ticket, a.repo)).expanduser()
    datei = handoff_datei(wt, a.ticket, seit)
    pfad = f"{respawn.HANDOFF_ORDNER}/{datei.name}" if datei else ""
    auftrag = spawn.neustart_auftrag(pfad)
    was = f"Handoff {pfad}" if datei else "ohne Handoff (normaler Start)"
    alt_text = f"bau.py {alt.bau_pid or '-'}, claude {alt.session_pid}"
    if a.dry_run:
        return respawn.Ergebnis(
            respawn.EXIT_OK,
            f"{a.kopf}: dry-run (PC) — alte Session beenden ({alt_text}), neuer Tab "
            f"„{a.name_alt}“ mit Remote Control, {was}",
        )
    if not w.baum_beenden(opfer):
        return respawn.Ergebnis(
            respawn.EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: alte Session ({alt_text}) lebt nach taskkill — nichts gestartet, Handarbeit nötig",
        )
    log.info("respawn #%s (PC): alte Session beendet (%s).", a.ticket, alt_text)
    try:
        w.tab_starten(a.repo, a.ticket, auftrag)
    except OSError as fehler:
        return respawn.Ergebnis(
            respawn.EXIT_NICHT_BEWIESEN,
            f"{a.kopf}: alte Session beendet, neuer Tab nicht gestartet ({fehler}) — Handarbeit nötig",
        )
    frist = min(a.warte_max, BELEG_MAX_S)
    ende = w.jetzt() + frist
    while True:
        neu = w.alte_session(a.repo, a.spec, a.ticket).session_pid
        if neu is not None and neu != alt.session_pid:
            return respawn.Ergebnis(
                respawn.EXIT_OK,
                f"{a.kopf}: ok (PC) — alte Session ({alt_text}) beendet, neue Session "
                f"{neu} im Tab „{a.name_alt}“, {was}",
            )
        if w.jetzt() >= ende:
            return respawn.Ergebnis(
                respawn.EXIT_NICHT_BEWIESEN,
                f"{a.kopf}: alte Session beendet, Tab „{a.name_alt}“ gestartet — neue Session "
                f"nach {int(frist)} s nicht in der Prozessliste",
            )
        w.schlafen(BELEG_TAKT_S)
