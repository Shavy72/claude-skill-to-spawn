"""Aufpasser #1453: ``bau``-Fenster mit Limit-Text wecken, sobald das Limit offen ist.

Vorfall 20.11.2026: ``bau 1375`` stand ~35 min mit „weekly limit“ still, obwohl das
Limit längst wieder offen war — „usage limit“ zählte als Arbeit, niemand stupste an.
Die Limit-Probe ist die aus #1449 (``waechter_lauf.limit_offen``), hier nur als
Attrappe: kein echter Claude-Aufruf, kein echtes tmux.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser

TICKET = 91453
JETZT = 1_800_000_000.0
LINIE = "─" * 80
LIMIT = (
    "⏺ Schreibe den Test\n"
    "  ⎿  You've hit your weekly limit · resets 8pm (Europe/Berlin)\n"
    "     /upgrade to increase your usage limit."
)


def bildschirm(ausgabe: str) -> str:
    """Pane-Text wie Claude Code ihn zeigt: Ausgabe, Eingabefeld, Statuszeile."""
    return "\n".join(
        [
            ausgabe,
            "",
            LINIE,
            "❯ ",
            LINIE,
            "  Opus 5.5 (1M context) · 91.7k (9.0%) · 2h06m",
            "  📁 spawn-1375 · ⎇ HEAD · 5h: 10% (reset 03:20) · 7d: 100%",
            "",
        ]
    )


def _aufpasser(
    tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    offen: bool,
    name: str = f"bau {TICKET}",
    jetzt: float = JETZT,
    trocken: bool = False,
) -> tuple[aufpasser.Aufpasser, aufpasser.Fenster, dict[str, list]]:
    spur: dict[str, list] = {"probe": [], "stupser": [], "fortsetzen": []}

    def probe(claude: str, modell: str, cwd: Path) -> bool:
        spur["probe"].append(modell)
        return offen

    monkeypatch.setattr(aufpasser, "limit_offen", probe, raising=False)
    monkeypatch.setattr(aufpasser, "bau_lauf_im_baum", lambda pid: None)
    monkeypatch.setattr(aufpasser, "worktree_finden", lambda *a, **k: None)
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp / "stand.json", tmux_socket="nie-benutzt", jetzt=lambda: jetzt, trocken=trocken
        )
    )
    a.pane_text = lambda ziel: text  # type: ignore[method-assign]
    a.session = lambda f: aufpasser.SessionInfo("sid", None, "idle", 4242)  # type: ignore[method-assign]
    a.deploy_laeuft = lambda muster=None: []  # type: ignore[method-assign]
    a.gate_im_fenster = lambda f, wt: None  # type: ignore[method-assign]
    a.folge_ab_handoff = lambda *a_, **k: False  # type: ignore[method-assign]
    a.melden = lambda *a_, **k: None  # type: ignore[method-assign]
    a.anstupsen = lambda ziel, still_min: spur["stupser"].append(ziel)  # type: ignore[method-assign]
    a.sichern_und_fortsetzen = lambda *a_, **k: spur["fortsetzen"].append(1)  # type: ignore[method-assign]
    f = aufpasser.Fenster(f"spec-{TICKET}", "1365", "3", name, "/x", 4242)
    # Seit zwei Stunden derselbe Text: weit über der Hänge-Schwelle.
    a.stand.fenster[f"{f.sitzung}/{f.name}"] = {
        "hash": aufpasser.pane_hash(text),
        "seit": jetzt - 2 * 3600,
        "stufe": 0,
    }
    return a, f, spur


def test_limit_offen_genau_ein_anstoss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rot vor #1453: Limit-Text zählte als Arbeit — Fenster blieb liegen."""
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [f.ziel]
    assert len(spur["probe"]) == 1


def test_limit_zu_kein_anstoss_keine_dauerschleife(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=False)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [] and spur["fortsetzen"] == []
    # Eine Probe je Aufpasser-Lauf, nicht je Prüfung.
    assert len(spur["probe"]) == 1


def test_limit_oben_darunter_neue_arbeit_kein_anstoss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = bildschirm(LIMIT + "\n\n❯ weiter\n\n⏺ Lese die Datei\n✻ Baked for 40s")
    a, f, spur = _aufpasser(tmp_path, monkeypatch, text, offen=True)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    # #1463 (B3): darunter steht fertige Arbeit („Baked for 40s“), seit 2 h still — die
    # Limit-Zeile ist alt, also Anstoß ohne Probe. Laufende Arbeit darunter: Gegenprobe in
    # test_aufpasser_limit_modell_1463.py::test_limit_oben_arbeit_laeuft_darunter_kein_anstoss.
    assert spur["stupser"] == [f.ziel] and spur["probe"] == []


def test_ohne_limit_text_wie_bisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm("✻ Baked for 40s · done 12:09 AM"), offen=False)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [f.ziel] and spur["probe"] == []


def test_limit_und_esc_to_interrupt_arbeitet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = bildschirm(LIMIT + "\n✻ Thinking… (esc to interrupt)")
    a, f, spur = _aufpasser(tmp_path, monkeypatch, text, offen=True)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [] and spur["probe"] == []


def test_aufseher_fenster_mit_limit_text_unberuehrt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Aufseher wartet sein Limit selbst ab (#1449) — der Aufpasser bleibt draußen."""
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True, name="wache 1365")
    a.fenster_pruefen(f, tmp_path, set())
    assert spur["stupser"] == [] and spur["probe"] == []


# -- Fix-Runde 1: Drossel (B1) und Trockenlauf (B2) ---------------------------------


def _lauf(
    tmp: Path, monkeypatch: pytest.MonkeyPatch, jetzt: float, offen: bool = False
) -> dict[str, list]:
    """Ein Aufpasser-Lauf (neue Instanz = Neustart) mit Stand-Schreiben am Ende."""
    a, f, spur = _aufpasser(
        tmp, monkeypatch, bildschirm(LIMIT), offen=offen, jetzt=jetzt
    )
    a.fenster_pruefen(f, tmp, {TICKET})
    a._stand_schreiben()
    return spur


def test_drossel_zweite_probe_innerhalb_30_min_bleibt_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1: Limit zu — der nächste Lauf 15 min später fragt die Probe nicht erneut."""
    assert len(_lauf(tmp_path, monkeypatch, JETZT)["probe"]) == 1
    spur = _lauf(tmp_path, monkeypatch, JETZT + 15 * 60)
    assert spur["probe"] == [] and spur["stupser"] == []


def test_drossel_nach_30_min_wieder_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert len(_lauf(tmp_path, monkeypatch, JETZT)["probe"]) == 1
    spur = _lauf(
        tmp_path, monkeypatch, JETZT + aufpasser.LIMIT_PROBE_ABSTAND_S, offen=True
    )
    assert len(spur["probe"]) == 1 and len(spur["stupser"]) == 1


def test_drossel_ueberlebt_neustart_im_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Zeitstempel liegt in ``stand.json`` — ein frischer Aufpasser sieht ihn."""
    _lauf(tmp_path, monkeypatch, JETZT)
    roh = (tmp_path / "stand.json" / "stand.json").read_text(encoding="utf-8")
    assert "limit_proben" in roh
    spur = _lauf(tmp_path, monkeypatch, JETZT + 29 * 60, offen=True)
    assert spur["probe"] == [] and spur["stupser"] == []


def test_trockenlauf_ruft_probe_nicht_auf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B2: ``--trocken`` kostet kein Abo — nur melden, dass geprüft würde."""
    a, f, spur = _aufpasser(
        tmp_path, monkeypatch, bildschirm(LIMIT), offen=True, trocken=True
    )
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["probe"] == [] and spur["stupser"] == []
    assert "[trocken] Limit-Probe würde laufen" in capsys.readouterr().out


# -- Fix-Runde 2: Zeit in der Zukunft im Stand ---------------------------------------


def test_zukunftszeit_im_stand_sperrt_probe_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uhr verstellt/Datei kopiert: Eintrag aus der Zukunft gilt als „keine Probe bekannt“."""
    import json

    _lauf(tmp_path, monkeypatch, JETZT)
    datei = tmp_path / "stand.json" / "stand.json"
    roh = json.loads(datei.read_text(encoding="utf-8"))
    for eintrag in roh["limit_proben"].values():
        eintrag["zeit"] = JETZT + 10 * 86400
    datei.write_text(json.dumps(roh), encoding="utf-8")
    spur = _lauf(tmp_path, monkeypatch, JETZT + 60)
    assert len(spur["probe"]) == 1
    neu = json.loads(datei.read_text(encoding="utf-8"))
    assert [e["zeit"] for e in neu["limit_proben"].values()] == [JETZT + 60]
