"""Probesitz (#214): die Checkliste, die das Setup selbst führt — 7 echte Prüfungen.

``python to_spawn.py probesitz`` prüft jeden Punkt wirklich (kein Vertrauen auf
Konfig-Werte), merkt sich das Ergebnis je Maschine + Repo und ``setup --zeigen``
hängt den Stand an. Rot heißt immer: Grund + „fehlt noch: …“.

1. Login trägt                       ``claude auth status`` → ``loggedIn``
2. Wegwerf-Session: Mini-Commit+Push  echte ``bau.py --probesitz``-Session auf einem Wegwerf-Ticket
3. Playwright gegen Staging           ``staging.url`` + ``/login`` antwortet 200
4. Sandbox sperrt außerhalb Worktree  ``srt`` lässt ``touch`` innen zu, außen nicht
5. Bau-Log-Zeile mit Token            aus dem Wegwerf-Lauf (``session_ende`` mit Token)
6. Künstlicher Handoff → Folgesession aus dem Wegwerf-Lauf (``handoff`` + ``session_start`` Staffel 2)
7. Mail „Probesitz grün“              nur wenn 1–6 grün; Art ``probesitz_gruen`` (geht auch bei ``nur_kritisch``)

Alle Unterprozesse laufen über einen injizierbaren ``laeufer`` — die Unit-Tests
brauchen weder Netz noch ``claude``. Nichts hier ist repo-spezifisch: Wegwerf-Ticket,
Zweig ``probesitz-<N>`` und Worktree ``wt-<N>`` entstehen und verschwinden von selbst.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from . import bau_log, config, melder, nest

log = logging.getLogger("to_spawn.probesitz")

ZUSTAND_UNTERORDNER = "probesitz"
EXIT_GRUEN = 0
EXIT_ROT = 1
EXIT_KONFIG = 2
MODELL_VORGABE = "claude-sonnet-5"
#: Gesamt-Zeitlimit des Wegwerf-Laufs (zwei kurze Runden, Sonnet).
WEGWERF_ZEITLIMIT_S = 15 * 60
BEOBACHTUNGS_TAKT_S = 5.0
BELEG_ZEILEN_MAX = 20
SKILL_ORDNER = Path(__file__).resolve().parent.parent

PUNKTE: tuple[str, ...] = (
    "Login trägt",
    "Wegwerf-Session: Mini-Commit + Push",
    "Playwright gegen Staging",
    "Sandbox sperrt außerhalb Worktree",
    "Bau-Log-Zeile mit Token",
    "Künstlicher Handoff startet Folge-Session",
    "Mail „Probesitz grün“",
)

#: Auftrag der Wegwerf-Session (``bau.py --probesitz``). Keine Backticks: auf dem
#: Windows-Notnagel (``shell=True``) würde die Shell sie auswerten.
WEGWERF_PROMPT = (
    "Probesitz-Wegwerf-Session für Ticket #{ticket} (Skill to-spawn). Du arbeitest im "
    "aktuellen Ordner (eigener Worktree). "
    "Runde 1 (kein Handoff im Startkontext): "
    "1) git checkout -B probesitz-{ticket}; "
    "2) Datei docs/probesitz/{ticket}_runde1.txt mit einer Zeile „Probesitz Runde 1“ anlegen; "
    "3) git add nur diese Datei, Commit mit Betreff "
    "„chore(probesitz): Wegwerf-Commit Runde 1 (#{ticket}) [skip ci]“; "
    "4) git push -u origin probesitz-{ticket}; "
    "5) Datei docs/handoffs/HANDOFF_<heutiges Datum YYYY-MM-DD>_{ticket}.md anlegen mit genau "
    "diesen Zeilen: „# Handoff Probesitz #{ticket}“, „Staffel: weiter“, „Runde 1 fertig: Commit "
    "gepusht. Runde 2 soll docs/probesitz/{ticket}_runde2.txt anlegen, committen, pushen und "
    "enden.“ — diese Datei NICHT committen; "
    "6) beende deine Antwort sofort danach mit dem Wort FERTIG. "
    "Runde 2 (ein Handoff steht im Startkontext): "
    "1) Datei docs/probesitz/{ticket}_runde2.txt mit „Probesitz Runde 2“ anlegen; "
    "2) Commit „chore(probesitz): Wegwerf-Commit Runde 2 (#{ticket}) [skip ci]“, "
    "git push origin probesitz-{ticket}; "
    "3) keinen Handoff schreiben; "
    "4) antworte nur FERTIG. "
    "Keine anderen Dateien anfassen, keine Fragen stellen, keine Werkzeuge außer git/Dateien."
)

Laeufer = Callable[..., "subprocess.CompletedProcess[str]"]
Which = Callable[[str], str | None]
SeiteLaden = Callable[[str, str, str], tuple[int, str]]


@dataclass
class Ergebnis:
    nummer: int
    titel: str
    ok: bool
    grund: str
    fehlt_noch: str = ""
    beleg: str = ""

    def zeile(self) -> str:
        marke = "✓" if self.ok else "✗"
        text = f"  {marke} {self.nummer} {self.titel} — {self.grund}"
        if not self.ok and self.fehlt_noch:
            text += f" · fehlt noch: {self.fehlt_noch}"
        return text


def _rot(nummer: int, grund: str, fehlt_noch: str = "", beleg: str = "") -> Ergebnis:
    return Ergebnis(nummer, PUNKTE[nummer - 1], False, grund, fehlt_noch, beleg)


def _gruen(nummer: int, grund: str, beleg: str = "") -> Ergebnis:
    return Ergebnis(nummer, PUNKTE[nummer - 1], True, grund, "", beleg)


def _jetzt() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --- Unterprozesse -----------------------------------------------------------------


def laeufer_standard(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Unterprozess ohne Terminal: stdin zu, Ausgabe eingefangen, Zeitablauf = Exit 124."""
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"Zeitlimit {timeout}s überschritten")
    except OSError as fehler:
        return subprocess.CompletedProcess(argv, 127, "", str(fehler))


# --- Zustand je Maschine + Repo -----------------------------------------------------


def zustand_datei(repo: Path) -> Path:
    return melder.zustand_ordner() / ZUSTAND_UNTERORDNER / f"{melder.repo_kennung(repo)}.json"


def lade_zustand(repo: Path) -> dict[str, Any]:
    return melder.lade_json(zustand_datei(repo))


def speichere_zustand(repo: Path, zustand: dict[str, Any]) -> None:
    melder.speichere_json(zustand_datei(repo), zustand)


def eintragen(zustand: dict[str, Any], ergebnis: Ergebnis) -> dict[str, Any]:
    """Ergebnis in den Zustand mischen (neues Objekt, Eingabe bleibt unverändert)."""
    neu = dict(zustand)
    punkte = dict(neu.get("punkte") or {})
    eintrag = asdict(ergebnis)
    eintrag.pop("nummer", None)
    eintrag.pop("titel", None)
    eintrag["ts"] = _jetzt()
    punkte[str(ergebnis.nummer)] = eintrag
    neu["punkte"] = punkte
    neu["letzter_lauf"] = eintrag["ts"]
    return neu


def punkt_gruen(zustand: dict[str, Any], nummer: int) -> bool:
    eintrag = (zustand.get("punkte") or {}).get(str(nummer))
    return bool(isinstance(eintrag, dict) and eintrag.get("ok"))


def offene_punkte(zustand: dict[str, Any], bis: int = 7) -> list[int]:
    return [n for n in range(1, bis + 1) if not punkt_gruen(zustand, n)]


def alle_gruen(zustand: dict[str, Any]) -> bool:
    return not offene_punkte(zustand)


def zeige(zustand: dict[str, Any]) -> str:
    """Block für ``setup --zeigen`` und den Befehl selbst."""
    zeilen = ["Probesitz (7 Punkte):"]
    punkte = zustand.get("punkte") or {}
    for nummer, titel in enumerate(PUNKTE, start=1):
        eintrag = punkte.get(str(nummer))
        if not isinstance(eintrag, dict):
            zeilen.append(f"  · {nummer} {titel} — noch nie geprüft")
            continue
        zeilen.append(
            Ergebnis(
                nummer,
                titel,
                bool(eintrag.get("ok")),
                str(eintrag.get("grund") or ""),
                str(eintrag.get("fehlt_noch") or ""),
            ).zeile()
        )
    stand = zustand.get("letzter_lauf")
    zeilen.append(
        f"  Stand: {stand}" if stand else "  Noch nie gelaufen: python to_spawn.py probesitz"
    )
    return "\n".join(zeilen)


# --- Punkt 1: Login -----------------------------------------------------------------


def pruefe_login(*, laeufer: Laeufer = laeufer_standard, which: Which = shutil.which) -> Ergebnis:
    claude = which("claude")
    if not claude:
        return _rot(1, "claude nicht gefunden", "Claude Code installieren + claude login")
    fertig = laeufer([claude, "auth", "status"], timeout=60)
    try:
        daten = json.loads(fertig.stdout or "{}")
    except ValueError:
        daten = {}
    if not isinstance(daten, dict) or (fertig.returncode != 0 and not daten):
        kurz = (fertig.stderr or fertig.stdout).strip()[-200:]
        return _rot(1, f"claude auth status Exit {fertig.returncode}: {kurz}", "claude login (Abo-Konto)")
    if not daten.get("loggedIn"):
        return _rot(1, "loggedIn false", "claude login (Abo-Konto)")
    konto = daten.get("subscriptionType") or daten.get("authMethod") or "angemeldet"
    return _gruen(1, f"loggedIn true ({konto})", beleg=json.dumps({"authMethod": daten.get("authMethod"), "subscriptionType": daten.get("subscriptionType")}))


# --- Punkt 3: Playwright gegen Staging --------------------------------------------------


def lies_zugang(text: str) -> tuple[str, str, str]:
    """Zugangsdatei → (adresse, nutzer, passwort). Zwei Formate:

    a) Zeilen ``nutzer=…``, ``passwort=…`` (optional ``adresse=…``), Kommentare mit ``#``;
    b) eine Zeile ``user:pass``. Werte werden nie geloggt.
    """
    adresse = nutzer = passwort = ""
    zeilen = [z.strip() for z in text.splitlines() if z.strip() and not z.strip().startswith("#")]
    for zeile in zeilen:
        schluessel, trenner, wert = zeile.partition("=")
        if not trenner:
            continue
        schluessel = schluessel.strip().lower()
        wert = wert.strip()
        if schluessel in ("adresse", "url"):
            adresse = wert
        elif schluessel in ("nutzer", "user"):
            nutzer = wert
        elif schluessel in ("passwort", "pass", "password"):
            passwort = wert
    if not nutzer and not passwort and zeilen and ":" in zeilen[0] and "=" not in zeilen[0]:
        nutzer, _, passwort = zeilen[0].partition(":")
    return adresse, nutzer, passwort


def seite_laden_playwright(url: str, nutzer: str, passwort: str) -> tuple[int, str]:
    """Echter Aufruf: Chromium headless, HTTP-Basic-Zugang, Rückgabe (Status, Seitentitel)."""
    from playwright.sync_api import (
        sync_playwright,  # ImportError → Aufrufer meldet „fehlt noch“
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            kontext = browser.new_context(
                http_credentials={"username": nutzer, "password": passwort} if nutzer else None
            )
            seite = kontext.new_page()
            antwort = seite.goto(url, wait_until="domcontentloaded", timeout=60_000)
            status = antwort.status if antwort is not None else 0
            return status, seite.title()
        finally:
            browser.close()


def pruefe_staging(
    konfig: dict[str, Any], repo: Path, *, seite_laden: SeiteLaden | None = None
) -> Ergebnis:
    staging = konfig.get("staging") if isinstance(konfig.get("staging"), dict) else {}
    url = str(staging.get("url") or "").strip()
    zugang_datei = str(staging.get("zugang_datei") or "").strip()
    nutzer = passwort = ""
    if zugang_datei:
        pfad = Path(zugang_datei).expanduser()
        if not pfad.is_absolute():
            pfad = repo / pfad
        try:
            adresse, nutzer, passwort = lies_zugang(pfad.read_text(encoding="utf-8"))
        except OSError as fehler:
            return _rot(3, f"staging.zugang_datei unlesbar ({fehler.__class__.__name__})", f"Zugangsdatei {pfad} anlegen (Staging-Nest, #211)")
        url = url or adresse
    if not url:
        return _rot(3, "staging.url fehlt", "staging.url in .to-spawn/config.json (Staging-Nest, #211)")
    ziel = url.rstrip("/") + "/login"
    laden = seite_laden or seite_laden_playwright
    try:
        status, titel = laden(ziel, nutzer, passwort)
    except ImportError:
        return _rot(
            3,
            f"Playwright fehlt in {sys.executable}",
            "Playwright für diesen Python-Interpreter (z. B. .venv/bin/python -m pip install playwright "
            "&& .venv/bin/python -m playwright install chromium)",
        )
    except Exception as fehler:  # noqa: BLE001 (Browser-/Netzfehler jeder Art → Rot mit Grund, nie Absturz)
        return _rot(3, f"{ziel} nicht erreichbar: {str(fehler)[:160]}", "Staging starten / Adresse und Zugang prüfen (#211)")
    if status != 200:
        return _rot(3, f"{ziel} antwortet {status}", "Staging-Login-Seite muss 200 liefern (Zugangsdatei/Staging-Nest, #211)")
    return _gruen(3, f"{ziel} antwortet 200", beleg=f"Titel: {titel}")


# --- Punkt 4: Sandbox --------------------------------------------------------------------


def pruefe_sandbox(
    konfig: dict[str, Any],
    repo: Path,
    *,
    laeufer: Laeufer = laeufer_standard,
    which: Which = shutil.which,
    home: Path | None = None,
) -> Ergebnis:
    """``srt`` mit den Skill-Einstellungen: ``touch`` im Worktree geht, außerhalb nicht."""
    srt, bwrap = which("srt"), which("bwrap")
    if not srt or not bwrap:
        fehlt = " und ".join(n for n, p in (("srt", srt), ("bwrap", bwrap)) if not p)
        return _rot(4, f"{fehlt} nicht gefunden", "srt + bubblewrap installieren (nest werkzeuge)")
    heim = home or Path.home()
    # Beide Ordner unter HOME, nicht unter /tmp: /tmp ist in der Sandbox immer beschreibbar.
    innen = Path(tempfile.mkdtemp(prefix=".probesitz-innen-", dir=heim))
    aussen = Path(tempfile.mkdtemp(prefix=".probesitz-aussen-", dir=heim))
    try:
        einstellungen = nest.sandbox_einstellungen(innen, repo, konfig, None, heim)
        datei = innen / "sandbox.json"
        datei.write_text(json.dumps(einstellungen, indent=1), encoding="utf-8")
        ziel_innen, ziel_aussen = innen / "probesitz.txt", aussen / "probesitz.txt"
        drinnen = laeufer([srt, "--settings", str(datei), "--", "touch", str(ziel_innen)], timeout=120)
        draussen = laeufer([srt, "--settings", str(datei), "--", "touch", str(ziel_aussen)], timeout=120)
        beleg = f"innen Exit {drinnen.returncode} · außen Exit {draussen.returncode}, Datei außen: {'da' if ziel_aussen.exists() else 'fehlt'}"
        modus = str((konfig.get("sandbox") or {}).get("modus", "aus")) if isinstance(konfig.get("sandbox"), dict) else "aus"
        hinweis = " (sandbox.modus aus — Sessions laufen ohne Sandbox)" if modus != "an" else " (sandbox.modus an)"
        if drinnen.returncode != 0 or not ziel_innen.exists():
            kurz = (drinnen.stderr or drinnen.stdout).strip()[-160:]
            return _rot(4, f"srt sperrt auch innerhalb des Worktrees (Exit {drinnen.returncode}: {kurz})", "srt/bwrap-Setup prüfen (nest werkzeuge, user namespaces)", beleg)
        if draussen.returncode == 0 or ziel_aussen.exists():
            return _rot(4, "srt lässt Schreiben außerhalb des Worktrees zu", "srt-Einstellungen prüfen (nest sandbox)", beleg)
        return _gruen(4, f"innen erlaubt, außen gesperrt{hinweis}", beleg)
    except OSError as fehler:
        return _rot(4, f"Sandbox-Prüfung scheiterte: {fehler}", "srt/bwrap-Setup prüfen (nest werkzeuge)")
    finally:
        shutil.rmtree(innen, ignore_errors=True)
        shutil.rmtree(aussen, ignore_errors=True)


# --- Punkte 2/5/6: Wegwerf-Lauf ------------------------------------------------------------


@dataclass
class SessionLauf:
    exit_code: int
    beobachtung: str | None = None
    zeit_ueberschritten: bool = False
    protokoll: str = ""


def _sessions_stand() -> Any:
    """``skripte/sessions_stand.py`` als Modul (liegt außerhalb des Pakets)."""
    spec = importlib.util.spec_from_file_location(
        "to_spawn_sessions_stand", SKILL_ORDNER / "skripte" / "sessions_stand.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("skripte/sessions_stand.py nicht ladbar")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def session_beobachten(ticket: str) -> str | None:
    """Erste sichtbare Claude-Session unter ``bau.py <ticket>`` samt ctx-Spalte (#237)."""
    try:
        modul = _sessions_stand()
        alle = modul.prozesse_lesen()
    except (ImportError, OSError, ValueError) as fehler:
        log.debug("Prozessliste nicht lesbar: %s", fehler)
        return None
    for prozess in alle:
        if not prozess.name.lower().startswith("python"):
            continue
        treffer = modul.MUSTER.search(prozess.cmd)
        if not treffer or treffer.group(2) != ticket:
            continue
        kinder = modul.nachkommen(prozess.pid, alle)
        session = next((k for k in kinder if k.name.lower() in modul.SESSION_NAMEN), None)
        if session is None:
            return None
        return f"Session PID {session.pid} unter bau.py {prozess.pid} · ctx {modul.context_mode_zustand(session.pid, alle)}"
    return None


def session_fahren(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    zeitlimit_s: float,
    beobachter: Callable[[str], str | None],
    ticket: str,
    ausgabe: TextIO | None = None,
) -> SessionLauf:
    """``bau.py --probesitz`` starten, alle 5 s nach der Session schauen, Zeitlimit hart."""
    with tempfile.NamedTemporaryFile(
        "w", prefix=f"probesitz-{ticket}-", suffix=".log", delete=False, encoding="utf-8"
    ) as protokoll:
        prozess = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=protokoll,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        start = time.monotonic()
        beobachtung: str | None = None
        zeit_um = False
        while True:
            try:
                prozess.wait(timeout=BEOBACHTUNGS_TAKT_S)
                break
            except subprocess.TimeoutExpired:
                pass
            if beobachtung is None:
                beobachtung = beobachter(ticket)
                if beobachtung and ausgabe:
                    ausgabe.write(f"    Wegwerf-Session sichtbar: {beobachtung}\n")
                    ausgabe.flush()
            if time.monotonic() - start > zeitlimit_s:
                zeit_um = True
                try:
                    os.killpg(prozess.pid, signal.SIGTERM)
                except OSError:
                    prozess.terminate()
                try:
                    prozess.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    prozess.kill()
                break
    return SessionLauf(prozess.returncode or 0, beobachtung, zeit_um, protokoll.name)


def werte_bau_log(zeilen: Iterable[dict[str, Any]]) -> tuple[Ergebnis, Ergebnis]:
    """Punkte 5 und 6 aus den Bau-Log-Zeilen des Wegwerf-Tickets."""
    zeilen = list(zeilen)
    if not zeilen:
        return (
            _rot(5, "keine Bau-Log-Zeile", "Bau-Log-Hook (to_spawn.py hook-stop) in der Session-Settings-Datei + TO_SPAWN_LOG_REPO"),
            _rot(6, "keine Bau-Log-Zeile", "Staffel-Hook scripts/hooks/staffel_stop.py im Repo + Handoff mit „Staffel: weiter“"),
        )
    enden = [z for z in zeilen if z.get("typ") == "session_ende"]
    mit_token = [z for z in enden if isinstance(z.get("tokens"), dict) and int(z["tokens"].get("gesamt") or 0) > 0]
    if mit_token:
        erste = mit_token[0]
        p5 = _gruen(5, f"session_ende mit {erste['tokens']['gesamt']} Token ({erste.get('modell') or 'Modell unbekannt'})", beleg=f"{erste.get('modell') or '?'} · {erste['tokens']['gesamt']} Token · Session {erste.get('session_id') or '?'}")
    elif enden:
        p5 = _rot(5, f"{len(enden)} session_ende-Zeile(n), aber 0 Token", "Transkript-Pfad im Stop-Hook (Token kommen aus dem Transkript) — Session-Persistenz prüfen")
    else:
        p5 = _rot(5, f"{len(zeilen)} Zeile(n), aber keine session_ende", "Bau-Log-Hook (to_spawn.py hook-stop) in der Session-Settings-Datei")
    handoffs = [z for z in zeilen if z.get("typ") == "handoff"]
    starts_2 = [z for z in zeilen if z.get("typ") == "session_start" and int(z.get("staffel") or 0) == 2]
    if handoffs and starts_2:
        p6 = _gruen(6, "handoff geschrieben, Staffel 2 gestartet", beleg=f"Handoff-Session {handoffs[0].get('session_id') or '?'} → Folge-Session {starts_2[0].get('session_id') or '?'}")
    elif handoffs:
        p6 = _rot(6, "handoff da, aber keine session_start mit Staffel 2", "Staffel-Hook scripts/hooks/staffel_stop.py im Repo (Übergabe an bau.py)")
    else:
        p6 = _rot(6, "keine handoff-Zeile", "Staffel-Hook scripts/hooks/staffel_stop.py im Repo + Handoff mit „Staffel: weiter“")
    return p5, p6


def _git(repo: Path, laeufer: Laeufer, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return laeufer(["git", "-C", str(repo), *args], timeout=timeout)


def pruefe_push(repo: Path, ticket: str, zweig: str, laeufer: Laeufer) -> Ergebnis:
    """Punkt 2: Zweig liegt auf origin und trägt mindestens einen Commit ``(#N)``."""
    fern = _git(repo, laeufer, "ls-remote", "origin", f"refs/heads/{zweig}")
    if fern.returncode != 0 or not fern.stdout.strip():
        return _rot(2, f"origin/{zweig} fehlt (Push kam nicht an)", "Session-Protokoll lesen: git push in der Wegwerf-Session")
    abruf = _git(repo, laeufer, "fetch", "origin", zweig)
    if abruf.returncode != 0:
        return _rot(2, f"git fetch origin {zweig} scheiterte: {abruf.stderr.strip()[-160:]}", "Netz/Zugang zu origin prüfen")
    basis = nest._standard_zweig(repo) or "origin/HEAD"
    protokoll = _git(repo, laeufer, "log", f"{basis}..FETCH_HEAD", "--oneline")
    betreffe = [z for z in protokoll.stdout.splitlines() if f"(#{ticket})" in z]
    if not betreffe:
        return _rot(2, f"origin/{zweig} ohne Commit mit (#{ticket}) im Betreff", "Wegwerf-Session hat nicht committet — Protokoll lesen", protokoll.stdout.strip()[:400])
    return _gruen(2, f"{len(betreffe)} Commit(s) auf origin/{zweig}", beleg="\n".join(betreffe))


def _wegwerf_ticket(repo: Path, laeufer: Laeufer, which: Which) -> str | None:
    gh = which("gh")
    if not gh:
        return None
    stempel = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    fertig = laeufer(
        [gh, "issue", "create", "--title", f"Probesitz {stempel}", "--body", "Wegwerf-Ticket des Probesitz (Skill to-spawn, #214). Wird am Ende automatisch geschlossen."],
        cwd=repo,
        timeout=120,
    )
    if fertig.returncode != 0:
        log.warning("gh issue create scheiterte: %s", (fertig.stderr or fertig.stdout).strip()[-200:])
        return None
    treffer = re.search(r"/(\d+)\s*$", fertig.stdout.strip())
    return treffer.group(1) if treffer else None


def _aufraeumen(repo: Path, ticket: str, worktree: Path, zweig: str, laeufer: Laeufer, which: Which, fazit: str) -> None:
    """Immer: Ticket schließen, Fern-Zweig, Worktree und lokale Zweige weg — Fehler nur loggen."""
    gh = which("gh")
    schritte: list[list[str]] = []
    if gh:
        schritte.append([gh, "issue", "close", ticket, "--comment", f"Probesitz beendet: {fazit}"])
    schritte += [
        ["git", "-C", str(repo), "push", "origin", "--delete", zweig],
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
        ["git", "-C", str(repo), "branch", "-D", f"ticket-{ticket}", zweig],
    ]
    for argv in schritte:
        fertig = laeufer(argv, cwd=repo, timeout=120)
        if fertig.returncode != 0:
            log.warning("Aufräumen (%s) Exit %s: %s", " ".join(argv[:4]), fertig.returncode, (fertig.stderr or fertig.stdout).strip()[-160:])
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)


def _log_auszug(worktree: Path, ticket: str) -> str:
    for pfad in (bau_log.lauf_pfad(worktree, ticket), bau_log.log_pfad(worktree, ticket)):
        try:
            zeilen = pfad.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        return "\n".join(zeilen[:BELEG_ZEILEN_MAX])
    return ""


def wegwerf_lauf(
    repo: Path,
    konfig: dict[str, Any],
    *,
    modell: str | None = None,
    laeufer: Laeufer = laeufer_standard,
    which: Which = shutil.which,
    session_starter: Callable[..., SessionLauf] = session_fahren,
    beobachter: Callable[[str], str | None] = session_beobachten,
    worktree_anlegen: Callable[[Path, Path, str], None] = nest.worktree_anlegen,
    skill: Path = SKILL_ORDNER,
    ausgabe: TextIO | None = None,
    zeitlimit_s: float = WEGWERF_ZEITLIMIT_S,
) -> dict[int, Ergebnis]:
    """Ein echter Lauf für die Punkte 2, 5 und 6 — Wegwerf-Ticket, Worktree, zwei Runden."""
    ticket = _wegwerf_ticket(repo, laeufer, which)
    if ticket is None:
        return {
            2: _rot(2, "gh nicht angemeldet", "gh auth login"),
            5: _rot(5, "hängt an Punkt 2"),
            6: _rot(6, "hängt an Punkt 2"),
        }
    worktree = Path(config.worktree_pfad(ticket)).expanduser()
    zweig = f"probesitz-{ticket}"
    fazit = "abgebrochen"
    ergebnisse: dict[int, Ergebnis] = {}
    if ausgabe:
        ausgabe.write(f"    Wegwerf-Ticket #{ticket}, Worktree {worktree}\n")
        ausgabe.flush()
    try:
        worktree_anlegen(worktree, repo, ticket)
        argv = [sys.executable, str(skill / "skripte" / "bau.py"), ticket, "--probesitz", "--sofort", "--staffel-max", "2", "--model", modell or MODELL_VORGABE]
        env = dict(os.environ)
        env["TO_SPAWN_REPO"] = str(repo)
        env.pop("CLAUDE_CODE_CHILD_SESSION", None)
        lauf = session_starter(argv, cwd=worktree, env=env, zeitlimit_s=zeitlimit_s, beobachter=beobachter, ticket=ticket, ausgabe=ausgabe)
        p5, p6 = werte_bau_log(bau_log.lese(worktree, ticket))
        p2 = pruefe_push(repo, ticket, zweig, laeufer)
        auszug = _log_auszug(worktree, ticket)
        if auszug:
            p5.beleg = (p5.beleg + "\n" if p5.beleg else "") + auszug
        if lauf.beobachtung:
            p2.beleg = (p2.beleg + "\n" if p2.beleg else "") + lauf.beobachtung
        if lauf.zeit_ueberschritten:
            p2 = _rot(2, f"Zeitlimit {int(zeitlimit_s)} s überschritten (Protokoll {lauf.protokoll})", "Session-Protokoll lesen", p2.beleg)
        if not (repo / "scripts" / "hooks" / "staffel_stop.py").is_file() and not p6.ok:
            p6.fehlt_noch = "scripts/hooks/staffel_stop.py im Repo (Staffel-Hook, siehe SKILL.md)"
        ergebnisse = {2: p2, 5: p5, 6: p6}
        fazit = ", ".join(f"Punkt {n} {'✓' if e.ok else '✗'}" for n, e in sorted(ergebnisse.items()))
    except Exception as fehler:  # jeder Fehler wird Rot mit Grund, aufgeräumt wird trotzdem
        log.exception("Wegwerf-Lauf abgebrochen")
        grund = f"{fehler.__class__.__name__}: {str(fehler)[:200]}"
        ergebnisse = {2: _rot(2, grund, "Fehler beheben, Probesitz erneut"), 5: _rot(5, "hängt an Punkt 2"), 6: _rot(6, "hängt an Punkt 2")}
        fazit = f"abgebrochen ({grund})"
    finally:
        _aufraeumen(repo, ticket, worktree, zweig, laeufer, which, fazit)
    return ergebnisse


# --- Punkt 7: Mail ------------------------------------------------------------------------


def _zusammenfassung(zustand: dict[str, Any]) -> str:
    return "\n".join(zeige(zustand).splitlines()[1:8])


def pruefe_mail(
    repo: Path,
    konfig: dict[str, Any],
    zustand: dict[str, Any],
    *,
    melden: Callable[..., bool] = melder.melden,
) -> Ergebnis:
    offen = offene_punkte(zustand, bis=6)
    if offen:
        return _rot(7, "Punkte offen: " + ", ".join(str(n) for n in offen), "erst die offenen Punkte")
    if not melder.mail_eingerichtet(konfig):
        return _rot(7, "kein Mail-Befehl", "mail.befehl in .to-spawn/config.json")
    stempel = _jetzt()
    vorschau = eintragen(zustand, _gruen(7, "Mail geht raus"))
    text = _zusammenfassung(vorschau)
    if melden(repo, "probesitz_gruen", "Probesitz grün", text, f"probesitz:{stempel}", konfig=konfig):
        return _gruen(7, f"Mail verschickt ({stempel})", beleg=text)
    return _rot(7, "mail.befehl hat die Meldung nicht angenommen (Exit ≠ 0, siehe Log)", "mail.befehl prüfen: JSON auf stdin, Exit 0 = gesendet")


# --- Alles zusammen -----------------------------------------------------------------


def laufen(
    repo: Path,
    konfig: dict[str, Any],
    *,
    punkte: set[int] | None = None,
    modell: str | None = None,
    ausgabe: TextIO | None = None,
    laeufer: Laeufer = laeufer_standard,
    which: Which = shutil.which,
    seite_laden: SeiteLaden | None = None,
    session_starter: Callable[..., SessionLauf] = session_fahren,
    beobachter: Callable[[str], str | None] = session_beobachten,
    melden: Callable[..., bool] = melder.melden,
) -> list[Ergebnis]:
    """Gewählte Punkte (Vorgabe alle) prüfen, Zustand nach jedem Punkt speichern."""
    gewaehlt = set(punkte) if punkte else set(range(1, 8))
    zustand = lade_zustand(repo)
    ergebnisse: list[Ergebnis] = []

    def merken(*neue: Ergebnis) -> None:
        nonlocal zustand
        for erg in neue:
            zustand = eintragen(zustand, erg)
            ergebnisse.append(erg)
            if ausgabe:
                ausgabe.write(erg.zeile() + "\n")
                ausgabe.flush()
        speichere_zustand(repo, zustand)

    if 1 in gewaehlt:
        merken(pruefe_login(laeufer=laeufer, which=which))
    if gewaehlt & {2, 5, 6}:
        lauf = wegwerf_lauf(repo, konfig, modell=modell, laeufer=laeufer, which=which, session_starter=session_starter, beobachter=beobachter, ausgabe=ausgabe)
        merken(*(lauf[n] for n in (2, 5, 6) if n in gewaehlt))
    if 3 in gewaehlt:
        merken(pruefe_staging(konfig, repo, seite_laden=seite_laden))
    if 4 in gewaehlt:
        merken(pruefe_sandbox(konfig, repo, laeufer=laeufer, which=which))
    if 7 in gewaehlt:
        merken(pruefe_mail(repo, konfig, zustand, melden=melden))
    return sorted(ergebnisse, key=lambda e: e.nummer)


def befehl(repo: Path, *, punkte: list[int] | None, nur_zeigen: bool, modell: str | None, ausgabe: TextIO = sys.stdout) -> int:
    """Unterbefehl ``probesitz``: 0 = 7/7 ✓, 1 = mindestens ein ✗, 2 = Konfig/Repo unlesbar."""
    if not repo.is_dir():
        ausgabe.write(f"Repo nicht lesbar: {repo}\n")
        return EXIT_KONFIG
    try:
        konfig = config.lade(repo)
    except OSError as fehler:
        ausgabe.write(f"Konfig nicht lesbar: {fehler}\n")
        return EXIT_KONFIG
    ungueltig = [n for n in (punkte or []) if n not in range(1, 8)]
    if ungueltig:
        ausgabe.write(f"--punkt nur 1–7, nicht {ungueltig}\n")
        return EXIT_KONFIG
    if not nur_zeigen:
        ausgabe.write("Probesitz läuft — jeder Punkt ist eine echte Prüfung.\n")
        laufen(repo, konfig, punkte=set(punkte) if punkte else None, modell=modell, ausgabe=ausgabe)
        ausgabe.write("\n")
    zustand = lade_zustand(repo)
    ausgabe.write(zeige(zustand) + "\n")
    return EXIT_GRUEN if alle_gruen(zustand) else EXIT_ROT


__all__ = [
    "EXIT_GRUEN",
    "EXIT_KONFIG",
    "EXIT_ROT",
    "PUNKTE",
    "WEGWERF_PROMPT",
    "ZUSTAND_UNTERORDNER",
    "Ergebnis",
    "alle_gruen",
    "befehl",
    "eintragen",
    "lade_zustand",
    "laufen",
    "lies_zugang",
    "pruefe_login",
    "pruefe_mail",
    "pruefe_push",
    "pruefe_sandbox",
    "pruefe_staging",
    "speichere_zustand",
    "wegwerf_lauf",
    "werte_bau_log",
    "zeige",
    "zustand_datei",
]
