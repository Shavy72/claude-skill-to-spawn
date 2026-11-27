"""Aufpasser #892 (Folge #1449 / #1453 / #1463): ``bau``-Fenster mit Limit-Text wecken,
sobald das Nutzungs-Limit wieder offen ist.

Prüft ohne echte Claude-Aufrufe und ohne echtes tmux:
- ``waechter_lauf.limit_offen`` (inkl. Entfernen von ``ANTHROPIC_API_KEY`` und Fehlerzweigen)
  und ``waechter_lauf.ist_echte_antwort``
- Limit-Pause als eigenen Zustand im Aufpasser (``limit_pause``, ``ohne_limit_zeilen``)
- Gedrosselte Probe (max. 1× je 30 min, persistent in ``stand.json``, Zukunftszeit ignoriert,
  keine Probe im Trockenlauf)
- Probe je Fenster-Modell aus dem Session-Transkript (Fallback ``modelle.ticket``)
- Alte Limit-Zeile über fertiger vs. laufender Arbeit
- Aufseher-Fenster bleiben unberührt
- 3/24h-Bremse (``STUPSER_MAX``) greift auch beim Wecken nach Limit-Ende
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, starter, waechter_lauf
from to_spawn.waechter_lauf import transkript_ordner

TICKET = 91453
JETZT = 1_800_000_000.0
LINIE = "─" * 80
LIMIT = (
    "⏺ Schreibe den Test\n"
    "  ⎿  You've hit your weekly limit · resets 8pm (Europe/Berlin)\n"
    "     /upgrade to increase your usage limit."
)
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-4-5"


def bildschirm(ausgabe: str) -> str:
    """Pane-Text wie Claude Code ihn zeigt: Ausgabe, Eingabefeld, Statuszeile."""
    return (
        f"{ausgabe}\n\n"
        f"{LINIE}\n"
        "❯ \n"
        f"{LINIE}\n"
        "  Opus 5.5 (1M context) · 91.7k (9.0%) · 2h06m\n"
        "  📁 spawn-1375 · ⎇ HEAD · 5h: 10% (reset 03:20) · 7d: 100%\n"
    )


def transkript_schreiben(heim: Path, cwd: Path, sid: str, modell: str) -> None:
    """Echte Antwort mit ``modell``, danach eine künstliche Limit-Zeile von Claude Code."""
    ordner = transkript_ordner(cwd, heim)
    ordner.mkdir(parents=True, exist_ok=True)
    zeilen = [
        {"type": "user", "message": {"content": "weiter"}},
        {"type": "assistant", "message": {"model": modell, "content": "Schreibe den Test"}},
        {
            "type": "assistant",
            "isApiErrorMessage": True,
            "message": {"model": "<synthetic>", "content": "You've hit your weekly limit"},
        },
    ]
    (ordner / f"{sid}.jsonl").write_text("\n".join(json.dumps(z) for z in zeilen) + "\n", encoding="utf-8")


def _aufpasser(
    tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str | dict[str, str],
    offen: bool | dict[str, bool],
    name: str = f"bau {TICKET}",
    jetzt: float = JETZT,
    trocken: bool = False,
    sessions: dict[str, aufpasser.SessionInfo | None] | None = None,
) -> tuple[aufpasser.Aufpasser, aufpasser.Fenster, dict[str, list[Any]]]:
    spur: dict[str, list[Any]] = {"probe": [], "stupser": [], "fortsetzen": [], "meldungen": []}

    def probe(claude: str, modell: str, cwd: Path) -> bool:
        spur["probe"].append(modell)
        return offen if isinstance(offen, bool) else offen[modell]

    monkeypatch.setattr(aufpasser, "limit_offen", probe, raising=False)
    monkeypatch.setattr(aufpasser, "bau_lauf_im_baum", lambda pid: None)
    monkeypatch.setattr(aufpasser, "worktree_finden", lambda *a, **k: None)
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp / "zustand",
            tmux_socket="nie-benutzt",
            jetzt=lambda: jetzt,
            trocken=trocken,
            heim=tmp / "heim",
        )
    )
    if isinstance(text, dict):
        a.pane_text = lambda ziel: text[ziel.split(":", 1)[0].removeprefix("=spec-")]  # type: ignore[method-assign]
    else:
        a.pane_text = lambda ziel: text  # type: ignore[method-assign]
    a.session = lambda f: (sessions or {}).get(f.name) or aufpasser.SessionInfo("sid", None, "idle", 4242)  # type: ignore[method-assign]
    a.deploy_laeuft = lambda muster=None: []  # type: ignore[method-assign]
    a.gate_im_fenster = lambda f, wt: None  # type: ignore[method-assign]
    a.folge_ab_handoff = lambda *a_, **k: False  # type: ignore[method-assign]
    a.melden = lambda spec, repo, schluessel, ereignis, nachricht: spur["meldungen"].append(ereignis)  # type: ignore[method-assign]
    a.anstupsen = lambda ziel, still_min: spur["stupser"].append(ziel)  # type: ignore[method-assign]
    a.sichern_und_fortsetzen = lambda *a_, **k: spur["fortsetzen"].append(1)  # type: ignore[method-assign]
    f = aufpasser.Fenster(f"spec-{TICKET}", "1365", "3", name, "/x", 4242)
    if isinstance(text, str):
        a.stand.fenster[f"{f.sitzung}/{f.name}"] = {
            "hash": aufpasser.pane_hash(text),
            "seit": jetzt - 2 * 3600,
            "stufe": 0,
        }
    return a, f, spur


def _fenster(a: aufpasser.Aufpasser, ticket: int, pane: str, text: str) -> aufpasser.Fenster:
    f = aufpasser.Fenster(f"spec-{ticket}", "1365", pane, f"bau {ticket}", "/x", 4200 + int(pane))
    a.stand.fenster[f"{f.sitzung}/{f.name}"] = {
        "hash": aufpasser.pane_hash(text),
        "seit": JETZT - 2 * 3600,
        "stufe": 0,
    }
    return f


# -- waechter_lauf.limit_offen & ist_echte_antwort (#1449 / #892) --------------------


def test_limit_offen_probe_offen_und_ohne_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe: list[tuple[list[str], dict[str, Any]]] = []

    def fake_starte(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        aufrufe.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-geheim")
    monkeypatch.setattr(starter, "starte", fake_starte)
    assert waechter_lauf.limit_offen("claude", OPUS, tmp_path) is True
    assert len(aufrufe) == 1
    cmd, kwargs = aufrufe[0]
    assert cmd[:5] == ["claude", "-p", "--model", OPUS, "--no-session-persistence"]
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]


@pytest.mark.parametrize(
    ("ergebnis", "ausnahme"),
    [
        (subprocess.CompletedProcess(["claude"], 0, stdout="You've hit your weekly limit", stderr=""), None),
        (subprocess.CompletedProcess(["claude"], 1, stdout="", stderr="error"), None),
        (None, subprocess.TimeoutExpired(["claude"], 120)),
        (None, OSError("nicht gefunden")),
        (None, starter.StartGesperrt("Test-Sperre")),
    ],
)
def test_limit_offen_probe_fehler_und_limit_text_gelten_als_zu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ergebnis: subprocess.CompletedProcess[str] | None,
    ausnahme: Exception | None,
) -> None:
    def fake_starte(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if ausnahme is not None:
            raise ausnahme
        assert ergebnis is not None
        return ergebnis

    monkeypatch.setattr(starter, "starte", fake_starte)
    assert waechter_lauf.limit_offen("claude", OPUS, tmp_path) is False


def test_ist_echte_antwort_trennt_modell_von_synthetisch() -> None:
    assert waechter_lauf.ist_echte_antwort({"type": "assistant", "message": {"model": OPUS}}) is True
    assert (
        waechter_lauf.ist_echte_antwort({"type": "assistant", "isApiErrorMessage": True, "message": {"model": OPUS}})
        is False
    )
    assert waechter_lauf.ist_echte_antwort({"type": "assistant", "message": {"model": "<synthetic>"}}) is False
    assert waechter_lauf.ist_echte_antwort({"type": "user", "message": {"content": "hi"}}) is False


# -- Aufpasser: Limit-Pause, Wecken, Drossel, Trockenlauf (#1453 / #892) -------------


def test_limit_offen_genau_ein_anstoss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rot-Beweis vor #892: Limit-Text zählte als Arbeit — bau-Fenster blieb liegen."""
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [f.ziel]
    assert len(spur["probe"]) == 1


def test_limit_zu_kein_anstoss_keine_dauerschleife(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=False)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == [] and spur["fortsetzen"] == []
    assert len(spur["probe"]) == 1


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
    """Der Aufseher wartet sein Limit selbst ab — der Aufpasser bleibt draußen."""
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True, name="wache 1365")
    a.fenster_pruefen(f, tmp_path, set())
    assert spur["stupser"] == [] and spur["probe"] == []


def test_drei_in_24h_bremse_gilt_auch_nach_limit_ende(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """3/24h-Bremse: nach 3 Anstupsern in 24 h kein 4. Wecken, sondern ``stupser_erschoepft``."""
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True)
    e = a.stand.fenster[f"{f.sitzung}/{f.name}"]
    e["stupser"] = [JETZT - 3600, JETZT - 2400, JETZT - 1200]
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["stupser"] == []
    assert "stupser_erschoepft" in spur["meldungen"]
    assert len(spur["probe"]) == 1


def _lauf(tmp: Path, monkeypatch: pytest.MonkeyPatch, jetzt: float, offen: bool = False) -> dict[str, list[Any]]:
    a, f, spur = _aufpasser(tmp, monkeypatch, bildschirm(LIMIT), offen=offen, jetzt=jetzt)
    a.fenster_pruefen(f, tmp, {TICKET})
    a._stand_schreiben()
    return spur


def test_drossel_zweite_probe_innerhalb_30_min_bleibt_aus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_lauf(tmp_path, monkeypatch, JETZT)["probe"]) == 1
    spur = _lauf(tmp_path, monkeypatch, JETZT + 15 * 60)
    assert spur["probe"] == [] and spur["stupser"] == []


def test_drossel_nach_30_min_wieder_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_lauf(tmp_path, monkeypatch, JETZT)["probe"]) == 1
    spur = _lauf(tmp_path, monkeypatch, JETZT + aufpasser.LIMIT_PROBE_ABSTAND_S, offen=True)
    assert len(spur["probe"]) == 1 and len(spur["stupser"]) == 1


def test_drossel_ueberlebt_neustart_im_stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lauf(tmp_path, monkeypatch, JETZT)
    roh = (tmp_path / "zustand" / "stand.json").read_text(encoding="utf-8")
    assert "limit_proben" in roh
    spur = _lauf(tmp_path, monkeypatch, JETZT + 29 * 60, offen=True)
    assert spur["probe"] == [] and spur["stupser"] == []


def test_trockenlauf_ruft_probe_nicht_auf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    a, f, spur = _aufpasser(tmp_path, monkeypatch, bildschirm(LIMIT), offen=True, trocken=True)
    a.fenster_pruefen(f, tmp_path, {TICKET})
    assert spur["probe"] == [] and spur["stupser"] == []
    assert "[trocken] Limit-Probe würde laufen" in capsys.readouterr().out


def test_zukunftszeit_im_stand_sperrt_probe_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lauf(tmp_path, monkeypatch, JETZT)
    datei = tmp_path / "zustand" / "stand.json"
    roh = json.loads(datei.read_text(encoding="utf-8"))
    for eintrag in roh["limit_proben"].values():
        eintrag["zeit"] = JETZT + 10 * 86400
    datei.write_text(json.dumps(roh), encoding="utf-8")
    spur = _lauf(tmp_path, monkeypatch, JETZT + 60)
    assert len(spur["probe"]) == 1
    neu = json.loads(datei.read_text(encoding="utf-8"))
    assert [e["zeit"] for e in neu["limit_proben"].values()] == [JETZT + 60]


# -- Probe je Fenster-Modell & alte Limit-Zeile (#1463 / #892) -----------------------


def test_zwei_fenster_probe_je_modell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heim = tmp_path / "heim"
    cwd_opus, cwd_sonnet = tmp_path / "wt-opus", tmp_path / "wt-sonnet"
    transkript_schreiben(heim, cwd_opus, "sid-opus", OPUS)
    transkript_schreiben(heim, cwd_sonnet, "sid-sonnet", SONNET)
    text = bildschirm(LIMIT)
    sessions = {
        "bau 91001": aufpasser.SessionInfo("sid-opus", cwd_opus, "idle", 4201),
        "bau 91002": aufpasser.SessionInfo("sid-sonnet", cwd_sonnet, "idle", 4202),
    }
    a, _, spur = _aufpasser(
        tmp_path,
        monkeypatch,
        {"91001": text, "91002": text},
        offen={OPUS: False, SONNET: True},
        sessions=sessions,
    )
    f_opus = _fenster(a, 91001, "1", text)
    f_sonnet = _fenster(a, 91002, "2", text)
    a.fenster_pruefen(f_opus, tmp_path, {91001, 91002})
    a.fenster_pruefen(f_sonnet, tmp_path, {91001, 91002})
    assert sorted(spur["probe"]) == [OPUS, SONNET]
    assert spur["stupser"] == [f_sonnet.ziel]


def test_ohne_transkript_faellt_auf_ticket_modell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = bildschirm(LIMIT)
    a, _, spur = _aufpasser(tmp_path, monkeypatch, {"91003": text}, offen=True)
    f = _fenster(a, 91003, "3", text)
    a.fenster_pruefen(f, tmp_path, {91003})
    ticket_modell = str(aufpasser.config.lade(tmp_path)["modelle"]["ticket"])
    assert spur["probe"] == [ticket_modell]
    assert spur["stupser"] == [f.ziel]


def test_limit_oben_fertige_antwort_darunter_wird_angestossen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = bildschirm(LIMIT + "\n\n❯ weiter\n\n⏺ Test geschrieben, alle grün. Commit liegt.\n\n✻ Worked for 3m 12s")
    a, _, spur = _aufpasser(tmp_path, monkeypatch, {"91004": text}, offen=False)
    f = _fenster(a, 91004, "4", text)
    a.fenster_pruefen(f, tmp_path, {91004})
    assert spur["stupser"] == [f.ziel]
    assert spur["probe"] == []


def test_limit_oben_arbeit_laeuft_darunter_kein_anstoss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = bildschirm(LIMIT + "\n\n❯ weiter\n\n⏺ Lese die Datei\n✻ Thinking… (esc to interrupt)")
    a, _, spur = _aufpasser(tmp_path, monkeypatch, {"91005": text}, offen=True)
    f = _fenster(a, 91005, "5", text)
    a.fenster_pruefen(f, tmp_path, {91005})
    assert spur["stupser"] == [] and spur["probe"] == []
