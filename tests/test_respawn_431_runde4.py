"""#431 Prüfpanel Runde 4 — Befunde 3 und 4 (``respawn``).

3. ``fenster_starten`` scheitert (z. B. Zeitüberschreitung), tmux hat das Fenster
   „bau N neu“ aber evtl. schon angelegt → Aufräumen muss es über die Fensterliste
   finden, schließen und die Sessions-Datei zurücklegen.
4. ``_alte_unruhe``: ein einzelner unlesbarer Bildschirm darf den Hinweis nicht
   dauerhaft auf „nicht lesbar“ stellen — es zählt der letzte Zustand.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_respawn_431 import (
    NEU_ZIEL,
    TICKET,
    FakeWerkzeug,
    _lauf,
    umgebung,  # noqa: F401  (Fixture)
)

from to_spawn import respawn, sessions_datei
from to_spawn.tmux_aufruf import TmuxFehler

# --- Befund 3: halb gestartetes Fenster ----------------------------------------------


def _starten_wirft(fake: FakeWerkzeug, repo: Path, *, fenster_anlegen: bool) -> None:
    """``fenster_starten`` legt (optional) Fenster + Sessions-Datei an und wirft dann."""
    original = fake.fenster_starten

    def fenster_starten(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        if fenster_anlegen:
            original(sitzung, name, cwd, befehl)
        datei = sessions_datei.pfad(repo, str(TICKET))
        datei.write_text('{"neu": true}', encoding="utf-8")
        raise TmuxFehler("new-window", "Zeitüberschreitung nach 30 s")

    fake.fenster_starten = fenster_starten  # type: ignore[method-assign]


def _sessions_vorher(repo: Path) -> Path:
    datei = sessions_datei.pfad(repo, str(TICKET))
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text('{"alt": true}', encoding="utf-8")
    return datei


def test_b3_halb_gestartetes_fenster_wird_geschlossen(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    datei = _sessions_vorher(repo)
    fake = FakeWerkzeug(wt)
    _starten_wirft(fake, repo, fenster_anlegen=True)

    erg = _lauf(repo, fake)

    assert erg.exit == 1, erg.zeile
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe, fake.aufrufe
    assert all(f.ziel != NEU_ZIEL for f in fake.fenster)
    assert "neues Fenster zu" in erg.zeile, erg.zeile
    assert datei.read_text(encoding="utf-8") == '{"alt": true}'
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)


def test_b3_sessions_datei_zurueck_auch_ohne_fenster(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    datei = _sessions_vorher(repo)
    fake = FakeWerkzeug(wt)
    _starten_wirft(fake, repo, fenster_anlegen=False)

    erg = _lauf(repo, fake)

    assert erg.exit == 1, erg.zeile
    assert datei.read_text(encoding="utf-8") == '{"alt": true}'
    assert "zwei Sessions offen" not in erg.zeile, erg.zeile


# --- Befund 4: Unruhe-Hinweis nach letztem Zustand ----------------------------------


class _Schirm:
    """Minimal-Werkzeug: simulierte Zeit, Bildschirm aus einer Folge (letzter bleibt)."""

    def __init__(self, folge: list[str | None]) -> None:
        self.zeit = 0.0
        self.folge = folge

    def jetzt(self) -> float:
        return self.zeit

    def schlafen(self, s: float) -> None:
        self.zeit += s

    def bildschirm(self, ziel: str) -> str | None:
        return self.folge.pop(0) if len(self.folge) > 1 else self.folge[0]


ARBEIT = f"✻ Denkt… ({respawn.ARBEIT_HINWEIS})"


def test_b4_einmal_unlesbar_dann_arbeit_heisst_arbeitete_noch() -> None:
    hinweis = respawn._alte_unruhe(_Schirm([None, ARBEIT]), "=spec:@1")  # type: ignore[arg-type]
    assert hinweis is not None
    assert "arbeitete nach" in hinweis, hinweis
    assert "nicht lesbar" not in hinweis, hinweis


def test_b4_zuletzt_unlesbar_heisst_nicht_lesbar() -> None:
    hinweis = respawn._alte_unruhe(_Schirm([ARBEIT, None]), "=spec:@1")  # type: ignore[arg-type]
    assert hinweis is not None
    assert "nicht lesbar" in hinweis, hinweis
