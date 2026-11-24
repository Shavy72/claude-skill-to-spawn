"""Fertig einreichen (#411) — der eine Weg, auf dem ein fertiger Stand auf den Hauptzweig kommt.

Schnittstelle: :func:`einreichen` (CLI ``to_spawn.py einreichen <N>``). Bau-Session und Aufseher
rufen denselben Befehl; die Regel, *wie* der Stand ankommt, steht nur hier:

- Aus dem eigenen Ticket-Zweig ``ticket/<N>-…`` (Bau-Session): Stand muss committet sein, auf
  ``origin/<hauptzweig>`` aufsetzen und die Prüf-Tür (``pruef.pruefen``) grün bestehen. Kennt
  die Prüf-Tür keine Sprache im Repo (weder Python noch Node), gibt es nichts zu testen: der
  Stand geht ohne Tests durch, die Meldung sagt es.
- Vom Hauptzweig selbst (Aufseher: Übergabe/Stand-Dateien): nur Commits, die ausschließlich
  ``docs/`` anfassen; sie gehen über den Zweig ``ticket/<N>-uebergabe``. Code gehört in ein Ticket.

Danach wird der Zweig gepusht (nie Force, nie direkt auf den Hauptzweig — die Git-Sperre bleibt
unberührt). Bringt das Projekt eine eigene Push-Sperre samt Einreich-Dienst mit
(``nest.GIT_HOOKS_INSTALLER`` auf ``origin/<hauptzweig>``), endet der Weg dort: der Dienst
übernimmt ``ticket/*``. Sonst öffnet der Befehl einen Pull-Request und mergt ihn sofort mit
Merge-Commit (``--match-head-commit``: nur genau der geprüfte Stand). Commit-SHAs bleiben
erhalten, darum zieht der Hauptzweig vom Aufseher danach per Fast-Forward nach
(lokal == origin). Keine Frage, kein Warten auf einen Takt.

Laufen mehrere Tickets im selben Klon (#476), hält oft ein anderes gerade die Ref-Sperre auf
``origin/<hauptzweig>``: ``_git`` wiederholt dann kurz. Scheitert nur das Nachziehen nach dem
Merge, zählt der echte Pull-Request-Stand auf GitHub — nie ein falsches „nicht eingereicht“.
Bleibt auch diese Stand-Abfrage nach kurzem Wiederholen ohne Antwort (Netz, Zeitlimit,
Rate-Limit, #492), lautet die Meldung „Stand unbekannt – erneut prüfen“ (Exit 2): derselbe
Befehl noch einmal erkennt einen gelungenen Merge (``nichts einzureichen``) und reicht nie doppelt ein.
Das verpasste Nachziehen holt das nächste Einreichen vom Hauptzweig nach: fehlen dem Hauptzweig
lokal nur Merge-Commits (keine fremden Commits), führt es ``origin/<hauptzweig>`` zuerst ein.

Exit-Codes: 0 angekommen bzw. übergeben · 1 rot (Grund in der Meldung, nichts gemergt) ·
2 Stand unbekannt (gemergt oder nicht — denselben Befehl erneut rufen) · 5 Aufruf falsch
(falscher Zweig/Ordner, falsche Argumente #505 — nicht wiederholen, Befehl prüfen). Die Meldungs-Zeile samt Kopf liefert :attr:`Ergebnis.zeile`.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from . import config, gh, nest, pruef, pruefplan, uebergabe_sichern

log = logging.getLogger("to_spawn.einreichen")

OK, ROT, UNBEKANNT, AUFRUF = 0, 1, 2, 5
#: Kopf der Meldungs-Zeile je Exit-Code; alles andere ist rot.
KOPF = {OK: "EINGEREICHT", UNBEKANNT: "Stand unbekannt – erneut prüfen"}
#: Ordner, die der Aufseher vom Hauptzweig aus einreichen darf (Übergaben, Stand-Dateien).
AUFSEHER_ORDNER = ("docs/",)
GIT_MAX_S = 120.0
GH_MAX_S = 120.0
#: Ref-/Index-Sperre eines parallelen git-Aufrufs im selben Klon (#476) ist vorübergehend.
SPERRE_MERKMALE = ("cannot lock ref", ".lock': File exists")
SPERRE_VERSUCHE = 3
SPERRE_PAUSE_S = 1.0

T = TypeVar("T")


@dataclass(frozen=True)
class Ergebnis:
    exit: int
    text: str

    @property
    def zeile(self) -> str:
        """Meldung für Bau-Session/Aufseher: Kopf nach :data:`KOPF`, sonst „NICHT EINGEREICHT“."""
        return f"{KOPF.get(self.exit, 'NICHT EINGEREICHT')}: {self.text}"


class _Rot(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code, self.text = code, text


def _wiederholt(lauf: Callable[[], T], nochmal: Callable[[T], bool], was: str) -> T:
    """``lauf`` bis zu :data:`SPERRE_VERSUCHE`-mal; ``nochmal(ergebnis)`` sagt „vorübergehend gescheitert“.

    Gibt das letzte Ergebnis zurück — ob es gelungen ist, entscheidet der Aufrufer.
    """
    erg = lauf()
    for versuch in range(1, SPERRE_VERSUCHE):
        if not nochmal(erg):
            break
        log.info("einreichen: %s gescheitert (Versuch %d), warte %.1f s", was, versuch, SPERRE_PAUSE_S)
        time.sleep(SPERRE_PAUSE_S)
        erg = lauf()
    return erg


def _git(ordner: Path, *args: str) -> str:
    """git-Aufruf; Sperre eines parallelen Tickets (:data:`SPERRE_MERKMALE`) → kurz wiederholen."""

    def lauf() -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(ordner), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_MAX_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as fehler:
            raise _Rot(ROT, f"git {args[0]} scheiterte: {fehler}") from fehler

    def gesperrt(aus: subprocess.CompletedProcess[str]) -> bool:
        return aus.returncode != 0 and any(m in aus.stderr for m in SPERRE_MERKMALE)

    aus = _wiederholt(lauf, gesperrt, f"git {args[0]} (gesperrt)")
    if aus.returncode != 0:
        raise _Rot(ROT, f"git {' '.join(args[:2])} scheiterte: {aus.stderr.strip()[:300]}")
    return aus.stdout.strip()


def _ist_vorfahr(ordner: Path, a: str, b: str) -> bool:
    erg = subprocess.run(
        ["git", "-C", str(ordner), "merge-base", "--is-ancestor", a, b],
        capture_output=True,
        timeout=GIT_MAX_S,
        check=False,
    )
    return erg.returncode == 0


def _gh(ordner: Path, *args: str) -> str:
    code, aus = gh.lauf(list(args), cwd=ordner, timeout=GH_MAX_S, fehlertext=True)
    if code != 0:
        raise _Rot(ROT, f"gh {' '.join(args[:2])} scheiterte ({code}): {aus[:300]}")
    return aus


def _pr_gemergt(ordner: Path, nr: str) -> bool:
    """Echter Pull-Request-Stand auf GitHub (#476); Abfrage scheitert auch wiederholt → Exit 2 (#492)."""
    args = ["pr", "view", nr, "--json", "state", "--jq", ".state"]
    code, aus = _wiederholt(
        lambda: gh.lauf(args, cwd=ordner, timeout=GH_MAX_S, fehlertext=True),
        lambda erg: erg[0] != 0,
        "gh pr view",
    )
    if code != 0:
        raise _Rot(UNBEKANNT, f"Pull-Request #{nr}: Stand-Abfrage ohne Antwort ({code}): {aus[:300]}")
    return aus.strip() == "MERGED"


def _projekt_weg(ordner: Path, haupt: str) -> bool:
    """Das Projekt bringt eigene Push-Sperre + Einreich-Dienst mit (übernimmt ``ticket/*``)."""
    erg = subprocess.run(
        ["git", "-C", str(ordner), "cat-file", "-e", f"origin/{haupt}:{nest.GIT_HOOKS_INSTALLER.as_posix()}"],
        capture_output=True,
        timeout=GIT_MAX_S,
        check=False,
    )
    return erg.returncode == 0


def _zweig(ordner: Path, nummer: str, haupt: str) -> tuple[str, bool]:
    """(Ziel-Zweig auf origin, vom Hauptzweig?) — nur eigener Ticket-Zweig oder der Hauptzweig."""
    aktuell = _git(ordner, "rev-parse", "--abbrev-ref", "HEAD")
    if aktuell.startswith(f"ticket/{nummer}-") and len(aktuell) > len(f"ticket/{nummer}-"):
        return aktuell, False
    if aktuell == haupt:
        return f"ticket/{nummer}-uebergabe", True
    raise _Rot(
        AUFRUF,
        f"Zweig „{aktuell}“ ist weder ticket/{nummer}-… noch {haupt} — im eigenen Ticket-Worktree "
        "(bzw. als Aufseher im Hauptklon) aufrufen",
    )


def _nur_aufseher_dateien(ordner: Path, haupt: str) -> None:
    pfade = [p for p in _git(ordner, "diff", "--name-only", f"origin/{haupt}...HEAD").splitlines() if p]
    fremd = [p for p in pfade if not p.startswith(AUFSEHER_ORDNER)]
    if fremd:
        raise _Rot(
            ROT,
            f"vom Hauptzweig nur Übergaben unter {', '.join(AUFSEHER_ORDNER)} — Code gehört in ein Ticket: "
            + ", ".join(fremd[:5]),
        )


def _volle_sha(ordner: Path, sha: str) -> str:
    """Volle SHA (40 Hex) für ``--match-head-commit``; kurze wird aufgeweitet, Unauflösbares → ``_Rot`` (#464)."""
    if len(sha) == 40 and all(z in "0123456789abcdef" for z in sha):
        return sha
    try:
        voll = _git(ordner, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    except _Rot:
        voll = ""
    if len(voll) != 40 or any(z not in "0123456789abcdef" for z in voll):
        raise _Rot(AUFRUF, f"SHA „{sha}“ nicht eindeutig auflösbar — für den Merge die volle SHA (40 Zeichen) angeben")
    return voll


def _pull_request(ordner: Path, zweig: str, haupt: str, sha: str) -> str:
    """Offenen Pull-Request für ``zweig`` finden oder anlegen; gibt die Nummer zurück."""
    vorhanden = _gh(ordner, "pr", "list", "--head", zweig, "--base", haupt, "--state", "open",
                    "--json", "number", "--jq", ".[0].number")  # fmt: skip
    if vorhanden.strip():
        return vorhanden.strip()
    titel = _git(ordner, "log", "-1", "--format=%s", sha)
    url = _gh(ordner, "pr", "create", "--base", haupt, "--head", zweig, "--title", titel,
              "--body", "Eingereicht mit `to_spawn.py einreichen` (geprüfter Stand, #411).")  # fmt: skip
    return url.rstrip("/").rsplit("/", 1)[-1]


def _pruefung(ordner: Path, basis: str) -> str:
    """Prüf-Tür für einen Ticket-Stand; gibt einen Hinweis für die Meldung zurück, rot → ``_Rot``.

    Repo ohne Python/Node (reine Doku, Shell, Konfig): es gibt nichts zu testen, also ist das
    kein Werkzeug-Fehler — der Stand geht ohne Tests durch und die Meldung sagt das.

    Welche Prüfung je Ticket dran ist, sagt allein ``pruefplan.welche`` (#425: die schnelle).
    Übergangsregel: Repo nicht eingerichtet (``pruefplan.eingerichtet``) → Leiter wie vor Teil B
    ohne ``art`` plus Warn-Zeile in Log und Protokoll.
    """
    konfig = config.lade(ordner)
    art = pruefplan.welche(konfig, pruefplan.SpecStand(pruefplan.Moment.TICKET))
    if art == "keine":
        return " (keine Prüfung dran)"
    if not pruef.hat_werkzeug(ordner) and not pruefplan.befehl(konfig, art):
        return " (ohne Tests: weder Python noch Node im Repo)"
    if pruefplan.eingerichtet(konfig):
        urteil = pruef.pruefen(ordner, basis, "HEAD", art=art)
    else:
        log.warning("einreichen: %s", pruefplan.NICHT_EINGERICHTET)
        urteil = pruef.pruefen(ordner, basis, "HEAD", warnung=pruefplan.NICHT_EINGERICHTET)
    gelaufen = urteil.zeile.get("art", "alter Ablauf")
    log.info("einreichen: Prüfung %s %.2f s (Exit %s)", gelaufen, urteil.zeile.get("gesamt_s", 0.0), urteil.exit)
    if urteil.exit != 0:
        kurz = urteil.zeile.get("fehler_kurz") or "Prüfung nicht grün"
        raise _Rot(ROT, f"Prüfung rot (Exit {urteil.exit}): {kurz}")
    return ""


def einreichen(ordner: Path, nummer: str) -> Ergebnis:
    """Bringt den fertigen Stand in ``ordner`` für Ticket/Spec ``nummer`` auf den Hauptzweig.

    Wirft nie: jeder Abbruch (auch Werkzeug kaputt, Zeitüberschreitung) kommt als ``Ergebnis``
    mit Exit 1, 2 bzw. 5 zurück, damit Aufrufer nur ``exit`` lesen.
    """
    try:
        return _einreichen(ordner, str(nummer).lstrip("#"))
    except _Rot as rot:
        log.warning("einreichen #%s: %s", nummer, rot.text)
        return Ergebnis(rot.code, rot.text)
    except (OSError, subprocess.TimeoutExpired) as fehler:
        text = f"Werkzeug brach ab ({type(fehler).__name__}): {str(fehler)[:300]}"
        log.warning("einreichen #%s: %s", nummer, text)
        return Ergebnis(ROT, text)


def _einreichen(ordner: Path, nummer: str) -> Ergebnis:
    if not nummer.isdigit():
        raise _Rot(AUFRUF, f"Nummer „{nummer}“ ist keine Ticket-/Spec-Nummer")
    haupt = gh.hauptzweig(ordner)
    zweig, vom_haupt = _zweig(ordner, nummer, haupt)
    _git(ordner, "fetch", "--quiet", "origin", haupt)
    basis = f"origin/{haupt}"
    sha = _git(ordner, "rev-parse", "HEAD")
    if _ist_vorfahr(ordner, sha, basis):
        return Ergebnis(OK, f"nichts einzureichen: {sha[:7]} ist schon auf {basis}")
    if vom_haupt and not _ist_vorfahr(ordner, basis, sha) and not _git(ordner, "rev-list", "--no-merges", f"{sha}..{basis}"):
        # #476: nur Merge-Commits eigener, schon gemergter Einreichungen fehlen (Nachziehen verpasst)
        try:
            _git(ordner, "merge", "--no-edit", "--quiet", basis)
        except _Rot:
            # Konflikt: keinen halben Merge im geteilten Klon liegen lassen
            subprocess.run(
                ["git", "-C", str(ordner), "merge", "--abort"], capture_output=True, timeout=GIT_MAX_S, check=False
            )
            raise
        sha = _git(ordner, "rev-parse", "HEAD")
    if not _ist_vorfahr(ordner, basis, sha):
        raise _Rot(ROT, f"{basis} ist weiter: erst {basis} einarbeiten, dann erneut einreichen")
    gesperrt = uebergabe_sichern.gesperrte_pfade(ordner, "HEAD", "--not", "--remotes=origin")
    if gesperrt:
        raise _Rot(ROT, f"enthält Dateien, die nie hochgeladen werden: {', '.join(gesperrt)}")
    if vom_haupt:
        _nur_aufseher_dateien(ordner, haupt)
        hinweis = ""
    else:
        hinweis = _pruefung(ordner, basis)
    _git(ordner, "push", "--quiet", "origin", f"{sha}:refs/heads/{zweig}")
    if _projekt_weg(ordner, haupt):
        return Ergebnis(OK, f"{zweig} ({sha[:7]}) gepusht{hinweis} — der Einreich-Dienst des Projekts übernimmt")
    nr = _pull_request(ordner, zweig, haupt, sha)
    _gh(ordner, "pr", "merge", nr, "--merge", "--match-head-commit", _volle_sha(ordner, sha))
    try:
        _git(ordner, "fetch", "--quiet", "origin", haupt)
    except _Rot as rot:  # #476: Nachziehen scheitert (Sperre) — der Pull-Request-Stand entscheidet
        if not _pr_gemergt(ordner, nr):
            raise
        log.warning("einreichen #%s: %s — Pull-Request #%s ist gemergt", nummer, rot.text, nr)
        return Ergebnis(OK, f"{sha[:7]} gemergt (Pull-Request #{nr}, {zweig}){hinweis} — {basis} lokal nicht nachgezogen")
    if not _ist_vorfahr(ordner, sha, basis):
        raise _Rot(ROT, f"Pull-Request #{nr} gemeldet, aber {sha[:7]} fehlt auf {basis}")
    if vom_haupt:
        _git(ordner, "merge", "--ff-only", "--quiet", basis)
    return Ergebnis(OK, f"{sha[:7]} ist auf {basis} (Pull-Request #{nr}, {zweig}){hinweis}")
