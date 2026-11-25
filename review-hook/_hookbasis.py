"""Gemeinsame Basis der Review-Hooks (VERTRAG.md §8).

Grundsatz: Hooks dürfen die Arbeit nie lahmlegen. Jeder Fehler → Exit 0 ohne Ausgabe,
Zeile nach hook-fehler.log. `REVIEW_AUS=1` schaltet alle Review-Hooks stumm.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _review_ordner() -> Path:
    """Ort des Review-Werkzeugs: im Repo direkt daneben (``<repo>/review``),
    installiert unter ``~/.claude/hooks/review/`` zwei Ebenen höher (``~/.claude/review``)."""
    eltern = Path(__file__).resolve().parents
    for ebene in (1, 2):
        if ebene < len(eltern):
            kandidat = eltern[ebene] / "review"
            if (kandidat / "review_route.py").is_file():
                return kandidat
    return eltern[2] / "review"


def _ohne_fenster() -> dict[str, Any]:
    """Unter Windows ohne sichtbares Konsolenfenster (nie DETACHED_PROCESS)."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


REVIEW = _review_ordner()
ROUTE = REVIEW / "review_route.py"
BELEG = REVIEW / "review_beleg.py"
DECKEL_S = 8

LOG = logging.getLogger("review.hook")


def sitzungen_ordner() -> Path:
    return Path(os.environ.get("REVIEW_SITZUNGEN") or REVIEW / "sitzungen")


def fehler_log_pfad() -> Path:
    return Path(os.environ.get("REVIEW_HOOK_LOG") or REVIEW / "hook-fehler.log")


def _logging_einrichten(name: str) -> None:
    pfad = fehler_log_pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(pfad, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter(f"%(asctime)s {name} %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.WARNING)


def sitzung_pfad(session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", session_id) or ".." in session_id:
        raise ValueError(f"session_id ungültig: {session_id!r}")
    return sitzungen_ordner() / f"{session_id}.json"


def lade_sitzung(session_id: str) -> dict[str, Any]:
    pfad = sitzung_pfad(session_id)
    if not pfad.is_file():
        return {"dateien": [], "bloecke": {}}
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    daten.setdefault("dateien", [])
    daten.setdefault("bloecke", {})
    return daten


def speichere_sitzung(session_id: str, daten: dict[str, Any]) -> None:
    # ponytail: kein Datei-Lock, parallele Subagent-Edits können sich selten überschreiben; Lock-Datei wenn das stört.
    pfad = sitzung_pfad(session_id)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, pfad)


def sitzung_dateien_log_pfad(session_id: str) -> Path:
    """Anhänge-Log neben der Sitzungsdatei (Beleg 11: race-freies Backup für gemerkte
    Dateien, falls parallele Subagent-Edits sich beim JSON-Speichern überschreiben)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", session_id) or ".." in session_id:
        raise ValueError(f"session_id ungültig: {session_id!r}")
    return sitzungen_ordner() / f"{session_id}.dateien.jsonl"


def dateien_anhaengen(session_id: str, text: str) -> None:
    """Eine Datei race-frei ans Log anhängen: O_APPEND + genau ein os.write je Zeile,
    dadurch keine Kollision mit parallelen Schreibern (anders als das JSON, das komplett
    neu geschrieben wird)."""
    pfad = sitzung_dateien_log_pfad(session_id)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zeile = (json.dumps(text, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(pfad, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
    try:
        os.write(fd, zeile)
    finally:
        os.close(fd)


def lade_dateien_log(session_id: str) -> list[str]:
    pfad = sitzung_dateien_log_pfad(session_id)
    if not pfad.is_file():
        return []
    ergebnis: list[str] = []
    for zeile in pfad.read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            wert = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if isinstance(wert, str):
            ergebnis.append(wert)
    return ergebnis


def befehl(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=DECKEL_S,
        **_ohne_fenster(),
    )


def python_skript(skript: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return befehl([sys.executable, str(skript), *args])


def route(*args: str) -> dict[str, Any]:
    ergebnis = python_skript(ROUTE, *args)
    if ergebnis.returncode != 0:
        raise RuntimeError(f"review_route Exit {ergebnis.returncode}: {ergebnis.stderr.strip()}")
    return json.loads(ergebnis.stdout)


def git_wurzel(ordner: Path) -> Path | None:
    ergebnis = befehl(["git", "-C", str(ordner), "rev-parse", "--show-toplevel"])
    if ergebnis.returncode != 0 or not ergebnis.stdout.strip():
        return None
    return Path(ergebnis.stdout.strip()).resolve()


def arg(text: str) -> str:
    """Pfad für die Anweisung an Claude, bei Leerzeichen in Anführungszeichen."""
    return f'"{text}"' if " " in text else text


def ausfuehren(name: str, haupt: Callable[[dict[str, Any]], dict[str, Any] | None]) -> None:
    if os.environ.get("REVIEW_AUS") == "1":
        sys.exit(0)
    try:
        _logging_einrichten(name)
        eingabe = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(eingabe, dict):
            raise ValueError("stdin ist kein JSON-Objekt")
        ausgabe = haupt(eingabe)
        if ausgabe:
            sys.stdout.buffer.write(json.dumps(ausgabe, ensure_ascii=False).encode("utf-8"))
            sys.stdout.buffer.flush()
    except Exception as fehler:  # noqa: BLE001 — Hook darf nie blockieren
        try:
            LOG.error("%s: %s", type(fehler).__name__, fehler)
        except Exception:  # noqa: BLE001, S110 — Log-Ausfall darf auch nicht blockieren
            pass
    sys.exit(0)
