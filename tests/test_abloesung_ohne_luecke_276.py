"""#276: Bau-Session bleibt nach eigener Übergabe nie ohne Nachfolger stehen.

Befund (Probe 22./23.11.2026): Die Bau-Session schreibt an ihrer Grenze Handoff (+
Startprompt), committet, beendet ihren Loop — und verlässt sich auf Aufseher/Aufpasser.
Lief keiner, stand das Fenster still, obwohl das Ticket Rest-Arbeit hatte.

Soll: Der Staffel-Hook ihres eigenen Launchers (``bau.py``, läuft immer mit, Linux und
Windows) erkennt den committeten eigenen Handoff nach derselben Regel wie Leiter und
Aufpasser (``eigener_handoff``) und übergibt an die nächste Staffel-Runde — ohne Zeile
„Staffel: weiter“ und ohne Aufsicht.

Echt: git-Repo (Worktree), Leitstand + Sperre in ``tmp_path``, Regel ``eigener_handoff``,
Staffel-Datei. Attrappen nur für Außenwelt, die eigene Tests hat: GitHub-Zustand
(``ticket_offen``), Prozesssuche und Beenden (``test_staffel_windows_501``).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "skripte" / "hooks"))

import staffel_stop  # noqa: E402

from to_spawn import eigener_handoff, leitstand  # noqa: E402

TICKET = 92761
JETZT = time.time()
HANDOFF = f"docs/handoffs/HANDOFF_2026-11-23_{TICKET}.md"
START = f"docs/handoffs/START_2026-11-23_{TICKET}.txt"


def _git(ordner: Path, *args: str, zeit: float | None = None) -> None:
    env = dict(os.environ)
    if zeit is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(zeit)} +0000"
    subprocess.run(["git", "-C", str(ordner), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Ticket-Worktree mit Sessionstart vor 30 min, Umgebung wie von ``bau.py`` gesetzt."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / f"wt-{TICKET}"
    wt.mkdir()
    _git(wt, "init", "-q")
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "README.md").write_text("x\n", encoding="utf-8")
    _git(wt, "add", "README.md")
    _git(wt, "commit", "-q", "-m", "start", zeit=JETZT - 7200)
    staffel_datei = tmp_path / "out" / "staffel.json"
    for name, wert in {
        "BAU_TICKET": str(TICKET),
        "BAU_STAFFEL_DATEI": str(staffel_datei),
        "BAU_SESSION_START": str(JETZT - 1800),
        "BAU_HANDOFF_DIRS": os.pathsep.join([str(wt / "docs" / "handoffs"), str(wt / "docs")]),
        "BAU_WORKTREE": str(wt),
        "BAU_STAFFEL_RUNDE": "1",
        "BAU_STAFFEL_MAX": "8",
        "BAU_STAFFEL_FINGERABDRUCK": "",
    }.items():
        monkeypatch.setenv(name, wert)
    beendet: list[int] = []
    monkeypatch.setattr(staffel_stop, "ticket_offen", lambda ticket: True)
    monkeypatch.setattr(staffel_stop, "claude_vorfahr", lambda pid: 4242)
    monkeypatch.setattr(staffel_stop, "beenden", lambda pid: beendet.append(pid) or True)
    return {"wt": wt, "staffel": staffel_datei, "beendet": beendet}


def _uebergabe(wt: Path, *, committen: bool = True) -> None:
    """Handoff + Startprompt wie in der Probe: OHNE „Staffel: weiter“, vor 1 min committet."""
    (wt / "docs" / "handoffs").mkdir(parents=True, exist_ok=True)
    (wt / HANDOFF).write_text(
        "# Handoff\nRest: Ergebnis der Schlange, Klickweg auf Staging, schließen.\n",
        encoding="utf-8",
    )
    (wt / START).write_text(f"Weiter ab Handoff {HANDOFF}\n", encoding="utf-8")
    if committen:
        _git(wt, "add", HANDOFF, START)
        _git(wt, "commit", "-q", "-m", "docs: Handoff (#92761)", zeit=JETZT - 60)


def _hook(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    return staffel_stop.main()


def test_eigener_handoff_ohne_aufsicht_startet_folge_runde(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rot-Beweis #276: Übergabe geschrieben, Ticket offen, kein Aufseher → Folge-Runde."""
    _uebergabe(welt["wt"])
    assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [4242], "Session wurde nicht abgelöst — Fenster stünde still"
    daten = json.loads(welt["staffel"].read_text(encoding="utf-8"))
    assert Path(daten["handoff"]) == welt["wt"] / HANDOFF
    # Derselbe Handoff zählt für Leiter/Aufpasser nicht mehr — keine zweite Ablösung.
    assert leitstand.leiter_respawn(TICKET) is not None
    assert eigener_handoff.der_session(welt["wt"], TICKET, JETZT - 1800) is None


def test_ungecommitteter_handoff_loest_nicht_ab(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _uebergabe(welt["wt"], committen=False)
    assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [] and not welt["staffel"].exists()


def test_leiter_loest_selbst_ab_kein_doppel(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Leiter-Stufe 2 (Leiter hat den Handoff angefordert) → ihr respawn macht den Rest."""
    leitstand.setze_leiter_stufe(TICKET, 2, JETZT - 300)
    _uebergabe(welt["wt"])
    assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [] and not welt["staffel"].exists()


def test_belegte_leiter_sperre_kein_doppel(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _uebergabe(welt["wt"])
    halter = leitstand.versuche(f"leiter-{TICKET}", "test-aufpasser")
    assert halter is not None
    with halter:
        assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [] and not welt["staffel"].exists()
    # Handoff bleibt gültig: Leiter/Aufpasser lösen im nächsten Takt ab.
    assert eigener_handoff.der_session(welt["wt"], TICKET, JETZT - 1800) is not None


def test_staffel_grenze_laesst_handoff_fuer_aufsicht_liegen(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letzte Runde: kein Kill ohne Folge-Runde — sonst stünde das Ticket ohne Session."""
    monkeypatch.setenv("BAU_STAFFEL_RUNDE", "8")
    _uebergabe(welt["wt"])
    assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [] and not welt["staffel"].exists()
    assert leitstand.leiter_respawn(TICKET) is None


def test_handoff_der_vorrunde_zaehlt_nicht(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Folge-Runde startet nach dem Handoff-Commit: derselbe Handoff löst nie erneut ab."""
    _uebergabe(welt["wt"])
    monkeypatch.setenv("BAU_SESSION_START", str(JETZT - 30))
    assert _hook(monkeypatch) == 0
    assert welt["beendet"] == [] and not welt["staffel"].exists()


def test_vorlage_haelt_weckruf_bis_abloesung() -> None:
    """Text-Quelle: Übergabe beendet den Loop nicht mehr, Ablösung ab dem Ziel-Wert."""
    from to_spawn import anleitung

    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))[
        "prompt_template"
    ]
    assert "Schritt 3–5 macht der Aufseher" not in vorlage
    assert "ScheduleWakeup 600 s" in vorlage and "ab Ziel-Wert" in vorlage
    assert "Staffel-Hook von `bau.py`" in anleitung.ABLOESE_SOP
    assert "ScheduleWakeup 600 s" in anleitung.ABLOESE_SOP
