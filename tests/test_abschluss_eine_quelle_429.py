"""#429: Thermo-/Rückblick- und Abschluss-Anweisung stehen nur einmal im Quelltext.

Die gemeinsame Quelle ist ``to_spawn/anleitung.py`` (``ABSCHLUSS_ANWEISUNG``, angelegt
in #367); ``skripte/wache.py`` und ``to_spawn/waechter_takt.py`` lesen von dort.
Dieser Test schützt gegen erneutes Kopieren des Wortlauts in eine zweite Datei.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from conftest import repo_dateien

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import anleitung, waechter_takt

#: Eindeutige Satzteile der Abschluss-, Thermo- und Rückblick-Anweisung.
#: Jeder Satzteil muss genau einmal in ``to_spawn/anleitung.py`` stehen und in keiner
#: anderen getrackten Quelldatei des Skills (außer ``tests/``, ``docs/``, ``.review/``).
MERKMALE: tuple[str, ...] = (
    "Danach Aufbau-Prüfung (einmal):",
    "thermo_lauf.py plan {S}",
    "thermo_lauf.py sammeln {S}",
    "Kein Umbau in der Spec — Befunde stehen als Code-Befunde",
    "Danach Rückblick (einmal):",
    "rueckblick.py planen {S}",
    "rueckblick.py sammeln {S}",
    "Exit 0 → ein Subagent (`model: opus`, Skill mp-retro)",
    "PFLICHT Abschluss-Paket `--stand abnahme` (einmal):",
    "Nach dem Live-Deploy dasselbe einmal mit `--stand live`",
)

AUSGENOMMENE_ORDNER: frozenset[str] = frozenset({".review", "tests", "docs"})


def _lade_wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_eine_quelle_429", SKILL / "skripte" / "wache.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def quelltext_fundorte(
    wurzel: Path,
    merkmale: Iterable[str] = MERKMALE,
) -> dict[str, list[tuple[str, int]]]:
    """Liefert je Merkmal alle ``(rel_pfad, anzahl)``-Treffer in getrackten Quelldateien."""
    liste = tuple(merkmale)
    fundorte: dict[str, list[tuple[str, int]]] = {m: [] for m in liste}
    for datei in repo_dateien(wurzel):
        rel = datei.relative_to(wurzel)
        if rel.parts and rel.parts[0] in AUSGENOMMENE_ORDNER:
            continue
        try:
            inhalt = datei.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in liste:
            anzahl = inhalt.count(m)
            if anzahl:
                fundorte[m].append((rel.as_posix(), anzahl))
    return fundorte


def test_thermo_und_rueckblick_wortlaut_nur_einmal_im_quelltext() -> None:
    """Wortlaut der Thermo-/Rückblick-/Abschluss-Anweisung steht genau einmal im Quelltext."""
    for merkmal in MERKMALE:
        assert anleitung.ABSCHLUSS_ANWEISUNG.count(merkmal) == 1, merkmal

    fundorte = quelltext_fundorte(SKILL)
    abweichungen = {m: orte for m, orte in fundorte.items() if orte != [("to_spawn/anleitung.py", 1)]}
    assert not abweichungen, f"Abschluss-/Thermo-/Rückblick-Wortlaut nicht nur einmal in anleitung.py: {abweichungen}"


def test_wache_und_takt_lesen_konstante_ohne_inline_kopie() -> None:
    """``skripte/wache.py`` und ``to_spawn/waechter_takt.py`` referenzieren die Konstante ohne Kopie."""
    for rel in ("skripte/wache.py", "to_spawn/waechter_takt.py"):
        quelltext = (SKILL / rel).read_text(encoding="utf-8")
        assert "anleitung.ABSCHLUSS_ANWEISUNG" in quelltext, rel
        doppelt = [m for m in MERKMALE if m in quelltext]
        assert not doppelt, f"{rel} enthält doppelten Wortlaut: {doppelt}"

    wache = _lade_wache()
    assert anleitung.ABSCHLUSS_ANWEISUNG in wache.PROMPT
    assert anleitung.ABSCHLUSS_ANWEISUNG in waechter_takt.PROMPT

    wache_text = wache.prompt_bauen(429, "o/r", 1800, {})
    takt_text = waechter_takt.baue_prompt(429, SKILL, ["- #429 prüfen"], None)
    for text in (wache_text, takt_text):
        assert "thermo_lauf.py plan 429" in text
        assert "thermo_lauf.py sammeln 429" in text
        assert "rueckblick.py planen 429" in text
        assert "rueckblick.py sammeln 429" in text


def test_quelltext_waechter_meldet_doppelte_kopie(tmp_path: Path) -> None:
    """Selbsttest: Eine Kopie der Thermo-/Rückblick-Sätze in wache.py/waechter_takt.py wird erkannt."""
    (tmp_path / "to_spawn").mkdir()
    (tmp_path / "skripte").mkdir()
    probe = MERKMALE[0] + " " + MERKMALE[4]
    (tmp_path / "to_spawn" / "anleitung.py").write_text(f'ABSCHLUSS_ANWEISUNG = "{probe}"\n', encoding="utf-8")
    (tmp_path / "skripte" / "wache.py").write_text(f'PROMPT = "{probe}"\n', encoding="utf-8")
    (tmp_path / "to_spawn" / "waechter_takt.py").write_text(f'PROMPT = "{probe}"\n', encoding="utf-8")

    fundorte = quelltext_fundorte(tmp_path, (MERKMALE[0], MERKMALE[4]))
    for m in (MERKMALE[0], MERKMALE[4]):
        assert sorted(fundorte[m]) == [
            ("skripte/wache.py", 1),
            ("to_spawn/anleitung.py", 1),
            ("to_spawn/waechter_takt.py", 1),
        ]

    (tmp_path / "skripte" / "wache.py").write_text("PROMPT = '{ABSCHLUSS}'\n", encoding="utf-8")
    (tmp_path / "to_spawn" / "waechter_takt.py").write_text("PROMPT = '{ABSCHLUSS}'\n", encoding="utf-8")
    sauber = quelltext_fundorte(tmp_path, (MERKMALE[0], MERKMALE[4]))
    assert sauber == {
        MERKMALE[0]: [("to_spawn/anleitung.py", 1)],
        MERKMALE[4]: [("to_spawn/anleitung.py", 1)],
    }
