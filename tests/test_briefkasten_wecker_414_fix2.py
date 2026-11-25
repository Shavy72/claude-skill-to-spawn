"""#414 Fix-Runde 2: grauer Platzhalter, Puffer-Reste, fehlende Fälle des Briefkasten-Weckers.

Live-Befund (echtes Claude-Fenster): in einer frischen Sitzung steht hinter ``❯`` ein grauer
Platzhalter. ``capture-pane -e -p`` zeigt ihn gedimmt (``ESC[2m … ESC[0m``), ``capture-pane -p``
als normalen Text — der Wecker hielt das Feld deshalb für belegt und weckte nie.

Gestellt ist nur ``tmux`` (Attrappe aus ``test_briefkasten_wecker_414`` plus eine Hülle, die
einzelne Unterbefehle mit Exit 1 scheitern lässt); Briefkasten, tmux-Tür und Wecker sind echt.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

import pytest
import test_briefkasten_wecker_414 as basis
from test_briefkasten_wecker_414 import (
    FAKE_TMUX,
    HALB,
    LINIE,
    PANE,
    SPEC,
    STILL,
    _bild,
    _frage,
    _getippt,
    _wecker_modul,
)

from conftest import attrappe
from to_spawn import aufpasser, respawn
from to_spawn.tmux_aufruf import TmuxFehler

welt = basis.welt  # Fixture aus dem Grund-Test (Hauptbaum + gestelltes tmux)

#: Echtes Bild aus ``capture-pane -e -p`` einer frischen Claude-Sitzung (Live-Versuch, #414).
PLATZHALTER = '❯ \x1b[2mTry "fix lint errors"\x1b[0m'
#: Die Rahmenlinien kommen mit ``-e`` ebenfalls gefärbt.
FARB_LINIE = f"\x1b[38;5;244m{LINIE}\x1b[39m"


def _feld(eingabe: str, linie: str = FARB_LINIE) -> str:
    return f"● Frage eingeworfen, warte.\n\n{linie}\n{eingabe}\n{linie}\n  ⏵⏵ auto mode · 1h 2m\n"


# --- Aufgabe 1: eingabe_leer kennt gedimmten Text ------------------------------------------


def test_gedimmter_platzhalter_gilt_als_leer() -> None:
    assert aufpasser.eingabe_leer(_feld(PLATZHALTER)) is True


def test_normaler_text_bleibt_belegt() -> None:
    assert aufpasser.eingabe_leer(_feld("❯ ich tippe gerade")) is False
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[1mich tippe\x1b[0m")) is False


def test_gemischt_gedimmt_und_normal_ist_belegt() -> None:
    assert aufpasser.eingabe_leer(_feld('❯ ich\x1b[2mTry "fix lint errors"\x1b[0m')) is False
    assert aufpasser.eingabe_leer(_feld('❯ \x1b[2mTry "fix\x1b[22m lint')) is False


def test_leeres_feld_gefaerbt_und_klar() -> None:
    assert aufpasser.eingabe_leer(_feld("❯ ")) is True
    assert aufpasser.eingabe_leer(_feld("\x1b[39m❯\x1b[0m \x1b[7m \x1b[27m")) is True
    assert aufpasser.eingabe_leer(_feld("❯ ", linie=LINIE)) is True


def test_truecolor_mit_2_im_farbwert_ist_nicht_gedimmt() -> None:
    """``38;2;r;g;b`` enthält eine 2 als Farbmodus, nicht als „gedimmt“ → Text bleibt belegt."""
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[38;2;153;153;153mich tippe\x1b[39m")) is False


def test_truecolor_mit_lauter_zweien_ist_nicht_gedimmt() -> None:
    """#435: ``38;2;2;2;2`` (alle Farbwerte 2) ist Echtfarbe, kein SGR 2 → Text bleibt belegt;
    erst eine weitere ``2`` danach schaltet wirklich auf gedimmt."""
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[38;2;2;2;2mich tippe\x1b[39m")) is False
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[48;2;2;2;2mich tippe\x1b[49m")) is False
    assert aufpasser.eingabe_leer(_feld('❯ \x1b[38;2;2;2;2;2mTry "fix"\x1b[0m')) is True


def test_ohne_eingabefeld_nie_leer() -> None:
    assert aufpasser.eingabe_leer(f"\x1b[2m{LINIE}\x1b[0m\nbau@server:~$ \n") is False


def test_ohne_farben_entfernt_nur_steuerzeichen() -> None:
    assert aufpasser.ohne_farben(_feld(PLATZHALTER)) == _feld('❯ Try "fix lint errors"', linie=LINIE)


# --- Aufgabe 1 im Wecker: Bild mit Farbcodes, nur für diese Prüfung --------------------------


def _aufrufe(welt: dict[str, Any]) -> list[list[str]]:
    datei = welt["tmux"] / "aufrufe.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def test_wecker_weckt_fenster_mit_grauem_platzhalter(welt: dict[str, Any]) -> None:
    """Frische Sitzung mit Platzhalter hinter ``❯`` → genau ein Weckruf; gelesen mit ``-e``."""
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    frage_id = _frage(welt)
    _bild(welt, _feld(PLATZHALTER))
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage_id in getippt[0]["text"]
    bilder = [a for a in _aufrufe(welt) if a[:1] == ["capture-pane"]]
    assert bilder and all("-e" in a for a in bilder), bilder


def test_wecker_tippt_nie_in_echten_text_mit_farben(welt: dict[str, Any]) -> None:
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _frage(welt)
    for bild in (
        _feld("❯ ich tippe gerade"),
        _feld('❯ ich\x1b[2mTry "fix"\x1b[0m'),
        HALB,
    ):
        _bild(welt, bild)
        w.einmal()
    assert _getippt(welt) == []


def test_bildschirm_ohne_farben_bleibt_klartext(welt: dict[str, Any]) -> None:
    """Andere Aufrufer von ``bildschirm`` bekommen weiter ``capture-pane -p`` ohne ``-e``."""
    respawn.TmuxWerkzeug().bildschirm(PANE)
    assert _aufrufe(welt)[-1] == ["capture-pane", "-p", "-t", PANE]


# --- Aufgabe 3a: Claude läuft, aber kein Eingabefeld zu sehen --------------------------------


def test_claude_ohne_eingabefeld_kein_tippen(welt: dict[str, Any]) -> None:
    """Claude-PID im Pane, still, aber kein ``❯``-Feld (z. B. Vollbild-Dialog) → nichts tippen."""
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _frage(welt)
    for bild in (
        "● Frage eingeworfen, warte.\n\n  ⏵⏵ auto mode\n",
        f"{LINIE}\n  Esc to cancel\n{LINIE}\n",
    ):
        _bild(welt, bild)
        w.einmal()
        w.einmal()
    assert _getippt(welt) == []


# --- Aufgaben 2 + 3b: scheiternde tmux-Aufrufe ------------------------------------------------

HUELLE = """
import os, sys
from pathlib import Path
_kaputt = Path(os.environ["FAKE_TMUX_ORDNER"]) / "kaputt.json"
if _kaputt.is_file():
    import json as _json
    _regel = _json.loads(_kaputt.read_text(encoding="utf-8"))
    if sys.argv[1:2] == [_regel["befehl"]]:
        with (Path(os.environ["FAKE_TMUX_ORDNER"]) / "aufrufe.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(sys.argv[1:]) + "\\n")
        sys.stderr.write(_regel["stderr"])
        sys.exit(1)
"""


@pytest.fixture
def kaputt(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """tmux-Attrappe, die den Unterbefehl aus ``kaputt.json`` mit Exit 1 scheitern lässt."""
    tmux = attrappe(welt["bin"] / "bin-kaputt", "tmux", HUELLE + FAKE_TMUX)
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([sys.executable, str(tmux)]))
    return welt


def _kaputt_machen(welt: dict[str, Any], befehl: str, stderr: str) -> None:
    (welt["tmux"] / "kaputt.json").write_text(json.dumps({"befehl": befehl, "stderr": stderr}), encoding="utf-8")


def _heil_machen(welt: dict[str, Any]) -> None:
    (welt["tmux"] / "kaputt.json").unlink()


def test_tippen_raeumt_puffer_bei_paste_fehler(kaputt: dict[str, Any]) -> None:
    """Scheitert ``paste-buffer``, löscht ``tippen`` den eigenen Puffer und wirft weiter."""
    _kaputt_machen(kaputt, "paste-buffer", "server busy")
    with pytest.raises(TmuxFehler):
        respawn.TmuxWerkzeug().tippen(PANE, "hallo")
    aufrufe = _aufrufe(kaputt)
    geladen = next(a for a in aufrufe if a[:1] == ["load-buffer"])
    puffer = geladen[geladen.index("-b") + 1]
    assert ["delete-buffer", "-b", puffer] in aufrufe, aufrufe
    assert not any(a[:1] == ["send-keys"] for a in aufrufe), "nach gescheitertem Einfügen kein Enter"


def test_tippen_ohne_fehler_loescht_keinen_puffer_extra(kaputt: dict[str, Any]) -> None:
    respawn.TmuxWerkzeug().tippen(PANE, "hallo")
    assert not any(a[:1] == ["delete-buffer"] for a in _aufrufe(kaputt))


def test_tmux_fehler_beim_tippen_kein_absturz_einmal_gemeldet_dann_geweckt(
    kaputt: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    w = _wecker_modul().Wecker(SPEC, kaputt["haupt"], PANE)
    frage_id = _frage(kaputt)
    _kaputt_machen(kaputt, "paste-buffer", "server busy")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            w.einmal()
    assert _getippt(kaputt) == []
    meldungen = [r for r in caplog.records if "gescheitert" in r.getMessage()]
    assert len(meldungen) == 1, [r.getMessage() for r in caplog.records]
    _heil_machen(kaputt)
    w.einmal()
    getippt = _getippt(kaputt)
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage_id in getippt[0]["text"]


def test_fenster_weg_beim_tippen_frage_erledigt(kaputt: dict[str, Any]) -> None:
    w = _wecker_modul().Wecker(SPEC, kaputt["haupt"], PANE)
    _frage(kaputt)
    _kaputt_machen(kaputt, "paste-buffer", f"can't find pane: {PANE}")
    w.einmal()
    _heil_machen(kaputt)
    w.einmal()
    w.einmal()
    assert _getippt(kaputt) == [], "Fenster war weg: Frage gilt als erledigt, kein später Weckruf"


def test_wecker_faden_ueberlebt_tmux_fehler(kaputt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Der echte Faden aus ``laeuft`` läuft nach einem tmux-Fehler weiter und weckt danach."""
    modul = _wecker_modul()
    monkeypatch.setattr(modul, "TAKT_S", 0.05)
    monkeypatch.setenv("TMUX_PANE", PANE)
    _kaputt_machen(kaputt, "paste-buffer", "server busy")
    with modul.laeuft(SPEC, kaputt["haupt"]) as w:
        assert w is not None
        _frage(kaputt)
        ende = time.time() + 5
        while time.time() < ende and not any(a[:1] == ["paste-buffer"] for a in _aufrufe(kaputt)):
            time.sleep(0.05)
        _heil_machen(kaputt)
        while time.time() < ende and not _getippt(kaputt):
            time.sleep(0.05)
    assert [g["ziel"] for g in _getippt(kaputt)] == [PANE]


def test_stille_feld_ohne_farben_weiter_leer() -> None:
    """Gegenprobe zum Altbestand: das klare Bild aus #414 bleibt „leer“."""
    assert aufpasser.eingabe_leer(STILL) is True


# --- Fix-Runde 3 (Review 395d121): Mehrzeiler, SGR-Feinheiten, sonstige Steuerfolgen ----------


def test_mehrzeilig_gedimmte_erste_zeile_normaler_text_darunter_ist_belegt() -> None:
    """Zeile 1 hinter ``❯`` gedimmt, Folgezeile ohne ``❯`` mit normalem Text → belegt."""
    eingabe = '❯ \x1b[2mTry "fix lint errors"\x1b[0m\n  zweite Zeile getippt'
    assert aufpasser.eingabe_leer(_feld(eingabe)) is False


def test_mehrzeilig_alles_gedimmt_bleibt_leer() -> None:
    eingabe = '❯ \x1b[2mTry "fix lint errors"\x1b[0m\n  \x1b[2mund noch mehr\x1b[0m'
    assert aufpasser.eingabe_leer(_feld(eingabe)) is True


def test_doppelt_unterstrichen_mit_doppelpunkt_ist_nicht_gedimmt() -> None:
    """``4:2`` ist der Unterstrich-Stil „doppelt“, kein SGR 2 → Text bleibt belegt."""
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[4:2mich tippe\x1b[0m")) is False


def test_truecolor_mit_doppelpunkt_ist_nicht_gedimmt() -> None:
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[38:2::153:153:153mich tippe\x1b[39m")) is False


def test_reset_und_gedimmt_in_einer_folge_ist_leer() -> None:
    assert aufpasser.eingabe_leer(_feld('❯ \x1b[0;2mTry "fix lint errors"\x1b[0m')) is True


def test_256_farben_index_2_ist_nicht_gedimmt() -> None:
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[38;5;2mich tippe\x1b[39m")) is False


def test_reset_mitten_in_der_zeile_ist_belegt() -> None:
    assert aufpasser.eingabe_leer(_feld("❯ \x1b[2mfoo\x1b[0m bar")) is False


def test_cursor_und_csi_folgen_im_gedimmten_platzhalter_bleiben_leer() -> None:
    """Cursor als inverser Block (SGR 7) und Cursor-Folgen ändern nichts am Gedimmt-Sein."""
    eingabe = '❯ \x1b[?25l\x1b[2m\x1b[7mT\x1b[27mry "fix lint errors"\x1b[0m\x1b[1C\x1b[K'
    assert aufpasser.eingabe_leer(_feld(eingabe)) is True


def test_inverser_cursor_ohne_dim_vor_gedimmtem_platzhalter_ist_leer() -> None:
    """#435: Ein einzelner inverser Cursor-Buchstabe (SGR 7) ohne SGR 2 direkt vor einem
    gedimmten Platzhalter gehört zum Platzhalter, nicht zu getipptem Text."""
    for eingabe in (
        '❯ \x1b[7mT\x1b[27m\x1b[2mry "fix lint errors"\x1b[0m',
        '❯ \x1b[7mT\x1b[0m\x1b[2mry "fix lint errors"\x1b[0m',
        '❯ \x1b[7mT\x1b[27;2mry "fix lint errors"\x1b[0m',
        '❯ \x1b[7mT\x1b[0;2mry "fix lint errors"\x1b[0m',
        '❯ \x1b[?25l\x1b[7mT\x1b[27m\x1b[2mry "fix lint errors"\x1b[0m\x1b[1C\x1b[K',
    ):
        assert aufpasser.eingabe_leer(_feld(eingabe)) is True, eingabe


def test_inverser_text_ohne_platzhalter_bleibt_belegt() -> None:
    """#435 Gegenproben: inverser Text ohne folgenden gedimmten Platzhalter, mehr als ein
    inverses Zeichen oder echter Text im Feld gelten weiter als belegt."""
    for eingabe in (
        "❯ \x1b[7mT\x1b[27m",
        '❯ \x1b[7mTr\x1b[27m\x1b[2my "fix"\x1b[0m',
        '❯ ich\x1b[7mT\x1b[27m\x1b[2mry "fix"\x1b[0m',
        '❯ \x1b[7mT\x1b[27m \x1b[2mry "fix"\x1b[0m',
        "❯ \x1b[2mfoo\x1b[0m\x1b[7mT\x1b[27m\x1b[2mbar\x1b[0m",
        '❯ \x1b[7mT\x1b[27m\x1b[2mry "fix"\x1b[0m\n  zweite Zeile',
        '❯ \x1b[38;2;7;7;7mT\x1b[39m\x1b[2mry "fix"\x1b[0m',
        "❯ \x1b[7mT\x1b[27m\x1b[2m   \x1b[0m",
    ):
        assert aufpasser.eingabe_leer(_feld(eingabe)) is False, eingabe


def test_osc_link_folgen_zaehlen_nicht_als_text() -> None:
    """OSC 8 (Link) endet mit BEL oder ``ESC \\`` und ist keine Eingabe."""
    assert aufpasser.eingabe_leer(_feld("❯ \x1b]8;;https://x.de\x07\x1b]8;;\x07")) is True
    assert aufpasser.eingabe_leer(_feld("❯ \x1b]8;;https://x.de\x1b\\\x1b]8;;\x1b\\")) is True
    assert aufpasser.ohne_farben("a\x1b]8;;https://x.de\x07b\x1b]8;;\x1b\\c") == "abc"


def test_protocol_bildschirm_kennt_farben() -> None:
    """Die Naht ``Werkzeug`` beschreibt ``bildschirm`` so, wie ``TmuxWerkzeug`` sie anbietet."""
    import inspect

    parameter = inspect.signature(respawn.Werkzeug.bildschirm).parameters
    assert "farben" in parameter
    assert parameter["farben"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter["farben"].default is False
