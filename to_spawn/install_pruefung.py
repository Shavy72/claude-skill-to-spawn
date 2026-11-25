"""Installations-Schutz (#415): Suite-Prüfung, Fehler-Log, Ticket-Deduplizierung und atomarer Tausch.

Eine Stelle für ``install.sh`` und ``install.ps1``:

1. ``pruefen``: fährt die Skill-Test-Suite (``pytest -q -p no:cacheprovider -n 4 --dist worksteal``,
   wie ``skripte/suite.sh`` — Parallelität bleibt ungesenkt) gegen den neuen Klon, bevor eine
   Datei im installierten Stand angefasst wird. Bei Rot:
   - Exit ≠ 0 mit klarer Meldung auf stderr; der alte Stand bleibt Byte für Byte gleich.
   - JSONL-Zeile (Zeit, SHA, rote Kennungen) im Fehler-Log im Datenordner des Skills
     (``~/.claude/to-spawn/install_fehler.jsonl`` bzw. ``TO_SPAWN_INSTALL_LOG``).
   - Genau ein offenes Ticket im Skill-Repo (Label ``install:rot``): steht schon eins offen,
     kommt ein Kommentar statt eines zweiten Tickets. Keine Mail, kein Telegram, kein Push.
2. ``tauschen``: kopiert alle Quellen zuerst vollständig in einen versteckten Staging-Ordner
   auf demselben Dateisystem (inkl. Cache-Bereinigung, Ausführungsrechte und ``STAND.json``)
   und tauscht erst danach atomar mit dem Zielordner (Linux: ``renameat2(RENAME_EXCHANGE)``,
   Rückfall: Umbenennen mit Rollback). Ein Abbruch mittendrin hinterlässt nie einen halben Stand.
3. ``verbuchen``: nach erfolgreichem grünem Tausch wird der installierte SHA im selben
   Fehler-Log vermerkt.

Rekursionsschutz: läuft ``install.sh``/``install.ps1`` bereits innerhalb eines aktiven
``pytest``-Laufs (``PYTEST_CURRENT_TEST`` gesetzt — etwa wenn die Suite selbst die
Installer-Tests ausführt), wird keine verschachtelte zweite Suite gestartet.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from to_spawn import gh, skill_stand
from to_spawn.pruef import ausfuehren

log = logging.getLogger("to_spawn.install_pruefung")

#: Umgebungsvariable zum Umbiegen des Fehler-Logs (Tests); Vorgabe: ``~/.claude/to-spawn/install_fehler.jsonl``.
FEHLER_LOG_ENV = "TO_SPAWN_INSTALL_LOG"
#: Dateiname im Datenordner des Skills (``~/.claude/to-spawn/``).
FEHLER_LOG_NAME = "install_fehler.jsonl"
#: Label des einen offenen Tickets für rote Skill-Installationen.
TICKET_LABEL = "install:rot"
#: Worker-Zahl wie in ``skripte/suite.sh`` — Parallelität wird nie gesenkt.
SUITE_WORKER = "4"
#: Caches, die beim atomaren Kopieren nie im Ziel landen.
CACHE_ORDNER = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})

_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")
_FEHLER_ZEILE = re.compile(r"^(FAILED|ERROR)\s+(.+)$")
_SAMMEL_FEHLER = re.compile(r"ERROR collecting\s+(\S+)")
_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


class SuiteErgebnis(NamedTuple):
    """Ergebnis eines Suite-Laufs gegen den zu installierenden Skill-Klon."""

    ok: bool
    exit_code: int
    rote_kennungen: list[str]
    ausgabe: str


def log_pfad() -> Path:
    """Pfad des Fehler-Logs im Datenordner des Skills (``~/.claude/to-spawn/install_fehler.jsonl``)."""
    roh = os.environ.get(FEHLER_LOG_ENV)
    if roh:
        return Path(roh).expanduser()
    return Path.home() / ".claude" / "to-spawn" / FEHLER_LOG_NAME


def jetzt_iso() -> str:
    """Aktuelle Ortszeit als ISO-8601-Zeitstempel mit Zeitzone."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def klon_sha(klon: Path) -> str:
    """Voller Commit-SHA von ``HEAD`` im Klon, oder ``""`` ohne Git."""
    return skill_stand._git(klon, "rev-parse", "HEAD") or ""


def in_laufender_suite() -> bool:
    """True, wenn der Aufruf bereits innerhalb eines aktiven pytest-Laufs erfolgt (Rekursionsschutz)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def suite_befehl(python_exe: str = sys.executable) -> list[str]:
    """Standard-Befehl der Skill-Suite — gleiche Parallelität wie ``skripte/suite.sh``."""
    return [
        python_exe,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-n",
        SUITE_WORKER,
        "--dist",
        "worksteal",
        "-r",
        "fE",
    ]


def rote_kennungen_aus_ausgabe(ausgabe: str, exit_code: int) -> list[str]:
    """Extrahiert sortierte, eindeutige rote Test-Kennungen aus der pytest-Ausgabe."""
    kennungen: set[str] = set()
    for roh in ausgabe.splitlines():
        zeile = roh.strip()
        treffer = _FEHLER_ZEILE.match(zeile)
        if treffer:
            rest = treffer.group(2).split(" - ", 1)[0].strip()
            if rest.startswith("collecting "):
                rest = rest[len("collecting ") :].strip()
            if rest:
                kennungen.add(rest.replace("\\", "/"))
            continue
        sammel = _SAMMEL_FEHLER.search(zeile)
        if sammel:
            kennungen.add(sammel.group(1).strip("_ ").replace("\\", "/"))
    if not kennungen and exit_code != 0:
        if "No module named pytest" in ausgabe:
            return ["pytest-fehlt"]
        return [f"suite-exit-{exit_code}"]
    return sorted(kennungen)


def suite_fahren(klon: Path, python_exe: str = sys.executable) -> SuiteErgebnis:
    """Fährt die Skill-Test-Suite gegen ``klon`` über die Prüf-Tür ``pruef.ausfuehren.lauf``."""
    if not (klon / "conftest.py").is_file():
        meldung = "conftest.py fehlt — Skill-Suite läuft nur im Git-Klon, nicht aus einer Kopie."
        return SuiteErgebnis(False, 64, ["conftest.py-fehlt"], meldung)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    befehl = suite_befehl(python_exe)
    lauf = ausfuehren.lauf(befehl, klon)
    if lauf.code == 0:
        return SuiteErgebnis(True, 0, [], lauf.ausgabe)
    if lauf.code == ausfuehren.NICHT_GESTARTET and lauf.ausgabe.startswith(f"{befehl[0]}: "):
        return SuiteErgebnis(False, lauf.code, ["pytest-start-fehler"], lauf.ausgabe)
    kennungen = rote_kennungen_aus_ausgabe(lauf.ausgabe, lauf.code)
    return SuiteErgebnis(False, lauf.code, kennungen, lauf.ausgabe)


def log_schreiben(eintrag: dict[str, Any], datei: Path | None = None) -> bool:
    """Hängt eine JSONL-Zeile ans Fehler-Log an; False = Datei nicht schreibbar."""
    ziel = datei or log_pfad()
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with ziel.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Fehler-Log nicht schreibbar (%s): %s", ziel, fehler)
        return False
    return True


def rot_signatur(kennungen: Sequence[str]) -> str:
    """Kurze, deterministische Kennung über die Menge roter Tests."""
    text = "\n".join(sorted(set(kennungen)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ticket_text(
    *, sha: str, exit_code: int, kennungen: Sequence[str], zeit: str, signatur: str
) -> str:
    liste = "\n".join(f"- `{k}`" for k in kennungen) or "- (keine Einzelkennung ermittelt)"
    return (
        f"Skill-Installation wegen roter Test-Suite abgebrochen (alter Stand blieb unverändert).\n\n"
        f"- **Zeit:** `{zeit}`\n"
        f"- **SHA:** `{sha or 'unbekannt'}`\n"
        f"- **Exit-Code:** `{exit_code}`\n\n"
        f"### Rote Kennungen\n\n{liste}\n\n"
        f"<!-- install-rot-kennung: {signatur} -->"
    )


def ticket_melden(
    klon: Path,
    *,
    sha: str,
    exit_code: int,
    kennungen: Sequence[str],
    zeit: str,
) -> str | None:
    """Hält genau ein offenes Ticket im Skill-Repo (Kommentar bei bestehendem offenem Ticket).

    Keine Mail, kein Telegram, kein Push — ausschließlich GitHub-Issue im Skill-Repo über ``gh``.
    Gibt ``"#<nr>"`` oder die Issue-URL zurück; ``None`` ohne GitHub-Origin oder bei ``gh``-Fehler.
    """
    slug = gh.github_slug_aus_origin(klon)
    if not slug:
        log.warning("Kein GitHub-Origin in %s — kein Ticket angelegt.", klon)
        return None
    signatur = rot_signatur(kennungen)
    marker = f"<!-- install-rot-kennung: {signatur} -->"
    body = _ticket_text(sha=sha, exit_code=exit_code, kennungen=kennungen, zeit=zeit, signatur=signatur)

    offen = gh.json_lauf(
        [
            "issue",
            "list",
            "--repo",
            slug,
            "--label",
            TICKET_LABEL,
            "--state",
            "open",
            "--json",
            "number,title,body",
        ],
        cwd=klon,
    )
    if not isinstance(offen, list):
        log.warning("Offene Tickets (%s) in %s nicht lesbar — kein Ticket angelegt.", TICKET_LABEL, slug)
        return None

    treffer = sorted(
        (eintrag for eintrag in offen if isinstance(eintrag, dict) and eintrag.get("number")),
        key=lambda eintrag: int(eintrag["number"]),
    )
    if treffer:
        passend = next(
            (e for e in treffer if marker in str(e.get("body") or "")),
            treffer[0],
        )
        nr = int(passend["number"])
        code, _aus = gh.lauf(
            ["issue", "comment", str(nr), "--repo", slug, "--body", body],
            cwd=klon,
        )
        if code != 0:
            log.warning("Kommentar an Ticket #%d (%s) gescheitert (Exit %d).", nr, slug, code)
            return None
        return f"#{nr}"

    gh.label_sicherstellen(
        klon,
        TICKET_LABEL,
        farbe="b60205",
        beschreibung="Skill-Installation wegen roter Test-Suite abgebrochen (#415)",
    )
    kurz = sha[: skill_stand.KURZ] if sha else "unbekannt"
    titel = f"fix(install): Skill-Suite rot bei Installation ({kurz})"
    code, ausgabe = gh.lauf(
        [
            "issue",
            "create",
            "--repo",
            slug,
            "--title",
            titel,
            "--body",
            body,
            "--label",
            TICKET_LABEL,
        ],
        cwd=klon,
    )
    if code != 0:
        log.warning("Ticket-Anlage in %s gescheitert (Exit %d).", slug, code)
        return None
    url_treffer = _ISSUE_URL.search(ausgabe or "")
    return f"#{url_treffer.group(1)}" if url_treffer else (ausgabe.strip() or None)


def pruefen(
    klon: Path,
    *,
    python_exe: str = sys.executable,
    log_datei: Path | None = None,
) -> int:
    """Prüf-Tor vor der Installation: 0 = grün (oder Rekursionsschutz), ≠ 0 = rot (Abbruch)."""
    if in_laufender_suite():
        log.debug("Läuft bereits in aktiver pytest-Suite — keine verschachtelte Suite gestartet.")
        return 0
    ergebnis = suite_fahren(klon, python_exe=python_exe)
    if ergebnis.ok:
        return 0

    zeit = jetzt_iso()
    sha = klon_sha(klon)
    ticket = ticket_melden(
        klon,
        sha=sha,
        exit_code=ergebnis.exit_code,
        kennungen=ergebnis.rote_kennungen,
        zeit=zeit,
    )
    ziel_log = log_datei or log_pfad()
    log_schreiben(
        {
            "zeit": zeit,
            "ergebnis": "rot",
            "sha": sha,
            "exit": ergebnis.exit_code,
            "rote_kennungen": ergebnis.rote_kennungen,
            "ticket": ticket,
        },
        datei=ziel_log,
    )
    kurz = sha[: skill_stand.KURZ] if sha else "unbekannt"
    vorschau = ", ".join(ergebnis.rote_kennungen[:5])
    if len(ergebnis.rote_kennungen) > 5:
        vorschau += f" (+{len(ergebnis.rote_kennungen) - 5} weitere)"
    ticket_hinweis = f", Ticket {ticket}" if ticket else ""
    log.error(
        "Abbruch: Skill-Test-Suite rot (Stand %s, Exit %d, %d rote Kennung(en): %s%s) "
        "— alter Stand bleibt unverändert. Fehler-Log: %s",
        kurz,
        ergebnis.exit_code,
        len(ergebnis.rote_kennungen),
        vorschau,
        ticket_hinweis,
        ziel_log,
    )
    return ergebnis.exit_code if ergebnis.exit_code != 0 else 1


def verbuchen(
    klon: Path,
    *,
    ziel: Path | None = None,
    log_datei: Path | None = None,
) -> int:
    """Vermerkt einen erfolgreich installierten grünen Stand (SHA) im Fehler-Log."""
    sha = klon_sha(klon)
    if not sha and ziel is not None:
        sha = skill_stand._lies_stempel(ziel).get("commit", "")
    eintrag: dict[str, Any] = {
        "zeit": jetzt_iso(),
        "ergebnis": "gruen",
        "sha": sha,
        "rote_kennungen": [],
    }
    if ziel is not None:
        eintrag["ziel"] = str(ziel)
    log_schreiben(eintrag, datei=log_datei)
    return 0


# --- Atomarer Ordner-Tausch -----------------------------------------------------------------


def _tausche_linux_atomar(quelle: Path, ziel: Path) -> bool:
    """Atomarer Verzeichnis-Tausch via Linux ``renameat2(RENAME_EXCHANGE)``; False = Rückfall nötig."""
    if sys.platform != "linux":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fn = getattr(libc, "renameat2", None)
    except OSError:
        return False
    if fn is None:
        return False
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    ctypes.set_errno(0)
    rc = fn(_AT_FDCWD, os.fsencode(quelle), _AT_FDCWD, os.fsencode(ziel), _RENAME_EXCHANGE)
    if rc == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.ENOSYS, errno.EINVAL, errno.EPERM, errno.EOPNOTSUPP):
        return False
    raise OSError(err, os.strerror(err), str(quelle), None, str(ziel))


def _sicherungs_pfad(skills: Path, name: str, zeit: str) -> Path:
    alt_ordner = skills / "_alt"
    sicherung = alt_ordner / f"{name}-{zeit}"
    while sicherung.exists():
        sicherung = sicherung.with_name(sicherung.name + "-1")
    return sicherung


def _kopiere_eintrag(quelle: Path, ziel_ordner: Path) -> None:
    if not quelle.exists() and not quelle.is_symlink():
        return
    if quelle.name in CACHE_ORDNER:
        return
    ziel = ziel_ordner / quelle.name
    if quelle.is_symlink():
        os.symlink(os.readlink(quelle), ziel)
    elif quelle.is_dir():
        shutil.copytree(
            quelle,
            ziel,
            symlinks=True,
            ignore=shutil.ignore_patterns(*CACHE_ORDNER),
        )
    else:
        shutil.copy2(quelle, ziel)


def _ausfuehrbar_setzen(ordner: Path, muster: Sequence[str]) -> None:
    if os.name == "nt":
        return
    for eintrag in muster:
        for pfad in ordner.glob(eintrag):
            if pfad.is_file():
                pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _melde_altlasten(sicherung: Path, ziel: Path) -> None:
    for datei in sorted(p for p in sicherung.rglob("*") if p.is_file()):
        rel = datei.relative_to(sicherung)
        if CACHE_ORDNER.intersection(rel.parts):
            continue
        rel_text = rel.as_posix()
        if rel_text == skill_stand.STEMPEL:
            continue
        if not (ziel / rel).exists():
            log.info("Hinweis: nur im alten Stand (liegt in %s): %s", sicherung, rel_text)


def tausche_atomar(
    skills: Path,
    name: str,
    quellen: Sequence[Path],
    zeit: str,
    *,
    klon: Path | None = None,
    ausfuehrbar: Sequence[str] = (),
) -> Path | None:
    """Kopiert ``quellen`` erst in einen Staging-Ordner in ``skills`` und tauscht dann atomar.

    Bei jedem Fehler vor Abschluss bleibt ein vorhandener Ordner ``skills/name`` unverändert
    und der Staging-Ordner wird aufgeräumt. Rückgabe: Sicherungsordner unter ``_alt/`` (oder
    ``None`` bei Erstinstallation).
    """
    skills.mkdir(parents=True, exist_ok=True)
    ziel = skills / name
    staging = Path(tempfile.mkdtemp(prefix=f".neu-{name}-", dir=str(skills)))
    if os.name != "nt":
        # mkdtemp legt 0700 an; normaler Ordner-Modus wie mkdir.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(staging, 0o777 & ~umask)

    staging_verbraucht = False
    sicherung: Path | None = None
    stempel_daten: dict[str, str] | None = None

    try:
        for quelle in quellen:
            _kopiere_eintrag(quelle, staging)
        _ausfuehrbar_setzen(staging, ausfuehrbar)
        if klon is not None:
            stempel_daten = skill_stand.stempel(staging, klon)

        if not ziel.exists() and not ziel.is_symlink():
            os.rename(staging, ziel)
            staging_verbraucht = True
        else:
            sicherung = _sicherungs_pfad(skills, name, zeit)
            sicherung.parent.mkdir(parents=True, exist_ok=True)
            if _tausche_linux_atomar(staging, ziel):
                # Nach RENAME_EXCHANGE liegt der neue Stand in ``ziel``, der alte in ``staging``.
                staging_verbraucht = True
                os.rename(staging, sicherung)
            else:
                os.rename(ziel, sicherung)
                try:
                    os.rename(staging, ziel)
                    staging_verbraucht = True
                except BaseException:
                    if not ziel.exists() and sicherung.exists():
                        os.rename(sicherung, ziel)
                    raise
    finally:
        if not staging_verbraucht and staging.exists():
            # Einzige Lösch-Stelle ist der Hausmeister (#1368).
            from to_spawn import hausmeister

            try:
                hausmeister.helfer_ordner_loeschen(skills.resolve(), staging.name)
            except (OSError, ValueError) as fehler:
                log.warning("Staging-Ordner %s nicht entfernt: %s", staging, fehler)

    if sicherung is not None:
        log.info("Alter Stand gesichert: %s", sicherung)
        _melde_altlasten(sicherung, ziel)
    if stempel_daten is not None:
        kurz = stempel_daten.get("commit", "")[: skill_stand.KURZ] or "unbekannt"
        log.info("Skill-Stand gestempelt: %s (%s)", kurz, ziel / skill_stand.STEMPEL)
    return sicherung


def _logging_einrichten() -> None:
    """INFO auf stdout (wie bisherige Installer-Ausgaben), WARNING/ERROR auf stderr."""
    wurzel = logging.getLogger()
    wurzel.handlers.clear()
    wurzel.setLevel(logging.INFO)
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.INFO)
    out.addFilter(lambda satz: satz.levelno < logging.WARNING)
    out.setFormatter(logging.Formatter("%(message)s"))
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(logging.Formatter("%(message)s"))
    wurzel.addHandler(out)
    wurzel.addHandler(err)


def main(argv: list[str] | None = None) -> int:
    _logging_einrichten()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="befehl", required=True)

    p_pruefen = sub.add_parser("pruefen", help="Skill-Suite vor dem Austausch ausführen")
    p_pruefen.add_argument("--klon", type=Path, required=True, help="Git-Klon des Skills")
    p_pruefen.add_argument("--python", default=sys.executable, help="Python-Interpreter für pytest")

    p_verbuchen = sub.add_parser("verbuchen", help="Installierten grünen SHA im Fehler-Log vermerken")
    p_verbuchen.add_argument("--klon", type=Path, required=True, help="Git-Klon des Skills")
    p_verbuchen.add_argument("--ziel", type=Path, help="Installierter Zielordner")

    p_tauschen = sub.add_parser("tauschen", help="Ordner atomar über Staging tauschen")
    p_tauschen.add_argument("--skills", type=Path, required=True, help="Ziel-Oberordner (z. B. ~/.claude/skills)")
    p_tauschen.add_argument("--name", required=True, help="Ordnername im Ziel (z. B. to-spawn)")
    p_tauschen.add_argument("--zeit", required=True, help="Zeitstempel für _alt/<name>-<zeit>")
    p_tauschen.add_argument("--klon", type=Path, help="Optional: Git-Klon zum Stempeln von STAND.json vor Tausch")
    p_tauschen.add_argument(
        "--ausfuehrbar",
        action="append",
        default=[],
        help="Relatives Pfad-Muster für chmod +x vor Tausch (mehrfach möglich)",
    )
    p_tauschen.add_argument("quellen", nargs="*", type=Path, help="Zu kopierende Quellen")

    args = parser.parse_args(argv)
    if args.befehl == "pruefen":
        return pruefen(args.klon, python_exe=args.python)
    if args.befehl == "verbuchen":
        return verbuchen(args.klon, ziel=args.ziel)
    tausche_atomar(
        skills=args.skills,
        name=args.name,
        quellen=args.quellen,
        zeit=args.zeit,
        klon=args.klon,
        ausfuehrbar=args.ausfuehrbar,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
