"""Vorlagen-Platzhalter absichern, Annahme-Kommentar mit echter Kennung (#501).

Reste aus Review 495 (#490): ein unbekannter oder ungefüllter ``{GROSS}``-Platzhalter landete
roh im Start-Text; eine Vorlage ohne ``{ANNAHME_GRENZE}`` bekam die Annahme-Regel nicht; der
Annahme-Kommentar des Aufsehers nannte ``<id>`` statt der Kennung der offenen Live-Frage.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import test_capo_checkpoint_285 as cp
from test_waechter_213 import _capo, _text, welt  # noqa: F401  (welt = Fixture)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import briefkasten, capo

PLATZHALTER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")


def _bau() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "bau_platzhalter_501", SKILL / "skripte" / "bau.py"
    )
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _vorlagen() -> list[tuple[str, str]]:
    """Jede mitgelieferte Vorlage mit ``prompt_template`` im Skill."""
    funde = []
    for datei in sorted((SKILL / "repo-scripts").glob("*.json")):
        daten = json.loads(datei.read_text(encoding="utf-8"))
        if isinstance(daten, dict) and "prompt_template" in daten:
            funde.append((datei.name, daten["prompt_template"]))
    assert funde, "keine mitgelieferte Vorlage gefunden"
    return funde


def test_unbekannter_platzhalter_wirft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    with pytest.raises(ValueError, match=r"\{GIBT_ES_NICHT\}"):
        _bau().build_prompt(
            "Ticket {N}: {GIBT_ES_NICHT}", "7", "9", "T", "- k", konfig={}
        )


def test_ungefuellter_platzhalter_aus_einem_wert_wirft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bau = _bau()
    monkeypatch.setattr(bau.anleitung, "MINDSET", "Mindset {NOCH_LEER}")
    with pytest.raises(ValueError, match=r"\{NOCH_LEER\}"):
        bau.build_prompt("{MINDSET} {N}", "7", "9", "T", "- k", konfig={})


def test_klammern_in_titel_und_kontext_kein_fehlalarm() -> None:
    text = _bau().build_prompt(
        "{N} {TITLE}\n{KONTEXT}", "7", "9", "Fix {FOO}", '- {"A": 1} {BAR}', konfig={}
    )
    assert "Fix {FOO}" in text and "{BAR}" in text


@pytest.mark.parametrize(("name", "vorlage"), _vorlagen())
def test_jede_mitgelieferte_vorlage_fuellt_sich_ohne_rest(
    name: str, vorlage: str
) -> None:
    text = _bau().build_prompt(vorlage, "7", "9", "T", "- k", konfig={})
    assert PLATZHALTER.findall(text) == [], name


def test_vorlage_ohne_annahme_platzhalter_wird_gemeldet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        text = _bau().build_prompt("Ticket {N} bauen.", "7", "9", "T", "- k", konfig={})
    assert text == "Ticket 7 bauen."
    assert "ohne {ANNAHME_GRENZE}" in caplog.text, caplog.text


def test_annahme_kommentar_nennt_echte_kennung(
    welt: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    frage_id = briefkasten.einwerfen(
        902, "Live schalten?", "ja", 2, [], hauptbaum=welt["repo"]
    )
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [
        t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")
    ]
    assert len(annahme) == 1, cp._texte(welt, "902")
    assert f"--id {frage_id} " in annahme[0], annahme[0]
    assert "<id>" not in annahme[0], annahme[0]


def test_annahme_grenze_greift_ohne_tmux(
    welt: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows-PC ohne tmux: der Annahme-Kommentar trägt dieselbe Regel (Blick-Takt 1500/1200 s)."""
    monkeypatch.setenv("TO_SPAWN_TMUX", str(welt["repo"] / "kein-tmux-hier"))
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [
        t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")
    ]
    assert len(annahme) == 1, cp._texte(welt, "902")
    befehl = f"python {(SKILL / 'to_spawn.py').as_posix()}"
    assert capo.annahme_grenze(902, befehl) in annahme[0], annahme[0]


def test_start_mit_platzhalter_rest_endet_mit_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Vorlage mit unbekanntem {FOO_BAR}: Exit 2 und Grund auf stderr, kein Traceback."""
    bau = _bau()
    monkeypatch.setattr(
        bau, "load_default", lambda: {"prompt_template": "Ticket {N}: {FOO_BAR}"}
    )
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "1"}, {}))
    monkeypatch.setattr(
        sys, "argv", ["bau.py", "900", "--dry-run", "--print-prompt"]
    )
    with pytest.raises(SystemExit) as ende:
        bau.main()
    assert ende.value.code == 2
    assert "{FOO_BAR}" in capsys.readouterr().err


def test_zwei_offene_klasse_2_fragen_keine_kennung_im_kommentar(
    welt: dict[str, Path],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    ids = [
        briefkasten.einwerfen(902, f"Frage {i}?", "ja", 2, [], hauptbaum=welt["repo"])
        for i in (1, 2)
    ]
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [
        t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")
    ]
    assert len(annahme) == 1, cp._texte(welt, "902")
    assert all(i not in annahme[0] for i in ids), annahme[0]
    assert "<id>" in annahme[0], annahme[0]
