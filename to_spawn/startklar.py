"""Startklar-Prüfung vor dem Spec-Start (#450, Beschlüsse E32/E33/E37/E39).

Was: Bevor ein Aufseher eine Spec beaufsichtigt, prüft dieses Modul den
Aufseher-Ordner (das Repo bzw. den Worktree, in dem der Aufseher läuft) auf drei
Dinge, die im Bau schon einmal mitten im Lauf gefehlt haben:

1. ``venv``       — ``bau <N>`` ruft ``./.venv/bin/python`` im Aufseher-Ordner auf.
   Ein frischer Worktree hat keine ``.venv`` → Exit 127 (Vorfall Lektion V2).
   Fehlt sie, wird sie angelegt: Symlink auf die ``.venv`` des Hauptrepos, sonst
   eine neue venv.
2. ``schlüssel``  — API-Schlüssel, die die Tickets brauchen (FAL_KEY, ELEVENLABS…,
   WERKSTATT_TOKEN fehlten mitten im Bau). Werte werden nie ausgegeben/geloggt.
3. ``werkzeug``   — die Aufseher-Werkzeuge laufen wirklich: erst mit den
   eingetragenen PreToolUse-Hooks durchspielen (Vorfall V7: der eigene Hook
   ``claude_git_sperre.py`` blockte ``aufraeumen.mjs``), dann trocken ausführen.

Warum ein Modul: das Wissen „was heißt startklar“ (Pfade, Hook-Regeln,
Schlüsselquellen) lebt nur hier. Aufrufer kennen nur ``pruefe`` → ``ausgabe``
bzw. ``gate`` (Aufseher-Start, ``skripte/wache.py``) und den CLI-Befehl
``to_spawn.py startklar <S>``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn import config

log = logging.getLogger("to_spawn.startklar")

SKILL = Path(__file__).resolve().parent.parent
AUFRAEUMEN = (
    Path.home() / ".claude" / "hooks" / "smart-zone" / "staffel" / "aufraeumen.mjs"
)
HOOK_TIMEOUT_S = 20
WERKZEUG_TIMEOUT_S = 60
VENV_TIMEOUT_S = 300
#: Umgebungsvariable zum Abschalten des Gates (nur Notfall, wird im Log gewarnt).
SCHALTER = "TO_SPAWN_STARTKLAR"

#: Läufer: (argv oder Shell-Befehl, cwd, stdin, timeout, Zusatz-Umgebung) → Ergebnis.
#: Wirft ``subprocess.TimeoutExpired``/``OSError`` wie ``subprocess.run``.
Laeufer = Callable[
    [Sequence[str] | str, Path, str | None, float, Mapping[str, str]],
    "subprocess.CompletedProcess[str]",
]


@dataclass(frozen=True)
class Befund:
    """Ein Prüfergebnis. ``bereich`` ist "venv", "schlüssel" oder "werkzeug"."""

    bereich: str
    ok: bool
    text: str
    behebung: str = ""


@dataclass(frozen=True)
class Werkzeug:
    """Ein Aufseher-Werkzeug: ``befehl`` genau so, wie der Aufseher ihn tippt.

    Platzhalter: ``{spec}``, ``{skill}``, ``{home}``, ``{py}``. ``datei`` muss existieren.
    """

    name: str
    befehl: str
    datei: str


def _py_name() -> str:
    return "python" if os.name == "nt" else "python3"


#: Die Werkzeuge aus dem Aufseher-Prompt (skripte/wache.py), je mit Trockenlauf.
WERKZEUGE: tuple[Werkzeug, ...] = (
    Werkzeug(
        "aufraeumen",
        "node {home}/.claude/hooks/smart-zone/staffel/aufraeumen.mjs --spec {spec} --dry-run",
        "{home}/.claude/hooks/smart-zone/staffel/aufraeumen.mjs",
    ),
    Werkzeug(
        "neustart", "{py} {skill}/to_spawn.py neustart --help", "{skill}/to_spawn.py"
    ),
)


def _standard_laeufer(
    befehl: Sequence[str] | str,
    cwd: Path,
    eingabe: str | None,
    timeout: float,
    zusatz: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    shell = isinstance(befehl, str)
    argv: Sequence[str] | str = befehl
    if shell and os.name != "nt":
        argv = ["bash", "-c", str(befehl)]
        shell = False
    return subprocess.run(
        argv,
        cwd=str(cwd),
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=shell,
        env={**os.environ, **zusatz},
        check=False,
    )


def _kurz(text: str, laenge: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= laenge else text[: laenge - 1] + "…"


# --- Git-Hilfen ----------------------------------------------------------------


def hauptrepo(ordner: Path) -> Path | None:
    """Hauptrepo-Ordner, wenn ``ordner`` ein Git-Worktree ist; sonst ``None``."""
    try:
        ergebnis = subprocess.run(
            [
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                "--git-dir",
            ],
            cwd=str(ordner),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git rev-parse in %s fehlgeschlagen: %s", ordner, fehler)
        return None
    zeilen = ergebnis.stdout.split()
    if ergebnis.returncode != 0 or len(zeilen) != 2:
        return None
    gemeinsam, eigen = (Path(z).resolve() for z in zeilen)
    if gemeinsam == eigen:
        return None
    return gemeinsam.parent


def _ist_ignoriert(ordner: Path, pfad: str) -> bool | None:
    try:
        ergebnis = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", pfad],
            cwd=str(ordner),
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git check-ignore in %s fehlgeschlagen: %s", ordner, fehler)
        return None
    if ergebnis.returncode in (0, 1):
        return ergebnis.returncode == 0
    return None


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
                timeout=60,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("Interpreter %s läuft nicht: %s", python, fehler)
        return False


def _neue_venv(venv: Path) -> str | None:
    """Legt eine venv an; gibt bei Fehler den Grund zurück."""
    try:
        ergebnis = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            capture_output=True,
            text=True,
            timeout=VENV_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return str(fehler)
    if ergebnis.returncode != 0:
        return _kurz(
            ergebnis.stderr or ergebnis.stdout or f"Exit {ergebnis.returncode}"
        )
    return None


def venv_sicherstellen(ordner: Path) -> Befund:
    """Sorgt für eine lauffähige ``<ordner>/.venv`` (Symlink aufs Hauptrepo oder neu)."""
    ordner = Path(ordner)
    venv = ordner / ".venv"
    behebung = f"im Ordner {ordner}: `{_py_name()} -m venv .venv` bzw. Symlink .venv → <Hauptrepo>/.venv"
    aktion = "vorhanden"
    if venv.is_symlink() and not _laeuft(_venv_python(venv)):
        log.info("Kaputter .venv-Symlink in %s wird ersetzt.", ordner)
        venv.unlink()
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
            try:
                os.symlink(quelle, venv, target_is_directory=True)
                aktion = f"Symlink → {quelle} angelegt"
            except OSError as fehler:
                log.warning(
                    "Symlink %s → %s scheitert (%s), lege neue venv an.",
                    venv,
                    quelle,
                    fehler,
                )
        if not venv.exists():
            grund = _neue_venv(venv)
            if grund:
                return Befund(
                    "venv", False, f".venv anlegen scheitert: {grund}", behebung
                )
            aktion = "neu angelegt"
        if not _laeuft(_venv_python(venv)):
            return Befund(
                "venv", False, f".venv {aktion}, Interpreter läuft aber nicht", behebung
            )
    text = f".venv {aktion}, Interpreter läuft"
    if _ist_ignoriert(ordner, ".venv") is False:
        text += " — Warnung: .venv steht nicht in .gitignore (nicht committen!)"
    return Befund("venv", True, text)


# --- 2. Schlüssel --------------------------------------------------------------

_ZEILE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _env_datei(pfad: Path) -> dict[str, str]:
    """Einfacher KEY=VALUE-Parser (Kommentare, ``export``, Anführungszeichen)."""
    werte: dict[str, str] = {}
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
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


def _manifest_tickets(ordner: Path, spec: int) -> dict[str, dict[str, Any]] | None:
    pfad = ordner / "docs" / "agents" / "manifests" / f"spec-{spec}.json"
    if not pfad.is_file():
        return None
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Manifest %s unlesbar: %s", pfad, fehler)
        return None
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    return (
        {str(k): v for k, v in tickets.items() if isinstance(v, dict)}
        if isinstance(tickets, dict)
        else {}
    )


def _benoetigt(
    ordner: Path, spec: int, tickets: dict[str, dict[str, Any]] | None
) -> list[str]:
    namen: set[str] = set()
    konfig = config.lade(ordner)
    namen.update(
        str(n) for n in (konfig.get("startklar", {}) or {}).get("schluessel", []) or []
    )
    if not tickets:
        return sorted(namen)
    bekannt = list(_env_datei(ordner / ".env.example"))
    for ticket in tickets.values():
        namen.update(str(n) for n in ticket.get("schluessel", []) or [])
        text = " ".join(
            str(ticket.get(feld, "")) for feld in ("title", "umfang", "files")
        )
        for name in bekannt:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
                namen.add(name)
    return sorted(namen)


def schluessel_pruefen(
    ordner: Path, spec: int, environ: Mapping[str, str] | None = None
) -> list[Befund]:
    """Prüft, ob alle benötigten Schlüssel einen Wert haben — nennt nur NAMEN."""
    ordner = Path(ordner)
    umgebung = os.environ if environ is None else environ
    tickets = _manifest_tickets(ordner, spec)
    benoetigt = _benoetigt(ordner, spec, tickets)
    quellen = [_env_datei(ordner / ".env")]
    haupt = hauptrepo(ordner)
    if haupt is not None:
        quellen.append(_env_datei(haupt / ".env"))
    fehlend = [
        name
        for name in benoetigt
        if not (
            umgebung.get(name, "").strip()
            or any(q.get(name, "").strip() for q in quellen)
        )
    ]
    hinweis = (
        "" if tickets is not None else " (kein Manifest, nur Konfig-Schlüssel geprüft)"
    )
    if not fehlend:
        liste = ", ".join(benoetigt) if benoetigt else "keine nötig"
        return [Befund("schlüssel", True, f"Schlüssel vorhanden: {liste}{hinweis}")]
    befehl = f"python3 {SKILL.as_posix()}/to_spawn.py nest secrets --ziel {(ordner / '.env').as_posix()}"
    return [
        Befund(
            "schlüssel",
            False,
            f"Schlüssel fehlt: {name}{hinweis}",
            f"Bitwarden-Eintrag {name} anlegen/prüfen, dann `{befehl}`",
        )
        for name in fehlend
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


def _bash_hooks(settings_dateien: Iterable[Path]) -> list[tuple[str, float]]:
    hooks: list[tuple[str, float]] = []
    for datei in settings_dateien:
        if not Path(datei).is_file():
            continue
        try:
            daten = json.loads(Path(datei).read_text(encoding="utf-8"))
        except (OSError, ValueError) as fehler:
            log.warning("Settings %s unlesbar: %s", datei, fehler)
            continue
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
                    hooks.append(
                        (
                            str(hook["command"]),
                            float(hook.get("timeout") or HOOK_TIMEOUT_S),
                        )
                    )
    return hooks


def _block_grund(ergebnis: subprocess.CompletedProcess[str]) -> str | None:
    """Grund, wenn der Hook blockt (Exit 2 oder deny/block per JSON), sonst ``None``."""
    if ergebnis.returncode == 2:
        return _kurz(ergebnis.stderr or ergebnis.stdout or "Exit 2")
    if ergebnis.returncode != 0:
        return None
    try:
        daten = json.loads(ergebnis.stdout or "null")
    except ValueError:
        return None
    if not isinstance(daten, dict):
        return None
    spezifisch = daten.get("hookSpecificOutput") or {}
    if isinstance(spezifisch, dict) and spezifisch.get("permissionDecision") == "deny":
        return _kurz(str(spezifisch.get("permissionDecisionReason") or "deny"))
    if daten.get("decision") == "block":
        return _kurz(str(daten.get("reason") or "block"))
    return None


def _platzhalter(text: str, spec: int) -> str:
    return text.format(
        spec=spec, skill=SKILL.as_posix(), home=Path.home().as_posix(), py=_py_name()
    )


def _argv(befehl: str) -> Sequence[str] | str:
    return befehl if os.name == "nt" else shlex.split(befehl)


def _probe_eines(
    werkzeug: Werkzeug,
    ordner: Path,
    spec: int,
    hooks: list[tuple[str, float]],
    laeufer: Laeufer,
) -> Befund:
    befehl = _platzhalter(werkzeug.befehl, spec)
    datei = Path(_platzhalter(werkzeug.datei, spec))
    if not datei.is_file():
        return Befund(
            "werkzeug",
            False,
            f"{werkzeug.name}: Werkzeug fehlt ({datei})",
            "Skill/Hooks neu ausrollen (install.sh)",
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
    for hook, timeout in hooks:
        try:
            ergebnis = laeufer(
                hook, ordner, eingabe, timeout, {"CLAUDE_PROJECT_DIR": str(ordner)}
            )
        except subprocess.TimeoutExpired:
            warnungen.append(f"Hook `{_kurz(hook, 80)}` Timeout nach {timeout:.0f} s")
            continue
        except OSError as fehler:
            warnungen.append(f"Hook `{_kurz(hook, 80)}` startet nicht: {fehler}")
            continue
        grund = _block_grund(ergebnis)
        if grund is not None:
            return Befund(
                "werkzeug",
                False,
                f"{werkzeug.name}: Hook `{_kurz(hook, 120)}` blockt den Befehl: {grund}",
                "Hook-Regel für dieses Werkzeug freigeben (Hook-Datei/Settings anpassen), dann erneut prüfen",
            )
        if ergebnis.returncode != 0:
            warnungen.append(
                f"Hook `{_kurz(hook, 80)}` Exit {ergebnis.returncode} (kein Block): {_kurz(ergebnis.stderr, 120)}"
            )
    for warnung in warnungen:
        log.warning("%s: %s", werkzeug.name, warnung)
    try:
        lauf = laeufer(_argv(befehl), ordner, None, WERKZEUG_TIMEOUT_S, {})
    except subprocess.TimeoutExpired:
        return Befund(
            "werkzeug",
            False,
            f"{werkzeug.name}: Trockenlauf Timeout ({WERKZEUG_TIMEOUT_S} s)",
            f"`{befehl}` von Hand prüfen",
        )
    except OSError as fehler:
        return Befund(
            "werkzeug",
            False,
            f"{werkzeug.name}: Werkzeug fehlt ({fehler})",
            "Programm installieren (node/python im PATH?)",
        )
    if lauf.returncode != 0:
        return Befund(
            "werkzeug",
            False,
            f"{werkzeug.name}: Trockenlauf Exit {lauf.returncode}: {_kurz(lauf.stderr or lauf.stdout, 160)}",
            f"`{befehl}` von Hand prüfen",
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
) -> list[Befund]:
    """Spielt jedes Aufseher-Werkzeug durch die PreToolUse-Hooks und trocken durch."""
    ordner = Path(ordner)
    dateien = (
        standard_settings_dateien(ordner)
        if settings_dateien is None
        else list(settings_dateien)
    )
    hooks = _bash_hooks(dateien)
    return [_probe_eines(w, ordner, spec, hooks, laeufer) for w in werkzeuge]


# --- Gesamt --------------------------------------------------------------------


def pruefe(
    ordner: Path,
    spec: int,
    *,
    settings_dateien: Iterable[Path] | None = None,
    environ: Mapping[str, str] | None = None,
    laeufer: Laeufer = _standard_laeufer,
    werkzeuge: Sequence[Werkzeug] = WERKZEUGE,
) -> list[Befund]:
    """Alle drei Teile: venv, Schlüssel, Werkzeuge."""
    ordner = Path(ordner)
    return [
        venv_sicherstellen(ordner),
        *schluessel_pruefen(ordner, spec, environ),
        *werkzeug_probe(ordner, spec, settings_dateien, laeufer, werkzeuge),
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
    """Aufseher-Start freigeben (0) oder verweigern (2). ``TO_SPAWN_STARTKLAR=aus`` schaltet ab."""
    umgebung = os.environ if environ is None else environ
    if umgebung.get(SCHALTER, "").strip().lower() == "aus":
        log.warning(
            "Startklar-Prüfung abgeschaltet (%s=aus) — Start ohne Prüfung.", SCHALTER
        )
        return 0
    befunde = (pruefer or pruefe)(Path(ordner), spec)
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
