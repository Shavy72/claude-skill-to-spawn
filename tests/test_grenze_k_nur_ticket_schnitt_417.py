"""#417: ``staffel.grenze_k`` ist nur der Ticket-Schnitt, die Ablösung hängt an der Smart-Zone.

Regel des Eigentümers: Grenz-Zahlen für laufende Sessions stehen nur in
``~/.claude/smart-zone.json``; ``staffel.grenze_k`` in ``.to-spawn/config.json`` ist allein
die Planungs-Grenze je Ticket. Diese Tests brechen den Build, sobald jemand eine zweite
Grenz-Quelle baut (neuer Leser von ``grenze_k``) oder die Anleitung den klaren Satz verliert.
"""

from __future__ import annotations

import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

#: Heutige Leser (Grep-Beleg #417): Vorgabe, Ticket-Schnitt, Lernstoff für /to-tickets.
ERLAUBTE_LESER = {
    "to_spawn/config.py",
    "to_spawn/manifest.py",
    "to_spawn/bau_log.py",
}

#: Ein Schlüssel-Zugriff auf das Feld, z. B. ``.get("grenze_k")`` oder ``["grenze_k"]``.
SCHLUESSEL = re.compile(r"""["']grenze_k["']""")

ENDUNGEN = {".py", ".mjs", ".js", ".sh", ".ps1"}


def _leser() -> set[str]:
    gefunden: set[str] = set()
    for ordner in ("to_spawn", "skripte"):
        for datei in (WURZEL / ordner).rglob("*"):
            if not datei.is_file() or datei.suffix not in ENDUNGEN:
                continue
            if "__pycache__" in datei.parts:
                continue
            text = datei.read_text(encoding="utf-8", errors="replace")
            if SCHLUESSEL.search(text):
                gefunden.add(datei.relative_to(WURZEL).as_posix())
    return gefunden


def test_grenze_k_nur_in_erlaubten_modulen_gelesen() -> None:
    fremde = _leser() - ERLAUBTE_LESER
    assert not fremde, (
        f"grenze_k wird in {sorted(fremde)} gelesen. grenze_k ist nur der Ticket-Schnitt; "
        "die Ablösung einer laufenden Session hängt allein an ~/.claude/smart-zone.json (#417)."
    )


def test_stop_hook_kennt_grenze_k_nicht() -> None:
    hook = (WURZEL / "skripte" / "hooks" / "staffel_stop.py").read_text(
        encoding="utf-8"
    )
    assert "grenze_k" not in hook


def test_skill_md_sagt_klar_was_grenze_k_ist() -> None:
    text = (WURZEL / "SKILL.md").read_text(encoding="utf-8")
    assert "`staffel.grenze_k` ist nur der Ticket-Schnitt" in text
    assert "~/.claude/smart-zone.json" in text
    assert "Staffel (200k)" not in text, (
        "Anleitung legt eine feste Ablöse-Zahl im Skill nahe"
    )


def test_konfig_vorgabe_erklaert_grenze_k() -> None:
    text = (WURZEL / "to_spawn" / "config.py").read_text(encoding="utf-8")
    zeilen = text.splitlines()
    nr = next(i for i, z in enumerate(zeilen) if '"grenze_k"' in z)
    kommentar = " ".join(zeilen[max(nr - 4, 0) : nr])
    assert "Ticket-Schnitt" in kommentar and "smart-zone.json" in kommentar
