"""Skill-Stand an einer Stelle (L1 E2-5): welcher Commit läuft, und ist er der von ``main``?

Versteckt, woher der Stand kommt: im Git-Klon aus ``git rev-parse``, in der installierten
Kopie (dort liegt kein ``.git``) aus dem Stempel ``STAND.json``, den der Installer schreibt
(``{commit, zeit, quelle, klon, dirty}``). Der Vergleich fragt ``main`` des Skill-Repos per
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
            # Eigenständig (Installer startet das Modul als Skript), daher ohne prozessbaum.fensterlos():
            # Ausgabe ist abgefangen, ohne Fenster also immer unschädlich (#658).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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


def _klon_pfad(stempel: dict[str, str]) -> str:
    """Vorhandener Klon-Pfad aus dem Stempel, sonst Platzhalter ``<Skill-Klon>`` (#216)."""
    klon = stempel.get("klon", "")
    if klon and Path(klon).is_dir():
        return klon
    return "<Skill-Klon>"


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
    if not commit:
        return (
            UNBEKANNT,
            f"kein {STEMPEL} mit Commit — einmal aus dem Git-Klon neu installieren",
        )
    if not quelle:
        klon = _klon_pfad(stempel)
        return (
            UNBEKANNT,
            (
                f"Stand {commit[:KURZ]} aus Klon ohne origin ({klon}) — Vergleich mit {ZWEIG} "
                f"erst nach: git -C {klon} remote add origin <url>, dann neu installieren"
            ),
        )
    antwort = _git(skill, "ls-remote", quelle, f"refs/heads/{ZWEIG}")
    if not antwort:
        return (
            UNBEKANNT,
            f"Hinweis: {ZWEIG} von {quelle} nicht erreichbar, Stand {commit[:KURZ]}",
        )
    entfernt = antwort.split()[0]
    if entfernt == commit and stempel.get("dirty") == "ja":
        return UNBEKANNT, (
            f"{commit[:KURZ]} = {ZWEIG}, aber mit ungespeicherten Änderungen im Klon installiert (dirty)"
        )
    if entfernt == commit:
        return AKTUELL, f"{commit[:KURZ]} = {ZWEIG}"
    # ponytail: nur SHA-Gleichheit, eine Installation von einem Zweig vor main gilt als veraltet;
    # Upgrade: Vorfahren-Prüfung, wenn der Stempel einmal die Historie kennt.
    klon = _klon_pfad(stempel)
    return VERALTET, (
        f"veraltet: installiert {commit[:KURZ]}, {ZWEIG} {entfernt[:KURZ]} — Befehl: {_befehl(klon)}"
    )


def _ist_dauerhafter_arbeitsbaum(ordner: Path) -> bool:
    """True, wenn ``ordner`` ein vollständiger, nicht-flacher Arbeitsbaum (kein Bare-Repo) ist."""
    if not (ordner / ".git").is_dir():
        return False
    aus = _git(ordner, "rev-parse", "--is-bare-repository", "--is-shallow-repository")
    if aus is None:
        return False
    zeilen = aus.splitlines()
    return len(zeilen) == 2 and zeilen[0] == "false" and zeilen[1] == "false"


def _haupt_aus_alternates(git_dir: Path) -> Path | None:
    """Dauerhafter Haupt-Klon hinter einem geteilten Klon (``--shared``/``--reference``), sonst ``None``."""
    datei = git_dir / "objects" / "info" / "alternates"
    try:
        rohtext = datei.read_text(encoding="utf-8")
    except OSError:
        return None
    for zeile in rohtext.splitlines():
        eintrag = zeile.strip()
        if not eintrag or eintrag.startswith("#"):
            continue
        if len(eintrag) >= 2 and eintrag[0] == '"' and eintrag[-1] == '"':
            eintrag = eintrag[1:-1]
        ziel = (git_dir / "objects" / Path(eintrag)).resolve()
        if ziel.name == "objects" and ziel.parent.name == ".git":
            haupt = ziel.parent.parent.resolve()
            if _ist_dauerhafter_arbeitsbaum(haupt):
                return haupt
    return None


def _dauerhafter_klon(klon: Path) -> str | None:
    """Dauerhafter Klon-Pfad für ``STAND.json``, oder ``None`` bei Wegwerf-Ordner (#216).

    - Git-Worktree (Arbeitsordner): verweist auf den dauerhaften Haupt-Klon dahinter;
      bei Bare-Repo ohne Arbeitsbaum ``None``.
    - Geteilter Wegwerf-Klon (``objects/info/alternates``): verweist auf den Haupt-Klon
      dahinter, sonst ``None``.
    - Flacher Wegwerf-Klon (``--depth``) oder Ordner ohne ``.git``: ``None``.
    """
    aus = _git(
        klon,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        "--git-dir",
        "--is-shallow-repository",
        "--show-toplevel",
    )
    if aus is None:
        return None
    zeilen = aus.splitlines()
    if len(zeilen) != 4:
        return None
    common = Path(zeilen[0]).resolve()
    git_dir = Path(zeilen[1]).resolve()
    flach = zeilen[2] == "true"
    wurzel = Path(zeilen[3]).resolve()
    if common != git_dir:
        haupt = common.parent.resolve()
        return (
            str(haupt)
            if common.name == ".git" and _ist_dauerhafter_arbeitsbaum(haupt)
            else None
        )
    if (git_dir / "objects" / "info" / "alternates").is_file():
        haupt = _haupt_aus_alternates(git_dir)
        return str(haupt) if haupt is not None else None
    if flach or not _ist_dauerhafter_arbeitsbaum(wurzel):
        return None
    return str(wurzel)


def stempel(ziel: Path, klon: Path) -> dict[str, str]:
    """Schreibt ``ziel/STAND.json`` aus dem Git-Klon ``klon`` (Commit, Zeit, Quelle, ggf. Klon, Dirty).

    ``klon`` zeigt auf den dauerhaften Haupt-Klon (bei Worktree/geteiltem Klon nicht auf den
    Arbeitsordner) und entfällt bei Wegwerf-Ordnern (#216).
    ``dirty`` ist ``"ja"``, wenn der kopierte Arbeitsbaum vom Commit abweicht (``git status
    --porcelain``), ``"nein"`` bei sauberem Klon, ``"unbekannt"`` ohne git.
    """
    status = _git(klon, "status", "--porcelain")
    daten: dict[str, str] = {
        "commit": _git(klon, "rev-parse", "HEAD") or "",
        "zeit": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "quelle": _git(klon, "remote", "get-url", "origin") or "",
        "dirty": "unbekannt" if status is None else ("ja" if status else "nein"),
    }
    dauerhaft = _dauerhafter_klon(klon)
    if dauerhaft:
        daten["klon"] = dauerhaft
    if not daten["commit"]:
        log.warning("%s ist kein Git-Klon — Stand bleibt unbekannt", klon)
    elif not daten["quelle"]:
        log.warning("%s hat kein origin — Vergleich mit %s nicht möglich", klon, ZWEIG)
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
