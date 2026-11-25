"""#562/#263: Windows-Prozess mit unlesbarer Kommandozeile darf die Deploy-Wache nicht „frei“ melden.

Ein Deploy unter fremdem Konto oder mit erhöhten Rechten hat für uns keine lesbare
Kommandozeile, sein Programmname steht aber in der Kernel-Prozessliste. Passt der
Programmname auf das Muster ⇒ Treffer (fail-closed). Passt er nicht ⇒ weiter
übersprungen, sonst schlüge die Wache auf jedem normalen Windows-Rechner Dauer-Alarm.

Rechenlogik mit nachgestellter Prozessliste (die Kernel-Nähte liefern ``_PROZESSE``):
ein echter Prozess unter fremdem Konto lässt sich im Test nicht starten.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from to_spawn import prozessbaum
from to_spawn.aufpasser import Aufpasser, Einstellungen

#: PID → (Eltern-PID, Kommandozeile, Programmname); ``""`` = Kommandozeile unlesbar.
_PROZESSE: dict[int, tuple[int, str, str]] = {
    30: (4, "", "deploy_tool.exe"),  # Deploy unter fremdem Konto
    31: (4, "", "bash.exe"),  # fremder Prozess, Name passt nicht
    32: (4, "", ""),  # nicht einmal der Name ist bekannt
}


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Erzwingt den Windows-Zweig von ``passende`` mit der Liste ``_PROZESSE``."""
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "kein-proc")
    monkeypatch.setattr(
        prozessbaum, "_momentaufnahme_windows", lambda: {pid: (e, 0) for pid, (e, _, _) in _PROZESSE.items()}
    )
    monkeypatch.setattr(prozessbaum, "_kommandozeile_windows", lambda pid: _PROZESSE.get(pid, (0, "", ""))[1])
    monkeypatch.setattr(
        prozessbaum, "_programmnamen_windows", lambda: {pid: n for pid, (_, _, n) in _PROZESSE.items()}, raising=False
    )


def test_passender_name_ohne_kommandozeile_ist_treffer(windows: None) -> None:
    assert prozessbaum.passende(r"deploy_tool") == [(30, "deploy_tool.exe")]


def test_unpassender_name_ohne_kommandozeile_bleibt_unauffaellig(windows: None) -> None:
    assert prozessbaum.passende(r"safe_deploy_vps\.sh") == []


def test_wache_meldet_nicht_frei(windows: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    e = Einstellungen(
        zustand=tmp_path / "zustand", tmux_socket=f"deploywache562-{os.getpid()}", deploy_muster="deploy_tool"
    )
    wache = Aufpasser(e)
    monkeypatch.setattr(wache, "_prozess_alter_s", lambda _pid: None)  # Alter unbekannt = keine Waise
    assert wache.deploy_laeuft("deploy_tool") == ["deploy_tool.exe"]
    assert wache.deploy_laeuft(r"safe_deploy_vps\.sh") == []
