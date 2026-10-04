"""Startklar-Prüfung vor dem Spec-Start (#450, Beschlüsse E32/E33/E37/E39).

Was: Bevor ein Aufseher eine Spec beaufsichtigt, prüft dieses Modul den
Aufseher-Ordner (das Repo bzw. den Worktree, in dem der Aufseher läuft) auf drei
Dinge, die im Bau schon einmal mitten im Lauf gefehlt haben:

1. ``venv``       — ``bau <N>`` ruft ``./.venv/bin/python`` im Aufseher-Ordner auf.
   Ein frischer Worktree hat keine ``.venv`` → Exit 127 (Vorfall Lektion V2).
   Fehlt sie, wird sie angelegt: Symlink auf die ``.venv`` des Hauptrepos (Pakete
   sind dort schon), sonst eine neue venv samt ``pip install -r requirements.txt``.
   Eine echte venv muss alle Pakete aus ``requirements.txt`` haben.
2. ``schlüssel``  — API-Schlüssel, die die Tickets brauchen (FAL_KEY, ELEVENLABS…,
   WERKSTATT_TOKEN fehlten mitten im Bau). Werte werden nie ausgegeben/geloggt.
   Zählen nur Quellen, die ``bau`` lädt (``.env`` im Ordner bzw. im Hauptrepo).
3. ``werkzeug``   — die Aufseher-Werkzeuge laufen wirklich: der Befehlstext GENAU
   wie im Aufseher-Prompt (``BEFEHL_*``, eine Quelle mit ``skripte/wache.py``) geht
   durch ``permissions.deny`` und alle PreToolUse-Hooks (über bash, wie Claude
   Code; Vorfall V7: ``claude_git_sperre.py`` blockte ``aufraeumen.mjs``), danach
   ein Trockenlauf.

Warum ein Modul: das Wissen „was heißt startklar“ (Pfade, Hook-Regeln,
Schlüsselquellen, Aufseher-Befehlstexte) lebt nur hier. Aufrufer kennen nur
``pruefe`` → ``ausgabe`` bzw. ``gate`` (Aufseher-Start, ``skripte/wache.py``),
``BEFEHL_*``/``aufseher_vorlage``/``befehl_text`` (Prompt) und den CLI-Befehl
``to_spawn.py startklar <S>``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn import config
from to_spawn.manifest import MANIFEST_ORDNER

log = logging.getLogger("to_spawn.startklar")

SKILL = Path(__file__).resolve().parent.parent
AUFRAEUMEN = (
    Path.home() / ".claude" / "hooks" / "smart-zone" / "staffel" / "aufraeumen.mjs"
)
HOOK_TIMEOUT_S = 20
WERKZEUG_TIMEOUT_S = 60
GIT_TIMEOUT_S = 20
GH_TIMEOUT_S = 30
INTERPRETER_TIMEOUT_S = 60
VENV_TIMEOUT_S = 300
PIP_TIMEOUT_S = 900
#: Werte kürzer als das werden nicht geschwärzt (sonst trifft „1“ jede Zahl).
GEHEIM_MIN_LAENGE = 6
#: Umgebungsvariable zum Abschalten des Gates (nur Notfall, wird im Log gewarnt).
SCHALTER = "TO_SPAWN_STARTKLAR"

# --- Aufseher-Befehle: eine Quelle für Prompt (skripte/wache.py) und Probe -------
#: Platzhalter wie im Aufseher-Prompt: ``{S}`` Spec, ``{SKILL}`` Skill-Ordner,
#: ``<N>`` Ticket. ``python`` wird unter Linux zu ``python3`` (``aufseher_vorlage``).
BEFEHL_AUFRAEUMEN = "node ~/.claude/hooks/smart-zone/staffel/aufraeumen.mjs --spec {S}"
BEFEHL_NEUSTART = "python {SKILL}/to_spawn.py neustart {S} <N>"
BEFEHL_RESPAWN = "python {SKILL}/to_spawn.py respawn {S} <N>"

#: Läufer: (Shell-Befehl, cwd, stdin, timeout, Zusatz-Umgebung) → Ergebnis.
#: Wirft ``subprocess.TimeoutExpired``/``OSError`` wie ``subprocess.run``.
Laeufer = Callable[
    [str, Path, str | None, float, Mapping[str, str]],
    "subprocess.CompletedProcess[str]",
]
#: Issue-Leser: (Ordner, Ticketnummer) → Issue-Text; wirft bei Fehler.
IssueLeser = Callable[[Path, str], str]


@dataclass(frozen=True)
class Befund:
    """Ein Prüfergebnis. ``bereich`` ist "venv", "konfig", "schlüssel" oder "werkzeug"."""

    bereich: str
    ok: bool
    text: str
    behebung: str = ""


@dataclass(frozen=True)
class Werkzeug:
    """Ein Aufseher-Werkzeug.

    ``befehl`` ist die Vorlage, genau so, wie der Aufseher sie tippt (Platzhalter
    ``{S}``/``{spec}``, ``{SKILL}``/``{skill}``, ``{home}``, ``{py}``, ``<N>``);
    ``trocken`` wird für den Trockenlauf angehängt. ``datei`` muss existieren.
    """

    name: str
    befehl: str
    datei: str
    trocken: str = ""


def _py_name() -> str:
    return "python" if os.name == "nt" else "python3"


#: Die Werkzeuge aus dem Aufseher-Prompt (skripte/wache.py), je mit Trockenlauf.
WERKZEUGE: tuple[Werkzeug, ...] = (
    Werkzeug(
        "aufraeumen",
        BEFEHL_AUFRAEUMEN,
        "{home}/.claude/hooks/smart-zone/staffel/aufraeumen.mjs",
        " --dry-run",
    ),
    Werkzeug("neustart", BEFEHL_NEUSTART, "{SKILL}/to_spawn.py", " --help"),
    Werkzeug("respawn", BEFEHL_RESPAWN, "{SKILL}/to_spawn.py", " --help"),
)


def aufseher_vorlage(text: str) -> str:
    """Prompt-Vorlage fürs Betriebssystem: Linux kennt nur ``python3``."""
    if os.name == "nt":
        return text
    return re.sub(r"\bpython(?= )", "python3", text)


def skill_pfad() -> str:
    """Skill-Ordner, wie er in Aufseher-Befehlen steht (posix, für bash gequotet)."""
    return shlex.quote(SKILL.as_posix())


def befehl_text(werkzeug: Werkzeug, spec: int, ticket: int | str) -> str:
    """Befehl genau so, wie er im Aufseher-Prompt steht (``<N>`` → ``ticket``)."""
    heim = shlex.quote(Path.home().as_posix())
    return (
        aufseher_vorlage(werkzeug.befehl)
        .format(
            S=spec,
            spec=spec,
            SKILL=skill_pfad(),
            skill=skill_pfad(),
            home=heim,
            py=_py_name(),
        )
        .replace("<N>", str(ticket))
    )


def _bash_pfad() -> str | None:
    """bash wie bei Claude Code: Windows → Git-Bash, sonst ``bash`` im PATH."""
    if os.name == "nt":
        for kandidat in (
            Path(os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "")),
            Path("C:/Program Files/Git/bin/bash.exe"),
        ):
            if str(kandidat) not in ("", ".") and kandidat.is_file():
                return str(kandidat)
    return shutil.which("bash")


def _standard_laeufer(
    befehl: str,
    cwd: Path,
    eingabe: str | None,
    timeout: float,
    zusatz: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    bash = _bash_pfad()
    if bash is None:
        raise OSError("bash fehlt (Windows: Git for Windows/Git-Bash installieren)")
    return subprocess.run(
        [bash, "-c", befehl],
        cwd=str(cwd),
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, **zusatz},
        check=False,
    )


def _kurz(text: str, laenge: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= laenge else text[: laenge - 1] + "…"


def _schwaerzen(text: str, geheim: Iterable[str]) -> str:
    for wert in sorted(set(geheim), key=len, reverse=True):
        if len(wert) >= GEHEIM_MIN_LAENGE:
            text = text.replace(wert, "***")
    return text


# --- Git-Hilfen ----------------------------------------------------------------


def _git_lauf(ordner: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """``git <args>`` in ``ordner``; ``None``, wenn git nicht startet/hängt."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(ordner),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git %s in %s fehlgeschlagen: %s", args[0], ordner, fehler)
        return None


def _git_dirs(ordner: Path) -> tuple[Path, Path] | None:
    """(gemeinsames .git, eigenes .git) von ``ordner`` oder ``None``."""
    ergebnis = _git_lauf(
        ordner, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"
    )
    if ergebnis is None or ergebnis.returncode != 0:
        return None
    zeilen = [z for z in ergebnis.stdout.splitlines() if z.strip()]
    if len(zeilen) != 2:
        return None
    return Path(zeilen[0]).resolve(), Path(zeilen[1]).resolve()


def hauptrepo(ordner: Path) -> Path | None:
    """Hauptrepo-Ordner, wenn ``ordner`` ein Git-Worktree ist; sonst ``None``."""
    dirs = _git_dirs(ordner)
    if dirs is None or dirs[0] == dirs[1]:
        return None
    return dirs[0].parent


def _ist_ignoriert(ordner: Path, pfad: str) -> bool | None:
    ergebnis = _git_lauf(ordner, "check-ignore", "-q", "--no-index", pfad)
    if ergebnis is None or ergebnis.returncode not in (0, 1):
        return None
    return ergebnis.returncode == 0


def _venv_ausschliessen(ordner: Path) -> str:
    """Trägt ``/.venv`` in ``<git-common-dir>/info/exclude`` ein (idempotent).

    Ein ``.venv``-Symlink ist eine Datei — ``.venv/`` in ``.gitignore`` greift nicht,
    und anders als echte venvs bringt er keine eigene ``.gitignore`` mit.
    """
    dirs = _git_dirs(ordner)
    if dirs is None:
        return " — Warnung: kein Git-Ordner, .venv nicht ausgeschlossen"
    exclude = dirs[0] / "info" / "exclude"
    try:
        zeilen = (
            exclude.read_text(encoding="utf-8").splitlines()
            if exclude.is_file()
            else []
        )
        if "/.venv" in zeilen:
            return ""
        exclude.parent.mkdir(parents=True, exist_ok=True)
        vorher = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        trenner = "" if not vorher or vorher.endswith("\n") else "\n"
        exclude.write_text(f"{vorher}{trenner}/.venv\n", encoding="utf-8")
    except OSError as fehler:
        log.warning("%s nicht beschreibbar: %s", exclude, fehler)
        return f" — Warnung: .venv nicht in .gitignore und {exclude} nicht beschreibbar (nicht committen!)"
    return f" — .venv stand nicht in .gitignore → /.venv in {exclude} eingetragen"


# --- 1. venv -------------------------------------------------------------------


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _laeuft(python: Path) -> bool:
    if not python.exists():
        return False
    try:
        return (
            subprocess.run(
                [str(python), "-c", "pass"],
                capture_output=True,
                timeout=INTERPRETER_TIMEOUT_S,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("Interpreter %s läuft nicht: %s", python, fehler)
        return False


def _ausfuehren(
    argv: Sequence[str], timeout: float, geheim: Iterable[str] = ()
) -> str | None:
    """Führt ``argv`` aus; gibt bei Fehler den (geschwärzten) Grund zurück, sonst ``None``."""
    try:
        ergebnis = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return _kurz(_schwaerzen(str(fehler), geheim))
    if ergebnis.returncode != 0:
        return _kurz(
            _schwaerzen(
                ergebnis.stderr or ergebnis.stdout or f"Exit {ergebnis.returncode}",
                geheim,
            )
        )
    return None


def _anforderungen(datei: Path) -> list[str]:
    """Paketnamen aus ``requirements.txt`` (ohne Optionen/Includes/Marker-/URL-Zeilen)."""
    try:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as fehler:
        log.warning("%s unlesbar: %s", datei, fehler)
        return []
    namen = []
    for zeile in zeilen:
        zeile = zeile.split(" #", 1)[0].strip()
        if not zeile or zeile.startswith(("#", "-")) or ";" in zeile or "://" in zeile:
            continue
        treffer = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", zeile)
        if treffer:
            namen.append(treffer.group(0))
    return namen


_PAKET_PROBE = (
    "import importlib.metadata as m, sys\n"
    "for n in sys.argv[1:]:\n"
    "    try:\n"
    "        m.distribution(n)\n"
    "    except m.PackageNotFoundError:\n"
    "        print(n)\n"
)


def _fehlende_pakete(
    python: Path, namen: Sequence[str], geheim: Iterable[str] = ()
) -> list[str] | str:
    """Pakete, die im Interpreter fehlen — oder (geschwärzter) Fehlertext, wenn die Probe scheitert."""
    if not namen:
        return []
    try:
        ergebnis = subprocess.run(
            [str(python), "-c", _PAKET_PROBE, *namen],
            capture_output=True,
            text=True,
            timeout=INTERPRETER_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return f"Paket-Probe scheitert: {_kurz(_schwaerzen(str(fehler), geheim))}"
    if ergebnis.returncode != 0:
        return f"Paket-Probe scheitert: {_kurz(_schwaerzen(ergebnis.stderr, geheim))}"
    return ergebnis.stdout.split()


def _neue_venv(venv: Path, mit_pip: bool) -> str | None:
    """Legt eine venv an (``--without-pip``, wenn nichts zu installieren ist)."""
    argv = [sys.executable, "-m", "venv", str(venv)]
    if not mit_pip:
        argv.append("--without-pip")
    return _ausfuehren(argv, VENV_TIMEOUT_S)


def venv_sicherstellen(
    ordner: Path,
    environ: Mapping[str, str] | None = None,
    spec: int | None = None,
) -> Befund:
    """Sorgt für eine lauffähige ``<ordner>/.venv`` (Symlink aufs Hauptrepo oder neu).

    Pakete aus ``requirements.txt`` werden immer geprüft — auch bei einem Symlink
    (das Hauptrepo kann veraltet sein). Fehlertexte von pip/Probe sind geschwärzt.
    """
    ordner = Path(ordner)
    geheim = _geheime_werte(ordner, environ, spec)
    venv = ordner / ".venv"
    py = _py_name()
    anforderungen = ordner / "requirements.txt"
    pip_befehl = (
        f"`{_venv_python(Path('.venv')).as_posix()} -m pip install -r requirements.txt`"
    )
    behebung = f"im Ordner {ordner}: `{py} -m venv .venv` bzw. Symlink .venv → <Hauptrepo>/.venv"
    if anforderungen.is_file():
        behebung += f", dann {pip_befehl}"
    aktion = "vorhanden"
    zusatz = ""
    if venv.is_symlink() and not _laeuft(_venv_python(venv)):
        log.info("Kaputter .venv-Symlink in %s wird ersetzt.", ordner)
        try:
            venv.unlink()
        except OSError as fehler:
            return Befund(
                "venv",
                False,
                f"kaputter .venv-Symlink in {ordner} nicht löschbar: {fehler}",
                f"Symlink .venv von Hand löschen, dann {behebung}",
            )
    if not _laeuft(_venv_python(venv)):
        if venv.exists():
            return Befund(
                "venv",
                False,
                f".venv in {ordner} ohne lauffähigen Interpreter",
                f"Ordner .venv löschen, dann {behebung}",
            )
        haupt = hauptrepo(ordner)
        quelle = haupt / ".venv" if haupt else None
        if quelle is not None and _laeuft(_venv_python(quelle)):
            vorher_ignoriert = _ist_ignoriert(ordner, ".venv")
            try:
                os.symlink(quelle, venv, target_is_directory=True)
                aktion = f"Symlink → {quelle} angelegt"
                if vorher_ignoriert is not True:
                    zusatz = _venv_ausschliessen(ordner)
            except OSError as fehler:
                log.warning(
                    "Symlink %s → %s scheitert (%s), lege neue venv an.",
                    venv,
                    quelle,
                    fehler,
                )
        if not venv.exists():
            grund = _neue_venv(venv, mit_pip=anforderungen.is_file())
            if grund:
                return Befund(
                    "venv", False, f".venv anlegen scheitert: {grund}", behebung
                )
            aktion = "neu angelegt"
            if anforderungen.is_file():
                grund = _ausfuehren(
                    [
                        str(_venv_python(venv)),
                        "-m",
                        "pip",
                        "install",
                        "-q",
                        "-r",
                        str(anforderungen),
                    ],
                    PIP_TIMEOUT_S,
                    geheim,
                )
                if grund:
                    return Befund(
                        "venv",
                        False,
                        f".venv neu angelegt, Pakete fehlen: pip install -r requirements.txt scheitert: {grund}",
                        f"im Ordner {ordner}: {pip_befehl}",
                    )
                aktion += " + requirements.txt installiert"
        if not _laeuft(_venv_python(venv)):
            return Befund(
                "venv", False, f".venv {aktion}, Interpreter läuft aber nicht", behebung
            )
    if anforderungen.is_file():
        fehlend = _fehlende_pakete(
            _venv_python(venv), _anforderungen(anforderungen), geheim
        )
        if isinstance(fehlend, str) or fehlend:
            was = (
                fehlend
                if isinstance(fehlend, str)
                else _schwaerzen(", ".join(fehlend), geheim)
            )
            return Befund(
                "venv",
                False,
                f".venv {aktion}, Pakete aus requirements.txt fehlen: {was}",
                f"im Ordner {ordner}: {pip_befehl}",
            )
    text = f".venv {aktion}, Interpreter läuft{zusatz}"
    if not zusatz and _ist_ignoriert(ordner, ".venv") is False:
        if venv.is_symlink():
            text += _venv_ausschliessen(ordner)
        else:
            text += " — Warnung: .venv steht nicht in .gitignore (nicht committen!)"
    return Befund("venv", True, text)


# --- 2. Schlüssel --------------------------------------------------------------

_ZEILE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ManifestKaputt(Exception):
    """Manifest vorhanden, aber unlesbar oder in falscher Form."""


def _env_datei(pfad: Path) -> dict[str, str]:
    """Einfacher KEY=VALUE-Parser (Kommentare, ``export``, Anführungszeichen)."""
    werte: dict[str, str] = {}
    if not pfad.is_file():
        return werte
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as fehler:
        log.warning("%s unlesbar: %s", pfad, fehler)
        return werte
    for zeile in zeilen:
        if zeile.lstrip().startswith("#"):
            continue
        treffer = _ZEILE.match(zeile)
        if not treffer:
            continue
        wert = treffer.group(2).strip()
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1]
        elif " #" in wert:
            wert = wert.split(" #", 1)[0].strip()
        werte[treffer.group(1)] = wert
    return werte


def _env_quellen(ordner: Path) -> list[dict[str, str]]:
    """Die ``.env``-Dateien, die ``bau`` lädt: Ordner und (bei Worktree) Hauptrepo."""
    quellen = [_env_datei(ordner / ".env")]
    haupt = hauptrepo(ordner)
    if haupt is not None:
        quellen.append(_env_datei(haupt / ".env"))
    return quellen


def _geheime_werte(
    ordner: Path, environ: Mapping[str, str] | None = None, spec: int | None = None
) -> list[str]:
    """Werte zum Schwärzen: alle ``.env``-Werte + Umgebungswerte möglicher Schlüssel.

    Mögliche Schlüssel = Namen aus ``.env``/``.env.example``, Konfig und Manifest
    (Obermenge der benötigten, ohne ``gh``-Abfrage).
    """
    umgebung = os.environ if environ is None else environ
    quellen = _env_quellen(ordner)
    namen: set[str] = {n for q in quellen for n in q}
    namen.update(_env_datei(ordner / ".env.example"))
    namen.update(_konfig_schluessel(ordner))
    if spec is not None:
        try:
            tickets = _manifest_tickets(ordner, spec) or {}
        except ManifestKaputt:
            tickets = {}
        for ticket in tickets.values():
            namen.update(str(n) for n in ticket.get("schluessel", []) or [])
    werte = [w for q in quellen for w in q.values() if w]
    werte += [umgebung[n] for n in namen if umgebung.get(n)]
    return werte


def _konfig_schluessel(ordner: Path) -> list[str]:
    """Schlüssel-Namen aus ``startklar.schluessel`` der Repo-Konfig."""
    abschnitt = config.lade(ordner).get("startklar")
    namen = abschnitt.get("schluessel") if isinstance(abschnitt, dict) else None
    return [str(n) for n in namen] if isinstance(namen, list) else []


def konfig_pfad(ordner: Path) -> Path:
    """Pfad der Repo-Konfig, wie ``config.lade`` ihn liest."""
    return config.repo_wurzel(Path(ordner)) / config.KONFIG_PFAD


def _konfig_typfehler(daten: object) -> str | None:
    """Grund, wenn ``daten`` kein gültiges Konfig-Objekt ist (``startklar.schluessel`` = Liste von Texten)."""
    if not isinstance(daten, dict):
        return "kein JSON-Objekt"
    abschnitt = daten.get("startklar")
    if abschnitt is None:
        return None
    if not isinstance(abschnitt, dict):
        return "Feld startklar ist kein Objekt"
    namen = abschnitt.get("schluessel")
    if namen is not None and not (
        isinstance(namen, list) and all(isinstance(n, str) for n in namen)
    ):
        return "Feld startklar.schluessel ist keine Liste von Texten"
    return None


def konfig_pruefen(ordner: Path) -> list[Befund]:
    """Roter Befund, wenn die Repo-Konfig existiert, aber kein gültiges JSON-Objekt ist.

    ``config.lade`` nimmt dann still die Vorgaben — Startklar macht es sichtbar.
    """
    datei = konfig_pfad(ordner)
    if not datei.is_file():
        return []
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        grund = f"unlesbar ({_kurz(str(fehler), 120)})"
    else:
        grund = _konfig_typfehler(daten)
        if grund is None:
            return []
    return [
        Befund(
            "konfig",
            False,
            f"Konfig {datei} {grund} — es gälten still die Vorgaben",
            f"{datei} reparieren (gültiges JSON-Objekt) oder löschen, dann erneut prüfen",
        )
    ]


def _manifest_pfad(ordner: Path, spec: int) -> Path:
    return ordner / MANIFEST_ORDNER / f"spec-{spec}.json"


def _manifest_tickets(ordner: Path, spec: int) -> dict[str, dict[str, Any]] | None:
    """Tickets aus dem Manifest; ``None`` ohne Manifest, ``ManifestKaputt`` bei Fehler."""
    pfad = _manifest_pfad(ordner, spec)
    if not pfad.is_file():
        return None
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        raise ManifestKaputt(f"Manifest {pfad} unlesbar: {fehler}") from fehler
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    if not isinstance(tickets, dict) or not all(
        isinstance(v, dict) for v in tickets.values()
    ):
        raise ManifestKaputt(
            f"Manifest {pfad}: Feld tickets ist kein Objekt {{Nummer: {{…}}}}"
        )
    for nummer, ticket in tickets.items():
        namen = ticket.get("schluessel")
        if namen is not None and not (
            isinstance(namen, list) and all(isinstance(n, str) for n in namen)
        ):
            raise ManifestKaputt(
                f"Manifest {pfad}: Feld tickets.{nummer}.schluessel ist keine Liste von Texten"
            )
    return {str(k): v for k, v in tickets.items()}


def _beispiel_ticket(ordner: Path, spec: int) -> str:
    """Erstes Ticket aus dem Manifest für ``<N>`` in Befehlen, sonst ``0``."""
    try:
        tickets = _manifest_tickets(ordner, spec)
    except ManifestKaputt as fehler:
        log.warning("%s", fehler)
        return "0"
    return next(iter(tickets), "0") if tickets else "0"


def _gh_issue_text(ordner: Path, nummer: str) -> str:
    """Issue-Text per ``gh`` (Naht für Tests: ``issue_leser``)."""
    ergebnis = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            nummer,
            "--json",
            "title,body",
            "-q",
            '.title + "\\n" + .body',
        ],
        cwd=str(ordner),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GH_TIMEOUT_S,
        check=False,
    )
    if ergebnis.returncode != 0:
        raise RuntimeError(ergebnis.stderr or f"Exit {ergebnis.returncode}")
    return ergebnis.stdout


@dataclass(frozen=True)
class _Bedarf:
    """Ergebnis von ``_benoetigt``: Namen, Warnungen, ggf. Grund für Unvollständigkeit."""

    namen: list[str]
    warnungen: list[str]
    unvollstaendig: str = ""
    unvollstaendig_behebung: str = ""


def _benoetigt(
    ordner: Path,
    tickets: dict[str, dict[str, Any]] | None,
    issue_leser: IssueLeser,
    geheim: Iterable[str] = (),
) -> _Bedarf:
    """Benötigte Schlüssel-Namen aus Konfig, Manifest und Issue-Texten.

    Scheitert das Lesen eines Issues, fragt es die übrigen nicht mehr ab (kein
    N×Timeout) und meldet die Prüfung als unvollständig. Der Fehlertext wird
    mit ``geheim`` geschwärzt, bevor er gekürzt wird.
    """
    namen: set[str] = set(_konfig_schluessel(ordner))
    warnungen: list[str] = []
    if not tickets:
        return _Bedarf(sorted(namen), warnungen)
    bekannt = list(_env_datei(ordner / ".env.example"))
    if not bekannt:
        warnungen.append("Issue-Scan übersprungen, .env.example fehlt")
    unvollstaendig = ""
    behebung = ""
    nicht_geprueft: list[str] = []
    for nummer, ticket in tickets.items():
        namen.update(str(n) for n in ticket.get("schluessel", []) or [])
        if not bekannt:
            continue
        text = " ".join(
            str(ticket.get(feld, "")) for feld in ("title", "umfang", "files", "body")
        )
        if unvollstaendig:
            nicht_geprueft.append(f"#{nummer}")
        else:
            try:
                text += " " + issue_leser(ordner, nummer)
            except FileNotFoundError:
                unvollstaendig = f"Issue #{nummer} nicht lesbar (gh nicht installiert)"
                behebung = "GitHub-CLI gh installieren und `gh auth status` prüfen, dann erneut prüfen"
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as fehler:
                unvollstaendig = (
                    f"Issue #{nummer} nicht lesbar "
                    f"({_kurz(_schwaerzen(str(fehler), geheim), 120)})"
                )
                behebung = (
                    "`gh auth status` prüfen (Anmeldung/Netz), dann erneut prüfen"
                )
        for name in bekannt:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
                namen.add(name)
    if nicht_geprueft:
        unvollstaendig += "; Issue-Text nicht geprüft: " + ", ".join(nicht_geprueft)
    return _Bedarf(sorted(namen), warnungen, unvollstaendig, behebung)


def schluessel_behebung_befehl(ordner: Path) -> str:
    """Befehl, der die Schlüssel aus Bitwarden in ``<ordner>/.env`` schreibt."""
    return f"{_py_name()} {skill_pfad()}/to_spawn.py nest secrets --ziel {shlex.quote((Path(ordner) / '.env').as_posix())}"


def schluessel_pruefen(
    ordner: Path,
    spec: int,
    environ: Mapping[str, str] | None = None,
    issue_leser: IssueLeser | None = None,
    *,
    manifest_pflicht: bool = False,
) -> list[Befund]:
    """Prüft, ob alle benötigten Schlüssel einen Wert haben — nennt nur NAMEN.

    Zählen nur die ``.env``-Quellen, die ``bau`` lädt; steht ein Schlüssel nur in
    ``environ`` (Umgebung des prüfenden Prozesses), ist das ok mit Warnung.
    ``manifest_pflicht`` (Gate): fehlendes Spec-Manifest ist rot statt Hinweis.
    Ist ein Issue nicht lesbar, ist die Prüfung unvollständig → rot.
    """
    ordner = Path(ordner)
    leser = _gh_issue_text if issue_leser is None else issue_leser
    umgebung = os.environ if environ is None else environ
    try:
        tickets = _manifest_tickets(ordner, spec)
    except ManifestKaputt as fehler:
        return [
            Befund(
                "schlüssel",
                False,
                str(fehler),
                "Manifest reparieren (neu erzeugen mit /to-tickets), dann erneut prüfen",
            )
        ]
    geheim = _geheime_werte(ordner, umgebung, spec)
    bedarf = _benoetigt(ordner, tickets, leser, geheim)
    benoetigt, warnungen = bedarf.namen, list(bedarf.warnungen)
    quellen = _env_quellen(ordner)
    nur_umgebung = []
    fehlend = []
    for name in benoetigt:
        if any(q.get(name, "").strip() for q in quellen):
            continue
        if umgebung.get(name, "").strip():
            nur_umgebung.append(name)
        else:
            fehlend.append(name)
    if nur_umgebung:
        warnungen.append(
            "nur in der Umgebung dieses Prozesses, nicht in .env (bau lädt sie evtl. nicht): "
            + ", ".join(nur_umgebung)
        )
    hinweis = (
        "" if tickets is not None else " (kein Manifest, nur Konfig-Schlüssel geprüft)"
    )
    warnung = (" — Warnung: " + "; ".join(warnungen)) if warnungen else ""
    zusatz: list[Befund] = []
    if tickets is None and manifest_pflicht:
        zusatz.append(
            Befund(
                "schlüssel",
                False,
                f"Manifest {_manifest_pfad(ordner, spec)} fehlt — Schlüssel-Bedarf der Tickets unbekannt",
                f"/to-tickets ausführen bzw. Manifest docs/agents/manifests/spec-{spec}.json ziehen (git pull), dann erneut prüfen",
            )
        )
    if bedarf.unvollstaendig:
        zusatz.append(
            Befund(
                "schlüssel",
                False,
                f"Schlüssel-Prüfung unvollständig: {bedarf.unvollstaendig}",
                bedarf.unvollstaendig_behebung,
            )
        )
    if not fehlend:
        liste = ", ".join(benoetigt) if benoetigt else "keine nötig"
        return [
            Befund(
                "schlüssel", True, f"Schlüssel vorhanden: {liste}{hinweis}{warnung}"
            ),
            *zusatz,
        ]
    befehl = schluessel_behebung_befehl(ordner)
    return [
        *(
            Befund(
                "schlüssel",
                False,
                f"Schlüssel fehlt: {name}{hinweis}{warnung}",
                f"Bitwarden-Eintrag {name} anlegen/prüfen, dann `{befehl}`",
            )
            for name in fehlend
        ),
        *zusatz,
    ]


# --- 3. Werkzeug-Probe ---------------------------------------------------------


def standard_settings_dateien(ordner: Path) -> list[Path]:
    """Alle Settings-Dateien, deren PreToolUse-Hooks eine Session im ``ordner`` hätte."""
    if sys.platform == "win32":
        verwaltet = Path("C:/Program Files/ClaudeCode/managed-settings.json")
    elif sys.platform == "darwin":
        verwaltet = Path(
            "/Library/Application Support/ClaudeCode/managed-settings.json"
        )
    else:
        verwaltet = Path("/etc/claude-code/managed-settings.json")
    return [
        verwaltet,
        Path.home() / ".claude" / "settings.json",
        Path(ordner) / ".claude" / "settings.json",
        Path(ordner) / ".claude" / "settings.local.json",
    ]


def _passt(matcher: str, werkzeug: str = "Bash") -> bool:
    if matcher in ("", "*"):
        return True
    try:
        return re.fullmatch(matcher, werkzeug) is not None
    except re.error:
        return matcher == werkzeug


def _timeout(wert: object) -> float:
    try:
        zahl = float(wert) if wert not in (None, "") else HOOK_TIMEOUT_S  # type: ignore[arg-type]
    except (TypeError, ValueError):
        log.warning("Hook-timeout %r keine Zahl — nehme %s s.", wert, HOOK_TIMEOUT_S)
        return float(HOOK_TIMEOUT_S)
    return zahl if zahl > 0 else float(HOOK_TIMEOUT_S)


@dataclass(frozen=True)
class _Regeln:
    """Was eine Bash-Session aus den Settings bekommt."""

    hooks: list[tuple[str, float]]
    deny: list[str]
    fehler: list[str]


def _regeln(settings_dateien: Iterable[Path]) -> _Regeln:
    hooks: list[tuple[str, float]] = []
    deny: list[str] = []
    fehler: list[str] = []
    for datei in settings_dateien:
        datei = Path(datei)
        if not datei.is_file():
            continue
        try:
            daten = json.loads(datei.read_text(encoding="utf-8"))
            if not isinstance(daten, dict):
                raise ValueError("kein JSON-Objekt")
        except (OSError, ValueError) as grund:
            log.warning("Settings %s unlesbar: %s", datei, grund)
            fehler.append(f"Settings {datei} unlesbar: {_kurz(str(grund), 120)}")
            continue
        regeln = (daten.get("permissions", {}) or {}).get("deny", []) or []
        deny.extend(str(r) for r in regeln if isinstance(r, str))
        for eintrag in (daten.get("hooks", {}) or {}).get("PreToolUse", []) or []:
            if not isinstance(eintrag, dict) or not _passt(
                str(eintrag.get("matcher", "") or "")
            ):
                continue
            for hook in eintrag.get("hooks", []) or []:
                if (
                    isinstance(hook, dict)
                    and hook.get("type", "command") == "command"
                    and hook.get("command")
                ):
                    hooks.append((str(hook["command"]), _timeout(hook.get("timeout"))))
    return _Regeln(hooks, deny, fehler)


def _deny_trifft(regel: str, befehl: str) -> bool:
    """``permissions.deny``-Regel gegen einen Bash-Befehl (vereinfacht wie Claude Code).

    ``Bash`` = alles · ``Bash(präfix:*)`` = Präfix · ``Bash(mit*stern)`` = Muster ·
    ``Bash(text)`` = genau dieser Befehl.
    """
    regel = regel.strip()
    if regel == "Bash":
        return True
    treffer = re.fullmatch(r"Bash\((.*)\)", regel, re.DOTALL)
    if not treffer:
        return False
    muster = treffer.group(1).strip()
    if muster.endswith(":*"):
        return befehl.startswith(muster[:-2])
    if "*" in muster:
        return fnmatch.fnmatchcase(befehl, muster)
    return befehl == muster


def _block_grund(ergebnis: subprocess.CompletedProcess[str]) -> str | None:
    """Grund (roh, ungekürzt, ungeschwärzt), wenn der Hook blockt, sonst ``None``.

    Block = Exit 2, ``permissionDecision`` deny/ask (ask wartet unbeaufsichtigt
    ewig), ``decision: block`` oder ``continue: false``.
    """
    if ergebnis.returncode == 2:
        return ergebnis.stderr or ergebnis.stdout or "Exit 2"
    if ergebnis.returncode != 0:
        return None
    try:
        daten = json.loads(ergebnis.stdout or "null")
    except ValueError:
        return None
    if not isinstance(daten, dict):
        return None
    spezifisch = daten.get("hookSpecificOutput") or {}
    if isinstance(spezifisch, dict):
        entscheidung = spezifisch.get("permissionDecision")
        if entscheidung in ("deny", "ask"):
            return f"{entscheidung}: {spezifisch.get('permissionDecisionReason') or ''}"
    if daten.get("decision") == "block":
        return str(daten.get("reason") or "block")
    if daten.get("continue") is False:
        return f"continue: false {daten.get('stopReason') or ''}"
    return None


def _rot(werkzeug: Werkzeug, text: str, behebung: str) -> Befund:
    return Befund("werkzeug", False, f"{werkzeug.name}: {text}", behebung)


def _probe_eines(
    werkzeug: Werkzeug,
    ordner: Path,
    spec: int,
    ticket: str,
    regeln: _Regeln,
    laeufer: Laeufer,
    geheim: Sequence[str],
) -> Befund:
    befehl = befehl_text(werkzeug, spec, ticket)
    datei = Path(
        werkzeug.datei.format(
            S=spec,
            spec=spec,
            SKILL=SKILL.as_posix(),
            skill=SKILL.as_posix(),
            home=Path.home().as_posix(),
            py=_py_name(),
        )
    )
    if not datei.is_file():
        return _rot(
            werkzeug,
            f"Werkzeug fehlt ({datei})",
            "Skill/Hooks neu ausrollen (install.sh)",
        )
    for regel in regeln.deny:
        if _deny_trifft(regel, befehl):
            return _rot(
                werkzeug,
                f"permissions.deny `{regel}` verbietet `{befehl}`",
                "deny-Regel in den Settings lockern, dann erneut prüfen",
            )
    eingabe = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": befehl},
            "cwd": str(ordner),
            "session_id": "startklar",
        }
    )
    warnungen: list[str] = []
    for hook, timeout in regeln.hooks:
        name = f"Hook `{_kurz(hook, 120)}`"
        try:
            ergebnis = laeufer(
                hook, ordner, eingabe, timeout, {"CLAUDE_PROJECT_DIR": str(ordner)}
            )
        except subprocess.TimeoutExpired:
            return _rot(
                werkzeug,
                f"{name} Timeout nach {timeout:.0f} s — Ergebnis unbekannt",
                "Hook von Hand mit dem Befehl prüfen (hängt er?), dann erneut prüfen",
            )
        except OSError as fehler:
            return _rot(
                werkzeug,
                f"{name} startet nicht: {fehler}",
                "bash/Hook-Programm installieren (Windows: Git-Bash), dann erneut prüfen",
            )
        grund = _block_grund(ergebnis)
        if grund is not None:
            return _rot(
                werkzeug,
                f"{name} blockt den Befehl: {_kurz(_schwaerzen(grund, geheim))}",
                "Hook-Regel für dieses Werkzeug freigeben (Hook-Datei/Settings anpassen), dann erneut prüfen",
            )
        if ergebnis.returncode != 0:
            fehlertext = _kurz(_schwaerzen(ergebnis.stderr, geheim), 120)
            warnungen.append(
                f"Hook `{_kurz(hook, 80)}` Exit {ergebnis.returncode} (kein Block): {fehlertext}"
            )
    for warnung in warnungen:
        log.warning("%s: %s", werkzeug.name, warnung)
    trocken = befehl + werkzeug.trocken
    try:
        lauf = laeufer(trocken, ordner, None, WERKZEUG_TIMEOUT_S, {})
    except subprocess.TimeoutExpired:
        return _rot(
            werkzeug,
            f"Trockenlauf Timeout ({WERKZEUG_TIMEOUT_S} s)",
            f"`{trocken}` von Hand prüfen",
        )
    except OSError as fehler:
        return _rot(
            werkzeug,
            f"Trockenlauf startet nicht ({fehler})",
            "bash/node/python installieren (Windows: Git-Bash), dann erneut prüfen",
        )
    if lauf.returncode != 0:
        fehlertext = _kurz(_schwaerzen(lauf.stderr or lauf.stdout, geheim), 160)
        return _rot(
            werkzeug,
            f"Trockenlauf Exit {lauf.returncode}: {fehlertext}",
            f"`{trocken}` von Hand prüfen",
        )
    text = f"{werkzeug.name}: Hooks lassen durch, Trockenlauf ok"
    if warnungen:
        text += " — Warnung: " + "; ".join(warnungen)
    return Befund("werkzeug", True, text)


def werkzeug_probe(
    ordner: Path,
    spec: int,
    settings_dateien: Iterable[Path] | None = None,
    laeufer: Laeufer = _standard_laeufer,
    werkzeuge: Sequence[Werkzeug] = WERKZEUGE,
    environ: Mapping[str, str] | None = None,
) -> list[Befund]:
    """Spielt jedes Aufseher-Werkzeug durch deny-Regeln, PreToolUse-Hooks und trocken."""
    ordner = Path(ordner)
    dateien = (
        standard_settings_dateien(ordner)
        if settings_dateien is None
        else list(settings_dateien)
    )
    regeln = _regeln(dateien)
    befunde = [
        Befund("werkzeug", False, text, "Settings-Datei reparieren (gültiges JSON)")
        for text in regeln.fehler
    ]
    ticket = _beispiel_ticket(ordner, spec)
    geheim = _geheime_werte(ordner, environ, spec)
    return befunde + [
        _probe_eines(w, ordner, spec, ticket, regeln, laeufer, geheim)
        for w in werkzeuge
    ]


# --- Gesamt --------------------------------------------------------------------


def pruefe(
    ordner: Path,
    spec: int,
    *,
    settings_dateien: Iterable[Path] | None = None,
    environ: Mapping[str, str] | None = None,
    laeufer: Laeufer = _standard_laeufer,
    werkzeuge: Sequence[Werkzeug] = WERKZEUGE,
    issue_leser: IssueLeser | None = None,
    manifest_pflicht: bool = False,
) -> list[Befund]:
    """Alle Teile: venv, Konfig, Schlüssel, Werkzeuge.

    ``manifest_pflicht`` setzt das Gate (fehlendes Manifest = rot); die CLI nicht.
    """
    ordner = Path(ordner)
    return [
        venv_sicherstellen(ordner, environ, spec),
        *konfig_pruefen(ordner),
        *schluessel_pruefen(
            ordner, spec, environ, issue_leser, manifest_pflicht=manifest_pflicht
        ),
        *werkzeug_probe(
            ordner, spec, settings_dateien, laeufer, werkzeuge, environ=environ
        ),
    ]


def ausgabe(befunde: Sequence[Befund]) -> tuple[int, str]:
    """Exit-Code (0 alles ok, sonst 1) und eine Zeile je Befund."""
    zeilen = []
    for befund in befunde:
        zeile = f"{'✅' if befund.ok else '❌'} {befund.bereich}: {befund.text}"
        if befund.behebung and not befund.ok:
            zeile += f" → {befund.behebung}"
        zeilen.append(zeile)
    return (0 if all(b.ok for b in befunde) else 1), "\n".join(zeilen)


def gate(
    ordner: Path,
    spec: int,
    *,
    environ: Mapping[str, str] | None = None,
    pruefer: Callable[[Path, int], Sequence[Befund]] | None = None,
) -> int:
    """Aufseher-Start freigeben (0) oder verweigern (2). ``TO_SPAWN_STARTKLAR=aus`` schaltet ab.

    ``ordner`` = der Ordner, in dem der Aufseher läuft (``fahre(cwd=…)``).
    """
    umgebung = os.environ if environ is None else environ
    if umgebung.get(SCHALTER, "").strip().lower() == "aus":
        log.warning(
            "Startklar-Prüfung abgeschaltet (%s=aus) — Start ohne Prüfung.", SCHALTER
        )
        return 0
    if pruefer is None:
        befunde = pruefe(Path(ordner), spec, manifest_pflicht=True)
    else:
        befunde = pruefer(Path(ordner), spec)
    code, text = ausgabe(befunde)
    for zeile in text.splitlines():
        (log.info if code == 0 else log.error)("Startklar: %s", zeile)
    if code:
        log.error(
            "Start verweigert: Aufseher-Ordner %s ist nicht startklar (Notfall: %s=aus).",
            ordner,
            SCHALTER,
        )
        return 2
    return 0
