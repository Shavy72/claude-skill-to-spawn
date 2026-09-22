"""Aufsicht über die Wächter-Session: Nutzungs-Limit erkennen, Ausweich-Modell starten (#213).

``claude --fallback-model`` greift nur bei „overloaded/not available“, nicht beim
Nutzungs-Limit („You've hit your session limit“, „You've reached your Fable limit“).
Deshalb startet :func:`fahre` den Wächter selbst (``Popen``) und liest in einem
Hintergrund-Faden die neuen Zeilen seines Transkripts
``~/.claude/projects/<cwd>/<session-id>.jsonl``. Taucht die Limit-Zeile auf:

* Wächter läuft noch auf dem Haupt-Modell → Prozess beenden, Bau-Log-Zeile
  ``waechter_modell``, Mail ``waechter_ausweich``, Neustart per
  ``claude --resume <session-id> --model <Ausweich>`` mit kurzem Weiter-Prompt.
* Wächter läuft schon auf dem Ausweich-Modell → Reset-Uhrzeit aus der Limit-Zeile
  lesen (#254): nennt sie eine, pausiert der Wächter bis dahin (plus Puffer) und
  fährt danach selbst per ``--resume`` weiter; nennt sie keine (oder liegt der
  Reset mehr als :data:`MAX_WARTE_S` weg), bleibt es bei Mail ``session_tot``.

Der Aufpasser (#236) setzt ein stilles Wächter-Fenster über ``fahre(session_id=…)``
mit ``--resume`` fort; die Gesprächs-ID steht in ``.to-spawn/sessions/wache-<S>.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import bau_log, melder, sessions_datei

log = logging.getLogger("to_spawn.waechter_lauf")

_LIMIT_TEXT = re.compile(r"(reached|hit) your .*limit", re.IGNORECASE)
#: Uhrzeit-Angabe derselben Zeile: „· resets 5:40am (Europe/Berlin)“ (#254).
_RESET_TEXT = re.compile(
    r"resets?\s+(?P<stunde>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<halb>am|pm)?"
    r"(?:\s*\((?P<zone>[^)]+)\))?",
    re.IGNORECASE,
)
TAKT_S = 5.0
#: Sekunden, nach denen ein fehlendes Transkript eine Warnung wert ist.
WARTE_TRANSKRIPT_S = 60.0
#: Puffer nach dem Reset, bevor der Wächter weiterfährt (#254).
RESET_PUFFER_S = 120.0
#: Obergrenze der Pause. Darüber (z. B. Wochen-Limit) meldet der Wächter wie bisher
#: und bleibt stehen — lieber ein Mensch als eine Pause über Tage.
MAX_WARTE_S = 6 * 3600.0
#: So weit darf ein Reset zurückliegen und noch gelten. Weiter zurück heißt: die Zeile
#: meint ein anderes Fenster — sonst startet der Wächter im Takt des Puffers neu (F1).
RESET_TOLERANZ_S = 120.0
#: So oft pausiert der Wächter in Folge. Danach steht er wie vor #254 — ein Limit,
#: das nach jedem Neustart sofort wieder greift, ist ein Fall für einen Menschen (F1).
MAX_PAUSEN = 3


def zahl_aus_umgebung(name: str, vorgabe: float) -> float:
    """Zahl aus einer Umgebungs-Variablen, nie negativ; Müll → ``vorgabe`` (F8)."""
    roh = (os.environ.get(name) or "").strip()
    if not roh:
        return vorgabe
    try:
        return max(0.0, float(roh))
    except ValueError:
        log.warning("%s=%r ist keine Zahl — es gilt %s.", name, roh, vorgabe)
        return vorgabe


def transkript_ordner(cwd: Path, heim: Path | None = None) -> Path:
    """Ordner, in den Claude Code die Transkripte einer Arbeitsmappe schreibt."""
    return (
        (heim or Path.home())
        / ".claude"
        / "projects"
        / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    )


def _texte(eintrag: dict[str, Any]) -> str:
    inhalt = (eintrag.get("message") or {}).get("content")
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        return " ".join(
            str(b.get("text") or "")
            for b in inhalt
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def ist_limit_zeile(eintrag: dict[str, Any]) -> bool:
    """Limit-Meldung von Claude Code (nicht: ein Zitat im Nutzer- oder Werkzeug-Text)."""
    if eintrag.get("type") != "assistant":
        return False
    fehler = (
        bool(eintrag.get("isApiErrorMessage"))
        or (eintrag.get("message") or {}).get("model") == "<synthetic>"
    )
    if not fehler:
        return False
    # ``error == "rate_limit"`` allein reicht nicht: auch kurze API-Drosselungen tragen es.
    return bool(_LIMIT_TEXT.search(_texte(eintrag)))


def _zone(name: str | None, rueckfall: tzinfo | None) -> tzinfo | None:
    """Zeitzone aus der Klammer der Limit-Zeile; unbekannter Name → Ortszeit.

    Unter Windows kennt Python die Zeitzonen nur mit dem Paket ``tzdata``. Fehlt es,
    landet jede Angabe hier im Rückfall — deshalb eine Warnung statt einer Debug-Zeile.
    """
    if name:
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError) as fehler:
            log.warning(
                "Zeitzone %r unbekannt (%s) — es gilt die Ortszeit dieses Rechners. "
                "Unter Windows hilft „pip install tzdata“.",
                name,
                fehler,
            )
    return rueckfall


def _aus_quota(eintrag: dict[str, Any]) -> datetime | None:
    """``quotaLimits.resetsAt`` ist die genaue Quelle (Unix-Zeit) — sie schlägt den Text."""
    quota = eintrag.get("quotaLimits")
    wert = quota.get("resetsAt") if isinstance(quota, dict) else None
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(wert), tz=timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError) as fehler:
        log.debug("resetsAt %r unbrauchbar (%s) — Text lesen.", wert, fehler)
        return None


def _aus_text(text: str, jetzt: datetime) -> datetime | None:
    """Uhrzeit aus „resets 5:40am (Europe/Berlin)“ — schon vorbei heißt: morgen."""
    treffer = _RESET_TEXT.search(text)
    if not treffer:
        return None
    stunde = int(treffer["stunde"])
    minute = int(treffer["minute"] or 0)
    halb = (treffer["halb"] or "").lower()
    if halb == "pm" and stunde < 12:
        stunde += 12
    elif halb == "am" and stunde == 12:
        stunde = 0
    elif not halb and 1 <= stunde <= 12:
        # „resets 7“ kann 7 Uhr oder 19 Uhr heißen — raten wäre bis zu 12 h daneben (F7).
        log.info("Uhrzeit %r ohne am/pm ist mehrdeutig — keine Pause.", treffer.group(0))
        return None
    if stunde > 23 or minute > 59:
        log.debug("Uhrzeit %s:%s aus der Limit-Zeile ist keine Zeit.", stunde, minute)
        return None
    ortszeit = jetzt.astimezone(_zone(treffer["zone"], jetzt.tzinfo))
    # ``fold=1``: an der Zeitumstellung gibt es die Stunde zweimal — erst nach der
    # zweiten ist das Limit wirklich offen (F7).
    ziel = ortszeit.replace(
        hour=stunde, minute=minute, second=0, microsecond=0, fold=1
    )
    if ziel <= ortszeit:
        ziel += timedelta(days=1)  # über Mitternacht hinweg
    return ziel


def reset_zeitpunkt(
    eintrag: dict[str, Any], jetzt: datetime | None = None
) -> datetime | None:
    """Wann das Nutzungs-Limit wieder aufgeht; ``None``, wenn die Zeile es nicht sagt."""
    jetzt = jetzt or datetime.now().astimezone()
    return _aus_quota(eintrag) or _aus_text(_texte(eintrag), jetzt)


def warte_sekunden(
    ziel: datetime,
    *,
    jetzt: datetime | None = None,
    puffer: float = RESET_PUFFER_S,
    hoechstens: float = MAX_WARTE_S,
) -> float | None:
    """Sekunden bis ``ziel`` plus Puffer; ``None``, wenn das länger als ``hoechstens`` dauert."""
    jetzt = jetzt or datetime.now().astimezone()
    rest = (ziel - jetzt).total_seconds()
    if rest < -RESET_TOLERANZ_S:
        log.info(
            "Reset %s liegt %.0f s zurück — die Zeile meint ein anderes Fenster.",
            ziel,
            -rest,
        )
        return None
    sekunden = max(0.0, rest) + puffer
    if sekunden > hoechstens:
        log.info(
            "Reset erst %s (%.1f h) — das ist zu lang zum Warten.",
            ziel,
            sekunden / 3600.0,
        )
        return None
    return sekunden


def _warten(
    sekunden: float, takt: float, abbruch: Callable[[], bool] | None
) -> bool:
    """Die Pause absitzen; ``False``, wenn ``abbruch`` vorher greift (z. B. Umzug, #212)."""
    ende = time.monotonic() + sekunden
    while True:
        if abbruch is not None and abbruch():
            return False
        rest = ende - time.monotonic()
        if rest <= 0:
            return True
        time.sleep(min(takt, rest))


class Aufsicht(threading.Thread):
    """Liest neue Zeilen einer Transkript-Datei ab Byte ``ab`` und meldet die erste Limit-Zeile."""

    def __init__(
        self,
        datei: Path,
        ab: int,
        takt: float,
        bei_limit: Callable[[str], None],
        warte_s: float = WARTE_TRANSKRIPT_S,
    ) -> None:
        super().__init__(name="waechter-aufsicht", daemon=True)
        self.datei = datei
        self.pos = ab
        self.takt = takt
        self.bei_limit = bei_limit
        self.warte_s = warte_s
        self.halt = threading.Event()
        #: Die gefundene Limit-Zeile — der Rückruf liest daraus die Reset-Uhrzeit (#254).
        self.limit_eintrag: dict[str, Any] | None = None

    def _neue_zeilen(self, rest: bytes) -> tuple[list[bytes], bytes]:
        try:
            with self.datei.open("rb") as strom:
                strom.seek(self.pos)
                stueck = strom.read()
        except OSError as fehler:
            log.debug("Transkript %s (noch) nicht lesbar: %s", self.datei, fehler)
            return [], rest
        self.pos += len(stueck)
        *zeilen, rest = (rest + stueck).split(b"\n")
        return zeilen, rest

    def _limit_in(self, zeilen: list[bytes]) -> bool:
        for roh in zeilen:
            try:
                eintrag = json.loads(roh.decode("utf-8", errors="replace"))
            except ValueError as fehler:
                log.debug("Transkript-Zeile unlesbar (%s): %.80r", fehler, roh)
                continue
            if isinstance(eintrag, dict) and ist_limit_zeile(eintrag):
                self.limit_eintrag = eintrag
                self.bei_limit(_texte(eintrag)[:200] or str(eintrag.get("error")))
                return True
        return False

    def run(self) -> None:
        rest = b""
        beginn = time.monotonic()
        gewarnt = False
        while True:
            zuletzt = self.halt.is_set()  # nach dem Halt genau einmal nachlesen
            zeilen, rest = self._neue_zeilen(rest)
            if self._limit_in(zeilen) or zuletzt:
                return
            if (
                not gewarnt
                and not self.datei.is_file()
                and time.monotonic() - beginn > self.warte_s
            ):
                gewarnt = True
                log.warning(
                    "Transkript %s nach %.0f s nicht da — Limit-Erkennung blind.",
                    self.datei,
                    self.warte_s,
                )
            self.halt.wait(self.takt)


def befehl(
    claude: str,
    modell: str,
    ausweich: str,
    remote_control: bool,
    spec: int,
    prompt: str,
    session_id: str | None = None,
) -> list[str]:
    """Start-Befehl der Wächter-Session."""
    cmd = [claude, "--model", modell]
    if ausweich and ausweich != modell:
        cmd += ["--fallback-model", ausweich]
    if remote_control:
        cmd += ["--remote-control", f"Wächter #{spec}"]
    if session_id:
        cmd += ["--session-id", session_id]
    return [*cmd, prompt]


def _starte(cmd: list[str], cwd: Path) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(cmd, cwd=str(cwd))
    except OSError:
        # Windows: ``claude`` ist ein .cmd-Shim, der nur über die Shell startet.
        return subprocess.Popen(
            " ".join(f'"{c}"' for c in cmd), cwd=str(cwd), shell=True
        )


def _beende(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _log_repo(repo: Path) -> Path | None:
    """Bau-Log-Ziel: Regel des Skills (``TO_SPAWN_LOG_REPO``, fehlt der Ordner → ``None``)."""
    regel = getattr(bau_log, "log_repo", None)
    if callable(regel):
        ziel = regel(repo, versioniert=True)  # schreibt docs/agents/bau_log/ (#257)
        return ziel if isinstance(ziel, Path) else None
    return repo


def _melde_wechsel(repo: Path, spec: int, sid: str, modell: str, ausweich: str, grund: str) -> None:
    """Mail zuerst, dann Zeile ins versionierte Bau-Log — nie ein Grund, den Neustart zu lassen."""
    try:
        melder.melden(
            repo,
            "waechter_ausweich",
            f"Wächter #{spec} läuft auf Ausweich-Modell",
            f"Wächter #{spec}: {modell} hat das Limit erreicht ({grund}) — weiter mit {ausweich}.",
            f"waechter_ausweich|{spec}|{sid}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        log.warning("Wächter #%s: Mail waechter_ausweich gescheitert: %s", spec, fehler)
    _log_zeile(repo, spec, "waechter_modell", von=modell, nach=ausweich, grund=grund)


def _log_zeile(repo: Path, spec: int, typ: str, **felder: Any) -> None:
    """Eine Zeile ins versionierte Bau-Log — nie ein Grund, den Wächter zu stoppen."""
    ziel = _log_repo(repo)
    try:
        if ziel is not None:
            bau_log.eintrag_schreiben(ziel, spec, typ, **felder)
            return
        # Versionierte Datei nicht erreichbar (#257: Worktree weg) — die Zeile darf
        # trotzdem nicht verschwinden, also in die unversionierte Laufdatei (F4).
        rueckfall = bau_log.log_rueckfall() or repo
        bau_log.schreibe(rueckfall, spec, typ, **felder)
        log.warning(
            "Wächter #%s: Zeile %s nur in der Laufdatei unter %s (kein versioniertes Ziel).",
            spec,
            typ,
            rueckfall,
        )
    except (OSError, ValueError) as fehler:
        log.warning(
            "Wächter #%s: Bau-Log-Zeile %s nicht geschrieben (%s) — Wächter läuft weiter.",
            spec,
            typ,
            fehler,
        )


def _melde_pause(
    repo: Path,
    spec: int,
    sid: str,
    modell: str,
    ziel: datetime,
    sekunden: float,
    grund: str,
    runde: int,
) -> None:
    """Mail und Bau-Log-Zeile vor der Limit-Pause (#254).

    Die Runde steht im Melde-Schlüssel: sonst entprellt der Melder die zweite Pause
    derselben Session weg und eine Schleife liefe unsichtbar (F6).
    """
    bis = f"{ziel.astimezone():%d.%m. %H:%M}"
    try:
        melder.melden(
            repo,
            "waechter_pause",
            f"Wächter #{spec} pausiert bis {bis}",
            f"Wächter #{spec}: {modell} hat das Limit erreicht ({grund}). "
            f"Pause {runde} von {MAX_PAUSEN} bis {bis} ({sekunden / 60:.0f} min), "
            "danach fährt er selbst weiter.",
            f"waechter_pause|{spec}|{sid}|{runde}|{int(ziel.timestamp())}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        log.warning("Wächter #%s: Mail waechter_pause gescheitert: %s", spec, fehler)
    _log_zeile(
        repo,
        spec,
        "waechter_pause",
        modell=modell,
        bis=bis,
        sekunden=round(sekunden, 1),
        runde=runde,
        grund=grund,
    )


def _melde_stillstand(
    repo: Path, spec: int, sid: str, modell: str, text: str, grund: str
) -> None:
    """Wächter bleibt stehen: Mail wie vor #254 — und eine Spur im Bau-Log (F5)."""
    try:
        raus = melder.melden(
            repo,
            "session_tot",
            f"Wächter #{spec} steht",
            f"Wächter #{spec} hat auch auf dem Ausweich-Modell {modell} das Limit erreicht: {text}",
            f"session_tot|waechter|{spec}|{sid}|{datetime.now().astimezone():%Y-%m-%d}",
        )
    except (OSError, ValueError, subprocess.SubprocessError) as fehler:
        raus = False
        log.warning("Wächter #%s: Mail session_tot gescheitert: %s", spec, fehler)
    if not raus:
        log.warning(
            "Wächter #%s steht (%s) und es ging KEINE Mail raus — nur diese Zeile.",
            spec,
            grund,
        )
    _log_zeile(repo, spec, "blockiert", modell=modell, grund=grund)


def fahre(
    *,
    claude: str,
    spec: int,
    prompt: str,
    modell: str,
    ausweich: str,
    remote_control: bool,
    repo: Path,
    cwd: Path,
    takt: float = TAKT_S,
    abbruch: Callable[[], bool] | None = None,
    session_id: str | None = None,
    puffer: float = RESET_PUFFER_S,
    hoechstens: float = MAX_WARTE_S,
) -> int:
    """Wächter starten und beaufsichtigen; Rückgabe = Exit-Code der letzten Session.

    ``abbruch`` (optional) wird je Takt gefragt; ``True`` beendet die Session mit Exit 0.
    ``session_id`` (Aufpasser, #236 R1): vorhandenes Gespräch per ``--resume`` fortsetzen
    statt frisch zu starten — nur wenn das Transkript noch da ist, sonst Exit 2.
    Die Gesprächs-ID landet in ``<repo>/.to-spawn/sessions/wache-<S>.json`` (R2).
    Auf dem Ausweich-Modell wartet der Wächter das Limit aus, wenn die Limit-Zeile
    eine Reset-Uhrzeit nennt (#254): ``puffer`` Sekunden obendrauf, länger als
    ``hoechstens`` wird nie gewartet.
    """
    if session_id:
        sid = session_id
        transkript = transkript_ordner(cwd) / f"{sid}.jsonl"
        if not transkript.is_file():
            log.error(
                "Wächter #%s --resume %s: Transkript %s fehlt — kein Start.",
                spec,
                sid,
                transkript,
            )
            return 2
        cmd = [claude, "--resume", sid, "--model", modell]
        if remote_control:
            cmd += ["--remote-control", f"Wächter #{spec}"]
        cmd.append(
            f"Aufpasser: Weiter als Bau-Wächter Spec #{spec} genau dort, wo du warst — "
            "nächster Tick wie gehabt."
        )
    else:
        sid = str(uuid.uuid4())
        cmd = befehl(claude, modell, ausweich, remote_control, spec, prompt, session_id=sid)
    sessions_datei.schreiben(repo, f"wache-{spec}", sid, cwd, 1)
    pausen = 0  # Limit-Pausen dieser Session (Obergrenze MAX_PAUSEN, F1)
    while True:
        datei = transkript_ordner(cwd) / f"{sid}.jsonl"
        ab = datei.stat().st_size if datei.is_file() else 0
        proc = _starte(cmd, cwd)
        limit = threading.Event()
        gruende: list[str] = []
        pause: list[tuple[datetime, float]] = []
        auf_ausweich = not ausweich or modell == ausweich
        # Der Rückruf braucht die ganze Limit-Zeile (Reset-Uhrzeit, #254); die Aufsicht
        # legt sie vorher in ``limit_eintrag`` ab, deshalb wird sie zuerst gebaut.
        aufsicht = Aufsicht(datei, ab, takt, lambda _text: None)

        def bei_limit(
            text: str,
            _proc: subprocess.Popen[bytes] = proc,
            _auf: bool = auf_ausweich,
            _gruende: list[str] = gruende,
            _limit: threading.Event = limit,
            _modell: str = modell,
            _pause: list[tuple[datetime, float]] = pause,
            _aufsicht: Aufsicht = aufsicht,
            _runde: int = pausen + 1,
        ) -> None:
            _gruende.append(text)
            _limit.set()
            log.warning(
                "Wächter #%s: Nutzungs-Limit erkannt (%s) — %s", spec, _modell, text
            )
            if not _auf:
                _beende(_proc)
                return
            ziel = reset_zeitpunkt(_aufsicht.limit_eintrag or {})
            wartezeit = (
                warte_sekunden(ziel, puffer=puffer, hoechstens=hoechstens)
                if ziel is not None
                else None
            )
            if _runde > MAX_PAUSEN:
                # Limit greift nach jedem Neustart sofort wieder — hier hilft nur ein Mensch (F1).
                _melde_stillstand(
                    repo,
                    spec,
                    sid,
                    _modell,
                    text,
                    f"{MAX_PAUSEN} Pausen in Folge halfen nicht",
                )
                return
            if ziel is None or wartezeit is None:
                # Keine brauchbare Uhrzeit → wie vor #254: melden, Fenster stehen lassen.
                _melde_stillstand(
                    repo, spec, sid, _modell, text, "keine brauchbare Reset-Uhrzeit"
                )
                return
            _pause.append((ziel, wartezeit))
            _beende(_proc)

        aufsicht.bei_limit = bei_limit
        aufsicht.start()
        while True:
            try:
                rc = proc.wait(timeout=takt)
                break
            except subprocess.TimeoutExpired:
                if abbruch is not None and abbruch():
                    log.info(
                        "Wächter #%s: Abbruch-Bedingung erfüllt — Session wird beendet.",
                        spec,
                    )
                    aufsicht.halt.set()
                    _beende(proc)
                    return 0
        aufsicht.halt.set()
        aufsicht.join(timeout=takt + 1)
        if not limit.is_set() or (auf_ausweich and not pause):
            return rc

        grund = gruende[0] if gruende else "Nutzungs-Limit"
        if auf_ausweich:
            ziel, wartezeit = pause[0]
            pausen += 1
            _melde_pause(repo, spec, sid, modell, ziel, wartezeit, grund, pausen)
            log.warning(
                "Wächter #%s: Limit-Pause bis %s (%.0f s) — danach fährt er selbst weiter.",
                spec,
                ziel,
                wartezeit,
            )
            if not _warten(wartezeit, takt, abbruch):
                log.info("Wächter #%s: Abbruch während der Limit-Pause.", spec)
                return 0
            _log_zeile(
                repo,
                spec,
                "waechter_weiter",
                modell=modell,
                pause_s=round(wartezeit, 1),
                runde=pausen,
            )
            cmd = [claude, "--resume", sid, "--model", modell]
            if remote_control:
                cmd += ["--remote-control", f"Wächter #{spec}"]
            cmd.append(
                f"Weiter als Bau-Wächter Spec #{spec}: Das Nutzungs-Limit ist seit "
                f"{ziel.astimezone():%H:%M} wieder offen, die Pause ist vorbei. "
                f"Nächster Tick wie gehabt (python scripts/capo.py {spec}); "
                f"docs/agents/bau_log/{spec}.jsonl beim nächsten Handoff-Commit mitnehmen."
            )
            continue

        _melde_wechsel(repo, spec, sid, modell, ausweich, grund)
        weiter = (
            f"Weiter als Bau-Wächter Spec #{spec}: Modell-Wechsel {modell} → {ausweich} wegen Nutzungs-Limit. "
            f"Nächster Tick wie gehabt (python scripts/capo.py {spec}); "
            f"docs/agents/bau_log/{spec}.jsonl beim nächsten Handoff-Commit mitnehmen."
        )
        modell = ausweich
        cmd = [claude, "--resume", sid, "--model", ausweich]
        if remote_control:
            cmd += ["--remote-control", f"Wächter #{spec}"]
        cmd.append(weiter)
