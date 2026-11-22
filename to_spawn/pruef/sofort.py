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
eine Doku-Endung hat (Erlaubnisliste ``_DOKU_ENDUNGEN``/``_DOKU_NAMEN``), keine davon ein Symlink ist und kein getrackter Symlink auf sie zeigt. Fehlt der Schlüssel, ist er
ungültig oder die Konfig kaputt: nie Abkürzung, die Leiter prüft voll (fail-closed). Gleiches gilt,
wenn ein Muster eine Nicht-Doku-Datei trifft (``**``, ``*``, ``*.py``, ``*.sh``, ``Makefile``, #164): Die
Basis kann ein Vorgänger-Commit sein, darum prüft die Tür die Weite selbst, statt der Basis-Wahl
des Aufrufers zu vertrauen.

Kein falsches Grün: fehlt ein Linter oder ist unklar, was geändert ist, wird die Stufe
ausgelassen (``lief=False`` + Grund) und die Leiter prüft voll weiter.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from to_spawn import config
from to_spawn.pruef import ausfuehren, cache, leiter

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


@dataclass(frozen=True)
class _Linter:
    adapter: str
    name: str
    endungen: frozenset[str]
    finde: Callable[[Path], str | None]
    argumente: tuple[str, ...]


def _ruff(repo: Path) -> str | None:
    """ruff aus dem venv des Repos (Linux ``bin``, Windows ``Scripts``), sonst aus dem PATH."""
    venv = [str(repo / ordner / teil) for ordner in (".venv", "venv") for teil in ("bin", "Scripts")]
    return shutil.which("ruff", path=os.pathsep.join(venv)) or shutil.which("ruff")


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
        )
    except OSError as fehler:
        return ausfuehren.NICHT_GESTARTET, str(fehler)
    return erg.returncode, erg.stdout if erg.returncode == 0 else erg.stderr


def geaendert(repo: Path, basis: str) -> list[str] | None:
    """Geänderte Pfade (``/``, relativ zum Repo) von ``basis`` bis Arbeitsbaum; ``None`` = unklar.

    Neue (ungetrackte) Dateien zählen nur, wenn ``ungespeichert`` sie als schmutzig meldet —
    Laufdateien unter ``.to-spawn/`` und Werkzeug-Ordner fallen hier wie dort heraus (#218).
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
    gibt es Grün für einen Stand, den keiner committet hat. Ausgenommen: Laufdateien unter
    ``.to-spawn/`` (Protokoll, Marker, Sitzungen) und ungetrackte Werkzeug-Ordner (nur als
    Ordner-Eintrag: Caches in jeder Tiefe, ``.venv``/``venv``/``node_modules`` nur ganz oben).
    Per ``.gitignore`` Verstecktes meldet ``git status`` nicht — das deckt ``cache.schluessel`` ab.
    Die Konfig ``.to-spawn/config.json`` zählt immer — auch neu oder per ``.gitignore`` versteckt.
    ``normal`` fasst neue Ordner zu ``ordner/`` zusammen.
    """
    offen, kurz = _offen(repo)
    return [f"? (git status: {kurz})"] if kurz else offen


def _offen(repo: Path) -> tuple[list[str], str]:
    """Kern von ``ungespeichert``: (schmutzige Pfade, Ursache); Ursache ≠ "" = git gescheitert."""
    konfig = config.KONFIG_PFAD.as_posix()
    raus, kurz = _git_status(repo, "--untracked-files=normal", "--", ".", ":(exclude).to-spawn")
    konfig_raus = ""
    if not kurz:
        konfig_raus, kurz = _git_status(repo, "--untracked-files=all", "--ignored=matching", "--", konfig)
    if kurz:
        log.warning("git status in %s gescheitert: %s", repo, kurz)
        return [], kurz
    offen: list[str] = []
    eintraege = iter(raus.split("\0"))
    for eintrag in eintraege:
        if not eintrag:
            continue
        art, pfad = eintrag[:2], eintrag[3:]
        if art[0] in "RC":
            next(eintraege, None)  # -z: Quellpfad einer Umbenennung folgt als eigener Eintrag
        if art == "??" and cache.ist_werkzeug_ordner(pfad):
            continue
        offen.append(pfad)
    if konfig_raus.strip("\0"):
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
_SHEBANG = "#!"


def _ist_doku_datei(repo: Path, basis: str, pfad: str) -> bool:
    """True, wenn ``pfad`` Doku sein kann: Doku-Endung bzw. -Name und kein Shebang in Zeile 1.

    Shebang zählt im Arbeitsbaum und im Stand ``basis`` (gelöscht, alter Pfad bei Umbenennung);
    ist der Inhalt an keiner der beiden Stellen lesbar: im Zweifel Code. Muster entscheiden zusätzlich.
    """
    datei = PurePosixPath(pfad)
    if not (datei.suffix.lower() in _DOKU_ENDUNGEN if datei.suffix else datei.name in _DOKU_NAMEN):
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
