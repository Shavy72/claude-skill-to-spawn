"""Tests für wochenbericht.py: echter Hook-Aufruf per subprocess (Muster test_hooks.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))  # direkter Import von wochenbericht (kernzahl) für die Unit-Tests


def _lauf(env: dict[str, str]) -> tuple[int, dict[str, Any] | None]:
    ergebnis = subprocess.run(
        [sys.executable, str(HOOKS / "wochenbericht.py")],
        input=b"{}",
        capture_output=True,
        env=env,
        timeout=60,
    )
    text = ergebnis.stdout.decode("utf-8").strip()
    return ergebnis.returncode, (json.loads(text) if text else None)


def _umgebung(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "REVIEW_WOCHENBERICHT_ORDNER": str(tmp_path / ".review"),
        "REVIEW_LOG": str(tmp_path / "ausloeser.jsonl"),
        "REVIEW_HOOK_LOG": str(tmp_path / "hook-fehler.log"),
    }
    # REVIEW_LOG zeigt auf nichtexistente Datei -> leerer Auslöser-Bestand,
    # der echte review_bericht.py läuft trotzdem durch (Datei fehlt -> []).


def test_erster_lauf_schreibt_und_meldet(tmp_path: Path) -> None:
    env = _umgebung(tmp_path)
    code, aus = _lauf(env)
    assert code == 0
    assert aus is not None
    ausgabe = aus["hookSpecificOutput"]
    assert ausgabe["hookEventName"] == "SessionStart"
    kontext = ausgabe["additionalContext"]
    assert kontext.startswith("Review-Wochenbericht: ")
    assert "(" in kontext and kontext.endswith(")")

    heute = date.today().isoformat()
    bericht = tmp_path / ".review" / f"wochenbericht-{heute}.md"
    assert bericht.is_file()
    assert "Review-Bericht" in bericht.read_text(encoding="utf-8")

    stand = json.loads((tmp_path / ".review" / "wochenbericht-stand.json").read_text(encoding="utf-8"))
    assert stand["letzter_lauf"] == heute


def test_zweiter_lauf_selber_tag_bleibt_still(tmp_path: Path) -> None:
    env = _umgebung(tmp_path)
    code1, aus1 = _lauf(env)
    assert (code1, aus1 is not None) == (0, True)

    code2, aus2 = _lauf(env)
    assert (code2, aus2) == (0, None)

    # Kein zweiter Bericht-Aufruf: nur die eine Berichtsdatei des Tages liegt vor.
    berichte = list((tmp_path / ".review").glob("wochenbericht-*.md"))
    assert len(berichte) == 1


def test_nach_acht_tagen_wieder_faellig(tmp_path: Path) -> None:
    env = _umgebung(tmp_path)
    code1, aus1 = _lauf(env)
    assert (code1, aus1 is not None) == (0, True)

    ordner = tmp_path / ".review"
    alt = (date.today() - timedelta(days=8)).isoformat()
    (ordner / "wochenbericht-stand.json").write_text(json.dumps({"letzter_lauf": alt}), encoding="utf-8")

    code2, aus2 = _lauf(env)
    assert code2 == 0
    assert aus2 is not None
    assert aus2["hookSpecificOutput"]["additionalContext"].startswith("Review-Wochenbericht: ")

    heute = date.today().isoformat()
    stand = json.loads((ordner / "wochenbericht-stand.json").read_text(encoding="utf-8"))
    assert stand["letzter_lauf"] == heute


def test_kernzahl_ohne_pruefungen() -> None:
    from wochenbericht import kernzahl

    text = (
        "# Review-Bericht (letzte 7 Tage)\n\n"
        "| Moment | Soll | Ist | Quote | gruen | gelb | rot | gestrichen/roh | Fehlalarm | Token | Hinweis |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| – | 0 | 0 | – | 0 | 0 | 0 | 0/0 | – | 0 | |"
    )
    assert kernzahl(text) == "keine Prüfungen"


def test_kernzahl_summiert_mehrere_zeilen() -> None:
    from wochenbericht import kernzahl

    text = (
        "| Moment | Soll | Ist | Quote | gruen | gelb | rot | gestrichen/roh | Fehlalarm | Token | Hinweis |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
        "| commit | 2 | 2 | 100% | 2 | 0 | 0 | 3/10 | 30% | 500 | |\n"
        "| ticket | 1 | 1 | 100% | 1 | 0 | 0 | 1/5 | 20% | 200 | |"
    )
    assert kernzahl(text) == "Fehlalarm-Quote 27%"
