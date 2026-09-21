"""Fixrunde #236 — drei Nachfixes in ``to_spawn/aufpasser.py`` (N1–N3) als
Weg-Tests. Gleiche Welt wie ``test_aufpasser_236_fix.py`` (echtes tmux auf
eigenem Socket, echtes git, Temp-HOME); die Fixtures/Helfer werden von dort
importiert, nicht kopiert. Die echten Sitzungen des Bau-Servers (``spec-*``)
und ``~/.claude/sessions/`` werden nie berührt.
"""

# ruff: noqa: F811 — ``welt``/``_ohne_tmux_pane`` kommen als Fixtures aus den Erst-Tests.
from __future__ import annotations

import time
import uuid

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    fenster_namen,
    pane_text,
    welt,
)
from test_aufpasser_236_fix import (  # noqa: F401 — ``_ohne_tmux_pane`` ist ein Fixture
    BAU_CLAUDE,
    _bau_claude_vorlage,
    _fenster,
    _ohne_tmux_pane,
    _still_seit,
)

from to_spawn import aufpasser

# --- N1: Deploy-Wache fail-closed, wenn ``pgrep`` nicht nutzbar ist ---------


def test_n1_pgrep_nicht_nutzbar_lauf_bricht_fail_closed_ab(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """``pgrep`` liefert einen Fehlertext mit „No such file“ ⇒ ``deploy_laeuft``
    wirft ``RuntimeError``; ``lauf()`` fängt sie, loggt „Lauf abgebrochen,
    nichts angefasst“, schreibt ``stand.json`` trotzdem und gibt 1 zurück —
    ein Fenster, das sonst angestupst würde, bleibt unangetastet (fail-closed
    statt blind eingreifen)."""
    (welt.bin / "pgrep").write_text(
        "#!/bin/bash\necho 'pgrep: No such file or directory' >&2\nexit 1\n",
        encoding="utf-8",
    )
    (welt.bin / "pgrep").chmod(0o755)

    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    text_vorher = pane_text("bau 9002")
    stand_datei = welt.zustand / "stand.json"
    mtime_vorher = stand_datei.stat().st_mtime_ns

    assert welt.lauf() == 1
    assert "Lauf abgebrochen, nichts angefasst" in welt.log()
    assert pane_text("bau 9002") == text_vorher, "kein send-keys, Fenster unverändert"
    assert stand_datei.stat().st_mtime_ns != mtime_vorher, "stand.json trotzdem geschrieben"
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0, "kein Eingriff versucht"
    assert welt.kommentare() == [], "kein Kommentar, obwohl Fenster still stand"


# --- N2: Respawn-Fehler beim Fortsetzen → Stufe 3, kein zweiter Versuch -----


def test_n2_fortsetzen_fehlschlag_stufe_3_kein_zweiter_versuch(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schlägt ``_fenster_fortsetzen`` fehl — hier bewusst simuliert per
    Monkeypatch, die echte tmux-Seite steckt nicht im Test —, wird „Fortsetzen
    fehlgeschlagen (tmux: …) — braucht David“ gemeldet und die Stufe auf 3
    gesetzt. Ein zweiter Lauf versucht es nicht noch einmal: kein zweiter
    Aufruf, kein zweiter Kommentar."""
    rufe: list[int] = []

    def _kaputt(self: aufpasser.Aufpasser, *args: object, **kwargs: object) -> None:
        rufe.append(1)
        raise RuntimeError("tmux kaputt")

    monkeypatch.setattr(aufpasser.Aufpasser, "_fenster_fortsetzen", _kaputt)

    welt.worktree(9002, schmutzig=False)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)

    assert welt.lauf() == 0
    assert len(rufe) == 1
    assert welt.eintrag("bau 9002")["stufe"] == 3
    erwartet = "Fortsetzen fehlgeschlagen (tmux: tmux kaputt) — braucht David"
    treffer = [k for k in welt.kommentare() if erwartet in k]
    assert len(treffer) == 1, welt.kommentare()

    # Zweiter Lauf: Stufe 3 bleibt liegen, kein weiterer Versuch, keine zweite Meldung.
    assert welt.lauf() == 0
    assert len(rufe) == 1, "kein zweiter Aufruf von _fenster_fortsetzen"
    assert welt.eintrag("bau 9002")["stufe"] == 3
    treffer_2 = [k for k in welt.kommentare() if erwartet in k]
    assert len(treffer_2) == 1, "kein zweiter Kommentar"


# --- N3: NACHWEIS_S = 120 überlebt eine verzögerte Session-JSON -------------


def test_n3_nachweis_ueberlebt_verzoegerte_session_json(welt: Welt) -> None:
    """langsam: echter Nachweis-Weg (~30 s). Die Fake-Claude-Attrappe schreibt
    ihre ``sessions/<pid>.json`` erst nach 25 s
    (``FAKE_CLAUDE_JSON_VERZOEGERUNG_S``, additiv in ``fake_claude_schlaeft.sh``
    ergänzt) — mit ``NACHWEIS_S = 120`` muss ``_fortsetzen_nachweisen`` trotzdem
    grün ausgehen (Fenster bleibt, Stufe 2, „fortgesetzt“ gemeldet)."""
    assert aufpasser.NACHWEIS_S == 120
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    vorlage = _bau_claude_vorlage(
        welt,
        BAU_CLAUDE.replace(
            'exec "{claude}"',
            'FAKE_CLAUDE_JSON_VERZOEGERUNG_S=25 exec "{claude}"',
        ),
    )

    begonnen = time.monotonic()
    assert welt.lauf("--bau-vorlage", vorlage) == 0
    dauer = time.monotonic() - begonnen
    assert dauer >= 25, "Nachweis darf nicht vor der verzögerten JSON grün gehen"

    assert fenster_namen().count("bau 9002") == 1
    assert welt.eintrag("bau 9002")["stufe"] == 2
    assert any("fortgesetzt" in k and sid[:8] in k for k in welt.kommentare())
