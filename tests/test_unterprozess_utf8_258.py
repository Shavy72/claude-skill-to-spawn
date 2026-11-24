"""Wächter (#258): ``text=True``-Unterprozess-Aufrufe lesen UTF-8, nie die Windows-Locale."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from to_spawn import eigener_handoff, konsole

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


KONSTANTEN = {"UNTERPROZESS_TEXT", "UNTERPROZESS_ANZEIGE", "UNTERPROZESS_DATEN"}


def _konsole_konstante(knoten: ast.expr) -> bool:
    return (
        isinstance(knoten, ast.Attribute)
        and isinstance(knoten.value, ast.Name)
        and knoten.value.id == "konsole"
        and knoten.attr in KONSTANTEN
    )


def _gut(wert: ast.expr) -> bool:
    """``**konsole.<Konstante>`` oder ``**{**konsole.<Konstante>, ...}``."""
    if _konsole_konstante(wert):
        return True
    return isinstance(wert, ast.Dict) and any(
        k is None and _konsole_konstante(v) for k, v in zip(wert.keys, wert.values)
    )


def _ist_utf8(wert: ast.expr) -> bool:
    return isinstance(wert, ast.Constant) and str(wert.value).lower().replace("-", "") == "utf8"


def _ohne_utf8(datei: Path) -> list[int]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    zeilen = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        text = any(
            k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in knoten.keywords
        )
        if not text:
            continue
        encoding_ok = any(k.arg == "encoding" and _ist_utf8(k.value) for k in knoten.keywords)
        spread_ok = any(k.arg is None and _gut(k.value) for k in knoten.keywords)
        if not (encoding_ok or spread_ok):
            zeilen.append(knoten.lineno)
    return zeilen


def test_waechter_erkennt_schlechte_formen(tmp_path: Path) -> None:
    quelle = tmp_path / "x.py"
    quelle.write_text(
        "import subprocess\n"
        "subprocess.run(['a'], text=True)\n"
        "subprocess.run(['a'], universal_newlines=True)\n"
        "subprocess.run(['a'], text=True, encoding='cp1252')\n"
        "subprocess.run(['a'], text=True, **kw)\n"
        "subprocess.run(['a'], text=True, **konsole.ANDERES)\n"
        "subprocess.run(['a'], text=True, encoding='utf-8')\n"
        "subprocess.run(['a'], **konsole.UNTERPROZESS_DATEN, text=True)\n"
        "subprocess.run(['a'], text=True, **{**konsole.UNTERPROZESS_DATEN, 'errors': 'x'})\n",
        encoding="utf-8",
    )
    assert _ohne_utf8(quelle) == [2, 3, 4, 5, 6]


def test_helfer_liest_echte_umlaute_aus_echtem_unterprozess() -> None:
    code = r"import sys; sys.stdout.buffer.write('\u00e4\u00f6\u00fc \u00df \u20ac'.encode('utf-8'))"
    for lesart in (konsole.UNTERPROZESS_ANZEIGE, konsole.UNTERPROZESS_DATEN):
        lauf = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, **lesart)
        assert lauf.stdout.strip() == "äöü ß €"


def test_git_dateiname_mit_umlaut_ueber_eigener_handoff(tmp_path: Path) -> None:
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True)
    (tmp_path / "größe.txt").write_text("x", encoding="utf-8")
    subprocess.run([*git, "add", "."], check=True)
    assert eigener_handoff._git(tmp_path, "-c", "core.quotepath=false", "ls-files").strip() == "größe.txt"


def test_text_true_ohne_utf8_nur_in_allowlist() -> None:
    schlecht = {}
    for datei in sorted((WURZEL / "to_spawn").rglob("*.py")):
        rel = datei.relative_to(WURZEL).as_posix()
        if rel in NOCH_NICHT_MIGRIERT:
            continue
        if zeilen := _ohne_utf8(datei):
            schlecht[rel] = zeilen
    assert not schlecht, f"text=True ohne **konsole.UNTERPROZESS_TEXT / encoding: {schlecht}"
