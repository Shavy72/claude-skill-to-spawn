"""#381: Bau-Session wartet nach grüner Schlange „auf eine neue Session“, die nie kommt.

Beobachtet: Stand lag auf dem Hauptzweig, Rest-Schritte (Klickweg, Beleg) offen — die Session
hielt das Ticket für übergeben und stand ~15 min still, bis der Aufseher sie anstieß.

- Start-Text (eine Quelle: ``repo-scripts/_default.json``) sagt: dieselbe Session macht weiter.
- Steh-Erkennung: „wartet auf Nachfolger/neue Session“ am Prompt nutzt die kurze Schwelle des
  Anstupsers (#485). Echte Ablösung (eigener Handoff genannt) wird nicht angestupst.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from test_aufpasser_236 import SITZUNG, SOCKET, Welt, pane_text, tmux, welt  # noqa: F401 — Fixture

from to_spawn import aufpasser, eigener_handoff

SKILL = Path(__file__).resolve().parent.parent
FELD = "─" * 30 + "\n❯ \n" + "─" * 30 + "\n  Statuszeile\n"
SATZ = "„auf eine neue Session warten“ gibt es nicht"

WARTET = [
    "Schlange grün, Stand liegt auf origin/main.\nIch warte jetzt auf die neue Session, "
    "die Klickweg und Beleg übernimmt.",
    "Ticket übergeben. Wartet auf Nachfolger.",
    "Code ist auf dem Hauptzweig. Warte auf die Folgesession für den Klickweg.",
    "Alles eingereicht — warte auf Übergabe an die nächste Session.",
]
ARBEITET = [
    "Klickweg läuft, Beleg wird geschrieben.",
    "Einreichen fertig (Exit 0). Jetzt Beleg-Konto auf Staging anlegen.",
    "WARTET: #5 offen",
]
ECHTE_ABLOESUNG = (
    "Handoff docs/handoffs/HANDOFF_2026-11-24_7.md und START_2026-11-24_7.txt committet.\n"
    "Ablösung ausstehend — ScheduleWakeup 600 s, warte auf die Folgesession."
)


@pytest.mark.parametrize("text", WARTET)
def test_wartet_auf_nachfolger_erkannt(text: str) -> None:
    assert aufpasser.wartet_auf_nachfolger(text + "\n\n" + FELD), text


#: Zulässiges Warten (Fix-Runde 1): Ablösung, Mensch, Prüfer, andere Session.
ZULAESSIG = [
    "Handoff committet. Ich warte auf die Ablösung durch den Launcher.",
    "Ich warte auf die Übergabe des Menschen.",
    "Ich warte auf die andere Session (Review).",
    "Ablösung ausstehend, nichts tun.",
    "Wartet auf den Prüfer.",
    "Wartet auf den Menschen.",
]


@pytest.mark.parametrize("text", [*ARBEITET, *ZULAESSIG])
def test_arbeit_und_zulaessiges_warten_nicht_erkannt(text: str) -> None:
    assert not aufpasser.wartet_auf_nachfolger(text + "\n\n" + FELD), text


def _aufpasser(tmp_path: Path) -> aufpasser.Aufpasser:
    return aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "stand.json", trocken=True))


def test_wartet_umsonst_ohne_handoff_im_repo(tmp_path: Path) -> None:
    """Kein eigener Handoff im Repo (echte Abfrage) ⇒ Warten ist umsonst ⇒ Anstoß-Fall."""
    a = _aufpasser(tmp_path)
    assert a.wartet_umsonst(WARTET[1] + "\n\n" + FELD, 7, tmp_path)
    assert not a.wartet_umsonst(ZULAESSIG[0] + "\n\n" + FELD, 7, tmp_path)
    assert not a.wartet_umsonst(WARTET[1] + "\n\n" + FELD, None, tmp_path)


def test_echte_abloesung_am_repo_erkannt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener Handoff liegt (Regel aus ``eigener_handoff``) ⇒ kein Anstoß, auch ohne Dateinamen im Text."""
    gesehen: list[int] = []

    def aktuell(repo: Path, ticket: int) -> eigener_handoff.Treffer:
        gesehen.append(ticket)
        return eigener_handoff.Treffer("docs/handoffs/HANDOFF_2026-11-24_7.md", time.time())

    monkeypatch.setattr(eigener_handoff, "aktuell", aktuell)
    assert not _aufpasser(tmp_path).wartet_umsonst(ECHTE_ABLOESUNG + "\n\n" + FELD, 7, tmp_path)
    assert gesehen == [7]


def test_weit_oben_im_verlauf_zaehlt_nicht() -> None:
    assert not aufpasser.wartet_auf_nachfolger(WARTET[1] + "\n" + "x\n" * 10 + FELD)


def test_start_text_eine_quelle_hat_den_satz() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    text = vorlage["prompt_template"]
    assert text.count(SATZ) == 1
    assert "macht dieselbe Session sofort die Rest-Schritte" in text


# --- Weg-Test: echtes tmux, Aufpasser-Lauf wie #485 ------------------------------


@pytest.fixture(autouse=True)
def _ohne_tmux_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_TMUX_SOCKET", SOCKET)


def _fenster(welt: Welt, name: str, zeile: str) -> None:
    tmux("new-window", "-d", "-t", f"={SITZUNG}", "-n", name, "-c", str(welt.repo), f"printf '{zeile}\\n'; sleep 3600")
    time.sleep(0.5)


def _still_seit(welt: Welt, name: str, minuten: float) -> None:
    welt.stand_setzen(
        name,
        hash=hashlib.sha1(pane_text(name).encode()).hexdigest(),
        seit=time.time() - minuten * 60,
        stufe=0,
    )


@pytest.mark.braucht("tmux")
def test_wartende_session_nach_gruener_schlange_angestupst(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "Schlange grün. Warte auf die neue Session für Klickweg und Beleg.")
    _still_seit(welt, "bau 9002", 6)
    assert welt.lauf() == 0
    time.sleep(1)
    assert "Aufpasser: Du stehst seit 6 min still" in pane_text("bau 9002")
    assert welt.eintrag("bau 9002")["stufe"] == 1


@pytest.mark.braucht("tmux")
def test_echte_abloesung_nicht_angestupst(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    treffer = eigener_handoff.Treffer("docs/handoffs/HANDOFF_2026-11-24_9002.md", time.time())
    monkeypatch.setattr(eigener_handoff, "aktuell", lambda repo, ticket: treffer)
    _fenster(welt, "bau 9002", "Ablösung ausstehend, warte auf die Folgesession.")
    _still_seit(welt, "bau 9002", 6)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "Aufpasser: Du stehst" not in pane_text("bau 9002")
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0
