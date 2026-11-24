"""#414: Frage und Antwort im Briefkasten wecken den Empfänger — nicht erst der Takt.

Echt sind Briefkasten (Dateien im Hauptbaum), tmux-Tür (``tmux_aufruf``) und ``wache.main``.
Gestellt ist nur ``tmux`` über die vorhandene Naht ``TO_SPAWN_TMUX`` (Attrappe aus
``conftest.attrappe``): sie liefert Fensterliste und Bildschirm aus Dateien und schreibt jeden
eingefügten Text mit Ziel nach ``getippt.jsonl``.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from conftest import attrappe
from to_spawn import briefkasten, respawn

SPEC = 4140
TICKET = 4141
PANE = "%77"
STILL = "● Frage eingeworfen, warte.\n\n❯ \n"
RUECKFRAGE = "Do you want to proceed?\n❯ 1. Yes\n"

FAKE_TMUX = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
ordner = Path(os.environ["FAKE_TMUX_ORDNER"])
ordner.mkdir(parents=True, exist_ok=True)
with (ordner / "aufrufe.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
befehl = args[:1]
if befehl == ["list-windows"]:
    datei = ordner / "fenster.txt"
    print(datei.read_text(encoding="utf-8") if datei.is_file() else "", end="")
elif befehl == ["capture-pane"]:
    datei = ordner / "bild.txt"
    print(datei.read_text(encoding="utf-8") if datei.is_file() else "", end="")
elif befehl == ["load-buffer"]:
    (ordner / "puffer.txt").write_text(sys.stdin.read(), encoding="utf-8")
elif befehl == ["paste-buffer"]:
    text = (ordner / "puffer.txt").read_text(encoding="utf-8")
    ziel = args[args.index("-t") + 1]
    with (ordner / "getippt.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ziel": ziel, "text": text}, ensure_ascii=False) + "\\n")
sys.exit(0)
"""


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Hauptbaum mit Manifest der Spec, gestelltes tmux mit Fenster ``bau <TICKET>`` (still)."""
    haupt = tmp_path / "haupt"
    datei = haupt / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    datei.parent.mkdir(parents=True)
    datei.write_text(json.dumps({"spec": SPEC, "tickets": {str(TICKET): {"titel": "Probe"}}}), encoding="utf-8")
    ordner = tmp_path / "tmux"
    ordner.mkdir()
    (ordner / "fenster.txt").write_text(f"1\twache {SPEC}\t0\n2\tbau {TICKET}\t0\n", encoding="utf-8")
    (ordner / "bild.txt").write_text(STILL, encoding="utf-8")
    tmux = attrappe(tmp_path / "bin", "tmux", FAKE_TMUX)
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([sys.executable, str(tmux)]))
    monkeypatch.setenv("FAKE_TMUX_ORDNER", str(ordner))
    monkeypatch.setattr(respawn, "TIPP_PAUSE_S", 0.0)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    return {"haupt": haupt, "tmux": ordner}


def _getippt(welt: dict[str, Path]) -> list[dict[str, str]]:
    datei = welt["tmux"] / "getippt.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _frage(welt: dict[str, Path]) -> str:
    return briefkasten.einwerfen(TICKET, "Zweig A oder B?", "A", None, ["Lastenheft"], hauptbaum=welt["haupt"])


def _wecker_modul() -> Any:
    # Im Test geladen: vor dem Fix fehlt das Modul — jeder Test hier ist rot, nicht übersprungen.
    return importlib.import_module("to_spawn.briefkasten_wecker")


def test_wache_frage_weckt_aufseher_ohne_takt(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test über ``wache.main``: eine Frage, die während des laufenden Aufsehers eingeht,
    landet binnen Sekunden als Weckruf im eigenen Pane des Aufsehers — kein Takt fällig."""
    wecker = _wecker_modul()
    monkeypatch.setattr(wecker, "TAKT_S", 0.2)
    quelle = Path(__file__).resolve().parent.parent / "skripte" / "wache.py"
    modul_spec = importlib.util.spec_from_file_location("wache_wecker_414", quelle)
    assert modul_spec is not None and modul_spec.loader is not None
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    @contextmanager
    def ohne_blick(*_a: Any, **_k: Any):  # noqa: ANN202 — Handoff-Blick ist nicht Thema hier
        yield None

    gesehen: list[list[dict[str, str]]] = []

    def fahre(**_kw: Any) -> int:
        frage_id = _frage(welt)  # Frage kommt erst, als der Aufseher schon läuft
        ende = time.monotonic() + 10
        while time.monotonic() < ende and not _getippt(welt):
            time.sleep(0.1)
        gesehen.append(_getippt(welt))
        gesehen.append([{"id": frage_id}])
        return 0

    monkeypatch.setattr(wache.handoff_blick, "laeuft", ohne_blick)
    monkeypatch.setattr(wache.waechter_lauf, "fahre", fahre)
    monkeypatch.setattr(wache, "REPO_ORDNER", welt["haupt"])
    monkeypatch.setattr(wache, "repo_slug_oder_abbruch", lambda: "o/r")
    monkeypatch.setattr(wache, "start_ordner", lambda: tmp_path)
    monkeypatch.setattr(wache, "auf_speicher_warten", lambda *a, **k: 0)
    monkeypatch.setattr(wache.config, "sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.config, "lade", lambda *a, **k: {})
    monkeypatch.setattr(wache.startklar, "gate", lambda *a, **k: 0)
    monkeypatch.setattr(wache.context_mode, "pruefen", lambda **k: tmp_path)
    monkeypatch.setattr(wache.vertrauen, "still_sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.nest, "git_hooks_einrichten", lambda *a, **k: None)
    monkeypatch.setattr(wache.tempfile, "mkdtemp", lambda **k: str(tmp_path))
    for name in ("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "BAU_UMZUG_DATEI", "TO_SPAWN_WACHE_SPEC", wache.ABLOESE_ENV):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("TMUX_PANE", PANE)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "argv", ["wache.py", str(SPEC)])

    assert wache.main() == 0
    getippt, (frage,) = gesehen
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage["id"] in getippt[0]["text"] and f"#{TICKET}" in getippt[0]["text"]


def test_antwort_weckt_bau_fenster_einmal(welt: dict[str, Path]) -> None:
    """Antwort des Aufsehers → Weckruf ins Fenster ``bau <N>``, genau einmal."""
    wecker = _wecker_modul()
    frage_id = _frage(welt)
    w = wecker.Wecker(SPEC, welt["haupt"], PANE)  # Frage war schon offen: Ausgangsstand
    w.einmal()
    assert _getippt(welt) == [], "schon offene Frage weckt beim Start nicht"
    briefkasten.antworten(TICKET, frage_id, "A", ["Beleg"], "passt", hauptbaum=welt["haupt"])
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [f"=spec-{SPEC}:2"], getippt
    assert f"antwort {TICKET} --id {frage_id}" in getippt[0]["text"]


def test_frage_wartet_bei_rueckfrage_und_weckt_dann_einmal(welt: dict[str, Path]) -> None:
    """Rückfrage-Fenster im Aufseher-Pane → nichts tippen; danach still → ein Weckruf, kein zweiter."""
    wecker = _wecker_modul()
    w = wecker.Wecker(SPEC, welt["haupt"], PANE)
    frage_id = _frage(welt)
    (welt["tmux"] / "bild.txt").write_text(RUECKFRAGE, encoding="utf-8")
    w.einmal()
    assert _getippt(welt) == []
    (welt["tmux"] / "bild.txt").write_text(STILL, encoding="utf-8")
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage_id in getippt[0]["text"]


def test_ohne_tmux_pane_kein_wecker_und_kein_tmux(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """PC ohne tmux (kein ``TMUX_PANE``): kein Faden, kein tmux-Aufruf — der Takt bleibt."""
    wecker = _wecker_modul()
    monkeypatch.delenv("TMUX_PANE", raising=False)
    with wecker.laeuft(SPEC, welt["haupt"]) as w:
        _frage(welt)
        assert w is None
    assert not (welt["tmux"] / "aufrufe.jsonl").exists()
    assert os.environ.get("TMUX_PANE") is None
