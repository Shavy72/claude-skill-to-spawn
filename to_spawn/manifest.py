"""Manifest lesen und die Regularien prüfen, bevor Sessions gespawnt werden.

Pflichtfelder je Ticket: ``schaetzung_k`` (Zahl > 0, Tausend Token) und
``umfang`` (Klartext). Die Schätzung muss unter der Smart-Zone-Grenze liegen.
Dazu kommen Belegungs-Prüfungen (Ticket doppelt im Manifest oder in einem
zweiten Manifest) und der GitHub-Teil (Blocker-Kanten, Checkpoint-Ticket,
Assignee). Ist eine Regel verletzt, weigert sich der Skill (Exit 3) — jeder
Fehler-Text nennt seine Abhilfe.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gh

log = logging.getLogger("to_spawn.manifest")

#: Exit-Code der Weigerung (Regularien nicht erfüllt).
EXIT_WEIGERUNG = 3

MANIFEST_ORDNER = Path("docs") / "agents" / "manifests"
_MANIFEST_NAME = re.compile(r"spec-(\d+)\.json")
#: Überschrift „Blocked by“ (#, ##, ### …, Doppelpunkt optional); Rest der Zeile zählt mit.
_BLOCKED_BY_KOPF = re.compile(r"^\s{0,3}#{1,6}\s*blocked by\s*:?\s*(.*)$", re.IGNORECASE)
#: Fett geschrieben: ``**Blocked by:** #12`` bzw. ``**Blocked by**: #12``, auch mitten in der Zeile.
_BLOCKED_BY_FETT = re.compile(r"\*\*\s*blocked by\s*:?\s*\*\*\s*:?\s*(.*)$", re.IGNORECASE)
#: Irgendeine Markdown-Überschrift (``#12`` ohne Leerzeichen ist keine).
_UEBERSCHRIFT = re.compile(r"^\s{0,3}#{1,6}\s")
#: Verweis als ``#12`` oder als volle URL ``…/issues/12``.
_VERWEIS = re.compile(r"(?:#|/issues/)(\d+)\b")


def ticket_schluessel(nummer: Any) -> tuple[int, int | str]:
    """Sortier-Schlüssel für Ticket-Nummern: Zahlen aufsteigend, sonstige Namen danach."""
    text = str(nummer)
    return (0, int(text)) if text.isdigit() else (1, text)


def manifest_pfad(repo: Path, spec: int | str) -> Path:
    return repo / MANIFEST_ORDNER / f"spec-{spec}.json"


def alle_manifeste(repo: Path) -> dict[str, Path]:
    """Alle Manifeste ``spec-<Zahl>.json`` des Repos, Spec-Nummer → Pfad."""
    ordner = repo / MANIFEST_ORDNER
    if not ordner.is_dir():
        return {}
    gefunden: dict[str, Path] = {}
    for datei in sorted(ordner.glob("spec-*.json")):
        treffer = _MANIFEST_NAME.fullmatch(datei.name)
        if treffer:
            gefunden[treffer.group(1)] = datei
    return gefunden


def lies_json(datei: Path) -> tuple[Any, list[str]]:
    """JSON lesen und doppelte Ticket-Schlüssel mitschreiben.

    ``json.loads`` behält bei doppelten Schlüsseln still nur den letzten
    Eintrag; der Haken hier merkt sich jede Ticket-Nummer, die zweimal kommt.
    """
    doppelt: list[str] = []

    def haken(paare: list[tuple[str, Any]]) -> dict[str, Any]:
        objekt: dict[str, Any] = {}
        for schluessel, wert in paare:
            if schluessel in objekt and schluessel.isdigit():
                doppelt.append(schluessel)
            objekt[schluessel] = wert
        return objekt

    daten = json.loads(datei.read_text(encoding="utf-8"), object_pairs_hook=haken)
    return daten, doppelt


def lade_manifest_mit_doppelten(repo: Path, spec: int | str) -> tuple[dict[str, Any], list[str]]:
    """Wie :func:`lade_manifest`, zusätzlich die doppelt belegten Ticket-Nummern."""
    datei = manifest_pfad(repo, spec)
    if not datei.is_file():
        raise FileNotFoundError(str(datei))
    daten, doppelt = lies_json(datei)
    if not isinstance(daten, dict) or not isinstance(daten.get("tickets"), dict):
        raise ValueError(f"Manifest ohne Ticket-Tabelle: {datei}")  # noqa: TRY004 — Aufrufer fangen ValueError
    return daten, doppelt


def lade_manifest(repo: Path, spec: int | str) -> dict[str, Any]:
    """Manifest der Spec lesen; ``FileNotFoundError``/``ValueError`` bei Problemen."""
    return lade_manifest_mit_doppelten(repo, spec)[0]


@dataclass
class Bericht:
    """Ergebnis der Regularien-Prüfung."""

    spec: str
    tickets: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)

    @property
    def sauber(self) -> bool:
        return not self.fehler

    def text(self) -> str:
        zeilen = [f"Regularien Spec #{self.spec} · {len(self.tickets)} Tickets"]
        for eintrag in self.fehler:
            zeilen.append(f"  FEHLER  {eintrag}")
        for eintrag in self.warnungen:
            zeilen.append(f"  Warnung {eintrag}")
        if self.sauber and not self.warnungen:
            zeilen.append("  alles erfüllt")
        elif self.fehler:
            zeilen.append(
                "  → WEIGERUNG — nichts gestartet. Erst /to-tickets: Felder ergänzen "
                "bzw. Tickets neu schneiden, dann erneut."
            )
        return "\n".join(zeilen)


def _zahl(wert: float) -> str:
    return f"{wert:g}"


def pruefe(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    mit_github: bool = True,
    auswahl: Sequence[str] | None = None,
) -> Bericht:
    """Manifest gegen die Regularien prüfen (Felder, Grenze, Belegung, GitHub).

    ``auswahl`` sind die Tickets, die gestartet werden sollen (``--tickets``):
    jede Nummer muss im Manifest stehen. Die Regeln laufen trotzdem über das
    ganze Manifest.
    """
    bericht = Bericht(spec=str(spec))
    try:
        daten, doppelt = lade_manifest_mit_doppelten(repo, spec)
    except FileNotFoundError as fehler:
        bericht.fehler.append(f"Manifest fehlt: {fehler} — erst /to-tickets.")
        return bericht
    except ValueError as fehler:
        bericht.fehler.append(f"{fehler} — Manifest reparieren oder mit /to-tickets neu anlegen.")
        return bericht

    grenze = float(konfig.get("staffel", {}).get("grenze_k", 200))
    tickets: dict[str, Any] = daten["tickets"]
    bericht.tickets = sorted(tickets, key=ticket_schluessel)

    if not bericht.tickets:
        bericht.fehler.append("Manifest führt kein einziges Ticket — erst /to-tickets.")
        return bericht

    for gewaehlt in auswahl or []:
        nummer = str(gewaehlt).strip().lstrip("#").strip()
        if nummer and nummer not in tickets:
            bericht.fehler.append(
                f"#{nummer} steht nicht im Manifest spec-{spec}.json — erst /to-tickets "
                "(Eintrag mit schaetzung_k + umfang)."
            )

    for nummer in sorted(set(doppelt), key=ticket_schluessel):
        bericht.fehler.append(
            f"#{nummer} steht zweimal im Manifest (doppelt belegt) — ein Eintrag geht still "
            f"verloren; in spec-{spec}.json zusammenführen."
        )

    for nummer in bericht.tickets:
        _pruefe_felder(bericht, nummer, tickets[nummer], grenze)

    github: dict[str, dict[str, Any] | None] = {}
    if mit_github:
        github = _hole_github(repo, bericht)
    _pruefe_andere_manifeste(repo, bericht, github)
    if github:
        _pruefe_github(repo, bericht, github, konfig)
    return bericht


def _pruefe_felder(bericht: Bericht, nummer: str, roh: Any, grenze: float) -> None:
    eintrag = roh if isinstance(roh, dict) else {}
    schaetzung = eintrag.get("schaetzung_k")
    umfang = eintrag.get("umfang")
    if not isinstance(schaetzung, (int, float)) or isinstance(schaetzung, bool) or schaetzung <= 0:
        bericht.fehler.append(
            f"#{nummer}: Feld schaetzung_k fehlt oder ist keine Zahl über 0 — "
            "in /to-tickets die Schätzung (Tausend Token) eintragen."
        )
    elif schaetzung >= grenze:
        bericht.fehler.append(
            f"#{nummer}: Schätzung {_zahl(schaetzung)}k liegt an oder über der Grenze "
            f"{_zahl(grenze)}k (verlangt: unter {_zahl(grenze)}k) — Ticket ist zu groß "
            f"geschnitten; in Teil-Tickets je unter {_zahl(grenze)}k schneiden (/to-tickets)."
        )
    if not isinstance(umfang, str) or not umfang.strip():
        bericht.fehler.append(
            f"#{nummer}: Feld umfang (Klartext) fehlt — in /to-tickets in einem Satz eintragen."
        )


def _ist_zu(daten: dict[str, Any] | None) -> bool:
    return isinstance(daten, dict) and str(daten.get("state", "")).upper() != "OPEN"


def _pruefe_andere_manifeste(
    repo: Path, bericht: Bericht, github: dict[str, dict[str, Any] | None]
) -> None:
    """Ticket in einem zweiten Manifest = zwei Spawns auf demselben Ticket.

    Mitgeprüft wird jede ``spec-*.json`` außer der eigenen Datei, also auch
    ``spec-149-ticket-179.json``. Ohne GitHub ist der Zustand unbekannt — das
    zählt wie offen (fail-closed).
    """
    eigene = set(bericht.tickets)
    ordner = repo / MANIFEST_ORDNER
    eigene_datei = manifest_pfad(repo, bericht.spec).name
    dateien = sorted(ordner.glob("spec-*.json")) if ordner.is_dir() else []
    for datei in dateien:
        if datei.name == eigene_datei:
            continue
        try:
            daten, _ = lies_json(datei)
        except (OSError, ValueError) as fehler:
            bericht.warnungen.append(f"{datei.name} ist unlesbar ({fehler}) — nicht mitgeprüft.")
            continue
        fremde = daten.get("tickets") if isinstance(daten, dict) else None
        if not isinstance(fremde, dict):
            continue
        for nummer in sorted(eigene & set(fremde), key=ticket_schluessel):
            if _ist_zu(github.get(nummer)):
                continue
            unbekannt = " (Zustand ohne GitHub unbekannt)" if not github else ""
            bericht.fehler.append(
                f"#{nummer} steht auch in {datei.name} — doppelt belegt: zwei Spawns starten "
                f"zwei Sessions auf demselben Ticket. Aus einem Manifest streichen.{unbekannt}"
            )


def _github_fehler(bericht: Bericht, wo: str) -> str:
    return (
        f"GitHub-Abfrage {wo} fehlgeschlagen — `gh auth status` prüfen; bewusst ohne "
        f"GitHub: `pruefen {bericht.spec} --ohne-github`."
    )


def _hole_github(repo: Path, bericht: Bericht) -> dict[str, dict[str, Any] | None]:
    """Je Ticket Issue-Daten + native Kanten holen; fail-closed bei jedem Ausfall."""
    if gh.gh_befehl() is None:
        bericht.fehler.append(_github_fehler(bericht, "(gh fehlt)"))
        return {}
    slug = gh.repo_aus_origin(repo)
    if not slug:
        bericht.fehler.append(_github_fehler(bericht, "(origin nicht lesbar)"))
        return {}
    ergebnis: dict[str, dict[str, Any] | None] = {}
    for nummer in bericht.tickets:
        daten = gh.ticket_daten(nummer, cwd=repo)
        kanten = gh.blocked_by(slug, nummer, cwd=repo)
        if daten is None or kanten is None:
            bericht.fehler.append(_github_fehler(bericht, f"für #{nummer}"))
            ergebnis[nummer] = None
            continue
        ergebnis[nummer] = {**daten, "_kanten": kanten, "_slug": slug}
    return ergebnis


def _blocker_verweise(body: str) -> list[str]:
    """Nummern aus allen „Blocked by“-Abschnitten des Ticket-Texts.

    Regel: Überschrift (``#`` bis ``######``, Groß/Klein egal, Doppelpunkt
    optional) → Rest der Kopfzeile plus alle Zeilen bis zur nächsten
    Überschrift. Fett (``**Blocked by:**``) → Rest der Zeile; ist er leer, die
    folgenden Zeilen bis zur nächsten Leerzeile oder Überschrift. Wörter wie
    „None“, „Keine“ oder „-“ enthalten keine Nummer und zählen damit nicht.
    """
    zeilen = (body or "").splitlines()
    abschnitte: list[str] = []
    i = 0
    while i < len(zeilen):
        kopf = _BLOCKED_BY_KOPF.match(zeilen[i])
        fett = None if kopf else _BLOCKED_BY_FETT.search(zeilen[i])
        i += 1
        if kopf:
            abschnitte.append(kopf.group(1))
            while i < len(zeilen) and not _UEBERSCHRIFT.match(zeilen[i]):
                abschnitte.append(zeilen[i])
                i += 1
        elif fett:
            abschnitte.append(fett.group(1))
            if not fett.group(1).strip():
                while i < len(zeilen) and zeilen[i].strip() and not _UEBERSCHRIFT.match(zeilen[i]):
                    abschnitte.append(zeilen[i])
                    i += 1
    return list(dict.fromkeys(_VERWEIS.findall("\n".join(abschnitte))))


def _pruefe_github(
    repo: Path,
    bericht: Bericht,
    github: dict[str, dict[str, Any] | None],
    konfig: dict[str, Any],
) -> None:
    eigene = set(bericht.tickets)
    label = str(konfig.get("regularien", {}).get("checkpoint_label", "checkpoint:human"))
    mit_kante = 0
    labels_gesehen = False
    checkpoints: list[str] = []
    # Zustand je Nummer (True = offen, None = Abfrage gescheitert) — je Prüflauf nur einmal.
    zustaende: dict[str, bool | None] = {
        n: not _ist_zu(d) for n, d in github.items() if isinstance(d, dict)
    }

    def offen(verweis: str) -> bool | None:
        if verweis not in zustaende:
            zustaende[verweis] = gh.ticket_offen(verweis, cwd=repo)
        return zustaende[verweis]

    for nummer in bericht.tickets:
        daten = github.get(nummer)
        if daten is None:
            continue
        kanten = daten.get("_kanten") or []
        if kanten:
            mit_kante += 1
        if "labels" in daten:
            labels_gesehen = True
            namen = {
                str(eintrag.get("name")) for eintrag in daten["labels"] if isinstance(eintrag, dict)
            }
            if label in namen:
                checkpoints.append(nummer)
        if _ist_zu(daten):
            continue
        _pruefe_verweise(bericht, nummer, daten, kanten, eigene, offen)
        belegt = [
            str(eintrag.get("login"))
            for eintrag in daten.get("assignees") or []
            if isinstance(eintrag, dict) and eintrag.get("login")
        ]
        if belegt:
            bericht.warnungen.append(
                f"#{nummer} ist schon belegt (Assignee {', '.join(belegt)}) — läuft auf einem "
                f"anderen Rechner noch eine Session? Dort `sessions {bericht.spec}` prüfen; "
                "sonst ist das eine Wiederaufnahme."
            )

    if len(bericht.tickets) > 1 and mit_kante == 0:
        bericht.warnungen.append(
            "Kein Ticket hat eine native blocked_by-Kante — alle Sessions starten "
            "gleichzeitig. Reihenfolge gewollt? Sonst /to-tickets nachziehen."
        )
    if not labels_gesehen:
        bericht.warnungen.append("Checkpoint ungeprüft (GitHub-Antwort ohne Labels).")
    elif not checkpoints:
        bericht.fehler.append(
            f"Kein Checkpoint-Ticket: keins der Tickets trägt das Label {label} — /to-tickets: "
            "Abnahme-Ticket für Davids Test anlegen, von allen anderen blockiert."
        )
    else:
        _pruefe_checkpoint(bericht, github, checkpoints)


def _pruefe_checkpoint(
    bericht: Bericht, github: dict[str, dict[str, Any] | None], checkpoints: list[str]
) -> None:
    """Solange andere Tickets offen sind: Checkpoint offen und per Kante hinten dran."""
    offene_andere = [
        n
        for n in bericht.tickets
        if isinstance(github.get(n), dict) and not _ist_zu(github[n]) and n not in checkpoints
    ]
    if not offene_andere:
        return
    offene_cp = [n for n in checkpoints if not _ist_zu(github[n])]
    if not offene_cp:
        zu = ", ".join(f"#{n}" for n in checkpoints)
        andere = ", ".join(f"#{n}" for n in offene_andere)
        wort = "Checkpoint-Ticket" if len(checkpoints) == 1 else "Checkpoint-Tickets"
        verb = "ist" if len(checkpoints) == 1 else "sind"
        offen_verb = "ist" if len(offene_andere) == 1 else "sind"
        bericht.fehler.append(
            f"{wort} {zu} {verb} schon zu, aber {andere} {offen_verb} offen — neues "
            "Abnahme-Ticket anlegen."
        )
        return
    for nummer in offene_cp:
        daten = github[nummer] or {}
        if not daten.get("_kanten"):
            bericht.fehler.append(
                f"Checkpoint-Ticket #{nummer} hat keine native Kante — es startet sofort statt "
                "nach den anderen; Kanten auf die übrigen Tickets setzen."
            )


def _pruefe_verweise(
    bericht: Bericht,
    nummer: str,
    daten: dict[str, Any],
    kanten: list[Any],
    eigene: set[str],
    offen: Callable[[str], bool | None],
) -> None:
    """Blocker-Verweise im Ticket-Text gegen die nativen Kanten halten.

    Ohne native Kante ist ein Verweis ein Fehler, wenn er ein Ticket der Spec
    ist oder offen ist (Zustand unbekannt = Fehler, fail-closed). Nur ein
    geschlossenes Fremd-Ticket ist eine Warnung (vermutlich Entwurfs-Nummer).
    """
    native = {str(k.get("number")) for k in kanten if isinstance(k, dict)}
    slug = daten.get("_slug", "<owner/repo>")
    for verweis in _blocker_verweise(str(daten.get("body") or "")):
        if verweis == nummer or verweis in native:
            continue
        befehl = (
            f"gh api -X POST repos/{slug}/issues/{nummer}/dependencies/blocked_by -F "
            f"issue_id=$(gh api repos/{slug}/issues/{verweis} --jq .id)"
        )
        if verweis in eigene:
            bericht.fehler.append(
                f"#{nummer}: laut Ticket-Text blockiert von #{verweis}, aber die native Kante "
                f"fehlt — bau wartet dann nicht. Setzen: {befehl}"
            )
            continue
        zustand = offen(verweis)
        if zustand is None:
            bericht.fehler.append(
                f"#{nummer}: " + _github_fehler(bericht, f"für Blocker-Verweis #{verweis}")
            )
        elif zustand:
            bericht.fehler.append(
                f"#{nummer}: laut Ticket-Text blockiert von #{verweis} (offen, nicht in dieser "
                "Spec), aber keine native Kante — Entwurfs-Nummer? Text auf die echte Nummer "
                f"korrigieren oder Kante setzen: {befehl}"
            )
        else:
            bericht.warnungen.append(
                f"#{nummer}: Blocker-Verweis #{verweis} ist kein Ticket dieser Spec und schon "
                "zu (Entwurfs-Nummer aus /to-tickets?) — Text oder Kante prüfen."
            )
