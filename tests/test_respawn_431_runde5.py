"""#431 Prüfpanel Runde 5 — Befunde 2, 4, 5 und 6 (``respawn``).

2. Fensterliste beim Aufräumen unlesbar → ein halb gestartetes „bau N neu“ ist
   unbekannt; die Zeile muss es als evtl. offen melden (unklar = offen).
4. ``committet``: git-Fehler heißt „Commit-Stand nicht prüfbar“, nicht „nicht committet“.
5. Abbruch nach Beginn des Beendens: ``stand.hinweise`` gehören in die Zeile.
6. Unter der alten Pane läuft kein Claude → Exit 3 „alte Session fehlt“, nichts tippen.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_respawn_431 import (
    TICKET,
    FakeWerkzeug,
    _lauf,
    umgebung,  # noqa: F401  (Fixture)
)
from test_respawn_431_runde3 import _speicher_sperre
from test_respawn_431_runde4 import _sessions_vorher, _starten_wirft

from to_spawn import respawn
from to_spawn.tmux_aufruf import TmuxFehler

# --- Befund 2: Fensterliste beim Aufräumen unlesbar -----------------------------------


def test_r5_b2_unlesbare_fensterliste_meldet_neues_fenster_evtl_offen(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    _sessions_vorher(repo)
    fake = FakeWerkzeug(wt)
    _starten_wirft(fake, repo, fenster_anlegen=True)
    original_liste = fake.fenster_liste

    def fenster_liste() -> list[respawn.FensterInfo]:
        if any(a[0] == "fenster_starten" for a in fake.aufrufe):
            raise TmuxFehler("list-windows", "server exited unexpectedly")
        return original_liste()

    fake.fenster_liste = fenster_liste  # type: ignore[method-assign]

    erg = _lauf(repo, fake)

    assert erg.exit == 1, erg.zeile
    assert (
        f"Fenster „bau {TICKET} neu“ evtl. offen (Fensterliste unlesbar), Handarbeit nötig"
        in erg.zeile
    ), erg.zeile


# --- Befund 4: Commit-Stand nicht prüfbar ---------------------------------------------


def test_r5_b4_git_fehler_heisst_nicht_pruefbar(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)

    def committet(wt_: Path, pfade: list[Path]) -> bool:
        raise subprocess.CalledProcessError(128, ["git", "log"])

    fake.committet = committet  # type: ignore[method-assign]

    erg = _lauf(repo, fake)

    assert erg.exit == 0, erg.zeile
    assert "Commit-Stand nicht prüfbar (" in erg.zeile, erg.zeile
    assert "nicht committet" not in erg.zeile, erg.zeile


def test_r5_b4_tmux_werkzeug_reicht_git_fehler_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    werkzeug = respawn.TmuxWerkzeug()

    def git(wt: Path, *argumente: str) -> str:
        raise subprocess.CalledProcessError(128, ["git", *argumente])

    monkeypatch.setattr(werkzeug, "_git", git)
    with pytest.raises(subprocess.CalledProcessError):
        werkzeug.committet(tmp_path, [tmp_path / "HANDOFF.md"])


# --- Befund 5: Hinweise auch beim Abbruch nach Beenden-Beginn -------------------------


def test_r5_b5_abbruch_beim_beenden_behaelt_hinweise(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, fehler_bei={"alte_session_beenden": RuntimeError("kill gescheitert")}
    )
    _speicher_sperre(fake, 120)

    erg = _lauf(repo, fake)

    assert erg.exit == 1, erg.zeile
    assert "beim Beenden der alten Session" in erg.zeile, erg.zeile
    assert "wartete 120 s auf Speicher" in erg.zeile, erg.zeile


# --- Befund 6: kein Claude unter der alten Pane ---------------------------------------


def test_r5_b6_ohne_claude_unter_alter_pane_exit_3(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    fake.claude_da = False

    erg = _lauf(repo, fake)

    assert erg.exit == 3, erg.zeile
    assert "alte Session fehlt" in erg.zeile, erg.zeile
    assert fake.namen() == [], fake.aufrufe


def test_r5_b6_tmux_werkzeug_prueft_prozessbaum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from to_spawn import prozessbaum

    monkeypatch.setattr(prozessbaum, "baum", lambda pid: [pid, 200, 300])
    monkeypatch.setattr(prozessbaum, "ist_claude", lambda pid: pid == 300)
    assert respawn.TmuxWerkzeug().claude_laeuft(100) is True
    monkeypatch.setattr(prozessbaum, "ist_claude", lambda pid: False)
    assert respawn.TmuxWerkzeug().claude_laeuft(100) is False
