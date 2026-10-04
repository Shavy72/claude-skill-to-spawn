"""#431 Befund 0: ``respawn``-Sessions (nacktes ``env BAU_TICKET=N claude``) zählen als „läuft“."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKRIPT = Path(__file__).resolve().parent.parent / "skripte" / "sessions_stand.py"


@pytest.fixture
def modul() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sessions_stand_431", SKRIPT)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    try:
        spec.loader.exec_module(m)
        m.fremdes_repo = lambda pid: False
        yield m
    finally:
        sys.modules.pop(spec.name, None)


def _env(tabelle: dict[int, str]):
    return lambda pid: tabelle.get(pid)


def test_respawn_session_ohne_bau_py_laeuft(modul: ModuleType) -> None:
    P = modul.Prozess
    alle = [P(50, 1, "claude", "claude --model x --effort medium", None)]
    modul.ticket_aus_environ = _env({50: "431"})
    eintraege = {"431": modul.Eintrag("431", "ticket", "t")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["431"].session_pid == 50
    assert eintraege["431"].zustand.startswith("läuft")
    assert "VERWAIST" not in eintraege["431"].zustand


def test_bau_py_kind_mit_bau_ticket_wird_nicht_doppelt_gezaehlt(
    modul: ModuleType,
) -> None:
    P = modul.Prozess
    alle = [
        P(10, 1, "python", "python skripte/bau.py 431", None),
        P(
            11,
            10,
            "claude",
            "claude --settings x-bau/431-20260919-000000/settings.json",
            None,
        ),
        P(12, 11, "node", "node mcp.js", None),
    ]
    modul.ticket_aus_environ = _env({11: "431", 12: "431"})
    eintraege = {"431": modul.Eintrag("431", "ticket", "t")}
    modul.zuordnen(eintraege, alle)
    assert eintraege["431"].pid == 10
    assert eintraege["431"].session_pid == 11
    assert "VERWAIST" not in eintraege["431"].zustand


def test_kindprozess_der_respawn_session_zaehlt_nicht(modul: ModuleType) -> None:
    P = modul.Prozess
    alle = [P(50, 1, "claude", "claude", None), P(51, 50, "node", "node mcp.js", None)]
    modul.ticket_aus_environ = _env({50: "431", 51: "431"})
    eintraege: dict = {}
    modul.zuordnen(eintraege, alle)
    assert eintraege["431"].session_pid == 50


def test_environ_leser_ohne_rechte_ist_still(
    modul: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def kaputt(self: Path, *a: object, **k: object) -> bytes:
        raise PermissionError("nein")

    monkeypatch.setattr(Path, "read_bytes", kaputt)
    assert modul.ticket_aus_environ(1) is None


def test_environ_leser_findet_variable(
    modul: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"A=1\0BAU_TICKET=431\0B=2\0")
    assert modul.ticket_aus_environ(1) == "431"


def test_enkel_mit_gleichem_ticket_steht_vorn_pid_ist_claude(
    modul: ModuleType,
) -> None:
    """Enkel (claude -> bash -> node) erben BAU_TICKET; die Reihenfolge darf nichts ändern (Befund 10)."""
    P = modul.Prozess
    alle = [
        P(52, 51, "node", "node mcp.js", None),
        P(51, 50, "bash", "bash -c run", None),
        P(50, 1, "claude", "claude --model x", None),
    ]
    modul.ticket_aus_environ = _env({50: "431", 51: "431", 52: "431"})
    eintraege: dict = {}
    modul.zuordnen(eintraege, alle)
    assert list(eintraege) == ["431"]
    assert eintraege["431"].session_pid == 50
