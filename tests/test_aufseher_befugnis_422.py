"""#422: Jeder Nachfolge-Aufseher bekommt Befugnis + Mindset aus der einen Quelle.

Ein Start-Prompt des Vorgängers wie „Auftrag wie bisher“ verweist auf einen Text, den
ein Nachfolger nicht hat. Geprüft wird der Text, mit dem die neue Aufseher-Session
startet — nach der Ablösung über die respawn-Tür, beim frischen Neustart durch den
Aufpasser und beim Fortsetzen eines Gesprächs (``--resume``).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import anleitung, befugnis, respawn_aufseher, waechter_lauf

SKILL = Path(__file__).resolve().parent.parent
START_WIE_BISHER = (
    "Aufseher Spec #1 übernehmen. Handoff: docs/HANDOFF_2026-11-24_waechter_1.md. "
    "Weiter mit /loop Bau-Aufseher Spec #1 (Auftrag wie bisher), nächster Tick sofort.\n"
)


def _wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "wache_422", SKILL / "skripte" / "wache.py"
    )
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _traegt_befugnis(text: str) -> None:
    assert befugnis.KOPF in text, "Befugnis-Kopf fehlt"
    assert anleitung.MINDSET in text, "Mindset fehlt"
    # Briefkasten-Teil des Aufsehers (``befugnis.aufseher_text``, Anfang ohne Befehlspfad).
    assert befugnis.aufseher_text("X").split("`X")[0] in text, (
        "Befugnis-Abschnitt fehlt"
    )


class _Werkzeug:
    """Test-Werkzeug der Aufseher-Tür: der Vorgänger schreibt nur „Auftrag wie bisher“."""

    def __init__(self, wurzel: Path) -> None:
        self.wurzel = wurzel
        self.uhr = time.time()

    def tippen(self, ziel: str, text: str) -> None:
        treffer = re.search(
            r"Handoff nach (\S+) und den Start-Prompt .*? nach (\S+) ", text
        )
        if treffer:
            for rel, inhalt in (
                (treffer[1], "Handoff\n"),
                (treffer[2], START_WIE_BISHER),
            ):
                pfad = self.wurzel / rel
                pfad.parent.mkdir(parents=True, exist_ok=True)
                pfad.write_text(inhalt, encoding="utf-8")

    def jetzt(self) -> float:
        return self.uhr

    def schlafen(self, s: float) -> None:
        self.uhr += s


def test_abloesung_auftrag_wie_bisher_traegt_befugnis(tmp_path: Path) -> None:
    """respawn-Tür → Ablöse-Datei → ``wache.abloese_prompt``: Startkontext des Nachfolgers."""
    erg = respawn_aufseher.aufseher_abloesen(
        tmp_path, 1, "%5", werkzeug=_Werkzeug(tmp_path), warte_max=60
    )
    assert erg.exit == 0 and erg.handoff is not None, erg.zeile
    wache = _wache()
    auftrag = wache.prompt_bauen(1, "x/y", 600, {})
    start = wache.abloese_prompt(auftrag, erg.handoff, 2, None, erg.start_prompt)
    assert "Auftrag wie bisher" in start
    _traegt_befugnis(start)


def test_aufpasser_frisch_traegt_befugnis() -> None:
    """Aufpasser startet ``wache.py`` ohne ``--resume`` → voller Auftrag aus ``prompt_bauen``."""
    _traegt_befugnis(_wache().prompt_bauen(1, "x/y", 600, {}))


class _Gestartet(Exception):
    pass


def test_aufpasser_fortgesetzt_traegt_befugnis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aufpasser setzt das Aufseher-Gespräch fort (``fahre(session_id=…)``) → Weiter-Text trägt Befugnis."""
    monkeypatch.setenv("HOME", str(tmp_path / "heim"))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    sid = "11111111-2222-3333-4444-555555555555"
    ordner = waechter_lauf.transkript_ordner(cwd)
    ordner.mkdir(parents=True)
    (ordner / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
    befehle: list[list[str]] = []

    def starte(cmd: list[str], _cwd: Path) -> None:
        befehle.append(cmd)
        raise _Gestartet

    monkeypatch.setattr(waechter_lauf, "_starte", starte)
    with pytest.raises(_Gestartet):
        waechter_lauf.fahre(
            claude="claude",
            spec=1,
            prompt="",
            modell="claude-opus-5",
            ausweich="",
            remote_control=False,
            repo=cwd,
            cwd=cwd,
            session_id=sid,
        )
    assert len(befehle) == 1 and "--resume" in befehle[0], befehle
    _traegt_befugnis(befehle[0][-1])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
