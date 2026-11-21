"""Stufe 1 Sofort-Prüfung und Stufe 2 „nur Doku“ des Prüf-Werkzeugkastens (Paket-intern, #100).

Schnittstelle zur Leiter: ``stufen(repo, basis) -> (liste, halt)``. ``liste`` sind die Stufen
„sofort“ und „doku“ fürs Protokoll; ``halt`` ≠ "" heißt, die übrigen Stufen laufen nicht (Grund
steht drin: „nur Doku“ oder schnell rot). Versteckt hier: welche Dateien geändert sind (git, auch
ungespeicherte und neue), was als Doku gilt, welcher Linter zu welcher Endung gehört und wo er liegt.

Kein falsches Grün: fehlt ein Linter oder ist unklar, was geändert ist, wird die Stufe
ausgelassen (``lief=False`` + Grund) und die Leiter prüft voll weiter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from to_spawn.pruef import ausfuehren, leiter

NUR_DOKU = "nur Doku"
ROT_HALT = "Stufe sofort rot (schnell rot)"

#: Doku = nur diese Endungen (Erlaubnis-Liste: Unbekanntes ist nie Doku). ``.txt`` fehlt bewusst
#: (``requirements.txt``).
_DOKU_ENDUNGEN = frozenset({".md", ".markdown", ".rst", ".adoc"})
#: Gesucht wird in allen getrackten Dateien außer Doku (Code, Tests, Konfig, Skripte).
_NICHT_DOKU_PFADE = (
    ".",
    *(f":(exclude,glob)**/*{endung}" for endung in sorted(_DOKU_ENDUNGEN)),
)


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
    """Geänderte Pfade (``/``, relativ zum Repo) von ``basis`` bis Arbeitsbaum; ``None`` = unklar."""
    code, raus = _git(repo, "diff", "--name-only", "--no-renames", "-z", basis, "--")
    if code != 0:
        return None
    neu_code, neu = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if neu_code != 0:
        return None
    return sorted({pfad for pfad in (raus + neu).split("\0") if pfad})


def _ist_doku(repo: Path, pfad: str) -> bool:
    if PurePosixPath(pfad).suffix.lower() not in _DOKU_ENDUNGEN:
        return False
    # Im Zweifel keine Doku: Kommt in irgendeiner Nicht-Doku-Datei der Dateiname, ein Ordnername
    # des Pfads (Code liest den Ordner) oder das Endungs-Glob (``*.md``) vor, liest Code sie
    # womöglich zur Laufzeit. -i: Windows-Dateisystem ignoriert Groß/klein. Teilwort-Treffer
    # machen nur vorsichtiger. git grep: 0 gefunden, 1 nicht gefunden, sonst unklar → keine Doku.
    # ponytail: Code, der Doku über berechnete Pfade ohne diese Wörter liest, fällt durch;
    # Upgrade: Konfig-Schlüssel pruefen.doku_muster je Repo, wenn ein solcher Fall auftaucht.
    teile = PurePosixPath(pfad)
    muster = [teile.name, *teile.parts[:-1], f"*{teile.suffix}"]
    suche = [arg for wort in muster for arg in ("-e", wort)]
    code, _ = _git(repo, "grep", "-q", "-i", "-F", *suche, "--", *_NICHT_DOKU_PFADE)
    return code == 1


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
    nur_doku = all(_ist_doku(repo, pfad) for pfad in pfade)
    liste.append(
        leiter.Stufe(
            "doku",
            "",
            True,
            NUR_DOKU if nur_doku else "nicht nur Doku geändert",
            round(time.monotonic() - doku_start, 2),
            "",
            "",
            leiter.GRUEN,
            "",
        )
    )
    return liste, NUR_DOKU if nur_doku else ""
