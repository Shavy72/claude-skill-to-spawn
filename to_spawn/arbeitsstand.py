"""Arbeitsordner auf Hauptzweig-Stand vor dem Start (#283).

Der Start einer Bau-Session liest Vorlage (``_default.json`` im Manifest-Ordner) und
Hilfs-Skripte aus dem Arbeitsordner des Repos. Liegt der hinter ``origin/<Hauptzweig>``,
liefe die Session still mit altem Stand (Fall PC: 5839 Commits zurück). Dieses Modul
stellt den Stand sicher, bevor irgendetwas davon gelesen wird.

Eine Tür: :func:`sicherstellen` (``nachziehen=False`` = nur prüfen, z. B. Probelauf und
``einrichten --pruefen``). Ergebnis :class:`Stand` mit ``zustand``:

- ``aktuell``     — nicht hinter ``origin/<Hauptzweig>`` (eigene Commits voraus sind ok).
- ``nachgezogen`` — war N Commits zurück, sauber per fast-forward nachgezogen.
- ``veraltet``    — N Commits zurück, nur geprüft (``nachziehen=False``).
- ``abbruch``     — N Commits zurück und nicht nachziehbar (lokale Änderungen, anderer
  Zweig, abweichende Historie) oder Stand nicht prüfbar (fetch/Netz/git) — nie still
  „aktuell“. ``text`` nennt Zahl und was zu tun ist.

Versteckt wird hier: Name des Hauptzweigs (über :func:`to_spawn.gh.hauptzweig`), wie
„hinter“ gemessen wird (``rev-list --count HEAD..origin/<Hauptzweig>``), fetch, die
Nachzieh-Regel (nur fast-forward bei sauberem Baum, nie rebase/stash/reset) und die
Sperre gegen parallele Starts im selben Ordner (mehrere ``bau``-Tabs zugleich).

Test-Naht: ``TO_SPAWN_TEST_ARBEITSSTAND_OK=1`` gilt nur unter Test
(:func:`to_spawn.starter.unter_test`) und meldet ``aktuell`` ohne git — Start-Tests mit
Wegwerf-Repos ohne ``origin`` bleiben so unberührt. ``tests/test_arbeitsstand_283.py``
löscht die Variable und prüft mit echtem git.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from to_spawn import gh, sperre, starter

log = logging.getLogger("to_spawn.arbeitsstand")

AKTUELL, NACHGEZOGEN, VERALTET, ABBRUCH = (
    "aktuell",
    "nachgezogen",
    "veraltet",
    "abbruch",
)
TEST_OK_ENV = "TO_SPAWN_TEST_ARBEITSSTAND_OK"
#: Zeitlimits (Sekunden): fetch braucht Netz, lokale git-Aufrufe nicht.
FETCH_TIMEOUT_S = 120.0
GIT_TIMEOUT_S = 30.0
#: So lange wartet ein paralleler Start auf die Sperre, bevor er abbricht.
SPERRE_WARTEN_S = 300.0


@dataclass(frozen=True)
class Stand:
    zustand: str
    text: str
    hinter: int | None = None

    @property
    def ok(self) -> bool:
        """Darf gestartet werden? Nur bei ``aktuell``/``nachgezogen``."""
        return self.zustand in (AKTUELL, NACHGEZOGEN)


def _git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> tuple[int, str]:
    """``git <args>`` in ``repo`` → (Exit, stdout bzw. stderr bei Fehler); 127/124 = fehlt/Zeit."""
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"Zeitlimit {timeout:.0f} s"
    except OSError as fehler:
        return 127, str(fehler)
    aus = fertig.stdout if fertig.returncode == 0 else (fertig.stderr or fertig.stdout)
    return fertig.returncode, (aus or "").strip()


def _kurz(text: str) -> str:
    zeilen = [z for z in text.splitlines() if z.strip()]
    return (zeilen[-1] if zeilen else "ohne Meldung")[:200]


def _sperr_datei(repo: Path) -> Path | None:
    code, ort = _git(repo, "rev-parse", "--git-common-dir")
    if code != 0 or not ort:
        return None
    pfad = Path(ort)
    return (
        pfad if pfad.is_absolute() else repo / pfad
    ) / "to-spawn-arbeitsstand.sperre"


def sicherstellen(repo: Path, *, nachziehen: bool = True) -> Stand:
    """Stellt sicher, dass ``repo`` nicht hinter ``origin/<Hauptzweig>`` liegt (siehe Modul-Text)."""
    if os.environ.get(TEST_OK_ENV) == "1" and starter.unter_test():
        return Stand(AKTUELL, "Stand-Check im Test übersprungen", 0)
    datei = _sperr_datei(repo)
    if datei is None:
        return Stand(
            ABBRUCH, f"Stand nicht prüfbar: {repo} ist kein git-Arbeitsordner."
        )
    try:
        with sperre.halte(datei, warten=SPERRE_WARTEN_S) as gehalten:
            if not gehalten:
                return Stand(
                    ABBRUCH,
                    f"Stand nicht prüfbar: ein anderer Start zieht {repo} seit über "
                    f"{SPERRE_WARTEN_S:.0f} s nach — prüfen, ob git hängt, dann erneut starten.",
                )
            return _pruefen_und_nachziehen(repo, nachziehen)
    except OSError as fehler:
        return Stand(
            ABBRUCH, f"Stand nicht prüfbar: Sperre {datei} nicht nutzbar ({fehler})."
        )


def _pruefen_und_nachziehen(repo: Path, nachziehen: bool) -> Stand:
    haupt = gh.hauptzweig(repo)
    ziel = f"origin/{haupt}"
    code, aus = _git(repo, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT_S)
    if code != 0:
        return Stand(
            ABBRUCH,
            f"Stand nicht prüfbar: git fetch origin scheiterte in {repo} ({_kurz(aus)}) — "
            "Netz/Zugang prüfen, dann erneut starten. Kein Start mit womöglich altem Stand.",
        )
    code, aus = _git(repo, "rev-list", "--count", f"HEAD..{ziel}")
    if code != 0 or not aus.isdigit():
        return Stand(
            ABBRUCH, f"Stand nicht prüfbar: {ziel} fehlt in {repo} ({_kurz(aus)})."
        )
    hinter = int(aus)
    if hinter == 0:
        return Stand(AKTUELL, f"Arbeitsordner auf Stand von {ziel}", 0)
    vorn = f"Arbeitsordner {repo} liegt {hinter} Commits hinter {ziel}"
    nachzieh_befehl = f"git -C {repo} pull --ff-only origin {haupt}"
    code, zweig = _git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    if code != 0 or zweig != haupt:
        auf = zweig if code == 0 else "keinem Zweig (losgelöster HEAD)"
        return Stand(
            ABBRUCH,
            f"{vorn} und steht auf {auf} statt {haupt}. Tun: git -C {repo} switch {haupt}, "
            f"dann {nachzieh_befehl}, dann erneut starten.",
            hinter,
        )
    code, aenderungen = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if code != 0:
        return Stand(
            ABBRUCH, f"{vorn}; git status scheiterte ({_kurz(aenderungen)}).", hinter
        )
    if aenderungen:
        anzahl = len(aenderungen.splitlines())
        return Stand(
            ABBRUCH,
            f"{vorn} und hat {anzahl} lokal geänderte Dateien — nicht nachgezogen. Tun: "
            f"Änderungen sichern (commit oder stash), dann {nachzieh_befehl}, dann erneut starten.",
            hinter,
        )
    if not nachziehen:
        return Stand(VERALTET, f"{vorn}. Tun: {nachzieh_befehl}", hinter)
    code, aus = _git(repo, "merge", "--ff-only", "--quiet", ziel)
    if code != 0:
        _, voraus = _git(repo, "rev-list", "--count", f"{ziel}..HEAD")
        return Stand(
            ABBRUCH,
            f"{vorn}, eigene Commits voraus: {voraus or '?'} — nicht vorspulbar ({_kurz(aus)}). "
            f"Tun: Historie von Hand klären (nicht rebasen/stashen im geteilten Ordner), dann erneut starten.",
            hinter,
        )
    log.info("%s — %d Commits nachgezogen.", vorn, hinter)
    return Stand(
        NACHGEZOGEN, f"Arbeitsordner {hinter} Commits auf {ziel} nachgezogen", hinter
    )
