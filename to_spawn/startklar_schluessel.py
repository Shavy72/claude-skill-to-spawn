"""Startklar-Prüfung Teil 2: Konfig, Manifest und Schlüssel-Bedarf.

Ausgelagert aus ``to_spawn/startklar.py`` (#49 / App #461). Prüft die Repo-Konfig,
liest ``.env``-Quellen, sammelt Werte zum Schwärzen und prüft den Schlüssel-Bedarf
aus Konfig, Manifest und GitHub-Issues (nennt nur Namen, nie Werte).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn import config, gh
from to_spawn.manifest import manifest_im_repo, manifest_pfad
from to_spawn.startklar_basis import (
    GH_TIMEOUT_S,
    Befund,
    IssueLeser,
    _kurz,
    _py_name,
    _schwaerzen,
    hauptrepo,
    skill_pfad,
)

log = logging.getLogger("to_spawn.startklar")

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


def _geheime_werte(ordner: Path, environ: Mapping[str, str] | None = None, spec: int | None = None) -> list[str]:
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
    if namen is not None and not (isinstance(namen, list) and all(isinstance(n, str) for n in namen)):
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
    return manifest_pfad(ordner, spec)


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
    if not isinstance(tickets, dict) or not all(isinstance(v, dict) for v in tickets.values()):
        raise ManifestKaputt(f"Manifest {pfad}: Feld tickets ist kein Objekt {{Nummer: {{…}}}}")
    for nummer, ticket in tickets.items():
        namen = ticket.get("schluessel")
        if namen is not None and not (isinstance(namen, list) and all(isinstance(n, str) for n in namen)):
            raise ManifestKaputt(f"Manifest {pfad}: Feld tickets.{nummer}.schluessel ist keine Liste von Texten")
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
    """Issue-Text per ``gh`` (Naht für Tests: ``issue_leser``).

    Läuft über die eine ``gh``-Tür (:func:`gh.lauf`) — sie findet unter Windows auch
    ``gh.cmd``/``gh.bat`` über PATHEXT. ``gh`` fehlt → ``FileNotFoundError``; Exit ≠ 0 →
    ``RuntimeError`` mit dem Fehlertext von ``gh``.
    """
    if gh.gh_befehl() is None:
        raise FileNotFoundError("gh")
    code, ausgabe = gh.lauf(
        ["issue", "view", nummer, "--json", "title,body", "-q", '.title + "\\n" + .body'],
        cwd=ordner,
        timeout=GH_TIMEOUT_S,
        fehlertext=True,
    )
    if code != 0:
        raise RuntimeError(ausgabe or f"Exit {code}")
    return ausgabe


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
        text = " ".join(str(ticket.get(feld, "")) for feld in ("title", "umfang", "files", "body"))
        if unvollstaendig:
            nicht_geprueft.append(f"#{nummer}")
        else:
            try:
                text += " " + issue_leser(ordner, nummer)
            except FileNotFoundError:
                unvollstaendig = f"Issue #{nummer} nicht lesbar (gh nicht installiert)"
                behebung = "GitHub-CLI gh installieren und `gh auth status` prüfen, dann erneut prüfen"
            except (OSError, subprocess.TimeoutExpired, RuntimeError) as fehler:
                unvollstaendig = f"Issue #{nummer} nicht lesbar ({_kurz(_schwaerzen(str(fehler), geheim), 120)})"
                behebung = "`gh auth status` prüfen (Anmeldung/Netz), dann erneut prüfen"
        for name in bekannt:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
                namen.add(name)
    if nicht_geprueft:
        unvollstaendig += "; Issue-Text nicht geprüft: " + ", ".join(nicht_geprueft)
    return _Bedarf(sorted(namen), warnungen, unvollstaendig, behebung)


def schluessel_behebung_befehl(ordner: Path, *, skill_text: str | None = None) -> str:
    """Befehl, der die Schlüssel aus Bitwarden in ``<ordner>/.env`` schreibt."""
    skill = skill_pfad() if skill_text is None else skill_text
    return f"{_py_name()} {skill}/to_spawn.py nest secrets --ziel {shlex.quote((Path(ordner) / '.env').as_posix())}"


def schluessel_pruefen(
    ordner: Path,
    spec: int,
    environ: Mapping[str, str] | None = None,
    issue_leser: IssueLeser | None = None,
    *,
    manifest_pflicht: bool = False,
    skill_text: str | None = None,
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
            "nur in der Umgebung dieses Prozesses, nicht in .env (bau lädt sie evtl. nicht): " + ", ".join(nur_umgebung)
        )
    hinweis = "" if tickets is not None else " (kein Manifest, nur Konfig-Schlüssel geprüft)"
    warnung = (" — Warnung: " + "; ".join(warnungen)) if warnungen else ""
    zusatz: list[Befund] = []
    if tickets is None and manifest_pflicht:
        zusatz.append(
            Befund(
                "schlüssel",
                False,
                f"Manifest {_manifest_pfad(ordner, spec)} fehlt — Schlüssel-Bedarf der Tickets unbekannt",
                f"/to-tickets ausführen bzw. Manifest {manifest_im_repo(ordner, spec)} ziehen (git pull), dann erneut prüfen",
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
            Befund("schlüssel", True, f"Schlüssel vorhanden: {liste}{hinweis}{warnung}"),
            *zusatz,
        ]
    befehl = schluessel_behebung_befehl(ordner, skill_text=skill_text)
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
