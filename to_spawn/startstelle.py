"""Eine Start-Stelle für Ticket-Sessions (#97, L1 E2-13, App #1390).

Jeder Weg, der eine Bau-Session startet — ``skripte/spawn_srv.sh``, ``spawn_local.ps1``,
Aufpasser (``fenster_starten``), respawn, capo-Folgerunde, ``spawn.neustart`` — öffnet
ein Fenster mit ``skripte/bau.py <N>``. Dort ruft ``main()`` vor dem ersten Claude-Start
genau einmal :func:`betreten`. Darum gibt es keine zweite Stelle, an der geprüft wird
(der Prüf-Test ``tests/test_startstelle_97.py`` wird rot, wenn ``claude`` an ihr vorbei
startet).

:func:`betreten` entscheidet unter einer Sperre je Ticket (Maschine über ``to_spawn.sperre``
und maschinenübergreifend über eine Remote-Lease am gemeinsamen ``origin``-Remote, #191):

1. Läuft für das Ticket schon eine Session auf dieser Maschine? → kein Start (Exit 0,
   eine Zeile, kein Fehler-Lärm). Die lokale Sperre hält der laufende ``bau.py`` bis zu seinem
   Ende; stirbt er, gibt das Betriebssystem sie frei (tote Sperre = keine Sperre).
2. Ticket-Lage über :func:`wiederanlauf.feststellen` (fragt :func:`ticket_zustand.fertig_gebaut`
   und :func:`ticket_zustand.endgueltig_zu`, #279, #282): endgültig zu oder fertig gebaut mit
   allen Belegen (``art == "nichts"``) → kein Start (Exit 0, eine Zeile „nichts zu tun“).
   Fertig gebaut, aber es fehlen noch Abschluss-Schritte (``art == "abschluss"``) → Start
   erlaubt mit Stand-Hinweis in :attr:`Entscheidung.hinweis` (ohne Blocker-Warten).
   Nachbesserung an einem fertig gebauten Ticket: vorher das Staging-Label entfernen (#279).
3. Ticket-Stand nicht bestimmbar (gh fehlt, Netz weg, kaputte Antwort) → fail-closed:
   kein Start, laute Meldung, Exit 2.
4. Vor 2./3.: ``wt-<N>`` ist schon Worktree eines anderen Repos (gleiche Nummer, kein
   ``worktree_basis``, :func:`config.worktree_fremd`) → kein Start, Exit 9 (#499).
5. Maschinenübergreifende Fern-Sperre (#191) über ``refs/to-spawn/sperren/<N>`` auf ``origin``
   (atomares CAS per ``git push --force-with-lease``):
   - Aktive Lease eines anderen Rechners → kein Start (Exit 0, nennt Rechner und Ablauf).
   - Remote unerreichbar, Zeitgrenze oder kaputter Lease-Inhalt → fail-closed (Exit 2).
   - Abgelaufene Lease (Absturz/Netzverlust), eigene verwaiste Lease desselben Rechners oder
     ausdrückliche Übernahme (``uebernahme=True``, z. B. ``bau.py --umzug``) → atomare
     Übernahme per CAS.
   - Laufende Session verlängert ihre Lease im Hintergrund und gibt sie beim Ende per CAS frei
     (eine inzwischen übernommene Lease wird nie versehentlich gelöscht).

Ablösung (respawn startet ``bau.py <N> --sofort --ohne-prompt``): die neue Session darf neben der alten starten,
aber nur eine Ablösung je Ticket gleichzeitig. Sie übernimmt die Ticket-Sperre, sobald
die alte Session endet — danach ist sie wieder „die eine“ Session.

:func:`weiter_entscheidung` prüft vor jeder Staffel-Runde erneut, ob das Ticket noch zu bauen
ist und die Fern-Sperre nicht inzwischen von einem anderen Rechner übernommen wurde; ein
unbekannter Stand wird kurz wiederholt und endet sonst laut mit Exit 2 (#191).
Die gh-Abfrage hat eine Zeitgrenze (``gh.TICKET_STAND_TIMEOUT_S``), bau.py hängt nie stumm.

Test-Nähte: unter Test (``starter.unter_test()``) gilt mit ``TO_SPAWN_TEST_TICKET_OFFEN=1``
jedes Ticket als offen und ``TO_SPAWN_TEST_FERN_SPERRE_AUS=1`` schaltet die Fern-Sperre für
Tests ohne gemeinsames Bare-Remote ab (beide außerhalb von Tests wirkungslos);
``TO_SPAWN_START_SPERREN`` lenkt den lokalen Sperr-Ordner um.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from to_spawn import auswahl, config, gh, sperre, starter, ticket_zustand, wiederanlauf

log = logging.getLogger(__name__)

#: Umleitung des Sperr-Ordners (Tests, Sonderfälle).
SPERR_ORDNER_ENV = "TO_SPAWN_START_SPERREN"
#: Nur unter Test wirksam: jedes Ticket gilt als offen (Tests ohne gh-Attrappe).
TEST_OFFEN_ENV = "TO_SPAWN_TEST_TICKET_OFFEN"
#: Nur unter Test wirksam: Remote-Lease überspringen (Tests mit künstlicher GitHub-URL ohne echtes Remote).
TEST_FERN_SPERRE_AUS_ENV = "TO_SPAWN_TEST_FERN_SPERRE_AUS"
#: Exit-Code, wenn der Ticket-Stand oder die Fern-Sperre nicht bestimmbar ist (laut, fail-closed).
EXIT_UNKLAR = 2
#: Exit-Code, wenn ``wt-<N>`` schon einem anderen Repo gehört (#499, wie ``spec-<S>`` in #457).
EXIT_FREMDER_ORDNER = 9
#: Vor einer Staffel-Runde: so oft wird ein unbekannter Ticket-Stand gefragt (#191).
STAND_VERSUCHE = 3
#: Pause (s) zwischen den Versuchen; Umgebungsvariable kürzt sie (Tests).
STAND_ABSTAND_S = 5.0
STAND_ABSTAND_ENV = "TO_SPAWN_STAND_ABSTAND"

#: Ref-Namensraum für maschinenübergreifende Ticket-Leases auf ``origin`` (#191).
FERN_SPERRE_REF_PRAEFIX = "refs/to-spawn/sperren/"
#: Schema-Version der Lease-Nutzlast.
FERN_SPERRE_SCHEMA = 1
#: Standard-Gültigkeitsdauer (s) einer Fern-Sperre ohne Verlängerung (15 Minuten).
FERN_SPERRE_TTL_S = 900.0
FERN_SPERRE_TTL_ENV = "TO_SPAWN_FERN_SPERRE_TTL"
#: Takt (s) der Hintergrund-Verlängerung (Standard: ``ttl / 3``); Umgebungsvariable für Tests.
FERN_SPERRE_TAKT_ENV = "TO_SPAWN_FERN_SPERRE_TAKT"
#: Zeitgrenze (s) je Remote-Git-Aufruf der Fern-Sperre.
FERN_SPERRE_TIMEOUT_S = 30.0
FERN_SPERRE_TIMEOUT_ENV = "TO_SPAWN_FERN_SPERRE_TIMEOUT"
#: Kurze Zeitgrenze (s) beim Freigeben am Prozess-Ende, damit ``atexit`` nie ewig hängt.
FERN_SPERRE_FREIGABE_TIMEOUT_S = 15.0
#: Höchstzahl atomarer CAS-Versuche bei gleichzeitigem Wettlauf zweier Rechner.
FERN_SPERRE_CAS_VERSUCHE = 3

#: Liefert ``True`` (noch zu bauen), ``False`` (fertig gebaut) oder ``None`` (unbekannt).
TicketPruefer = Callable[[str, "Path | None"], "bool | None"]

# Gehaltene lokale Sperren bis Prozess-Ende (sonst schlösse der Müllsammler sie).
_GEHALTEN: list[contextlib.ExitStack] = []
# Gehaltene Remote-Leases bis Prozess-Ende (Heartbeat + CAS-Freigabe via atexit).
_FERN_SPERREN: list[_GehalteneFernSperre] = []
_FERN_LOCK = threading.Lock()
_ATEXIT_REGISTRIERT = False


@dataclass(frozen=True)
class FernSperre:
    """Gelesene maschinenübergreifende Ticket-Lease vom gemeinsamen Remote (#191)."""

    ticket: str
    rechner: str
    pid: int
    token: str
    beginn: float
    erneuert: float
    ablauf: float
    ttl_s: float
    sha: str = ""

    def ist_abgelaufen(self, jetzt: float | None = None) -> bool:
        """Ist die Lease zum Zeitpunkt ``jetzt`` abgelaufen?"""
        return (time.time() if jetzt is None else jetzt) >= self.ablauf


@dataclass(frozen=True)
class Entscheidung:
    """Ergebnis von :func:`betreten`: ``erlaubt`` + eine Zeile Grund + Exit-Code bei Nein."""

    erlaubt: bool
    grund: str
    exit_code: int = 0
    #: Wiederanlauf-Art (``neu``/``fortsetzen``/``abschluss``/``nichts``), leer ohne Lage-Prüfung (#282).
    art: str = ""
    #: Fertiger Stand-Hinweis für den Start-Text der Session (nur bei ``erlaubt=True`` und bekannter Lage).
    hinweis: str = ""


def sperr_datei(ticket: str, gh_repo: str) -> Path:
    """Sperr-Datei je Repo und Ticket auf dieser Maschine (alle Klone teilen sie)."""
    ordner = os.environ.get(SPERR_ORDNER_ENV) or str(Path.home() / ".to-spawn" / "start-sperren")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", gh_repo or "ohne-repo")
    return Path(ordner) / f"{slug}-{ticket}.lock"


def fern_sperr_ref(ticket: str) -> str:
    """Git-Ref-Pfad der maschinenübergreifenden Ticket-Sperre auf ``origin`` (#191)."""
    sauber = re.sub(r"[^A-Za-z0-9._-]+", "_", str(ticket).strip()) or "unbekannt"
    return f"{FERN_SPERRE_REF_PRAEFIX}{sauber}"


def _env_positiv(name: str, vorgabe: float) -> float:
    roh = os.environ.get(name)
    if not roh:
        return vorgabe
    try:
        wert = float(roh)
    except ValueError:
        return vorgabe
    return wert if wert > 0 else vorgabe


def _fern_sperre_ttl() -> float:
    return _env_positiv(FERN_SPERRE_TTL_ENV, FERN_SPERRE_TTL_S)


def _fern_sperre_takt(ttl_s: float) -> float:
    standard = max(0.1, ttl_s / 3.0)
    return _env_positiv(FERN_SPERRE_TAKT_ENV, standard)


def _fern_sperre_timeout() -> float:
    return _env_positiv(FERN_SPERRE_TIMEOUT_ENV, FERN_SPERRE_TIMEOUT_S)


def _fern_sperre_aktiv(cwd: Path | None) -> bool:
    """Ist die maschinenübergreifende Fern-Sperre für diesen Aufruf aktiv?"""
    if cwd is None:
        return False
    if os.environ.get(TEST_FERN_SPERRE_AUS_ENV) == "1" and starter.unter_test():
        return False
    return gh.hat_origin(cwd)


def _fern_sperre_payload(
    *,
    ticket: str,
    rechner: str,
    pid: int,
    token: str,
    beginn: float,
    erneuert: float,
    ttl_s: float,
) -> str:
    return json.dumps(
        {
            "schema": FERN_SPERRE_SCHEMA,
            "ticket": str(ticket),
            "rechner": rechner,
            "pid": pid,
            "token": token,
            "beginn": beginn,
            "erneuert": erneuert,
            "ablauf": erneuert + ttl_s,
            "ttl_s": ttl_s,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _ist_zahl(wert: object) -> bool:
    return isinstance(wert, (int, float)) and not isinstance(wert, bool)


def _fern_sperre_parsen(roh: str, ticket: str, sha: str) -> FernSperre | None:
    """Parst eine Lease-Commit-Nachricht streng; alles Ungültige → ``None`` (fail-closed)."""
    try:
        daten = json.loads(roh)
    except ValueError:
        return None
    if not isinstance(daten, dict):
        return None
    if daten.get("schema") != FERN_SPERRE_SCHEMA:
        return None
    if str(daten.get("ticket", "")) != str(ticket):
        return None
    rechner = daten.get("rechner")
    token = daten.get("token")
    pid = daten.get("pid")
    beginn = daten.get("beginn")
    erneuert = daten.get("erneuert")
    ablauf = daten.get("ablauf")
    ttl_s = daten.get("ttl_s")
    if not isinstance(rechner, str) or not rechner.strip():
        return None
    if not isinstance(token, str) or not token.strip():
        return None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
        return None
    if not (_ist_zahl(beginn) and _ist_zahl(erneuert) and _ist_zahl(ablauf) and _ist_zahl(ttl_s)):
        return None
    assert isinstance(beginn, (int, float)) and isinstance(erneuert, (int, float))
    assert isinstance(ablauf, (int, float)) and isinstance(ttl_s, (int, float))
    if ablauf <= 0 or ttl_s <= 0:
        return None
    return FernSperre(
        ticket=str(ticket),
        rechner=rechner.strip(),
        pid=pid,
        token=token.strip(),
        beginn=float(beginn),
        erneuert=float(erneuert),
        ablauf=float(ablauf),
        ttl_s=float(ttl_s),
        sha=sha,
    )


def _fern_sperre_lesen(repo: Path, ticket: str, *, timeout: float) -> tuple[bool, FernSperre | None, str]:
    """Liest die aktuelle Fern-Sperre von ``origin`` → ``(ok, lease_oder_none, fehler)``."""
    ref = fern_sperr_ref(ticket)
    ok_sha, sha, fehler = gh.remote_ref_sha(repo, ref, timeout=timeout)
    if not ok_sha:
        return False, None, fehler
    if not sha:
        return True, None, ""
    ok_msg, geladen_sha, nachricht, fehler_msg = gh.remote_ref_nachricht(repo, ref, timeout=timeout)
    if not ok_msg:
        # Ref könnte zwischen ls-remote und fetch sauber freigegeben worden sein → einmal gegenprüfen.
        ok_nach, sha_nach, _ = gh.remote_ref_sha(repo, ref, timeout=timeout)
        if ok_nach and not sha_nach:
            return True, None, ""
        return False, None, fehler_msg
    lease = _fern_sperre_parsen(nachricht, ticket, geladen_sha)
    if lease is None:
        return False, None, f"Lease-Inhalt auf {ref} unlesbar oder kaputt"
    return True, lease, ""


class _GehalteneFernSperre:
    """Hält eine erworbene Remote-Lease, verlängert sie im Hintergrund und gibt sie per CAS frei."""

    def __init__(
        self,
        *,
        ticket: str,
        repo: Path,
        ref: str,
        rechner: str,
        token: str,
        beginn: float,
        sha: str,
        ttl_s: float,
        takt_s: float,
        timeout_s: float,
    ) -> None:
        self.ticket = str(ticket)
        self.repo = repo.resolve()
        self.ref = ref
        self.rechner = rechner
        self.token = token
        self.beginn = beginn
        self.sha = sha
        self.ttl_s = ttl_s
        self.takt_s = takt_s
        self.timeout_s = timeout_s
        self.verloren = False
        self.freigegeben = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def starte_verlaengerung(self) -> None:
        def _schleife() -> None:
            while not self._stop.wait(self.takt_s):
                self._einmal_verlaengern()

        self._thread = threading.Thread(
            target=_schleife,
            name=f"fern-sperre-{self.ticket}",
            daemon=True,
        )
        self._thread.start()

    def _einmal_verlaengern(self) -> None:
        with self._lock:
            if self.freigegeben or self.verloren or not self.sha:
                return
            alt_sha = self.sha
        jetzt = time.time()
        nachricht = _fern_sperre_payload(
            ticket=self.ticket,
            rechner=self.rechner,
            pid=os.getpid(),
            token=self.token,
            beginn=self.beginn,
            erneuert=jetzt,
            ttl_s=self.ttl_s,
        )
        ok_com, neu_sha, fehler_com = gh.commit_mit_nachricht(self.repo, nachricht, timeout=self.timeout_s)
        if not ok_com:
            log.warning("Fern-Sperre #%s nicht verlängerbar (Commit): %s", self.ticket, fehler_com)
            return
        ok_push, konflikt, fehler_push = gh.remote_ref_cas_setzen(
            self.repo, self.ref, neu_sha, alt_sha, timeout=self.timeout_s
        )
        mit_freigabe_loeschen = ""
        with self._lock:
            if self.freigegeben:
                if ok_push:
                    mit_freigabe_loeschen = neu_sha
            elif ok_push:
                self.sha = neu_sha
            elif konflikt:
                self.verloren = True
                self._stop.set()
                log.warning("Fern-Sperre #%s wurde inzwischen übernommen — Verlängerung gestoppt.", self.ticket)
            else:
                log.warning("Fern-Sperre #%s Verlängerung gescheitert: %s", self.ticket, fehler_push)
        if mit_freigabe_loeschen:
            gh.remote_ref_cas_loeschen(
                self.repo,
                self.ref,
                mit_freigabe_loeschen,
                timeout=min(self.timeout_s, FERN_SPERRE_FREIGABE_TIMEOUT_S),
            )

    def als_verloren_markieren(self) -> None:
        with self._lock:
            self.verloren = True
            self._stop.set()

    def freigeben(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self.freigegeben:
                return
            self.freigegeben = True
            alt_sha = self.sha
            verloren = self.verloren
        if verloren or not alt_sha:
            return
        ok, konflikt, fehler = gh.remote_ref_cas_loeschen(
            self.repo,
            self.ref,
            alt_sha,
            timeout=min(self.timeout_s, FERN_SPERRE_FREIGABE_TIMEOUT_S),
        )
        if not ok and not konflikt:
            log.warning("Fern-Sperre #%s beim Beenden nicht freigebbar: %s", self.ticket, fehler)


def _fern_sperre_registrieren(halter: _GehalteneFernSperre) -> None:
    global _ATEXIT_REGISTRIERT
    with _FERN_LOCK:
        _FERN_SPERREN.append(halter)
        if not _ATEXIT_REGISTRIERT:
            atexit.register(alle_fern_sperren_freigeben)
            _ATEXIT_REGISTRIERT = True


def _gehaltene_fern_sperre(ticket: str, repo: Path) -> _GehalteneFernSperre | None:
    ziel = repo.resolve()
    with _FERN_LOCK:
        for eintrag in reversed(_FERN_SPERREN):
            if eintrag.ticket == str(ticket) and eintrag.repo == ziel and not eintrag.freigegeben:
                return eintrag
    return None


def alle_fern_sperren_freigeben() -> None:
    """Gibt alle von diesem Prozess gehaltenen Fern-Sperren sicher per CAS frei (#191)."""
    with _FERN_LOCK:
        eintraege = list(_FERN_SPERREN)
        _FERN_SPERREN.clear()
    for eintrag in reversed(eintraege):
        try:
            eintrag.freigeben()
        except OSError as fehler:  # pragma: no cover - Schutz bei atexit
            log.warning("Fern-Sperre #%s Freigabe fehlgeschlagen: %s", eintrag.ticket, fehler)


def _fern_sperre_belegt_entscheidung(ticket: str, bestehend: FernSperre, jetzt: float) -> Entscheidung:
    rest_s = max(1, int(bestehend.ablauf - jetzt))
    ablauf_text = datetime.fromtimestamp(bestehend.ablauf, tz=UTC).astimezone().isoformat(timespec="seconds")
    grund = (
        f"bau {ticket}: läuft schon auf Rechner {bestehend.rechner} "
        f"(Fern-Sperre gültig bis {ablauf_text}, noch {rest_s} s) — kein zweiter Start."
    )
    log.info("%s", grund)
    return Entscheidung(False, grund, 0)


def _fern_sperre_fail_closed(ticket: str, detail: str) -> Entscheidung:
    grund = (
        f"bau {ticket}: Fern-Sperre am Remote nicht nutzbar ({detail}) — KEIN Start (fail-closed). "
        "Remote-Verbindung prüfen, dann neu starten."
    )
    log.error("%s", grund)
    return Entscheidung(False, grund, EXIT_UNKLAR)


def _fern_sperre_absichern(
    entscheidung: Entscheidung,
    ticket: str,
    cwd: Path | None,
    *,
    abloesung: bool,
    uebernahme: bool,
) -> Entscheidung:
    """Sichert eine lokal erlaubte Start-Entscheidung zusätzlich über das gemeinsame Remote ab (#191)."""
    if not entscheidung.erlaubt or not _fern_sperre_aktiv(cwd):
        return entscheidung
    assert cwd is not None
    ref = fern_sperr_ref(ticket)
    ttl_s = _fern_sperre_ttl()
    takt_s = _fern_sperre_takt(ttl_s)
    timeout_s = _fern_sperre_timeout()
    eigener = auswahl.aktueller_rechner()

    for _versuch in range(FERN_SPERRE_CAS_VERSUCHE):
        ok_lesen, bestehend, fehler = _fern_sperre_lesen(cwd, ticket, timeout=timeout_s)
        if not ok_lesen:
            return _fern_sperre_fail_closed(ticket, fehler)
        jetzt = time.time()
        erwartet_sha = ""
        if bestehend is not None:
            abgelaufen = bestehend.ist_abgelaufen(jetzt)
            eigener_rechner = auswahl.gleicher_rechner(bestehend.rechner, eigener)
            if not abgelaufen and not eigener_rechner and not uebernahme:
                return _fern_sperre_belegt_entscheidung(ticket, bestehend, jetzt)
            if abgelaufen:
                log.warning(
                    "bau %s: abgelaufene Fern-Sperre von Rechner %s wird übernommen.",
                    ticket,
                    bestehend.rechner,
                )
            elif uebernahme and not eigener_rechner:
                log.info(
                    "bau %s: übernehme Fern-Sperre von Rechner %s (Übernahme/Umzug).",
                    ticket,
                    bestehend.rechner,
                )
            elif eigener_rechner:
                log.info(
                    "bau %s: übernehme Fern-Sperre desselben Rechners %s (%s).",
                    ticket,
                    bestehend.rechner,
                    "Ablösung" if abloesung else "verwaist nach Absturz",
                )
            erwartet_sha = bestehend.sha

        token = uuid.uuid4().hex
        nachricht = _fern_sperre_payload(
            ticket=ticket,
            rechner=eigener,
            pid=os.getpid(),
            token=token,
            beginn=jetzt,
            erneuert=jetzt,
            ttl_s=ttl_s,
        )
        ok_com, neu_sha, fehler_com = gh.commit_mit_nachricht(cwd, nachricht, timeout=timeout_s)
        if not ok_com:
            return _fern_sperre_fail_closed(ticket, f"Lease-Commit gescheitert: {fehler_com}")
        ok_push, konflikt, fehler_push = gh.remote_ref_cas_setzen(cwd, ref, neu_sha, erwartet_sha, timeout=timeout_s)
        if ok_push:
            halter = _GehalteneFernSperre(
                ticket=ticket,
                repo=cwd,
                ref=ref,
                rechner=eigener,
                token=token,
                beginn=jetzt,
                sha=neu_sha,
                ttl_s=ttl_s,
                takt_s=takt_s,
                timeout_s=timeout_s,
            )
            _fern_sperre_registrieren(halter)
            halter.starte_verlaengerung()
            return entscheidung
        if konflikt:
            continue
        return _fern_sperre_fail_closed(ticket, f"CAS-Push gescheitert: {fehler_push}")

    # Nach wiederholtem CAS-Wettlauf noch einmal lesen: gewinnt ein anderer Rechner, ruhig abweisen.
    ok_nach, bestehend_nach, fehler_nach = _fern_sperre_lesen(cwd, ticket, timeout=timeout_s)
    if not ok_nach:
        return _fern_sperre_fail_closed(ticket, fehler_nach)
    jetzt = time.time()
    if bestehend_nach is not None and not bestehend_nach.ist_abgelaufen(jetzt):
        return _fern_sperre_belegt_entscheidung(ticket, bestehend_nach, jetzt)
    return _fern_sperre_fail_closed(ticket, "wiederholter CAS-Konflikt beim Setzen der Fern-Sperre")


def _fern_sperre_pruefen_weiter(ticket: str, cwd: Path | None) -> Entscheidung | None:
    """Prüft vor einer Staffel-Runde, ob die Fern-Sperre noch diesem Lauf gehört (#191)."""
    if not _fern_sperre_aktiv(cwd):
        return None
    assert cwd is not None
    ok, bestehend, fehler = _fern_sperre_lesen(cwd, ticket, timeout=_fern_sperre_timeout())
    if not ok:
        return _fern_sperre_fail_closed(ticket, fehler)
    if bestehend is None or bestehend.ist_abgelaufen():
        return None
    halter = _gehaltene_fern_sperre(ticket, cwd)
    fremder_token = halter is not None and bestehend.token != halter.token
    fremder_rechner = not auswahl.gleicher_rechner(bestehend.rechner)
    if fremder_token or fremder_rechner:
        if halter is not None:
            halter.als_verloren_markieren()
        grund = (
            f"bau {ticket}: Fern-Sperre inzwischen von Rechner {bestehend.rechner} übernommen — kein weiterer Start."
        )
        log.info("%s", grund)
        return Entscheidung(False, grund, 0)
    return None


def _pruefen_mit_pruefer(ticket: str, cwd: Path | None, pruefer: TicketPruefer) -> Entscheidung:
    offen = pruefer(ticket, cwd)
    if offen is None:
        grund = (
            f"bau {ticket}: Ticket-Stand nicht bestimmbar (gh fehlt/Fehler/Zeitüberschreitung) — KEIN Start "
            "(fail-closed). gh prüfen, dann neu starten."
        )
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_UNKLAR)
    if not offen:
        grund = (
            f"bau {ticket}: Ticket ist geschlossen oder fertig gebaut (grün auf Staging) — kein Start. "
            f"Nachbesserung an einem fertig gebauten Ticket: vorher Label „{ticket_zustand.STAGING_FERTIG_LABEL}“ "
            "entfernen, dann neu starten."
        )
        log.info("%s", grund)
        return Entscheidung(False, grund)
    return Entscheidung(True, f"bau {ticket}: offen, Sperre gehalten — Start.")


def _pruefen(ticket: str, cwd: Path | None, pruefer: TicketPruefer | None) -> Entscheidung:
    if fremd := config.worktree_fremd(ticket, cwd):
        grund = f"WEIGERUNG (#499): bau {ticket}: {fremd} KEIN Start."
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_FREMDER_ORDNER)
    if pruefer is not None:
        return _pruefen_mit_pruefer(ticket, cwd, pruefer)
    if os.environ.get(TEST_OFFEN_ENV) == "1" and starter.unter_test():
        return Entscheidung(True, f"bau {ticket}: offen, Sperre gehalten — Start.")
    daten = ticket_zustand.lesen(ticket, cwd, mit_claim=True)
    if daten is None:
        grund = (
            f"bau {ticket}: Ticket-Stand nicht bestimmbar (gh fehlt/Fehler/Zeitüberschreitung) — KEIN Start "
            "(fail-closed). gh prüfen, dann neu starten."
        )
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_UNKLAR)
    if ticket_zustand.endgueltig_zu(daten):
        grund = f"bau {ticket}: nichts zu tun ({ticket_zustand.wortlaut(daten)}) — Ticket ist geschlossen, kein Start."
        log.info("%s", grund)
        return Entscheidung(False, grund, art="nichts")
    if ticket_zustand.ist_abnahme(daten, repo=cwd):
        label = ticket_zustand.abnahme_label(cwd)
        grund = (
            f"bau {ticket}: nichts zu tun (Abnahme-Ticket mit Label „{label}“) — wartet auf den Nutzer, kein Bau-Start."
        )
        log.info("%s", grund)
        return Entscheidung(False, grund, art="nichts")
    lage = (
        wiederanlauf.feststellen(int(ticket), cwd, daten=daten)
        if cwd is not None and ticket.isdigit() and (cwd / ".git").exists()
        else None
    )
    if lage is not None:
        if lage.art == "nichts":
            grund = (
                f"bau {ticket}: nichts zu tun — Ticket ist fertig gebaut (alle Belege da), kein Start. "
                f"Nachbesserung an einem fertig gebauten Ticket: vorher Label „{ticket_zustand.STAGING_FERTIG_LABEL}“ "
                "entfernen, dann neu starten."
            )
            log.info("%s", grund)
            return Entscheidung(False, grund, art="nichts")
        if lage.art == "abschluss":
            grund = f"bau {ticket}: fertig gebaut, Abschluss offen (fehlt: {', '.join(lage.fehlt)}) — Start."
            log.info("%s", grund)
            return Entscheidung(True, grund, art=lage.art, hinweis=lage.hinweis)
        return Entscheidung(
            True,
            f"bau {ticket}: {lage.art}, Sperre gehalten — Start.",
            art=lage.art,
            hinweis=lage.hinweis,
        )
    if ticket_zustand.fertig_gebaut(daten):
        grund = (
            f"bau {ticket}: nichts zu tun — Ticket ist geschlossen oder fertig gebaut (grün auf Staging) — kein Start. "
            f"Nachbesserung an einem fertig gebauten Ticket: vorher Label „{ticket_zustand.STAGING_FERTIG_LABEL}“ "
            "entfernen, dann neu starten."
        )
        log.info("%s", grund)
        return Entscheidung(False, grund, art="nichts")
    return Entscheidung(True, f"bau {ticket}: offen, Sperre gehalten — Start.")


def betreten(
    ticket: str,
    gh_repo: str,
    cwd: Path | None = None,
    *,
    abloesung: bool = False,
    uebernahme: bool = False,
    pruefer: TicketPruefer | None = None,
) -> Entscheidung:
    """Die eine Tür vor jedem Session-Start; bei ``erlaubt`` bleibt die Sperre bis Prozess-Ende.

    ``abloesung``: respawn — Start neben der laufenden Session auf derselben Maschine erlaubt
    (höchstens eine Ablösung gleichzeitig), die Ticket-Sperre geht nach deren Ende über.
    ``uebernahme``: ausdrückliche Übernahme einer aktiven Fern-Sperre eines anderen Rechners
    (z. B. ``bau.py --umzug``).
    ``pruefer``: Test-Naht statt :func:`ticket_zustand.lesen`.
    """
    ticket = str(ticket)
    haupt = sperr_datei(ticket, gh_repo)
    stapel = contextlib.ExitStack()
    try:
        if stapel.enter_context(sperre.halte(haupt)):
            entscheidung = _pruefen(ticket, cwd, pruefer)
            if entscheidung.erlaubt:
                entscheidung = _fern_sperre_absichern(entscheidung, ticket, cwd, abloesung=False, uebernahme=uebernahme)
        elif not abloesung:
            entscheidung = Entscheidung(False, f"bau {ticket}: läuft schon auf dieser Maschine — kein zweiter Start.")
            log.info("%s", entscheidung.grund)
        elif not stapel.enter_context(sperre.halte(haupt.with_name(haupt.name + ".abloesung"))):
            entscheidung = Entscheidung(False, f"bau {ticket}: eine Ablösung läuft schon — kein zweiter Start.")
            log.info("%s", entscheidung.grund)
        else:
            entscheidung = _pruefen(ticket, cwd, pruefer)
            if entscheidung.erlaubt:
                entscheidung = _fern_sperre_absichern(entscheidung, ticket, cwd, abloesung=True, uebernahme=uebernahme)
            if entscheidung.erlaubt:
                _uebernehmen_wenn_frei(haupt, stapel)
                return entscheidung
    except OSError as fehler:
        stapel.close()
        grund = f"bau {ticket}: Start-Sperre {haupt} nicht nutzbar ({fehler}) — KEIN Start."
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_UNKLAR)
    if entscheidung.erlaubt:
        _GEHALTEN.append(stapel)
    else:
        stapel.close()
    return entscheidung


def _uebernehmen_wenn_frei(haupt: Path, abloese_stapel: contextlib.ExitStack) -> None:
    """Ablösung: im Hintergrund auf die Ticket-Sperre warten, halten, dann Ablöse-Sperre frei."""

    def warten() -> None:
        with contextlib.ExitStack() as stapel:
            try:
                if not stapel.enter_context(sperre.halte(haupt, warten=float(10**9))):
                    return
            except OSError as fehler:
                log.error("Ablösung: Ticket-Sperre %s nicht übernehmbar: %s", haupt, fehler)
                return
            abloese_stapel.close()
            threading.Event().wait()  # bis Prozess-Ende halten

    _GEHALTEN.append(abloese_stapel)
    threading.Thread(target=warten, name="start-sperre-uebernahme", daemon=True).start()


def weiter_entscheidung(
    ticket: str,
    cwd: Path | None = None,
    *,
    pruefer: TicketPruefer | None = None,
    abstand: float | None = None,
) -> Entscheidung:
    """Vor jeder Staffel-Runde: Ticket noch zu bauen? (#191, #279)

    Noch zu bauen → weiter; fertig gebaut → Ende mit Exit 0. Unbekannt (gh-Aussetzer) wird bis zu
    :data:`STAND_VERSUCHE`-mal mit ``abstand`` Sekunden Pause neu gefragt (Standard aus
    :data:`STAND_ABSTAND_ENV`, sonst :data:`STAND_ABSTAND_S`); bleibt es unbekannt →
    laut, Exit :data:`EXIT_UNKLAR` — nie still wie „geschlossen“.
    """
    if abstand is None:
        try:
            abstand = float(os.environ.get(STAND_ABSTAND_ENV) or STAND_ABSTAND_S)
        except ValueError:
            abstand = STAND_ABSTAND_S
    for versuch in range(1, STAND_VERSUCHE + 1):
        entscheidung = _pruefen(str(ticket), cwd, pruefer)
        if entscheidung.erlaubt:
            fern = _fern_sperre_pruefen_weiter(str(ticket), cwd)
            if fern is not None:
                entscheidung = fern
        if entscheidung.exit_code != EXIT_UNKLAR:
            return entscheidung
        if versuch < STAND_VERSUCHE:
            log.warning(
                "bau %s: Ticket-Stand unbekannt, Versuch %d/%d — neuer Versuch.", ticket, versuch, STAND_VERSUCHE
            )
            time.sleep(max(0.0, abstand))
    return entscheidung


def weiter_erlaubt(
    ticket: str,
    cwd: Path | None = None,
    *,
    pruefer: TicketPruefer | None = None,
    abstand: float | None = None,
) -> bool:
    """Wie :func:`weiter_entscheidung`, nur ja/nein (gleiche Wiederhol-Regel bei unbekanntem Stand)."""
    return weiter_entscheidung(ticket, cwd, pruefer=pruefer, abstand=abstand).erlaubt
