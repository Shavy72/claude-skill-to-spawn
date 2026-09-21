"""Aufsicht über die Wächter-Session: Nutzungs-Limit erkennen, Ausweich-Modell starten (#213).

``claude --fallback-model`` greift nur bei „overloaded/not available“, nicht beim
Nutzungs-Limit („You've hit your session limit“, „You've reached your Fable limit“).
Deshalb startet :func:`fahre` den Wächter selbst (``Popen``) und liest in einem
Hintergrund-Faden die neuen Zeilen seines Transkripts
``~/.claude/projects/<cwd>/<session-id>.jsonl``. Taucht die Limit-Zeile auf:

* Wächter läuft noch auf dem Haupt-Modell → Prozess beenden, Bau-Log-Zeile
  ``waechter_modell``, Mail ``waechter_ausweich``, Neustart per
  ``claude --resume <session-id> --model <Ausweich>`` mit kurzem Weiter-Prompt.
* Wächter läuft schon auf dem Ausweich-Modell → nur Mail ``session_tot`` (er steht).

Der Aufpasser (#236) setzt ein stilles Wächter-Fenster über ``fahre(session_id=…)``
mit ``--resume`` fort; die Gesprächs-ID steht in ``.to-spawn/sessions/wache-<S>.json``.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from . import bau_log, melder, sessions_datei

log = logging.getLogger("to_spawn.waechter_lauf")

_LIMIT_TEXT = re.compile(r"(reached|hit) your .*limit", re.IGNORECASE)
TAKT_S = 5.0
#: Sekunden, nach denen ein fehlendes Transkript eine Warnung wert ist.
WARTE_TRANSKRIPT_S = 60.0


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
    ziel = _log_repo(repo)
    if ziel is None:
        log.info("Wächter #%s: kein Bau-Log-Ziel — Zeile waechter_modell entfällt.", spec)
        return
    try:
        bau_log.eintrag_schreiben(
            ziel, spec, "waechter_modell", von=modell, nach=ausweich, grund=grund
        )
    except (OSError, ValueError) as fehler:
        log.warning(
            "Wächter #%s: Bau-Log-Zeile waechter_modell nicht geschrieben (%s) — Neustart trotzdem.",
            spec,
            fehler,
        )


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
) -> int:
    """Wächter starten und beaufsichtigen; Rückgabe = Exit-Code der letzten Session.

    ``abbruch`` (optional) wird je Takt gefragt; ``True`` beendet die Session mit Exit 0.
    ``session_id`` (Aufpasser, #236 R1): vorhandenes Gespräch per ``--resume`` fortsetzen
    statt frisch zu starten — nur wenn das Transkript noch da ist, sonst Exit 2.
    Die Gesprächs-ID landet in ``<repo>/.to-spawn/sessions/wache-<S>.json`` (R2).
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
    while True:
        datei = transkript_ordner(cwd) / f"{sid}.jsonl"
        ab = datei.stat().st_size if datei.is_file() else 0
        proc = _starte(cmd, cwd)
        limit = threading.Event()
        gruende: list[str] = []
        auf_ausweich = not ausweich or modell == ausweich

        def bei_limit(
            text: str,
            _proc: subprocess.Popen[bytes] = proc,
            _auf: bool = auf_ausweich,
            _gruende: list[str] = gruende,
            _limit: threading.Event = limit,
            _modell: str = modell,
        ) -> None:
            _gruende.append(text)
            _limit.set()
            log.warning(
                "Wächter #%s: Nutzungs-Limit erkannt (%s) — %s", spec, _modell, text
            )
            if _auf:
                melder.melden(
                    repo,
                    "session_tot",
                    f"Wächter #{spec} steht",
                    f"Wächter #{spec} hat auch auf dem Ausweich-Modell {_modell} das Limit erreicht: {text}",
                    f"session_tot|waechter|{spec}|{sid}|{datetime.now().astimezone():%Y-%m-%d}",
                )
            else:
                _beende(_proc)

        aufsicht = Aufsicht(datei, ab, takt, bei_limit)
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
        if not limit.is_set() or auf_ausweich:
            return rc

        grund = gruende[0] if gruende else "Nutzungs-Limit"
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
