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

Exit-Codes: 0 angekommen bzw. übergeben · 1 rot (Grund in der Meldung, nichts gemergt) ·
5 Aufruf falsch (falscher Zweig/Ordner).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config, gh, nest, pruef, pruefplan, uebergabe_sichern

log = logging.getLogger("to_spawn.einreichen")

OK, ROT, AUFRUF = 0, 1, 5
#: Ordner, die der Aufseher vom Hauptzweig aus einreichen darf (Übergaben, Stand-Dateien).
AUFSEHER_ORDNER = ("docs/",)
GIT_MAX_S = 120.0
GH_MAX_S = 120.0


@dataclass(frozen=True)
class Ergebnis:
    exit: int
    text: str


class _Rot(Exception):
    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code, self.text = code, text


def _git(ordner: Path, *args: str) -> str:
    try:
        aus = subprocess.run(
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
    """
    konfig = config.lade(ordner)
    art = pruefplan.welche(konfig, pruefplan.SpecStand(pruefplan.Moment.TICKET))
    if art == "keine":
        return " (keine Prüfung dran)"
    if not pruef.hat_werkzeug(ordner) and not pruefplan.befehl(konfig, art):
        return " (ohne Tests: weder Python noch Node im Repo)"
    urteil = pruef.pruefen(ordner, basis, "HEAD", art=art)
    log.info("einreichen: Prüfung %s %.2f s (Exit %s)", art, urteil.zeile.get("gesamt_s", 0.0), urteil.exit)
    if urteil.exit != 0:
        kurz = urteil.zeile.get("fehler_kurz") or "Prüfung nicht grün"
        raise _Rot(ROT, f"Prüfung rot (Exit {urteil.exit}): {kurz}")
    return ""


def einreichen(ordner: Path, nummer: str) -> Ergebnis:
    """Bringt den fertigen Stand in ``ordner`` für Ticket/Spec ``nummer`` auf den Hauptzweig.

    Wirft nie: jeder Abbruch (auch Werkzeug kaputt, Zeitüberschreitung) kommt als ``Ergebnis``
    mit Exit 1 bzw. 5 zurück, damit Aufrufer nur ``exit`` lesen.
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
    _gh(ordner, "pr", "merge", nr, "--merge", "--match-head-commit", sha)
    _git(ordner, "fetch", "--quiet", "origin", haupt)
    if not _ist_vorfahr(ordner, sha, basis):
        raise _Rot(ROT, f"Pull-Request #{nr} gemeldet, aber {sha[:7]} fehlt auf {basis}")
    if vom_haupt:
        _git(ordner, "merge", "--ff-only", "--quiet", basis)
    return Ergebnis(OK, f"{sha[:7]} ist auf {basis} (Pull-Request #{nr}, {zweig}){hinweis}")
