"""Wächter (#258): ``text=True``-Unterprozess-Aufrufe lesen UTF-8, nie die Windows-Locale."""

from __future__ import annotations

import ast
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent

# Aufgeräumt ist, was Issue #258 nennt. ponytail: übrige Dateien stehen hier, bis sie migriert sind;
# Upgrade-Pfad: Datei auf ``**konsole.UNTERPROZESS_TEXT`` umstellen und hier streichen.
NOCH_NICHT_MIGRIERT = {
    "to_spawn/belege.py", "to_spawn/config.py", "to_spawn/einreichen.py", "to_spawn/gh.py",
    "to_spawn/hausmeister.py", "to_spawn/hausmeister_aufgabe.py", "to_spawn/hausmeister_windows.py",
    "to_spawn/inventur.py", "to_spawn/komplett_lauf.py", "to_spawn/melder.py", "to_spawn/nest.py",
    "to_spawn/pc_fenster.py", "to_spawn/probesitz.py", "to_spawn/respawn.py", "to_spawn/setup.py",
    "to_spawn/skill_stand.py", "to_spawn/staging_schalter.py", "to_spawn/startklar.py",
    "to_spawn/umzug.py", "to_spawn/prozessbaum.py",
}


def _ohne_utf8(datei: Path) -> list[int]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    zeilen = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        namen = {k.arg for k in knoten.keywords}
        text = any(k.arg == "text" and isinstance(k.value, ast.Constant) and k.value.value is True
                   for k in knoten.keywords)
        if text and "encoding" not in namen and None not in namen:
            zeilen.append(knoten.lineno)
    return zeilen


def test_text_true_ohne_utf8_nur_in_allowlist() -> None:
    schlecht = {}
    for datei in sorted((WURZEL / "to_spawn").rglob("*.py")):
        rel = datei.relative_to(WURZEL).as_posix()
        if rel in NOCH_NICHT_MIGRIERT:
            continue
        if zeilen := _ohne_utf8(datei):
            schlecht[rel] = zeilen
    assert not schlecht, f"text=True ohne **konsole.UNTERPROZESS_TEXT / encoding: {schlecht}"
