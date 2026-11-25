"""Vorlagen-Platzhalter absichern, Annahme-Kommentar mit echter Kennung (#501).

Reste aus Review 495 (#490): ein unbekannter oder ungefüllter ``{GROSS}``-Platzhalter landete
roh im Start-Text; eine Vorlage ohne ``{ANNAHME_GRENZE}`` bekam die Annahme-Regel nicht; der
Annahme-Kommentar des Aufsehers nannte ``<id>`` statt der Kennung der offenen Live-Frage.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import test_capo_checkpoint_285 as cp
from test_waechter_213 import _capo, _text, welt  # noqa: F401  (welt = Fixture)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import bash_wahl, briefkasten, capo, ja_einrichten

PLATZHALTER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
#: Der Ja-Befehl, wie er im Annahme-Kommentar steht: ``! ~/.claude/bin/ja 902`` (#531).
JA_IM_KOMMENTAR = re.compile(r"`! ~/\.claude/bin/ja ([^`]+)`")


def _ja_ausfuehren(
    welt: dict[str, Path],
    tmp_path: Path,
    annahme: str,  # noqa: F811
) -> subprocess.CompletedProcess[str]:
    """Den Befehl aus dem Kommentar herauslesen und genau so laufen lassen (ohne Terminal, mit Sperre)."""
    treffer = JA_IM_KOMMENTAR.findall(annahme)
    assert len(treffer) == 1, annahme
    heim = tmp_path / "heim"
    heim.mkdir()
    assert ja_einrichten.sperre_eintragen(heim / ".claude", sys.executable) is True
    sh, _cmd = ja_einrichten.befehl_anlegen(heim / ".claude", sys.executable)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update({"TO_SPAWN_REPO": str(welt["repo"]), "HOME": str(heim)})
    # Unter Windows startet ``!`` den Befehl in der Git-Bash; die Shebang-Datei direkt geht dort nicht (WinError 193).
    start = [bash_wahl.bash(), str(sh)] if os.name == "nt" else [str(sh)]
    return subprocess.run(
        [*start, *treffer[0].split()],
        cwd=welt["repo"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def _bau() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bau_platzhalter_501", SKILL / "skripte" / "bau.py")
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
        _bau().build_prompt("Ticket {N}: {GIBT_ES_NICHT}", "7", "9", "T", "- k", konfig={})


def test_ungefuellter_platzhalter_aus_einem_wert_wirft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bau = _bau()
    monkeypatch.setattr(bau.anleitung, "MINDSET", "Mindset {NOCH_LEER}")
    with pytest.raises(ValueError, match=r"\{NOCH_LEER\}"):
        bau.build_prompt("{MINDSET} {N}", "7", "9", "T", "- k", konfig={})


def test_klammern_in_titel_und_kontext_kein_fehlalarm() -> None:
    text = _bau().build_prompt("{N} {TITLE}\n{KONTEXT}", "7", "9", "Fix {FOO}", '- {"A": 1} {BAR}', konfig={})
    assert "Fix {FOO}" in text and "{BAR}" in text


@pytest.mark.parametrize(("name", "vorlage"), _vorlagen())
def test_jede_mitgelieferte_vorlage_fuellt_sich_ohne_rest(name: str, vorlage: str) -> None:
    text = _bau().build_prompt(vorlage, "7", "9", "T", "- k", konfig={})
    assert PLATZHALTER.findall(text) == [], name


def test_vorlage_ohne_annahme_platzhalter_wird_gemeldet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        text = _bau().build_prompt("Ticket {N} bauen.", "7", "9", "T", "- k", konfig={})
    assert text == "Ticket 7 bauen."
    assert "ohne {ANNAHME_GRENZE}" in caplog.text, caplog.text


def test_annahme_kommentar_nennt_fertigen_ja_befehl_mit_echter_nummer(
    welt: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Befehl im Kommentar trägt die echte Ticketnummer, keinen Platzhalter, und beantwortet die Frage.

    Angepasst durch #531, Sinn aus #501 bleibt: kopierbarer, wirklich funktionierender Befehl.
    """
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    frage_id = briefkasten.einwerfen(902, "Live schalten?", "ja", 2, [], hauptbaum=welt["repo"])
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")]
    assert len(annahme) == 1, cp._texte(welt, "902")
    assert "! ~/.claude/bin/ja 902`" in annahme[0], annahme[0]
    assert not re.search(r"<[^>\n]*>", annahme[0]), annahme[0]
    lauf = _ja_ausfuehren(welt, tmp_path, annahme[0])
    assert lauf.returncode == 0, lauf.stderr
    stand = briefkasten.lesen("902", frage_id, hauptbaum=welt["repo"])
    assert stand.von == briefkasten.NUTZER_VON and stand.wahl == "ja"


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
    annahme = [t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")]
    assert len(annahme) == 1, cp._texte(welt, "902")
    befehl = f"python {(SKILL / 'to_spawn.py').as_posix()}"
    assert capo.annahme_grenze(902, befehl) in annahme[0], annahme[0]


def test_start_mit_platzhalter_rest_endet_mit_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Vorlage mit unbekanntem {FOO_BAR}: Exit 2 und Grund auf stderr, kein Traceback."""
    bau = _bau()
    monkeypatch.setattr(bau, "load_default", lambda: {"prompt_template": "Ticket {N}: {FOO_BAR}"})
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "1"}, {}))
    monkeypatch.setattr(sys, "argv", ["bau.py", "900", "--dry-run", "--print-prompt"])
    with pytest.raises(SystemExit) as ende:
        bau.main()
    assert ende.value.code == 2
    assert "{FOO_BAR}" in capsys.readouterr().err


def test_zwei_offene_klasse_2_fragen_befehl_lehnt_sicher_ab(
    welt: dict[str, Path],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zwei offene Fragen: der Befehl im Kommentar beantwortet keine still, sondern lehnt klar ab.

    Angepasst durch #531, Sinn aus #501 bleibt: nie still die falsche Frage beantworten.
    """
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    ids = [briefkasten.einwerfen(902, f"Frage {i}?", "ja", 2, [], hauptbaum=welt["repo"]) for i in (1, 2)]
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", f"{cp.FRAGE}\n\n{cp.VORSCHLAG}", 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    annahme = [t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")]
    assert len(annahme) == 1, cp._texte(welt, "902")
    lauf = _ja_ausfuehren(welt, tmp_path, annahme[0])
    assert lauf.returncode != 0 and "2 offene Fragen" in lauf.stderr, lauf.stderr
    for i in ids:
        assert briefkasten.lesen("902", i, hauptbaum=welt["repo"]) == briefkasten.NUTZER
