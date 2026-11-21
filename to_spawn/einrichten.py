"""Einrichter (L1 Etappe 1): ein Repo für to-spawn einrichten oder nur prüfen.

Tür nach außen ist allein :func:`einrichten`. Die Soll-Liste ``_BAUSTEINE`` ist
intern: jeder :class:`Baustein` hat Name, Version, ``pruefe`` (liest nur) und
optional ``lege_an`` (wiederholbar ohne Schaden). ``.to-spawn/stand.json`` hält
``{skill_stand, bausteine: {name: version}}``; weicht eine Version ab, läuft nur
der ``lege_an`` dieses Bausteins.

Harte Regeln:

* Getrackte Dateien im App-Repo werden nie geschrieben — auch nicht die
  ``.gitignore``; Ignore-Zeilen gehen nach ``.git/info/exclude`` (lokal).
* ``pruefen=True`` schreibt nichts: Exit 0 = alles da und aktuell, 3 = Abweichung.
* Kaputte oder fehlende ``stand.json`` = neu einrichten, nie Abbruch.
* Eine große Lücke wird gemeldet und hält nicht auf; nur ein Anleger-Fehler
  (Ausnahme) gibt Exit 1.
* Zweiter Lauf ohne Änderung schreibt nichts (mtime von ``stand.json`` gleich).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import subprocess
import sys
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import config, gh, setup, skill_stand, umzug

log = logging.getLogger("to_spawn.einrichten")

EXIT_ABWEICHUNG = 3
STAND_PFAD = Path(".to-spawn") / "stand.json"
_SKILL = Path(__file__).resolve().parent.parent
#: Vorlagen der Weiterleitungen und des Manifest-Musters im Skill.
_VORLAGEN = _SKILL / "repo-scripts"
_MANIFEST_VORLAGE = "_default.json"
_EXCLUDE_KOPF = "# to-spawn Einrichter: lokale Laufdateien (nicht versioniert)"
#: Zeitlimit je ``gh``-Aufruf (Sekunden): ein hängendes ``gh`` hält den Spawn nicht auf.
_GH_TIMEOUT = 15.0
#: Zeitlimit je ``git``-Aufruf (Sekunden); Zeitlimit = Exit 124 (gilt als getrackt).
_GIT_TIMEOUT = 15.0

#: Ergebnis von ``pruefe``: ``DA`` = in Ordnung, ``FEHLT`` = ``lege_an`` kann es
#: richten, ``LUECKE`` = nur von Hand zu richten (große Lücke).
DA, FEHLT, LUECKE = "da_aktuell", "fehlt", "grosse_luecke"
Befund = tuple[str, str]


@dataclass(frozen=True)
class Ort:
    """Was ein Baustein braucht: Repo-Wurzel und Ausgabe (für den Setup-Dialog)."""

    wurzel: Path
    ausgabe: TextIO


@dataclass(frozen=True)
class Baustein:
    name: str
    version: int
    pruefe: Callable[[Ort], Befund]
    lege_an: Callable[[Ort], None] | None = None


# --- Git-Helfer -----------------------------------------------------------


def _git(wurzel: Path, *args: str) -> tuple[int, str]:
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=wurzel,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "git %s: Zeitlimit %s s überschritten", " ".join(args[:2]), _GIT_TIMEOUT
        )
        return 124, ""
    except OSError:
        return 127, ""
    return fertig.returncode, fertig.stdout.strip()


def _getrackt(wurzel: Path, datei: Path) -> bool:
    """Fail-closed: nur ``ls-files``-Exit 1 heißt ungetrackt; Git-Fehler gilt als getrackt.

    Der Pfad wird ohne Auflösen von Symlinks geprüft (ein getrackter Link bleibt
    getrackt); liegt er nicht unter der Wurzel, gilt er als getrackt.
    """
    try:
        rel = Path(os.path.abspath(datei)).relative_to(os.path.abspath(wurzel))
    except ValueError:
        return True
    code = _git(wurzel, "ls-files", "--error-unmatch", "--", rel.as_posix())[0]
    if code not in (0, 1):
        log.warning("git ls-files Exit %s für %s — gilt als getrackt", code, rel)
    return code != 1


class SchreibVerbot(OSError):
    """Der Schreib-Schutz verweigert ein Ziel: große Lücke, kein Anleger-Fehler (Exit bleibt 0)."""


def _schreib_schutz(wurzel: Path, datei: Path, *, grenze: Path | None = None) -> None:
    """Einzige Schreib-Tür des Einrichters: wirft ``OSError``, wenn ``datei`` tabu ist.

    Geschrieben wird nur, wenn (a) der Pfad ohne Symlink-Auflösung ungetrackt ist
    (entfällt bei ``grenze``, z. B. Git-Ordner für ``info/exclude``), (b) weder
    ``datei`` noch ein Eltern-Ordner bis ``grenze`` (Vorgabe: Repo-Wurzel) ein
    Symlink ist und (c) das aufgelöste Ziel unter der aufgelösten ``grenze`` liegt.
    Jeder Zweifel = nicht schreiben (:class:`SchreibVerbot`), der Ablauf meldet die Lücke.
    """
    basis = Path(os.path.abspath(grenze if grenze is not None else wurzel))
    pfad = Path(os.path.abspath(datei))
    if grenze is None and _getrackt(wurzel, pfad):
        raise SchreibVerbot(f"getrackt, nicht geschrieben: {pfad}")
    if basis not in pfad.parents:
        raise SchreibVerbot(f"außerhalb {basis}, nicht geschrieben: {pfad}")
    stufe = pfad
    while stufe != basis:
        if stufe.is_symlink():
            raise SchreibVerbot(f"Symlink {stufe}, nicht geschrieben: {pfad}")
        stufe = stufe.parent
    if basis.resolve() not in pfad.resolve().parents:
        raise SchreibVerbot(f"Ziel außerhalb {basis}, nicht geschrieben: {pfad}")


def _rel(wurzel: Path, datei: Path) -> str | None:
    try:
        return datei.resolve().relative_to(wurzel.resolve()).as_posix()
    except ValueError:
        return None


# --- Bausteine --------------------------------------------------------------


def _konfig_datei(ort: Ort) -> Path:
    return ort.wurzel / config.KONFIG_PFAD


def _pruefe_konfig(ort: Ort) -> Befund:
    try:
        roh = setup.lies_roh(_konfig_datei(ort))
    except setup.KonfigUnlesbar as fehler:
        return LUECKE, f"{fehler} — nicht überschrieben, von Hand reparieren"
    return (DA, str(config.KONFIG_PFAD)) if roh is not None else (FEHLT, "fehlt")


def _lege_konfig_an(ort: Ort) -> None:
    datei = _konfig_datei(ort)
    if datei.exists():
        return
    _schreib_schutz(ort.wurzel, datei)
    if sys.stdin.isatty():
        setup.erster_start(
            ort.wurzel,
            ist_tty=True,
            eingabe=setup.stdin_eingabe(ort.ausgabe),
            ausgabe=ort.ausgabe,
        )
        return
    # Wie config.sicherstellen, aber ohne .gitignore-Schreiben (getrackt im App-Repo).
    inhalt = copy.deepcopy(config.DEFAULTS)
    inhalt["worktree_basis"] = config.worktree_basis_vorgabe(ort.wurzel)
    datei.parent.mkdir(parents=True, exist_ok=True)
    try:
        with datei.open("x", encoding="utf-8") as strom:
            strom.write(json.dumps(inhalt, indent=2, ensure_ascii=False) + "\n")
    except FileExistsError:
        return
    ort.ausgabe.write(setup.ERSTES_MAL + "\n")


def _pruefe_worktree_basis(ort: Ort) -> Befund:
    datei = _konfig_datei(ort)
    try:
        roh = setup.lies_roh(datei)
    except setup.KonfigUnlesbar:
        return LUECKE, "Konfig unlesbar"
    if roh is None:
        return FEHLT, "Konfig fehlt"
    wert = str(roh.get("worktree_basis") or "").strip()
    if wert:
        return DA, wert
    vorschlag = config.worktree_basis_vorgabe(ort.wurzel)
    if _getrackt(ort.wurzel, datei):
        # Leer in getrackter Konfig ist gültige Vorgabe (DuoPlus, altes Verhalten).
        return DA, f"leer (Vorgabe {vorschlag})"
    return FEHLT, f"leer, Vorschlag {vorschlag}"


def _lege_worktree_basis_an(ort: Ort) -> None:
    datei = _konfig_datei(ort)
    roh = setup.lies_roh(datei)
    if (
        roh is None
        or _getrackt(ort.wurzel, datei)
        or str(roh.get("worktree_basis") or "").strip()
    ):
        return
    roh["worktree_basis"] = config.worktree_basis_vorgabe(ort.wurzel)
    _schreib_schutz(ort.wurzel, datei)
    setup._schreibe_atomar(datei, roh)


def _manifest_datei(ort: Ort) -> Path:
    return config.pfad(config.lade(ort.wurzel), "manifeste") / _MANIFEST_VORLAGE


def _pruefe_manifest(ort: Ort) -> Befund:
    datei = _manifest_datei(ort)
    return (DA, str(datei)) if datei.is_file() else (FEHLT, f"fehlt: {datei}")


def _lege_manifest_an(ort: Ort) -> None:
    datei = _manifest_datei(ort)
    if datei.exists():
        return
    _schreib_schutz(ort.wurzel, datei)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_bytes((_VORLAGEN / _MANIFEST_VORLAGE).read_bytes())


def _zu_ignorieren(ort: Ort) -> dict[str, str]:
    """Pfad (relativ) → Exclude-Zeile für alles, was der Einrichter ungetrackt anlegt."""
    ziele = {STAND_PFAD.as_posix(): ".to-spawn/*"}
    manifest = _manifest_datei(ort)
    rel = _rel(ort.wurzel, manifest)
    if rel is not None and not _getrackt(ort.wurzel, manifest):
        ziele[rel] = f"/{rel}"
    return ziele


def _nicht_ignoriert(ort: Ort) -> dict[str, str]:
    if _git(ort.wurzel, "rev-parse", "--git-dir")[0] != 0:
        return {}
    return {
        rel: zeile
        for rel, zeile in _zu_ignorieren(ort).items()
        if _git(ort.wurzel, "check-ignore", "-q", "--", rel)[0] != 0
    }


def _pruefe_gitignore(ort: Ort) -> Befund:
    offen = _nicht_ignoriert(ort)
    if not offen:
        return DA, "Laufdateien ignoriert"
    return FEHLT, "nicht ignoriert: " + ", ".join(sorted(offen))


def _lege_gitignore_an(ort: Ort) -> None:
    offen = _nicht_ignoriert(ort)
    if not offen:
        return
    code, pfad = _git(ort.wurzel, "rev-parse", "--git-path", "info/exclude")
    if code != 0 or not pfad:
        raise OSError("git rev-parse --git-path info/exclude scheiterte")
    datei = Path(pfad) if Path(pfad).is_absolute() else ort.wurzel / pfad
    code, git_ordner = _git(ort.wurzel, "rev-parse", "--git-common-dir")
    if code != 0 or not git_ordner:
        raise OSError("git rev-parse --git-common-dir scheiterte")
    grenze = (
        Path(git_ordner) if Path(git_ordner).is_absolute() else ort.wurzel / git_ordner
    )
    _schreib_schutz(ort.wurzel, datei, grenze=grenze)
    vorhanden = datei.read_text(encoding="utf-8") if datei.is_file() else ""
    zeilen = [
        z for z in dict.fromkeys(offen.values()) if z not in vorhanden.splitlines()
    ]
    if not zeilen:
        return
    trenner = "" if not vorhanden or vorhanden.endswith("\n") else "\n"
    kopf = "" if _EXCLUDE_KOPF in vorhanden else _EXCLUDE_KOPF + "\n"
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8") as strom:
        strom.write(trenner + kopf + "\n".join(zeilen) + "\n")


def _label(ort: Ort) -> str:
    regularien = config.lade(ort.wurzel).get("regularien") or {}
    return str(regularien.get("checkpoint_label") or "checkpoint:human")


def _pruefe_label(ort: Ort) -> Befund:
    label = _label(ort)
    pfad = "repos/{owner}/{repo}/labels/" + urllib.parse.quote(label, safe="")
    code, _ = gh.lauf(["api", pfad], cwd=ort.wurzel, timeout=_GH_TIMEOUT)
    if code == 0:
        return DA, label
    if code == 127:
        return LUECKE, f"gh fehlt — von Hand: gh label create {label}"
    if code == 124:
        return LUECKE, f"gh Zeitlimit — von Hand: gh label create {label}"
    return FEHLT, f"{label} fehlt im GitHub-Repo"


def _lege_label_an(ort: Ort) -> None:
    # Scheitern/Zeitlimit gibt False (Warnung im Log); die Nachprüfung meldet die Lücke.
    gh.label_sicherstellen(ort.wurzel, _label(ort), timeout=_GH_TIMEOUT)


def _maschinen_soll(ort: Ort) -> dict[str, str]:
    return {
        "slug": gh.github_slug_aus_origin(ort.wurzel),
        "worktree_basis": str(config.lade(ort.wurzel).get("worktree_basis") or ""),
    }


def _pruefe_maschine(ort: Ort) -> Befund:
    soll = _maschinen_soll(ort)
    if not soll["slug"]:  # #112: Wegwerf-Klon nie in die maschinenweite Liste
        return LUECKE, (
            "origin ist kein GitHub-Repo — nicht in die Maschinen-Liste eingetragen; "
            "beheben: git remote set-url origin https://github.com/<besitzer>/<name>"
        )
    datei = config.maschine_datei()
    if not datei.is_file():
        return FEHLT, f"fehlt: {datei}"
    try:
        json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        return (
            LUECKE,
            f"Maschinen-Liste unlesbar ({fehler}) — von Hand reparieren: {datei}",
        )
    for eintrag in config.maschine()["repos"]:
        try:
            gleich = (
                Path(eintrag["pfad"]).expanduser().resolve() == ort.wurzel.resolve()
            )
        except (OSError, ValueError):
            gleich = False
        if gleich:
            leer = [k for k, v in soll.items() if v and not eintrag[k]]
            return (FEHLT, "leer: " + ", ".join(leer)) if leer else (DA, str(datei))
    return FEHLT, f"Repo nicht eingetragen: {datei}"


def _lege_maschine_an(ort: Ort) -> None:
    config.maschine_eintragen(ort.wurzel)


def _weiterleitungen(ort: Ort) -> list[tuple[Path, Path]]:
    """(Vorlage, Ziel) für jede im Repo vorhandene Weiterleitung unter ``scripts/``."""
    paare = []
    for vorlage in sorted(_VORLAGEN.iterdir()):
        ziel = ort.wurzel / "scripts" / vorlage.name
        if vorlage.name != _MANIFEST_VORLAGE and vorlage.is_file() and ziel.is_file():
            paare.append((vorlage, ziel))
    return paare


def _veraltet(ort: Ort) -> tuple[list[Path], list[Path]]:
    """(ungetrackt veraltet, getrackt veraltet)."""
    frei: list[Path] = []
    getrackt: list[Path] = []
    for vorlage, ziel in _weiterleitungen(ort):
        if ziel.read_bytes() != vorlage.read_bytes():
            (getrackt if _getrackt(ort.wurzel, ziel) else frei).append(ziel)
    return frei, getrackt


def _pruefe_weiterleitungen(ort: Ort) -> Befund:
    if not _weiterleitungen(ort):
        return DA, "keine vorhanden (nicht Pflicht)"
    frei, getrackt = _veraltet(ort)

    def namen(liste: list[Path]) -> str:
        return ", ".join(p.relative_to(ort.wurzel).as_posix() for p in liste)

    if getrackt:
        return LUECKE, f"veraltet (getrackt, nicht angefasst): {namen(getrackt)}"
    if frei:
        return FEHLT, f"veraltet (ungetrackt): {namen(frei)}"
    return DA, "aktuell"


def _lege_weiterleitungen_an(ort: Ort) -> None:
    frei, _ = _veraltet(ort)
    for ziel in frei:
        _schreib_schutz(ort.wurzel, ziel)
        ziel.write_bytes((_VORLAGEN / ziel.name).read_bytes())


def _pruefe_server_klon(ort: Ort) -> Befund:
    konfig = config.lade(ort.wurzel)
    ordner = umzug.server_repo(konfig, ort.wurzel)
    if (Path(ordner).expanduser() / ".git").exists():
        return DA, f"{ordner} auf diesem Rechner"
    # ponytail: kein SSH-Aufruf je Spawn, Server-Klon nur lokal geprüft; Upgrade: ssh test -d in Etappe 3.
    return DA, f"{ordner} auf {umzug.ssh_ziel(konfig)} nicht geprüft (kein Netz-Aufruf)"


def _pruefe_gh(ort: Ort) -> Befund:
    befehl = gh.gh_befehl()
    return (DA, befehl[-1]) if befehl else (LUECKE, "gh fehlt (Tickets brauchen gh)")


def _pruefe_venv(ort: Ort) -> Befund:
    venv = ort.wurzel / ".venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not venv.exists() and not venv.is_symlink():
        return DA, "keine .venv — legt startklar beim Aufseher-Start an"
    if python.exists():
        return DA, str(python)
    return LUECKE, f".venv kaputt: {python} fehlt"


def _pruefe_skill_aktuell(ort: Ort) -> Befund:
    """Installierte Kopie gegen ``main`` des Skill-Repos; nicht erreichbar = nur Hinweis."""
    zustand, text = skill_stand.vergleich()
    return (LUECKE, text) if zustand == skill_stand.VERALTET else (DA, text)


_BAUSTEINE: list[Baustein] = [
    Baustein("konfig", 1, _pruefe_konfig, _lege_konfig_an),
    Baustein("worktree_basis", 1, _pruefe_worktree_basis, _lege_worktree_basis_an),
    Baustein("manifest", 1, _pruefe_manifest, _lege_manifest_an),
    Baustein("gitignore", 1, _pruefe_gitignore, _lege_gitignore_an),
    Baustein("checkpoint_label", 1, _pruefe_label, _lege_label_an),
    Baustein("maschinen_liste", 1, _pruefe_maschine, _lege_maschine_an),
    Baustein("weiterleitungen", 1, _pruefe_weiterleitungen, _lege_weiterleitungen_an),
    Baustein("server_klon", 1, _pruefe_server_klon),
    Baustein("gh", 1, _pruefe_gh),
    Baustein("venv", 1, _pruefe_venv),
    Baustein("skill_aktuell", 1, _pruefe_skill_aktuell),
]


# --- Ablauf -------------------------------------------------------------------


def _lies_stand(datei: Path) -> dict:
    """Roh gelesene ``stand.json``; kaputt/fehlt = ``{}`` (neu einrichten)."""
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as fehler:
        log.warning("stand.json unlesbar (%s) — richte neu ein: %s", datei, fehler)
        return {}
    return daten if isinstance(daten, dict) else {}


def _sicher(aufruf: Callable[[Ort], Befund], ort: Ort) -> Befund:
    try:
        return aufruf(ort)
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        return LUECKE, f"Prüfung scheiterte: {fehler}"


def einrichten(
    repo: Path, *, pruefen: bool = False, ausgabe: TextIO = sys.stdout
) -> int:
    """Richtet ``repo`` für to-spawn ein (``pruefen=True``: nur prüfen, schreibt nichts).

    Exit: 0 = eingerichtet (große Lücken nur gemeldet) bzw. alles da und aktuell;
    1 = ein Anleger scheiterte; 3 = beim Prüfen eine Abweichung.
    """
    ort = Ort(config.repo_wurzel(repo), ausgabe)
    stand_datei = ort.wurzel / STAND_PFAD
    roh = _lies_stand(stand_datei)
    alt = roh.get("bausteine") if isinstance(roh.get("bausteine"), dict) else {}
    neu: dict[str, int] = {}
    abweichungen: list[str] = []
    luecken: list[str] = []
    code = 0
    ausgabe.write(f"{'Prüfen' if pruefen else 'Einrichten'}: {ort.wurzel}\n")
    for baustein in _BAUSTEINE:
        version_alt = alt.get(baustein.name)
        befund, text = _sicher(baustein.pruefe, ort)
        if befund == DA and (
            version_alt == baustein.version or (version_alt is None and not pruefen)
        ):
            zustand = DA
        elif pruefen:
            zustand = (
                befund
                if befund != DA
                else ("veraltet" if version_alt is not None else "nicht eingerichtet")
            )
            abweichungen.append(baustein.name)
        else:
            if baustein.lege_an is not None:
                try:
                    baustein.lege_an(ort)
                except SchreibVerbot as verbot:
                    log.warning("Anleger %s: %s", baustein.name, verbot)
                    ausgabe.write(f"  {baustein.name}: {LUECKE} — {verbot}\n")
                    luecken.append(baustein.name)
                    continue
                except (OSError, ValueError, subprocess.SubprocessError) as fehler:
                    log.error("Anleger %s scheiterte: %s", baustein.name, fehler)
                    ausgabe.write(
                        f"  {baustein.name}: {LUECKE} — Anleger scheiterte: {fehler}\n"
                    )
                    luecken.append(baustein.name)
                    code = 1
                    continue
                vorher = befund
                befund, text = _sicher(baustein.pruefe, ort)
            else:
                vorher = befund
            if befund != DA:
                zustand = LUECKE
            elif vorher == DA or (
                version_alt is not None and version_alt != baustein.version
            ):
                zustand = "nachgezogen"
            else:
                zustand = "angelegt"
        if zustand == LUECKE:
            luecken.append(baustein.name)
        else:
            neu[baustein.name] = baustein.version
        ausgabe.write(f"  {baustein.name}: {zustand} — {text}\n")
    if pruefen:
        if abweichungen:
            ausgabe.write(f"Abweichung: {', '.join(abweichungen)}\n")
            return EXIT_ABWEICHUNG
        ausgabe.write("alles da und aktuell\n")
        return 0
    stand = {"skill_stand": skill_stand.stand(), "bausteine": neu}
    if stand != roh:
        try:
            _schreib_schutz(ort.wurzel, stand_datei)
            setup._schreibe_atomar(stand_datei, stand)
        except SchreibVerbot as verbot:
            log.warning("stand.json nicht geschrieben: %s", verbot)
        except OSError as fehler:
            log.error("stand.json nicht geschrieben (%s): %s", stand_datei, fehler)
            code = 1
    if luecken:
        ausgabe.write(
            f"Große Lücke (nur gemeldet, hält nicht auf): {', '.join(luecken)}\n"
        )
    return code
