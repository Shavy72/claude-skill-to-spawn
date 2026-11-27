"""Umgebung für neue Claude-Sessions ohne geerbte Sitzungs-Variablen.

Warum: Startet ein Skript (bau, wache, Leitstand, Probesitz) eine neue Claude-Session
aus einer laufenden Session heraus, erbt sie deren Sitzungs-Variablen
(``CLAUDECODE``, ``CLAUDE_CODE_SESSION_*``, ``CLAUDE_CODE_CHILD_SESSION`` …). Die neue
Session hält sich dann für eine „Child Session“: kein eigenes Transcript, kein Resume,
keine Nachrichten zwischen Sessions. Einstellungs-Variablen wie
``CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`` oder ``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` bleiben.

Muster-Vorlage: ``~/.claude/bin/claude-frisch.ps1`` (Shell-Starts); Node-Gegenstück:
``SITZUNGS_MUSTER`` in ``hooks/smart-zone/staffel/staffel-lib.mjs``.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

#: Wurzel dieses laufenden Skills (Ordner über ``to_spawn/``).
_SKILL_EIGEN = Path(__file__).resolve().parent.parent

#: Umgebungsvariablen, die jedes neu gestartete Bau-/Aufseher-Fenster aus der
#: Start-Umgebung erben muss — für ``spawn_srv.sh``, Aufseher/``capo``, Ablösung,
#: Aufpasser und Windows-Tabs an genau dieser einen Stelle gebündelt (#846).
START_ERB_VARIABLEN: tuple[str, ...] = ("TO_SPAWN_MASCHINE",)

SITZUNGS_MUSTER = re.compile(
    r"^(CLAUDECODE|CLAUDE_PID|CLAUDE_EFFORT|CLAUDE_CODE_CHILD_SESSION|CLAUDE_CODE_SESSION_.*"
    r"|CLAUDE_CODE_MESSAGING_.*|CLAUDE_CODE_BRIDGE_.*|CLAUDE_CODE_SSE_PORT"
    r"|CLAUDE_CODE_ENTRYPOINT|CLAUDE_CODE_EXECPATH)$"
)


def ist_sitzungs_variable(name: str) -> bool:
    """True, wenn ``name`` an die laufende Session gebunden ist und nicht vererbt werden darf."""
    return SITZUNGS_MUSTER.match(name) is not None


def ohne_sitzung(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Kopie von ``env`` (Standard: ``os.environ``) ohne Sitzungs-Variablen.

    Das Original bleibt unverändert; Aufrufer übergeben das Ergebnis als ``env=`` an
    ``subprocess`` oder schreiben es zurück in ``os.environ``.
    """
    quelle = os.environ if env is None else env
    return {k: v for k, v in quelle.items() if not ist_sitzungs_variable(k)}


def fenster_umgebung(
    repo: Path | str,
    *,
    skill_home: Path | str | None = None,
    warte_datei: Path | str | None = None,
    auftrag: str = "",
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Einzige Stelle für die Umgebung neu gestarteter Bau-/Aufseher-Fenster (#846).

    Bündelt ``REPO``, ``TO_SPAWN_HOME``, ``TO_SPAWN_REPO``, optional
    ``TO_SPAWN_WARTE_DATEI`` und ``BAU_AUFTRAG`` sowie alle geerbten Start-Variablen
    (:data:`START_ERB_VARIABLEN`, z. B. ``TO_SPAWN_MASCHINE``), sobald sie in ``env``
    (Standard: ``os.environ``) nicht-leer gesetzt sind.
    """
    quelle = os.environ if env is None else env
    ort = str(repo)
    if skill_home is not None:
        skill = str(skill_home)
    else:
        skill = (quelle.get("TO_SPAWN_HOME") or "").strip() or str(_SKILL_EIGEN)
    werte: dict[str, str] = {
        "REPO": ort,
        "TO_SPAWN_HOME": skill,
        "TO_SPAWN_REPO": ort,
    }
    if warte_datei is not None and str(warte_datei).strip():
        werte["TO_SPAWN_WARTE_DATEI"] = str(warte_datei)
    for name in START_ERB_VARIABLEN:
        wert = (quelle.get(name) or "").strip()
        if wert:
            werte[name] = wert
    if auftrag.strip():
        werte["BAU_AUFTRAG"] = auftrag.strip()
    return werte


def shell_vorspann(
    repo: Path | str,
    *,
    skill_home: Path | str | None = None,
    warte_datei: Path | str | None = None,
    auftrag: str = "",
    env: Mapping[str, str] | None = None,
) -> str:
    """POSIX-Shell-Vorspann ``export ...;`` aus :func:`fenster_umgebung` (#846)."""
    werte = fenster_umgebung(
        repo,
        skill_home=skill_home,
        warte_datei=warte_datei,
        auftrag=auftrag,
        env=env,
    )
    paare = " ".join(f"{name}={shlex.quote(wert)}" for name, wert in werte.items())
    return f"export {paare};"


def _pwsh_env_setzen(name: str, wert: str) -> str:
    """Einzelne PowerShell-Zuweisung ohne ``;`` (``wt`` trennt Tabs an ``;``)."""
    return f"Set-Item Env:{name} '{wert.replace(chr(39), chr(39) * 2)}'"


def pwsh_vorspann(
    repo: Path | str,
    *,
    skill_home: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """PowerShell-Vorspann (ohne ``;``) für neue Windows-Terminal-Tabs (#846).

    Ein neuer Tab in einem bestehenden ``wt``-Fenster (``wt -w 0 new-tab``) erbt die
    Umgebung des laufenden Terminals statt die des Aufrufers. Dieser Vorspann reicht
    ``TO_SPAWN_HOME`` (falls explizit gesetzt) und :data:`START_ERB_VARIABLEN`
    (``TO_SPAWN_MASCHINE``) über ``Set-Item Env:...`` verkettet mit ``&&`` weiter.
    """
    quelle = os.environ if env is None else env
    werte = fenster_umgebung(repo, skill_home=skill_home, env=quelle)
    teile: list[str] = []
    if skill_home is not None or (quelle.get("TO_SPAWN_HOME") or "").strip():
        teile.append(_pwsh_env_setzen("TO_SPAWN_HOME", werte["TO_SPAWN_HOME"]))
    for name in START_ERB_VARIABLEN:
        if name in werte:
            teile.append(_pwsh_env_setzen(name, werte[name]))
    return " && ".join(teile)
