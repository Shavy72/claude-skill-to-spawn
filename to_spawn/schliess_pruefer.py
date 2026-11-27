"""Schließ-Prüfer: die eine Stelle für die festen Fertig- und Schließ-Regeln (#864 / E19, E21, E50, E51).

Schmale Tür: :func:`pruefe` nimmt einen :class:`Stand` (Repo, Ticket, Ref/Hauptzweig,
optional Grün-Nachweis) und liefert ein :class:`PruefErgebnis` (``ok`` oder strukturierte
:class:`Grund`-Einträge).

Feste Regeln (alle nur hier, keine Kopie in ``capo`` oder Aufrufern):

1. ``commit_ohne_nummer`` — mindestens ein Commit trägt die Ticketnummer ``(#N)`` am Ende
   oder als Scope ``typ(#N):`` (ohne ``docs(#N):``).
2. ``beweis_fehlt`` / ``beleg_form`` — Belegseite liegt auf dem Hauptzweig im Beleg-Ordner
   und erfüllt die feste Form (Pflichtfelder ``Ticket``, ``Commit-Stand``, ``Staging-Adresse``,
   ``Rolle``, ``Datum``; Abschnitt ``Staging-Klickweg`` ohne Platzhalter mit vorhandenem,
   nicht-leerem Desktop- und Handy-Bild). Reine Doku-Tickets außerhalb des Beleg-Ordners
   brauchen keine Belegseite (E4).
3. ``test_ersetzt`` — kein Ticket-Commit entfernt Tests oder Testdateien im Netto-Vergleich
   (bewusste Umbenennung nur mit Vermerk ``test-umbenannt:`` in derselben Datei; git-Fehler
   gelten als „nicht prüfbar“, nie als sauber).
4. ``nicht_gruen_auf_hauptzweig`` — der jüngste Ticket-Commit ist durch einen grünen
   Hauptzweig-Stand belegt (direkter SHA/Flag oder Ticket-Kommentar „grün auf master/main“
   mit vollem 40-Zeichen-SHA).

„Server nicht aktuell“ (``vps_ungleich_origin``) ist bewusst KEINE Regel dieses Moduls (E21).
"""

from __future__ import annotations

import logging
import posixpath
import re
import struct
import subprocess
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from . import config, gh, prozessbaum

log = logging.getLogger("to_spawn.schliess_pruefer")

#: Typ der Git-Aufruf-Hilfe: ``(repo, *args) -> (exit_code, stdout)``.
GitLauf = Callable[..., tuple[int, str]]

#: Beleg-Ordner, wenn ``regularien.belege_ordner`` in der Repo-Konfig fehlt.
BELEGE_ORDNER = "docs/verify-hard"

#: Regeln, die capo beim Prüfen geschlossener Tickets über diese Tür abruft.
CAPO_REGELN: frozenset[str] = frozenset({"commit_ohne_nummer", "beweis_fehlt", "test_ersetzt"})

#: Alle Schließ-Regeln dieses Prüfers — bewusst ohne „Server nicht aktuell“ (``vps_ungleich_origin``).
SCHLIESS_REGELN: frozenset[str] = frozenset(
    {
        "commit_ohne_nummer",
        "beweis_fehlt",
        "beleg_form",
        "test_ersetzt",
        "nicht_gruen_auf_hauptzweig",
    }
)

_TEST_DATEI = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|[^/]*\.(test|spec)\.[cm]?[jt]sx?$)")
#: Kopien von Tests (Belege, Archiv, Mutanten) sind keine Testdateien.
_KEINE_TESTDATEI = re.compile(r"(^|/)(docs|archive)/|(^|/)mutants/")
_PY_TEST = re.compile(r"^-[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)", re.MULTILINE)
_JS_TEST = re.compile(r"""^-[ \t]*(?:it|test)\([ \t]*(["'`])(.+?)\1""", re.MULTILINE)
#: Vermerk im Commit-Text für eine bewusste Test-Umbenennung (#448), z. B.
#: ``test-umbenannt: tests/t.py::test_alt -> tests/t.py::test_neu``.
_UMBENANNT = re.compile(r"(?im)^[ \t]*test-umbenannt[ \t]*:(.*)$")
_PFEIL = re.compile(r"[ \t]*(?:→|->|=>)[ \t]*")
_PY_NAME = re.compile(r"test_\w+")
#: Pfad vor ``::`` im Vermerk: ``test-umbenannt: tests/a.py::test_alt -> test_neu`` (#1205).
_VERMERK_PFAD = re.compile(r"([^\s:]+\.py)::")
#: JS-Testname in Anführungszeichen im Vermerk: ``test-umbenannt: 'alt' -> 'neu'``.
_ZITAT = re.compile(r"""(["'`])(.+?)\1""")
_DATUM_PFAD = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Beleg-Form: Rot-Beweis-Dateien sind keine Haupt-Belegseiten.
_ROT_DATEI = re.compile(r"(?:^|[_\-/.])rot(?:[_\-/.]|$)", re.IGNORECASE)
_BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".webp")
_SHA_KURZ = re.compile(r"\b([0-9a-fA-F]{7,40})\b")
_SHA_VOLL = re.compile(r"\b([0-9a-fA-F]{40})\b")
_DATUM_WERT = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b")
_URL_WERT = re.compile(r"https?://[^\s<>)\]`'\"]+", re.IGNORECASE)
_KLICKWEG_KOPF = re.compile(r"^(#{1,6})\s+(?:staging[\s-]*)?klickweg\b", re.IGNORECASE)
_UEBERSCHRIFT = re.compile(r"^(#{1,6})\s+")
_MD_BILD = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
_INLINE_BILD = re.compile(r"`([^`\n]+\.(?:png|jpe?g|webp))`", re.IGNORECASE)
_PFAD_BILD = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:png|jpe?g|webp))(?![\w/.-])", re.IGNORECASE)
_DESKTOP_WORT = re.compile(r"\b(?:desktop|wide|pc|1280|1440|1920)\b", re.IGNORECASE)
_HANDY_WORT = re.compile(r"\b(?:handy|mobil\w*|mobile|phone|390x844|390)\b", re.IGNORECASE)
_OFFEN_VERNEINT = re.compile(r"\b(?:0|null|nichts(?: mehr)?|keine(?: \w+)?|nix)\s+offen\b", re.IGNORECASE)
_PLATZHALTER = re.compile(
    r"(?:"
    r"\bfolgt\b"
    r"|\b(?:noch\s+)?nicht\s+geprüft\b"
    r"|\b(?:noch\s+)?nicht\s+geprueft\b"
    r"|\b(?:todo|tbd|ausstehend|platzhalter)\b"
    r"|\boffen\b"
    r"|⬜"
    r"|(?:^|\s)-\s*\[\s\]"
    r"|\?\?\?"
    r"|<[^>\n]{1,40}>"
    r")",
    re.IGNORECASE,
)
_FELD_LEER_PLATZHALTER = re.compile(r"^(?:[-—–_.*]+|n/?a|\.{2,})$", re.IGNORECASE)
_GRUEN_KOMMENTAR = re.compile(r"\bgr(?:ü|ue)n\s+auf\s+(?:master|main|hauptzweig)\b", re.IGNORECASE)
_LOKAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


@dataclass
class Commit:
    sha: str
    betreff: str
    zeit: int


@dataclass
class Grund:
    """Strukturierter Ablehnungsgrund des Schließ-Prüfers."""

    regel: str
    text: str
    kern: str = ""
    details: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.regel}: {self.text}"


@dataclass
class PruefErgebnis:
    """Antwort der Schließ-Prüfer-Tür :func:`pruefe`."""

    ok: bool
    gruende: list[Grund] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class Stand:
    """Eingabe der schmalen Tür :func:`pruefe`."""

    repo: Path
    ticket: int
    ref: str = "HEAD"
    belege_ordner: str | None = None
    eigene: list[Commit] | None = None
    #: Hauptzweig-Ref (z. B. ``origin/master``), auf dem Belegseite und grüner Stand liegen müssen.
    #: Fehlt er, gilt ``ref``.
    haupt_ref: str | None = None
    #: Nachweis „Commit grün auf Hauptzweig“: ``True``, Commit-SHA (``str``) oder ``False``/``None``.
    gruen_auf_hauptzweig: bool | str | None = None
    #: Ticket-Kommentare (Texte) als alternative Quelle für das Grün-Signal „grün auf master/main“.
    kommentare: Sequence[str] | None = None
    #: Pflichtprüfung „Commit grün auf Hauptzweig“ aktiv (Vorgabe: ``True`` beim Schließen).
    gruen_pruefen: bool = True
    #: Feste Form der Belegseite prüfen (Pflichtfelder, Klickweg-Abschnitt, Desktop-/Handy-Bild).
    form_pruefen: bool = True
    #: Belegdateien müssen am geprüften Hauptzweig liegen (nicht nur in älteren Commits).
    beleg_auf_hauptzweig: bool = True
    #: Abschnitt Staging-Klickweg mit Desktop- und Handy-Bild verlangen (Vorgabe: ``True``).
    klickweg_pflicht: bool = True
    #: Optionale Git-Lauf-Funktion (für bestehende Aufrufer wie ``capo._git``).
    git_lauf: GitLauf | None = None


class _NichtPruefbar(Exception):
    """Testverlust eines Commits nicht feststellbar (git-Fehler/Zeitablauf, #1210)."""


# --- Git ------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """``git <args>`` mit Zeitgrenze (``gh.FETCH_ZEIT_S`` bzw. ``gh.GIT_ZEIT_S``); Zeitablauf = Exit 124."""
    grenze = gh.FETCH_ZEIT_S if args[:1] == ("fetch",) else gh.GIT_ZEIT_S
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=grenze,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s nach %s s abgebrochen.", args[:2], grenze)
        return 124, ""
    except OSError as fehler:
        log.warning("git %s gescheitert: %s", args[:2], fehler)
        return 127, ""
    return fertig.returncode, fertig.stdout.rstrip("\n")


def _git_bytes(repo: Path, *args: str) -> tuple[int, bytes]:
    """``git <args>`` binär (für Bilder aus ``git show <ref>:<pfad>``)."""
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            timeout=gh.GIT_ZEIT_S,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s (binär) nach %s s abgebrochen.", args[:2], gh.GIT_ZEIT_S)
        return 124, b""
    except OSError as fehler:
        log.warning("git %s (binär) gescheitert: %s", args[:2], fehler)
        return 127, b""
    return fertig.returncode, fertig.stdout


def _git_wiederholen(repo: Path, *args: str, git_lauf: GitLauf | None = None) -> tuple[int, str]:
    """Git-Aufruf mit genau einer Wiederholung bei Zeitablauf (Exit 124, Infrastruktur, #861)."""
    lauf = git_lauf or _git
    code, text = lauf(repo, *args)
    if code == 124:
        log.warning("git %s Zeitablauf (Exit 124) — einmaliger Wiederholungsversuch", args[:2])
        code, text = lauf(repo, *args)
    return code, text


def _nicht_pruefbar_grund(befehl: str, code: int) -> str:
    """Fehlertext für :class:`_NichtPruefbar`; Exit 124 wird als Infrastruktur gekennzeichnet (#861)."""
    if code == 124:
        return f"{befehl} Zeitablauf (Infrastruktur, Exit 124)"
    return f"{befehl} Exit {code}"


def alle_commits(repo: Path, ref: str, *, git_lauf: GitLauf | None = None) -> list[Commit]:
    lauf = git_lauf or _git
    code, text = lauf(repo, "log", ref, "--format=%H%x1f%s%x1f%ct")
    if code != 0:
        return []
    commits: list[Commit] = []
    for zeile in text.splitlines():
        teile = zeile.split("\x1f")
        if len(teile) == 3 and teile[2].isdigit():
            commits.append(Commit(teile[0], teile[1], int(teile[2])))
    return commits


def betreff_hat_nummer(betreff: str, ticket: int) -> bool:
    """Betreff endet mit ``(#N)`` (optional `` [skip ci]``) oder beginnt mit ``typ(#N):`` (E1).

    ``docs(#N): …`` zählt nicht: so heißen Nachträge (Aufseher, Handoff), nicht der Bau.
    """
    if re.search(rf"\(#{ticket}\)(?: \[skip ci\])?\s*$", betreff):
        return True
    treffer = re.match(rf"([a-z]+)\(#{ticket}\)!?:", betreff)
    return treffer is not None and treffer.group(1) != "docs"


def ticket_commits(commits: list[Commit], ticket: int) -> list[Commit]:
    return [c for c in commits if betreff_hat_nummer(c.betreff, ticket)]


def _dateien(
    repo: Path,
    sha: str,
    *,
    streng: bool = False,
    git_lauf: GitLauf | None = None,
) -> list[tuple[str, str]]:
    """(Status, Pfad) je Datei eines Commits, Umbenennungen als Status ``R`` mit neuem Pfad.

    Mit ``streng=True`` (Testverlust-Pfad, #861) wird bei Exit 124 einmal wiederholt und jeder
    git-Fehler mit Warnung als :class:`_NichtPruefbar` gemeldet statt still ``[]`` zurückzugeben.
    """
    if streng:
        code, text = _git_wiederholen(repo, "show", "--format=", "--name-status", "-M", sha, git_lauf=git_lauf)
    else:
        lauf = git_lauf or _git
        code, text = lauf(repo, "show", "--format=", "--name-status", "-M", sha)
    if code != 0:
        if streng:
            grund = _nicht_pruefbar_grund("git show --name-status", code)
            log.warning("git show --name-status %s gescheitert (%s) — Testverlust nicht prüfbar", sha[:7], grund)
            raise _NichtPruefbar(grund)
        return []
    ergebnis: list[tuple[str, str]] = []
    for zeile in text.splitlines():
        teile = zeile.split("\t")
        if len(teile) >= 2:
            ergebnis.append((teile[0][:1], teile[-1]))
    return ergebnis


def _spitze_commit(eigene: list[Commit]) -> Commit | None:
    """Jüngster Ticket-Commit; ``eigene`` kommt aus ``git log`` (neueste zuerst)."""
    if not eigene:
        return None
    return min(enumerate(eigene), key=lambda p: (-p[1].zeit, p[0]))[1]


# --- Regel 1: Ticketnummer im Commit --------------------------------------------


def regel_commit(ticket: int, eigene: list[Commit]) -> Grund | None:
    if eigene:
        return None
    return Grund(
        regel="commit_ohne_nummer",
        text=f"kein Commit auf dem Hauptzweig endet mit „(#{ticket})“",
    )


# --- Regel 2: Belegseite vorhanden & in fester Form -----------------------------


def belege_ordner(konfig: Mapping[str, Any]) -> str:
    """Beleg-Ordner aus ``regularien.belege_ordner`` (ohne Schrägstriche am Rand), sonst Vorgabe."""
    regularien = konfig.get("regularien") if isinstance(konfig.get("regularien"), Mapping) else {}
    return str(regularien.get("belege_ordner") or BELEGE_ORDNER).replace("\\", "/").strip("/") or BELEGE_ORDNER


def beleg_passt(pfad: str, ticket: int) -> bool:
    """Nummer als eigenes Zahlwort im Pfad unterhalb des Beleg-Ordners (Datei oder Ordner).

    Belegseiten liegen als Datei (``213_gruen.txt``) oder als Ordner
    (``2026-09-18_203_staffel/BEWEIS.md``); ein Datum im Namen zählt nie als Nummer.
    """
    ohne_datum = _DATUM_PFAD.sub("-", pfad)
    return re.search(rf"(^|\D){ticket}(\D|$)", ohne_datum) is not None


def belegseiten(
    repo: Path,
    ticket: int,
    *,
    ref: str = "HEAD",
    ordner: str | None = None,
    git_lauf: GitLauf | None = None,
) -> list[str]:
    """Belegseiten eines Tickets: Repo-Pfade unter dem Beleg-Ordner am Stand ``ref`` (``git ls-tree``).

    ``ordner`` fehlt → aus der Repo-Konfig. Git nicht lesbar → leere Liste (wie „kein Beleg“).
    """
    ordner = (ordner if ordner is not None else belege_ordner(config.lade(repo))).replace("\\", "/").strip(
        "/"
    ) or BELEGE_ORDNER
    lauf = git_lauf or _git
    code, text = lauf(repo, "ls-tree", "-r", "--name-only", ref, "--", ordner)
    namen = text.splitlines() if code == 0 else []
    return [pfad for pfad in namen if beleg_passt(pfad[len(ordner) + 1 :], ticket)]


def ist_doku(pfad: str) -> bool:
    return pfad.startswith("docs/") or pfad.lower().endswith(".md")


def _nur_doku(repo: Path, sha: str, *, git_lauf: GitLauf | None = None) -> bool:
    dateien = _dateien(repo, sha, git_lauf=git_lauf)
    return bool(dateien) and all(ist_doku(pfad) for _, pfad in dateien)


def _ist_platzhalter_text(text: str) -> bool:
    """Enthält ``text`` einen Platzhalter (z. B. ``folgt``, ``TODO``, ``nicht geprüft``, ``⬜``)?"""
    bereinigt = _OFFEN_VERNEINT.sub("", text).strip()
    if not bereinigt or _FELD_LEER_PLATZHALTER.fullmatch(bereinigt):
        return True
    return _PLATZHALTER.search(bereinigt) is not None


def _kopffeld_werte(text: str) -> dict[str, str]:
    """Liest ``Schlüssel: Wert``-Zeilen (auch als Liste, fett oder Tabelle) aus einer Markdown-Belegseite."""
    felder: dict[str, str] = {}
    in_code = False
    for roh in text.splitlines():
        zeile = roh.strip()
        if zeile.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not zeile:
            continue
        # Tabellenzeile: | Schlüssel | Wert |
        if zeile.startswith("|") and zeile.count("|") >= 2:
            zellen = [z.strip().strip("*`_ ") for z in zeile.strip("|").split("|")]
            if len(zellen) >= 2 and zellen[0] and not set(zellen[0]) <= {"-", ":"}:
                schluessel = zellen[0].rstrip(":").strip().lower()
                wert = zellen[1].strip()
                if schluessel and wert and schluessel not in felder:
                    felder[schluessel] = wert
            continue
        # Normale / Listen-Zeile: optional "- ", optional **Schlüssel:** Wert
        treffer = re.match(
            r"^(?:[-*+]\s+)?[*_`]*([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9 _-]{1,30})[*_`]*\s*:\s*[*_]*(.*)$", zeile
        )
        if treffer:
            schluessel = treffer.group(1).strip().lower()
            wert = treffer.group(2).strip().strip("*").strip()
            if schluessel and schluessel not in felder:
                felder[schluessel] = wert
    return felder


def _klickweg_abschnitt(text: str) -> str | None:
    """Inhalt des Abschnitts ``## Staging-Klickweg`` (bis zur nächsten gleichrangigen/höheren Überschrift)."""
    zeilen = text.splitlines()
    start_idx: int | None = None
    start_ebene = 0
    in_code = False
    for idx, roh in enumerate(zeilen):
        zeile = roh.strip()
        if zeile.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if start_idx is None:
            treffer = _KLICKWEG_KOPF.match(zeile)
            if treffer:
                start_idx = idx + 1
                start_ebene = len(treffer.group(1))
        else:
            kopf = _UEBERSCHRIFT.match(zeile)
            if kopf and len(kopf.group(1)) <= start_ebene:
                return "\n".join(zeilen[start_idx:idx])
    if start_idx is None:
        return None
    return "\n".join(zeilen[start_idx:])


def _url_ist_staging_tauglich(wert: str) -> bool:
    """Gültige http(s)-Adresse mit Hostnamen, kein localhost/Loopback und kein Platzhalter."""
    if _ist_platzhalter_text(wert):
        return False
    treffer = _URL_WERT.search(wert)
    if treffer is None:
        return False
    teile = urllib.parse.urlsplit(treffer.group(0))
    host = (teile.hostname or "").strip().lower()
    if teile.scheme.lower() not in ("http", "https") or not host:
        return False
    if host in _LOKAL_HOSTS or host.endswith(".local") or host.startswith("127."):
        return False
    return "." in host


def _bild_referenzen(abschnitt: str) -> list[tuple[str, str, str]]:
    """(alt_text, pfad, zeile) aller Bild-Verweise im Klickweg-Abschnitt."""
    funde: list[tuple[str, str, str]] = []
    gesehen: set[str] = set()
    for zeile in abschnitt.splitlines():
        for alt, roh_pfad in _MD_BILD.findall(zeile):
            pfad = roh_pfad.strip().split()[0].strip("<>\"'")
            if pfad.lower().endswith(_BILD_ENDUNGEN) and pfad not in gesehen:
                gesehen.add(pfad)
                funde.append((alt.strip(), pfad, zeile))
        for muster in (_INLINE_BILD, _PFAD_BILD):
            for roh_pfad in muster.findall(zeile):
                pfad = roh_pfad.strip()
                if pfad not in gesehen:
                    gesehen.add(pfad)
                    funde.append(("", pfad, zeile))
    return funde


def _bild_repo_pfad(beleg_pfad: str, roh_pfad: str) -> list[str]:
    """Mögliche Repo-Pfade (POSIX) für einen Bild-Verweis aus einer Belegseite."""
    sauber = roh_pfad.replace("\\", "/").lstrip("/")
    basis = str(PurePosixPath(beleg_pfad).parent)
    relativ = (
        posixpath.normpath(posixpath.join(basis, sauber)) if basis and basis != "." else posixpath.normpath(sauber)
    )
    direkt = posixpath.normpath(sauber)
    kandidaten: list[str] = []
    for kandidat in (relativ, direkt):
        if kandidat and not kandidat.startswith("..") and kandidat not in kandidaten:
            kandidaten.append(kandidat)
    return kandidaten


def _bild_groesse_und_breite(repo: Path, ref: str, repo_pfad: str) -> tuple[bool, int, int | None]:
    """(vorhanden, byte_groesse, breite_px) eines Bildes am Git-Stand ``ref``."""
    code, daten = _git_bytes(repo, "show", f"{ref}:{repo_pfad}")
    if code != 0:
        return False, 0, None
    groesse = len(daten)
    if groesse == 0:
        return True, 0, None
    return True, groesse, _bild_breite(daten)


def _bild_breite(daten: bytes) -> int | None:
    """Bildbreite in Pixeln aus PNG- oder WebP-Header (ohne Fremdbibliothek), sonst ``None``."""
    if len(daten) >= 24 and daten[:8] == b"\x89PNG\r\n\x1a\n" and daten[12:16] == b"IHDR":
        breite, _hoehe = struct.unpack(">II", daten[16:24])
        return int(breite)
    if len(daten) >= 30 and daten[:4] == b"RIFF" and daten[8:12] == b"WEBP":
        chunk = daten[12:16]
        if chunk == b"VP8X" and len(daten) >= 27:
            return int.from_bytes(daten[24:27], "little") + 1
        if chunk == b"VP8 " and len(daten) >= 30:
            return int.from_bytes(daten[26:28], "little") & 0x3FFF
        if chunk == b"VP8L" and len(daten) >= 25:
            bits = int.from_bytes(daten[21:25], "little")
            return (bits & 0x3FFF) + 1
    return None


def pruefe_beleg_form(
    repo: Path,
    ref: str,
    beleg_pfad: str,
    ticket: int,
    *,
    klickweg_pflicht: bool = True,
    git_lauf: GitLauf | None = None,
) -> list[str]:
    """Prüft die feste Form einer Markdown-Belegseite am Git-Stand ``ref`` (E50).

    Rückgabe: Liste konkreter Form-Mängel (leer = Belegseite erfüllt die feste Form).
    """
    lauf = git_lauf or _git
    code, text = lauf(repo, "show", f"{ref}:{beleg_pfad}")
    if code != 0:
        return [f"Belegseite {beleg_pfad} am Stand {ref} nicht lesbar"]
    if not text.strip():
        return [f"Belegseite {beleg_pfad} ist leer"]

    maengel: list[str] = []
    felder = _kopffeld_werte(text)

    # 1. Ticket
    ticket_wert = felder.get("ticket", "")
    if not ticket_wert:
        maengel.append("Pflichtfeld Ticket fehlt")
    elif not re.search(rf"(?:^|\D)#?{ticket}(?:\D|$)", ticket_wert):
        maengel.append(f"Pflichtfeld Ticket passt nicht zu #{ticket} (gefunden: {ticket_wert})")

    # 2. Commit-Stand / Stand-SHA
    commit_wert = felder.get("commit-stand") or felder.get("stand-sha") or felder.get("commit") or ""
    if not commit_wert:
        maengel.append("Pflichtfeld Commit-Stand fehlt")
    elif _ist_platzhalter_text(commit_wert) or not _SHA_KURZ.search(commit_wert):
        maengel.append(f"Pflichtfeld Commit-Stand ohne gültigen SHA (gefunden: {commit_wert})")

    # 3. Staging-Adresse / Umgebung
    staging_wert = felder.get("staging-adresse") or felder.get("umgebung") or felder.get("staging") or ""
    if not staging_wert:
        maengel.append("Pflichtfeld Staging-Adresse fehlt")
    elif not _url_ist_staging_tauglich(staging_wert):
        maengel.append(f"Pflichtfeld Staging-Adresse ungültig oder lokal (gefunden: {staging_wert})")

    # 4. Rolle
    rolle_wert = felder.get("rolle", "")
    if not rolle_wert:
        maengel.append("Pflichtfeld Rolle fehlt")
    elif _ist_platzhalter_text(rolle_wert):
        maengel.append(f"Pflichtfeld Rolle enthält Platzhalter (gefunden: {rolle_wert})")

    # 5. Datum
    datum_wert = felder.get("datum", "")
    if not datum_wert:
        maengel.append("Pflichtfeld Datum fehlt")
    elif _ist_platzhalter_text(datum_wert) or not _DATUM_WERT.search(datum_wert):
        maengel.append(f"Pflichtfeld Datum ungültig (erwartet JJJJ-MM-TT, gefunden: {datum_wert})")

    # 6. Abschnitt Staging-Klickweg ohne Platzhalter mit Desktop- und Handy-Bild
    abschnitt = _klickweg_abschnitt(text)
    if abschnitt is None:
        maengel.append("Abschnitt Staging-Klickweg fehlt")
        return maengel
    if not abschnitt.strip():
        maengel.append("Abschnitt Staging-Klickweg ist leer")
        return maengel
    if _ist_platzhalter_text(abschnitt):
        maengel.append("Abschnitt Staging-Klickweg enthält Platzhalter")

    if klickweg_pflicht:
        bilder = _bild_referenzen(abschnitt)
        hat_desktop = False
        hat_handy = False
        for alt, roh_pfad, zeile in bilder:
            gefunden_pfad: str | None = None
            groesse = 0
            breite: int | None = None
            for kandidat in _bild_repo_pfad(beleg_pfad, roh_pfad):
                da, groesse, breite = _bild_groesse_und_breite(repo, ref, kandidat)
                if da:
                    gefunden_pfad = kandidat
                    break
            if gefunden_pfad is None:
                maengel.append(f"Bild fehlt auf {ref}: {roh_pfad}")
                continue
            if groesse == 0:
                maengel.append(f"Bild ist leer: {roh_pfad}")
                continue
            kontext = f"{alt} {roh_pfad} {zeile}"
            if _DESKTOP_WORT.search(kontext) or (breite is not None and breite >= 800):
                hat_desktop = True
            if _HANDY_WORT.search(kontext) or (breite is not None and breite <= 500):
                hat_handy = True
        if not hat_desktop:
            maengel.append("Desktop-Bild im Abschnitt Staging-Klickweg fehlt")
        if not hat_handy:
            maengel.append("Handy-Bild im Abschnitt Staging-Klickweg fehlt")

    return maengel


def regel_beweis(
    repo: Path,
    ref: str,
    ticket: int,
    eigene: list[Commit],
    ordner: str,
    *,
    form_pruefen: bool = True,
    beleg_auf_hauptzweig: bool = True,
    klickweg_pflicht: bool = True,
    git_lauf: GitLauf | None = None,
) -> Grund | None:
    """Prüft Vorhandensein (auf ``ref``) und bei ``form_pruefen=True`` die feste Form der Belegseite."""
    ordner = ordner.replace("\\", "/").strip("/") or BELEGE_ORDNER
    seiten = belegseiten(repo, ticket, ref=ref, ordner=ordner, git_lauf=git_lauf)
    if not seiten and not beleg_auf_hauptzweig:
        namen = [
            pfad
            for commit in eigene
            for _, pfad in _dateien(repo, commit.sha, git_lauf=git_lauf)
            if pfad.startswith(ordner + "/")
        ]
        if any(beleg_passt(pfad[len(ordner) + 1 :], ticket) for pfad in namen):
            return None
    if not seiten:
        if eigene and all(_nur_doku(repo, c.sha, git_lauf=git_lauf) for c in eigene):
            # Reines Doku-Ticket: die Doku ist der Beleg (E4) — aber nur Doku außerhalb
            # des Beleg-Ordners; eine fremde Belegseite (z. B. 9010 statt 901) zählt nicht.
            doku = [pfad for c in eigene for _, pfad in _dateien(repo, c.sha, git_lauf=git_lauf)]
            if any(not pfad.startswith(ordner + "/") for pfad in doku):
                return None
        return Grund(
            regel="beweis_fehlt",
            text=f"keine Belegseite (Datei oder Ordner) mit „{ticket}“ im Namen unter {ordner}/",
        )
    if not form_pruefen:
        return None
    md_seiten = [p for p in seiten if p.lower().endswith(".md") and not _ROT_DATEI.search(p[len(ordner) + 1 :])]
    if not md_seiten:
        return Grund(
            regel="beleg_form",
            text=f"keine Markdown-Belegseite (.md) mit „{ticket}“ unter {ordner}/ auf {ref}",
            details=["Markdown-Belegseite (.md) fehlt"],
        )
    beste_maengel: list[str] | None = None
    beste_datei = md_seiten[0]
    for pfad in md_seiten:
        maengel = pruefe_beleg_form(
            repo,
            ref,
            pfad,
            ticket,
            klickweg_pflicht=klickweg_pflicht,
            git_lauf=git_lauf,
        )
        if not maengel:
            return None
        if beste_maengel is None or len(maengel) < len(beste_maengel):
            beste_maengel = maengel
            beste_datei = pfad
    maengel_liste = beste_maengel or []
    return Grund(
        regel="beleg_form",
        text=f"Belegseite {beste_datei} erfüllt die feste Form nicht: {'; '.join(maengel_liste)}",
        kern=f"{beste_datei}|{'|'.join(maengel_liste)}",
        details=maengel_liste,
    )


# --- Regel 3: Kein Test ersetzt -------------------------------------------------


def ist_testdatei(pfad: str) -> bool:
    """Echte Testdatei — nicht deren Kopien unter ``docs/``, ``archive/`` oder ``mutants/``."""
    return bool(_TEST_DATEI.search(pfad)) and not _KEINE_TESTDATEI.search(pfad)


def _test_abschnitte(diff: str) -> dict[str, str]:
    """Diff je Datei (neuer Pfad → Abschnitt), nur echte Testdateien."""
    abschnitte: dict[str, str] = {}
    for teil in re.split(r"(?m)^(?=diff --git )", diff):
        kopf = re.match(r"diff --git a/(\S+) b/(\S+)", teil)
        if kopf and (ist_testdatei(kopf.group(1)) or ist_testdatei(kopf.group(2))):
            abschnitte[kopf.group(2)] = teil
    return abschnitte


def entfernte_tests(diff: str) -> list[str]:
    """Namen entfernter Testfälle, die im selben Diff nicht wieder auftauchen."""
    plus = "\n".join(z for z in diff.splitlines() if z.startswith("+") and not z.startswith("+++"))
    namen: list[str] = []
    for treffer in _PY_TEST.finditer(diff):
        name = treffer.group(1)
        if not re.search(rf"\b{re.escape(name)}\b", plus):
            namen.append(name)
    for treffer in _JS_TEST.finditer(diff):
        name = treffer.group(2)
        if not any(f"{q}{name}{q}" in plus for q in ("'", '"', "`")):
            namen.append(name)
    return list(dict.fromkeys(namen))


def umbenannte_tests(
    diff: str,
    commit_text: str,
    an_spitze: Callable[[str], str | None] | None = None,
) -> set[str]:
    """Alte Namen bewusst umbenannter Python-/JS-Tests (#448, #1205) — kein Verlust."""
    mit_kopf = bool(re.search(r"(?m)^diff --git ", diff))
    abschnitte: list[tuple[str | None, str]] = list(_test_abschnitte(diff).items()) if mit_kopf else [(None, diff)]
    teile = [teil for _, teil in abschnitte]
    alte: set[str] = set()
    for vermerk in _UMBENANNT.finditer(commit_text):
        zitate = [z[1] for z in _ZITAT.findall(vermerk.group(1))]
        if zitate:
            for alt, neu in zip(zitate[::2], zitate[1::2]):
                if any(_steht_drin(neu, teil, "+") and (not mit_kopf or _steht_drin(alt, teil, "-")) for teil in teile):
                    alte.add(alt)
            continue
        for paar in re.split(r"[,;]", vermerk.group(1)):
            seiten = _PFEIL.split(paar.strip())
            if len(seiten) != 2:
                continue
            links, rechts = (_PY_NAME.findall(seite) for seite in seiten)
            if not links or not rechts:
                continue
            alt, neu = links[-1], rechts[-1]
            vermerk_pfad = _VERMERK_PFAD.search(seiten[0])
            if any(
                _def_zeile("+", neu, teil)
                and (not mit_kopf or _def_zeile("-", alt, teil))
                and _paar_passt_zur_datei(pfad, neu, vermerk_pfad.group(1) if vermerk_pfad else None, an_spitze)
                for pfad, teil in abschnitte
            ):
                alte.add(alt)
    return alte


def _paar_passt_zur_datei(
    pfad: str | None,
    neu: str,
    vermerk_pfad: str | None,
    an_spitze: Callable[[str], str | None] | None,
) -> bool:
    """Datei-Bedingungen eines Python-Vermerks (#1205), siehe :func:`umbenannte_tests`."""
    if pfad is None:
        return True
    if vermerk_pfad is not None and vermerk_pfad != pfad:
        return False
    if an_spitze is None:
        return True
    text = an_spitze(pfad)
    return text is not None and _steht_drin(neu, text)


def _def_zeile(zeichen: str, name: str, diff: str) -> bool:
    """Steht ``def <name>`` als ``+``- bzw. ``-``-Zeile im Diff?"""
    muster = rf"(?m)^{re.escape(zeichen)}[ \t]*(?:async[ \t]+)?def[ \t]+{re.escape(name)}\b"
    return re.search(muster, diff) is not None


def _datei_an_spitze(
    repo: Path,
    spitze: str,
    pfad: str,
    *,
    git_lauf: GitLauf | None = None,
) -> str | None:
    """Inhalt von ``pfad`` im Baum von ``spitze``; ``None``, wenn die Datei dort nachweislich fehlt (#861)."""
    code, text = _git_wiederholen(repo, "show", f"{spitze}:{pfad}", git_lauf=git_lauf)
    if code == 0:
        return text
    if code in (124, 127):
        grund = _nicht_pruefbar_grund(f"git show {spitze[:7]}:{pfad}", code)
        log.warning("Datei %s an %s nicht lesbar (%s) — Testverlust nicht prüfbar", pfad, spitze[:7], grund)
        raise _NichtPruefbar(grund)
    ls_code, ls_text = _git_wiederholen(repo, "ls-tree", "-r", "--name-only", spitze, "--", pfad, git_lauf=git_lauf)
    if ls_code == 0 and pfad not in ls_text.splitlines():
        return None
    fehler_code = ls_code if ls_code != 0 else code
    befehl = f"git ls-tree {spitze[:7]}" if ls_code != 0 else f"git show {spitze[:7]}:{pfad}"
    grund = _nicht_pruefbar_grund(befehl, fehler_code)
    log.warning("Datei %s an %s nicht lesbar (%s) — Testverlust nicht prüfbar", pfad, spitze[:7], grund)
    raise _NichtPruefbar(grund)


def _steht_drin(name: str, text: str, zeichen: str = "") -> bool:
    """Steht der Testfall ``name`` (Python-``def`` oder JS-``it``/``test``) im Text?"""
    vor = re.escape(zeichen)
    if _PY_NAME.fullmatch(name):
        muster = rf"(?m)^{vor}[ \t]*(?:async[ \t]+)?def[ \t]+{re.escape(name)}\b"
    else:
        muster = rf"""(?m)^{vor}[ \t]*(?:it|test)\([ \t]*(["'`]){re.escape(name)}\1"""
    return re.search(muster, text) is not None


def _kein_netto_verlust(
    repo: Path,
    basis: str | None,
    spitze: str,
    name: str,
    abschnitte: dict[str, str],
    *,
    git_lauf: GitLauf | None = None,
) -> bool:
    """Netto-Diff gegen die Ticket-Basis (#826): kein Verlust, wenn der entfernte Test an der Spitze wieder steht
    oder vor dem Ticket noch nicht existierte."""
    for pfad, abschnitt in abschnitte.items():
        if not _steht_drin(name, abschnitt, "-"):
            continue
        an_spitze = _datei_an_spitze(repo, spitze, pfad, git_lauf=git_lauf)
        if an_spitze is not None and _steht_drin(name, an_spitze):
            log.info("Test %s in %s an der Spitze %s wieder da — kein Verlust", name, pfad, spitze[:7])
            return True
        if basis is None:
            continue
        vorher = _datei_an_spitze(repo, basis, pfad, git_lauf=git_lauf)
        if vorher is None or not _steht_drin(name, vorher):
            log.info("Test %s in %s gab es vor dem Ticket nicht — kein Verlust", name, pfad)
            return True
    return False


def _ticket_vermerke(repo: Path, eigene: list[Commit], *, git_lauf: GitLauf | None = None) -> str:
    """``test-umbenannt:``-Zeilen aus allen Commit-Texten des Tickets (#1205)."""
    lauf = git_lauf or _git
    zeilen: list[str] = []
    for commit in eigene:
        code, text = lauf(repo, "show", "-s", "--format=%B", commit.sha)
        if code != 0:
            log.warning("Commit-Text von %s nicht lesbar — Vermerke fehlen", commit.sha[:7])
            continue
        zeilen += [z for z in text.splitlines() if _UMBENANNT.search(z)]
    return "\n".join(zeilen)


def _test_verluste(
    repo: Path,
    sha: str,
    spitze: str | None = None,
    basis: str | None = None,
    vermerke: str | None = None,
    *,
    git_lauf: GitLauf | None = None,
) -> list[str]:
    """Entfernte Tests eines Commits; mit ``spitze`` nur der Netto-Verlust (#826, #1205, #1210)."""
    code, diff = _git_wiederholen(repo, "show", "--format=", "-M", "--unified=0", sha, git_lauf=git_lauf)
    if code != 0:
        grund = _nicht_pruefbar_grund("git show", code)
        log.warning("git show %s gescheitert (%s) — Testverlust nicht prüfbar", sha[:7], grund)
        raise _NichtPruefbar(grund)
    abschnitte = _test_abschnitte(diff)
    verluste = entfernte_tests("".join(abschnitte.values()))
    if verluste:
        if vermerke is None:
            lauf = git_lauf or _git
            code_text, vermerke = lauf(repo, "show", "-s", "--format=%B", sha)
            if code_text != 0:
                log.warning("Commit-Text von %s nicht lesbar — Vermerke fehlen", sha[:7])
                vermerke = ""
        umbenannt = umbenannte_tests(
            diff,
            vermerke,
            (lambda pfad: _datei_an_spitze(repo, spitze, pfad, git_lauf=git_lauf)) if spitze else None,
        )
        verluste = [name for name in verluste if name not in umbenannt]
    if verluste and spitze:
        verluste = [
            n for n in verluste if not _kein_netto_verlust(repo, basis, spitze, n, abschnitte, git_lauf=git_lauf)
        ]
    for status, pfad in _dateien(repo, sha, streng=True, git_lauf=git_lauf):
        if status != "D" or not ist_testdatei(pfad):
            continue
        abschnitt = abschnitte.get(pfad, "")
        if _PY_TEST.search(abschnitt) or _JS_TEST.search(abschnitt):
            continue
        if spitze and spitze != sha and _datei_an_spitze(repo, spitze, pfad, git_lauf=git_lauf) is not None:
            continue
        verluste.append(f"Datei {pfad}")
    return verluste


def regel_tests(
    repo: Path,
    ticket: int,
    eigene: list[Commit],
    *,
    git_lauf: GitLauf | None = None,
) -> Grund | None:
    """Prüft alle Ticket-Commits auf Netto-Testverluste."""
    lauf = git_lauf or _git
    verluste: list[str] = []
    spitze_commit = _spitze_commit(eigene)
    spitze = spitze_commit.sha if spitze_commit is not None else None
    basis = None
    if eigene:
        aeltester = min(enumerate(eigene), key=lambda p: (p[1].zeit, -p[0]))[1]
        code, eltern = lauf(repo, "rev-parse", "--verify", "-q", f"{aeltester.sha}^")
        basis = eltern.strip() if code == 0 and eltern.strip() else None
    vermerke = _ticket_vermerke(repo, eigene, git_lauf=git_lauf)
    nicht_pruefbar: list[str] = []
    for commit in eigene:
        try:
            namen = _test_verluste(repo, commit.sha, spitze, basis, vermerke, git_lauf=git_lauf)
        except _NichtPruefbar as grund:
            nicht_pruefbar.append(f"{commit.sha[:7]} ({grund})")
            continue
        verluste += [f"{name} ({commit.sha[:7]})" for name in namen]
    if not verluste and not nicht_pruefbar:
        return None
    teile: list[str] = []
    if verluste:
        liste = ", ".join(verluste[:4]) + (f" (+{len(verluste) - 4})" if len(verluste) > 4 else "")
        teile.append(f"Test entfernt statt ergänzt: {liste}")
    if nicht_pruefbar:
        teile.append(f"Testverlust nicht prüfbar: {', '.join(nicht_pruefbar)}")
    return Grund(regel="test_ersetzt", text="; ".join(teile), details=verluste + nicht_pruefbar)


# --- Regel 4: Commit grün auf Hauptzweig ----------------------------------------


def gruene_commits_aus_kommentaren(kommentare: Sequence[str]) -> list[str]:
    """Extrahiert volle 40-Zeichen-SHAs aus Kommentaren mit „grün auf master/main“ (E18)."""
    treffer: list[str] = []
    for text in kommentare:
        if not text or not _GRUEN_KOMMENTAR.search(text):
            continue
        for sha in _SHA_VOLL.findall(text):
            klein = sha.lower()
            if klein not in treffer:
                treffer.append(klein)
    return treffer


def _sha_deckt_ticket_auf_hauptzweig(
    repo: Path,
    haupt_ref: str,
    gruen_sha: str,
    eigene: list[Commit],
    *,
    git_lauf: GitLauf | None = None,
) -> tuple[bool, str]:
    """Prüft per Git, ob ``gruen_sha`` auf ``haupt_ref`` liegt und den jüngsten Ticket-Commit einschließt."""
    lauf = git_lauf or _git
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", gruen_sha):
        return False, f"ungültiger Commit-SHA im Grün-Nachweis: {gruen_sha}"
    code, aufgeloest = lauf(repo, "rev-parse", "--verify", "-q", f"{gruen_sha}^{{commit}}")
    if code != 0 or not aufgeloest.strip():
        return False, f"grüner Commit {gruen_sha[:7]} im Repo nicht vorhanden"
    voll_sha = aufgeloest.strip()
    auf_haupt, _ = lauf(repo, "merge-base", "--is-ancestor", voll_sha, haupt_ref)
    if auf_haupt != 0:
        return False, f"grüner Commit {voll_sha[:7]} liegt nicht auf {haupt_ref}"
    spitze = _spitze_commit(eigene)
    if spitze is not None:
        drin, _ = lauf(repo, "merge-base", "--is-ancestor", spitze.sha, voll_sha)
        if drin != 0:
            return (
                False,
                f"neuester Ticket-Commit {spitze.sha[:7]} ist nicht im grünen Stand {voll_sha[:7]} enthalten",
            )
    return True, ""


def regel_gruen_auf_hauptzweig(
    repo: Path,
    haupt_ref: str,
    eigene: list[Commit],
    gruen_auf_hauptzweig: bool | str | None,
    kommentare: Sequence[str] | None = None,
    *,
    git_lauf: GitLauf | None = None,
) -> Grund | None:
    """Prüft den Nachweis „Commit grün auf Hauptzweig“ (E18/E19)."""
    if gruen_auf_hauptzweig is False:
        return Grund(
            regel="nicht_gruen_auf_hauptzweig",
            text=f"Commit ist nicht grün auf {haupt_ref}",
        )
    if gruen_auf_hauptzweig is True:
        spitze = _spitze_commit(eigene)
        if spitze is not None:
            lauf = git_lauf or _git
            drin, _ = lauf(repo, "merge-base", "--is-ancestor", spitze.sha, haupt_ref)
            if drin != 0:
                return Grund(
                    regel="nicht_gruen_auf_hauptzweig",
                    text=f"neuester Ticket-Commit {spitze.sha[:7]} liegt nicht auf {haupt_ref}",
                )
        return None
    if isinstance(gruen_auf_hauptzweig, str):
        ok, meldung = _sha_deckt_ticket_auf_hauptzweig(
            repo,
            haupt_ref,
            gruen_auf_hauptzweig.strip(),
            eigene,
            git_lauf=git_lauf,
        )
        if ok:
            return None
        return Grund(regel="nicht_gruen_auf_hauptzweig", text=meldung)
    if kommentare is not None:
        shas = gruene_commits_aus_kommentaren(kommentare)
        letzter_grund = "kein Grün-Nachweis („grün auf master/main“) mit vollem 40-Zeichen-SHA in den Kommentaren"
        for sha in reversed(shas):
            ok, meldung = _sha_deckt_ticket_auf_hauptzweig(repo, haupt_ref, sha, eigene, git_lauf=git_lauf)
            if ok:
                return None
            letzter_grund = meldung
        return Grund(regel="nicht_gruen_auf_hauptzweig", text=letzter_grund)
    return Grund(
        regel="nicht_gruen_auf_hauptzweig",
        text=f"kein Grün-Nachweis für {haupt_ref} vorhanden",
    )


# --- Schmale Tür ----------------------------------------------------------------


def _stand_aus_eingabe(eingabe: Stand | Mapping[str, Any]) -> Stand:
    """Wandelt ein :class:`Stand`-Objekt oder ein ``Mapping`` (z. B. aus ``taktgeber.py``) in :class:`Stand`."""
    if isinstance(eingabe, Stand):
        return eingabe
    if not isinstance(eingabe, Mapping):
        raise TypeError(f"Stand oder Mapping erwartet, erhalten: {type(eingabe).__name__}")
    repo = Path(eingabe["repo"])
    ticket = int(eingabe["ticket"])
    ref = str(eingabe.get("ref") or "HEAD")
    ordner_roh = eingabe.get("belege_ordner")
    konfig_roh = eingabe.get("konfig")
    ordner: str | None = (
        str(ordner_roh)
        if ordner_roh is not None
        else (belege_ordner(konfig_roh) if isinstance(konfig_roh, Mapping) else None)
    )
    eigene_roh = eingabe.get("eigene")
    if eigene_roh is not None:
        eigene: list[Commit] | None = list(eigene_roh)
    elif eingabe.get("commits") is not None:
        eigene = ticket_commits(list(eingabe["commits"]), ticket)
    else:
        eigene = None
    if "gruen_auf_hauptzweig" in eingabe:
        gruen_wert = eingabe["gruen_auf_hauptzweig"]
    else:
        gruen_wert = eingabe.get("gruen_sha")
    haupt_ref_roh = eingabe.get("haupt_ref")
    return Stand(
        repo=repo,
        ticket=ticket,
        ref=ref,
        belege_ordner=ordner,
        eigene=eigene,
        haupt_ref=str(haupt_ref_roh) if haupt_ref_roh is not None else None,
        gruen_auf_hauptzweig=gruen_wert,
        kommentare=eingabe.get("kommentare"),
        gruen_pruefen=bool(eingabe.get("gruen_pruefen", True)),
        form_pruefen=bool(eingabe.get("form_pruefen", True)),
        beleg_auf_hauptzweig=bool(eingabe.get("beleg_auf_hauptzweig", True)),
        klickweg_pflicht=bool(eingabe.get("klickweg_pflicht", True)),
        git_lauf=eingabe.get("git_lauf"),
    )


def pruefe(stand: Stand | Mapping[str, Any]) -> PruefErgebnis:
    """Schmale Tür des Schließ-Prüfers: :class:`Stand` (oder Mapping) rein, :class:`PruefErgebnis` raus.

    Prüft alle Schließ-Regeln an genau dieser Stelle — bewusst ohne „Server nicht aktuell“.
    """
    daten = _stand_aus_eingabe(stand)
    lauf = daten.git_lauf
    ziel_ref = daten.haupt_ref or daten.ref
    ordner = daten.belege_ordner if daten.belege_ordner is not None else belege_ordner(config.lade(daten.repo))
    eigene = (
        list(daten.eigene)
        if daten.eigene is not None
        else ticket_commits(alle_commits(daten.repo, daten.ref, git_lauf=lauf), daten.ticket)
    )
    kandidaten: list[Grund | None] = [
        regel_commit(daten.ticket, eigene),
        regel_beweis(
            daten.repo,
            ziel_ref,
            daten.ticket,
            eigene,
            ordner,
            form_pruefen=daten.form_pruefen,
            beleg_auf_hauptzweig=daten.beleg_auf_hauptzweig,
            klickweg_pflicht=daten.klickweg_pflicht,
            git_lauf=lauf,
        ),
        regel_tests(daten.repo, daten.ticket, eigene, git_lauf=lauf),
    ]
    if daten.gruen_pruefen:
        kandidaten.append(
            regel_gruen_auf_hauptzweig(
                daten.repo,
                ziel_ref,
                eigene,
                daten.gruen_auf_hauptzweig,
                daten.kommentare,
                git_lauf=lauf,
            )
        )
    gruende = [g for g in kandidaten if g is not None]
    return PruefErgebnis(ok=not gruende, gruende=gruende)
