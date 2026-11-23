"""#323: Texte an Sessions nennen die Rolle („der Mensch“), keinen festen Personennamen.

Gilt für die drei Prompt-Quellen Aufseher-Takt, Aufseher-Prompt und Mindset sowie (#325)
für die Ticketstand-Ausgabe, die der Aufseher jeden Takt wörtlich vorn zeigt, den Melder und
``SKILL.md``. Das Muster steht nur hier; ``test_feste_namen_l1.py`` deckt die übrigen festen Namen ab.
"""

from __future__ import annotations

import re
import tokenize
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
#: Ab Python 3.12 zerlegt ``tokenize`` f-Strings; der Text steht dann in FSTRING_MIDDLE.
FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)


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


#: Teil 2 (#325): Diese Dateien führen den Namen noch in Code-Bezeichnern und Daten-Schlüsseln
#: (z. B. ``davids_antwort``, ``braucht_david``, ``art == "david"``), deren Umbenennung
#: gespeicherte Stände brechen würde. Geprüft werden darum nur Text-Bausteine (Strings,
#: Docstrings, Kommentare) per ``tokenize``; Bezeichner sind NAME-Tokens und fallen heraus.
TEXT_DATEIEN = (
    "to_spawn/aufpasser.py",
    "to_spawn/capo.py",
    "to_spawn/hausmeister.py",
    "skripte/leitstand.py",
    "skripte/abschluss_paket.py",
)
#: Einziger erlaubter Text-Baustein: der Daten-Schlüssel der Ereignis-Art im Leitstand.
DATEN_SCHLUESSEL = frozenset({'"david"'})


def _text_funde(datei: str) -> list[str]:
    funde: list[str] = []
    with (SKILL / datei).open(encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT, FSTRING_MIDDLE):
                continue
            if tok.string in DATEN_SCHLUESSEL:
                continue
            for versatz, zeile in enumerate(tok.string.splitlines()):
                # Escape „\n“ steht roh im Token und verdeckt sonst die Wortgrenze.
                if NAME.search(zeile.replace("\\n", " ")):
                    funde.append(f"{datei}:{tok.start[0] + versatz}")
    return funde


def test_kein_fester_personenname_in_ausgabe_texten() -> None:
    funde = [fund for datei in TEXT_DATEIEN for fund in _text_funde(datei)]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"
