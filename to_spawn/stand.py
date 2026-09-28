"""Skill-Stand vor dem Server-Start sichern (duoplus-management#325).

Vor ``spawn --ziel srv`` (und per ``to_spawn.py stand``) prüft der Schritt, ob der
Bau-Server den Skill-Stand des PCs hat. Weicht er ab, schiebt er ihn selbst hinüber
und prüft erneut — gestartet wird nur bei Gleichstand. Die Befehle stehen in der
Repo-Konfig unter ``stand`` (``pruefen``/``push`` als Text, Exit 0 = gleich,
3 = abweichend, sonst nicht prüfbar). Fehlen sie, wird der Schritt mit Warnung
übersprungen (Fremdrepo-Verträglichkeit, #257). Zeitlimits: ``timeout_pruefen_s``
(Vorgabe 180) und ``timeout_push_s`` (Vorgabe 900).
"""

from __future__ import annotations

import ast
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.stand")

SKILL_ORDNER = Path(__file__).resolve().parent.parent

#: Exit-Code des Prüf-Befehls bei abweichendem Stand.
EXIT_ABWEICHEND = 3
#: Rückgabe bei jedem Fehler (Push gescheitert, weiter abweichend, nicht prüfbar).
EXIT_FEHLER = 1
#: Vorgabe-Zeitlimits in Sekunden (Konfig ``stand.timeout_pruefen_s``/``timeout_push_s``).
TIMEOUT_PRUEFEN_S = 180
TIMEOUT_PUSH_S = 900


def _git_bash() -> str:
    """Git-Bash unter Windows: ``GIT_BASH``, Standardpfad, dann neben ``git`` gesucht.

    Ein nacktes ``bash`` trifft unter Windows ``System32\bash.exe`` (WSL ohne /bin/bash).
    """
    kandidaten = [os.environ.get("GIT_BASH") or "", "C:/Program Files/Git/bin/bash.exe"]
    git = shutil.which("git")
    if git:
        kandidaten += [
            str(p / "bin" / "bash.exe") for p in Path(git).resolve().parents[:3]
        ]
    for kandidat in kandidaten:
        if kandidat and Path(kandidat).is_file():
            return kandidat
    raise ValueError(
        "Git-Bash nicht gefunden (GIT_BASH setzen oder Git für Windows installieren)"
    )


def _argv(befehl: str) -> list[str]:
    """Text-Befehl → argv; ``python``/``python3`` → laufender Interpreter, ``bash`` unter
    Windows → Git-Bash."""
    teile = shlex.split(befehl)
    if teile and teile[0] in ("python", "python3"):
        teile[0] = sys.executable
    elif teile and teile[0] == "bash" and sys.platform == "win32":
        teile[0] = _git_bash()
    return teile


def _lauf(befehl: str, repo: Path, timeout_s: float) -> int | None:
    """Befehl im Repo ausführen; ``None``, wenn das Programm nicht startet oder hängt."""
    try:
        return subprocess.run(
            _argv(befehl),
            cwd=str(repo),
            check=False,
            timeout=timeout_s,
            # Push-Kette (nest_push.sh) soll denselben Python nehmen wie der Aufrufer.
            env={**os.environ, "TO_SPAWN_PY": sys.executable},
        ).returncode
    except subprocess.TimeoutExpired:
        log.error(
            "Zeitlimit (%g s) überschritten: %s — nichts gestartet", timeout_s, befehl
        )
        return None
    except (OSError, ValueError) as fehler:
        log.error("Befehl nicht ausführbar: %s (%s) — nichts gestartet", befehl, fehler)
        return None


def syntaxfehler(skill_ordner: Path) -> list[str]:
    """``datei:zeile`` je Python-Datei des Skills, die sich nicht parsen lässt.

    Der Push kopiert den PC-Stand samt halb gespeicherter Dateien anderer Sessions;
    ein Syntaxfehler dort legt auf dem Bau-Server den Wächter lahm (#325).
    """
    kaputt: list[str] = []
    for datei in sorted(skill_ordner.rglob("*.py")):
        teile = set(datei.relative_to(skill_ordner).parts)
        if "__pycache__" in teile or "tests" in teile:
            continue
        try:
            ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        except (SyntaxError, UnicodeDecodeError) as fehler:
            zeile = getattr(fehler, "lineno", None) or "?"
            kaputt.append(f"{datei.relative_to(skill_ordner).as_posix()}:{zeile}")
    return kaputt


def sichere_stand(
    repo: Path, konfig: dict[str, Any], *, nur_pruefen: bool = False
) -> int:
    """Stand prüfen, bei Abweichung pushen und nachprüfen.

    Rückgabe: 0 = gleich/nachgezogen/nicht eingerichtet · 3 = abweichend (nur bei
    ``nur_pruefen``) · 1 = Fehler, nichts starten.
    """
    stand = konfig.get("stand") or {}
    pruefen = str(stand.get("pruefen") or "").strip()
    push = str(stand.get("push") or "").strip()
    if not pruefen:
        log.warning(
            "Stand-Check nicht eingerichtet — Server könnte alten Skill haben "
            "(Konfig `stand.pruefen` leer, übersprungen)."
        )
        return 0
    t_pruefen = float(stand.get("timeout_pruefen_s") or TIMEOUT_PRUEFEN_S)
    t_push = float(stand.get("timeout_push_s") or TIMEOUT_PUSH_S)

    log.info("Stand-Check: %s", pruefen)
    rc = _lauf(pruefen, repo, t_pruefen)
    if rc is None:
        return EXIT_FEHLER
    if rc == 0:
        log.info("Stand-Check: Bau-Server hat den Skill-Stand des PCs.")
        return 0
    if rc != EXIT_ABWEICHEND:
        log.error("Stand nicht prüfbar (Exit %s) — nichts gestartet", rc)
        return EXIT_FEHLER
    if nur_pruefen:
        log.warning(
            "Stand-Check: Bau-Server weicht ab — echter Start würde erst pushen."
        )
        return EXIT_ABWEICHEND
    if not push:
        log.error("Bau-Server weicht ab, aber `stand.push` ist leer — nichts gestartet")
        return EXIT_FEHLER

    skill_ordner = Path(str(stand.get("skill_ordner") or SKILL_ORDNER))
    kaputt = syntaxfehler(skill_ordner)
    if kaputt:
        log.error(
            "Syntaxfehler im Skill (%s) — Push gesperrt, sonst landet halbfertiger Code "
            "auf dem Bau-Server; nichts gestartet. Fertig speichern lassen, dann neu.",
            ", ".join(kaputt[:5]),
        )
        return EXIT_FEHLER
    log.info("Bau-Server weicht ab — Push: %s", push)
    rc = _lauf(push, repo, t_push)
    if rc is None:
        return EXIT_FEHLER
    if rc != 0:
        log.error(
            "Push auf den Bau-Server gescheitert (Exit %s) — nichts gestartet", rc
        )
        return EXIT_FEHLER

    log.info("Push fertig — Stand-Check erneut.")
    rc = _lauf(pruefen, repo, t_pruefen)
    if rc != 0:
        log.error("Server weicht nach Push noch ab (Exit %s) — nichts gestartet", rc)
        return EXIT_FEHLER
    log.info("Stand-Check nach Push: gleich.")
    return 0
