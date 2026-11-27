"""#601: Jeder ``gh``-Aufruf hat eine Zeitgrenze — hängt GitHub, hängt der Aufpasser nicht mit.

``gh.lauf`` ist die eine Stelle, die den ``gh``-Prozess startet. Ohne ausdrückliche Grenze gilt
der Standard (:func:`gh.standard_zeit`); Ablauf = Fehlerweg (Exit 124, leeres stdout), also
``blocked_by`` → ``None`` → kein Anstoß, kein Vorfall (Weg seit #594/#596 getestet).

Gestellt ist nur ``gh`` (externer Dienst) als echtes Skript über die Test-Tür
``TO_SPAWN_GH_STUB``, das schläft — und ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414``).
Ein eigener Faden-Wächter belegt, dass der Aufruf innerhalb der Grenze endet (ohne Fix: rot).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import LINIE, SPEC, TICKET, PANE, _bild, _getippt

from to_spawn import aufpasser, bau_log, briefkasten_wecker, gh

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)
GRENZE_S = 1.0
WAECHTER_S = 8.0
SCHLAF_S = 20
#: Umgebung der Standard-Grenze (wie ``gh.GH_ZEIT_ENV``; als Text, damit der Rot-Beweis ohne Fix läuft)
GH_ZEIT_ENV = "TO_SPAWN_GH_TIMEOUT"


def _stub(ordner: Path, name: str, rumpf: str) -> Path:
    pfad = ordner / f"{name}.py"
    pfad.write_text(f"import sys, time  # noqa: E401\n{rumpf}\n", encoding="utf-8")
    return pfad


@pytest.fixture
def haengt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``gh``-Ersatz, der nie antwortet; Standard-Grenze klein gestellt."""
    stub = _stub(tmp_path, "gh_haengt", f"time.sleep({SCHLAF_S})")
    monkeypatch.setenv(gh.GH_STUB_ENV, str(stub))
    monkeypatch.setenv(GH_ZEIT_ENV, str(GRENZE_S))
    return stub


def _mit_waechter(aufruf: Callable[[], Any]) -> tuple[Any, float]:
    """Aufruf im Faden; endet er nicht binnen :data:`WAECHTER_S`, schlägt der Test fehl."""
    ergebnis: dict[str, Any] = {}

    def _lauf() -> None:
        try:
            ergebnis["wert"] = aufruf()
        except BaseException as fehler:  # noqa: BLE001 — Absturz soll im Test sichtbar werden
            ergebnis["fehler"] = fehler

    start = time.monotonic()
    faden = threading.Thread(target=_lauf, daemon=True)
    faden.start()
    faden.join(WAECHTER_S)
    dauer = time.monotonic() - start
    assert not faden.is_alive(), f"gh-Aufruf hängt nach {dauer:.1f} s noch (keine Zeitgrenze)"
    assert "fehler" not in ergebnis, ergebnis.get("fehler")
    return ergebnis.get("wert"), dauer


def test_haengendes_gh_endet_mit_fehlerweg(haengt: Path) -> None:
    (code, ausgabe), dauer = _mit_waechter(lambda: gh.lauf(["api", "repos/o/r/issues/1"]))
    assert (code, ausgabe) == (124, "")
    assert dauer < GRENZE_S + 4
    deps, _ = _mit_waechter(lambda: gh.blocked_by("o/r", "1"))
    assert deps is None


def _wartet(zeile: str) -> str:
    return f"● Bash(to_spawn.py blocker {TICKET})\n  ⎿  {zeile}\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode\n"


def _vorfaelle(haupt: Path) -> list[dict]:
    zeilen = bau_log.lese(haupt, TICKET)
    return [z for z in zeilen if z.get("typ") == "vorfall"]


def test_aufpasser_und_wecker_mit_haengendem_gh_kein_anstoss_kein_vorfall(
    welt: dict[str, Any], haengt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in bau_log.RUECKFALL_VARIABLEN:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(welt["bin"] / "waechter"))
    bild = _wartet(f"WARTET: #{TICKET + 10} offen")
    # Aufpasser-Regel (Takt)
    wert, _ = _mit_waechter(lambda: aufpasser.erledigt_gewartet(bild, TICKET, welt["haupt"], "o/r"))
    assert wert is None
    # Briefkasten-Wecker (60 s) — derselbe Weg
    _bild(welt, bild)
    w = briefkasten_wecker.Wecker(SPEC, welt["haupt"], PANE, gh_repo="o/r")
    _mit_waechter(w.einmal)
    assert _getippt(welt) == []
    assert _vorfaelle(welt["haupt"]) == []


def test_normaler_aufruf_unveraendert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    antwort = [{"number": 7, "state": "open", "labels": []}]
    stub = _stub(tmp_path, "gh_ok", f"print({json.dumps(json.dumps(antwort))}); sys.exit(0)")
    monkeypatch.setenv(gh.GH_STUB_ENV, str(stub))
    monkeypatch.setenv(GH_ZEIT_ENV, str(GRENZE_S))
    assert gh.blocked_by("o/r", "1") == antwort
    assert gh.lauf(["api", "x"]) == (0, json.dumps(antwort))


def test_standard_zeit_ohne_und_mit_kaputter_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gh.GH_ZEIT_ENV == GH_ZEIT_ENV
    monkeypatch.delenv(gh.GH_ZEIT_ENV, raising=False)
    assert gh.standard_zeit() == gh.GH_ZEIT_S > 0
    for kaputt in ("kaputt", "0", "-3"):
        monkeypatch.setenv(gh.GH_ZEIT_ENV, kaputt)
        assert gh.standard_zeit() == gh.GH_ZEIT_S
