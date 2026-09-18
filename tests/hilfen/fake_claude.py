"""Gestelltes ``claude``-Programm für den Weg-Test (die einzige echte Attrappe).

Verhält sich wie eine Bau-Session: liest den Prompt (letztes Argument), legt ein
Mini-Transkript mit ``message.usage`` an und endet je nach Szenario.

Umgebung:
  ``FAKE_CLAUDE_SZENARIO``  ``fertig`` (endet einfach), ``handoff``
                            (erster Lauf schreibt Handoff + Marker) oder ``hooks``
                            (führt die Hooks aus ``--settings`` aus wie Claude Code, #204)
  ``FAKE_CLAUDE_AUSGABE``   Ordner für Prompt-Mitschriften und Transkripte
  ``TO_SPAWN_TICKET``       Ticket-Nummer (setzt die Staffel-Schleife)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> int:
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    ausgabe = Path(os.environ.get("FAKE_CLAUDE_AUSGABE") or Path.cwd() / ".testlauf")
    ausgabe.mkdir(parents=True, exist_ok=True)
    ticket = os.environ.get("TO_SPAWN_TICKET", "0")

    zaehler_datei = ausgabe / f"aufrufe-{ticket}.txt"
    aufruf = int(zaehler_datei.read_text(encoding="utf-8")) + 1 if zaehler_datei.is_file() else 1
    zaehler_datei.write_text(str(aufruf), encoding="utf-8")
    (ausgabe / f"prompt-{ticket}-{aufruf}.txt").write_text(prompt, encoding="utf-8")

    jetzt = datetime.now(timezone.utc).isoformat()
    transkript = ausgabe / f"transkript-{ticket}-{aufruf}.jsonl"
    with transkript.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": jetzt,
                    "message": {"model": "fake-modell", "usage": {"input_tokens": 10}},
                }
            )
            + "\n"
        )

    szenario = os.environ.get("FAKE_CLAUDE_SZENARIO", "fertig")
    if szenario == "handoff" and aufruf == 1:
        repo = Path.cwd()
        ordner = repo / "docs" / "handoffs"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / f"HANDOFF_2026-09-18_{ticket}.md").write_text(
            "Stand: Hälfte gebaut, Rest offen — Fortsetzung übernimmt.\n",
            encoding="utf-8",
        )
        marker = repo / ".to-spawn" / f"stop-{ticket}"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("handoff", encoding="utf-8")
    if szenario == "hooks":
        return _hooks_szenario(ausgabe, ticket)
    return 0


# --- Szenario "hooks" (#204) --------------------------------------------------

#: Feste Startzeit des gestellten Transkripts; Einträge liegen 60 s auseinander.
HOOK_BEGINN = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
HAUPT_SESSION = "sess-haupt-204"
SUBAGENT_ID = "agent-204"


def _zeit(sekunden: int) -> str:
    return (HOOK_BEGINN + timedelta(seconds=sekunden)).isoformat().replace("+00:00", "Z")


def _assistant(
    sekunden: int,
    modell: str,
    usage: dict,
    sidechain: bool = False,
    nachricht_id: str | None = None,
    anfrage_id: str | None = None,
) -> dict:
    """assistant-Zeile; ``nachricht_id``/``anfrage_id`` wie bei Claude Code (Fixrunde #204)."""
    zeile = {
        "type": "assistant",
        "isSidechain": sidechain,
        "sessionId": HAUPT_SESSION,
        "timestamp": _zeit(sekunden),
        "message": {"model": modell, "usage": usage},
    }
    if nachricht_id:
        zeile["message"]["id"] = nachricht_id
    if anfrage_id:
        zeile["requestId"] = anfrage_id
    return zeile


def _mit_teilzeile(zeile: dict, versatz: int = 1) -> list[dict]:
    """Claude Code schreibt eine Antwort als mehrere Zeilen mit gleicher Kennung und
    gleicher ``usage`` (je Inhaltsblock eine Zeile). Die Teilzeile darf nicht doppelt
    zählen (Fixrunde #204)."""
    teil = json.loads(json.dumps(zeile))
    teil["timestamp"] = (
        datetime.fromisoformat(zeile["timestamp"].replace("Z", "+00:00"))
        + timedelta(seconds=versatz)
    ).isoformat().replace("+00:00", "Z")
    return [zeile, teil]


def _anhaengen(pfad: Path, zeilen: list[dict]) -> None:
    with pfad.open("a", encoding="utf-8") as fh:
        for zeile in zeilen:
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _hook_befehle(ereignis: str) -> list[str]:
    """Befehle eines Hook-Ereignisses aus der Datei hinter ``--settings``."""
    if "--settings" not in sys.argv:
        return []
    datei = Path(sys.argv[sys.argv.index("--settings") + 1])
    daten = json.loads(datei.read_text(encoding="utf-8"))
    befehle: list[str] = []
    for gruppe in (daten.get("hooks") or {}).get(ereignis) or []:
        for hook in gruppe.get("hooks") or []:
            if hook.get("type") == "command" and hook.get("command"):
                befehle.append(str(hook["command"]))
    return befehle


def _hooks_ausfuehren(ausgabe: Path, ticket: str, ereignis: str, eingabe: dict) -> None:
    """Wie Claude Code: jeder Befehl per Shell, JSON auf stdin, Ergebnis mitschreiben."""
    for befehl in _hook_befehle(ereignis):
        lauf = subprocess.run(
            befehl,
            shell=True,
            input=json.dumps(eingabe, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        _anhaengen(
            ausgabe / f"hooks-{ticket}.jsonl",
            [
                {
                    "ereignis": ereignis,
                    "befehl": befehl,
                    "code": lauf.returncode,
                    "stderr": lauf.stderr[-800:],
                }
            ],
        )


def _hooks_szenario(ausgabe: Path, ticket: str) -> int:
    """Zwei Runden (Stop feuert je Runde) plus ein Subagent — Token-Werte sind bekannt."""
    haupt = ausgabe / f"hooks-transkript-{ticket}.jsonl"
    haupt.unlink(missing_ok=True)
    sub = ausgabe / f"hooks-subagent-{ticket}.jsonl"
    sub.unlink(missing_ok=True)
    _anhaengen(
        haupt,
        [
            {"type": "user", "sessionId": HAUPT_SESSION, "timestamp": _zeit(0)},
            *_mit_teilzeile(
                _assistant(
                    60,
                    "claude-opus-5",
                    {
                        "input_tokens": 1000,
                        "cache_read_input_tokens": 20000,
                        "cache_creation_input_tokens": 3000,
                        "output_tokens": 400,
                    },
                    nachricht_id="msg_runde1",
                    anfrage_id="req_runde1",
                )
            ),
        ],
    )
    stop = {
        "session_id": HAUPT_SESSION,
        "transcript_path": str(haupt),
        "cwd": str(Path.cwd()),
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Runde 1 fertig.",
    }
    _hooks_ausfuehren(ausgabe, ticket, "Stop", stop)

    _anhaengen(
        sub,
        [
            *_mit_teilzeile(
                _assistant(
                    120,
                    "claude-sonnet-5",
                    {
                        "input_tokens": 200,
                        "cache_read_input_tokens": 5000,
                        "cache_creation_input_tokens": 800,
                        "output_tokens": 100,
                    },
                    sidechain=True,
                    nachricht_id="msg_subagent1",
                )
            )
        ],
    )
    _hooks_ausfuehren(
        ausgabe,
        ticket,
        "SubagentStop",
        {
            "session_id": HAUPT_SESSION,
            "transcript_path": str(haupt),
            "agent_id": SUBAGENT_ID,
            "agent_type": "executor-sonnet",
            "agent_transcript_path": str(sub),
            "cwd": str(Path.cwd()),
            "hook_event_name": "SubagentStop",
            "stop_hook_active": False,
            "last_assistant_message": "Subagent fertig.",
        },
    )

    _anhaengen(
        haupt,
        [
            {"type": "user", "sessionId": HAUPT_SESSION, "timestamp": _zeit(180)},
            # Runde 2 nur mit requestId (ohne message.id) — Ersatz-Schlüssel.
            *_mit_teilzeile(
                _assistant(
                    240,
                    "claude-opus-5",
                    {
                        "input_tokens": 500,
                        "cache_read_input_tokens": 30000,
                        "cache_creation_input_tokens": 1000,
                        "output_tokens": 600,
                    },
                    anfrage_id="req_runde2",
                )
            ),
        ],
    )
    time.sleep(1.2)  # Dauer aus TO_SPAWN_START wird sonst 0 s
    _hooks_ausfuehren(ausgabe, ticket, "Stop", {**stop, "last_assistant_message": "Runde 2 fertig."})
    return 0


if __name__ == "__main__":
    sys.exit(main())
