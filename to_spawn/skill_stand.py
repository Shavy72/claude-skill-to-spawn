"""Skill-Stand an einer Stelle (L1 E2-5): welcher Commit läuft, und ist er der von ``main``?

Versteckt, woher der Stand kommt: im Git-Klon aus ``git rev-parse``, in der installierten
Kopie (dort liegt kein ``.git``) aus dem Stempel ``STAND.json``, den der Installer schreibt
(``{commit, zeit, quelle, klon}``). Der Vergleich fragt ``main`` des Skill-Repos per
``git ls-remote`` (kein ssh zu einem Server, kein Abbruch ohne Netz).

Türen: :func:`stand` (Kurz-SHA oder ``""``) und :func:`vergleich` (Zustand + Text);
der Installer ruft ``python <skill>/to_spawn/skill_stand.py --stempel <ziel> --klon <klon>``.
Das Modul importiert nichts aus dem Paket, damit der Installer es als Skript starten kann.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("to_spawn.skill_stand")

SKILL = Path(__file__).resolve().parents[1]
STEMPEL = "STAND.json"
ZWEIG = "main"
#: Länge des Kurz-SHA in ``stand()`` — gleich für Git-Klon und Stempel.
KURZ = 12
_TIMEOUT = 15.0
AKTUELL, VERALTET, UNBEKANNT = "aktuell", "veraltet", "unbekannt"


def _git(ort: Path, *args: str) -> str | None:
    """Ausgabe von ``git <args>`` in ``ort``; Fehler, Zeitlimit, kein git = ``None``."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=5")
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=ort,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git %s: %s", " ".join(args[:2]), fehler)
        return None
    return fertig.stdout.strip() if fertig.returncode == 0 else None


def _ist_klon(skill: Path) -> bool:
    return (skill / ".git").exists()


def _lies_stempel(skill: Path) -> dict[str, str]:
    try:
        daten = json.loads((skill / STEMPEL).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as fehler:
        log.warning("%s unlesbar: %s", skill / STEMPEL, fehler)
        return {}
    if not isinstance(daten, dict):
        return {}
    return {k: str(v) for k, v in daten.items() if isinstance(v, str)}


def stand(skill: Path = SKILL) -> str:
    """Kurz-SHA des laufenden Skills: Git-Klon vor Stempel; unbekannt = ``""``."""
    if _ist_klon(skill):
        return (_git(skill, "rev-parse", "HEAD") or "")[:KURZ]
    return _lies_stempel(skill).get("commit", "")[:KURZ]


def _befehl(klon: str) -> str:
    if os.name == "nt":
        return f"git -C {klon} pull; pwsh -File {Path(klon) / 'install.ps1'}"
    return f"git -C {klon} pull && bash {Path(klon) / 'install.sh'}"


def vergleich(skill: Path = SKILL) -> tuple[str, str]:
    """Stand der installierten Kopie gegen ``main`` im Skill-Repo: ``(Zustand, Text)``.

    ``VERALTET`` nennt den fertigen Befehl; ``UNBEKANNT`` ist nur ein Hinweis (kein Stempel,
    Git-Klon zum Entwickeln oder ``main`` nicht erreichbar).
    """
    if _ist_klon(skill):
        return (
            UNBEKANNT,
            f"Git-Klon {stand(skill)} — Vergleich nur für die installierte Kopie",
        )
    stempel = _lies_stempel(skill)
    commit, quelle = stempel.get("commit", ""), stempel.get("quelle", "")
    if not commit or not quelle:
        return (
            UNBEKANNT,
            f"kein {STEMPEL} mit Commit und Quelle — einmal neu installieren",
        )
    antwort = _git(skill, "ls-remote", quelle, f"refs/heads/{ZWEIG}")
    if not antwort:
        return (
            UNBEKANNT,
            f"Hinweis: {ZWEIG} von {quelle} nicht erreichbar, Stand {commit[:KURZ]}",
        )
    entfernt = antwort.split()[0]
    if entfernt == commit:
        return AKTUELL, f"{commit[:KURZ]} = {ZWEIG}"
    # ponytail: nur SHA-Gleichheit, eine Installation von einem Zweig vor main gilt als veraltet;
    # Upgrade: Vorfahren-Prüfung, wenn der Stempel einmal die Historie kennt.
    klon = stempel.get("klon") or "<Skill-Klon>"
    return VERALTET, (
        f"veraltet: installiert {commit[:KURZ]}, {ZWEIG} {entfernt[:KURZ]} — Befehl: {_befehl(klon)}"
    )


def stempel(ziel: Path, klon: Path) -> dict[str, str]:
    """Schreibt ``ziel/STAND.json`` aus dem Git-Klon ``klon`` (Commit, Zeit, Quelle, Klon)."""
    daten = {
        "commit": _git(klon, "rev-parse", "HEAD") or "",
        "zeit": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "quelle": _git(klon, "remote", "get-url", "origin") or "",
        "klon": str(klon.resolve()),
    }
    if not daten["commit"]:
        log.warning("%s ist kein Git-Klon — Stand bleibt unbekannt", klon)
    (ziel / STEMPEL).write_text(json.dumps(daten, indent=2) + "\n", encoding="utf-8")
    return daten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stempel", type=Path, required=True, help="installierte Kopie"
    )
    parser.add_argument("--klon", type=Path, required=True, help="Git-Klon des Skills")
    args = parser.parse_args(argv)
    daten = stempel(args.stempel, args.klon)
    print(
        f"Skill-Stand gestempelt: {daten['commit'][:KURZ] or 'unbekannt'} ({args.stempel / STEMPEL})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
