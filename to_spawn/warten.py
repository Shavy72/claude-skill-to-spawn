"""Abbruchfester Warte-Schritt für Bau-Sessions (#286, Anomalie 8).

Ein Warte-Befehl (z. B. Warteschlange oder externer Lauf) hängt nicht mehr an
einem einzelnen Hintergrund-Befehl der Session:

- Zustand liegt atomar in ``<repo>/.to-spawn/warten/<ticket>.json`` (unversioniert
  unter ``.to-spawn/``, geschützt über :func:`sperre.halte` und
  :func:`manifest.schreibe_atomar`).
- Mit ``--hintergrund`` startet ein entkoppelter, fensterloser Läufer
  (:func:`prozessbaum.ohne_fenster`), der den Warte-Befehl ausführt; ein Weckruf
  (``ScheduleWakeup``) fragt per ``to_spawn.py warten <N> --pruefen`` nach.
- Wird der Warte-Befehl (Kindprozess oder der Hintergrund-Läufer selbst) von außen
  beendet (z. B. ``SIGKILL`` / Speicherknappheit), geht der Stand nicht verloren:
  jeder Abbruch wird mit Zeitstempel und freiem Speicher sowohl in der
  Zustandsdatei als auch im lokalen Geräte-Logbuch festgehalten (Grundlage für
  Lastenheft L7 A37/N17; liegt unter ``~/.local/state/to-spawn/geraete-logbuch/``,
  nie im öffentlichen Repo).
- Nach einer Pause (:data:`PAUSE_VORGABE_S`) startet das Warten von selbst neu,
  höchstens :data:`MAX_VERSUCHE_VORGABE`-mal. Danach wird der Zustand auf
  ``blockiert`` gesetzt und ein Eintrag ``typ="blockiert"`` ins Bau-Log für den
  Aufseher geschrieben — 0 Fragen an den Nutzer.

Exit-Codes: 0 fertig · 1 regulär fehlgeschlagen · 2 kein/unlesbarer Zustand ·
3 läuft noch (oder nach Abbruch neu gestartet) · 4 blockiert nach N Abbrüchen ·
5 falscher Aufruf.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import bau_log, config, konsole, manifest, prozessbaum, speicher, sperre, ssh_beobachten, starter

log = logging.getLogger("to_spawn.warten")

#: Standard-Höchstzahl der Versuche (Erstlauf + Neustarts nach Abbruch von außen).
MAX_VERSUCHE_VORGABE = 3
#: Pause in Sekunden vor einem Neustart nach Abbruch von außen.
PAUSE_VORGABE_S = 30.0
#: Höchste Zahl gespeicherter Ausgabe-Zeichen in der Zustandsdatei.
AUSGABE_MAX = 2000
#: Wartezeit auf die Zustands-Sperre (Sekunden).
SPERRE_WARTE_S = 5.0

EXIT_OK = 0
EXIT_FEHLER = 1
EXIT_UNBEKANNT = 2
EXIT_LAEUFT = 3
EXIT_BLOCKIERT = 4
EXIT_AUFRUF = 5

#: Env-Naht für Tests und eigene Ablage des lokalen Geräte-Logbuchs (L7 N17).
LOGBUCH_ENV = "TO_SPAWN_GERAETE_LOGBUCH"
#: Relativer Ordner der Warte-Zustandsdateien unter einem Repo/Worktree.
WARTEN_ORDNER = Path(".to-spawn") / "warten"
#: Endzustände, bei deren Erreichen ein aktiver Weckruf fällig ist (#783).
ENDZUSTAENDE: frozenset[str] = frozenset({"fertig", "fehlgeschlagen", "blockiert"})

Status = Literal["laeuft", "fertig", "fehlgeschlagen", "blockiert"]

_SKILL_WURZEL = Path(__file__).resolve().parent.parent
_TO_SPAWN_CLI = _SKILL_WURZEL / "to_spawn.py"
_SAUBER_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_ABBRUCH_CODES = frozenset({0x40010004, 0xC000013A})


@dataclass(frozen=True)
class Ergebnis:
    """Rückgabe aller öffentlichen Türen dieses Moduls."""

    exit: int
    status: str
    zeile: str


# --- Pfade & Speicher-Messung ---------------------------------------------------------


def zustand_pfad(repo: Path, ticket: str | int, name: str = "") -> Path:
    """Zustandsdatei ``<repo>/.to-spawn/warten/<ticket>[-<name>].json``."""
    zusatz = _SAUBER_NAME.sub("-", name.strip()).strip("-") if name.strip() else ""
    dateiname = f"{ticket}-{zusatz}.json" if zusatz else f"{ticket}.json"
    return repo / WARTEN_ORDNER / dateiname


def _sperr_pfad(datei: Path) -> Path:
    return datei.with_suffix(".sperre")


def _laeufer_log_pfad(datei: Path) -> Path:
    return datei.with_suffix(".log")


def geraet_name() -> str:
    """Dateisicherer Gerätename für das lokale Geräte-Logbuch (L7 A37)."""
    try:
        roh = socket.gethostname()
    except OSError:
        roh = ""
    sauber = _SAUBER_NAME.sub("-", roh.strip()).strip("-")
    return sauber or "unbekannt"


def logbuch_pfad() -> Path:
    """Pfad des lokalen Geräte-Logbuchs (nie im öffentlichen Repo, L7 N17).

    Heimat-Ordner wird erst bei Gebrauch über :func:`config.heim_ordner` aufgelöst (#212).
    """
    eigen = os.environ.get(LOGBUCH_ENV, "").strip()
    if eigen:
        return Path(eigen).expanduser()
    return config.heim_ordner() / ".local" / "state" / "to-spawn" / "geraete-logbuch" / f"{geraet_name()}.jsonl"


_WindowsSpeicherStatus = speicher._WindowsSpeicherStatus


def _windows_speicher_mib() -> tuple[int | None, int | None]:
    """(frei_mib, gesamt_mib) über :func:`speicher.windows_speicher_mib` (eine Quelle, #802)."""
    return speicher.windows_speicher_mib()


def freier_speicher_mib() -> int | None:
    """Freier Arbeitsspeicher in MiB: Linux und Windows über :func:`speicher.system_speicher_mib`."""
    return speicher.system_speicher_mib()[0]


def gesamt_speicher_mib() -> int | None:
    """Gesamtspeicher in MiB: Linux und Windows über :func:`speicher.system_speicher_mib`."""
    return speicher.system_speicher_mib()[1]


def _speicher_text(frei: int | None) -> str:
    return f"{frei} MiB frei" if frei is not None else "Speicher unbekannt"


# --- Prozess-Prüfung & Abbruch-Erkennung ----------------------------------------------


def _prozess_start(pid: int) -> int | None:
    """Startzeit (Linux ``/proc/<pid>/stat``) gegen PID-Wiederverwendung; sonst ``None``."""
    return prozessbaum.startzeit(pid)


def _prozess_aktiv(pid: Any, erwartet_start: Any = None) -> bool:
    """Lebt der gemerkte Prozess noch aktiv (kein Zombie, keine recycelte PID)?"""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    start = erwartet_start if isinstance(erwartet_start, int) and not isinstance(erwartet_start, bool) else None
    try:
        return prozessbaum.aktiv(pid, erwartet_start=start)
    except OSError:
        return False


def ist_abbruch_exit(
    code: int,
    *,
    plattform: str | None = None,
    befehl: Sequence[str] | None = None,
) -> bool:
    """Wurde der Prozess von außen abgebrochen (Signal / harter Abbruch / SSH-Abbruch), nicht regulär beendet?"""
    ziel = plattform or sys.platform
    if code == 0:
        return False
    if code == ssh_beobachten.SSH_EXIT_ABBRUCH and ssh_beobachten.ist_ssh_befehl(befehl, plattform=ziel):
        return True
    if code < 0:
        return True
    if 128 < code <= 192:  # POSIX-Shell-Signalcodes (128 + Signal, z. B. 137 SIGKILL, 143 SIGTERM)
        return True
    if ziel == "win32" and code == signal.SIGTERM.value:
        # Windows: ``os.kill(pid, signal.SIGTERM)`` ruft ``TerminateProcess(handle, 15)``.
        return True
    return code in _WINDOWS_ABBRUCH_CODES or code >= 0xC0000000


def _abbruch_grund_fuer_exit(code: int, befehl: Sequence[str] | None = None) -> str:
    if code == ssh_beobachten.SSH_EXIT_ABBRUCH and ssh_beobachten.ist_ssh_befehl(befehl):
        return f"SSH-Verbindung abgebrochen (Exit {code}) — verbinde neu"
    if code < 0:
        sig_nr = -code
        try:
            name = signal.Signals(sig_nr).name
            return f"Warte-Befehl von außen beendet (Signal {sig_nr} / {name})"
        except ValueError:
            return f"Warte-Befehl von außen beendet (Signal {sig_nr})"
    if 128 < code <= 192:
        sig_nr = code - 128
        return f"Warte-Befehl von außen beendet (Exit {code}, Signal {sig_nr})"
    return f"Warte-Befehl von außen beendet (Exit {code})"


# --- Zustand & Geräte-Logbuch ---------------------------------------------------------


def _lies_zustand(datei: Path) -> dict[str, Any] | None:
    if not datei.is_file():
        return None
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Warte-Zustand %s unlesbar: %s", datei, fehler)
        return None
    return daten if isinstance(daten, dict) else None


def _schreibe_zustand(datei: Path, daten: dict[str, Any]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    daten["aktualisiert_am"] = bau_log.jetzt()
    for versuch in range(4):
        try:
            manifest.schreibe_atomar(datei, daten)
            return
        except PermissionError:
            if sys.platform != "win32" or versuch == 3:
                raise
            time.sleep(0.02)


def _kurz_ausgabe(text: str) -> str:
    sauber = text.strip()
    if len(sauber) <= AUSGABE_MAX:
        return sauber
    return "…\n" + sauber[-AUSGABE_MAX:]


def letzte_zeile(text: str, *, max_zeichen: int = 200) -> str:
    """Letzte nicht-leere Zeile aus einer Warte-Ausgabe, auf ``max_zeichen`` gekürzt."""
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    return zeilen[-1][:max_zeichen] if zeilen else ""


def _letzte_zeile(text: str) -> str:
    return letzte_zeile(text)


def zustaende_fuer_ticket(repo: Path, ticket: str | int) -> list[tuple[Path, dict[str, Any]]]:
    """Alle lesbaren Warte-Zustandsdateien eines Tickets (Ticket-Worktree + Hauptbaum, #783).

    Liefert ``[(pfad, daten), ...]`` für ``<ticket>.json`` und ``<ticket>-<name>.json``.
    Unlesbare Dateien werden über :func:`_lies_zustand` gewarnt und übersprungen; fremde
    Nummern mit gemeinsamem Präfix (z. B. ``<ticket>0.json``) werden nie mitgelesen.
    """
    nummer = str(ticket).strip()
    if not nummer.isdigit():
        return []
    orte: list[Path] = []
    gesehen: set[Path] = set()
    for wurzel in (Path(config.worktree_pfad(nummer, repo)).expanduser(), repo):
        ordner = wurzel / WARTEN_ORDNER
        if not ordner.is_dir():
            continue
        try:
            echt = ordner.resolve()
        except OSError:
            echt = ordner
        if echt in gesehen:
            continue
        gesehen.add(echt)
        orte.append(echt)
    treffer: list[tuple[Path, dict[str, Any]]] = []
    for ordner in orte:
        dateien: list[Path] = []
        haupt = ordner / f"{nummer}.json"
        if haupt.is_file():
            dateien.append(haupt)
        dateien.extend(p for p in sorted(ordner.glob(f"{nummer}-*.json")) if p.is_file())
        for datei in dateien:
            daten = _lies_zustand(datei)
            if daten is None:
                continue
            datei_ticket = str(daten.get("ticket") or nummer).strip()
            if datei_ticket != nummer:
                continue
            treffer.append((datei, daten))
    return treffer


def logbuch_schreiben(
    ticket: str | int,
    *,
    versuch: int,
    max_versuche: int,
    grund: str,
    befehl: Sequence[str],
    exit_code: int | None = None,
    frei_mib: int | None = None,
    gesamt_mib: int | None = None,
    pfad: Path | None = None,
) -> dict[str, Any]:
    """Einen Warte-Abbruch mit Zeit und freiem Speicher im Geräte-Logbuch festhalten (L7)."""
    frei = freier_speicher_mib() if frei_mib is None else frei_mib
    gesamt = gesamt_speicher_mib() if gesamt_mib is None else gesamt_mib
    eintrag: dict[str, Any] = {
        "ts": bau_log.jetzt(),
        "ereignis": "warte_abbruch",
        "geraet": geraet_name(),
        "plattform": sys.platform,
        "ticket": str(ticket),
        "versuch": versuch,
        "max_versuche": max_versuche,
        "frei_mib": frei,
        "gesamt_mib": gesamt,
        "exit_code": exit_code,
        "grund": grund,
        "befehl": list(befehl),
    }
    ziel = pfad if pfad is not None else logbuch_pfad()
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with ziel.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Geräte-Logbuch %s nicht beschreibbar: %s", ziel, fehler)
    return eintrag


def logbuch_lesen(pfad: Path | None = None) -> list[dict[str, Any]]:
    """Alle gültigen Zeilen eines Geräte-Logbuchs lesen; fehlende Datei → leer."""
    ziel = pfad if pfad is not None else logbuch_pfad()
    if not ziel.is_file():
        return []
    eintraege: list[dict[str, Any]] = []
    try:
        zeilen = ziel.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as fehler:
        log.warning("Geräte-Logbuch %s nicht lesbar: %s", ziel, fehler)
        return []
    for nr, roh in enumerate(zeilen, start=1):
        roh = roh.strip()
        if not roh:
            continue
        try:
            obj = json.loads(roh)
        except ValueError:
            log.warning("Kaputte Logbuch-Zeile %s:%d übersprungen.", ziel, nr)
            continue
        if isinstance(obj, dict):
            eintraege.append(obj)
    return eintraege


def _log_ziel(repo: Path, gespeichertes_ziel: str = "") -> Path:
    """Ziel-Repo fürs Bau-Log: gespeicherter Pfad → ``bau_log.log_repo`` → ``repo``."""
    if gespeichertes_ziel:
        pfad = Path(gespeichertes_ziel)
        if pfad.is_dir():
            return pfad
    return bau_log.log_repo(fallback=repo) or repo


def _blockiert_melden(
    repo: Path,
    ticket: str | int,
    *,
    versuche: int,
    grund: str,
    frei_mib: int | None,
    log_repo_text: str = "",
) -> None:
    """Eintrag ``blockiert`` ins Bau-Log für den Aufseher schreiben (0 Fragen an den Nutzer)."""
    ziel = _log_ziel(repo, log_repo_text)
    meldung = (
        f"Warte-Befehl nach {versuche} Abbruch-Versuchen von außen blockiert "
        f"(letzter Grund: {grund}; {_speicher_text(frei_mib)})"
    )
    try:
        bau_log.schreibe(
            "blockiert",
            repo=ziel,
            ticket=ticket,
            wer="warten",
            was=meldung,
            session=f"warten-{ticket}",
            commit=bau_log.git_kopf_kurz(ziel, ticket),
            grund=meldung,
            kontextstand=0,
            versuche=versuche,
            frei_mib=frei_mib,
        )
    except (OSError, ValueError, TimeoutError) as fehler:
        log.warning("Bau-Log #%s nicht beschreibbar (%s) — blockiert nur in Zustandsdatei.", ticket, fehler)


def _abbruch_eintrag(eintrag: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": eintrag["ts"],
        "versuch": eintrag["versuch"],
        "frei_mib": eintrag["frei_mib"],
        "gesamt_mib": eintrag["gesamt_mib"],
        "exit_code": eintrag["exit_code"],
        "grund": eintrag["grund"],
    }


def _grund_zustand(
    ticket: str | int,
    name: str,
    befehl: Sequence[str],
    arbeitsordner: Path,
    log_ziel: Path,
    *,
    modus: str,
    versuch: int,
    max_versuche: int,
    pause_s: float,
    abbrueche: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    jetzt = bau_log.jetzt()
    return {
        "ticket": str(ticket),
        "name": name,
        "befehl": list(befehl),
        "arbeitsordner": str(arbeitsordner),
        "log_repo": str(log_ziel),
        "status": "laeuft",
        "modus": modus,
        "pid": None,
        "pid_start": None,
        "kind_pid": None,
        "kind_start": None,
        "versuch": versuch,
        "max_versuche": max_versuche,
        "pause_s": pause_s,
        "gestartet_am": jetzt,
        "aktualisiert_am": jetzt,
        "ergebnis_exit": None,
        "ergebnis_text": "",
        "abbrueche": list(abbrueche or []),
    }


def _waise_beenden(stand: dict[str, Any]) -> None:
    """Falls nach dem Tod des Läufers noch das eigene Kind lebt, sauber beenden."""
    kind = stand.get("kind_pid")
    if isinstance(kind, int) and kind != stand.get("pid") and _prozess_aktiv(kind, stand.get("kind_start")):
        try:
            prozessbaum.senden([kind], signal.SIGTERM)
        except OSError as fehler:
            log.warning("Verwaistes Warte-Kind %d nicht beendet: %s", kind, fehler)


# --- Hintergrund-Läufer starten -------------------------------------------------------


def _starte_laeufer_prozess(state_datei: Path, ticket: str | int, arbeitsordner: Path) -> subprocess.Popen[bytes]:
    """Startet den entkoppelten, fensterlosen Läufer (Linux: eigene Sitzung, Windows: ohne Fenster)."""
    log_datei = _laeufer_log_pfad(state_datei)
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_TO_SPAWN_CLI),
        "warten",
        str(ticket),
        "--laeufer",
        str(state_datei),
    ]
    eigen: dict[str, Any] = prozessbaum.ohne_fenster() if sys.platform == "win32" else {"start_new_session": True}
    with log_datei.open("ab") as log_fh:
        return starter.starte(
            cmd,
            cwd=arbeitsordner,
            notnagel=None,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **eigen,
        )


# --- Überwachungs-Schleife (synchron & Läufer) ----------------------------------------


def _ueberwachen(
    repo: Path,
    state_datei: Path,
    stand: dict[str, Any],
    *,
    schlafen: Callable[[float], object] = time.sleep,
) -> Ergebnis:
    """Führt ``stand["befehl"]`` mit automatischem Neustart nach Abbruch von außen aus."""
    ticket = str(stand["ticket"])
    befehl = [str(teil) for teil in stand.get("befehl") or []]
    arbeitsordner = Path(str(stand.get("arbeitsordner") or repo))
    log_repo_text = str(stand.get("log_repo") or "")
    max_versuche = max(1, int(stand.get("max_versuche") or MAX_VERSUCHE_VORGABE))
    pause_s = max(0.0, float(stand.get("pause_s", PAUSE_VORGABE_S)))
    versuch = max(1, int(stand.get("versuch") or 1))
    eigen_pid = os.getpid()
    eigen_start = _prozess_start(eigen_pid)

    while True:
        try:
            kind = starter.starte(
                befehl,
                cwd=arbeitsordner,
                notnagel=None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **konsole.UNTERPROZESS_TEXT,
                **prozessbaum.fensterlos(),
            )
        except OSError as fehler:
            text = f"Warte-Befehl nicht startbar: {fehler}"
            with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
                stand.update(
                    status="fehlgeschlagen",
                    pid=eigen_pid,
                    pid_start=eigen_start,
                    kind_pid=None,
                    kind_start=None,
                    versuch=versuch,
                    ergebnis_exit=EXIT_FEHLER,
                    ergebnis_text=text,
                )
                _schreibe_zustand(state_datei, stand)
            return Ergebnis(EXIT_FEHLER, "fehlgeschlagen", f"Ticket #{ticket}: {text}")

        kind_start = _prozess_start(kind.pid)
        with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
            stand.update(
                status="laeuft",
                pid=eigen_pid,
                pid_start=eigen_start,
                kind_pid=kind.pid,
                kind_start=kind_start,
                versuch=versuch,
            )
            _schreibe_zustand(state_datei, stand)

        ausgabe, _ = kind.communicate()
        code = int(kind.returncode)
        kurz = _kurz_ausgabe(ausgabe or "")
        letzte = _letzte_zeile(kurz)

        if code == 0:
            with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
                stand.update(
                    status="fertig",
                    kind_pid=None,
                    kind_start=None,
                    versuch=versuch,
                    ergebnis_exit=0,
                    ergebnis_text=kurz,
                )
                _schreibe_zustand(state_datei, stand)
            zusatz = f" — {letzte}" if letzte else ""
            return Ergebnis(
                EXIT_OK,
                "fertig",
                f"Ticket #{ticket}: Warten fertig (Versuch {versuch}/{max_versuche}){zusatz}",
            )

        if ist_abbruch_exit(code, befehl=befehl):
            grund = _abbruch_grund_fuer_exit(code, befehl=befehl)
            eintrag = logbuch_schreiben(
                ticket,
                versuch=versuch,
                max_versuche=max_versuche,
                grund=grund,
                befehl=befehl,
                exit_code=code,
            )
            log.warning(
                "Ticket #%s: %s (Versuch %d/%d, %s)",
                ticket,
                grund,
                versuch,
                max_versuche,
                _speicher_text(eintrag["frei_mib"]),
            )
            stand.setdefault("abbrueche", []).append(_abbruch_eintrag(eintrag))
            if versuch < max_versuche:
                with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
                    stand.update(
                        status="laeuft",
                        kind_pid=None,
                        kind_start=None,
                        versuch=versuch,
                    )
                    _schreibe_zustand(state_datei, stand)
                log.info(
                    "Ticket #%s: warte %.1f s vor Neustart (Versuch %d/%d)",
                    ticket,
                    pause_s,
                    versuch + 1,
                    max_versuche,
                )
                schlafen(pause_s)
                versuch += 1
                continue

            with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
                stand.update(
                    status="blockiert",
                    kind_pid=None,
                    kind_start=None,
                    versuch=versuch,
                    ergebnis_exit=code,
                    ergebnis_text=grund,
                )
                _schreibe_zustand(state_datei, stand)
            _blockiert_melden(
                repo,
                ticket,
                versuche=versuch,
                grund=grund,
                frei_mib=eintrag["frei_mib"],
                log_repo_text=log_repo_text,
            )
            return Ergebnis(
                EXIT_BLOCKIERT,
                "blockiert",
                f"Ticket #{ticket}: Warten nach {versuch}/{max_versuche} Abbrüchen blockiert "
                f"({_speicher_text(eintrag['frei_mib'])}) — Eintrag für Aufseher geschrieben",
            )

        with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
            stand.update(
                status="fehlgeschlagen",
                kind_pid=None,
                kind_start=None,
                versuch=versuch,
                ergebnis_exit=code,
                ergebnis_text=kurz,
            )
            _schreibe_zustand(state_datei, stand)
        zusatz = f" — {letzte}" if letzte else ""
        return Ergebnis(
            EXIT_FEHLER,
            "fehlgeschlagen",
            f"Ticket #{ticket}: Warte-Befehl regulär fehlgeschlagen (Exit {code}, Versuch {versuch}/{max_versuche}){zusatz}",
        )


# --- Öffentliche Türen ----------------------------------------------------------------


def _abgebrochenen_zustand_behandeln(
    repo: Path,
    state_datei: Path,
    stand: dict[str, Any],
    *,
    grund: str,
    hintergrund_neustart: bool,
    schlafen: Callable[[float], object] = time.sleep,
) -> tuple[Ergebnis | None, dict[str, Any]]:
    """Protokolliert einen erkannten Wegfall; startet im Hintergrund neu oder schaltet auf ``blockiert``.

    Im synchronen Weg (``hintergrund_neustart=False``) gibt es ``(None, stand)`` zurück, wenn noch
    Versuche übrig sind — der Aufrufer geht dann nach der Pause direkt in :func:`_ueberwachen`.
    """
    ticket = str(stand["ticket"])
    befehl = [str(teil) for teil in stand.get("befehl") or []]
    max_versuche = max(1, int(stand.get("max_versuche") or MAX_VERSUCHE_VORGABE))
    pause_s = max(0.0, float(stand.get("pause_s", PAUSE_VORGABE_S)))
    versuch = max(1, int(stand.get("versuch") or 1))
    log_repo_text = str(stand.get("log_repo") or "")

    _waise_beenden(stand)
    eintrag = logbuch_schreiben(
        ticket,
        versuch=versuch,
        max_versuche=max_versuche,
        grund=grund,
        befehl=befehl,
        exit_code=None,
    )
    stand.setdefault("abbrueche", []).append(_abbruch_eintrag(eintrag))

    if versuch >= max_versuche or not befehl:
        with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
            stand.update(
                status="blockiert",
                pid=None,
                pid_start=None,
                kind_pid=None,
                kind_start=None,
                versuch=versuch,
                ergebnis_text=grund,
            )
            _schreibe_zustand(state_datei, stand)
        _blockiert_melden(
            repo,
            ticket,
            versuche=versuch,
            grund=grund,
            frei_mib=eintrag["frei_mib"],
            log_repo_text=log_repo_text,
        )
        return (
            Ergebnis(
                EXIT_BLOCKIERT,
                "blockiert",
                f"Ticket #{ticket}: Warten nach {versuch}/{max_versuche} Abbrüchen blockiert "
                f"({_speicher_text(eintrag['frei_mib'])}) — Eintrag für Aufseher geschrieben",
            ),
            stand,
        )

    with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
        stand.update(status="laeuft", pid=None, pid_start=None, kind_pid=None, kind_start=None)
        _schreibe_zustand(state_datei, stand)

    log.info(
        "Ticket #%s: Warte-Prozess abgebrochen (%s, %s) — Pause %.1f s vor Versuch %d/%d",
        ticket,
        grund,
        _speicher_text(eintrag["frei_mib"]),
        pause_s,
        versuch + 1,
        max_versuche,
    )
    schlafen(pause_s)
    stand["versuch"] = versuch + 1

    if not hintergrund_neustart:
        return None, stand

    arbeitsordner = Path(str(stand.get("arbeitsordner") or repo))
    with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
        stand.update(status="laeuft", modus="hintergrund", kind_pid=None, kind_start=None)
        _schreibe_zustand(state_datei, stand)
        proc = _starte_laeufer_prozess(state_datei, ticket, arbeitsordner)
        stand["pid"] = proc.pid
        stand["pid_start"] = _prozess_start(proc.pid)
        _schreibe_zustand(state_datei, stand)
    return (
        Ergebnis(
            EXIT_LAEUFT,
            "laeuft",
            f"Ticket #{ticket}: Abbruch erkannt ({_speicher_text(eintrag['frei_mib'])}) — "
            f"nach Pause neu gestartet (Versuch {stand['versuch']}/{max_versuche}, PID {proc.pid})",
        ),
        stand,
    )


def ausfuehren(
    repo: Path,
    ticket: str | int,
    befehl: Sequence[str],
    *,
    name: str = "",
    max_versuche: int = MAX_VERSUCHE_VORGABE,
    pause_s: float = PAUSE_VORGABE_S,
    arbeitsordner: Path | None = None,
    neu: bool = False,
    schlafen: Callable[[float], object] = time.sleep,
) -> Ergebnis:
    """Warte-Befehl synchron überwachen (Zustand in Datei, Neustart mit Pause bis N, dann ``blockiert``)."""
    befehlsliste = [str(teil) for teil in befehl]
    if not befehlsliste:
        return Ergebnis(EXIT_AUFRUF, "fehler", "Warte-Befehl fehlt — nach `--` angeben.")
    ordner = (arbeitsordner or repo).resolve()
    datei = zustand_pfad(repo, ticket, name)
    log_ziel = _log_ziel(repo)

    stand: dict[str, Any] | None = None
    if not neu:
        with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
            alt = _lies_zustand(datei)
        if alt is not None and alt.get("befehl") == befehlsliste:
            status = str(alt.get("status") or "")
            if status == "fertig":
                letzte = _letzte_zeile(str(alt.get("ergebnis_text") or ""))
                zusatz = f" — {letzte}" if letzte else ""
                return Ergebnis(EXIT_OK, "fertig", f"Ticket #{ticket}: Warten bereits fertig{zusatz}")
            if status == "blockiert":
                return Ergebnis(
                    EXIT_BLOCKIERT,
                    "blockiert",
                    f"Ticket #{ticket}: Warten bereits blockiert ({alt.get('ergebnis_text') or 'Obergrenze erreicht'})",
                )
            if status == "laeuft":
                if _prozess_aktiv(alt.get("pid"), alt.get("pid_start")):
                    return Ergebnis(
                        EXIT_LAEUFT,
                        "laeuft",
                        f"Ticket #{ticket}: Warte-Befehl läuft bereits (Versuch {alt.get('versuch')}/{alt.get('max_versuche')}, PID {alt.get('pid')})",
                    )
                alt["max_versuche"] = max(1, max_versuche)
                alt["pause_s"] = max(0.0, pause_s)
                alt["modus"] = "synchron"
                erg, alt = _abgebrochenen_zustand_behandeln(
                    repo,
                    datei,
                    alt,
                    grund=f"Vorheriger Warte-Prozess von außen beendet (PID {alt.get('pid')} nicht mehr aktiv)",
                    hintergrund_neustart=False,
                    schlafen=schlafen,
                )
                if erg is not None:
                    return erg
                stand = alt

    if stand is None:
        stand = _grund_zustand(
            ticket,
            name,
            befehlsliste,
            ordner,
            log_ziel,
            modus="synchron",
            versuch=1,
            max_versuche=max(1, max_versuche),
            pause_s=max(0.0, pause_s),
        )
        with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
            _schreibe_zustand(datei, stand)
    return _ueberwachen(repo, datei, stand, schlafen=schlafen)


def starte_hintergrund(
    repo: Path,
    ticket: str | int,
    befehl: Sequence[str],
    *,
    name: str = "",
    max_versuche: int = MAX_VERSUCHE_VORGABE,
    pause_s: float = PAUSE_VORGABE_S,
    arbeitsordner: Path | None = None,
    neu: bool = False,
    schlafen: Callable[[float], object] = time.sleep,
) -> Ergebnis:
    """Warte-Befehl entkoppelt im Hintergrund starten; Weckruf fragt per :func:`pruefen` nach."""
    befehlsliste = [str(teil) for teil in befehl]
    if not befehlsliste:
        return Ergebnis(EXIT_AUFRUF, "fehler", "Warte-Befehl fehlt — nach `--` angeben.")
    ordner = (arbeitsordner or repo).resolve()
    datei = zustand_pfad(repo, ticket, name)
    log_ziel = _log_ziel(repo)

    if not neu:
        with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
            alt = _lies_zustand(datei)
        if alt is not None and alt.get("befehl") == befehlsliste:
            status = str(alt.get("status") or "")
            if status == "fertig":
                letzte = _letzte_zeile(str(alt.get("ergebnis_text") or ""))
                zusatz = f" — {letzte}" if letzte else ""
                return Ergebnis(EXIT_OK, "fertig", f"Ticket #{ticket}: Warten bereits fertig{zusatz}")
            if status == "blockiert":
                return Ergebnis(
                    EXIT_BLOCKIERT,
                    "blockiert",
                    f"Ticket #{ticket}: Warten bereits blockiert ({alt.get('ergebnis_text') or 'Obergrenze erreicht'})",
                )
            if status == "laeuft":
                if _prozess_aktiv(alt.get("pid"), alt.get("pid_start")):
                    return Ergebnis(
                        EXIT_LAEUFT,
                        "laeuft",
                        f"Ticket #{ticket}: wartet noch (Versuch {alt.get('versuch')}/{alt.get('max_versuche')}, PID {alt.get('pid')})",
                    )
                alt["max_versuche"] = max(1, max_versuche)
                alt["pause_s"] = max(0.0, pause_s)
                erg, _ = _abgebrochenen_zustand_behandeln(
                    repo,
                    datei,
                    alt,
                    grund=f"Hintergrund-Läufer von außen beendet (PID {alt.get('pid')} nicht mehr aktiv)",
                    hintergrund_neustart=True,
                    schlafen=schlafen,
                )
                assert erg is not None
                return erg

    stand = _grund_zustand(
        ticket,
        name,
        befehlsliste,
        ordner,
        log_ziel,
        modus="hintergrund",
        versuch=1,
        max_versuche=max(1, max_versuche),
        pause_s=max(0.0, pause_s),
    )
    with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
        _schreibe_zustand(datei, stand)
        proc = _starte_laeufer_prozess(datei, ticket, ordner)
        stand["pid"] = proc.pid
        stand["pid_start"] = _prozess_start(proc.pid)
        _schreibe_zustand(datei, stand)
    return Ergebnis(
        EXIT_LAEUFT,
        "laeuft",
        f"Ticket #{ticket}: Warten im Hintergrund gestartet (Versuch 1/{stand['max_versuche']}, PID {proc.pid})",
    )


def pruefen(
    repo: Path,
    ticket: str | int,
    *,
    name: str = "",
    schlafen: Callable[[float], object] = time.sleep,
) -> Ergebnis:
    """Weckruf-Prüfung: Stand aus Datei lesen; toter Läufer → Abbruch loggen, Pause, Neustart bis N."""
    datei = zustand_pfad(repo, ticket, name)
    with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
        stand = _lies_zustand(datei)
    if stand is None:
        return Ergebnis(EXIT_UNBEKANNT, "unbekannt", f"Ticket #{ticket}: kein Warte-Zustand ({datei})")

    status = str(stand.get("status") or "")
    versuch = int(stand.get("versuch") or 1)
    max_versuche = int(stand.get("max_versuche") or MAX_VERSUCHE_VORGABE)
    letzte = _letzte_zeile(str(stand.get("ergebnis_text") or ""))
    zusatz = f" — {letzte}" if letzte else ""

    if status == "fertig":
        return Ergebnis(
            EXIT_OK,
            "fertig",
            f"Ticket #{ticket}: Warten fertig (Versuch {versuch}/{max_versuche}){zusatz}",
        )
    if status == "fehlgeschlagen":
        return Ergebnis(
            EXIT_FEHLER,
            "fehlgeschlagen",
            f"Ticket #{ticket}: Warte-Befehl regulär fehlgeschlagen "
            f"(Exit {stand.get('ergebnis_exit')}, Versuch {versuch}/{max_versuche}){zusatz}",
        )
    if status == "blockiert":
        return Ergebnis(
            EXIT_BLOCKIERT,
            "blockiert",
            f"Ticket #{ticket}: Warten blockiert nach {versuch}/{max_versuche} Abbrüchen{zusatz}",
        )
    if status == "laeuft":
        if _prozess_aktiv(stand.get("pid"), stand.get("pid_start")):
            abbrueche = len(stand.get("abbrueche") or [])
            return Ergebnis(
                EXIT_LAEUFT,
                "laeuft",
                f"Ticket #{ticket}: wartet noch (Versuch {versuch}/{max_versuche}, PID {stand.get('pid')}, "
                f"{abbrueche} Abbrüche)",
            )
        erg, _ = _abgebrochenen_zustand_behandeln(
            repo,
            datei,
            stand,
            grund=f"Hintergrund-Läufer von außen beendet (PID {stand.get('pid')} nicht mehr aktiv)",
            hintergrund_neustart=True,
            schlafen=schlafen,
        )
        assert erg is not None
        return erg

    return Ergebnis(EXIT_UNBEKANNT, "unbekannt", f"Ticket #{ticket}: unbekannter Warte-Status {status!r}")


def abbruch_melden(
    repo: Path,
    ticket: str | int,
    *,
    grund: str = "Warte-Befehl von außen beendet",
    name: str = "",
    max_versuche: int = MAX_VERSUCHE_VORGABE,
    pause_s: float = PAUSE_VORGABE_S,
    schlafen: Callable[[float], object] = time.sleep,
) -> Ergebnis:
    """Einen von außen (z. B. Claude Code) gemeldeten Abbruch erfassen und ggf. neu starten."""
    datei = zustand_pfad(repo, ticket, name)
    with sperre.halte(_sperr_pfad(datei), warten=SPERRE_WARTE_S):
        stand = _lies_zustand(datei)
    if stand is None:
        stand = _grund_zustand(
            ticket,
            name,
            [],
            repo.resolve(),
            _log_ziel(repo),
            modus="extern",
            versuch=1,
            max_versuche=max(1, max_versuche),
            pause_s=max(0.0, pause_s),
        )
    else:
        if stand.get("status") == "blockiert":
            return Ergebnis(
                EXIT_BLOCKIERT,
                "blockiert",
                f"Ticket #{ticket}: Warten bereits blockiert ({stand.get('ergebnis_text') or grund})",
            )
        stand["max_versuche"] = max(1, max_versuche)
        stand["pause_s"] = max(0.0, pause_s)
    erg, _ = _abgebrochenen_zustand_behandeln(
        repo,
        datei,
        stand,
        grund=grund.strip() or "Warte-Befehl von außen beendet",
        hintergrund_neustart=True,
        schlafen=schlafen,
    )
    assert erg is not None
    return erg


def laeufer_ausfuehren(state_datei: Path) -> int:
    """Einstieg des entkoppelten Hintergrund-Läufers (``--laeufer``)."""
    with sperre.halte(_sperr_pfad(state_datei), warten=SPERRE_WARTE_S):
        stand = _lies_zustand(state_datei)
    if stand is None or stand.get("status") != "laeuft":
        return EXIT_UNBEKANNT
    repo = state_datei.parents[2]  # <repo>/.to-spawn/warten/<datei>
    erg = _ueberwachen(repo, state_datei, stand)
    log.info("%s", erg.zeile)
    return erg.exit


# --- CLI ------------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    """Registriert den Unterbefehl ``to_spawn.py warten``."""
    from .cli_parser import ARGUMENTFEHLER_EXIT

    p = unter.add_parser(
        "warten",
        help="abbruchfester Warte-Schritt mit Zustand in Datei, Neustart nach Pause und Geräte-Logbuch (#286)",
    )
    p.set_defaults(**{ARGUMENTFEHLER_EXIT: EXIT_AUFRUF})
    p.add_argument("ticket", type=int, help="Ticket-Nummer")
    p.add_argument("--name", default="", help="optionaler Kurzname bei mehreren Warte-Schritten je Ticket")
    p.add_argument(
        "--max-versuche",
        type=int,
        default=MAX_VERSUCHE_VORGABE,
        help=f"Höchstzahl Versuche vor „blockiert“ ({MAX_VERSUCHE_VORGABE})",
    )
    p.add_argument(
        "--pause",
        type=float,
        default=PAUSE_VORGABE_S,
        help=f"Pause in Sekunden vor jedem Neustart ({int(PAUSE_VORGABE_S)})",
    )
    modus = p.add_mutually_exclusive_group()
    modus.add_argument(
        "--hintergrund",
        action="store_true",
        help="entkoppelt im Hintergrund starten (Weckruf fragt mit --pruefen nach)",
    )
    modus.add_argument(
        "--pruefen",
        action="store_true",
        help="Zustandsdatei prüfen; toten Läufer nach Pause neu starten oder blockiert melden",
    )
    modus.add_argument(
        "--abbruch",
        action="store_true",
        help="externen Abbruch (z. B. Speicherknappheit) melden, loggen und ggf. neu starten",
    )
    modus.add_argument("--laeufer", type=Path, help=argparse.SUPPRESS)
    p.add_argument("--grund", default="", help="Grund bei --abbruch")
    p.add_argument("--neu", action="store_true", help="vorhandenen Zustand verwerfen und neu beginnen")
    p.add_argument("--repo", type=Path, default=None, help="Repo-Ordner (sonst TO_SPAWN_REPO / Git-Wurzel)")
    p.add_argument("kommando", nargs="*", help="Warte-Befehl hinter `--`")


def _befehl_bereinigen(teile: Sequence[str]) -> list[str]:
    liste = list(teile)
    if liste and liste[0] == "--":
        liste = liste[1:]
    return liste


def lauf(args: argparse.Namespace, repo: Path) -> int:
    """Ausführung des Unterbefehls ``to_spawn.py warten``."""
    if args.laeufer is not None:
        return laeufer_ausfuehren(args.laeufer)
    ziel_repo = args.repo.expanduser().resolve() if args.repo else repo
    if args.pruefen:
        erg = pruefen(ziel_repo, args.ticket, name=args.name)
    elif args.abbruch:
        erg = abbruch_melden(
            ziel_repo,
            args.ticket,
            grund=args.grund or "Warte-Befehl von außen beendet",
            name=args.name,
            max_versuche=args.max_versuche,
            pause_s=args.pause,
        )
    else:
        befehl = _befehl_bereinigen(args.kommando)
        if not befehl:
            print("FEHLER: Warte-Befehl fehlt — nach `--` angeben (oder `--pruefen` nutzen).", file=sys.stderr)
            return EXIT_AUFRUF
        if args.hintergrund:
            erg = starte_hintergrund(
                ziel_repo,
                args.ticket,
                befehl,
                name=args.name,
                max_versuche=args.max_versuche,
                pause_s=args.pause,
                arbeitsordner=Path.cwd(),
                neu=args.neu,
            )
        else:
            erg = ausfuehren(
                ziel_repo,
                args.ticket,
                befehl,
                name=args.name,
                max_versuche=args.max_versuche,
                pause_s=args.pause,
                arbeitsordner=Path.cwd(),
                neu=args.neu,
            )
    print(erg.zeile)
    return erg.exit
