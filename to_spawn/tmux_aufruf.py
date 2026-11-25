"""Ein tmux-Aufruf mit klaren Fehlern und die einzige Stelle für tmux-Session-Namen je Repo (#457).

Welches tmux läuft (``TO_SPAWN_TMUX``), entscheidet ``capo._tmux_befehl``. Hier steht:
- wie ein Aufruf scheitert: Exit ≠ 0, Zeitüberschreitung oder kein tmux → :class:`TmuxFehler`,
  fehlendes Fenster/Pane → :class:`FensterWeg`. ``start=True`` (neues Fenster) läuft über die
  Start-Tür :mod:`to_spawn.starter` (#1406).
- wie die tmux-Session einer Spec heißt (``spec-<kurz>-<S>``, Übergang zu bestehender eigener
  ``spec-<S>``) und wem eine laufende Session gehört (:func:`sitzung_name`,
  :func:`sitzung_aufloesen`, :func:`sitzung_fremd`, :func:`spec_aus_sitzung`).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from to_spawn import config, prozessbaum, starter

log = logging.getLogger(__name__)

#: Exit-Code, wenn eine vorhandene tmux-Session einem anderen Repo gehört (#457).
EXIT_FREMD = 9

#: Erlaubte Formen von Spec-Sessions: alt ``spec-<S>`` und neu ``spec-<kurz>-<S>`` (#457).
_SITZUNG_RE = re.compile(r"^spec-(?:[a-z0-9][a-z0-9_-]*-)?(\d+)$")

_KEIN_SERVER_TEXTE = (
    "no server running",
    "error connecting",
    "nicht aufrufbar",
    "can't find",
)


class TmuxFehler(RuntimeError):
    """Ein tmux-Aufruf ist gescheitert (Exit ≠ 0 oder Zeitüberschreitung).

    Trägt Unterbefehl und stderr, damit die Ergebniszeile sagt, was tmux meldete.
    """

    def __init__(self, unterbefehl: str, stderr: str) -> None:
        super().__init__(f"tmux {unterbefehl}: {stderr.strip() or 'ohne Meldung'}")
        self.unterbefehl = unterbefehl
        self.stderr = stderr


class FensterWeg(TmuxFehler):
    """Das Ziel-Fenster/-Pane gibt es nicht mehr (Session hat sich selbst beendet)."""


def aufrufen(*argumente: str, eingabe: str | None = None, start: bool = False) -> str:
    """Ein tmux-Aufruf; Ausgabe als Text, jeder Fehler als :class:`TmuxFehler`.

    ``start=True``: der Aufruf startet etwas (``new-window``) und geht durch die Start-Tür.
    """
    from to_spawn import capo  # spät: capo nutzt sitzung_* aus diesem Modul

    unterbefehl = argumente[0] if argumente else ""
    # tmux spricht UTF-8; ohne ``encoding`` liest Windows cp1252 und ``●``/``❯`` brechen (#602).
    lauf_args = {
        "input": eingabe,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": True,
        "timeout": 30,
    }
    try:
        if start:
            fertig = starter.starte([*capo._tmux_befehl(), *argumente], warten=True, **lauf_args)
        else:
            fertig = subprocess.run([*capo._tmux_befehl(), *argumente], **lauf_args, **prozessbaum.fensterlos())
    except subprocess.CalledProcessError as fehler:
        stderr = str(fehler.stderr or "")
        if "can't find window" in stderr or "can't find pane" in stderr:
            raise FensterWeg(unterbefehl, stderr) from fehler
        raise TmuxFehler(unterbefehl, stderr) from fehler
    except OSError as fehler:  # tmux fehlt (z. B. am PC: FileNotFoundError, #501)
        raise TmuxFehler(unterbefehl, f"nicht aufrufbar ({fehler})") from fehler
    except subprocess.TimeoutExpired as fehler:
        raise TmuxFehler(unterbefehl, f"keine Antwort nach {fehler.timeout} s") from fehler
    return fertig.stdout


# --- Session-Name je Repo (#457) ---------------------------------------------


def sitzung_alt(spec: int | str) -> str:
    """Alter Session-Name ohne Repo-Anteil (``spec-<S>``) — für den Übergang (#457)."""
    return f"spec-{spec}"


def sitzung_name(repo: Path, spec: int | str) -> str:
    """Eindeutiger tmux-Session-Name je Repo: ``spec-<kurz>-<S>`` (#457)."""
    return f"spec-{config.repo_kurzname(repo)}-{spec}"


def spec_aus_sitzung(name: str) -> str | None:
    """Spec-Nummer aus ``spec-<S>`` oder ``spec-<kurz>-<S>``; ``None`` = keine Spec-Session."""
    treffer = _SITZUNG_RE.fullmatch(name.strip())
    return treffer.group(1) if treffer else None


def ordner_kennung(ordner: Path) -> str:
    """Repo-Kennung aus :func:`config.repo_kennung`, ohne Git der aufgelöste echte Pfad."""
    kennung = config.repo_kennung(ordner)
    if kennung is not None:
        return kennung
    return os.path.normcase(os.path.realpath(ordner))


def ordner_ist_fremd(ordner: Path, repo: Path) -> bool:
    """``True``, wenn ``ordner`` einem anderen Repo gehört als ``repo`` (#457)."""
    return ordner_kennung(ordner) != ordner_kennung(repo)


def _tmux_lesen(
    argumente: list[str],
    tmux: Callable[[list[str]], str | None] | None = None,
) -> tuple[str | None, str]:
    """Lesender tmux-Aufruf; Rückgabe ``(stdout, stderr)``, bei Fehler ``stdout=None``."""
    if tmux is not None:
        return tmux(argumente), ""
    try:
        return aufrufen(*argumente), ""
    except TmuxFehler as fehler:
        return None, fehler.stderr


def sitzung_besitzer(
    sitzung: str,
    *,
    tmux: Callable[[list[str]], str | None] | None = None,
) -> tuple[str | None, str | None]:
    """``(session_path, repo_kennung)`` einer laufenden tmux-Session; ``(None, None)`` = unbekannt."""
    roh, _ = _tmux_lesen(
        ["display-message", "-p", "-t", f"={sitzung}:", "#{session_path}"],
        tmux=tmux,
    )
    if roh is None:
        return None, None
    pfad = roh.strip().splitlines()[0].strip() if roh.strip() else ""
    if not pfad:
        return None, None
    return pfad, ordner_kennung(Path(pfad))


def sitzung_fremd(
    sitzung: str,
    repo: Path,
    *,
    tmux: Callable[[list[str]], str | None] | None = None,
) -> str | None:
    """Weigerungs-Zeile, wenn ``sitzung`` schon einem anderen Repo gehört (#457); sonst ``None``."""
    pfad, besitzer = sitzung_besitzer(sitzung, tmux=tmux)
    if pfad is None or besitzer is None:
        return None
    eigen = ordner_kennung(repo)
    if besitzer == eigen:
        return None
    return (
        f"WEIGERUNG (#457): tmux-Session {sitzung} gehört einem anderen Repo "
        f"({besitzer}, Ordner {pfad}), nicht {eigen} — nichts gestartet. "
        f"Erst die andere Spec beenden (Session {sitzung} schließen), dann erneut."
    )


def sitzung_aus_befehl(befehl: Sequence[str]) -> str | None:
    """Ziel-Session aus einem tmux-``new-window``/``new-session``-Befehl lesen (#457)."""
    for i, teil in enumerate(befehl):
        if teil in ("-t", "-s") and i + 1 < len(befehl):
            wert = befehl[i + 1].lstrip("=").split(":", 1)[0].strip()
            if wert:
                return wert
    return None


def sitzung_aufloesen(
    repo: Path,
    spec: int | str,
    *,
    tmux: Callable[[list[str]], str | None] | None = None,
) -> tuple[str, str | None]:
    """Session-Name für ``(repo, spec)`` samt Besitzer-Prüfung — die einzige Stelle (#457).

    Rückgabe ``(sitzung, fremd_grund)``:
    - Läuft ``spec-<kurz>-<S>`` schon: dieser Name; gehört er einem anderen Repo (gleicher
      Kurzname in zwei Organisationen), liefert ``fremd_grund`` die Weigerung (Exit 9).
    - Läuft stattdessen die alte ``spec-<S>`` und gehört zu ``repo`` (oder Besitzer unlesbar):
      weiter ``spec-<S>``, bis diese Session endet.
    - Gehört ``spec-<S>`` einem anderen Repo oder läuft keine Session: ``spec-<kurz>-<S>``.
    """
    neu = sitzung_name(repo, spec)
    alt = sitzung_alt(spec)
    roh, fehler = _tmux_lesen(["list-sessions", "-F", "#{session_name}"], tmux=tmux)
    if roh is not None:
        laufend = {zeile.split()[0] for zeile in roh.splitlines() if zeile.strip()}
        if neu in laufend:
            return neu, sitzung_fremd(neu, repo, tmux=tmux)
        if alt in laufend and sitzung_fremd(alt, repo, tmux=tmux) is None:
            return alt, None
        return neu, None
    # Kein tmux-Server oder tmux fehlt: neue Session im eindeutigen Namen.
    klein = fehler.lower()
    if any(m in klein for m in _KEIN_SERVER_TEXTE):
        return neu, None
    # Minimale Test-Attrappen, die nur ``list-windows`` kennen (z. B. tmux_stub_284):
    # antwortet ``spec-<S>`` ohne fremden Besitzer, gilt der bestehende Alt-Name.
    probe, _ = _tmux_lesen(["list-windows", "-t", f"={alt}", "-F", "#W"], tmux=tmux)
    if probe is not None and sitzung_fremd(alt, repo, tmux=tmux) is None:
        return alt, None
    return neu, None


def _schliesse_wenn_eigen(repo: Path, name: str) -> None:
    """Schließt genau die tmux-Session ``name``, wenn mindestens ein Pane in ``repo`` startet."""
    ziel = f"={name}"
    try:
        roh = aufrufen("list-panes", "-s", "-t", ziel, "-F", "#{pane_start_path}")
    except TmuxFehler as fehler:
        if "can't find session" not in fehler.stderr and "no server running" not in fehler.stderr:
            log.warning("Session %s nicht geschlossen: Pfade nicht abfragbar (%s)", name, fehler)
        return
    wurzel = repo.resolve()
    pfade = [Path(z).resolve() for z in roh.splitlines() if z.strip()]
    if not any(p == wurzel or wurzel in p.parents for p in pfade):
        log.warning("Session %s nicht geschlossen: kein Fenster startet in %s", name, wurzel)
        return
    try:
        aufrufen("kill-session", "-t", ziel)
    except FensterWeg:
        return


def spec_session_schliessen(repo: Path, spec: int) -> None:
    """tmux-Session der Spec schließen, aber nur wenn sie nachweislich zu ``repo`` gehört (#546, #457).

    Prüft sowohl den eindeutigen Namen ``spec-<kurz>-<S>`` als auch eine noch laufende alte
    ``spec-<S>`` — erst die Startpfade der Fenster abfragen, mindestens einer muss im
    Repo-Ordner liegen. Nicht belegbar → nichts schließen, eine Log-Zeile. Fehlt die Session,
    ist das kein Fehler. Windows-Terminal: ohne tmux scheitert die Abfrage → nichts geschlossen.
    """
    kandidaten = list(dict.fromkeys((sitzung_name(repo, spec), sitzung_alt(spec))))
    for name in kandidaten:
        _schliesse_wenn_eigen(repo, name)
