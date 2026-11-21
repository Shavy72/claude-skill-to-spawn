"""Rückfall-Regeln R1–R3: wann die Auswahl betroffener Tests nicht reicht (Paket-intern, #101).

Einzige Stelle dieses Wissens (PRUEFTOR_VERGLEICH 4.2/6.4). Die Leiter fragt ``vorab`` vor der
Auswahl und ``nach_auswahl`` danach; ``""`` heißt „Auswahl reicht“, sonst steht der Grund fürs
Protokoll drin und die Leiter prüft voll. Pfade kommen relativ zum Repo; ``\\`` wird vor jedem
Abgleich zu ``/`` (Windows).

- **R1** Nicht-Code-Datei geändert (außer reiner Doku) → voll: Daten-, Template-, Konfig-Dateien und
  Skripte sieht kein Import-Graph.
- **R2** Code-Datei geändert und Auswahl wählt 0 Tests, oder Auswahl nicht zählbar → voll („nicht bestimmbar“): z. B. dynamisch
  geladene Module oder ``mock.patch``-Strings ohne statischen Importeur.
- **R3** Prüf-Konfig, Abhängigkeiten, ``conftest.py``, Test-Setup oder ``pruefen.risiko_pfade`` aus
  ``.to-spawn/config.json`` geändert → voll.
- **LF** Der letzte Lauf im Protokoll war rot → voll; so laufen die zuletzt roten Tests sicher mit
  (``--lf`` als Obermenge, für Python und Node gleich).

Eingefroren mit #101: Stand dieser Datei steht als ``regeln@<sha7>`` (git-Blob-SHA, prüfbar mit
``git hash-object``) in jeder Protokoll-Zeile unter ``werkzeug``. Änderung nur mit neuer Messreihe
(M12 b).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath

#: Code-Endungen je Adapter; alles andere ist für diesen Adapter Nicht-Code (R1).
CODE_ENDUNGEN = {
    "py": frozenset({".py", ".pyi"}),
    "node": frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}),
}
#: Reine Doku (R1-Ausnahme): überall nur Markdown und Bilder, unter ``docs/`` auch Text-Doku.
_DOKU_UEBERALL = frozenset({".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_DOKU_IN_DOCS = _DOKU_UEBERALL | {".rst", ".txt", ".adoc", ".pdf"}
#: R3: Prüf-Konfig, Abhängigkeiten, Test-Setup (Muster auf den Dateinamen, Groß/klein egal).
_R3_NAMEN = (
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "requirements*.txt",
    "constraints*.txt",
    "uv.lock",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "vitest.config.*",
    "vitest.workspace.*",
    "vite.config.*",
    "jest.config.*",
    "tsconfig*.json",
    "setup.js",
    "setup.ts",
)
_KONFIG = PurePosixPath(".to-spawn") / "config.json"
#: Urteil der letzten Protokoll-Zeile, das LF auslöst.
_ROT = "rot"


def normal(pfad: str) -> str:
    """Pfad für jeden Abgleich: ``\\`` → ``/``, ohne führendes ``./``."""
    pfad = pfad.replace("\\", "/")
    return pfad[2:] if pfad.startswith("./") else pfad


def _ist_doku(pfad: str) -> bool:
    datei = PurePosixPath(pfad)
    endung = datei.suffix.lower()
    if pfad.startswith("docs/"):
        return endung in _DOKU_IN_DOCS
    return endung in _DOKU_UEBERALL


def _ist_r3(pfad: str, risiko: list[str]) -> bool:
    name = PurePosixPath(pfad).name.lower()
    return any(fnmatch.fnmatchcase(name, muster) for muster in _R3_NAMEN) or any(
        fnmatch.fnmatchcase(pfad, normal(muster)) for muster in risiko
    )


def risiko_pfade(repo: Path) -> list[str] | None:
    """``pruefen.risiko_pfade`` aus der Konfig im Arbeitsbaum; ``None`` = Konfig kaputt (→ voll).

    Arbeitsbaum statt Basis ist hier sicher: Ändert der Commit die Konfig, greift schon R1 (``.json``).
    """
    datei = repo / _KONFIG
    if not datei.is_file():
        return []
    try:
        eigen = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pruefen = eigen.get("pruefen") if isinstance(eigen, dict) else None
    muster = pruefen.get("risiko_pfade", []) if isinstance(pruefen, dict) else []
    if not isinstance(muster, list) or not all(isinstance(m, str) and m for m in muster):
        return None
    return muster


def vorab(
    pfade: list[str] | None,
    adapter: str,
    risiko: list[str] | None,
    letztes_urteil: str = "",
    r1: bool = True,
) -> str:
    """Grund für „voll“ vor der Auswahl; ``""`` = Auswahl darf laufen. ``r1=False`` nur im Rot-Beweis."""
    if pfade is None:
        return "geänderte Dateien unbekannt (git diff gescheitert)"
    if not pfade:
        return "nichts geändert (nie Grün ohne Lauf)"
    if risiko is None:
        return "R3: pruefen.risiko_pfade unlesbar"
    if letztes_urteil == _ROT:
        return "LF: letzter Lauf rot, zuletzt rote Tests laufen mit"
    liste = [normal(pfad) for pfad in pfade]
    r3 = next((pfad for pfad in liste if _ist_r3(pfad, risiko)), None)
    if r3 is not None:
        return f"R3: Prüf-Konfig/Abhängigkeit geändert ({r3})"
    code = CODE_ENDUNGEN.get(adapter, frozenset())
    fremd = next(
        (pfad for pfad in liste if PurePosixPath(pfad).suffix.lower() not in code and not _ist_doku(pfad)),
        None,
    )
    if r1 and fremd is not None:
        return f"R1: Nicht-Code-Datei geändert ({fremd})"
    return ""


def nach_auswahl(n: int | None, pfade: list[str], adapter: str) -> str:
    """Grund für „voll“ nach der Auswahl (R2); ``""`` = Auswahl ist sicher.

    0 Tests ohne geänderte Code-Datei ist richtig (nur Doku, die R1 durchlässt).
    """
    if n is None:
        return "R2: Auswahl nicht zählbar"
    code = CODE_ENDUNGEN.get(adapter, frozenset())
    if n == 0 and any(PurePosixPath(normal(pfad)).suffix.lower() in code for pfad in pfade):
        return "R2: Code geändert, Auswahl wählt 0 Tests (nicht bestimmbar)"
    return ""


def stand() -> str:
    """``regeln@<sha7>``: git-Blob-SHA dieser Datei (gleich ``git hash-object regeln.py``)."""
    inhalt = Path(__file__).read_bytes()
    return "regeln@" + hashlib.sha1(b"blob %d\0" % len(inhalt) + inhalt, usedforsecurity=False).hexdigest()[:7]
