"""#323: Texte an Sessions nennen die Rolle („der Mensch“), keinen festen Personennamen.

Gilt für die drei Prompt-Quellen Aufseher-Takt, Aufseher-Prompt und Mindset sowie (#325)
für die Ticketstand-Ausgabe, die der Aufseher jeden Takt wörtlich vorn zeigt, den Melder und
``SKILL.md``. Das Muster steht nur hier; ``test_feste_namen_l1.py`` deckt die übrigen festen Namen ab.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

DATEIEN = (
    "to_spawn/waechter_takt.py",
    "skripte/wache.py",
    "to_spawn/anleitung.py",
    "to_spawn/ticketstand.py",
    "to_spawn/melder.py",
    "SKILL.md",
)
#: Wortgrenze nur vorn: trifft auch die Genitiv-Form, aber keine Wörter, die den Namen
#: nur enthalten (#325).
NAME = re.compile(r"\bdavid", re.IGNORECASE)


def test_kein_fester_personenname_in_prompt_quellen() -> None:
    funde = [
        f"{datei}:{nr}"
        for datei in DATEIEN
        for nr, zeile in enumerate(
            (SKILL / datei).read_text(encoding="utf-8").splitlines(), 1
        )
        if NAME.search(zeile)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"
