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
from collections.abc import Mapping

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
