"""#323: Texte an Sessions nennen die Rolle („der Mensch“), keinen festen Personennamen.

Gilt für die drei Prompt-Quellen Aufseher-Takt, Aufseher-Prompt und Mindset sowie (#325)
für die Ticketstand-Ausgabe, die der Aufseher jeden Takt wörtlich vorn zeigt, den Melder und
``SKILL.md``. Das Muster steht nur hier; ``test_feste_namen_l1.py`` deckt die übrigen festen Namen ab.
"""

from __future__ import annotations

import ast
import re
import sys
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
    "to_spawn/hausmeister_aufgabe.py",
    "to_spawn.py",
)
#: Einziger erlaubter Text-Baustein: der Daten-Schlüssel der Ereignis-Art im Leitstand.
DATEN_SCHLUESSEL = frozenset({'"david"'})
ESCAPE = re.compile(r"\\[ntr]")


def _text_funde(datei: str) -> list[str]:
    funde: list[str] = []
    with (SKILL / datei).open(encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT, FSTRING_MIDDLE):
                continue
            if tok.string in DATEN_SCHLUESSEL:
                continue
            for versatz, zeile in enumerate(tok.string.splitlines()):
                # Escapes „\n“, „\t“, „\r“ stehen roh im Token und verdecken sonst die
                # Wortgrenze.
                if NAME.search(ESCAPE.sub(" ", zeile)):
                    funde.append(f"{datei}:{tok.start[0] + versatz}")
    return funde


def test_kein_fester_personenname_in_ausgabe_texten() -> None:
    funde = [fund for datei in TEXT_DATEIEN for fund in _text_funde(datei)]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def test_escape_verdeckt_namen_nicht(tmp_path: Path, monkeypatch) -> None:
    """Teil 3 (#325): Name direkt hinter ``\\n``, ``\\t`` oder ``\\r`` wird erkannt."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        'A = "x\\nDavid"\nB = "x\\tDavid"\nC = "x\\rDavid"\n', encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "SKILL", tmp_path)
    assert _text_funde("probe.py") == ["probe.py:1", "probe.py:2", "probe.py:3"]


#: Teil 3 (#325): Die Leitstand-Seite führt den Namen in CSS-Variablen, Klassen, IDs und
#: JS-Schlüsseln (``--david``, ``z-david``, ``braucht_david``). Geprüft werden darum nur
#: Kommentare (HTML, CSS, JS) — dort steht Wortlaut für Menschen; der Daten-Schlüssel
#: ``"david"`` darf darin genannt werden.
HTML_DATEIEN = ("leitstand/seite.html",)
KOMMENTAR = re.compile(r"<!--.*?-->|/\*.*?\*/|(?<![:\\])//[^\n]*", re.DOTALL)


def test_kein_fester_personenname_in_html_kommentaren() -> None:
    funde = []
    for datei in HTML_DATEIEN:
        text = (SKILL / datei).read_text(encoding="utf-8")
        for treffer in KOMMENTAR.finditer(text):
            kommentar = treffer.group()
            for schluessel in DATEN_SCHLUESSEL:
                kommentar = kommentar.replace(schluessel, "")
            if NAME.search(kommentar):
                funde.append(f"{datei}:{text.count(chr(10), 0, treffer.start()) + 1}")
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


#: Teil 4 (#325): weitere Python-Dateien mit Namen nur in Text-Bausteinen; Bezeichner wie
#: ``davids_antwort`` bleiben (siehe ``TEXT_DATEIEN``).
TEXT_DATEIEN_TEIL4 = (
    "skripte/rueckblick.py",
    "to_spawn/befund.py",
    "to_spawn/manifest.py",
    "to_spawn/staging_schalter.py",
    "to_spawn/waechter_lauf.py",
)
#: Teil 4 (#325): Markdown ohne Bezeichner — jede Zeile wird geprüft.
MARKDOWN_ORDNER = ("kette",)
MARKDOWN_DATEIEN = ("to_spawn/README.md",)
#: Teil 4 (#325): Diese Test-Dateien gehören zu anderen offenen Pull-Requests oder führen
#: Namen absichtlich (Prüflisten) und sind hier ausgenommen.
TESTS_AUSGENOMMEN = frozenset(
    {
        "tests/test_rolle_statt_name_323.py",
        "tests/test_installer_vollstaendig.py",
        "tests/test_starter_1406.py",
        "tests/test_regel_kopie_274.py",
        "tests/test_feste_namen_l1.py",
        "tests/test_einrichten_l1.py",
        "tests/test_starter_shell_l1.py",
    }
)


def test_kein_fester_personenname_in_ausgabe_texten_teil4() -> None:
    funde = [fund for datei in TEXT_DATEIEN_TEIL4 for fund in _text_funde(datei)]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def test_kein_fester_personenname_in_markdown() -> None:
    dateien = [
        str(p.relative_to(SKILL))
        for o in MARKDOWN_ORDNER
        for p in (SKILL / o).rglob("*.md")
    ]
    funde = [
        f"{datei}:{nr}"
        for datei in sorted(dateien) + list(MARKDOWN_DATEIEN)
        for nr, zeile in enumerate(
            (SKILL / datei).read_text(encoding="utf-8").splitlines(), 1
        )
        if NAME.search(zeile)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def _kommentar_und_docstring_funde(datei: str) -> list[str]:
    """Funde in Kommentaren und Docstrings; Test-Daten (übrige Strings) bleiben außen vor."""
    pfad = SKILL / datei
    text = pfad.read_text(encoding="utf-8")
    funde: list[str] = []
    with pfad.open(encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type == tokenize.COMMENT and NAME.search(tok.string):
                funde.append(f"{datei}:{tok.start[0]}")
    for knoten in ast.walk(ast.parse(text)):
        if not isinstance(
            knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if not (knoten.body and isinstance(knoten.body[0], ast.Expr)):
            continue
        wert = knoten.body[0].value
        if isinstance(wert, ast.Constant) and isinstance(wert.value, str):
            for versatz, zeile in enumerate(wert.value.splitlines()):
                if NAME.search(zeile):
                    funde.append(f"{datei}:{wert.lineno + versatz}")
    return funde


def test_kein_fester_personenname_in_test_kommentaren() -> None:
    """Teil 4 (#325): Kommentare und Docstrings der Tests nennen die Rolle."""
    dateien = sorted(
        rel
        for p in (SKILL / "tests").rglob("*.py")
        if (rel := str(p.relative_to(SKILL))) not in TESTS_AUSGENOMMEN
        and not Path(rel).name.startswith("test_starttext_")
    )
    funde = [
        fund for datei in dateien for fund in _kommentar_und_docstring_funde(datei)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def test_kein_grossgeschriebener_name_in_html_fliesstext() -> None:
    """Teil 4 (#325): Fließtext der Leitstand-Seite (HTML-Text, JS-Strings) nennt die Rolle.

    Klassen, IDs, CSS-Variablen und Daten-Schlüssel schreiben den Namen klein
    (``z-david``, ``braucht_david``); großgeschrieben steht er nur in Wortlaut für Menschen.
    """
    funde = [
        f"{datei}:{nr}"
        for datei in HTML_DATEIEN
        for nr, zeile in enumerate(
            (SKILL / datei).read_text(encoding="utf-8").splitlines(), 1
        )
        if re.search(r"\bDavid", zeile)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


#: Teil 5 (#325): Doku ohne Bezeichner — jede Zeile wird geprüft. Lastenheft und Plan-Log
#: zitieren den Wortlaut des Menschen bzw. führen ein laufendes Entscheidungs-Log und bleiben außen vor.
DOKU_DATEIEN_TEIL5 = (
    "docs/ABNAHME_L1.md",
    "docs/PLAN_438_gelb_liste.md",
    "docs/PLAN_257_fremdrepo.md",
    "docs/WIEDEREINSTIEG.md",
)


def test_kein_fester_personenname_in_doku_teil5() -> None:
    funde = [
        f"{datei}:{nr}"
        for datei in DOKU_DATEIEN_TEIL5
        for nr, zeile in enumerate(
            (SKILL / datei).read_text(encoding="utf-8").splitlines(), 1
        )
        if NAME.search(zeile)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def test_kein_fester_personenname_in_test_gruenden() -> None:
    """Teil 5 (#325): ``reason=`` von xfail/skip ist Wortlaut für Menschen, keine Test-Daten."""
    funde: list[str] = []
    for p in sorted((SKILL / "tests").rglob("*.py")):
        rel = str(p.relative_to(SKILL))
        if rel in TESTS_AUSGENOMMEN:
            continue
        for knoten in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(knoten, ast.Call):
                continue
            for kw in knoten.keywords:
                if (
                    kw.arg == "reason"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and NAME.search(kw.value.value)
                ):
                    funde.append(f"{rel}:{kw.value.lineno}")
    assert funde == [], f"fester Personenname statt Rolle: {funde}"


def test_kein_fester_personenname_in_shell_kommentaren() -> None:
    """Teil 5 (#325): Kommentarzeilen aller Shell-Skripte im Skill nennen die Rolle."""
    funde = [
        f"{p.relative_to(SKILL)}:{nr}"
        for p in sorted(SKILL.rglob("*.sh"))
        if ".git" not in p.parts
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if zeile.lstrip().startswith("#") and NAME.search(zeile)
    ]
    assert funde == [], f"fester Personenname statt Rolle: {funde}"
