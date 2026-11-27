"""Stufe 1 Sofort-Prüfung und Stufe 2 „nur Doku“ des Prüf-Werkzeugkastens (Paket-intern, #100).

Schnittstelle zur Leiter: ``stufen(repo, basis) -> (liste, halt)``. ``liste`` sind die Stufen
„sofort“ und „doku“ fürs Protokoll; ``halt`` ≠ "" heißt, die übrigen Stufen laufen nicht (Grund
steht drin: „nur Doku“ oder schnell rot). Versteckt hier: welche Dateien geändert sind (git, auch
ungespeicherte und neue), welcher Linter zu welcher Endung gehört und wo er liegt.

„Nur Doku“ (M11) gibt es nur für Repos, die ihre Doku-Pfade selbst erklären: Schlüssel
``pruefen.doku_pfade`` in ``.to-spawn/config.json``, eine Liste von Mustern relativ zur
Repo-Wurzel mit ``/`` (``fnmatch``, Groß/klein zählt, ``*`` passt auch über ``/``), z. B.
``["docs/**", "README.md"]``. Es gilt die Konfig im Stand der Basis, nie die des geprüften Commits;
ändert der Commit die Konfig, prüft die Leiter voll. Warum: Ob Code eine Datei liest, lässt sich nicht erraten (Tests über
``git ls-files``, ``iterdir``, Symlinks, ``mimetypes``) — nur das Repo weiß es. Abkürzung nur, wenn
JEDE geänderte Datei (neu, gelöscht, bei Umbenennung alter und neuer Pfad) unter ein Muster fällt,
eine Doku-Endung hat (Erlaubnisliste ``_DOKU_ENDUNGEN``/``_DOKU_NAMEN``, ohne Konfig-Ausnahmen
``_KONFIG_NAMEN`` und ``regeln._R3_ORDNER``), keine davon ein Symlink ist und kein getrackter Symlink auf sie zeigt. Fehlt der Schlüssel, ist er
ungültig oder die Konfig kaputt: nie Abkürzung, die Leiter prüft voll (fail-closed). Gleiches gilt,
wenn ein Muster eine Nicht-Doku-Datei trifft (``**``, ``*``, ``*.py``, ``*.sh``, ``Makefile``, #164): Die
Basis kann ein Vorgänger-Commit sein, darum prüft die Tür die Weite selbst, statt der Basis-Wahl
des Aufrufers zu vertrauen.

Kein falsches Grün: fehlt ein Linter oder ist unklar, was geändert ist, wird die Stufe
ausgelassen (``lief=False`` + Grund) und die Leiter prüft voll weiter.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from to_spawn import bash_wahl, config, konsole, prozessbaum, temp_ordner
from to_spawn.pruef import ausfuehren, cache, leiter, regeln, test_schutz

log = logging.getLogger("to_spawn.pruef")

NUR_DOKU = "nur Doku"
ROT_HALT = "Stufe sofort rot (schnell rot)"

NICHT_NUR_DOKU = "nicht nur Doku geändert"
OHNE_DOKU_PFADE = "Doku-Pfade nicht erklärt (pruefen.doku_pfade)"
DOKU_PFADE_UNGUELTIG = "pruefen.doku_pfade ungültig (Liste von Pfad-Mustern erwartet)"
SYMLINK = "Symlink geändert oder zeigt auf geänderte Datei"
SYMLINK_UNKLAR = "Symlinks unklar (git ls-files/ls-tree gescheitert)"
KONFIG_GEAENDERT = "Konfig geändert (.to-spawn/config.json)"
#: Geänderte Pfade (casefold), die die Abkürzung sperren: die Konfig selbst oder ihr Ordner als Symlink.
_KONFIG_PFADE = frozenset({config.KONFIG_PFAD.as_posix().casefold(), config.KONFIG_PFAD.parent.as_posix().casefold()})
#: git-Modus eines Symlinks.
_SYMLINK_MODUS = "120000"

# --- Schlüssel-/Secret-Sperre für den Beleg-Ordner (#863, aus App #503 / #426) -----------------

#: Vorgabe-Ordner für Belege, wenn ``regularien.belege_ordner`` nicht gesetzt ist.
BELEGE_ORDNER_VORGABE = str(config.DEFAULTS["regularien"]["belege_ordner"])
_BINAER_PROBE_BYTES = 8192
_PLATZHALTER_WOERTER = (
    "REDACTED",
    "PLACEHOLDER",
    "CHANGEME",
    "EXAMPLE",
    "YOUR_TOKEN",
    "YOUR_API",
    "YOUR_SECRET",
    "DUMMY",
    "FAKE_TOKEN",
    "MUSTER",
    "GESCHWAERZT",
)


@dataclass(frozen=True)
class SchluesselMuster:
    """Einzelnes Secret-/Schlüssel-Muster für den Beleg-Ordner (ohne Klartext-Ausgabe)."""

    name: str
    muster: re.Pattern[str]
    beschreibung: str
    wert_gruppe: int = 0


#: Zentrale Musterliste aus App #426 (Live-API-Bearer-Tokens, Secrets, private Schlüssel).
BELEG_SCHLUESSEL_MUSTER: tuple[SchluesselMuster, ...] = (
    SchluesselMuster(
        "private_key_pem",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
        "Privater Schlüssel (PEM-Kopf)",
    ),
    SchluesselMuster(
        "bearer_token",
        re.compile(r"\bBearer\s+([A-Za-z0-9._~+/=-]{16,})\b"),
        "Live-API-Bearer-Token",
        wert_gruppe=1,
    ),
    SchluesselMuster(
        "github_token",
        re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,})\b"),
        "GitHub-Zugriffstoken",
        wert_gruppe=1,
    ),
    SchluesselMuster(
        "provider_api_key",
        re.compile(
            r"\b(sk-(?:proj-|ant-api\d+-|live_|test_)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|xox[baprs]-[0-9A-Za-z-]{10,})\b"
        ),
        "API-Schlüssel (Provider-Präfix)",
        wert_gruppe=1,
    ),
    SchluesselMuster(
        "aws_access_key",
        re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b"),
        "AWS-Zugriffsschlüssel",
        wert_gruppe=1,
    ),
    SchluesselMuster(
        "jwt_token",
        re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"),
        "JWT-Token",
        wert_gruppe=1,
    ),
    SchluesselMuster(
        "secret_zuweisung",
        re.compile(
            r"(?i)\b(?:[a-z0-9_]*?(?:api[_-]?key|api[_-]?secret|api[_-]?token|access[_-]?token|auth[_-]?token|"
            r"bearer[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key))\b"
            r"""\s*[:=]\s*["']?([A-Za-z0-9._~+/=-]{16,})["']?"""
        ),
        "Secret-/API-Token-Zuweisung",
        wert_gruppe=1,
    ),
)


@dataclass(frozen=True)
class SchluesselFund:
    """Einzelner Schlüssel-/Secret-Fund im Beleg-Ordner (ohne Klartext-Wert)."""

    datei: str
    zeile: int
    muster: str
    beschreibung: str

    def als_dict(self) -> dict[str, Any]:
        return {
            "datei": self.datei,
            "zeile": self.zeile,
            "muster": self.muster,
            "beschreibung": self.beschreibung,
        }

    def kurz(self) -> str:
        return f"{self.datei}:{self.zeile} [{self.muster}] {self.beschreibung}"


@dataclass(frozen=True)
class BelegSchluesselErgebnis:
    """Ergebnis der Schlüssel-/Secret-Prüfung über den Beleg-Ordner (E40: ohne Mail-Alarm)."""

    ok: bool
    ordner: tuple[str, ...]
    dateien_geprueft: int
    funde: tuple[SchluesselFund, ...]

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ordner": list(self.ordner),
            "dateien_geprueft": self.dateien_geprueft,
            "funde": [f.als_dict() for f in self.funde],
        }

    def bericht(self) -> str:
        if self.ok:
            return f"Beleg-Schlüssel-Sperre grün ({self.dateien_geprueft} Belegdatei(en) geprüft)."
        zeilen = [
            f"ROT: Schlüssel-/Secret-Sperre im Beleg-Ordner ausgelöst ({len(self.funde)} Fund(e), kein Klartext ausgegeben):",
            *(f"- {fund.kurz()}" for fund in self.funde),
            "Behebung: Geheimnis aus dem Beleg-Ordner entfernen/schwärzen und danach erneut prüfen bzw. committen.",
        ]
        return "\n".join(zeilen)


def _normalisiere_pfad(pfad: str) -> str:
    """Normiert Repo-relative Pfade plattformübergreifend (Windows ``\\`` → POSIX ``/``)."""
    text = pfad.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _ordner_aus_konfig(daten: dict[str, Any] | None) -> str:
    if not isinstance(daten, dict):
        return ""
    regularien = daten.get("regularien")
    if not isinstance(regularien, dict):
        return ""
    roh = str(regularien.get("belege_ordner") or "").replace("\\", "/").strip("/")
    return roh


def belege_ordner_liste(repo: Path, basis: str | None = None) -> tuple[str, ...]:
    """Konfigurierte Beleg-Ordner (``regularien.belege_ordner``, Vorgabe ``docs/verify-hard``).

    Mit ``basis`` werden Basis- und Arbeitsbaum-Konfig berücksichtigt (fail-closed: ein Commit
    kann den Beleg-Ordner nicht durch Umbenennen der Konfig aus der Prüfung herausnehmen).
    """
    ordner: list[str] = []
    if basis is not None:
        aus_basis = _ordner_aus_konfig(_konfig_aus_basis(repo, basis))
        if aus_basis and aus_basis not in ordner:
            ordner.append(aus_basis)
    aus_baum = _ordner_aus_konfig(config.lade(repo))
    if aus_baum and aus_baum not in ordner:
        ordner.append(aus_baum)
    if not ordner:
        ordner.append(BELEGE_ORDNER_VORGABE)
    return tuple(ordner)


def ist_im_beleg_ordner(pfad: str, ordner: Sequence[str]) -> bool:
    """True, wenn ``pfad`` unter einem der genannten Beleg-Ordner liegt."""
    norm = _normalisiere_pfad(pfad)
    if not norm:
        return False
    return any(norm == ziel or norm.startswith(f"{ziel}/") for ziel in ordner if ziel)


def _ist_platzhalter(wert: str) -> bool:
    """Erkennt offensichtliche Platzhalter/Schwärzungen, damit keine Fehlalarme entstehen."""
    bereinigt = wert.strip()
    if not bereinigt:
        return True
    if len(set(bereinigt.lower())) <= 2:
        return True
    gross = bereinigt.upper()
    return any(wort in gross for wort in _PLATZHALTER_WOERTER)


def pruefe_beleg_text(pfad: str, text: str) -> list[SchluesselFund]:
    """Prüft den Text einer Beleg-Datei zeilenweise gegen ``BELEG_SCHLUESSEL_MUSTER``."""
    norm = _normalisiere_pfad(pfad)
    funde: list[SchluesselFund] = []
    for nr, zeile in enumerate(text.splitlines(), start=1):
        for regel in BELEG_SCHLUESSEL_MUSTER:
            treffer = regel.muster.search(zeile)
            if treffer is None:
                continue
            if regel.wert_gruppe > 0 and _ist_platzhalter(treffer.group(regel.wert_gruppe)):
                continue
            funde.append(SchluesselFund(norm, nr, regel.name, regel.beschreibung))
            break
    return funde


def _lese_textdatei(datei: Path) -> str | None:
    """Liest eine Textdatei; Binärdateien (NUL-Byte) oder fehlende Dateien ergeben ``None``."""
    try:
        daten = datei.read_bytes()
    except OSError:
        return None
    if b"\x00" in daten[:_BINAER_PROBE_BYTES]:
        return None
    return daten.decode("utf-8", errors="replace")


def _git_datei_text(repo: Path, ref_pfad: str) -> str | None:
    """Liest einen Git-Blob (z. B. ``HEAD:pfad`` oder ``:pfad``) als Text; binär/fehlend = ``None``."""
    try:
        erg = subprocess.run(
            ["git", "show", ref_pfad],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError:
        return None
    if erg.returncode != 0 or b"\x00" in erg.stdout[:_BINAER_PROBE_BYTES]:
        return None
    return erg.stdout.decode("utf-8", errors="replace")


def pruefe_beleg_dateien(
    repo: Path,
    pfade: Sequence[str],
    *,
    ordner: Sequence[str] | None = None,
    inhalte: dict[str, str] | None = None,
) -> BelegSchluesselErgebnis:
    """Prüft die angegebenen Repo-Pfade, soweit sie im Beleg-Ordner liegen."""
    ziel_ordner = tuple(ordner) if ordner is not None else belege_ordner_liste(repo)
    norm_inhalte = {_normalisiere_pfad(k): v for k, v in inhalte.items()} if inhalte is not None else None
    geprueft = 0
    funde: list[SchluesselFund] = []
    gesehen: set[str] = set()
    for roh in pfade:
        norm = _normalisiere_pfad(roh)
        if norm in gesehen or not ist_im_beleg_ordner(norm, ziel_ordner):
            continue
        gesehen.add(norm)
        if norm_inhalte is not None and norm in norm_inhalte:
            text: str | None = norm_inhalte[norm]
        else:
            text = _lese_textdatei(repo / norm)
        if text is None:
            continue
        geprueft += 1
        funde.extend(pruefe_beleg_text(norm, text))
    return BelegSchluesselErgebnis(
        ok=not funde,
        ordner=ziel_ordner,
        dateien_geprueft=geprueft,
        funde=tuple(funde),
    )


def pruefe_beleg_ordner(repo: Path, *, ordner: Sequence[str] | None = None) -> BelegSchluesselErgebnis:
    """Prüft alle vorhandenen Dateien in den konfigurierten Beleg-Ordnern von ``repo``."""
    ziel_ordner = tuple(ordner) if ordner is not None else belege_ordner_liste(repo)
    pfade: list[str] = []
    for eintrag in ziel_ordner:
        basis_dir = repo / eintrag
        if not basis_dir.is_dir():
            continue
        for datei in sorted(basis_dir.rglob("*")):
            if datei.is_file() and ".git" not in datei.parts:
                pfade.append(datei.relative_to(repo).as_posix())
    return pruefe_beleg_dateien(repo, pfade, ordner=ziel_ordner)


def pruefe_beleg_diff(repo: Path, basis: str, ziel: str | None = None) -> BelegSchluesselErgebnis:
    """Prüft geänderte Beleg-Dateien zwischen ``basis`` und ``ziel`` (``None`` = Arbeitsbaum)."""
    ziel_ordner = belege_ordner_liste(repo, basis)
    pfade = geaendert_zwischen(repo, basis, ziel)
    if not pfade:
        return BelegSchluesselErgebnis(ok=True, ordner=ziel_ordner, dateien_geprueft=0, funde=())
    beleg_pfade = [p for p in pfade if ist_im_beleg_ordner(p, ziel_ordner)]
    if not beleg_pfade:
        return BelegSchluesselErgebnis(ok=True, ordner=ziel_ordner, dateien_geprueft=0, funde=())
    if ziel is None:
        return pruefe_beleg_dateien(repo, beleg_pfade, ordner=ziel_ordner)
    inhalte: dict[str, str] = {}
    for pfad in beleg_pfade:
        text = _git_datei_text(repo, f"{ziel}:{pfad}")
        if text is not None:
            inhalte[pfad] = text
    return pruefe_beleg_dateien(repo, list(inhalte), ordner=ziel_ordner, inhalte=inhalte)


@dataclass(frozen=True)
class _Linter:
    adapter: str
    name: str
    endungen: frozenset[str]
    finde: Callable[[Path], str | None]
    argumente: tuple[str, ...]


def _finde_werkzeug(repo: Path, name: str) -> str | None:
    """Werkzeug aus dem venv des Repos (Linux ``bin``, Windows ``Scripts``), sonst aus dem PATH."""
    venv = [str(repo / ordner / teil) for ordner in (".venv", "venv") for teil in ("bin", "Scripts")]
    return shutil.which(name, path=os.pathsep.join(venv)) or shutil.which(name)


def _ruff(repo: Path) -> str | None:
    """ruff aus dem venv des Repos (Linux ``bin``, Windows ``Scripts``), sonst aus dem PATH."""
    return _finde_werkzeug(repo, "ruff")


def _eslint(repo: Path) -> str | None:
    """Nur das ESLint des Repos (``node_modules/.bin``, Windows ``eslint.cmd``) — es kennt die Konfig."""
    return shutil.which("eslint", path=str(repo / "node_modules" / ".bin"))


_LINTER = (
    # Nur harte Fehler (Syntax, undefinierte Namen), nicht der Stil des Repos: kein falsches Rot
    # in Repos, die ruff nicht nutzen. Repo-Konfig bleibt aktiv (requires-python für Syntax).
    _Linter(
        "py",
        "ruff",
        frozenset({".py", ".pyi"}),
        _ruff,
        ("check", "--no-cache", "--no-fix", "--quiet", "--select", "E9,F63,F7,F82"),
    ),
    _Linter(
        "node",
        "eslint",
        frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}),
        _eslint,
        (),
    ),
)


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        erg = subprocess.run(
            ["git", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError as fehler:
        return ausfuehren.NICHT_GESTARTET, str(fehler)
    return erg.returncode, erg.stdout if erg.returncode == 0 else erg.stderr


def geaendert(repo: Path, basis: str) -> list[str] | None:
    """Geänderte Pfade (``/``, relativ zum Repo) von ``basis`` bis Arbeitsbaum; ``None`` = unklar.

    Neue (ungetrackte) Dateien zählen nur, wenn ``ungespeichert`` sie als schmutzig meldet —
    Werkzeug-Reste nach ``cache.ist_werkzeug_rest`` fallen hier wie dort heraus (#218).
    """
    code, raus = _git(repo, "diff", "--name-only", "--no-renames", "-z", basis, "--")
    if code != 0:
        return None
    neu_code, neu = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if neu_code != 0:
        return None
    offen, kurz = _offen(repo)
    if kurz:
        return None
    neue = [p for p in neu.split("\0") if any(p == o or (o.endswith("/") and p.startswith(o)) for o in offen)]
    return sorted({pfad for pfad in [*raus.split("\0"), *neue] if pfad})


def _git_status(repo: Path, *args: str) -> tuple[str, str]:
    """(Ausgabe, Ursache) von ``git status --porcelain -z <args>``; Ursache ≠ "" = gescheitert."""
    try:
        erg = subprocess.run(
            ["git", "status", "--porcelain", "-z", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError as fehler:
        return "", str(fehler)
    if erg.returncode == 0:
        return erg.stdout, ""
    return "", (erg.stderr.strip().splitlines() or [f"Exit {erg.returncode}"])[0]


def ungespeichert(repo: Path) -> list[str]:
    """Pfade, die vom ausgecheckten Commit abweichen; ``["? (git status: <Ursache>)"]`` = unklar.

    Einzige Stelle für „was zählt als schmutziger Arbeitsbaum“: das Gate (``pruef.pruefen``)
    und Regel AB (``leiter``) nutzen beide diese Funktion (#218).
    Geprüft wird der Arbeitsbaum, das Urteil gilt aber dem SHA — beides muss gleich sein, sonst
    gibt es Grün für einen Stand, den keiner committet hat. Ausgenommen sind nur ungetrackte
    Werkzeug-Reste nach ``cache.ist_werkzeug_rest`` (bekannte Laufdateien unter ``.to-spawn/``,
    Caches, Installationen); jede andere neue Datei, auch unter ``.to-spawn/``, zählt.
    Per ``.gitignore`` Verstecktes meldet ``git status`` nicht — das deckt ``cache.schluessel`` ab.
    Die Konfig ``.to-spawn/config.json`` zählt immer — auch neu oder per ``.gitignore`` versteckt.
    ``normal`` fasst neue Ordner zu ``ordner/`` zusammen.
    """
    offen, kurz = _offen(repo)
    return [f"? (git status: {kurz})"] if kurz else offen


def _offen(repo: Path) -> tuple[list[str], str]:
    """Kern von ``ungespeichert``: (schmutzige Pfade, Ursache); Ursache ≠ "" = git gescheitert."""
    konfig = config.KONFIG_PFAD.as_posix()
    lauf = config.KONFIG_PFAD.parent.as_posix()
    raus, kurz = _git_status(repo, "--untracked-files=normal", "--", ".", f":(exclude){lauf}")
    lauf_raus = konfig_raus = ""
    if not kurz:
        # Unter ``.to-spawn/`` Datei für Datei, damit nur bekannte Laufdateien herausfallen.
        lauf_raus, kurz = _git_status(repo, "--untracked-files=all", "--", lauf)
    if not kurz:
        konfig_raus, kurz = _git_status(repo, "--untracked-files=all", "--ignored=matching", "--", konfig)
    if kurz:
        log.warning("git status in %s gescheitert: %s", repo, kurz)
        return [], kurz
    offen: list[str] = []
    eintraege = iter(f"{raus}\0{lauf_raus}".split("\0"))
    for eintrag in eintraege:
        if not eintrag:
            continue
        art, pfad = eintrag[:2], eintrag[3:]
        if art[0] in "RC":
            next(eintraege, None)  # -z: Quellpfad einer Umbenennung folgt als eigener Eintrag
        if art == "??" and cache.ist_werkzeug_rest(repo, pfad):
            continue
        offen.append(pfad)
    if konfig_raus.strip("\0") and konfig not in offen:
        offen.append(konfig)
    return offen, ""


def _doku_muster(repo: Path, basis: str, pfade: list[str]) -> tuple[list[str], str]:
    """(Muster, Grund): erklärte Doku-Pfade aus dem Stand ``basis``; leere Liste = keine Abkürzung.

    Einzige Stelle der Regel „der geprüfte Commit bestimmt nie selbst, wie streng er geprüft
    wird“: Die Konfig kommt aus ``basis`` (nie aus dem Arbeitsbaum, auch kein Symlink dorthin),
    und ändert der Commit sie selbst (oder ``.to-spawn`` als Ganzes), gibt es nie die Abkürzung.
    Fehlt sie in ``basis`` oder ist sie kaputt (Warnung): keine Muster, die Leiter prüft voll.
    """
    if any(pfad.casefold() in _KONFIG_PFADE for pfad in pfade):
        return [], KONFIG_GEAENDERT
    code, text = _git(repo, "show", f"{basis}:{config.KONFIG_PFAD.as_posix()}")
    if code != 0:
        return [], OHNE_DOKU_PFADE
    try:
        eigen = json.loads(text)
    except ValueError as fehler:
        log.warning("Konfig in %s unlesbar — keine Doku-Pfade: %s", basis, fehler)
        return [], OHNE_DOKU_PFADE
    pruefen = eigen.get("pruefen") if isinstance(eigen, dict) else None
    muster = pruefen.get("doku_pfade") if isinstance(pruefen, dict) else None
    if muster is None:
        return [], OHNE_DOKU_PFADE
    if not isinstance(muster, list) or not muster or not all(isinstance(m, str) and m for m in muster):
        log.warning("%s: %r", DOKU_PFADE_UNGUELTIG, muster)
        return [], DOKU_PFADE_UNGUELTIG
    code_pfade = [pfad for pfad in pfade if not _ist_doku_datei(repo, basis, pfad)]
    zu_weit = next((m for m in muster if _trifft_code(m, code_pfade)), None)
    if zu_weit is not None:
        grund = f"pruefen.doku_pfade zu weit: „{zu_weit}“ trifft Code-Dateien"
        log.warning("%s (Konfig in %s) — keine Abkürzung, volle Prüfung", grund, basis)
        return [], grund
    return muster, ""


#: Stellvertreter für Code-Dateien (je Linter-Endung, Wurzel und Unterordner): Ein Doku-Muster, das
#: einen davon trifft (``**``, ``*``, ``**/*``, ``*.*``, ``*.py`` …), ist zu weit — gilt auch, wenn es
#: aus einem Vorgänger-Commit stammt und die Basis ``HEAD~1`` ist (#164).
_CODE_STELLVERTRETER = tuple(
    f"{ordner}x{endung}" for linter in _LINTER for endung in sorted(linter.endungen) for ordner in ("", "a/b/")
)
#: Erlaubnisliste (einzige Stelle, #164 Fix-Runde 2): nur Dateien mit diesen Endungen (klein) können
#: Doku sein, egal welches Muster sie trifft. Eine Verbotsliste von Code-Endungen ist nie vollständig
#: (``.sh``, ``.json``, ``Makefile``, Shebang-Skripte …). Ohne Endung nie Doku, außer diesen Namen.
_DOKU_ENDUNGEN = frozenset({".md", ".txt", ".rst", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"})
_DOKU_NAMEN = frozenset({"LICENSE", "README", "CHANGELOG"})
#: Konfig mit Doku-Endung (Abhängigkeiten, Build; Name klein, ``fnmatch``): nie Doku (#204).
_KONFIG_NAMEN = ("requirements*.txt", "constraints*.txt", "cmakelists.txt")
_SHEBANG = "#!"


def _ist_doku_datei(repo: Path, basis: str, pfad: str) -> bool:
    """True, wenn ``pfad`` Doku sein kann: Doku-Endung bzw. -Name, kein Konfig-Name/-Ordner, kein Shebang in Zeile 1.

    Shebang zählt im Arbeitsbaum und im Stand ``basis`` (gelöscht, alter Pfad bei Umbenennung);
    ist der Inhalt an keiner der beiden Stellen lesbar: im Zweifel Code. Muster entscheiden zusätzlich.
    """
    datei = PurePosixPath(pfad)
    if not (datei.suffix.lower() in _DOKU_ENDUNGEN if datei.suffix else datei.name in _DOKU_NAMEN):
        return False
    if any(fnmatch.fnmatchcase(datei.name.lower(), muster) for muster in _KONFIG_NAMEN) or any(
        teil.lower() in regeln._R3_ORDNER for teil in datei.parts[:-1]
    ):
        return False
    gelesen = False
    try:
        with (repo / pfad).open("rb") as roh:
            if roh.read(len(_SHEBANG)) == _SHEBANG.encode():
                return False
        gelesen = True
    except FileNotFoundError:
        pass
    except OSError as fehler:
        log.warning("%s unlesbar — gilt als Code: %s", pfad, fehler)
        return False
    code, text = _git(repo, "show", f"{basis}:{pfad}")
    if code == 0 and text.startswith(_SHEBANG):
        return False
    return gelesen or code == 0


def _trifft_code(muster: str, code_pfade: list[str]) -> bool:
    """True, wenn ``muster`` eine Nicht-Doku-Datei trifft — dann taugt die ganze Liste nicht.

    Geprüft gegen Stellvertreter (``**``, ``*.py`` …) und gegen jeden wirklich geänderten Pfad, der
    keine Doku-Datei ist (``app.py``, ``run.sh``, ``Makefile``, Shebang-Skript, ``tests/**`` …, #164).
    """
    return any(fnmatch.fnmatchcase(pfad, muster) for pfad in (*_CODE_STELLVERTRETER, *code_pfade))


def _symlinks(repo: Path, basis: str) -> set[str] | None:
    """Getrackte Symlinks im Index und in ``basis`` (git-Modus 120000); ``None`` = unklar."""
    links: set[str] = set()
    for args in (("ls-files", "-s", "-z"), ("ls-tree", "-r", "-z", basis)):
        code, raus = _git(repo, *args)
        if code != 0:
            return None
        for eintrag in filter(None, raus.split("\0")):
            kopf, _, pfad = eintrag.partition("\t")
            if kopf.split(" ", 1)[0] == _SYMLINK_MODUS:
                links.add(pfad)
    return links


def _nicht_nur_doku(repo: Path, basis: str, pfade: list[str]) -> str:
    """Grund, warum die Abkürzung „nur Doku“ nicht gilt; "" = alle Pfade sind erklärte Doku."""
    muster, grund = _doku_muster(repo, basis, pfade)
    if not muster:
        return grund
    if not all(any(fnmatch.fnmatchcase(pfad, m) for m in muster) for pfad in pfade):
        return NICHT_NUR_DOKU
    links = _symlinks(repo, basis)
    if links is None:
        return SYMLINK_UNKLAR
    if any(pfad in links or (repo / pfad).is_symlink() for pfad in pfade):
        return SYMLINK
    geaenderte = [(repo / pfad).resolve() for pfad in pfade]
    for link in links:
        datei = repo / link
        if not datei.exists() and not datei.is_symlink():
            continue  # nur in basis, im Arbeitsbaum gelöscht — wäre selbst geändert (oben)
        if not datei.is_symlink():
            return SYMLINK  # Windows ohne Symlink-Recht: Ziel nicht auflösbar → im Zweifel voll
        ziel = datei.resolve()
        if any(ziel == pfad or ziel in pfad.parents for pfad in geaenderte):
            return SYMLINK
    return ""


def erklaerte_doku(repo: Path, basis: str, pfade: list[str]) -> frozenset[str]:
    """Die Pfade aus ``pfade``, die erklärte Doku sind (#101 Fix-Runde 1).

    Einzige Stelle für „was ist Doku“, auch für die Rückfall-Regeln in ``regeln``: Muster aus
    ``pruefen.doku_pfade`` im Stand ``basis`` (geprüft gegen alle ``pfade``), Doku-Datei (Endung,
    kein Shebang), keine Symlink-Verbindung. Ohne erklärte Doku-Pfade ist nichts Doku.
    """
    muster, _ = _doku_muster(repo, basis, pfade)
    kandidaten = [
        pfad
        for pfad in pfade
        if muster and _ist_doku_datei(repo, basis, pfad) and any(fnmatch.fnmatchcase(pfad, m) for m in muster)
    ]
    if kandidaten and _nicht_nur_doku(repo, basis, kandidaten):
        return frozenset()  # Symlink-Verbindung oder unklar: im Zweifel keine Doku
    return frozenset(kandidaten)


def _ausgelassen(name: str, adapter: str, grund: str, werkzeug: str = "") -> leiter.Stufe:
    return leiter.Stufe(name, adapter, False, grund, 0.0, "", werkzeug, leiter.AUSGELASSEN, "")


def _lint(repo: Path, linter: _Linter, dateien: list[str]) -> leiter.Stufe:
    programm = linter.finde(repo)
    if programm is None:
        return _ausgelassen(
            "sofort",
            linter.adapter,
            f"Werkzeug fehlt ({linter.name})",
            f"{linter.name}@?",
        )
    version = ausfuehren.lauf([programm, "--version"], repo)
    werkzeug = f"{linter.name}@{(version.ausgabe.split() or ['?'])[-1] if version.code == 0 else '?'}"
    # ponytail: alle Dateien in einem Aufruf; unter Windows bricht das ab ~32k Zeichen Befehlszeile,
    # Upgrade: in Blöcke teilen, wenn so große Änderungen durchs Sofort-Tor gehen.
    befehl = [programm, *linter.argumente, *dateien]
    erg = ausfuehren.lauf(befehl, repo)
    # ruff/ESLint: 0 sauber, 1 Fehler im Code, sonst Werkzeug kaputt (Konfig) → ausgelassen, voll prüft.
    if erg.code not in (0, 1):
        kurz = (erg.ausgabe.strip().splitlines() or ["?"])[-1]
        return _ausgelassen(
            "sofort",
            linter.adapter,
            f"{linter.name} lief nicht (Exit {erg.code}: {kurz})",
            werkzeug,
        )
    ergebnis = leiter.GRUEN if erg.code == 0 else leiter.ROT
    return leiter.Stufe(
        "sofort",
        linter.adapter,
        True,
        f"{len(dateien)} geänderte Datei(en)",
        round(erg.dauer_s, 2),
        " ".join(befehl),
        werkzeug,
        ergebnis,
        erg.ausgabe if ergebnis != leiter.GRUEN else "",
    )


def stufen(repo: Path, basis: str) -> tuple[list[leiter.Stufe], str]:
    """Stufen „sofort“ + „doku“ für den Arbeitsbaum gegen ``basis``; zweiter Wert = Halt-Grund."""
    pfade = geaendert(repo, basis)
    if pfade is None:
        grund = "geänderte Dateien unbekannt (git diff gescheitert)"
        return [_ausgelassen("sofort", "", grund), _ausgelassen("doku", "", grund)], ""
    if not pfade:
        # Nichts geändert: für Stufe 1/2 nichts zu tun, die Leiter prüft voll (nie Grün ohne Lauf).
        return [], ""
    liste: list[leiter.Stufe] = []
    for linter in _LINTER:
        dateien = [
            pfad for pfad in pfade if PurePosixPath(pfad).suffix.lower() in linter.endungen and (repo / pfad).is_file()
        ]
        if dateien:
            liste.append(_lint(repo, linter, dateien))
    if not liste:
        liste.append(_ausgelassen("sofort", "", "keine Code-Datei geändert"))
    beleg_ordner = belege_ordner_liste(repo, basis)
    beleg_pfade = [pfad for pfad in pfade if ist_im_beleg_ordner(pfad, beleg_ordner)]
    if beleg_pfade:
        beleg_start = time.monotonic()
        beleg_erg = pruefe_beleg_dateien(repo, beleg_pfade, ordner=beleg_ordner)
        beleg_dauer = round(time.monotonic() - beleg_start, 2)
        liste.append(
            leiter.Stufe(
                "beleg_schluessel",
                "",
                True,
                f"{beleg_erg.dateien_geprueft} Belegdatei(en) sauber"
                if beleg_erg.ok
                else f"{len(beleg_erg.funde)} Schlüssel-/Secret-Fund(e) im Beleg-Ordner",
                beleg_dauer,
                "to_spawn.pruef.sofort.pruefe_beleg_dateien",
                "beleg_schluessel",
                leiter.GRUEN if beleg_erg.ok else leiter.ROT,
                "" if beleg_erg.ok else beleg_erg.bericht(),
            )
        )
    if any(stufe.ergebnis == leiter.ROT for stufe in liste):
        return [*liste, _ausgelassen("doku", "", ROT_HALT)], ROT_HALT
    doku_start = time.monotonic()
    grund = _nicht_nur_doku(repo, basis, pfade)
    nur_doku = not grund
    liste.append(
        leiter.Stufe(
            "doku",
            "",
            True,
            grund or NUR_DOKU,
            round(time.monotonic() - doku_start, 2),
            "",
            "",
            leiter.GRUEN,
            "",
        )
    )
    return liste, NUR_DOKU if nur_doku else ""


# --- Einheitlicher Stufe-0-Ablauf (vor dem Push / in der CI, #794) -----------------------------

_SHELL_ZEICHEN = ("&&", "||", ";", "|", ">", "<", "$", "`", "\n")


@dataclass(frozen=True)
class Stufe0Ergebnis:
    """Gesamtergebnis von :func:`stufe0` (ruff, Typ-Prüfung, Test-Schutz)."""

    ok: bool
    exit_code: int
    stufen: tuple[leiter.Stufe, ...]
    fehler_kurz: str
    dauer_s: float
    test_schutz: test_schutz.Ergebnis | None = None
    beleg_schluessel: BelegSchluesselErgebnis | None = None

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "dauer_s": self.dauer_s,
            "fehler_kurz": self.fehler_kurz,
            "stufen": [
                {
                    "name": s.name,
                    "adapter": s.adapter,
                    "lief": s.lief,
                    "grund": s.grund,
                    "dauer_s": s.dauer_s,
                    "befehl": s.befehl,
                    "werkzeug": s.werkzeug,
                    "ergebnis": s.ergebnis,
                }
                for s in self.stufen
            ],
            "test_schutz": self.test_schutz.als_dict() if self.test_schutz is not None else None,
            "beleg_schluessel": self.beleg_schluessel.als_dict() if self.beleg_schluessel is not None else None,
        }

    def bericht(self) -> str:
        status = "grün" if self.ok else ("gate-fehler" if self.exit_code == leiter.GATE_FEHLER else "rot")
        zeilen = [f"Stufe 0 {status} ({self.dauer_s:.2f} s):"]
        for s in self.stufen:
            marke = {
                leiter.GRUEN: "grün",
                leiter.ROT: "rot",
                leiter.GATE_FEHLER: "gate-fehler",
                leiter.AUSGELASSEN: "ausgelassen",
            }.get(s.ergebnis, str(s.ergebnis))
            zeilen.append(f"- {s.name}: {marke} ({s.grund})")
        if self.fehler_kurz:
            zeilen.append(self.fehler_kurz)
        return "\n".join(zeilen)


def geaendert_zwischen(repo: Path, basis: str, ziel: str | None = None) -> list[str] | None:
    """Geänderte Dateien zwischen ``basis`` und ``ziel`` (``None`` = Arbeitsbaum gegen ``basis``)."""
    if ziel is None:
        return geaendert(repo, basis)
    code, raus = _git(repo, "diff", "--name-only", "--no-renames", "-z", basis, ziel, "--")
    if code != 0:
        return None
    return sorted({pfad for pfad in raus.split("\0") if pfad})


_geaendert_zwischen = geaendert_zwischen


def _konfig_aus_basis(repo: Path, basis: str) -> dict[str, Any] | None:
    code, text = _git(repo, "show", f"{basis}:{config.KONFIG_PFAD.as_posix()}")
    if code != 0:
        return None
    try:
        daten = json.loads(text)
    except ValueError as fehler:
        log.warning("Konfig in %s unlesbar — fail-closed Vorgaben für Stufe 0: %s", basis, fehler)
        return None
    return daten if isinstance(daten, dict) else None


def _stufe0_konfig_sicher(
    repo: Path,
    basis: str,
    *,
    ruff_befehl: str | None = None,
    typ_befehl: str | None = None,
    test_schutz_aktiv: bool | None = None,
) -> config.Stufe0Konfig:
    """Liest die Stufe-0-Konfig fail-closed: ein Commit darf Basis-Prüfungen nicht abschalten."""
    basis_daten = _konfig_aus_basis(repo, basis)
    basis_cfg = config.stufe0_konfig(basis_daten or {})
    baum_cfg = config.stufe0_konfig(config.lade(repo))

    ruff = ruff_befehl.strip() if ruff_befehl is not None else (basis_cfg.ruff or baum_cfg.ruff)
    typ = typ_befehl.strip() if typ_befehl is not None else (basis_cfg.typ_pruefung or baum_cfg.typ_pruefung)
    # Test-Schutz: Basis entscheidet; ohne Basis-Konfig gilt True (Arbeitsbaum darf nie abschalten).
    schutz = test_schutz_aktiv if test_schutz_aktiv is not None else basis_cfg.test_schutz
    return config.Stufe0Konfig(ruff=ruff, typ_pruefung=typ, test_schutz=schutz)


def _zerlege_befehl(befehl_text: str) -> list[str]:
    """Zerlegt einen Einzelbefehl plattformübergreifend (inkl. Windows-Pfaden mit ``\\``)."""
    if sys.platform == "win32" and "\\" in befehl_text:
        return [
            teil[1:-1] if len(teil) >= 2 and teil[0] == teil[-1] and teil[0] in ('"', "'") else teil
            for teil in shlex.split(befehl_text, posix=False)
        ]
    return shlex.split(befehl_text, posix=True)


def _befehl_ausfuehren(repo: Path, name: str, befehl_text: str) -> leiter.Stufe:
    """Führt einen konfigurierten Stufe-0-Befehl plattformübergreifend (Linux + Windows) aus."""
    if any(zeichen in befehl_text for zeichen in _SHELL_ZEICHEN):
        start = time.monotonic()
        try:
            erg = bash_wahl.lauf(
                ["-c", befehl_text],
                cwd=repo,
                env={**os.environ, **temp_ordner.umgebung(ordner=repo, von="pruef", wiederverwenden=True)},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as fehler:
            text = f"bash für {name} fehlt: {fehler}"
            return leiter.Stufe(name, "", False, text, 0.0, befehl_text, "bash", leiter.GATE_FEHLER, text)
        dauer = round(time.monotonic() - start, 2)
        ausgabe = erg.stdout or ""
        if erg.returncode == 0:
            return leiter.Stufe(name, "", True, "sauber", dauer, befehl_text, "bash", leiter.GRUEN, "")
        if erg.returncode == ausfuehren.NICHT_GESTARTET:
            return leiter.Stufe(
                name,
                "",
                False,
                f"Werkzeug nicht gefunden (Exit {erg.returncode})",
                dauer,
                befehl_text,
                "bash",
                leiter.GATE_FEHLER,
                ausgabe,
            )
        return leiter.Stufe(
            name,
            "",
            True,
            f"Exit {erg.returncode}",
            dauer,
            befehl_text,
            "bash",
            leiter.ROT,
            ausgabe or f"Exit {erg.returncode}",
        )

    try:
        argv = _zerlege_befehl(befehl_text)
    except ValueError as fehler:
        text = f"Befehl für {name} ungültig: {fehler}"
        return leiter.Stufe(name, "", False, text, 0.0, befehl_text, "", leiter.GATE_FEHLER, text)
    if not argv:
        return _ausgelassen(name, "", "Befehl leer")

    programm = argv[0]
    if "/" not in programm and "\\" not in programm:
        gefunden = _finde_werkzeug(repo, programm)
        if gefunden is not None:
            argv = [gefunden, *argv[1:]]
    erg_lauf = ausfuehren.lauf(argv, repo)
    werkzeug = PurePosixPath(argv[0].replace("\\", "/")).name
    if erg_lauf.code == 0:
        return leiter.Stufe(
            name, "", True, "sauber", round(erg_lauf.dauer_s, 2), befehl_text, werkzeug, leiter.GRUEN, ""
        )
    if erg_lauf.code == ausfuehren.NICHT_GESTARTET:
        return leiter.Stufe(
            name,
            "",
            False,
            f"Werkzeug fehlt ({programm})",
            round(erg_lauf.dauer_s, 2),
            befehl_text,
            werkzeug,
            leiter.GATE_FEHLER,
            erg_lauf.ausgabe,
        )
    return leiter.Stufe(
        name,
        "",
        True,
        f"Exit {erg_lauf.code}",
        round(erg_lauf.dauer_s, 2),
        befehl_text,
        werkzeug,
        leiter.ROT,
        erg_lauf.ausgabe or f"Exit {erg_lauf.code}",
    )


def _ruff_stufe0(repo: Path, basis: str, ziel: str | None, befehl: str) -> leiter.Stufe:
    if befehl:
        return _befehl_ausfuehren(repo, "ruff", befehl)
    pfade = _geaendert_zwischen(repo, basis, ziel)
    if pfade is None:
        return _ausgelassen("ruff", "py", "geänderte Dateien unbekannt (git diff gescheitert)")
    ruff_linter = _LINTER[0]
    dateien = [
        pfad for pfad in pfade if PurePosixPath(pfad).suffix.lower() in ruff_linter.endungen and (repo / pfad).is_file()
    ]
    if not dateien:
        return _ausgelassen("ruff", "py", "keine Python-Datei geändert")
    stufe = _lint(repo, ruff_linter, dateien)
    return leiter.Stufe(
        "ruff",
        stufe.adapter,
        stufe.lief,
        stufe.grund,
        stufe.dauer_s,
        stufe.befehl,
        stufe.werkzeug,
        stufe.ergebnis,
        stufe.ausgabe,
    )


def _test_schutz_stufe0(
    repo: Path, basis: str, ziel: str | None, *, aktiv: bool
) -> tuple[leiter.Stufe, test_schutz.Ergebnis | None]:
    if not aktiv:
        return _ausgelassen("test_schutz", "py", "Test-Schutz per Basis-Konfig abgeschaltet"), None
    start = time.monotonic()
    erg = test_schutz.pruefe_diff(repo, basis, ziel)
    dauer = round(time.monotonic() - start, 2)
    befehl = f"to_spawn.pruef.test_schutz --basis {basis}" + (f" --ziel {ziel}" if ziel else "")
    if erg.exit_code == test_schutz.EXIT_FEHLER:
        return (
            leiter.Stufe(
                "test_schutz", "py", False, erg.hinweis, dauer, befehl, "test_schutz", leiter.GATE_FEHLER, erg.hinweis
            ),
            erg,
        )
    if erg.ok:
        grund = f"{erg.zaehler.dateien_geprueft} Testdatei(en) geprüft"
        return leiter.Stufe("test_schutz", "py", True, grund, dauer, befehl, "test_schutz", leiter.GRUEN, ""), erg
    grund = f"{len(erg.verstoesse)} Verstoß/Verstöße"
    return (
        leiter.Stufe("test_schutz", "py", True, grund, dauer, befehl, "test_schutz", leiter.ROT, erg.bericht()),
        erg,
    )


def _beleg_schluessel_stufe0(repo: Path, basis: str, ziel: str | None) -> tuple[leiter.Stufe, BelegSchluesselErgebnis]:
    start = time.monotonic()
    erg = pruefe_beleg_diff(repo, basis, ziel)
    dauer = round(time.monotonic() - start, 2)
    befehl = f"to_spawn.pruef.sofort.pruefe_beleg_diff --basis {basis}" + (f" --ziel {ziel}" if ziel else "")
    if erg.dateien_geprueft == 0 and erg.ok:
        return _ausgelassen("beleg_schluessel", "", "keine Belegdatei geändert", "beleg_schluessel"), erg
    if erg.ok:
        return (
            leiter.Stufe(
                "beleg_schluessel",
                "",
                True,
                f"{erg.dateien_geprueft} Belegdatei(en) sauber",
                dauer,
                befehl,
                "beleg_schluessel",
                leiter.GRUEN,
                "",
            ),
            erg,
        )
    return (
        leiter.Stufe(
            "beleg_schluessel",
            "",
            True,
            f"{len(erg.funde)} Schlüssel-/Secret-Fund(e) im Beleg-Ordner",
            dauer,
            befehl,
            "beleg_schluessel",
            leiter.ROT,
            erg.bericht(),
        ),
        erg,
    )


def stufe0(
    repo: Path,
    basis: str = "HEAD",
    ziel: str | None = None,
    *,
    ruff_befehl: str | None = None,
    typ_befehl: str | None = None,
    test_schutz_aktiv: bool | None = None,
) -> Stufe0Ergebnis:
    """Einheitlicher Stufe-0-Ablauf (vor dem Push und in der CI): ruff, Typ-Prüfung, Test-Schutz, Beleg-Sperre."""
    start = time.monotonic()
    wurzel = config.repo_wurzel(repo)
    cfg = _stufe0_konfig_sicher(
        wurzel,
        basis,
        ruff_befehl=ruff_befehl,
        typ_befehl=typ_befehl,
        test_schutz_aktiv=test_schutz_aktiv,
    )
    ruff_stufe = _ruff_stufe0(wurzel, basis, ziel, cfg.ruff)
    if cfg.typ_pruefung:
        typ_stufe = _befehl_ausfuehren(wurzel, "typ_pruefung", cfg.typ_pruefung)
    else:
        typ_stufe = _ausgelassen("typ_pruefung", "", "Typ-Prüfung nicht konfiguriert (pruefung.typ_pruefung)")
    schutz_stufe, schutz_erg = _test_schutz_stufe0(wurzel, basis, ziel, aktiv=cfg.test_schutz)
    beleg_stufe, beleg_erg = _beleg_schluessel_stufe0(wurzel, basis, ziel)

    liste = [ruff_stufe, typ_stufe, schutz_stufe]
    if beleg_erg.dateien_geprueft > 0 or not beleg_erg.ok:
        liste.append(beleg_stufe)
    exit_code, fehler_kurz = leiter.ergebnis(liste)
    return Stufe0Ergebnis(
        ok=exit_code == leiter.GRUEN,
        exit_code=exit_code,
        stufen=tuple(liste),
        fehler_kurz=fehler_kurz,
        dauer_s=round(time.monotonic() - start, 2),
        test_schutz=schutz_erg,
        beleg_schluessel=beleg_erg,
    )


def _stufe0_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.sofort",
        description="Einheitlicher Stufe-0-Ablauf: ruff, konfigurierbare Typ-Prüfung und Test-Schutz.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner (Vorgabe: aktueller Ordner)")
    parser.add_argument(
        "--basis", "--base", dest="basis", default="HEAD", help="Ausgangsstand (git-Ref, Vorgabe: HEAD)"
    )
    parser.add_argument(
        "--ziel",
        default=None,
        help="Zielstand (git-Ref, z. B. HEAD in CI; weglassen = Arbeitsbaum gegen Basis prüfen)",
    )
    parser.add_argument("--ruff-befehl", default=None, help="Überschreibt pruefung.ruff")
    parser.add_argument("--typ-befehl", default=None, help="Überschreibt pruefung.typ_pruefung")
    parser.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout ausgeben")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    konsole.utf8_ausgabe()
    args = _stufe0_parser().parse_args(argv)
    erg = stufe0(
        args.repo,
        args.basis,
        args.ziel,
        ruff_befehl=args.ruff_befehl,
        typ_befehl=args.typ_befehl,
    )
    if args.als_json:
        sys.stdout.write(json.dumps(erg.als_dict(), ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(erg.bericht() + "\n")
    return erg.exit_code


if __name__ == "__main__":
    sys.exit(main())
