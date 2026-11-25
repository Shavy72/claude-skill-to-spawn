"""Arbeitsordner auf Hauptzweig-Stand vor dem Start (#283).

Der Start einer Bau-Session liest Manifest und Hilfs-Skripte aus dem Arbeitsordner des
Repos (die Vorlage ``_default.json`` liegt seit #274 im Skill unter ``repo-scripts/``).
Liegt der hinter ``origin/<Hauptzweig>``,
liefe die Session still mit altem Stand (Fall PC: 5839 Commits zurück). Dieses Modul
stellt den Stand sicher, bevor irgendetwas davon gelesen wird.

Zwei Türen:

- :func:`hinweis` — Start und ``--resume`` jeder Bau-Session (``skripte/bau.py``): nur prüfen,
  liefert höchstens eine Hinweis-Zeile oder ``None``, wirft nie, bewegt nie HEAD/Arbeitsbaum.
  Eine laufende Bau-Session bleibt an dieser Prüfung nie stehen.
- :func:`sicherstellen` — nur der Erst-Start (``to_spawn.py arbeitsstand`` aus
  ``spawn_srv.sh``/``spawn_local.ps1``) zieht nach oder bricht ab; ``nachziehen=False`` = nur
  prüfen (Probelauf, ``einrichten --pruefen``). Ergebnis :class:`Stand` mit ``zustand``:

- ``aktuell``     — nicht hinter ``origin/<Hauptzweig>`` (eigene Commits voraus sind ok).
- ``nachgezogen`` — war N Commits zurück, per fast-forward nachgezogen.
- ``hinweis``     — Start geht weiter, ``text`` warnt: Stand nicht prüfbar (fetch/Netz/kein
  ``origin``; der Text nennt das Alter des letzten erfolgreichen fetch) oder N Commits
  zurück auf anderem Zweig bzw. losgelöstem HEAD (ein Bau-Klon
  steht regelmäßig auf einem Ticket-Zweig) — dann wird nichts nachgezogen.
- ``veraltet``    — N Commits zurück, nur geprüft (``nachziehen=False``).
- ``abbruch``     — N Commits zurück auf dem Hauptzweig und nicht nachziehbar (eigene Commits
  voraus, git würde eine lokal geänderte oder unversionierte Datei überschreiben), kein
  git-Arbeitsordner oder Sperre belegt. ``text`` nennt Zahl, Grund und was zu tun ist
  (Befehle mit Pfad in doppelten Anführungszeichen).

Nachzieh-Regel: ``git merge --ff-only origin/<Hauptzweig>``, keine eigene Sauberkeits-Prüfung
davor. Lokale Änderungen an Dateien, die die neuen Commits nicht berühren (z. B. ein früher
an die getrackte ``.gitignore`` gehängter Block), bleiben unangetastet und hindern nicht;
würde eine lokal geänderte oder unversionierte Datei überschrieben, verweigert git selbst
und nichts bewegt sich. Nie reset/stash/rebase/checkout; autostash aus (``--no-autostash``),
kein Login-Prompt (``GIT_TERMINAL_PROMPT=0``).

Auch „nur prüfen“ führt ``git fetch origin`` aus (ändert nur Remote-Refs, ohne ihn wäre die
Zahl falsch) und wartet nur kurz (:data:`SPERRE_WARTEN_PRUEFEN_S`) auf die Sperre.

Versteckt wird hier: Name des Hauptzweigs (über :func:`to_spawn.gh.hauptzweig`), wie
„hinter“ gemessen wird (``rev-list --count HEAD..origin/<Hauptzweig>``), fetch, die
Nachzieh-Regel (oben) und die
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
import time
from dataclasses import dataclass
from pathlib import Path

from to_spawn import gh, prozessbaum, sperre, starter

log = logging.getLogger("to_spawn.arbeitsstand")

AKTUELL, NACHGEZOGEN, HINWEIS, VERALTET, ABBRUCH = (
    "aktuell",
    "nachgezogen",
    "hinweis",
    "veraltet",
    "abbruch",
)
TEST_OK_ENV = "TO_SPAWN_TEST_ARBEITSSTAND_OK"
#: Zeitlimits (Sekunden): fetch braucht Netz, lokale git-Aufrufe nicht.
FETCH_TIMEOUT_S = 120.0
#: Nur prüfen (Bau-Session, Probelauf): fetch darf den Start nicht lange aufhalten.
FETCH_TIMEOUT_PRUEFEN_S = 20.0
GIT_TIMEOUT_S = 30.0
#: So lange wartet ein paralleler Start auf die Sperre, bevor er abbricht.
SPERRE_WARTEN_S = 300.0
#: Nur prüfen (Probelauf, ``einrichten --pruefen``): nicht minutenlang hängen.
SPERRE_WARTEN_PRUEFEN_S = 10.0


@dataclass(frozen=True)
class Stand:
    zustand: str
    text: str
    hinter: int | None = None

    @property
    def ok(self) -> bool:
        """Darf gestartet werden? Bei ``aktuell``/``nachgezogen``/``hinweis``."""
        return self.zustand in (AKTUELL, NACHGEZOGEN, HINWEIS)


def _git(repo: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> tuple[int, str]:
    """``git <args>`` in ``repo`` → (Exit, stdout bzw. stderr bei Fehler); 127/124 = fehlt/Zeit."""
    try:
        fertig = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=str(repo),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **prozessbaum.fensterlos(),
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
    return (pfad if pfad.is_absolute() else repo / pfad) / "to-spawn-arbeitsstand.sperre"


def hinweis(repo: Path) -> str | None:
    """Nur prüfen (Start/``--resume`` einer Bau-Session): Hinweis-Text oder ``None`` bei aktuell.

    Bewegt nie HEAD oder Arbeitsbaum (nur ``git fetch``, ändert Remote-Refs), wirft nie.
    """
    try:
        stand = sicherstellen(repo, nachziehen=False)
    except Exception as fehler:  # noqa: BLE001 — die Prüfung darf keine Bau-Session anhalten
        return f"Stand nicht prüfbar: {fehler}"
    return None if stand.zustand == AKTUELL else stand.text


def sicherstellen(repo: Path, *, nachziehen: bool = True) -> Stand:
    """Stellt sicher, dass ``repo`` nicht hinter ``origin/<Hauptzweig>`` liegt (siehe Modul-Text)."""
    if os.environ.get(TEST_OK_ENV) == "1" and starter.unter_test():
        return Stand(AKTUELL, "Stand-Check im Test übersprungen", 0)
    datei = _sperr_datei(repo)
    if datei is None:
        return Stand(ABBRUCH, f"Stand nicht prüfbar: {repo} ist kein git-Arbeitsordner.")
    warten = SPERRE_WARTEN_S if nachziehen else SPERRE_WARTEN_PRUEFEN_S
    try:
        with sperre.halte(datei, warten=warten) as gehalten:
            if not gehalten:
                return Stand(
                    ABBRUCH,
                    f"Stand nicht prüfbar: ein anderer Start zieht {repo} seit über "
                    f"{warten:.0f} s nach — prüfen, ob git hängt, dann erneut starten.",
                )
            return _pruefen_und_nachziehen(repo, nachziehen)
    except OSError as fehler:
        return Stand(ABBRUCH, f"Stand nicht prüfbar: Sperre {datei} nicht nutzbar ({fehler}).")


def _fetch_alter(repo: Path) -> str:
    """Alter des letzten erfolgreichen fetch (Änderungszeit von ``FETCH_HEAD``) als Text."""
    code, ort = _git(repo, "rev-parse", "--git-path", "FETCH_HEAD")
    pfad = Path(ort) if Path(ort).is_absolute() else repo / ort
    try:
        sekunden = max(0.0, time.time() - pfad.stat().st_mtime) if code == 0 else None
    except OSError:
        sekunden = None
    if sekunden is None:
        return "letzter erfolgreicher fetch unbekannt"
    if sekunden < 3600:
        return f"letzter erfolgreicher fetch vor {int(sekunden // 60)} Min."
    if sekunden < 48 * 3600:
        return f"letzter erfolgreicher fetch vor {int(sekunden // 3600)} Std."
    return f"letzter erfolgreicher fetch vor {int(sekunden // 86400)} Tagen"


def _pruefen_und_nachziehen(repo: Path, nachziehen: bool) -> Stand:
    frist = FETCH_TIMEOUT_S if nachziehen else FETCH_TIMEOUT_PRUEFEN_S
    alter = _fetch_alter(repo)  # vor dem fetch lesen: ein erfolgreicher fetch setzt die Zeit neu
    code, aus = _git(repo, "fetch", "--quiet", "origin", timeout=frist)
    if code != 0:
        return Stand(
            HINWEIS,
            f"Stand nicht prüfbar: git fetch origin scheiterte in {repo} ({_kurz(aus)}, {alter}) — "
            "Start mit vorhandenem Stand; Netz/Zugang prüfen.",
        )
    # Erst nach dem fetch: ein frischer Klon hat vorher weder origin/HEAD noch origin/<Hauptzweig>.
    haupt = gh.hauptzweig(repo)
    ziel = f"origin/{haupt}"
    code, aus = _git(repo, "rev-list", "--count", f"HEAD..{ziel}")
    if code != 0 or not aus.isdigit():
        return Stand(
            HINWEIS,
            f"Stand nicht prüfbar: {ziel} fehlt in {repo} ({_kurz(aus)}) — Start mit vorhandenem Stand.",
        )
    hinter = int(aus)
    if hinter == 0:
        return Stand(AKTUELL, f"Arbeitsordner auf Stand von {ziel}", 0)
    vorn = f"Arbeitsordner {repo} liegt {hinter} Commits hinter {ziel}"
    nachzieh_befehl = f'git -C "{repo}" pull --ff-only origin {haupt}'
    code, zweig = _git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    if code != 0 or zweig != haupt:
        auf = zweig if code == 0 else "keinem Zweig (losgelöster HEAD)"
        return Stand(
            HINWEIS,
            f"{vorn} und steht auf {auf} statt {haupt} — nicht nachgezogen. Falls nötig: "
            f'git -C "{repo}" switch {haupt}, dann {nachzieh_befehl}.',
            hinter,
        )
    if not nachziehen:
        code, voraus = _git(repo, "rev-list", "--count", f"{ziel}..HEAD")
        if code == 0 and voraus.isdigit() and int(voraus) > 0:  # ff-Rat würde scheitern
            tun = f"eigene Commits voraus: {voraus} — nicht vorspulbar, Historie von Hand klären"
            return Stand(VERALTET, f"{vorn}, {tun}.", hinter)
        return Stand(VERALTET, f"{vorn}. Tun: {nachzieh_befehl}", hinter)
    code, aus = _git(repo, "merge", "--ff-only", "--no-autostash", "--quiet", ziel)
    if code != 0:
        return Stand(ABBRUCH, _merge_abbruch(repo, ziel, vorn, nachzieh_befehl, aus), hinter)
    log.info("%s — %d Commits nachgezogen.", vorn, hinter)
    return Stand(NACHGEZOGEN, f"Arbeitsordner {hinter} Commits auf {ziel} nachgezogen", hinter)


def _merge_abbruch(repo: Path, ziel: str, vorn: str, nachzieh_befehl: str, meldung: str) -> str:
    """Abbruch-Text nach gescheitertem ``merge --ff-only``: Grund aus Historie bzw. git-Meldung."""
    code, voraus = _git(repo, "rev-list", "--count", f"{ziel}..HEAD")
    if code == 0 and voraus.isdigit() and int(voraus) > 0:
        return (
            f"{vorn}, eigene Commits voraus: {voraus} — nicht vorspulbar. Tun: Historie von Hand "
            "klären (nicht rebasen/stashen im geteilten Ordner), dann erneut starten."
        )
    # git listet betroffene Dateien eingerückt (Tab) — unabhängig von der Sprache der Meldung.
    dateien = [z.strip() for z in meldung.splitlines() if z.startswith("	") and z.strip()]
    if dateien:
        liste = ", ".join(dateien[:5]) + (f" (+{len(dateien) - 5} weitere)" if len(dateien) > 5 else "")
        return (
            f"{vorn} — nicht vorspulbar: Nachziehen würde lokal geänderte oder unversionierte "
            f"Dateien überschreiben: {liste}. Tun: diese Dateien sichern (committen oder "
            f"wegkopieren und zurücksetzen), dann {nachzieh_befehl}, dann erneut starten."
        )
    return (
        f"{vorn} — nicht vorspulbar ({_kurz(meldung)}). Tun: Ursache beheben, dann "
        f"{nachzieh_befehl}, dann erneut starten."
    )
