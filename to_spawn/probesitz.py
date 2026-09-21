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
import uuid
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
#: Frist nach SIGTERM, bevor die Prozessgruppe hart beendet wird.
BEENDEN_FRIST_S = 30.0
#: Muster im Session-Protokoll, die einen Rechte-Stopp verraten (Fixrunde #214).
RECHTE_MUSTER = re.compile(r"permission denied|requires? permission|requested permissions|haven't granted|not allowed to use", re.IGNORECASE)
#: stderr von ``touch`` in der Sandbox, das als echte Sperre zählt.
SPERR_MUSTER = re.compile(r"permission denied|read-only file system|keine berechtigung", re.IGNORECASE)
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
    "2) git add nur diese Datei, Commit „chore(probesitz): Wegwerf-Commit Runde 2 (#{ticket}) [skip ci]“, "
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


def eintragen(
    zustand: dict[str, Any], ergebnis: Ergebnis, lauf_id: str | None = None
) -> dict[str, Any]:
    """Ergebnis in den Zustand mischen (neues Objekt, Eingabe bleibt unverändert).

    ``lauf_id`` kennzeichnet den Lauf: Punkt 7 und der Exit-Code trauen nur Punkten
    aus demselben Lauf (Fixrunde #214).
    """
    neu = dict(zustand)
    punkte = dict(neu.get("punkte") or {})
    eintrag = asdict(ergebnis)
    eintrag.pop("nummer", None)
    eintrag.pop("titel", None)
    eintrag["ts"] = _jetzt()
    if lauf_id:
        eintrag["lauf_id"] = lauf_id
    punkte[str(ergebnis.nummer)] = eintrag
    neu["punkte"] = punkte
    neu["letzter_lauf"] = eintrag["ts"]
    return neu


def punkt_gruen(zustand: dict[str, Any], nummer: int) -> bool:
    eintrag = (zustand.get("punkte") or {}).get(str(nummer))
    return bool(isinstance(eintrag, dict) and eintrag.get("ok"))


#: Grund-Anfang eines roten Punkts, der nur „nicht konfiguriert“ heißt (#257): Staging ist
#: freiwillig, ohne ``staging.url`` bleibt Punkt 3 sichtbar rot, sperrt aber Punkt 7 nicht.
NICHT_KONFIGURIERT = "staging.url fehlt"


def punkt_nicht_konfiguriert(zustand: dict[str, Any], nummer: int) -> bool:
    eintrag = (zustand.get("punkte") or {}).get(str(nummer))
    return bool(
        isinstance(eintrag, dict)
        and not eintrag.get("ok")
        and str(eintrag.get("grund") or "").startswith(NICHT_KONFIGURIERT)
    )


def offene_punkte(
    zustand: dict[str, Any], bis: int = 7, *, ohne_nicht_konfiguriert: bool = True
) -> list[int]:
    """Rote Punkte 1..``bis``; ``ohne_nicht_konfiguriert`` lässt „nur nicht konfiguriert“ aus."""
    return [
        n
        for n in range(1, bis + 1)
        if not punkt_gruen(zustand, n)
        and not (ohne_nicht_konfiguriert and punkt_nicht_konfiguriert(zustand, n))
    ]


def aeltere_punkte(zustand: dict[str, Any], lauf_id: str, bis: int = 6) -> list[int]:
    """Grüne Punkte, die nicht aus dem Lauf ``lauf_id`` stammen."""
    punkte = zustand.get("punkte") or {}
    return [
        n
        for n in range(1, bis + 1)
        if punkt_gruen(zustand, n) and (punkte.get(str(n)) or {}).get("lauf_id") != lauf_id
    ]


def alle_gruen(zustand: dict[str, Any]) -> bool:
    """7/7 ✓ — Punkt 7 wird nur grün, wenn 1–6 im selben Lauf grün waren.
    Ein nicht konfiguriertes Staging zählt hier als offen (Exit bleibt 1, sichtbar rot)."""
    return not offene_punkte(zustand, ohne_nicht_konfiguriert=False)


def _stempel_kurz(ts: Any) -> str:
    """ISO → ``TT.MM. HH:MM`` für die Anzeige."""
    text = str(ts or "")
    return f"{text[8:10]}.{text[5:7]}. {text[11:16]}" if len(text) >= 16 else text


def zeige(zustand: dict[str, Any]) -> str:
    """Block für ``setup --zeigen`` und den Befehl selbst."""
    zeilen = ["Probesitz (7 Punkte):"]
    punkte = zustand.get("punkte") or {}
    for nummer, titel in enumerate(PUNKTE, start=1):
        eintrag = punkte.get(str(nummer))
        if not isinstance(eintrag, dict):
            zeilen.append(f"  · {nummer} {titel} — noch nie geprüft")
            continue
        zeile = Ergebnis(
            nummer,
            titel,
            bool(eintrag.get("ok")),
            str(eintrag.get("grund") or ""),
            str(eintrag.get("fehlt_noch") or ""),
        ).zeile()
        if eintrag.get("lauf_id"):
            zeile += f" · {_stempel_kurz(eintrag.get('ts'))}"
        zeilen.append(zeile)
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
        meldung = (draussen.stderr or draussen.stdout).strip()
        if draussen.returncode != 1 or not SPERR_MUSTER.search(meldung):
            # 124 = Zeitlimit, 126/127 = srt/bwrap nicht startbar — das ist keine Sperre.
            return _rot(4, f"srt außen Exit {draussen.returncode} ohne Sperr-Meldung: {meldung[-160:]}", "srt/bwrap-Setup prüfen (nest werkzeuge)", beleg)
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
    # ``@dataclass`` sucht das Modul in ``sys.modules`` (Python 3.13) — ohne Eintrag
    # AttributeError (Live-Befund Probesitz 21.09., #214).
    sys.modules[spec.name] = modul
    try:
        spec.loader.exec_module(modul)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
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


def _posix() -> bool:
    return sys.platform != "win32"


def _signal_an_gruppe(prozess: subprocess.Popen[Any], sig: int, hart: bool) -> None:
    """Signal an die Prozessgruppe (POSIX) — sonst nur an bau.py selbst (Windows)."""
    if _posix():
        try:
            os.killpg(prozess.pid, sig)
            return
        except (OSError, AttributeError):
            pass
    if hart:
        prozess.kill()
    else:
        prozess.terminate()


def prozess_beenden(prozess: subprocess.Popen[Any], frist: float | None = None) -> None:
    """Prozessgruppe sanft beenden, nach ``frist`` hart — und immer auf das Ende warten.

    Sonst liefe ``claude`` unter einem toten ``bau.py`` weiter, während der Worktree
    schon weggeräumt wird (Fixrunde #214).
    """
    if prozess.poll() is not None:
        return
    _signal_an_gruppe(prozess, signal.SIGTERM, hart=False)
    try:
        prozess.wait(timeout=frist if frist is not None else BEENDEN_FRIST_S)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_an_gruppe(prozess, getattr(signal, "SIGKILL", signal.SIGTERM), hart=True)
    try:
        prozess.wait(timeout=BEENDEN_FRIST_S)
    except subprocess.TimeoutExpired:
        log.error("Prozess %s lässt sich nicht beenden — von Hand prüfen.", prozess.pid)


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
    """``bau.py --probesitz`` starten, alle 5 s nach der Session schauen, Zeitlimit hart.

    Was auch passiert (Ausnahme im Beobachter, Strg-C, Zeitlimit): beim Verlassen ist die
    Prozessgruppe beendet und abgewartet.
    """
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
            start_new_session=_posix(),
        )
        start = time.monotonic()
        beobachtung: str | None = None
        zeit_um = False
        try:
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
                    prozess_beenden(prozess)
                    break
        finally:
            prozess_beenden(prozess)
    code = prozess.returncode
    return SessionLauf(code if code is not None else -1, beobachtung, zeit_um, protokoll.name)


def protokoll_grund(pfad: str) -> str | None:
    """Rechte-Stopp im Session-Protokoll (``claude -p`` ohne erlaubte Werkzeuge)?"""
    try:
        text = Path(pfad).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    treffer = RECHTE_MUSTER.search(text)
    if not treffer:
        return None
    anfang = text.rfind("\n", 0, treffer.start()) + 1
    ende = text.find("\n", treffer.end())
    return text[anfang : ende if ende >= 0 else None].strip()[:200]


#: Hinweis-Text zu Punkt 6: der Hook liegt im Skill, eine Repo-Kopie ist freiwillig (#257).
STAFFEL_HOOK_HINWEIS = "Staffel-Hook (Skill `skripte/hooks/staffel_stop.py`, Repo-Kopie scripts/hooks/ freiwillig)"


def staffel_hook_vorhanden(repo: Path, skill: Path = SKILL_ORDNER) -> bool:
    """Repo-Kopie ODER Skill-Kopie — dieselbe Regel wie ``bau.py:staffel_hook_pfad`` (#257)."""
    return (repo / "scripts" / "hooks" / "staffel_stop.py").is_file() or (
        skill / "skripte" / "hooks" / "staffel_stop.py"
    ).is_file()


def werte_bau_log(zeilen: Iterable[dict[str, Any]]) -> tuple[Ergebnis, Ergebnis]:
    """Punkte 5 und 6 aus den Bau-Log-Zeilen des Wegwerf-Tickets."""
    zeilen = list(zeilen)
    if not zeilen:
        return (
            _rot(5, "keine Bau-Log-Zeile", "Bau-Log-Hook (to_spawn.py hook-stop) in der Session-Settings-Datei + TO_SPAWN_LOG_REPO"),
            _rot(6, "keine Bau-Log-Zeile", f"{STAFFEL_HOOK_HINWEIS} + Handoff mit „Staffel: weiter“"),
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
        p6 = _rot(6, "handoff da, aber keine session_start mit Staffel 2", f"{STAFFEL_HOOK_HINWEIS} (Übergabe an bau.py)")
    else:
        p6 = _rot(6, "keine handoff-Zeile", f"{STAFFEL_HOOK_HINWEIS} + Handoff mit „Staffel: weiter“")
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


def _wegwerf_ticket(repo: Path, laeufer: Laeufer, which: Which) -> tuple[str | None, str]:
    """Wegwerf-Ticket anlegen → (Nummer, "") oder (None, Grund)."""
    gh = which("gh")
    if not gh:
        return None, "gh nicht gefunden"
    stempel = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    fertig = laeufer(
        [gh, "issue", "create", "--title", f"Probesitz {stempel}", "--body", "Wegwerf-Ticket des Probesitz (Skill to-spawn, #214). Wird am Ende automatisch geschlossen."],
        cwd=repo,
        timeout=120,
    )
    meldung = (fertig.stderr or fertig.stdout).strip()
    if fertig.returncode != 0:
        log.warning("gh issue create scheiterte: %s", meldung[-200:])
        return None, (f"gh issue create Exit {fertig.returncode}: {meldung[-160:]}" if meldung else "gh nicht angemeldet")
    treffer = re.search(r"/(\d+)\s*$", fertig.stdout.strip())
    if not treffer:
        return None, f"gh issue create ohne Ticket-Nummer in der Antwort: {fertig.stdout.strip()[-160:]}"
    return treffer.group(1), ""


def _aufraeumen(repo: Path, ticket: str, worktree: Path, zweig: str, laeufer: Laeufer, which: Which, fazit: str) -> list[str]:
    """Immer: Ticket schließen, Fern-Zweig, Worktree und lokale Zweige weg.

    Rückgabe: Fehlschläge als Text (landen im Beleg von Punkt 2 und in der Ausgabe) —
    ein offenes Ticket oder ein liegengebliebener Zweig darf nicht unsichtbar bleiben.
    """
    gh = which("gh")
    fehler: list[str] = []

    def schritt(argv: list[str]) -> bool:
        fertig = laeufer(argv, cwd=repo, timeout=120)
        if fertig.returncode == 0:
            return True
        meldung = (fertig.stderr or fertig.stdout).strip()[-160:]
        kurz = " ".join(argv[1:4]) if argv[0] == "git" else " ".join(Path(argv[0]).name.split() + argv[1:3])
        log.warning("Aufräumen (%s) Exit %s: %s", kurz, fertig.returncode, meldung)
        fehler.append(f"{kurz} Exit {fertig.returncode}: {meldung}")
        return False

    if gh:
        schritt([gh, "issue", "close", ticket, "--comment", f"Probesitz beendet: {fazit}"])
    else:
        fehler.append(f"gh fehlt — Ticket #{ticket} bleibt offen")
    schritt(["git", "-C", str(repo), "push", "origin", "--delete", zweig])
    schritt(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)])
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    zweige = ["git", "-C", str(repo), "branch", "-D", f"ticket-{ticket}", zweig]
    if not schritt(zweige):
        # Registrierung eines weggezogenen Worktrees blockiert branch -D — erst prune, dann erneut.
        schritt(["git", "-C", str(repo), "worktree", "prune"])
        fehler.pop()
        schritt(zweige)
    return fehler


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
    ticket, grund = _wegwerf_ticket(repo, laeufer, which)
    if ticket is None:
        return {
            2: _rot(2, grund, "gh auth login"),
            5: _rot(5, "hängt an Punkt 2"),
            6: _rot(6, "hängt an Punkt 2"),
        }
    worktree = Path(config.worktree_pfad(ticket, repo)).expanduser()
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
        # Worktree-Datei + Rückfall-Laufdatei im Hauptbaum (Zeilen vor dem Worktree, #257)
        p5, p6 = werte_bau_log(bau_log.lese(worktree, ticket, hauptbaum=repo))
        p2 = pruefe_push(repo, ticket, zweig, laeufer)
        auszug = _log_auszug(worktree, ticket)
        if auszug:
            p5.beleg = (p5.beleg + "\n" if p5.beleg else "") + auszug
        if lauf.beobachtung:
            p2.beleg = (p2.beleg + "\n" if p2.beleg else "") + lauf.beobachtung
        if lauf.protokoll:
            p2.beleg = (p2.beleg + "\n" if p2.beleg else "") + f"Protokoll: {lauf.protokoll}"
        rechte = protokoll_grund(lauf.protokoll) if lauf.protokoll else None
        if lauf.zeit_ueberschritten:
            p2 = _rot(2, f"Zeitlimit {int(zeitlimit_s)} s überschritten (Protokoll {lauf.protokoll})", "Session-Protokoll lesen", p2.beleg)
        elif rechte:
            p2 = _rot(2, f"Session ohne Werkzeug-Rechte: {rechte}", "claude -p braucht --allowedTools (bau.py --probesitz) oder bypassPermissions", p2.beleg)
        elif lauf.exit_code != 0:
            p2 = _rot(2, f"bau.py Exit {lauf.exit_code} (Protokoll {lauf.protokoll})", "Session-Protokoll lesen (Sandbox-Pflicht, srt, claude, context-mode?)", p2.beleg)
        if not staffel_hook_vorhanden(repo, skill) and not p6.ok:
            p6.fehlt_noch = f"{STAFFEL_HOOK_HINWEIS} fehlt — Skill neu installieren (install.sh)"
        ergebnisse = {2: p2, 5: p5, 6: p6}
        fazit = ", ".join(f"Punkt {n} {'✓' if e.ok else '✗'}" for n, e in sorted(ergebnisse.items()))
    except Exception as fehler:  # jeder Fehler wird Rot mit Grund, aufgeräumt wird trotzdem
        log.exception("Wegwerf-Lauf abgebrochen")
        grund = f"{fehler.__class__.__name__}: {str(fehler)[:200]}"
        ergebnisse = {2: _rot(2, grund, "Fehler beheben, Probesitz erneut"), 5: _rot(5, "hängt an Punkt 2"), 6: _rot(6, "hängt an Punkt 2")}
        fazit = f"abgebrochen ({grund})"
    finally:
        reste = _aufraeumen(repo, ticket, worktree, zweig, laeufer, which, fazit)
        if reste and 2 in ergebnisse:
            text = "Aufräumen unvollständig: " + " · ".join(reste)
            ergebnisse[2].beleg = (ergebnisse[2].beleg + "\n" if ergebnisse[2].beleg else "") + text
            if ausgabe:
                ausgabe.write(f"    {text}\n")
                ausgabe.flush()
    return ergebnisse


# --- Punkt 7: Mail ------------------------------------------------------------------------


def _zusammenfassung(zustand: dict[str, Any]) -> str:
    return "\n".join(zeige(zustand).splitlines()[1:8])


def pruefe_mail(
    repo: Path,
    konfig: dict[str, Any],
    zustand: dict[str, Any],
    *,
    lauf_id: str | None = None,
    melden: Callable[..., bool] = melder.melden,
) -> Ergebnis:
    offen = offene_punkte(zustand, bis=6)
    if offen:
        return _rot(7, "Punkte offen: " + ", ".join(str(n) for n in offen), "erst die offenen Punkte")
    if lauf_id:
        alt = aeltere_punkte(zustand, lauf_id)
        if alt:
            punkte = zustand.get("punkte") or {}
            stempel = ", ".join(f"{n} ({_stempel_kurz((punkte.get(str(n)) or {}).get('ts'))})" for n in alt)
            return _rot(7, f"Punkte aus älterem Lauf: {', '.join(str(n) for n in alt)} — {stempel}", "vollständiger Lauf ohne --punkt")
    if not melder.mail_eingerichtet(konfig):
        return _rot(7, "kein Mail-Befehl", "mail.befehl in .to-spawn/config.json")
    # Staging nicht konfiguriert (Punkt 3 nur „staging.url fehlt“) → grün ohne Staging (#257).
    ohne_staging = bool(offene_punkte(zustand, bis=6, ohne_nicht_konfiguriert=False))
    betreff = "Probesitz grün (ohne Staging)" if ohne_staging else "Probesitz grün"
    stempel = _jetzt()
    vorschau = eintragen(zustand, _gruen(7, "Mail geht raus"), lauf_id)
    text = _zusammenfassung(vorschau)
    if melden(repo, "probesitz_gruen", betreff, text, f"probesitz:{stempel}", konfig=konfig):
        return _gruen(7, f"Mail verschickt ({stempel}){' — ohne Staging' if ohne_staging else ''}", beleg=text)
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
    lauf_id = _jetzt() + "-" + uuid.uuid4().hex[:8]
    ergebnisse: list[Ergebnis] = []

    def merken(*neue: Ergebnis) -> None:
        nonlocal zustand
        for erg in neue:
            zustand = eintragen(zustand, erg, lauf_id)
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
        merken(pruefe_mail(repo, konfig, zustand, lauf_id=lauf_id, melden=melden))
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
