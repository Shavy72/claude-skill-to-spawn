"""Tests für #284 — capo startet nach dem Wiederöffnen die Folge-Runde.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py``, die echte
Zustandsdatei, eine echte Worktree-Spur. Gestellt sind nur die externen
Programme: ``gh`` (Ersatz aus #213), der Mail-Befehl (schreibt in eine Datei)
und ``tmux`` (``hilfen/tmux_stub_284.py`` schreibt jeden Aufruf mit) — so prüft
der Test, dass der RICHTIGE Startbefehl mit den richtigen Argumenten rausging.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _konfig,
    _mails,
    _text,
    welt,
)

TMUX_STUB = Path(__file__).resolve().parent / "hilfen" / "tmux_stub_284.py"


# --- Hilfen ------------------------------------------------------------------


@pytest.fixture()
def tmux(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """tmux-Ersatz: Protokoll aller Aufrufe + steuerbare Fensterliste."""
    protokoll = welt["tmp"] / "tmux_aufrufe.jsonl"
    fenster = welt["tmp"] / "tmux_fenster.txt"
    monkeypatch.setenv(
        "TO_SPAWN_TMUX",
        json.dumps([sys.executable, str(TMUX_STUB), str(protokoll)]),
    )
    monkeypatch.setenv("FAKE_TMUX_FENSTER", str(fenster))
    fenster.write_text(f"wache {SPEC}\n", encoding="utf-8")
    return {"protokoll": protokoll, "fenster": fenster}


def _aufrufe(tmux: dict[str, Path]) -> list[list[str]]:
    if not tmux["protokoll"].is_file():
        return []
    return [
        json.loads(z)
        for z in tmux["protokoll"].read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def _starts(tmux: dict[str, Path]) -> list[list[str]]:
    return [a for a in _aufrufe(tmux) if a[:1] in (["new-window"], ["new-session"])]


def _zustand_datei(welt: dict[str, Path]) -> Path:
    return welt["zustand"] / f"test_wegwerf_{SPEC}.json"


def _folgerunden(welt: dict[str, Path]) -> dict:
    datei = _zustand_datei(welt)
    if not datei.is_file():
        return {}
    return json.loads(datei.read_text(encoding="utf-8")).get("folgerunden") or {}


def _wieder_oeffnen_lassen(welt: dict[str, Path], **zusatz: object) -> None:
    """Erster Tick mit offenem #901 (Ausgangsstand), dann zu ohne Beleg/Nummer."""
    _konfig(welt, waechter={"karenz_minuten": 0, **zusatz})
    _commit(welt["repo"], "feat: Bauteil ohne Nummer", {"web/app.py": "x\n"})
    _gh_setzen(welt, "901", state="open", closed_at=None)
    erste = _capo(welt)
    assert erste.returncode == 0, _text(erste)
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())


# --- a) wieder geöffnet + keine Session → Folge-Runde ------------------------


def test_folge_runde_startet_mit_richtigem_befehl(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    _wieder_oeffnen_lassen(welt)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#901 Folge-Runde gestartet" in ergebnis.stdout, _text(ergebnis)
    starts = _starts(tmux)
    assert len(starts) == 1, _aufrufe(tmux)
    argv = starts[0]
    assert argv[0] == "new-window"
    assert "-t" in argv and argv[argv.index("-t") + 1] == f"=spec-{SPEC}"
    assert "-n" in argv and argv[argv.index("-n") + 1] == "bau 901"
    assert "-c" in argv and argv[argv.index("-c") + 1] == str(welt["repo"])
    assert argv[-3:-1] == ["bash", "-lc"]
    assert argv[-1].endswith("bau 901 --sofort")
    assert "TO_SPAWN_HOME=" in argv[-1] and "REPO=" in argv[-1]
    assert _folgerunden(welt) == {"901": 1}


# --- b) Session läuft noch → kein Start --------------------------------------


def test_laufende_session_startet_nichts(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    _wieder_oeffnen_lassen(welt)
    tmux["fenster"].write_text(f"wache {SPEC}\nbau 901\n", encoding="utf-8")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#901 läuft bereits" in ergebnis.stdout, _text(ergebnis)
    assert _starts(tmux) == []
    assert _folgerunden(welt) == {}


# --- c) Grenze erreicht → kein Start, Meldung --------------------------------


def test_grenze_erreicht_meldet_statt_zu_starten(
    welt: dict[str, Path], tmux: dict[str, Path]
) -> None:
    _wieder_oeffnen_lassen(welt, folgerunden_max=2)
    datei = _zustand_datei(welt)
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["folgerunden"] = {"901": 2}
    datei.write_text(json.dumps(daten), encoding="utf-8")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#901 Folge-Runden-Grenze erreicht" in ergebnis.stdout, _text(ergebnis)
    assert _starts(tmux) == []
    assert _folgerunden(welt) == {"901": 2}
    arten = [m.get("art") for m in _mails(welt)]
    assert "session_tot" in arten, _mails(welt)


# --- d) Probelauf startet nichts ---------------------------------------------


def test_probelauf_startet_nichts(welt: dict[str, Path], tmux: dict[str, Path]) -> None:
    _wieder_oeffnen_lassen(welt)
    ergebnis = _capo(welt, "--dry-run")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "würde Folge-Runde starten" in ergebnis.stdout, _text(ergebnis)
    assert _starts(tmux) == []
    assert _folgerunden(welt) == {}
