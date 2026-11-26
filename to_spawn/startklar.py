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

Aufbau (#49 / App #461): ``startklar_basis.py`` (Typen, Befehle, Git-Hilfen),
``startklar_schluessel.py`` (Konfig, Manifest, Schlüssel) und
``startklar_werkzeug.py`` (deny/Hooks/Trockenlauf) halten die Teilbereiche unterhalb
der Projektgrenze; ``startklar.py`` bleibt die einzige öffentliche Schnittstelle für
Aufrufer (``pruefe``, ``ausgabe``, ``gate``, ``BEFEHL_*``, ``aufseher_vorlage``,
``befehl_text`` und ``to_spawn.py startklar <S>``).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from to_spawn import bash_wahl, konsole, prozessbaum, startklar_basis, startklar_schluessel, startklar_werkzeug
from to_spawn.startklar_basis import (
    BEFEHL_AUFRAEUMEN,
    BEFEHL_NEUSTART,
    BEFEHL_RESPAWN,
    GEHEIM_MIN_LAENGE,
    GH_TIMEOUT_S,
    GIT_TIMEOUT_S,
    HOOK_TIMEOUT_S,
    INTERPRETER_TIMEOUT_S,
    PIP_TIMEOUT_S,
    SCHALTER,
    SKILL,
    VENV_TIMEOUT_S,
    WERKZEUG_TIMEOUT_S,
    WERKZEUGE,
    Befund,
    IssueLeser,
    Laeufer,
    Werkzeug,
    _git_lauf,  # noqa: F401 — von test_unterprozess_utf8_258 genutzt
    _ist_ignoriert,
    _kurz,
    _py_name,
    _schwaerzen,
    _standard_laeufer,
    _venv_ausschliessen,
    aufraeumen_pfad,
    aufseher_vorlage,
    hauptrepo,
)
from to_spawn.startklar_schluessel import (
    ManifestKaputt,
    _geheime_werte,
    _gh_issue_text,
    _manifest_pfad,  # noqa: F401 — von test_pfade_rest_l1_p6a genutzt
    konfig_pfad,
    konfig_pruefen,
)
from to_spawn.startklar_werkzeug import (
    _Regeln,
    standard_settings_dateien,
)

log = logging.getLogger("to_spawn.startklar")

__all__ = [
    "BEFEHL_AUFRAEUMEN",
    "BEFEHL_NEUSTART",
    "BEFEHL_RESPAWN",
    "GEHEIM_MIN_LAENGE",
    "GH_TIMEOUT_S",
    "GIT_TIMEOUT_S",
    "HOOK_TIMEOUT_S",
    "INTERPRETER_TIMEOUT_S",
    "PIP_TIMEOUT_S",
    "SCHALTER",
    "SKILL",
    "VENV_TIMEOUT_S",
    "WERKZEUGE",
    "WERKZEUG_TIMEOUT_S",
    "Befund",
    "IssueLeser",
    "Laeufer",
    "ManifestKaputt",
    "Werkzeug",
    "aufraeumen_pfad",
    "aufseher_vorlage",
    "ausgabe",
    "bash_wahl",
    "befehl_text",
    "gate",
    "hauptrepo",
    "konfig_pfad",
    "konfig_pruefen",
    "pruefe",
    "schluessel_behebung_befehl",
    "schluessel_pruefen",
    "skill_pfad",
    "standard_settings_dateien",
    "venv_sicherstellen",
    "werkzeug_probe",
]


def skill_pfad() -> str:
    """Skill-Ordner, wie er in Aufseher-Befehlen steht (posix, für bash gequotet)."""
    return startklar_basis.skill_pfad(SKILL)


def befehl_text(werkzeug: Werkzeug, spec: int, ticket: int | str) -> str:
    """Befehl genau so, wie er im Aufseher-Prompt steht (``<N>`` → ``ticket``)."""
    return startklar_basis.befehl_text(werkzeug, spec, ticket, skill=SKILL)


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
                **prozessbaum.fensterlos(),
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("Interpreter %s läuft nicht: %s", python, fehler)
        return False


def _ausfuehren(argv: Sequence[str], timeout: float, geheim: Iterable[str] = ()) -> str | None:
    """Führt ``argv`` aus; gibt bei Fehler den (geschwärzten) Grund zurück, sonst ``None``."""
    try:
        ergebnis = subprocess.run(
            list(argv),
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            timeout=timeout,
            check=False,
            **prozessbaum.fensterlos(),
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


def _fehlende_pakete(python: Path, namen: Sequence[str], geheim: Iterable[str] = ()) -> list[str] | str:
    """Pakete, die im Interpreter fehlen — oder (geschwärzter) Fehlertext, wenn die Probe scheitert."""
    if not namen:
        return []
    try:
        ergebnis = subprocess.run(
            [str(python), "-c", _PAKET_PROBE, *namen],
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            timeout=INTERPRETER_TIMEOUT_S,
            check=False,
            **prozessbaum.fensterlos(),
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
    pip_befehl = f"`{_venv_python(Path('.venv')).as_posix()} -m pip install -r requirements.txt`"
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
                return Befund("venv", False, f".venv anlegen scheitert: {grund}", behebung)
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
            return Befund("venv", False, f".venv {aktion}, Interpreter läuft aber nicht", behebung)
    if anforderungen.is_file():
        fehlend = _fehlende_pakete(_venv_python(venv), _anforderungen(anforderungen), geheim)
        if isinstance(fehlend, str) or fehlend:
            was = fehlend if isinstance(fehlend, str) else _schwaerzen(", ".join(fehlend), geheim)
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


# --- 2. Schlüssel & 3. Werkzeug-Probe (Fassade zu den Teilmodulen) -------------


def schluessel_behebung_befehl(ordner: Path) -> str:
    """Befehl, der die Schlüssel aus Bitwarden in ``<ordner>/.env`` schreibt."""
    return startklar_schluessel.schluessel_behebung_befehl(ordner, skill_text=skill_pfad())


def schluessel_pruefen(
    ordner: Path,
    spec: int,
    environ: Mapping[str, str] | None = None,
    issue_leser: IssueLeser | None = None,
    *,
    manifest_pflicht: bool = False,
) -> list[Befund]:
    """Prüft, ob alle benötigten Schlüssel einen Wert haben — nennt nur NAMEN."""
    leser = _gh_issue_text if issue_leser is None else issue_leser
    return startklar_schluessel.schluessel_pruefen(
        ordner,
        spec,
        environ=environ,
        issue_leser=leser,
        manifest_pflicht=manifest_pflicht,
        skill_text=skill_pfad(),
    )


def _probe_eines(
    werkzeug: Werkzeug,
    ordner: Path,
    spec: int,
    ticket: str,
    regeln: _Regeln,
    laeufer: Laeufer,
    geheim: Sequence[str],
) -> Befund:
    return startklar_werkzeug._probe_eines(werkzeug, ordner, spec, ticket, regeln, laeufer, geheim, skill=SKILL)


def werkzeug_probe(
    ordner: Path,
    spec: int,
    settings_dateien: Iterable[Path] | None = None,
    laeufer: Laeufer = _standard_laeufer,
    werkzeuge: Sequence[Werkzeug] = WERKZEUGE,
    environ: Mapping[str, str] | None = None,
) -> list[Befund]:
    """Spielt jedes Aufseher-Werkzeug durch deny-Regeln, PreToolUse-Hooks und trocken."""
    return startklar_werkzeug.werkzeug_probe(
        ordner,
        spec,
        settings_dateien=settings_dateien,
        laeufer=laeufer,
        werkzeuge=werkzeuge,
        environ=environ,
        skill=SKILL,
    )


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
        *schluessel_pruefen(ordner, spec, environ, issue_leser, manifest_pflicht=manifest_pflicht),
        *werkzeug_probe(ordner, spec, settings_dateien, laeufer, werkzeuge, environ=environ),
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
        log.warning("Startklar-Prüfung abgeschaltet (%s=aus) — Start ohne Prüfung.", SCHALTER)
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
