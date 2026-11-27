"""Aufpasser #894 (Folge #1463): beschädigte ``stand.json``-Einträge beim Laden
verwerfen und genau einmal loggen.

Hinweis: Limit-Probe je Fenster-Modell aus dem Transkript sowie das Ignorieren alter
Limit-Zeilen über neuer fertiger Ausgabe sind bereits über #892 auf ``main`` und in
``tests/test_aufpasser_limit_1453.py`` abgedeckt.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser

JETZT = 1_800_000_000.0


def _aufpasser_mit_stand(tmp: Path, inhalt: object) -> aufpasser.Aufpasser:
    zustand = tmp / "zustand"
    zustand.mkdir(parents=True, exist_ok=True)
    (zustand / "stand.json").write_text(json.dumps(inhalt), encoding="utf-8")
    return aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=zustand,
            tmux_socket="nie-benutzt",
            jetzt=lambda: JETZT,
            heim=tmp / "heim",
        )
    )


def test_stand_laden_verwirft_beschaedigte_eintraege_und_loggt_einmal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Kaputte Einträge in stand.json werden verworfen, gültige bleiben, genau 1 Warning."""
    kaputt = {
        "fenster": {
            "spec-1/bau 1": {"hash": "abc", "seit": JETZT - 60, "stufe": 0},
            "spec-2/bau 2": "kein-dict",
            "spec-3/bau 3": None,
        },
        "meldungen": {
            "ok": JETZT - 10,
            "kaputt_str": "keine-zahl",
            "kaputt_bool": True,
        },
        "starts": {
            "ok": [JETZT - 100, "kaputt"],
            "kein_liste": "abc",
        },
        "limit_proben": {
            "claude|ok": {"zeit": JETZT - 60, "offen": False},
            "claude|kaputt_typ": "kein-dict",
            "claude|kaputt_zeit": {"zeit": "keine-zahl", "offen": True},
        },
        "platz_zuletzt": "keine-zahl",
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, kaputt)

    assert list(a.stand.fenster.keys()) == ["spec-1/bau 1"]
    assert a.stand.meldungen == {"ok": JETZT - 10}
    assert a.stand.starts == {"ok": [JETZT - 100]}
    assert list(a.stand.limit_proben.keys()) == ["claude|ok"]
    assert a.stand.platz_zuletzt == 0.0
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1


def test_stand_laden_kein_dict_liefert_leeren_stand_und_loggt_einmal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Ist das Wurzelobjekt in stand.json kein Dict, startet der Aufpasser leer mit 1 Warning."""
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, ["kein", "dict"])

    assert a.stand == aufpasser.Stand()
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1


def test_stand_laden_kaputte_limit_probe_sperrt_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ein beschädigter limit_proben-Eintrag verhindert weder Laden noch die nächste Probe."""
    kaputt = {
        "fenster": {},
        "meldungen": {},
        "starts": {},
        "limit_proben": {"claude|claude-opus-5-5": {"zeit": "kaputt", "offen": False}},
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, kaputt)

    assert a.stand.limit_proben == {}
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert aufpasser._probe_gueltig({"zeit": "kaputt", "offen": False}, JETZT) is False


TICKET = 91463
PANE_STILL = "✻ Baked for 40s · done 12:09 AM\n" + ("─" * 80) + "\n❯ \n" + ("─" * 80) + "\n"
PANE_HASH = aufpasser.pane_hash(PANE_STILL)


def _pruef_helfer(
    tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    inhalt: object,
    pane_text: str = PANE_STILL,
) -> tuple[aufpasser.Aufpasser, aufpasser.Fenster, str]:
    monkeypatch.setattr(aufpasser, "bau_lauf_im_baum", lambda pid: None)
    monkeypatch.setattr(aufpasser, "worktree_finden", lambda *a, **k: None)
    a = _aufpasser_mit_stand(tmp, inhalt)
    a.pane_text = lambda ziel: pane_text  # type: ignore[method-assign]
    a.session = lambda f: aufpasser.SessionInfo("sid-1463", None, "idle", 4242)  # type: ignore[method-assign]
    a.deploy_laeuft = lambda muster=None: []  # type: ignore[method-assign]
    a.gate_im_fenster = lambda f, wt: None  # type: ignore[method-assign]
    a.folge_ab_handoff = lambda *a_, **k: False  # type: ignore[method-assign]
    a.melden = lambda *a_, **k: None  # type: ignore[method-assign]
    a.anstupsen = lambda *a_, **k: None  # type: ignore[method-assign]
    a.sichern_und_fortsetzen = lambda *a_, **k: None  # type: ignore[method-assign]
    f = aufpasser.Fenster(f"spec-{TICKET}", "1463", "3", f"bau {TICKET}", "/x", 4242)
    return a, f, f"{f.sitzung}/{f.name}"


@pytest.mark.parametrize(
    ("fall", "kaputter_eintrag"),
    [
        ("seit_fehlt", {"hash": PANE_HASH, "stufe": 0}),
        ("seit_none", {"hash": PANE_HASH, "seit": None, "stufe": 0}),
        ("seit_string", {"hash": PANE_HASH, "seit": "keine-zahl", "stufe": 0}),
        ("seit_bool", {"hash": PANE_HASH, "seit": True, "stufe": 0}),
        ("seit_nan", {"hash": PANE_HASH, "seit": float("nan"), "stufe": 0}),
        ("seit_inf", {"hash": PANE_HASH, "seit": float("inf"), "stufe": 0}),
        ("eingriff_string", {"hash": PANE_HASH, "seit": JETZT - 7200, "eingriff": "kaputt", "stufe": 0}),
        ("eingriff_bool", {"hash": PANE_HASH, "seit": JETZT - 7200, "eingriff": False, "stufe": 0}),
        ("pane_aenderung_string", {"hash": PANE_HASH, "seit": JETZT - 7200, "pane_aenderung": "kaputt", "stufe": 0}),
        ("pane_aenderung_inf", {"hash": PANE_HASH, "seit": JETZT - 7200, "pane_aenderung": float("inf"), "stufe": 0}),
        ("stufe_string", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": "kaputt"}),
        ("stufe_bool", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": True}),
        ("stufe_none", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": None}),
        ("stufe_float", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": 1.5}),
        ("stupser_kein_list", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": 0, "stupser": "kaputt"}),
        (
            "stupser_string_elem",
            {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": 0, "stupser": [JETZT - 60, "kaputt"]},
        ),
        ("stupser_bool_elem", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": 0, "stupser": [True]}),
        ("stupser_nan_elem", {"hash": PANE_HASH, "seit": JETZT - 7200, "stufe": 0, "stupser": [float("nan")]}),
    ],
)
def test_stand_laden_verwirft_kaputte_fenster_eintraege_und_laeuft_ohne_absturz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fall: str,
    kaputter_eintrag: dict[str, object],
) -> None:
    """Beschädigte innere Felder in ``fenster`` werden verworfen (1 Warning),
    und anschließende ``fenster_pruefen()``- und ``eintrag()``-Läufe stürzen nicht ab."""
    schluessel = f"spec-{TICKET}/bau {TICKET}"
    inhalt = {
        "fenster": {
            "spec-1/bau 1": {"hash": "abc", "seit": JETZT - 60, "stufe": 0},
            schluessel: kaputter_eintrag,
        },
        "meldungen": {},
        "starts": {},
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a, f, geladen_schluessel = _pruef_helfer(tmp_path, monkeypatch, inhalt)

    assert geladen_schluessel == schluessel
    assert schluessel not in a.stand.fenster, fall
    assert list(a.stand.fenster.keys()) == ["spec-1/bau 1"], fall
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1, fall

    a.fenster_pruefen(f, tmp_path, {TICKET})
    e = a.eintrag(schluessel, "neuer Pane-Text\n❯ \n")
    assert isinstance(e["seit"], float)


def test_stand_laden_normalisiert_gueltige_fenster_eintraege(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Gültige ``fenster``-Einträge (inkl. optionaler ``None``-Felder) bleiben erhalten
    und ihre Zeitstempel werden als ``float`` bzw. ``list[float]`` normalisiert."""
    inhalt = {
        "fenster": {
            "spec-1/bau 1": {
                "hash": "abc",
                "seit": int(JETZT - 120),
                "eingriff": int(JETZT - 60),
                "pane_aenderung": int(JETZT - 90),
                "stufe": 2,
                "stupser": [int(JETZT - 300), JETZT - 60.5],
            },
            "spec-2/bau 2": {
                "hash": "def",
                "seit": JETZT - 30,
                "eingriff": None,
                "pane_aenderung": None,
                "stupser": None,
            },
        },
        "meldungen": {},
        "starts": {},
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, inhalt)

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    e1 = a.stand.fenster["spec-1/bau 1"]
    assert isinstance(e1["seit"], float) and e1["seit"] == float(int(JETZT - 120))
    assert isinstance(e1["eingriff"], float) and e1["eingriff"] == float(int(JETZT - 60))
    assert isinstance(e1["pane_aenderung"], float) and e1["pane_aenderung"] == float(int(JETZT - 90))
    assert e1["stufe"] == 2 and type(e1["stufe"]) is int
    assert e1["stupser"] == [float(int(JETZT - 300)), JETZT - 60.5]
    assert all(type(t) is float for t in e1["stupser"])
    assert "spec-2/bau 2" in a.stand.fenster


def test_stand_laden_mehrere_kaputte_fenster_eintraege_ein_sammelwarning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Mehrere kaputte ``fenster``-Einträge werden zusammengezählt und erzeugen genau 1 Warning."""
    inhalt = {
        "fenster": {
            "spec-1/bau 1": {"hash": "ok", "seit": JETZT - 60, "stufe": 0},
            "spec-2/bau 2": {"hash": "a", "seit": "kaputt"},
            "spec-3/bau 3": {"hash": "b", "seit": JETZT - 60, "stufe": "x"},
            "spec-4/bau 4": {"hash": "c", "seit": JETZT - 60, "stupser": ["x"]},
        },
    }
    with caplog.at_level(logging.WARNING, logger="aufpasser"):
        a = _aufpasser_mit_stand(tmp_path, inhalt)

    assert list(a.stand.fenster.keys()) == ["spec-1/bau 1"]
    warnungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnungen) == 1
    assert "3 beschädigte Einträge" in warnungen[0].getMessage()
