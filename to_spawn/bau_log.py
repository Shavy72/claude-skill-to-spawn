"""Bau-Log und Ereignis-Protokoll (#902 / #1395 / #903 / #1398).

Schreib-Tür ``schreibe(typ, **felder)`` für das gemeinsame Ereignis-Protokoll:
- Prüft die Pflichtfelder (``zeit``, ``ticket``, ``wer``, ``was``, ``session``,
  ``commit``, ``grund``, ``kontextstand``).
- Hält beim Schreiben eine lokale Datei-Sperre über :mod:`to_spawn.sperre`.
- Puffert Zeilen unter ``~/.local/state/to-spawn/protokoll`` (nie im Worktree).
- Schiebt den Puffer gebündelt per Git-Plumbing (ohne Arbeitsbaum-/Index-Eingriff)
  auf den verwaisten Zweig ``protokoll`` (``bau_log/<Ticket>.jsonl``), mit
  Rebase/Retry bei Push-Konflikt, Idempotenz per ``idempotenz_schluessel`` und
  Offline-Puffer ohne Fehler an den Aufrufer.
- Leser (``lese``, ``zusammenfassung``, ``alle_tickets``, ``hat_log``,
  ``letzter_session_start``, ``kontext_aktuell_k``) lesen primär aus
  ``origin/protokoll`` und lokalem Puffer (#903). Historische lokale Dateien
  werden nur noch lesend einbezogen; lokale Schreibwege existieren nicht mehr.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, sperre

log = logging.getLogger("to_spawn.bau_log")

#: Typ der Zeile einer abgelehnten Komplett-Prüfung (#496); Zähler: ``komplett_lauf.ablehnungen``.
KOMPLETT_ABGELEHNT = "komplett_abgelehnt"
#: Ereignis-Typ für gemeldete Hindernisse (#904 / App #1399, Tür + Typ).
HINDERNIS_GEMELDET = "hindernis_gemeldet"
#: Ereignis-Typ und ticketfreie Datei für 15-Minuten-Auslastungszeilen (#904 / App #1399).
AUSLASTUNG_TYP = "auslastung"
AUSLASTUNG_TICKET = "auslastung"
#: Verdichtete Monats-Zusammenfassung des 90-Tage-Pflege-Laufs (#904 / App #1399).
MONATS_ZUSAMMENFASSUNG = "monats_zusammenfassung"
#: Standard-Aufbewahrungsfrist (Tage) vor Verdichtung in Monats-Zusammenfassungen (#904).
PFLEGE_ALTER_TAGE = 90

#: Pflicht-Ereignistypen des gemeinsamen Ereignis-Protokolls (#902 / #1395 / #904).
EREIGNIS_TYPEN: tuple[str, ...] = (
    "start",
    "uebergabe",
    "schliessen",
    "wiedereroeffnen",
    "hindernis",
    HINDERNIS_GEMELDET,
)

#: Pflichtfelder jeder Zeile im Ereignis-Protokoll (#902 / #1395).
PFLICHTFELDER: tuple[str, ...] = (
    "zeit",
    "ticket",
    "wer",
    "was",
    "session",
    "commit",
    "grund",
    "kontextstand",
)

#: Verwaistes Git-Ziel des Ereignis-Protokolls (#902 / #1395 / #903).
PROTOKOLL_ZWEIG = "protokoll"
PROTOKOLL_REF = f"refs/heads/{PROTOKOLL_ZWEIG}"
PROTOKOLL_REMOTE_REF = f"refs/remotes/origin/{PROTOKOLL_ZWEIG}"
PROTOKOLL_BAUM_ORDNER = "bau_log"
PROTOKOLL_MONATE_ORDNER = f"{PROTOKOLL_BAUM_ORDNER}/monate"
_MONAT_MUSTER = re.compile(r"^\d{4}-\d{2}$")
#: Env-Überschreibungen für Tests und Sonderläufe.
PROTOKOLL_PUFFER_ENV = "TO_SPAWN_PROTOKOLL_PUFFER"
PROTOKOLL_REPO_ENV = "TO_SPAWN_PROTOKOLL_REPO"
PROTOKOLL_BUENDEL_ENV = "TO_SPAWN_PROTOKOLL_BUENDEL_S"
#: Wartezeit auf die lokale Datei-Sperre (s) und maximale Konflikt-Wiederholungen beim Push.
SPERR_WARTE_S = 30.0
MAX_PUSH_VERSUCHE = 12
#: Mindestabstand (s) zwischen zwei Netz-Fetches auf ``origin/protokoll`` im selben Prozess.
_PROTOKOLL_FETCH_TAKT_S = 5.0
_letzter_protokoll_fetch: dict[Path, float] = {}
_NETZ_URL_MUSTER = re.compile(r"^(?:https?://|ssh://|git://|[^/\s\\]+@[^/\s\\]+:)", re.IGNORECASE)
_TICKET_MUSTER = re.compile(r"^[A-Za-z0-9._-]+$")
_TEXT_PFLICHTFELDER: tuple[str, ...] = ("wer", "was", "session", "commit", "grund")

#: Erlaubte Zeilen-Typen (``staffel_limit`` meldet das Ende der Staffel-Kette).
TYPEN = (
    *EREIGNIS_TYPEN,
    "auftrag",
    "session_start",
    "entscheidung",
    "deploy_phase",
    "handoff",
    "session_ende",
    "subagent_ende",
    "staffel_limit",
    # Aufseher (#213): Live-Beweis blockiert (Feld ``grund``) und Modell-Wechsel beim Limit.
    "blockiert",
    # Bau-Session (#278): Frage an den Aufseher (``waechter_takt.FRAGE_TYPEN``).
    "frage",
    "waechter_modell",
    # Aufseher (#254): Pause bis zum Limit-Reset und der Neustart danach.
    "waechter_pause",
    "waechter_weiter",
    "zusammenfassung",
    # Lernschleife (#286): erkannter Stillstand mit Klasse/Symptom/Ursache/Lösung.
    "vorfall",
    # bau.py (#274 P1): Warnung beim Start (Regel-Kopie ignoriert, Repo-Zusatz unbrauchbar), Feld ``text``.
    "start_warnung",
    # Aufseher (#448): eigenes Fehl-Reopen zurückgenommen (Feld ``text``).
    "ruecknahme",
    # Aufseher: Review-Meldung „Mensch nötig“ (Felder ``fingerprint``, ``fix_runde``).
    "mensch_noetig",
    # Aufsicht (#436): Kontext an der Handoff-Grenze → Aufseher-Ablösung über respawn
    # (Felder ``kontext``, ``grenze``, ``exit``, ``zeile``; ``exit`` null = nicht versucht).
    "aufseher_abloesung",
    # komplett_lauf (#496): Komplett-Prüfung aus einem Bau-Ticket abgelehnt (Felder ``spec``, ``weg``).
    KOMPLETT_ABGELEHNT,
    # Taktgeber (#906, App #1402): zustandslose Handlungen (geschlossen, label_gesetzt, geweckt; wer="taktgeber").
    "taktgeber",
    # Protokoll-Pflege & Auslastung (#904, App #1399): 15-Minuten-Auslastung und Monats-Verdichtung.
    AUSLASTUNG_TYP,
    MONATS_ZUSAMMENFASSUNG,
)


class ProtokollPflichtfeldFehler(ValueError):
    """Fehlendes oder ungültiges Pflichtfeld beim Schreiben ins Ereignis-Protokoll (#902)."""


#: Felder, deren ``None`` als ``null`` stehen bleibt, weil es etwas bedeutet (sonst
#: fallen ``None``-Felder weg). ``aufseher_abloesung.exit`` null = Ablösung nicht versucht.
NULL_FELDER: dict[str, frozenset[str]] = {
    "aufseher_abloesung": frozenset({"exit"}),
    AUSLASTUNG_TYP: frozenset(
        {"platte_frei_prozent", "speicher_frei_mib", "speicher_gesamt_mib", "sessions", "sessions_gesamt"}
    ),
}

#: Vorgabe-Ordner der versionierten Logs (Alias für Aufrufer/Tests; es gilt ``pfade.bau_log``).
LOG_ORDNER = Path(config.DEFAULTS["pfade"]["bau_log"])
LAUF_ORDNER = Path(".to-spawn") / "bau_log"
_WT_MUSTER = re.compile(r"wt-(\d+)")


def log_pfad(repo: Path, ticket: str | int) -> Path:
    """Historische versionierte Datei (nur noch lesend für Altbestand)."""
    return log_ordner(repo) / f"{ticket}.jsonl"


def log_ordner(repo: Path) -> Path:
    """Ordner historischer versionierter Logs aus ``pfade.bau_log`` (fehlt er: Vorgabe), unter ``repo`` wie übergeben."""
    return config.pfad(config.Konfig(config.lade(repo), repo / config.KONFIG_PFAD), "bau_log")


def log_im_repo(repo: Path, ticket: str | int) -> str:
    """Historische Datei als Repo-Pfad (``/``-getrennt) für ``git show`` und Hinweise.

    Liegt ``pfade.bau_log`` außerhalb des Repos, kommt der absolute Pfad (``git show`` findet ihn dann nicht).
    """
    datei = log_pfad(repo, ticket)
    try:
        return datei.relative_to(repo).as_posix()
    except ValueError:
        return datei.as_posix()


def lauf_pfad(repo: Path, ticket: str | int) -> Path:
    """Historische unversionierte Laufdatei (nur noch lesend für Altbestand)."""
    return repo / LAUF_ORDNER / f"{ticket}.jsonl"


def hat_log(repo: Path, ticket: str | int) -> bool:
    return hat_protokoll(repo, ticket) or log_pfad(repo, ticket).is_file() or lauf_pfad(repo, ticket).is_file()


def log_ort(ticket: str | int, worktree: Path, hauptbaum: Path) -> Path | None:
    """Wo das Bau-Log eines Tickets liegt: zuerst im Ticket-Worktree, sonst im Hauptbaum.

    Dort schreiben die Hooks; der Hauptbaum hält das Log, wenn der Worktree fehlt.
    ``None`` = kein Log (Ticket noch nicht gestartet). Einzige Stelle dieser Regel
    für ``sessions_stand.token_text`` und den Aufseher-Stand (#430).
    """
    for ort in (worktree, hauptbaum):
        if hat_log(ort, ticket):  # versioniert oder Laufdatei (Fixrunde #204)
            return ort
    return None


def log_ort_fuer(ticket: str | int, repo: Path) -> Path | None:
    """Wie :func:`log_ort`, bestimmt den Ticket-Worktree selbst über :func:`config.worktree_pfad` (#438).

    Gemeinsamer Helfer, damit Aufrufer ``config.worktree_pfad`` und :func:`log_ort` nicht
    jeweils selbst zusammensetzen.
    """
    return log_ort(ticket, Path(config.worktree_pfad(ticket, repo)).expanduser(), repo)


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """``git <args>`` im ``repo`` mit Zeitgrenze :data:`gh.GIT_ZEIT_S` (#375)."""
    from . import gh  # spät: gh fragt config, aber nicht bau_log

    return gh._git_ausgabe(list(args), repo, timeout=gh.GIT_ZEIT_S)


def haupt_ref(repo: Path) -> str | None:
    """``origin/<Hauptzweig>`` nach der Regel von ``gh.hauptzweig`` (#257, #375); ``None`` ohne den Ref."""
    from . import gh  # spät: gh fragt config, aber nicht bau_log

    ref = f"origin/{gh.hauptzweig(repo)}"
    if _git(repo, "rev-parse", "--verify", "-q", ref)[0] == 0:
        return ref
    return None


def zeilen_aus(text: str, quelle: str) -> list[dict[str, Any]]:
    """JSONL-Zeilen parsen; kaputte Zeilen werden gewarnt und als ``{"typ": "kaputt"}`` gezählt."""
    zeilen: list[dict[str, Any]] = []
    for nr, roh in enumerate(text.splitlines(), 1):
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            eintrag = None
        if not isinstance(eintrag, dict):
            log.warning("Kaputte Bau-Log-Zeile %s:%s — als „kaputt“ gezählt.", quelle, nr)
            eintrag = {"typ": "kaputt"}
        zeilen.append(eintrag)
    return zeilen


def log_vom_ref(repo: Path, ref: str, ticket: str | int) -> list[dict[str, Any]]:
    """Bau-Log-Zeilen eines Tickets, wie sie auf ``ref`` liegen (#375)."""
    pfad = log_im_repo(repo, ticket)
    code, text = _git(repo, "show", f"{ref}:{pfad}")
    if code != 0:
        return []
    return zeilen_aus(text, f"{ref}:{pfad}")


def entscheidung_felder(z: dict[str, Any]) -> tuple[str, str, str]:
    """(Frage, Wahl, Grund) einer ``entscheidung``-Zeile, ungekürzt (#375).

    Einzige Stelle für den Rückfall auf die alten Bau-Log-Felder (``text`` → Frage,
    ``entscheidungen`` → Wahl). Kürzen und Formatieren machen die Aufrufer.
    """
    frage = str(z.get("frage") or z.get("text") or "")
    wahl = str(z.get("wahl") or z.get("entscheidungen") or "")
    grund = str(z.get("grund") or "")
    return frage, wahl, grund


def hat_log_auf_origin(repo: Path, ticket: str | int, *, hauptzweig: str | None = None) -> bool:
    """Liegt ein versioniertes Bau-Log von ``ticket`` auf ``origin/<Hauptzweig>`` oder
    einem geholten Remote-Ticket-Zweig ``origin/ticket/<N>-*``? (#438)

    Prüft nur lokale Remote-Tracking-Refs (kein Netz, kein ``git fetch``). Unversionierte
    Laufdateien auf einem anderen Rechner liegen außerhalb von Git und sind hier nicht sichtbar.
    """
    from . import gh  # spät: gh fragt config, aber nicht bau_log

    if _git(repo, "cat-file", "-e", f"{PROTOKOLL_REMOTE_REF}:{protokoll_datei_im_zweig(ticket)}")[0] == 0:
        return True
    pfad = log_im_repo(repo, ticket)
    if Path(pfad).is_absolute():
        return False
    haupt = hauptzweig or gh.hauptzweig(repo)
    refs = [f"origin/{haupt}"]
    code, zweige = _git(repo, "for-each-ref", "--format=%(refname)", f"refs/remotes/origin/ticket/{ticket}-*")
    if code == 0 and zweige:
        refs.extend(z.strip() for z in zweige.splitlines() if z.strip())
    for ref in refs:
        if _git(repo, "cat-file", "-e", f"{ref}:{pfad}")[0] == 0:
            return True
    return False


def kontext_text(spitze_k: float | None) -> str:
    """Spitzen-Kontext als Text, z. B. „180,2k“; ``—`` ohne Wert (#238, #430)."""
    return f"{_komma(spitze_k)}k" if spitze_k else "—"


#: Rückfall-Ordner für die Laufdatei, wenn der Worktree (noch/nicht mehr) fehlt (#257):
#: ``bau.py`` setzt ``TO_SPAWN_LOG_RUECKFALL`` auf den Hauptbaum (``TO_SPAWN_REPO`` nimmt es
#: aus der Session-Umgebung, #205); ``TO_SPAWN_REPO`` gilt außerhalb einer Session.
RUECKFALL_VARIABLEN = ("TO_SPAWN_LOG_RUECKFALL", "TO_SPAWN_REPO")


def log_rueckfall() -> Path | None:
    """Hauptbaum aus ``RUECKFALL_VARIABLEN`` (erster vorhandener Ordner), sonst ``None``."""
    for name in RUECKFALL_VARIABLEN:
        wert = os.environ.get(name, "").strip()
        if wert and Path(wert).expanduser().is_dir():
            return Path(wert).expanduser().resolve()
    return None


def log_repo(fallback: Path | None = None, *, versioniert: bool = False) -> Path | None:
    """Ziel-Repo für Ereignis-Protokoll-Aufrufe aus Hooks, ``eintrag`` und Aufseher (#204, #903).

    ``TO_SPAWN_LOG_REPO`` gesetzt (setzt ``bau.py`` auf den Ticket-Worktree) → genau
    dieser Ordner, wenn er existiert. Fehlt er (noch nicht angelegt oder schon vom
    Launcher gelöscht), gilt ohne ``versioniert=True`` der Hauptbaum aus
    :func:`log_rueckfall` (#257); mit ``versioniert=True`` (z. B. ``eintrag`` ohne
    vorhandenes Ziel) bleibt es bei ``None``.
    Ohne Variable gilt ``fallback`` bzw. die Git-Wurzel des aktuellen Ordners.
    """
    ziel = os.environ.get("TO_SPAWN_LOG_REPO", "").strip()
    if ziel:
        pfad = Path(ziel).expanduser()
        if pfad.is_dir():
            return pfad.resolve()
        rueckfall = None if versioniert else log_rueckfall()
        if rueckfall is not None:
            log.info("TO_SPAWN_LOG_REPO %s existiert nicht — Laufdatei im Hauptbaum %s.", pfad, rueckfall)
            return rueckfall
        log.info(
            "TO_SPAWN_LOG_REPO %s existiert (noch) nicht — keine Log-Zeile, kein Ausweichen in den Hauptbaum.",
            pfad,
        )
        return None
    if fallback is not None:
        return fallback
    return config.repo_wurzel()


def ticket_aus_umgebung(cwd: Path | None = None) -> str | None:
    """Ticket-Nummer aus ``TO_SPAWN_TICKET`` oder aus einem Worktree-Pfad ``wt-<N>``."""
    aus_env = os.environ.get("TO_SPAWN_TICKET")
    if aus_env and aus_env.strip().isdigit():
        return aus_env.strip()
    pfad = (cwd or Path.cwd()).resolve()
    for teil in [pfad.name, *(eltern.name for eltern in pfad.parents)]:
        treffer = _WT_MUSTER.fullmatch(teil)
        if treffer:
            return treffer.group(1)
    return None


def jetzt() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _haenge_an(datei: Path, rohzeilen: Iterable[str]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8", newline="\n") as fh:
        for roh in rohzeilen:
            fh.write(roh + "\n")


def rohzeilen(datei: Path) -> list[str]:
    """Nicht-leere Zeilen einer Log-Datei, ungeparst; fehlende Datei = leer.

    ``OSError``/``UnicodeDecodeError`` gehen an den Aufrufer (unlesbar ≠ leer).
    """
    if not datei.is_file():
        return []
    return [z.strip() for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def _fehlende(fest: list[str], lauf: list[str]) -> list[str]:
    """Laufdatei-Zeilen, die in der versionierten Datei fehlen (exakt je Zeile).

    Gezählt wie eine Mehrfachmenge: steht eine Zeile zweimal in der Laufdatei und
    einmal versioniert, fehlt sie genau einmal.
    """
    vorrat = Counter(fest)
    fehlend: list[str] = []
    for roh in lauf:
        if vorrat[roh] > 0:
            vorrat[roh] -= 1
        else:
            fehlend.append(roh)
    return fehlend


def protokoll_puffer_ordner() -> Path:
    """Puffer-Ordner für das Ereignis-Protokoll (Vorgabe: ``~/.local/state/to-spawn/protokoll``, #902).

    Liegt immer außerhalb von Repo und Worktree. Über ``TO_SPAWN_PROTOKOLL_PUFFER``
    (z. B. in Tests) überschreibbar.
    """
    vorgabe = os.environ.get(PROTOKOLL_PUFFER_ENV, "").strip()
    if vorgabe:
        return Path(vorgabe).expanduser()
    return config.heim_ordner() / ".local" / "state" / "to-spawn" / "protokoll"


def protokoll_datei_im_zweig(ticket: str | int) -> str:
    """Pfad der Ticket-Datei auf dem Zweig ``protokoll`` (immer ``/``-getrennt)."""
    return f"{PROTOKOLL_BAUM_ORDNER}/{ticket}.jsonl"


def _protokoll_jetzt() -> str:
    """ISO-8601-Zeitstempel mit Mikrosekunden für eindeutige zeitliche Ordnung."""
    return datetime.now(UTC).astimezone().isoformat(timespec="microseconds")


def _protokoll_repo(vorgabe: Path | None = None) -> Path | None:
    """Ziel-Repo für den Git-Plumbing-Push des Ereignis-Protokolls."""
    if vorgabe is not None:
        pfad = Path(vorgabe).expanduser()
        return pfad.resolve() if pfad.is_dir() else None
    aus_env = os.environ.get(PROTOKOLL_REPO_ENV, "").strip()
    if aus_env:
        pfad = Path(aus_env).expanduser()
        if pfad.is_dir():
            return pfad.resolve()
    return log_repo()


def _ist_im_ordner(pfad: Path, basis: Path) -> bool:
    try:
        pfad.resolve().relative_to(basis.resolve())
        return True
    except ValueError:
        return False


def _pruefe_puffer_ausserhalb(puffer: Path, repo: Path | None) -> None:
    if repo is not None and _ist_im_ordner(puffer, repo):
        raise ValueError(f"Protokoll-Puffer darf nie im Arbeitsordner liegen: {puffer}")


def _buendel_intervall_s() -> float:
    roh = os.environ.get(PROTOKOLL_BUENDEL_ENV, "").strip()
    if not roh:
        return 0.0
    try:
        wert = float(roh)
    except ValueError:
        return 0.0
    return max(wert, 0.0)


def _pruefe_pflichtfelder(typ: str, felder: dict[str, Any]) -> dict[str, Any]:
    """Prüft alle Pflichtfelder einer Ereignis-Protokoll-Zeile und normalisiert sie (#902)."""
    if not isinstance(typ, str) or not typ.strip():
        raise ProtokollPflichtfeldFehler("Ereignis-Typ darf nicht leer sein.")
    typ_wert = typ.strip()
    if typ_wert not in TYPEN:
        log.warning("Unbekannter Zeilen-Typ %r — wird trotzdem geschrieben.", typ_wert)

    if "zeit" in felder:
        zeit_roh = felder["zeit"]
        if not isinstance(zeit_roh, str) or not zeit_roh.strip() or _zeitpunkt(zeit_roh) is None:
            raise ProtokollPflichtfeldFehler("Pflichtfeld 'zeit' muss ein gültiger ISO-Zeitstempel sein.")
        zeit_wert = zeit_roh.strip()
    elif "ts" in felder:
        zeit_roh = felder["ts"]
        if not isinstance(zeit_roh, str) or not zeit_roh.strip() or _zeitpunkt(zeit_roh) is None:
            raise ProtokollPflichtfeldFehler("Pflichtfeld 'zeit' muss ein gültiger ISO-Zeitstempel sein.")
        zeit_wert = zeit_roh.strip()
    else:
        zeit_wert = _protokoll_jetzt()

    if "ticket" not in felder:
        raise ProtokollPflichtfeldFehler("Pflichtfeld 'ticket' fehlt.")
    ticket_roh = felder["ticket"]
    if ticket_roh is None or isinstance(ticket_roh, bool) or not isinstance(ticket_roh, (str, int)):
        raise ProtokollPflichtfeldFehler("Pflichtfeld 'ticket' ist ungültig.")
    ticket_wert = str(ticket_roh).strip()
    if not ticket_wert or ".." in ticket_wert or not _TICKET_MUSTER.fullmatch(ticket_wert):
        raise ProtokollPflichtfeldFehler(f"Pflichtfeld 'ticket' hat unzulässigen Wert: {ticket_roh!r}")

    textwerte: dict[str, str] = {}
    for name in _TEXT_PFLICHTFELDER:
        if name not in felder:
            raise ProtokollPflichtfeldFehler(f"Pflichtfeld '{name}' fehlt.")
        wert = felder[name]
        if not isinstance(wert, str) or not wert.strip():
            raise ProtokollPflichtfeldFehler(f"Pflichtfeld '{name}' darf nicht leer sein.")
        textwerte[name] = wert.strip()

    if "kontextstand" not in felder:
        raise ProtokollPflichtfeldFehler("Pflichtfeld 'kontextstand' fehlt.")
    kontext_roh = felder["kontextstand"]
    if kontext_roh is None or isinstance(kontext_roh, bool):
        raise ProtokollPflichtfeldFehler("Pflichtfeld 'kontextstand' darf nicht leer oder boolesch sein.")
    if isinstance(kontext_roh, str):
        kontext_wert: str | int | float = kontext_roh.strip()
        if not kontext_wert:
            raise ProtokollPflichtfeldFehler("Pflichtfeld 'kontextstand' darf nicht leer sein.")
    elif isinstance(kontext_roh, (int, float)):
        kontext_wert = kontext_roh
    else:
        raise ProtokollPflichtfeldFehler("Pflichtfeld 'kontextstand' muss Zahl oder Text sein.")

    zeile: dict[str, Any] = {
        "zeit": zeit_wert,
        "typ": typ_wert,
        "ticket": ticket_wert,
        **textwerte,
        "kontextstand": kontext_wert,
    }
    bleibt = NULL_FELDER.get(typ_wert, frozenset())
    vergeben = set(PFLICHTFELDER) | {"ts", "typ"}
    for key, val in felder.items():
        if key in vergeben:
            continue
        if val is not None or key in bleibt:
            zeile[key] = val
    return zeile


def _sortiere_rohzeilen(zeilen: list[str]) -> list[str]:
    """Sortiert JSONL-Rohzeilen stabil nach Zeitstempel (``zeit`` oder ``ts``)."""

    def _schluessel(roh: str) -> datetime:
        try:
            eintrag = json.loads(roh)
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        if not isinstance(eintrag, dict):
            return datetime.min.replace(tzinfo=UTC)
        return _sortier_zeit(eintrag)

    return sorted(zeilen, key=_schluessel)


def _puffer_lesen(puffer: Path) -> dict[str, list[str]]:
    if not puffer.is_dir():
        return {}
    wartend: dict[str, list[str]] = {}
    for datei in sorted(puffer.glob("*.jsonl")):
        ticket = datei.stem
        if not _TICKET_MUSTER.fullmatch(ticket) or ".." in ticket:
            continue
        zeilen = rohzeilen(datei)
        if zeilen:
            wartend[ticket] = zeilen
    return wartend


def _puffer_leeren(puffer: Path, gesendet: dict[str, list[str]]) -> None:
    for ticket, weg in gesendet.items():
        datei = puffer / f"{ticket}.jsonl"
        if not datei.is_file():
            continue
        rest = _fehlende(weg, rohzeilen(datei))
        if not rest:
            with contextlib.suppress(OSError):
                datei.unlink()
        else:
            datei.write_text("\n".join(rest) + "\n", encoding="utf-8", newline="\n")


def _buendel_faellig(puffer: Path, buendel_s: float) -> bool:
    if buendel_s <= 0:
        return True
    marker = puffer / ".letzter_push"
    if not marker.is_file():
        return True
    try:
        letzter = float(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return (time.time() - letzter) >= buendel_s


def _merke_push_zeit(puffer: Path) -> None:
    with contextlib.suppress(OSError):
        (puffer / ".letzter_push").write_text(f"{time.time():.6f}\n", encoding="utf-8", newline="\n")


def _idempotenz_schluessel_von(roh: str) -> str:
    """Liest ``idempotenz_schluessel`` aus einer JSONL-Rohzeile (leer, wenn nicht gesetzt)."""
    try:
        eintrag = json.loads(roh)
    except ValueError:
        return ""
    if not isinstance(eintrag, dict):
        return ""
    wert = eintrag.get("idempotenz_schluessel")
    return wert.strip() if isinstance(wert, str) else ""


def _hat_idempotenz_schluessel(roh_liste: Iterable[str], schluessel: str) -> dict[str, Any] | None:
    """Sucht den ersten Eintrag mit passendem ``idempotenz_schluessel`` in Rohzeilen."""
    if not schluessel:
        return None
    for roh in roh_liste:
        if _idempotenz_schluessel_von(roh) == schluessel:
            try:
                eintrag = json.loads(roh)
            except ValueError:
                continue
            if isinstance(eintrag, dict):
                return eintrag
    return None


def _fehlende_mit_idempotenz(fest: list[str], lauf: list[str]) -> list[str]:
    """Wie :func:`_fehlende`, überspringt aber zusätzlich doppelte ``idempotenz_schluessel``."""
    gesehene_schluessel = {s for roh in fest if (s := _idempotenz_schluessel_von(roh))}
    vorrat = Counter(fest)
    fehlend: list[str] = []
    for roh in lauf:
        if vorrat[roh] > 0:
            vorrat[roh] -= 1
            continue
        schluessel = _idempotenz_schluessel_von(roh)
        if schluessel:
            if schluessel in gesehene_schluessel:
                continue
            gesehene_schluessel.add(schluessel)
        fehlend.append(roh)
    return fehlend


def _origin_darf_netz(repo: Path) -> tuple[bool, bool]:
    """Prüft, ob ``repo`` ein nutzbares ``origin`` hat: ``(nutzbar, ist_netz_url)``.

    Unter Test (:func:`starter.unter_test`) werden echte Netz-URLs (``https://``,
    ``ssh://``, ``git@…``) nie angefunkt, damit Unit-Tests mit Attrappen-Origins kein
    Netz brauchen; lokale Bare-Repos laufen voll echt (#903).
    """
    from . import gh, starter  # spät: gh/starter importieren config, aber nicht bau_log

    if not repo.is_dir():
        return False, False
    url = gh._origin_url(repo)
    if not url:
        return False, False
    ist_netz = bool(_NETZ_URL_MUSTER.match(url))
    if ist_netz and starter.unter_test():
        return False, True
    return True, ist_netz


def protokoll_holen(repo: Path, *, erzwingen: bool = False) -> bool:
    """Aktualisiert ``refs/remotes/origin/protokoll`` per Plumbing-Fetch (ohne Arbeitsbaum-Eingriff, #903).

    Bei lokalen Bare-Repos immer frisch; bei Netz-URLs im Betrieb höchstens alle
    :data:`_PROTOKOLL_FETCH_TAKT_S` Sekunden (außer ``erzwingen=True``). Fehler werden
    nur geloggt, nie nach oben geworfen.
    """
    from . import gh

    nutzbar, ist_netz = _origin_darf_netz(repo)
    if not nutzbar:
        return False
    schluessel = repo.resolve()
    jetzt_mono = time.monotonic()
    if ist_netz and not erzwingen:
        letzter = _letzter_protokoll_fetch.get(schluessel)
        if letzter is not None and (jetzt_mono - letzter) < _PROTOKOLL_FETCH_TAKT_S:
            return True
    code, _out, err = gh._git_lauf(
        ["fetch", "-q", "--no-tags", "origin", f"+{PROTOKOLL_REF}:{PROTOKOLL_REMOTE_REF}"],
        repo,
        timeout=gh.FETCH_ZEIT_S,
    )
    if ist_netz:
        _letzter_protokoll_fetch[schluessel] = time.monotonic()
    if code != 0:
        log.debug(
            "Ereignis-Protokoll: Fetch von origin/%s in %s ohne Treffer (%s).", PROTOKOLL_ZWEIG, repo, err.strip()
        )
        return False
    return True


def protokoll_ref(repo: Path) -> str | None:
    """``refs/remotes/origin/protokoll``, wenn im Repo vorhanden; sonst ``None`` (#903)."""
    if not repo.is_dir():
        return None
    if _git(repo, "rev-parse", "--verify", "-q", PROTOKOLL_REMOTE_REF)[0] == 0:
        return PROTOKOLL_REMOTE_REF
    return None


def _protokoll_rohzeilen_vom_ref(repo: Path, ticket: str | int) -> list[str]:
    if not repo.is_dir():
        return []
    ticket_text = str(ticket).strip()
    if not ticket_text or ".." in ticket_text or not _TICKET_MUSTER.fullmatch(ticket_text):
        return []
    pfad = protokoll_datei_im_zweig(ticket_text)
    code, text = _git(repo, "show", f"{PROTOKOLL_REMOTE_REF}:{pfad}")
    if code != 0 or not text:
        return []
    return [z.strip() for z in text.splitlines() if z.strip()]


def _puffer_rohzeilen(ticket: str | int, puffer_ordner: Path | None = None) -> list[str]:
    ticket_text = str(ticket).strip()
    if not ticket_text or ".." in ticket_text or not _TICKET_MUSTER.fullmatch(ticket_text):
        return []
    puffer = Path(puffer_ordner).expanduser() if puffer_ordner is not None else protokoll_puffer_ordner()
    datei = puffer / f"{ticket_text}.jsonl"
    try:
        return rohzeilen(datei)
    except OSError as fehler:
        log.warning("Protokoll-Puffer %s nicht lesbar: %s", datei, fehler)
        return []


def hat_protokoll(
    repo: Path,
    ticket: str | int,
    *,
    holen: bool = True,
    puffer_ordner: Path | None = None,
) -> bool:
    """Liegt für ``ticket`` ein Ereignis-Protokoll auf ``origin/protokoll`` oder im Puffer (#903)?"""
    if not repo.is_dir():
        return False
    ticket_text = str(ticket).strip()
    if not ticket_text or ".." in ticket_text or not _TICKET_MUSTER.fullmatch(ticket_text):
        return False
    if holen:
        protokoll_holen(repo)
    pfad = protokoll_datei_im_zweig(ticket_text)
    if _git(repo, "cat-file", "-e", f"{PROTOKOLL_REMOTE_REF}:{pfad}")[0] == 0:
        return True
    return bool(_puffer_rohzeilen(ticket_text, puffer_ordner))


def protokoll_zeilen(
    repo: Path,
    ticket: str | int,
    *,
    holen: bool = True,
    puffer_ordner: Path | None = None,
) -> list[dict[str, Any]]:
    """Liest alle Ereignis-Protokoll-Zeilen für ``ticket`` aus ``origin/protokoll`` und lokalem Puffer (#903)."""
    if holen:
        protokoll_holen(repo)
    remote_roh = _protokoll_rohzeilen_vom_ref(repo, ticket)
    puffer_roh = _puffer_rohzeilen(ticket, puffer_ordner)
    kombiniert = [*remote_roh, *_fehlende_mit_idempotenz(remote_roh, puffer_roh)]
    paare = _parse_rohzeilen(kombiniert, f"{PROTOKOLL_REMOTE_REF}:{protokoll_datei_im_zweig(ticket)}")
    return sorted((eintrag for _, eintrag in paare), key=_sortier_zeit)


def protokoll_tickets(
    repo: Path,
    *,
    holen: bool = True,
    puffer_ordner: Path | None = None,
) -> list[str]:
    """Alle Ticket-Nummern, die auf ``origin/protokoll`` oder im lokalen Puffer liegen (#903)."""
    nummern: set[str] = set()
    if repo.is_dir():
        if holen:
            protokoll_holen(repo)
        code, ausgabe = _git(repo, "ls-tree", "--name-only", PROTOKOLL_REMOTE_REF, f"{PROTOKOLL_BAUM_ORDNER}/")
        if code == 0 and ausgabe:
            for pfad_str in ausgabe.splitlines():
                name = Path(pfad_str.strip()).name
                if name.endswith(".jsonl"):
                    stamm = name[:-6]
                    if stamm.isdigit():
                        nummern.add(stamm)
    puffer = Path(puffer_ordner).expanduser() if puffer_ordner is not None else protokoll_puffer_ordner()
    if puffer.is_dir():
        nummern.update(p.stem for p in puffer.glob("*.jsonl") if p.stem.isdigit())
    return sorted(nummern, key=int)


def git_kopf_kurz(repo: Path | None, ticket: str | int | None = None) -> str:
    """Kurzer HEAD-Commit des Ticket-Worktrees oder Repos; ``"-"``, wenn kein Commit lesbar (#903)."""
    kandidaten: list[Path] = []
    if repo is not None:
        if ticket is not None:
            try:
                wt = Path(config.worktree_pfad(ticket, repo)).expanduser()
            except (OSError, ValueError):
                wt = None
            if wt is not None and wt.is_dir():
                kandidaten.append(wt)
        if repo.is_dir():
            kandidaten.append(repo)
    for pfad in kandidaten:
        code, sha = _git(pfad, "rev-parse", "--short", "HEAD")
        if code == 0 and sha.strip():
            return sha.strip()
    return "-"


def kontextstand_aktuell(repo: Path | None, ticket: str | int | None = None) -> float | int:
    """Aktueller Kontextstand (in k) für Pflicht-Ereignisse; ``0``, wenn keiner vorliegt (#903)."""
    if repo is None or ticket is None or not repo.is_dir():
        return 0
    try:
        wert = kontext_aktuell_k(repo, ticket)
        if wert is not None:
            return wert
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return 0


def _plumbing_push_einmal(repo: Path, wartend: dict[str, list[str]]) -> tuple[bool, bool, str]:
    """Ein atomarer Git-Plumbing-Push aller gepufferten Tickets auf ``refs/heads/protokoll`` (#902).

    Arbeitet komplett ohne Arbeitsbaum- oder Index-Eingriff (eigene temporäre ``GIT_INDEX_FILE``).
    Rückgabe ``(ok, konflikt, fehler)``.
    """
    from . import gh  # spät: gh fragt config, aber nicht bau_log

    nutzbar, _ist_netz = _origin_darf_netz(repo)
    if not nutzbar:
        return False, False, "kein erreichbares origin konfiguriert"

    ok_ref, remote_sha, fehler_ref = gh.remote_ref_sha(repo, PROTOKOLL_REF)
    if not ok_ref:
        return False, False, fehler_ref

    temp_ref = ""
    parent_sha = ""
    try:
        if remote_sha:
            temp_ref = f"refs/to-spawn/tmp/protokoll-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}"
            c_fetch, s_fetch, e_fetch = gh._git_lauf(
                ["fetch", "-q", "--no-tags", "origin", f"+{PROTOKOLL_REF}:{temp_ref}"],
                repo,
                timeout=gh.FETCH_ZEIT_S,
            )
            if c_fetch != 0:
                return False, False, gh._git_fehler_kurz(c_fetch, s_fetch, e_fetch)
            c_rev, s_rev, e_rev = gh._git_lauf(
                ["rev-parse", "--verify", f"{temp_ref}^{{commit}}"],
                repo,
                timeout=gh.GIT_ZEIT_S,
            )
            parent_sha = s_rev.strip()
            if c_rev != 0 or not gh._GIT_SHA.fullmatch(parent_sha):
                return False, False, gh._git_fehler_kurz(c_rev, s_rev, e_rev)

        with tempfile.TemporaryDirectory(prefix="to-spawn-protokoll-") as tmp_dir:
            tmp_pfad = Path(tmp_dir)
            index_env = {"GIT_INDEX_FILE": str(tmp_pfad / "index")}
            if parent_sha:
                c_rt, s_rt, e_rt = gh._git_lauf(["read-tree", parent_sha], repo, extra_env=index_env)
                if c_rt != 0:
                    return False, False, gh._git_fehler_kurz(c_rt, s_rt, e_rt)

            neue_gesamt = 0
            geaenderte_tickets: list[str] = []
            for ticket, lokal_roh in sorted(wartend.items()):
                baum_pfad = protokoll_datei_im_zweig(ticket)
                if parent_sha:
                    c_cat, s_cat, _e_cat = gh._git_lauf(["cat-file", "-p", f"{parent_sha}:{baum_pfad}"], repo)
                    remote_roh = [z.strip() for z in s_cat.splitlines() if z.strip()] if c_cat == 0 else []
                else:
                    remote_roh = []
                fehlend = _fehlende_mit_idempotenz(remote_roh, lokal_roh)
                if not fehlend:
                    continue
                neue_gesamt += len(fehlend)
                geaenderte_tickets.append(ticket)
                kombiniert = _sortiere_rohzeilen([*remote_roh, *fehlend])
                blob_datei = tmp_pfad / f"{ticket}.jsonl"
                blob_datei.write_text("\n".join(kombiniert) + "\n", encoding="utf-8", newline="\n")
                c_hash, s_hash, e_hash = gh._git_lauf(["hash-object", "-w", "--", str(blob_datei)], repo)
                blob_sha = s_hash.strip()
                if c_hash != 0 or not gh._GIT_SHA.fullmatch(blob_sha):
                    return False, False, gh._git_fehler_kurz(c_hash, s_hash, e_hash)
                c_upd, s_upd, e_upd = gh._git_lauf(
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{baum_pfad}"],
                    repo,
                    extra_env=index_env,
                )
                if c_upd != 0:
                    return False, False, gh._git_fehler_kurz(c_upd, s_upd, e_upd)

            if neue_gesamt == 0:
                return True, False, ""

            c_tree, s_tree, e_tree = gh._git_lauf(["write-tree"], repo, extra_env=index_env)
            tree_sha = s_tree.strip()
            if c_tree != 0 or not gh._GIT_SHA.fullmatch(tree_sha):
                return False, False, gh._git_fehler_kurz(c_tree, s_tree, e_tree)

        commit_args = ["-c", "commit.gpgsign=false", "commit-tree", tree_sha]
        if parent_sha:
            commit_args += ["-p", parent_sha]
        commit_args += ["-F", "-"]
        tickets_kurz = ", ".join(f"#{t}" for t in geaenderte_tickets[:5])
        if len(geaenderte_tickets) > 5:
            tickets_kurz += f" +{len(geaenderte_tickets) - 5}"
        nachricht = f"protokoll: {neue_gesamt} Ereignis(se) ({tickets_kurz})\n"
        c_com, s_com, e_com = gh._git_lauf(
            commit_args,
            repo,
            eingabe=nachricht,
            extra_env=gh._INTERN_GIT_ENV,
        )
        commit_sha = s_com.strip()
        if c_com != 0 or not gh._GIT_SHA.fullmatch(commit_sha):
            return False, False, gh._git_fehler_kurz(c_com, s_com, e_com)

        ok_push, konflikt, fehler_push = gh.remote_ref_cas_setzen(repo, PROTOKOLL_REF, commit_sha, parent_sha)
        if ok_push:
            gh._git_lauf(["update-ref", PROTOKOLL_REMOTE_REF, commit_sha], repo)
            return True, False, ""
        return False, konflikt, fehler_push
    finally:
        if temp_ref:
            gh._git_lauf(["update-ref", "-d", temp_ref], repo)


def _schiebe_puffer_ein_durchlauf(repo: Path, wartend: dict[str, list[str]], puffer: Path) -> bool:
    """Schiebt ``wartend`` gebündelt mit Konflikt-Retry (hält dabei keine Puffer-Sperre)."""
    letzter_fehler = ""
    for versuch in range(1, MAX_PUSH_VERSUCHE + 1):
        ok, konflikt, fehler = _plumbing_push_einmal(repo, wartend)
        if ok:
            return True
        letzter_fehler = fehler
        if not konflikt:
            log.warning(
                "Ereignis-Protokoll: Push auf %s fehlgeschlagen (%s) — Zeilen bleiben im Puffer %s.",
                PROTOKOLL_ZWEIG,
                fehler,
                puffer,
            )
            return False
        if versuch < MAX_PUSH_VERSUCHE:
            time.sleep(min(0.03 * versuch, 0.25) + random.uniform(0.0, 0.05))
    log.warning(
        "Ereignis-Protokoll: Push auf %s nach %d Konflikt-Versuchen offen (%s) — Zeilen bleiben im Puffer %s.",
        PROTOKOLL_ZWEIG,
        MAX_PUSH_VERSUCHE,
        letzter_fehler,
        puffer,
    )
    return False


def _schiebe_puffer(puffer: Path, repo: Path | None) -> bool:
    """Serialisiert lokale Pushes über ``protokoll.lock``, hält ``puffer.lock`` nur für Datei-I/O."""
    push_sperre = puffer / "protokoll.lock"
    puffer_sperre = puffer / "puffer.lock"
    with sperre.halte(push_sperre, warten=SPERR_WARTE_S) as gehalten:
        if not gehalten:
            log.warning(
                "Ereignis-Protokoll: Push-Sperre %s nach %.1f s belegt — Puffer bleibt liegen.",
                push_sperre,
                SPERR_WARTE_S,
            )
            return False
        for _ in range(3):
            with sperre.halte(puffer_sperre, warten=SPERR_WARTE_S) as p_gehalten:
                if not p_gehalten:
                    log.warning("Ereignis-Protokoll: Puffer-Sperre %s belegt — Push vertagt.", puffer_sperre)
                    return False
                wartend = _puffer_lesen(puffer)
            if not wartend:
                return True
            if repo is None:
                log.warning("Ereignis-Protokoll: kein Repo gefunden — Zeilen bleiben im Puffer %s.", puffer)
                return False
            if not _schiebe_puffer_ein_durchlauf(repo, wartend, puffer):
                return False
            with sperre.halte(puffer_sperre, warten=SPERR_WARTE_S) as p_gehalten:
                if not p_gehalten:
                    log.warning("Ereignis-Protokoll: Puffer-Sperre %s nach Push belegt.", puffer_sperre)
                    return False
                _puffer_leeren(puffer, wartend)
                _merke_push_zeit(puffer)
                if not _puffer_lesen(puffer):
                    return True
        return False


def spuele(repo: Path | None = None, *, puffer_ordner: Path | None = None) -> bool:
    """Schiebt alle im lokalen Puffer wartenden Ereignis-Zeilen gebündelt auf ``protokoll`` (#902).

    Gibt ``True`` zurück, wenn der Puffer danach leer ist (nichts offen oder erfolgreich
    gepusht), ``False``, wenn der Push offline/gesperrt war (Zeilen bleiben im Puffer).
    """
    puffer = Path(puffer_ordner).expanduser() if puffer_ordner is not None else protokoll_puffer_ordner()
    ziel_repo = _protokoll_repo(repo)
    _pruefe_puffer_ausserhalb(puffer, ziel_repo)
    return _schiebe_puffer(puffer, ziel_repo)


def schreibe(typ: str | None = None, /, **felder: Any) -> dict[str, Any]:
    """Einzige Schreib-Tür für das Ereignis-Protokoll: ``schreibe(typ, **felder)`` (#902, #903).

    Prüft alle Pflichtfelder (``zeit``, ``ticket``, ``wer``, ``was``, ``session``,
    ``commit``, ``grund``, ``kontextstand``), sperrt lokal über :mod:`to_spawn.sperre`,
    puffert unter ``~/.local/state/to-spawn/protokoll`` (nie im Worktree) und schiebt
    gebündelt per Git-Plumbing auf ``protokoll`` (mit Rebase/Retry bei Konflikt und
    Offline-Puffer ohne Fehler an den Aufrufer).
    """
    if typ is None:
        if "typ" in felder:
            typ_wert = felder.pop("typ")
        else:
            raise TypeError("schreibe() erwartet schreibe(typ, **felder).")
    else:
        typ_wert = typ

    repo_vorgabe = felder.pop("repo", None) if isinstance(felder.get("repo"), (Path, os.PathLike)) else None
    puffer_vorgabe = felder.pop("puffer_ordner", None)
    sofort_push = felder.pop("sofort_push", None)
    idem_roh = felder.pop("idempotenz_schluessel", None)
    idem_schluessel = idem_roh.strip() if isinstance(idem_roh, str) else ""
    if idem_schluessel:
        felder["idempotenz_schluessel"] = idem_schluessel

    zeile = _pruefe_pflichtfelder(typ_wert, felder)
    puffer = Path(puffer_vorgabe).expanduser() if puffer_vorgabe is not None else protokoll_puffer_ordner()
    ziel_repo = _protokoll_repo(Path(repo_vorgabe) if repo_vorgabe is not None else None)
    _pruefe_puffer_ausserhalb(puffer, ziel_repo)

    if idem_schluessel and ziel_repo is not None:
        protokoll_holen(ziel_repo)
        vorhanden = _hat_idempotenz_schluessel(
            _protokoll_rohzeilen_vom_ref(ziel_repo, zeile["ticket"]), idem_schluessel
        )
        if vorhanden is not None:
            return vorhanden

    roh = json.dumps(zeile, ensure_ascii=False)
    puffer_sperre = puffer / "puffer.lock"
    ticket_datei = puffer / f"{zeile['ticket']}.jsonl"
    doppelt: dict[str, Any] | None = None
    with sperre.halte(puffer_sperre, warten=SPERR_WARTE_S) as gehalten:
        if not gehalten:
            raise TimeoutError(f"Ereignis-Protokoll: Puffer-Sperre {puffer_sperre} nach {SPERR_WARTE_S:.1f} s belegt.")
        if idem_schluessel:
            doppelt = _hat_idempotenz_schluessel(rohzeilen(ticket_datei), idem_schluessel)
        if doppelt is None:
            _haenge_an(ticket_datei, [roh])
        darf_pushen = bool(sofort_push) if sofort_push is not None else _buendel_faellig(puffer, _buendel_intervall_s())
    if darf_pushen:
        _schiebe_puffer(puffer, ziel_repo)
    return doppelt if doppelt is not None else zeile


def hindernis_gemeldet(**felder: Any) -> dict[str, Any]:
    """Schreib-Tür für den Ereignis-Typ ``hindernis_gemeldet`` (#904 / App #1399).

    Leitet alle Felder unverändert durch :func:`schreibe` (inkl. Pflichtfeld-Prüfung,
    Puffer, Sperre und Git-Plumbing-Push auf ``protokoll``).
    """
    return schreibe(HINDERNIS_GEMELDET, **felder)


def auslastung_schreiben(
    repo: Path | None = None,
    *,
    platte_frei_prozent: float | None,
    speicher_frei_mib: int | None,
    sessions: int | None,
    sessions_gesamt: int | None = None,
    speicher_gesamt_mib: int | None = None,
    wer: str = "aufpasser",
    grund: str = "15-Minuten-Takt",
    zeit: str | None = None,
    idempotenz_schluessel: str | None = None,
    puffer_ordner: Path | None = None,
    sofort_push: bool | None = None,
) -> dict[str, Any]:
    """Schreibt eine ticketfreie Auslastungszeile über :func:`schreibe` (#904 / App #1399).

    Wird unter ``bau_log/auslastung.jsonl`` auf ``origin/protokoll`` abgelegt und über
    :func:`lese` (``ticket=AUSLASTUNG_TICKET``) oder :func:`lese_auslastung` gelesen.
    """
    platte_text = f"{_komma(platte_frei_prozent)} %" if platte_frei_prozent is not None else "unbekannt"
    ram_text = f"{speicher_frei_mib} MiB" if speicher_frei_mib is not None else "unbekannt"
    sess_text = str(sessions) if sessions is not None else "unbekannt"
    felder: dict[str, Any] = {
        "ticket": AUSLASTUNG_TICKET,
        "wer": wer,
        "was": f"Platte {platte_text} frei, RAM {ram_text} frei, {sess_text} Sessions aktiv",
        "session": wer,
        "commit": "-",
        "grund": grund,
        "kontextstand": 0,
        "platte_frei_prozent": platte_frei_prozent,
        "speicher_frei_mib": speicher_frei_mib,
        "speicher_gesamt_mib": speicher_gesamt_mib,
        "sessions": sessions,
        "sessions_gesamt": sessions_gesamt,
    }
    if repo is not None:
        felder["repo"] = repo
    if zeit is not None:
        felder["zeit"] = zeit
    if idempotenz_schluessel is not None:
        felder["idempotenz_schluessel"] = idempotenz_schluessel
    if puffer_ordner is not None:
        felder["puffer_ordner"] = puffer_ordner
    if sofort_push is not None:
        felder["sofort_push"] = sofort_push
    return schreibe(AUSLASTUNG_TYP, **felder)


def lese_auslastung(
    repo: Path,
    *,
    holen: bool = True,
    puffer_ordner: Path | None = None,
) -> list[dict[str, Any]]:
    """Liest alle Auslastungszeilen aus ``origin/protokoll`` und lokalem Puffer (#904)."""
    return [
        z
        for z in lese(repo, AUSLASTUNG_TICKET, holen=holen, puffer_ordner=puffer_ordner)
        if z.get("typ") == AUSLASTUNG_TYP
    ]


def monats_datei_im_zweig(monat: str) -> str:
    """Pfad einer Monats-Zusammenfassung auf dem Zweig ``protokoll`` (``YYYY-MM``)."""
    return f"{PROTOKOLL_MONATE_ORDNER}/{monat}.jsonl"


def monats_zusammenfassungen(
    repo: Path,
    *,
    monat: str | None = None,
    holen: bool = True,
) -> list[dict[str, Any]]:
    """Liest verdichtete Monats-Zusammenfassungen aus ``origin/protokoll`` (#904)."""
    if not repo.is_dir():
        return []
    if holen:
        protokoll_holen(repo)
    if monat is not None:
        monat_text = monat.strip()
        if not _MONAT_MUSTER.fullmatch(monat_text):
            return []
        pfade = [monats_datei_im_zweig(monat_text)]
    else:
        code, ausgabe = _git(repo, "ls-tree", "--name-only", PROTOKOLL_REMOTE_REF, f"{PROTOKOLL_MONATE_ORDNER}/")
        if code != 0 or not ausgabe:
            return []
        pfade = sorted(p.strip() for p in ausgabe.splitlines() if p.strip().endswith(".jsonl"))
    ergebnis: list[dict[str, Any]] = []
    for pfad in pfade:
        code, text = _git(repo, "show", f"{PROTOKOLL_REMOTE_REF}:{pfad}")
        if code != 0 or not text:
            continue
        for _, eintrag in _parse_rohzeilen([z.strip() for z in text.splitlines() if z.strip()], pfad):
            ergebnis.append(eintrag)
    return sorted(ergebnis, key=lambda e: str(e.get("monat") or ""))


@dataclass(frozen=True)
class PflegeErgebnis:
    """Ergebnis eines idempotenten Pflege-Laufs (:func:`pflegen`, #904 / App #1399)."""

    ok: bool
    geaendert: bool
    verdichtet: int
    monate: tuple[str, ...]
    dateien_aktualisiert: int
    dateien_entfernt: int
    fehler: str = ""


def _zahl_oder_none(wert: Any) -> float | None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return float(wert)


def _monat_zusammenfuehren(
    monat: str,
    bestehend: dict[str, Any] | None,
    neue_eintraege: list[dict[str, Any]],
    bezugszeit: datetime,
) -> dict[str, Any]:
    """Führt eine bestehende Monats-Zusammenfassung und neu verdichtete Alt-Zeilen zusammen."""
    nach_typ: Counter[str] = Counter()
    tickets: set[str] = set()
    von_dt: datetime | None = None
    bis_dt: datetime | None = None
    zeilen_gesamt = 0

    ausl_messungen = 0
    platte_min: float | None = None
    speicher_min: int | None = None
    sessions_max: int | None = None
    sessions_gesamt_max: int | None = None

    if isinstance(bestehend, dict):
        alt_gesamt = bestehend.get("zeilen_gesamt")
        if isinstance(alt_gesamt, int) and not isinstance(alt_gesamt, bool):
            zeilen_gesamt = alt_gesamt
        alt_typen = bestehend.get("nach_typ")
        if isinstance(alt_typen, dict):
            for k, v in alt_typen.items():
                if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool):
                    nach_typ[k] += v
        alt_tickets = bestehend.get("tickets")
        if isinstance(alt_tickets, list):
            tickets.update(str(t) for t in alt_tickets if str(t).isdigit())
        von_dt = _zeitpunkt(bestehend.get("von"))
        bis_dt = _zeitpunkt(bestehend.get("bis"))
        alt_ausl = bestehend.get("auslastung")
        if isinstance(alt_ausl, dict):
            if isinstance(alt_ausl.get("messungen"), int) and not isinstance(alt_ausl.get("messungen"), bool):
                ausl_messungen = alt_ausl["messungen"]
            platte_min = _zahl_oder_none(alt_ausl.get("platte_frei_prozent_min"))
            sp = _zahl_oder_none(alt_ausl.get("speicher_frei_mib_min"))
            speicher_min = int(sp) if sp is not None else None
            se = _zahl_oder_none(alt_ausl.get("sessions_max"))
            sessions_max = int(se) if se is not None else None
            sg = _zahl_oder_none(alt_ausl.get("sessions_gesamt_max"))
            sessions_gesamt_max = int(sg) if sg is not None else None

    for eintrag in neue_eintraege:
        zeilen_gesamt += 1
        typ = str(eintrag.get("typ") or "unbekannt")
        nach_typ[typ] += 1
        ticket_wert = str(eintrag.get("ticket") or "").strip()
        if ticket_wert.isdigit():
            tickets.add(ticket_wert)
        zeit = _zeitpunkt(eintrag.get("zeit") or eintrag.get("ts"))
        if zeit is not None:
            if von_dt is None or zeit < von_dt:
                von_dt = zeit
            if bis_dt is None or zeit > bis_dt:
                bis_dt = zeit
        if typ == AUSLASTUNG_TYP:
            ausl_messungen += 1
            pl = _zahl_oder_none(eintrag.get("platte_frei_prozent"))
            if pl is not None:
                platte_min = pl if platte_min is None else min(platte_min, pl)
            sp = _zahl_oder_none(eintrag.get("speicher_frei_mib"))
            if sp is not None:
                sp_int = int(sp)
                speicher_min = sp_int if speicher_min is None else min(speicher_min, sp_int)
            se = _zahl_oder_none(eintrag.get("sessions"))
            if se is not None:
                se_int = int(se)
                sessions_max = se_int if sessions_max is None else max(sessions_max, se_int)
            sg = _zahl_oder_none(eintrag.get("sessions_gesamt"))
            if sg is not None:
                sg_int = int(sg)
                sessions_gesamt_max = sg_int if sessions_gesamt_max is None else max(sessions_gesamt_max, sg_int)

    ergebnis: dict[str, Any] = {
        "typ": MONATS_ZUSAMMENFASSUNG,
        "monat": monat,
        "zeit": bezugszeit.astimezone(UTC).isoformat(timespec="seconds"),
        "zeilen_gesamt": zeilen_gesamt,
        "nach_typ": dict(sorted(nach_typ.items())),
        "tickets": sorted(tickets, key=int),
        "von": von_dt.astimezone(UTC).isoformat(timespec="seconds") if von_dt is not None else None,
        "bis": bis_dt.astimezone(UTC).isoformat(timespec="seconds") if bis_dt is not None else None,
    }
    if ausl_messungen > 0:
        ergebnis["auslastung"] = {
            "messungen": ausl_messungen,
            "platte_frei_prozent_min": platte_min,
            "speicher_frei_mib_min": speicher_min,
            "sessions_max": sessions_max,
            "sessions_gesamt_max": sessions_gesamt_max,
        }
    return ergebnis


def _plumbing_pflegen_einmal(
    repo: Path,
    grenze: datetime,
    bezugszeit: datetime,
) -> tuple[PflegeErgebnis, bool]:
    """Ein atomarer Git-Plumbing-Pflege-Durchlauf auf ``refs/heads/protokoll`` (#904).

    Rückgabe ``(ergebnis, konflikt)``; berührt weder Arbeitsbaum noch Repo-Index.
    """
    from . import gh

    nutzbar, _ist_netz = _origin_darf_netz(repo)
    if not nutzbar:
        return PflegeErgebnis(False, False, 0, (), 0, 0, "kein erreichbares origin konfiguriert"), False

    ok_ref, remote_sha, fehler_ref = gh.remote_ref_sha(repo, PROTOKOLL_REF)
    if not ok_ref:
        return PflegeErgebnis(False, False, 0, (), 0, 0, fehler_ref), False
    if not remote_sha:
        return PflegeErgebnis(True, False, 0, (), 0, 0), False

    temp_ref = f"refs/to-spawn/tmp/pflege-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}"
    try:
        c_fetch, s_fetch, e_fetch = gh._git_lauf(
            ["fetch", "-q", "--no-tags", "origin", f"+{PROTOKOLL_REF}:{temp_ref}"],
            repo,
            timeout=gh.FETCH_ZEIT_S,
        )
        if c_fetch != 0:
            return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_fetch, s_fetch, e_fetch)), False
        c_rev, s_rev, e_rev = gh._git_lauf(
            ["rev-parse", "--verify", f"{temp_ref}^{{commit}}"],
            repo,
            timeout=gh.GIT_ZEIT_S,
        )
        parent_sha = s_rev.strip()
        if c_rev != 0 or not gh._GIT_SHA.fullmatch(parent_sha):
            return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_rev, s_rev, e_rev)), False

        c_ls, s_ls, e_ls = gh._git_lauf(
            ["ls-tree", "--name-only", parent_sha, f"{PROTOKOLL_BAUM_ORDNER}/"],
            repo,
        )
        if c_ls != 0:
            return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_ls, s_ls, e_ls)), False

        kandidaten = sorted(p.strip() for p in s_ls.splitlines() if p.strip().endswith(".jsonl"))
        monate_neu: dict[str, list[dict[str, Any]]] = {}
        datei_rest: dict[str, list[str]] = {}
        verdichtet = 0

        for baum_pfad in kandidaten:
            c_cat, s_cat, _e_cat = gh._git_lauf(["cat-file", "-p", f"{parent_sha}:{baum_pfad}"], repo)
            if c_cat != 0 or not s_cat:
                continue
            roh_zeilen = [z.strip() for z in s_cat.splitlines() if z.strip()]
            jung: list[str] = []
            alt_anzahl = 0
            for roh in roh_zeilen:
                try:
                    eintrag = json.loads(roh)
                except ValueError:
                    jung.append(roh)
                    continue
                if not isinstance(eintrag, dict):
                    jung.append(roh)
                    continue
                zeit = _zeitpunkt(eintrag.get("zeit") or eintrag.get("ts"))
                if zeit is not None and zeit < grenze:
                    monat = zeit.astimezone(UTC).strftime("%Y-%m")
                    monate_neu.setdefault(monat, []).append(eintrag)
                    alt_anzahl += 1
                else:
                    jung.append(roh)
            if alt_anzahl > 0:
                verdichtet += alt_anzahl
                datei_rest[baum_pfad] = jung

        if verdichtet == 0:
            return PflegeErgebnis(True, False, 0, (), 0, 0), False

        aktualisiert = 0
        entfernt = 0
        with tempfile.TemporaryDirectory(prefix="to-spawn-pflege-") as tmp_dir:
            tmp_pfad = Path(tmp_dir)
            index_env = {"GIT_INDEX_FILE": str(tmp_pfad / "index")}
            c_rt, s_rt, e_rt = gh._git_lauf(["read-tree", parent_sha], repo, extra_env=index_env)
            if c_rt != 0:
                return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_rt, s_rt, e_rt)), False

            for idx, (baum_pfad, jung) in enumerate(sorted(datei_rest.items())):
                if not jung:
                    c_rm, s_rm, e_rm = gh._git_lauf(
                        ["update-index", "--force-remove", "--", baum_pfad],
                        repo,
                        extra_env=index_env,
                    )
                    if c_rm != 0:
                        return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_rm, s_rm, e_rm)), False
                    entfernt += 1
                    continue
                blob_datei = tmp_pfad / f"rest-{idx}.jsonl"
                blob_datei.write_text("\n".join(_sortiere_rohzeilen(jung)) + "\n", encoding="utf-8", newline="\n")
                c_hash, s_hash, e_hash = gh._git_lauf(["hash-object", "-w", "--", str(blob_datei)], repo)
                blob_sha = s_hash.strip()
                if c_hash != 0 or not gh._GIT_SHA.fullmatch(blob_sha):
                    return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_hash, s_hash, e_hash)), False
                c_upd, s_upd, e_upd = gh._git_lauf(
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{baum_pfad}"],
                    repo,
                    extra_env=index_env,
                )
                if c_upd != 0:
                    return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_upd, s_upd, e_upd)), False
                aktualisiert += 1

            monate_sortiert = tuple(sorted(monate_neu))
            for monat in monate_sortiert:
                monats_pfad = monats_datei_im_zweig(monat)
                bestehend: dict[str, Any] | None = None
                c_mcat, s_mcat, _e_mcat = gh._git_lauf(["cat-file", "-p", f"{parent_sha}:{monats_pfad}"], repo)
                if c_mcat == 0 and s_mcat.strip():
                    for roh in s_mcat.splitlines():
                        roh = roh.strip()
                        if not roh:
                            continue
                        try:
                            geladen = json.loads(roh)
                        except ValueError:
                            continue
                        if isinstance(geladen, dict):
                            bestehend = geladen
                            break
                zusammen = _monat_zusammenfuehren(monat, bestehend, monate_neu[monat], bezugszeit)
                blob_datei = tmp_pfad / f"monat-{monat}.jsonl"
                blob_datei.write_text(json.dumps(zusammen, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
                c_hash, s_hash, e_hash = gh._git_lauf(["hash-object", "-w", "--", str(blob_datei)], repo)
                blob_sha = s_hash.strip()
                if c_hash != 0 or not gh._GIT_SHA.fullmatch(blob_sha):
                    return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_hash, s_hash, e_hash)), False
                c_upd, s_upd, e_upd = gh._git_lauf(
                    ["update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{monats_pfad}"],
                    repo,
                    extra_env=index_env,
                )
                if c_upd != 0:
                    return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_upd, s_upd, e_upd)), False

            c_tree, s_tree, e_tree = gh._git_lauf(["write-tree"], repo, extra_env=index_env)
            tree_sha = s_tree.strip()
            if c_tree != 0 or not gh._GIT_SHA.fullmatch(tree_sha):
                return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_tree, s_tree, e_tree)), False

        monate_kurz = ", ".join(monate_sortiert[:5])
        if len(monate_sortiert) > 5:
            monate_kurz += f" +{len(monate_sortiert) - 5}"
        nachricht = f"protokoll: Pflege {verdichtet} Alt-Zeile(n) verdichtet ({monate_kurz})\n"
        c_com, s_com, e_com = gh._git_lauf(
            ["-c", "commit.gpgsign=false", "commit-tree", tree_sha, "-p", parent_sha, "-F", "-"],
            repo,
            eingabe=nachricht,
            extra_env=gh._INTERN_GIT_ENV,
        )
        commit_sha = s_com.strip()
        if c_com != 0 or not gh._GIT_SHA.fullmatch(commit_sha):
            return PflegeErgebnis(False, False, 0, (), 0, 0, gh._git_fehler_kurz(c_com, s_com, e_com)), False

        ok_push, konflikt, fehler_push = gh.remote_ref_cas_setzen(repo, PROTOKOLL_REF, commit_sha, parent_sha)
        if ok_push:
            gh._git_lauf(["update-ref", PROTOKOLL_REMOTE_REF, commit_sha], repo)
            return PflegeErgebnis(True, True, verdichtet, monate_sortiert, aktualisiert, entfernt), False
        return PflegeErgebnis(False, False, 0, (), 0, 0, fehler_push), konflikt
    finally:
        gh._git_lauf(["update-ref", "-d", temp_ref], repo)


def pflegen(
    repo: Path | None = None,
    *,
    tage: int = PFLEGE_ALTER_TAGE,
    jetzt: datetime | None = None,
    puffer_ordner: Path | None = None,
) -> PflegeErgebnis:
    """Idempotenter Pflege-Lauf für das Ereignis-Protokoll (#904 / App #1399).

    Verdichtet alle Zeilen älter als ``tage`` Tage (Vorgabe 90) aus ``bau_log/*.jsonl``
    auf ``origin/protokoll`` in Monats-Zusammenfassungen unter
    ``bau_log/monate/<YYYY-MM>.jsonl``. Ein erneuter Lauf ohne neue Alt-Zeilen erzeugt
    keinen Commit. Arbeitet per Git-Plumbing unter ``protokoll.lock`` ohne Eingriff in
    Arbeitsbaum oder Index.
    """
    if tage <= 0:
        raise ValueError(f"tage muss positiv sein, erhalten: {tage}")
    ziel_repo = _protokoll_repo(repo)
    if ziel_repo is None:
        return PflegeErgebnis(False, False, 0, (), 0, 0, "kein Repo gefunden")
    puffer = Path(puffer_ordner).expanduser() if puffer_ordner is not None else protokoll_puffer_ordner()
    _pruefe_puffer_ausserhalb(puffer, ziel_repo)

    bezugszeit = jetzt if jetzt is not None else datetime.now(UTC)
    if bezugszeit.tzinfo is None:
        bezugszeit = bezugszeit.replace(tzinfo=UTC)
    grenze = bezugszeit - timedelta(days=tage)

    push_sperre = puffer / "protokoll.lock"
    with sperre.halte(push_sperre, warten=SPERR_WARTE_S) as gehalten:
        if not gehalten:
            return PflegeErgebnis(False, False, 0, (), 0, 0, f"Push-Sperre {push_sperre} belegt")
        letztes = PflegeErgebnis(False, False, 0, (), 0, 0, "Pflege nicht ausgeführt")
        for versuch in range(1, MAX_PUSH_VERSUCHE + 1):
            erg, konflikt = _plumbing_pflegen_einmal(ziel_repo, grenze, bezugszeit)
            if erg.ok:
                return erg
            letztes = erg
            if not konflikt:
                log.warning("Ereignis-Protokoll-Pflege fehlgeschlagen: %s", erg.fehler)
                return erg
            if versuch < MAX_PUSH_VERSUCHE:
                time.sleep(min(0.03 * versuch, 0.25) + random.uniform(0.0, 0.05))
        log.warning("Ereignis-Protokoll-Pflege nach %d Konflikten offen: %s", MAX_PUSH_VERSUCHE, letztes.fehler)
        return letztes


def _parse_rohzeilen(roh_liste: Iterable[str], quelle: str) -> list[tuple[str, dict[str, Any]]]:
    paare: list[tuple[str, dict[str, Any]]] = []
    for roh in roh_liste:
        try:
            eintrag = json.loads(roh)
        except ValueError:
            log.warning("Kaputte Log-Zeile in %s übersprungen.", quelle)
            continue
        if isinstance(eintrag, dict):
            paare.append((roh, eintrag))
    return paare


def _lese_datei(datei: Path) -> list[tuple[str, dict[str, Any]]]:
    return _parse_rohzeilen(rohzeilen(datei), str(datei))


def zeitstempel(zeile: dict[str, Any]) -> Any:
    """Zeitstempel einer Bau-Log-/Protokoll-Zeile (``zeit`` oder historisch ``ts``)."""
    return zeile.get("zeit") or zeile.get("ts")


def _sortier_zeit(zeile: dict[str, Any]) -> datetime:
    return _zeitpunkt(zeitstempel(zeile)) or datetime.min.replace(tzinfo=UTC)


def lese(
    repo: Path,
    ticket: str | int,
    *,
    hauptbaum: Path | None = None,
    holen: bool = True,
    puffer_ordner: Path | None = None,
) -> list[dict[str, Any]]:
    """Alle Zeilen eines Tickets: ``origin/protokoll`` ∪ Puffer ∪ Altbestand, nach ``zeit``/``ts`` sortiert (#903).

    Liest primär aus ``origin/protokoll`` (``bau_log/<Ticket>.jsonl``) und noch nicht
    gepushten Puffer-Zeilen. Historische lokale Dateien (versioniert und Laufdatei) werden
    für Übergang und Alttests zusammengeführt; doppelte Zeilen und doppelte
    ``idempotenz_schluessel`` zählen einmal.
    """
    protokoll_roh: list[str] = []
    if repo.is_dir():
        if holen:
            protokoll_holen(repo)
        protokoll_roh = _protokoll_rohzeilen_vom_ref(repo, ticket)
    if (
        not protokoll_roh
        and hauptbaum is not None
        and hauptbaum.is_dir()
        and (not repo.is_dir() or hauptbaum.resolve() != repo.resolve())
    ):
        if holen:
            protokoll_holen(hauptbaum)
        protokoll_roh = _protokoll_rohzeilen_vom_ref(hauptbaum, ticket)
    puffer_roh = _puffer_rohzeilen(ticket, puffer_ordner)

    basis_roh = [*protokoll_roh, *_fehlende_mit_idempotenz(protokoll_roh, puffer_roh)]
    fest = _lese_datei(log_pfad(repo, ticket))
    lauf = _lese_datei(lauf_pfad(repo, ticket))
    if hauptbaum is not None and hauptbaum.resolve() != repo.resolve():
        lauf += _lese_datei(lauf_pfad(hauptbaum, ticket))

    fest_roh = [roh for roh, _ in fest]
    lauf_roh = [roh for roh, _ in lauf]
    lokal_roh = [*fest_roh, *_fehlende(fest_roh, lauf_roh)]
    ergaenzt_roh = _fehlende_mit_idempotenz(basis_roh, lokal_roh)
    paare = _parse_rohzeilen([*basis_roh, *ergaenzt_roh], f"bau_log #{ticket}")
    return sorted((eintrag for _, eintrag in paare), key=_sortier_zeit)


def alle_tickets(repo: Path) -> list[str]:
    nummern: set[str] = set(protokoll_tickets(repo))
    for ordner in (log_ordner(repo), repo / LAUF_ORDNER):
        if ordner.is_dir():
            nummern.update(p.stem for p in ordner.glob("*.jsonl") if p.stem.isdigit())
    return sorted(nummern, key=int)


def _summe(zeilen: Iterable[dict[str, Any]], feld: str = "gesamt") -> int:
    gesamt = 0
    for zeile in zeilen:
        tokens = zeile.get("tokens") or {}
        if isinstance(tokens, dict):
            wert = tokens.get(feld)
            if isinstance(wert, (int, float)):
                gesamt += int(wert)
    return gesamt


def _spitzen(zeilen: Iterable[dict[str, Any]]) -> list[int]:
    """Spitzen-Kontext je Zeile (#238); Zeilen ohne ``kontext`` (vor #238) fehlen.

    Deren ``tokens.gesamt`` ist die Summe des Cache-Lesens über alle Aufrufe und
    keine Kontextgröße — sie wird nie als Ersatz genommen (``umrechnen`` holt den
    Wert aus dem Transkript nach).
    """
    werte: list[int] = []
    for zeile in zeilen:
        kontext = zeile.get("kontext")
        if isinstance(kontext, dict):
            wert = kontext.get("spitze")
            if isinstance(wert, (int, float)) and not isinstance(wert, bool):
                werte.append(int(wert))
    return werte


def _aufrufe(zeilen: Iterable[dict[str, Any]]) -> int:
    gesamt = 0
    for zeile in zeilen:
        kontext = zeile.get("kontext")
        if isinstance(kontext, dict):
            wert = kontext.get("aufrufe")
            if isinstance(wert, int) and not isinstance(wert, bool):
                gesamt += wert
    return gesamt


def _k(wert: int | None) -> float | None:
    return round(wert / 1000, 1) if wert else None


def _juengste_je_session(zeilen: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nur die jüngste Zeile je ``session_id`` (Hooks schreiben kumulierte Summen).

    Claude Code feuert den Stop-Hook am Ende jeder Runde; jede Zeile trägt die
    Summe des ganzen Transkripts bis dahin. Zeilen ohne ``session_id`` (ältere
    Logs) zählen einzeln.
    """
    je_id: dict[str, dict[str, Any]] = {}
    ohne_id: list[dict[str, Any]] = []
    for zeile in zeilen:
        kennung = zeile.get("session_id")
        if kennung:
            je_id[str(kennung)] = zeile  # spätere Zeile überschreibt frühere
        else:
            ohne_id.append(zeile)
    return [*je_id.values(), *ohne_id]


def _kontextstand_k(wert: Any) -> float | None:
    """Kontextstand eines Ereignis-Protokoll-Eintrags in Tausend Token (k), ``None`` ohne Wert."""
    if wert is None or isinstance(wert, bool):
        return None
    zahl: float
    if isinstance(wert, (int, float)):
        zahl = float(wert)
    elif isinstance(wert, str):
        sauber = wert.strip().rstrip("kK").replace(",", ".").strip()
        if not sauber:
            return None
        try:
            zahl = float(sauber)
        except ValueError:
            return None
    else:
        return None
    if zahl <= 0:
        return None
    return round(zahl / 1000, 1) if zahl > 2000 else round(zahl, 1)


def kontext_aktuell_k(
    repo: Path,
    ticket: str | int,
    *,
    hauptbaum: Path | None = None,
    nach: float | None = None,
) -> float | None:
    """Spitzen-Kontext (k) der aktuellen Hauptsession — für die Eingriffs-Leiter (#432, #903).

    Zählt primär die jüngste ``session_ende``-Zeile (kumulierte Hook-Werte); liegen nur
    Ereignis-Protokoll-Zeilen vor, gilt der jüngste positive ``kontextstand``. ``nach``
    (Unix-Zeit, z. B. der letzte Respawn) blendet ältere Zeilen aus.
    """
    alle = lese(repo, ticket, hauptbaum=hauptbaum)
    enden = [
        z for z in alle if z.get("typ") == "session_ende" and (nach is None or _sortier_zeit(z).timestamp() > nach)
    ]
    spitzen = _spitzen(enden[-1:])
    if spitzen:
        return _k(spitzen[0])
    if enden:
        return None
    proto_werte = [
        k
        for z in alle
        if (nach is None or _sortier_zeit(z).timestamp() > nach)
        and (k := _kontextstand_k(z.get("kontextstand"))) is not None
    ]
    return proto_werte[-1] if proto_werte else None


def letzter_session_start(repo: Path, ticket: str | int, *, hauptbaum: Path | None = None) -> float | None:
    """Unix-Zeit der jüngsten ``session_start``- oder ``start``-Zeile — „Ticket beginnt neu“ (#432, #903).

    Zählt Starter-, Hook- und Protokoll-Zeilen gleich. ``None`` = keine Zeile mit lesbarer Zeit.
    """
    zeiten = [
        zeitpunkt.timestamp()
        for z in lese(repo, ticket, hauptbaum=hauptbaum)
        if z.get("typ") in ("session_start", "start")
        and (zeitpunkt := _zeitpunkt(z.get("zeit") or z.get("ts"))) is not None
    ]
    return max(zeiten) if zeiten else None


def _dauer(zeile: dict[str, Any]) -> int:
    try:
        return int(float(zeile.get("dauer_s") or 0))
    except (TypeError, ValueError):
        return 0


def zusammenfassung(repo: Path, ticket: str | int, *, hauptbaum: Path | None = None) -> dict[str, Any]:
    """Kennzahlen eines Tickets für Tabelle und Lernstoff (``hauptbaum`` wie bei :func:`lese`)."""
    zeilen = lese(repo, ticket, hauptbaum=hauptbaum)
    enden = _juengste_je_session(z for z in zeilen if z.get("typ") == "session_ende")
    subs = _juengste_je_session(z for z in zeilen if z.get("typ") == "subagent_ende")
    starts = [z for z in zeilen if z.get("typ") in ("session_start", "start")]
    staffeln = [int(z.get("staffel") or 1) for z in zeilen if z.get("staffel") is not None]
    auftrag = next((z for z in zeilen if z.get("typ") == "auftrag"), {})
    dauer = sum(_dauer(z) for z in enden)
    kennungen = {
        str(kennung)
        for z in zeilen
        if (
            (z.get("typ") in ("session_start", "session_ende") and (kennung := z.get("session_id")))
            or (
                z.get("typ") in EREIGNIS_TYPEN
                and (kennung := z.get("session"))
                and str(kennung).strip() not in ("", "-")
            )
        )
    }
    enden_ohne_id = sum(1 for z in enden if not z.get("session_id"))
    starts_ohne_id = sum(
        1 for z in starts if not z.get("session_id") and str(z.get("session") or "").strip() in ("", "-")
    )
    # Der Starter (bau_loop) schreibt session_start ohne Kennung, die Hooks mit —
    # das Maximum zählt dieselbe Session nicht doppelt.
    sessions = max(len(kennungen) + enden_ohne_id, starts_ohne_id)
    klartext = next((z for z in reversed(zeilen) if z.get("typ") == "zusammenfassung"), {})
    haupt_spitzen = _spitzen(enden)
    sub_spitzen = _spitzen(subs)
    vollstaendig = bool(haupt_spitzen) and len(haupt_spitzen) == len(enden)
    if vollstaendig:
        ist_k = _k(sum(haupt_spitzen))
        spitze_k = _k(max(haupt_spitzen))
    elif not enden:
        proto_spitzen = [k for z in zeilen if (k := _kontextstand_k(z.get("kontextstand"))) is not None]
        spitze_k = max(proto_spitzen) if proto_spitzen else None
        ist_k = spitze_k
    else:
        ist_k = None
        spitze_k = None
    return {
        "ticket": str(ticket),
        "schaetzung_k": auftrag.get("schaetzung_k"),
        "umfang": auftrag.get("umfang") or auftrag.get("text"),
        "title": auftrag.get("title"),
        "sessions": sessions,
        "staffel": max(staffeln) if staffeln else sessions,
        "subagenten": len(subs),
        # Spitzen-Kontext der Hauptsession (#238); bei mehreren Staffeln die Summe
        # der Spitzen je Session — das ist der Bedarf, den ``schaetzung_k`` schätzt.
        # Fehlt einer Session der Wert (Log vor #238, nicht umgerechnet), gibt es
        # kein ``ist_k`` — ein Teilwert würde die Lernschleife zu klein lernen lassen.
        "ist_k": ist_k,
        "spitze_k": spitze_k,
        "sub_spitze_k": _k(max(sub_spitzen)) if sub_spitzen else None,
        "aufrufe": _aufrufe(enden),
        "output_k": round(_summe(enden, "output") / 1000, 1),
        # Verbrauch inkl. Cache-Lesen über alle Aufrufe — nie als Kontext anzeigen.
        "verbrauch_k": round((_summe(enden) + _summe(subs)) / 1000, 1),
        "dauer_s": dauer,
        "handoffs": sum(1 for z in zeilen if z.get("typ") in ("handoff", "uebergabe")),
        "entscheidungen": sum(1 for z in zeilen if z.get("typ") == "entscheidung"),
        # Klartext der jüngsten ``zusammenfassung``-Zeile (Fixrunde #204).
        "umfang_ist": klartext.get("umfang"),
        "schwierigkeiten": klartext.get("schwierigkeiten"),
        "entscheidungen_text": klartext.get("entscheidungen"),
    }


def tabelle(repo: Path, tickets: Iterable[str | int]) -> str:
    """Gesamt-Tabelle als Klartext (feste Spaltenbreiten, ohne Fremd-Bibliothek)."""
    # „Ist k“ = Spitzen-Kontext (#238); Verbrauch inkl. Cache-Lesen steht getrennt.
    kopf = (
        "Ticket",
        "Schätz.k",
        "Ist k",
        "Sess.",
        "Staffel",
        "Subag.",
        "Dauer",
        "Entsch.",
        "Sub-Spitze k",
        "Aufrufe",
        "Verbrauch k (Cache inkl.)",
    )
    reihen: list[tuple[str, ...]] = []
    for ticket in tickets:
        z = zusammenfassung(repo, ticket)
        schaetzung = z["schaetzung_k"]
        reihen.append(
            (
                f"#{z['ticket']}",
                f"{schaetzung:g}" if ist_schaetzung(schaetzung) else "—",
                f"{z['ist_k']:g}" if z["ist_k"] else "—",
                str(z["sessions"]),
                str(z["staffel"]),
                str(z["subagenten"]),
                f"{z['dauer_s'] // 60} min" if z["dauer_s"] else "—",
                str(z["entscheidungen"]),
                f"{z['sub_spitze_k']:g}" if z["sub_spitze_k"] else "—",
                str(z["aufrufe"]) if z["aufrufe"] else "—",
                f"{z['verbrauch_k']:g}" if z["verbrauch_k"] else "—",
            )
        )
    if not reihen:
        return "Bau-Log ist leer (noch kein Lauf)."
    breiten = [max(len(kopf[i]), *(len(r[i]) for r in reihen)) for i in range(len(kopf))]
    linien = [
        "  ".join(kopf[i].ljust(breiten[i]) for i in range(len(kopf))),
        "  ".join("-" * breiten[i] for i in range(len(kopf))),
    ]
    linien += ["  ".join(r[i].ljust(breiten[i]) for i in range(len(kopf))) for r in reihen]
    return "\n".join(linien)


#: Umfang-Arten für die Faustregel „Sessions je Art“ (Kleinschrift). Wortstamm am
#: Wortanfang: „Skripte“, „Tests“, „Tabellen“ zählen mit; kurze Kürzel wie „ui“,
#: „css“, „sql“, „cli“, „api“ nur als ganzes Wort.
UMFANG_ARTEN: dict[str, re.Pattern[str]] = {
    "Datenbank": re.compile(r"\b(datenbank\w*|tabelle\w*|migration\w*|sql|spalte\w*)\b"),
    "Oberfläche": re.compile(
        r"\b(oberfläche\w*|ui|seite\w*|template\w*|css|knopf\w*|knöpf\w*|button\w*"
        r"|dialog\w*|ansicht\w*)\b"
    ),
    "Hook": re.compile(r"\bhook\w*"),
    "Skript": re.compile(r"\b(skript\w*|script\w*|cli)\b"),
    "Doku": re.compile(r"\b(doku\w*|docs|readme\w*)\b"),
    "Deploy": re.compile(r"\b(deploy\w*|gate\w*|server\w*)\b"),
    "Test": re.compile(r"\b(test\w*|beweis\w*)\b"),
    "API": re.compile(r"\b(api|endpoint\w*|route\w*)\b"),
}


def ist_schaetzung(wert: Any) -> bool:
    """Echte Schätzung = Zahl über 0; ``True``/``False`` zählen nie."""
    return isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert > 0


def _komma(wert: float) -> str:
    """Eine Nachkommastelle mit deutschem Dezimalkomma."""
    return f"{wert:.1f}".replace(".", ",")


def _zeitpunkt(text: Any) -> datetime | None:
    try:
        wert = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=UTC)


def _juengster(repo: Path, ticket: str) -> datetime:
    zeiten = [z for z in (_zeitpunkt(r.get("zeit") or r.get("ts")) for r in lese(repo, ticket)) if z]
    return max(zeiten) if zeiten else datetime.min.replace(tzinfo=UTC)


def _manifest_eintraege(repo: Path) -> dict[str, dict[str, Any]]:
    """Ticket → Manifest-Eintrag aus allen ``spec-<Zahl>.json`` (Fallback-Quelle)."""
    from . import manifest

    eintraege: dict[str, dict[str, Any]] = {}
    for datei in manifest.alle_manifeste(repo).values():
        try:
            daten, _ = manifest.lies_json(datei)
        except (OSError, ValueError) as fehler:
            log.warning("Manifest %s unlesbar: %s", datei, fehler)
            continue
        tickets = daten.get("tickets") if isinstance(daten, dict) else None
        if isinstance(tickets, dict):
            for nummer, eintrag in tickets.items():
                if isinstance(eintrag, dict):
                    eintraege[str(nummer)] = eintrag
    return eintraege


def umfang_art(text: str) -> str:
    """Umfang-Art eines Tickets, z. B. „Datenbank+Oberfläche“ (sonst „Sonstiges“)."""
    klein = (text or "").lower()
    treffer = sorted(name for name, muster in UMFANG_ARTEN.items() if muster.search(klein))
    return "+".join(treffer) or "Sonstiges"


def _faustregeln(zeilen: list[dict[str, Any]], grenze: float) -> list[str]:
    regeln = ["Faustregeln für den Schnitt:"]
    faktoren = [z["ist_k"] / z["schaetzung_k"] for z in zeilen if ist_schaetzung(z["schaetzung_k"]) and z["ist_k"]]
    if faktoren:
        mittel = _komma(sum(faktoren) / len(faktoren))
        regeln.append(
            f"- Schätzungen lagen im Mittel bei Faktor {mittel} — Schätzung × {mittel} muss unter {grenze:g}k bleiben."
        )
    je_art: dict[str, list[int]] = {}
    for z in zeilen:
        if z["sessions"] >= 1:
            art = umfang_art(f"{z['umfang'] or ''} {z['title'] or ''}")
            je_art.setdefault(art, []).append(z["sessions"])
    for art, anzahl in sorted(je_art.items(), key=lambda paar: (-sum(paar[1]) / len(paar[1]), paar[0])):
        regeln.append(f"- {art} brauchte im Mittel {_komma(sum(anzahl) / len(anzahl))} Sessions (n={len(anzahl)})")
    staffel = [z["ticket"] for z in zeilen if z["staffel"] > 1]
    if staffel:
        regeln.append(
            f"- Staffel > 1 bei {len(staffel)} Tickets: "
            + ", ".join(f"#{t}" for t in staffel)
            + " — diese waren zu groß für eine Session."
        )
    else:
        regeln.append("- Kein Ticket brauchte eine zweite Staffel.")
    return regeln


def lernstoff(repo: Path, letzte: int = 30, grenze_k: float | None = None) -> str:
    """Die jüngsten Tickets (nach Zeitstempel) als Lernstoff für ``/to-tickets``."""
    tickets = sorted(alle_tickets(repo), key=lambda t: _juengster(repo, t), reverse=True)
    tickets = tickets[: max(letzte, 0)]
    if not tickets:
        return "Kein Bau-Log vorhanden — noch kein Lernstoff."
    if grenze_k is None:
        from . import config

        grenze_k = float(config.lade(repo).get("staffel", {}).get("grenze_k", 240))
    aus_manifest = _manifest_eintraege(repo)
    zeilen = ["Lernstoff aus dem Bau-Log (Schätzung → Ist, Sessions je Ticket, jüngste zuerst):"]
    daten: list[dict[str, Any]] = []
    for ticket in tickets:
        z = zusammenfassung(repo, ticket)
        eintrag = aus_manifest.get(str(ticket), {})
        if z["schaetzung_k"] is None:
            z["schaetzung_k"] = eintrag.get("schaetzung_k")
        z["title"] = z.get("title") or eintrag.get("title")
        if not z["umfang"]:
            z["umfang"] = eintrag.get("umfang") or z["title"]
        daten.append(z)
        schaetzung = z["schaetzung_k"]
        hat_schaetzung = ist_schaetzung(schaetzung)
        soll = f"{schaetzung:g}k" if hat_schaetzung else "ohne Schätzung"
        faktor = f" (Faktor {_komma(z['ist_k'] / schaetzung)})" if hat_schaetzung and z["ist_k"] else ""
        ist = f"{z['ist_k']:g}k" if z["ist_k"] else "?"
        umfang = str(z["umfang"] or "").replace("\n", " ")[:110]
        zeilen.append(
            f"- #{z['ticket']}: {soll} geschätzt → {ist} ist{faktor}, "
            f"{z['sessions']} Session(s), {z['subagenten']} Subagent(en)" + (f" · {umfang}" if umfang else "")
        )
        schwer = str(z["schwierigkeiten"] or "").replace("\n", " ")[:110]
        if schwer:
            zeilen.append(f"  Schwierigkeiten: {schwer}")
    zeilen.append("")
    zeilen += _faustregeln(daten, grenze_k)
    return "\n".join(zeilen)
