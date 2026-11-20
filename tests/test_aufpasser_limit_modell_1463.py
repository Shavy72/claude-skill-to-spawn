"""Aufpasser #1463 (Folge #1453): Limit-Probe je Modell des Fensters, Limit-Zeile über
fertiger Arbeit.

1. Die Probe fragt das Modell, mit dem das Fenster zuletzt geantwortet hat (Transkript der
   Session), nicht pauschal ``modelle.ticket``.
2. Steht die Limit-Zeile oben und darunter eine fertige Antwort, ist die Limit-Zeile alt:
   das Fenster ist still und wird wie jedes andere angestoßen (B3 aus review_1453.md).

Probe und tmux sind Attrappen: kein echter Claude-Aufruf, kein echtes tmux.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser
from to_spawn.waechter_lauf import transkript_ordner

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
    return "\n".join(
        [
            ausgabe,
            "",
            LINIE,
            "❯ ",
            LINIE,
            "  Opus 5.5 (1M context) · 91.7k (9.0%) · 2h06m",
            "",
        ]
    )


def transkript(heim: Path, cwd: Path, sid: str, modell: str) -> None:
    """Echte Antwort mit ``modell``, danach die künstliche Limit-Zeile von Claude Code."""
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
    texte: dict[str, str],
    offen: dict[str, bool] | bool,
    sessions: dict[str, aufpasser.SessionInfo | None] | None = None,
) -> tuple[aufpasser.Aufpasser, dict[str, list]]:
    spur: dict[str, list] = {"probe": [], "stupser": []}

    def probe(claude: str, modell: str, cwd: Path) -> bool:
        spur["probe"].append(modell)
        return offen if isinstance(offen, bool) else offen[modell]

    monkeypatch.setattr(aufpasser, "limit_offen", probe, raising=False)
    monkeypatch.setattr(aufpasser, "bau_lauf_im_baum", lambda pid: None)
    monkeypatch.setattr(aufpasser, "worktree_finden", lambda *a, **k: None)
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp / "stand.json",
            tmux_socket="nie-benutzt",
            jetzt=lambda: JETZT,
            heim=tmp / "heim",
        )
    )
    a.pane_text = lambda ziel: texte[ziel.split(":", 1)[0].removeprefix("=spec-")]  # type: ignore[method-assign]
    a.session = lambda f: (sessions or {}).get(f.name) or aufpasser.SessionInfo("ohne-transkript", None, "idle", 4242)  # type: ignore[method-assign]
    a.deploy_laeuft = lambda muster=None: []  # type: ignore[method-assign]
    a.gate_im_fenster = lambda f, wt: None  # type: ignore[method-assign]
    a.folge_ab_handoff = lambda *a_, **k: False  # type: ignore[method-assign]
    a.melden = lambda *a_, **k: None  # type: ignore[method-assign]
    a.anstupsen = lambda ziel, still_min: spur["stupser"].append(ziel)  # type: ignore[method-assign]
    a.sichern_und_fortsetzen = lambda *a_, **k: None  # type: ignore[method-assign]
    return a, spur


def _fenster(a: aufpasser.Aufpasser, ticket: int, pane: str, text: str) -> aufpasser.Fenster:
    f = aufpasser.Fenster(f"spec-{ticket}", "1365", pane, f"bau {ticket}", "/x", 4200 + int(pane))
    a.stand.fenster[f"{f.sitzung}/{f.name}"] = {
        "hash": aufpasser.pane_hash(text),
        "seit": JETZT - 2 * 3600,
        "stufe": 0,
    }
    return f


def test_zwei_fenster_probe_je_modell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opus-Fenster und Sonnet-Fenster in Limit-Pause; nur Sonnet ist wieder offen."""
    heim = tmp_path / "heim"
    cwd_opus, cwd_sonnet = tmp_path / "wt-opus", tmp_path / "wt-sonnet"
    transkript(heim, cwd_opus, "sid-opus", OPUS)
    transkript(heim, cwd_sonnet, "sid-sonnet", SONNET)
    text = bildschirm(LIMIT)
    sessions = {
        "bau 91001": aufpasser.SessionInfo("sid-opus", cwd_opus, "idle", 4201),
        "bau 91002": aufpasser.SessionInfo("sid-sonnet", cwd_sonnet, "idle", 4202),
    }
    a, spur = _aufpasser(
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
    """Keine Session/kein Transkript: wie #1453 — Modell aus ``modelle.ticket``."""
    text = bildschirm(LIMIT)
    a, spur = _aufpasser(tmp_path, monkeypatch, {"91003": text}, offen=True)
    f = _fenster(a, 91003, "3", text)
    a.fenster_pruefen(f, tmp_path, {91003})
    ticket_modell = str(aufpasser.config.lade(tmp_path)["modelle"]["ticket"])
    assert spur["probe"] == [ticket_modell]
    assert spur["stupser"] == [f.ziel]


def test_limit_oben_fertige_antwort_darunter_wird_angestossen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B3: Limit-Zeile oben, darunter fertige Antwort, seit 2 h still → Anstoß, keine Probe."""
    text = bildschirm(LIMIT + "\n\n❯ weiter\n\n⏺ Test geschrieben, alle grün. Commit liegt.\n\n✻ Worked for 3m 12s")
    a, spur = _aufpasser(tmp_path, monkeypatch, {"91004": text}, offen=False)
    f = _fenster(a, 91004, "4", text)
    a.fenster_pruefen(f, tmp_path, {91004})
    assert spur["stupser"] == [f.ziel]
    assert spur["probe"] == []


def test_limit_oben_arbeit_laeuft_darunter_kein_anstoss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gegenprobe zu B3: läuft die Arbeit unter der alten Limit-Zeile, bleibt das Fenster in Ruhe."""
    text = bildschirm(LIMIT + "\n\n❯ weiter\n\n⏺ Lese die Datei\n✻ Thinking… (esc to interrupt)")
    a, spur = _aufpasser(tmp_path, monkeypatch, {"91005": text}, offen=True)
    f = _fenster(a, 91005, "5", text)
    a.fenster_pruefen(f, tmp_path, {91005})
    assert spur["stupser"] == [] and spur["probe"] == []
