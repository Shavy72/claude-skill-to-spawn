"""Zustandsloser Taktgeber Bau-Server (#906, App #1402).

Läuft ohne wache Aufseher-Session (z. B. als Cron-Job alle 5 Minuten auf dem
Bau-Server) und führt keine eigene Zustandsdatei. Ein Lauf liest ausschließlich
den GitHub-Stand (Unter-Tickets der Spec, Labels, Kommentare der Deploy-Schlange
„grün auf <Hauptzweig>“ und „live“) und fragt den Prüfer:

- Alle 4 Regeln erfüllt + bereit zum Schließen → Ticket schließen (nie bei
  ``checkpoint:human``) und ein ggf. gesetztes Staging-Label entfernen.
- Alle 4 Regeln erfüllt, aber nur auf Staging (Live fehlt noch) → Label
  ``ticket_zustand.STAGING_FERTIG_LABEL`` (fertig auf Staging, wartet auf Live)
  setzen, nicht wieder öffnen, erst nach Live-Meldung der Schlange schließen.
- Wiedereröffnetes Ticket → Bau-Session einmal über :mod:`to_spawn.startstelle`
  mit Sperre je Ticket wecken (kein Doppel-Wecken bei Gleichlauf).
- Jede ausgeführte Handlung (``geschlossen``, ``label_gesetzt``, ``geweckt``)
  wird über :func:`to_spawn.bau_log.schreibe` protokolliert (``wer="taktgeber"``).

Die Fertig-Regeln stehen ausschließlich im Prüfer (:mod:`to_spawn.schliess_pruefer`
aus #864); dieses Modul enthält keine eigene Fertig-Regel.
"""

from __future__ import annotations

import argparse
import getpass
import html
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from . import (
    auswahl,
    bau_log,
    befund,
    capo,
    config,
    gh,
    konsole,
    manifest,
    pc_fenster,
    prozessbaum,
    schliess_pruefer,
    sperre,
    starter,
    startstelle,
    ticket_zustand,
    tmux_aufruf,
    wecker,
)

log = logging.getLogger("to_spawn.taktgeber")

SKILL = Path(__file__).resolve().parent.parent
#: Standard-Takt in Minuten (Linux-Cron und Windows-Aufgabe).
TAKT_MIN = 5
#: Standard-Cron-Takt des zustandslosen Taktgebers (alle 5 Minuten).
CRON_TAKT = f"*/{TAKT_MIN} * * * *"
#: Zeitlimit je ``schtasks``-Aufruf in Sekunden (Windows-Aufgabenplanung).
SCHTASKS_ZEITLIMIT_S = 30
#: Akteur-Kennung im Ereignis-Protokoll (``bau_log.schreibe``).
WER = "taktgeber"
#: Sichtbare Label-Beschreibung für ``ticket_zustand.STAGING_FERTIG_LABEL``.
STAGING_LABEL_BESCHREIBUNG = "Fertig auf Staging, wartet auf Live"
#: Farbe für das Staging-Wartelabel (nur beim erstmaligen Anlegen).
STAGING_LABEL_FARBE = "fbca04"
#: CLI-Unterbefehle dieses Moduls.
BEFEHLE = ("taktgeber", "taktgeber-einrichten")
#: Wartezeit (s) nach dem Starten von ``bau.py``, bis die Start-Sperre übernommen ist.
_WECK_UEBERNAHME_WARTE_S = 0.6

#: Voller Git-Commit-Hash (40 Hex-Zeichen; 7-Zeichen-Kurz-Hashes genügen nicht, #1400).
_VOLL_SHA = re.compile(r"\b([0-9a-f]{40})\b", re.IGNORECASE)
#: Verneinungen/Warte-Hinweise, die nie als Live-Meldung gelten dürfen.
_KEIN_LIVE = re.compile(
    r"\b(?:wartet\s+auf\s+live|nicht\s+live|noch\s+nicht\s+live|ohne\s+live|vor\s+live|fehlt\s+live)\b",
    re.IGNORECASE,
)
#: Positive Live-Meldung der Deploy-Schlange am Ticket.
_LIVE_SIGNAL = re.compile(
    r"(?:\bist\s+live\b|\blive\s+geschaltet\b|\bauf\s+live\b|\blive\s*[:·(]|\blive\b[^\n]*\b[0-9a-f]{40}\b)",
    re.IGNORECASE,
)
#: Wiedereröffnungs-Hinweis in Beteiligten-Kommentaren.
_REOPEN_KOMMENTAR = re.compile(r"\bwieder\s+geöffnet\b", re.IGNORECASE)


@dataclass(frozen=True)
class PruefErgebnis:
    """Ergebnis des Prüfers für ein Ticket: ``ok`` oder Liste der Ablehnungsgründe."""

    ok: bool
    gruende: tuple[str, ...] = ()


@dataclass(frozen=True)
class TicketAktion:
    """Ergebnis der Bearbeitung genau eines Tickets in einem Taktgeber-Lauf."""

    ticket: int
    aktion: str
    grund: str
    commit: str = ""


@dataclass(frozen=True)
class LaufErgebnis:
    """Gesamtergebnis eines zustandslosen Taktgeber-Laufs über eine Spec."""

    ok: bool
    aktionen: list[TicketAktion]
    fehler: str = ""


PrueferFn = Callable[[Path, str, int, list[capo.Commit], dict[str, Any], str | None], PruefErgebnis]
WeckerFn = Callable[[Path, int, int, str, str, bool], tuple[bool, str]]


# --- GitHub-Signale (rein lesend, zustandslos) ----------------------------------


def _zeitpunkt(roh: Any) -> datetime | None:
    if not roh:
        return None
    text = str(roh).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        wert = datetime.fromisoformat(text)
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=UTC)


def _kommentar_koerper(kommentar: Mapping[str, Any]) -> str:
    return str(kommentar.get("body") or "")


def _ist_reopen_kommentar(kommentar: Mapping[str, Any]) -> bool:
    text = _kommentar_koerper(kommentar).strip()
    if not text:
        return False
    return text.startswith(befund.KOPF) or bool(_REOPEN_KOMMENTAR.search(text))


def _gruen_muster(hauptzweig: str) -> re.Pattern[str]:
    zweig = re.escape(hauptzweig.strip() or "master")
    return re.compile(rf"grün\s+auf\s+{zweig}\b", re.IGNORECASE)


def gruen_auf_hauptzweig(
    kommentare: Sequence[Mapping[str, Any]],
    hauptzweig: str,
    *,
    nach_zeit: datetime | None = None,
) -> tuple[str, int] | None:
    """Jüngstes „grün auf <hauptzweig>“-Signal mit vollem 40-Zeichen-SHA → ``(sha, index)``.

    Liefert ``None``, wenn kein passender Kommentar existiert, der Hash zu kurz ist
    (7 Zeichen genügen nicht, #1400) oder nach dem Kommentar eine Wiedereröffnung lag.
    """
    muster = _gruen_muster(hauptzweig)
    letzter_treffer: tuple[str, int] | None = None
    for idx, kommentar in enumerate(kommentare):
        if _ist_reopen_kommentar(kommentar):
            letzter_treffer = None
            continue
        zeit = _zeitpunkt(kommentar.get("created_at"))
        if nach_zeit is not None and zeit is not None and zeit <= nach_zeit:
            continue
        text = _kommentar_koerper(kommentar)
        if not muster.search(text):
            continue
        sha_treffer = _VOLL_SHA.search(text)
        if sha_treffer:
            letzter_treffer = (sha_treffer.group(1).lower(), idx)
    return letzter_treffer


def ist_live_gemeldet(
    kommentare: Sequence[Mapping[str, Any]],
    gruen_sha: str,
    ab_index: int,
) -> bool:
    """Hat die Deploy-Schlange Live für ``gruen_sha`` ab ``ab_index`` am Ticket gemeldet?

    Verneinungen wie „wartet auf Live“ oder „nicht live“ zählen nie. Nennt der
    Live-Kommentar einen vollen SHA, muss er zu ``gruen_sha`` passen.
    """
    ziel = gruen_sha.lower()
    for kommentar in kommentare[max(0, ab_index) :]:
        text = _kommentar_koerper(kommentar)
        if not text or _KEIN_LIVE.search(text):
            continue
        if not _LIVE_SIGNAL.search(text):
            continue
        shas = [s.lower() for s in _VOLL_SHA.findall(text)]
        if shas and ziel not in shas:
            continue
        return True
    return False


def ist_wiedereroeffnet(
    issue: Mapping[str, Any],
    kommentare: Sequence[Mapping[str, Any]],
    gruen_index: int | None,
) -> tuple[bool, str]:
    """Erkennt rein aus dem GitHub-Stand, ob ein offenes Ticket wiedereröffnet wurde."""
    if ticket_zustand.endgueltig_zu(issue) or ticket_zustand.fertig_gebaut(issue):
        return False, ""
    if gruen_index is not None:
        return False, ""
    letzter_reopen = ""
    for kommentar in reversed(kommentare):
        if _ist_reopen_kommentar(kommentar):
            letzter_reopen = " ".join(_kommentar_koerper(kommentar).split())[:240]
            break
    grund_feld = ticket_zustand.schliessgrund(issue)
    if grund_feld == "reopened":
        return True, letzter_reopen or "Ticket auf GitHub wiedereröffnet"
    if letzter_reopen and issue.get("closed_at"):
        return True, letzter_reopen
    return False, ""


def braucht_live(konfig: dict[str, Any]) -> bool:
    """Verlangt dieses Repo eine Live-Meldung der Schlange vor dem Schließen?"""
    if config.live_bewusst_ohne(konfig):
        return False
    live = config.live_schritt(konfig)
    vps = konfig.get("vps") if isinstance(konfig.get("vps"), dict) else {}
    return bool(live.get("befehl") or live.get("url") or vps.get("ssh") or config.hat_staging(konfig))


# --- Prüfer-Tür (keine eigene Fertig-Regel im Taktgeber) ------------------------


def _pruefer_antwort_normieren(antwort: Any) -> PruefErgebnis:
    if isinstance(antwort, PruefErgebnis):
        return antwort
    if hasattr(antwort, "ok") and hasattr(antwort, "gruende"):
        gruende = tuple(str(g) for g in antwort.gruende or ())
        return PruefErgebnis(bool(antwort.ok) and not gruende, gruende)
    if isinstance(antwort, tuple) and len(antwort) == 2:
        ok, gruende = antwort
        liste = tuple(str(g) for g in (gruende or ()))
        return PruefErgebnis(bool(ok) and not liste, liste)
    if isinstance(antwort, bool):
        return PruefErgebnis(antwort, () if antwort else ("Prüfer hat abgelehnt",))
    return PruefErgebnis(False, ("Prüfer-Antwort unlesbar",))


def pruefe_mit_pruefer(
    repo: Path,
    ref: str,
    ticket: int,
    commits: list[capo.Commit],
    konfig: dict[str, Any],
    *,
    gruen_sha: str | None,
) -> PruefErgebnis:
    """Fragt ausschließlich :func:`to_spawn.schliess_pruefer.pruefe` (#864) — keine eigene Fertig-Regel."""
    stand = schliess_pruefer.Stand(
        repo=repo,
        ticket=ticket,
        ref=ref,
        belege_ordner=schliess_pruefer.belege_ordner(konfig),
        eigene=schliess_pruefer.ticket_commits(commits, ticket),
        gruen_auf_hauptzweig=gruen_sha,
    )
    return _pruefer_antwort_normieren(schliess_pruefer.pruefe(stand))


# --- GitHub-Schreibaktionen (idempotent) ----------------------------------------


def _ticket_frisch(ticket: int, gh_repo: str, repo: Path) -> dict[str, Any] | None:
    """Liest den aktuellen Ticket-Stand direkt vor einer Schreibaktion (Gleichlauf-Schutz)."""
    daten = gh.json_lauf(
        ["issue", "view", str(ticket), "--repo", gh_repo, "--json", "state,labels,stateReason"],
        cwd=repo,
        timeout=gh.ticket_stand_timeout(),
    )
    return daten if isinstance(daten, dict) and "state" in daten else None


def _label_setzen(repo: Path, gh_repo: str, ticket: int, label: str) -> bool:
    gh.label_sicherstellen(
        repo,
        label,
        farbe=STAGING_LABEL_FARBE,
        beschreibung=STAGING_LABEL_BESCHREIBUNG,
        repo_slug=gh_repo,
    )
    code, _ = gh.lauf(["issue", "edit", str(ticket), "--repo", gh_repo, "--add-label", label], cwd=repo)
    if code != 0:
        log.warning("Taktgeber #%s: Label %s nicht gesetzt (gh Exit %s).", ticket, label, code)
        return False
    return True


def _label_entfernen(repo: Path, gh_repo: str, ticket: int, label: str) -> bool:
    code, _ = gh.lauf(["issue", "edit", str(ticket), "--repo", gh_repo, "--remove-label", label], cwd=repo)
    if code != 0:
        log.warning("Taktgeber #%s: Label %s nicht entfernt (gh Exit %s).", ticket, label, code)
        return False
    return True


def _ticket_schliessen(repo: Path, gh_repo: str, ticket: int, gruen_sha: str, *, live: bool) -> bool:
    zusatz = ", live gemeldet" if live else ""
    text = f"Taktgeber: geschlossen (alle 4 Regeln erfüllt{zusatz}, Commit {gruen_sha[:12]})."
    return befund.kommentiere(ticket, gh_repo, text, befehl="close")


# --- Wecken über die Start-Stelle (Linux & Windows) -----------------------------


def abgeloest_optionen() -> dict[str, Any]:
    """Popen-Optionen für abgelöste Kind-Prozesse (Windows: fensterlos, nie ``DETACHED_PROCESS``)."""
    if sys.platform == "win32":
        basis = int(prozessbaum.fensterlos().get("creationflags", 0))
        return {"creationflags": basis | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _sperre_belegt(sperr_pfad: Path) -> bool:
    with sperre.halte(sperr_pfad) as frei:
        return not frei


def wecke_ticket(
    repo: Path,
    spec: int,
    ticket: int,
    gh_repo: str,
    grund: str,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Weckt die Bau-Session eines wiedereröffneten Tickets über :mod:`to_spawn.startstelle`.

    Prüft vor dem Start die Start-Sperre (``startstelle.sperr_datei``): läuft schon
    eine Session für das Ticket, passiert nichts. Gestartet wird immer ``bau.py``
    (das selbst :func:`startstelle.betreten` durchläuft).
    """
    haupt_sperre = startstelle.sperr_datei(str(ticket), gh_repo)
    if _sperre_belegt(haupt_sperre):
        return False, f"#{ticket} läuft schon (Start-Sperre belegt) — kein zweiter Weck-Aufruf"
    auftrag = f"Wiedereröffnet ({grund}) — Befund beheben, testen und neu einreichen."

    if pc_fenster.am_pc():
        if not pc_fenster.wt_verfuegbar():
            log.warning(
                "Taktgeber #%s: Windows Terminal (wt) fehlt — kein unsichtbarer Hintergrundstart am PC.",
                ticket,
            )
            return (
                False,
                f"#{ticket} nicht geweckt: Windows Terminal (wt) fehlt — kein unsichtbarer Hintergrundstart am PC",
            )
        if dry_run:
            return True, f"#{ticket} [Probe] würde Tab „bau {ticket}“ über Start-Stelle öffnen"
        try:
            pc_fenster.tab_starten(repo, ticket, auftrag)
        except OSError as fehler:
            log.warning("Taktgeber #%s: Tab-Start fehlgeschlagen: %s", ticket, fehler)
            return False, f"#{ticket} Tab-Start fehlgeschlagen ({fehler})"

        frist = time.monotonic() + _WECK_UEBERNAHME_WARTE_S
        while time.monotonic() < frist:
            if _sperre_belegt(haupt_sperre):
                break
            time.sleep(0.05)
        return True, f"#{ticket} geweckt im Tab „bau {ticket}“ über Start-Stelle ({grund})"

    if dry_run:
        return True, f"#{ticket} [Probe] würde Bau-Session über Start-Stelle wecken"

    if sys.platform == "win32":
        befehl = capo.folge_befehl(repo, spec, ticket, None, auftrag)
        umgebung = capo.folge_umgebung(auftrag)
        ueber_tmux = False
    else:
        sitzung, _ = tmux_aufruf.sitzung_aufloesen(repo, spec)
        fenster = capo.tmux_fenster(spec, sitzung=sitzung)
        befehl = capo.folge_befehl(repo, spec, ticket, fenster, auftrag, sitzung=sitzung)
        umgebung = capo.folge_umgebung(auftrag)
        ueber_tmux = fenster is not None

    try:
        proc = starter.starte(
            befehl,
            cwd=repo,
            notnagel=None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=umgebung,
            **abgeloest_optionen(),
        )
    except OSError as fehler:
        log.warning("Taktgeber #%s: Weck-Start fehlgeschlagen: %s", ticket, fehler)
        return False, f"#{ticket} Weck-Start fehlgeschlagen ({fehler})"

    # Kurz warten, bis der gestartete Prozess die Ticket-Start-Sperre übernimmt (oder
    # tmux seinen Befehl abgesetzt hat), damit ein unmittelbar folgender Takt nicht doppelt weckt.
    frist = time.monotonic() + _WECK_UEBERNAHME_WARTE_S
    while time.monotonic() < frist:
        if _sperre_belegt(haupt_sperre):
            break
        if ueber_tmux and proc.poll() is not None:
            break
        if not ueber_tmux and proc.poll() is not None:
            break
        time.sleep(0.05)

    return True, f"#{ticket} geweckt über Start-Stelle ({grund})"


# --- Einzelticket-Entscheidung & Hauptlauf --------------------------------------


#: Zuordnung Taktgeber-Aktion → Ereignis-Protokoll-Typ (#902/#903/#906).
_AKTION_TYP: dict[str, str] = {
    "geschlossen": "schliessen",
    "label_gesetzt": "taktgeber",
    "geweckt": "taktgeber",
}


def _aktion_was(ticket: int, aktion: str, felder: Mapping[str, Any]) -> str:
    if aktion == "geschlossen":
        return f"Ticket #{ticket} geschlossen"
    if aktion == "label_gesetzt":
        label = str(felder.get("label") or ticket_zustand.STAGING_FERTIG_LABEL)
        return f"Label {label} auf #{ticket} gesetzt"
    if aktion == "geweckt":
        return f"Bau-Session für #{ticket} geweckt"
    return f"Taktgeber-Aktion {aktion} auf #{ticket}"


def _protokolliere(repo: Path, ticket: int, aktion: str, **felder: Any) -> None:
    """Jede Handlung geht ausschließlich über :func:`bau_log.schreibe` (``wer="taktgeber"``)."""
    rest = dict(felder)
    typ = _AKTION_TYP.get(aktion, "taktgeber")
    was = str(rest.pop("was", "") or "").strip() or _aktion_was(ticket, aktion, rest)
    commit_wert = str(rest.pop("commit", "") or "").strip() or bau_log.git_kopf_kurz(repo, ticket)
    grund_text = str(rest.pop("grund", "") or "").strip() or was
    session_wert = str(rest.pop("session", "") or "").strip() or WER
    kontextstand = rest.pop("kontextstand", None)
    if kontextstand is None:
        kontextstand = bau_log.kontextstand_aktuell(repo, ticket)
    idempotenz = str(rest.pop("idempotenz_schluessel", "") or "").strip()
    if not idempotenz:
        if aktion == "label_gesetzt":
            label = str(rest.get("label") or ticket_zustand.STAGING_FERTIG_LABEL)
            idempotenz = f"taktgeber:{aktion}:{ticket}:{label}:{commit_wert}"
        elif aktion == "geschlossen":
            idempotenz = f"taktgeber:{aktion}:{ticket}:{commit_wert}"
        else:
            idempotenz = f"taktgeber:{aktion}:{ticket}:{commit_wert}:{grund_text[:80]}"
    try:
        bau_log.schreibe(
            typ,
            repo=repo,
            ticket=ticket,
            wer=WER,
            was=was,
            session=session_wert,
            commit=commit_wert,
            grund=grund_text,
            kontextstand=kontextstand,
            aktion=aktion,
            idempotenz_schluessel=idempotenz,
            **rest,
        )
    except (OSError, ValueError, TimeoutError) as fehler:
        log.warning("Taktgeber #%s: Ereignis-Protokoll (%s/%s) nicht geschrieben: %s", ticket, typ, aktion, fehler)


def _bearbeite_ticket(
    repo: Path,
    spec: int,
    gh_repo: str,
    issue: Mapping[str, Any],
    ref: str,
    hauptzweig: str,
    commits: list[capo.Commit],
    konfig: dict[str, Any],
    *,
    checkpoint: str,
    dry_run: bool,
    pruefer: PrueferFn,
    wecker: WeckerFn,
) -> TicketAktion:
    nr = int(issue["number"])
    if ticket_zustand.endgueltig_zu(issue):
        return TicketAktion(nr, "unveraendert", "bereits geschlossen")

    labels = ticket_zustand.label_namen(issue)
    if checkpoint in labels:
        return TicketAktion(nr, "checkpoint_offen", f"Label {checkpoint} — schließt Taktgeber nie")

    # Pro-Ticket-OS-Sperre (kernel-basiert, keine Zustandsdatei) verhindert Doppel-Handlungen bei Gleichlauf.
    takt_sperre = startstelle.sperr_datei(str(nr), gh_repo).with_suffix(".taktgeber.lock")
    with sperre.halte(takt_sperre) as frei:
        if not frei:
            return TicketAktion(nr, "uebersprungen", "paralleler Taktgeber-Lauf aktiv")

        kommentare = capo.issue_kommentare(gh_repo, nr)
        if kommentare is None:
            log.warning("Taktgeber #%s: Kommentare nicht lesbar — übersprungen (fail-closed).", nr)
            return TicketAktion(nr, "uebersprungen", "Kommentare nicht lesbar (fail-closed)")

        closed_at = _zeitpunkt(issue.get("closed_at"))
        gruen = gruen_auf_hauptzweig(kommentare, hauptzweig, nach_zeit=closed_at)
        gruen_sha, gruen_idx = gruen if gruen is not None else (None, None)

        # 1) Wiedereröffnetes Ticket ohne neues Grün-Signal → über Start-Stelle wecken.
        reopened, reopen_grund = ist_wiedereroeffnet(issue, kommentare, gruen_idx)
        if reopened:
            gestartet, meldung = wecker(repo, spec, nr, gh_repo, reopen_grund, dry_run)
            if gestartet:
                if not dry_run:
                    _protokolliere(repo, nr, "geweckt", grund=reopen_grund)
                return TicketAktion(nr, "geweckt", meldung)
            return TicketAktion(nr, "unveraendert", meldung)

        # 2) Noch kein „grün auf <hauptzweig>“-Kommentar → noch im Bau, nichts zu schließen.
        if gruen_sha is None or gruen_idx is None:
            return TicketAktion(nr, "unveraendert", f"noch kein „grün auf {hauptzweig}“-Kommentar mit vollem Hash")

        # 3) Prüfer fragen (einzige Regel-Quelle).
        pruefung = pruefer(repo, ref, nr, commits, konfig, gruen_sha)
        if not pruefung.ok:
            grund = "; ".join(pruefung.gruende) or "Prüfer hat abgelehnt"
            return TicketAktion(nr, "abgelehnt", grund, commit=gruen_sha)

        # 4) Aktuellen Ticket-Stand vor Schreibaktion frisch prüfen (schützt bei Gleichlauf).
        frisch = _ticket_frisch(nr, gh_repo, repo) or issue
        if ticket_zustand.endgueltig_zu(frisch):
            return TicketAktion(nr, "unveraendert", "inzwischen geschlossen")
        frisch_labels = ticket_zustand.label_namen(frisch)
        if checkpoint in frisch_labels:
            return TicketAktion(nr, "checkpoint_offen", f"Label {checkpoint} — schließt Taktgeber nie")

        # 5) Live-Pflicht prüfen: fehlt Live → nur Staging-Label setzen, nie wieder öffnen.
        live_noetig = braucht_live(konfig)
        live_da = ist_live_gemeldet(kommentare, gruen_sha, gruen_idx)
        if live_noetig and not live_da:
            if ticket_zustand.STAGING_FERTIG_LABEL in frisch_labels:
                return TicketAktion(nr, "wartet_live", "Staging-Label schon gesetzt, wartet auf Live", commit=gruen_sha)
            if dry_run:
                return TicketAktion(
                    nr,
                    "label_gesetzt",
                    f"[Probe] würde Label {ticket_zustand.STAGING_FERTIG_LABEL} setzen",
                    commit=gruen_sha,
                )
            if _label_setzen(repo, gh_repo, nr, ticket_zustand.STAGING_FERTIG_LABEL):
                _protokolliere(
                    repo,
                    nr,
                    "label_gesetzt",
                    label=ticket_zustand.STAGING_FERTIG_LABEL,
                    commit=gruen_sha,
                    grund="fertig auf Staging, wartet auf Live",
                )
                return TicketAktion(
                    nr,
                    "label_gesetzt",
                    f"Label {ticket_zustand.STAGING_FERTIG_LABEL} gesetzt (wartet auf Live)",
                    commit=gruen_sha,
                )
            return TicketAktion(nr, "uebersprungen", "Staging-Label konnte nicht gesetzt werden", commit=gruen_sha)

        # 6) Alle 4 Regeln erfüllt + bereit zum Schließen (live oder kein Live-Schritt) → schließen.
        if dry_run:
            return TicketAktion(nr, "geschlossen", "[Probe] würde schließen", commit=gruen_sha)
        if ticket_zustand.STAGING_FERTIG_LABEL in frisch_labels:
            _label_entfernen(repo, gh_repo, nr, ticket_zustand.STAGING_FERTIG_LABEL)
        if _ticket_schliessen(repo, gh_repo, nr, gruen_sha, live=live_da):
            _protokolliere(
                repo,
                nr,
                "geschlossen",
                commit=gruen_sha,
                live=live_da,
                grund="alle 4 Regeln erfüllt" + (" und live" if live_da else ""),
            )
            return TicketAktion(nr, "geschlossen", "alle 4 Regeln erfüllt", commit=gruen_sha)
        return TicketAktion(nr, "uebersprungen", "Schließen auf GitHub gescheitert", commit=gruen_sha)


def lauf(
    repo: Path,
    spec: int,
    gh_repo: str,
    *,
    konfig: dict[str, Any] | None = None,
    dry_run: bool = False,
    pruefer: PrueferFn | None = None,
    wecker: WeckerFn | None = None,
) -> LaufErgebnis:
    """Führt einen vollständigen zustandslosen Taktgeber-Lauf für Spec ``spec`` aus."""
    repo = repo.resolve()
    konfig_aktiv = konfig if konfig is not None else config.lade(repo)
    liste = capo.kinder(gh_repo, spec)
    if liste is None:
        return LaufErgebnis(False, [], f"Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh)")
    auswahl_stand = auswahl.lesen(repo, spec)
    if auswahl_stand is not None:
        liste = auswahl.filtere_issues(liste, auswahl_stand)
    ref = capo.haupt_ref(repo)
    if ref is None:
        return LaufErgebnis(False, [], "kein origin/<Hauptzweig> vorhanden — git fetch prüfen")

    hauptzweig = gh.hauptzweig(repo)
    checkpoint = manifest.checkpoint_label(konfig_aktiv)
    commits = capo.alle_commits(repo, ref)
    pruef_fn: PrueferFn = (
        pruefer
        if pruefer is not None
        else lambda r, rf, t, cs, k, sha: pruefe_mit_pruefer(r, rf, t, cs, k, gruen_sha=sha)
    )
    weck_fn: WeckerFn = wecker if wecker is not None else wecke_ticket

    aktionen: list[TicketAktion] = []
    for issue in sorted(liste, key=lambda i: int(i["number"])):
        aktionen.append(
            _bearbeite_ticket(
                repo,
                spec,
                gh_repo,
                issue,
                ref,
                hauptzweig,
                commits,
                konfig_aktiv,
                checkpoint=checkpoint,
                dry_run=dry_run,
                pruefer=pruef_fn,
                wecker=weck_fn,
            )
        )
    return LaufErgebnis(True, aktionen)


# --- Idempotente Cron-Einrichtung (Bau-Server, nur Linux) -----------------------


def log_datei(spec: int, *, anlegen: bool = True) -> Path:
    """Logdatei für die Cron- und Windows-Aufgaben-Ausgabe des Taktgebers (kein Zustand, reines Lauf-Log)."""
    ordner = Path(
        os.environ.get("TO_SPAWN_TAKTGEBER_LOG") or (Path.home() / ".local" / "state" / "to-spawn" / "taktgeber")
    )
    if anlegen:
        ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{spec}.log"


def _lauf_log_schreiben(spec: int, zeilen: Sequence[str]) -> None:
    """Hängt Zeitstempel-Zeilen an ``log_datei(spec)`` an (für ``pythonw.exe`` / Windows-Aufgaben, #907)."""
    stempel = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    try:
        pfad = log_datei(spec)
        with pfad.open("a", encoding="utf-8", newline="\n") as fh:
            for zeile in zeilen:
                fh.write(f"{stempel} {zeile}\n")
    except OSError as fehler:
        log.warning("Taktgeber #%s: Lauf-Log nicht schreibbar: %s", spec, fehler)


def cron_marke(spec: int) -> str:
    """Eigene Cron-Marke (kollidiert nie mit dem bestehenden Aufseher-Takt)."""
    return f"# to-spawn-taktgeber #{spec}"


def cron_zeilen(zeilen: list[str], spec: int, befehl: str) -> list[str]:
    """Crontab mit genau einer Taktgeber-Zeile für ``spec`` (idempotent)."""
    marke = cron_marke(spec)
    zeile = f"{befehl}  {marke}"
    stellen = [i for i, z in enumerate(zeilen) if z.rstrip().endswith(marke)]
    neu = [z for z in zeilen if not z.rstrip().endswith(marke)]
    neu.insert(stellen[0] if stellen else len(neu), zeile)
    return neu


def cron_befehl(spec: int, repo: Path, log_pfad: Path) -> str:
    """Cron-Befehl: alle Pfade gequotet, kein Claude nötig."""
    q = shlex.quote
    return (
        f"{CRON_TAKT} {q(sys.executable)} {q(str(SKILL / 'to_spawn.py'))} "
        f"taktgeber {spec} --repo-dir {q(str(repo))} >> {q(str(log_pfad))} 2>&1"
    )


def cron_einrichten(spec: int, repo: Path, *, trocken: bool = False) -> str:
    """Taktgeber alle 5 min als Cron-Zeile (idempotent, über stdin, nur Linux)."""
    from . import aufpasser

    ziel_log = log_datei(spec, anlegen=False)
    befehl = cron_befehl(spec, repo, ziel_log)
    alt = aufpasser._crontab("-l")
    zeilen = alt.stdout.splitlines() if alt.returncode == 0 else []
    neu = cron_zeilen(zeilen, spec, befehl)
    zeile = neu[next(i for i, z in enumerate(neu) if z.rstrip().endswith(cron_marke(spec)))]
    if neu == zeilen:
        return f"Cron unverändert: {zeile}"
    if trocken:
        return f"[trocken] Cron würde eingerichtet: {zeile}"
    ziel_log.parent.mkdir(parents=True, exist_ok=True)
    stempel = time.strftime("%Y%m%d-%H%M%S")
    (ziel_log.parent / f"crontab.vorher.{stempel}").write_text(alt.stdout, encoding="utf-8")
    ergebnis = aufpasser._crontab("-", eingabe="\n".join(neu) + "\n")
    if ergebnis.returncode != 0:
        raise RuntimeError(f"crontab schreiben: {ergebnis.stderr.strip()[:300]}")
    return f"Cron eingerichtet: {zeile}"


# --- Idempotente Windows-Aufgaben (PC: beim Anmelden + im Takt, fensterlos) -----


def windows_aufgabe_name(spec: int, *, anmelden: bool = False) -> str:
    """Name der Windows-Aufgabe für den Taktgeber (Takt- oder Anmelde-Aufgabe, #907)."""
    return f"to-spawn Taktgeber #{spec} Anmelden" if anmelden else f"to-spawn Taktgeber #{spec}"


def windows_aufgabe_argumente(spec: int, repo: Path) -> str:
    """Argumente für ``pythonw.exe`` in der Windows-Aufgabenplanung."""
    return f'"{SKILL / "to_spawn.py"}" taktgeber {spec} --repo-dir "{repo}"'


def _xml_feld(xml: str, name: str) -> str:
    treffer = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
    return html.unescape(treffer.group(1)).strip() if treffer else ""


def windows_aufgabe_gleich(
    xml: str | None,
    pythonw: str,
    argumente: str,
    *,
    takt_min: int | None = None,
    anmelden: bool = False,
) -> bool:
    """Prüft, ob die vorhandene ``schtasks /Query /XML``-Aufgabe Befehl, Argumente und Auslöser trifft."""
    # Regex statt XML-Parser: schtasks kündigt UTF-16 an, liefert über die Pipe aber Text.
    if not xml:
        return False
    if _xml_feld(xml, "Command").strip('"').lower() != pythonw.lower():
        return False
    if _xml_feld(xml, "Arguments") != argumente:
        return False
    if anmelden:
        return "<LogonTrigger>" in xml
    if takt_min is not None:
        return wecker._windows_intervall_min(_xml_feld(xml, "Interval")) == takt_min
    return True


def _schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    """Führt ``schtasks`` fensterlos mit Zeitlimit aus (``CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP``)."""
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            check=False,
            timeout=SCHTASKS_ZEITLIMIT_S,
            **prozessbaum.ohne_fenster(),
        )
    except FileNotFoundError as fehler:
        raise RuntimeError("schtasks nicht gefunden — Windows-Aufgabenplanung fehlt") from fehler
    except subprocess.TimeoutExpired as fehler:
        raise RuntimeError(f"schtasks {args[0]} hängt (über {SCHTASKS_ZEITLIMIT_S} s)") from fehler


def _windows_benutzer_id() -> str:
    """Aktuelle Windows-Benutzerkennung (``DOMAIN\\USER``) für benutzerbezogenes ``LogonTrigger``-XML."""
    nutzer = (os.environ.get("USERNAME") or "").strip()
    if not nutzer:
        try:
            nutzer = getpass.getuser().strip()
        except (KeyError, OSError):
            nutzer = ""
    domaene = (os.environ.get("USERDOMAIN") or "").strip()
    return f"{domaene}\\{nutzer}" if domaene and nutzer else nutzer


def windows_anmelde_xml(pythonw: str, argumente: str, *, benutzer: str | None = None) -> str:
    """Erzeugt Aufgabenplanungs-XML für den Anmelde-Auslöser ohne Admin-Rechte (#907).

    ``schtasks /Create /F /SC ONLOGON`` scheitert auf Windows 11 ohne erhöhte Rechte mit
    ``Zugriff verweigert``; ``/XML`` mit eigenem ``<UserId>`` im ``<LogonTrigger>`` und
    ``LeastPrivilege`` legt die Aufgabe im normalen Nutzer-Kontext an.
    """
    user_esc = xml_escape((benutzer if benutzer is not None else _windows_benutzer_id()).strip())
    befehl_esc = xml_escape(pythonw)
    arg_esc = xml_escape(argumente)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        "  <Triggers>\r\n"
        "    <LogonTrigger>\r\n"
        "      <Enabled>true</Enabled>\r\n"
        f"      <UserId>{user_esc}</UserId>\r\n"
        "    </LogonTrigger>\r\n"
        "  </Triggers>\r\n"
        "  <Principals>\r\n"
        '    <Principal id="Author">\r\n'
        f"      <UserId>{user_esc}</UserId>\r\n"
        "      <LogonType>InteractiveToken</LogonType>\r\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\r\n"
        "    </Principal>\r\n"
        "  </Principals>\r\n"
        "  <Settings>\r\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\r\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\r\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\r\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\r\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\r\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\r\n"
        "    <Enabled>true</Enabled>\r\n"
        "    <Hidden>false</Hidden>\r\n"
        "  </Settings>\r\n"
        '  <Actions Context="Author">\r\n'
        "    <Exec>\r\n"
        f"      <Command>{befehl_esc}</Command>\r\n"
        f"      <Arguments>{arg_esc}</Arguments>\r\n"
        "    </Exec>\r\n"
        "  </Actions>\r\n"
        "</Task>\r\n"
    )


def _windows_anmelde_aufgabe_anlegen(name: str, pythonw: str, argumente: str) -> subprocess.CompletedProcess[str]:
    """Legt die Anmelde-Aufgabe per UTF-16-Temp-XML an und löscht die Temp-Datei in ``finally``."""
    xml_inhalt = windows_anmelde_xml(pythonw, argumente)
    xml_pfad: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-16",
            newline="",
            suffix=".xml",
            prefix="to-spawn-taktgeber-",
            delete=False,
        ) as datei:
            datei.write(xml_inhalt)
            xml_pfad = Path(datei.name)
        return _schtasks("/Create", "/F", "/TN", name, "/XML", str(xml_pfad))
    finally:
        if xml_pfad is not None:
            xml_pfad.unlink(missing_ok=True)


def _windows_aufgabe_abfrage(name: str, *, trocken: bool) -> str | None:
    try:
        lauf = _schtasks("/Query", "/TN", name, "/XML")
    except RuntimeError:
        if not trocken:
            raise
        return None
    return lauf.stdout if lauf.returncode == 0 else None


def windows_aufgaben_einrichten(
    spec: int,
    repo: Path,
    *,
    takt_min: int = TAKT_MIN,
    trocken: bool = False,
    python: str = sys.executable,
    takt_name: str | None = None,
    anmelden_name: str | None = None,
) -> str:
    """Richtet den Taktgeber unter Windows beim Anmelden und alle ``takt_min`` min idempotent ein (#907).

    Startet über ``pythonw.exe`` ohne aufpoppendes Konsolenfenster; jeder ``schtasks``-Aufruf
    läuft mit ``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`` über :func:`prozessbaum.ohne_fenster`.
    Die Anmelde-Aufgabe wird über ``/XML`` mit benutzerbezogenem ``<LogonTrigger>`` und
    ``LeastPrivilege`` angelegt, sodass sie auch ohne Admin-Rechte funktioniert.
    """
    wecker.windows_aufgabe_intervall(takt_min)  # prüft 1–60 Minuten
    repo = repo.resolve()
    pythonw = Path(python).with_name("pythonw.exe")
    argumente = windows_aufgabe_argumente(spec, repo)
    befehl = f'"{pythonw}" {argumente}'
    anmelden_tn = anmelden_name or windows_aufgabe_name(spec, anmelden=True)
    takt_tn = takt_name or windows_aufgabe_name(spec, anmelden=False)

    meldungen: list[str] = []
    plaene: tuple[tuple[str, str, bool, list[str]], ...] = (
        ("Anmelde-Aufgabe", anmelden_tn, True, []),
        (f"Takt-Aufgabe (alle {takt_min} min)", takt_tn, False, ["/SC", "MINUTE", "/MO", str(takt_min)]),
    )
    for titel, name, ist_anmelden, zeitplan in plaene:
        vorhanden = _windows_aufgabe_abfrage(name, trocken=trocken)
        gleich = windows_aufgabe_gleich(
            vorhanden,
            str(pythonw),
            argumente,
            takt_min=None if ist_anmelden else takt_min,
            anmelden=ist_anmelden,
        )
        if gleich:
            meldungen.append(f"{titel} unverändert: {name}")
            continue
        if trocken:
            meldungen.append(f"[trocken] {titel} würde eingerichtet: {name} → {befehl}")
            continue
        if not pythonw.is_file():
            raise RuntimeError(
                f"pythonw.exe fehlt neben {python} — ohne sie öffnet die geplante Aufgabe bei jedem Lauf ein Fenster."
            )
        if ist_anmelden:
            ergebnis = _windows_anmelde_aufgabe_anlegen(name, str(pythonw), argumente)
        else:
            ergebnis = _schtasks("/Create", "/F", *zeitplan, "/TN", name, "/TR", befehl)
        if ergebnis.returncode != 0:
            raise RuntimeError(f"schtasks /Create ({name}): {(ergebnis.stderr or ergebnis.stdout).strip()[:300]}")
        meldungen.append(f"{titel} eingerichtet: {name} → {befehl}")
    return "\n".join(meldungen)


# --- CLI ------------------------------------------------------------------------


def _repo(args: argparse.Namespace) -> Path:
    if args.repo_dir:
        return Path(args.repo_dir).resolve()
    if os.environ.get("TO_SPAWN_REPO"):
        return Path(os.environ["TO_SPAWN_REPO"]).resolve()
    return config.repo_wurzel()


def richte_parser_ein(unter: Any) -> None:
    p = unter.add_parser(
        "taktgeber",
        help="Zustandsloser Taktgeber: schließen, Staging-Label setzen, wiedereröffnete Tickets wecken (#906)",
    )
    p.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts schreiben oder wecken")
    p.add_argument("--repo-dir", default="", help="Git-Repo (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)")
    p.add_argument("--gh-repo", default="", help="owner/name (Vorgabe: aus origin)")

    p_e = unter.add_parser(
        "taktgeber-einrichten",
        help="Zustandslosen Taktgeber alle 5 min einrichten (Linux: Cron, Windows: Anmelde- + Takt-Aufgabe)",
    )
    p_e.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    p_e.add_argument("--repo-dir", default="", help="Git-Repo (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)")
    p_e.add_argument("--trocken", action="store_true", help="nur zeigen, nichts schreiben")


def _cli_zeilen_ausgeben(zeilen: Sequence[str], *, spec: int, in_datei: bool, kopf: str | None = None) -> None:
    """Gibt CLI-Zeilen auf stdout aus (sofern vorhanden) und schreibt bei ``in_datei`` ins Lauf-Log."""
    if sys.stdout is not None:
        for zeile in zeilen:
            print(zeile)
    if in_datei:
        log_zeilen = [kopf, *zeilen] if kopf else list(zeilen)
        if log_zeilen:
            _lauf_log_schreiben(spec, log_zeilen)


def cli_lauf(args: argparse.Namespace) -> int:
    if args.befehl == "taktgeber-einrichten":
        try:
            if pc_fenster.am_pc():
                print(windows_aufgaben_einrichten(args.spec, _repo(args), trocken=args.trocken))
            else:
                print(cron_einrichten(args.spec, _repo(args), trocken=args.trocken))
        except RuntimeError as fehler:
            print(f"FEHLER: {fehler}")
            return 1
        return 0

    mit_datei_log = pc_fenster.am_pc() or sys.stdout is None
    try:
        repo = _repo(args)
        gh_repo = args.gh_repo or gh.repo_aus_origin(repo, fallback="")
        if not gh_repo:
            meldung = f"FEHLER: Taktgeber #{args.spec}: GitHub-Repo unbekannt — --gh-repo owner/name angeben."
            _cli_zeilen_ausgeben([meldung], spec=args.spec, in_datei=mit_datei_log)
            return 2
        erg = lauf(repo, args.spec, gh_repo, dry_run=args.dry_run)
    except Exception as fehler:
        log.exception("Taktgeber #%s: unerwarteter Fehler", args.spec)
        meldung = f"FEHLER: Taktgeber #{args.spec}: {type(fehler).__name__}: {fehler}"
        _cli_zeilen_ausgeben([meldung], spec=args.spec, in_datei=mit_datei_log)
        return 1

    if not erg.ok:
        log.error("Taktgeber #%s: %s", args.spec, erg.fehler)
        meldung = f"FEHLER: Taktgeber #{args.spec}: {erg.fehler}"
        _cli_zeilen_ausgeben([meldung], spec=args.spec, in_datei=mit_datei_log)
        return 1

    modus = ", Probe" if args.dry_run else ""
    kopf = f"Taktgeber #{args.spec} ({gh_repo}{modus}): {len(erg.aktionen)} Ticket(s), Exit 0"
    zeilen = [f"#{eintrag.ticket}: {eintrag.aktion} — {eintrag.grund}" for eintrag in erg.aktionen]
    _cli_zeilen_ausgeben(zeilen, spec=args.spec, in_datei=mit_datei_log, kopf=kopf)
    return 0
