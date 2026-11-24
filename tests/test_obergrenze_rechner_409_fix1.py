"""#409 Fix-Runde 1: Rechner-Grenze, Fehlerfall der Sitzungs-Suche, Präfix-Namen, Aufpasser-Weg.

Die Sitzungs-Grenze (``max_sessions`` je ``spec-<S>``) allein ließe beliebig viele Specs
nebeneinander starten. Darum gilt zusätzlich eine harte Grenze für den ganzen Rechner:
1,5 × ``max_sessions`` je 16 GB Gesamtspeicher (``MemTotal``), gezählt über alle
Claude-Prozesse. Echt sind tmux (eigener Socket, aus dem #409-Test), ``speicher`` und der
Aufpasser; Attrappe ist nur ``/proc`` bzw. ``meminfo``.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #409-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_obergrenze_eigene_sitzung_409 import (  # noqa: F401 — ``welt`` ist ein Fixture
    _KONFIG,
    EIGEN,
    FREMD,
    SOCKET,
    Welt,
    _claude,
    _tmux,
    _vier_fremde_zwei_eigene,
    welt,
)

from to_spawn import aufpasser, speicher

pytestmark = pytest.mark.braucht("tmux")


def _sitzung_dazu(welt: Welt, name: str) -> None:
    _tmux("new-session", "-d", "-s", name, "sleep 600")
    for zeile in _tmux("list-sessions", "-F", "#{session_name}\t#{socket_path},#{pid},#{session_id}").splitlines():
        sitzung, wert = zeile.split("\t")
        welt.tmux_wert[sitzung] = wert.replace("$", "")


# --- 1. Rechner-Grenze --------------------------------------------------------


def test_rechner_grenze_zahlen() -> None:
    """16 GB und 6 → 9; 32 GB und 5 → 15; ohne ``MemTotal`` gilt 16 GB."""
    assert speicher.rechner_grenze(6, 16 * 1024 * 1024) == 9
    assert speicher.rechner_grenze(5, 32 * 1024 * 1024) == 15
    assert speicher.rechner_grenze(6, None) == 9
    assert speicher.rechner_grenze(6, 16_300_000) == 9  # echter 16-GB-Rechner meldet etwas weniger


def test_zwei_sitzungen_an_rechner_grenze_blockieren(welt: Welt) -> None:
    """2 eigene + 5 fremde = 7 = 1,5 × 5 bei 16 GB: kein Start, obwohl ``spec-409`` Platz hat."""
    for pid in (301, 302):
        _claude(welt, pid, EIGEN)
    for pid in range(201, 206):
        _claude(welt, pid, FREMD)
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=SOCKET)
    assert not frei, text
    assert text == "Rechner voll: schon 7 Claude-Sessions auf dem Rechner (Obergrenze 7)"


def test_rechner_grenze_waechst_mit_gesamtspeicher(welt: Welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gross = tmp_path / "meminfo-32"
    gross.write_text("MemTotal:       33554432 kB\nMemAvailable:   33554432 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(gross))
    for pid in (301, 302):
        _claude(welt, pid, EIGEN)
    for pid in range(201, 206):
        _claude(welt, pid, FREMD)
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=SOCKET)
    assert frei, text
    assert "Rechner 7 von 15" in text, text


# --- 2. Fehlerfall der Sitzungs-Suche ---------------------------------------


def test_tmux_fehlt_zaehlt_rechnerweit(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    _vier_fremde_zwei_eigene(welt)
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([f"/nicht/da/tmux-{os.getpid()}"]))
    assert speicher.sitzung_nach_name(EIGEN, SOCKET) is None
    assert speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=SOCKET) == (
        False,
        "schon 6 Claude-Sessions (Obergrenze 5)",
    )


def test_tmux_fehler_zaehlt_rechnerweit(welt: Welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kaputt = tmp_path / "tmux-kaputt"
    kaputt.write_text("#!/bin/sh\necho 'protocol version mismatch' >&2\nexit 1\n", encoding="utf-8")
    kaputt.chmod(0o755)
    monkeypatch.setenv("TO_SPAWN_TMUX", str(kaputt))
    _vier_fremde_zwei_eigene(welt)
    assert speicher.sitzung_nach_name(EIGEN, SOCKET) is None
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=SOCKET)
    assert not frei and text == "schon 6 Claude-Sessions (Obergrenze 5)", text


def test_kein_tmux_server_heisst_keine_sitzung(welt: Welt) -> None:
    """„no server running“ = Sitzung gibt es noch nicht, kein Fehler."""
    _vier_fremde_zwei_eigene(welt)
    assert speicher.sitzung_nach_name(EIGEN, f"nicht-da-{os.getpid()}") == speicher.KEINE_SITZUNG
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=f"nicht-da-{os.getpid()}")
    assert frei, text


# --- 4. Präfix-Namen, platz_frei_hier ohne TMUX, Aufpasser-Weg --------------


def test_praefix_name_zaehlt_nicht_mit(welt: Welt) -> None:
    """Prozesse in ``spec-13`` zählen nicht für ``spec-1``."""
    _sitzung_dazu(welt, "spec-1")
    _sitzung_dazu(welt, "spec-13")
    for pid in range(501, 506):
        _claude(welt, pid, "spec-13")
    _claude(welt, 601, "spec-1")
    frei, text = speicher.platz_frei(_KONFIG, sitzung="spec-1", tmux_socket=SOCKET)
    assert frei, text
    assert "1 Claude-Sessions in spec-1 " in text, text
    frei13, text13 = speicher.platz_frei(_KONFIG, sitzung="spec-13", tmux_socket=SOCKET)
    assert not frei13 and text13 == "schon 5 Claude-Sessions in spec-13 (Obergrenze 5)", text13


def test_platz_frei_hier_ohne_tmux_zaehlt_rechnerweit(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    _vier_fremde_zwei_eigene(welt)
    monkeypatch.delenv("TMUX", raising=False)
    assert speicher.platz_frei_hier(_KONFIG) == (False, "schon 6 Claude-Sessions (Obergrenze 5)")


def _aufpasser(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> tuple[aufpasser.Aufpasser, Path]:
    # Blankes tmux: der Socket kommt nur über ``tmux_socket`` des Aufpassers an.
    monkeypatch.delenv("TO_SPAWN_TMUX")
    repo = welt.tmp / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(_KONFIG), encoding="utf-8")
    e = aufpasser.Einstellungen(zustand=welt.tmp / "zustand", tmux_socket=SOCKET, deploy_muster="niemals-409")
    return aufpasser.Aufpasser(e), repo


_IDLE = aufpasser.SessionInfo(session_id="s409", cwd=None, status="idle", pid=1)


def test_aufpasser_platznot_zaehlt_nur_eigene_sitzung(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """2 eigene + 4 fremde, Obergrenze 5: keine Platznot (rechnerweit wären es 6)."""
    _vier_fremde_zwei_eigene(welt)
    a, repo = _aufpasser(welt, monkeypatch)
    assert a._platznot(repo, _IDLE, EIGEN) is False


def test_aufpasser_platznot_bei_voller_eigener_sitzung(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    """5 eigene + 1 fremde: Platznot — eine falsch gesuchte Sitzung zählte 0."""
    for pid in range(301, 306):
        _claude(welt, pid, EIGEN)
    _claude(welt, 201, FREMD)
    a, repo = _aufpasser(welt, monkeypatch)
    assert a._platznot(repo, _IDLE, EIGEN) is True
