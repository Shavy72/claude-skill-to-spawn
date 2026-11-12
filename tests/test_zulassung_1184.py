"""#1184: Zulassung nach Speicher-Budget + verwaiste Bau-Sessions.

Aufzeichnungen ``hilfen/zulassung_1184/ps.txt`` und ``panes.txt`` stammen vom
Bau-Server (12.11.2026, ``ps -eo pid=,ppid=,etimes=,args=`` gekürzt auf 220 Zeichen,
``tmux list-panes -a -F …``). Die Sperre (``fcntl``) ist unter Windows eine
Attrappe — geprüft wird die Rechnung, nicht der Kernel.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from to_spawn import config, speicher, verwaist

HILFEN = Path(__file__).parent / "hilfen" / "zulassung_1184"
PS = (HILFEN / "ps.txt").read_text(encoding="utf-8")
PANES = (HILFEN / "panes.txt").read_text(encoding="utf-8")
GB = 1024


# --- Budget-Rechnung -----------------------------------------------------------


def test_budget_vorgabe_aus_config() -> None:
    assert speicher.budget(None) == config.DEFAULTS["zulassung"]
    assert (
        speicher.budget({"zulassung": {"bedarf_mib": 2048, "reserve_mib": -1}})[
            "bedarf_mib"
        ]
        == 2048
    )
    assert speicher.budget({"zulassung": {"reserve_mib": -1}})["reserve_mib"] == 4096


def test_budget_urteil_grenze_genau() -> None:
    # Reserve 4 GB + Bedarf 1,5 GB = 5,5 GB nötig
    assert speicher.budget_urteil(None, 5632, 0)[0] is True
    assert speicher.budget_urteil(None, 5631, 0)[0] is False
    # zwei anlaufende Sessions ziehen 3 GB ab
    ok, text = speicher.budget_urteil(None, 8 * GB, 2)
    assert ok is False
    assert "Speicher-Budget voll" in text and "2 anlaufende" in text


@pytest.fixture
def linux_mit_sperre(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    attrappe = types.SimpleNamespace(LOCK_EX=2, flock=lambda *_: None)
    monkeypatch.setitem(sys.modules, "fcntl", attrappe)
    stand = tmp_path / "zulassung.json"
    monkeypatch.setenv("TO_SPAWN_ZULASSUNG_STAND", str(stand))
    return stand


def test_zulassen_merkt_vor_bis_budget_voll(linux_mit_sperre: Path) -> None:
    # 10 GB frei: Starts 1–3 passen (10−0, 10−1,5, 10−3 ≥ 5,5), der vierte nicht (10−4,5).
    urteile = [
        speicher.zulassen(None, frei=10 * GB - 1, sessions=0, jetzt=1000.0 + i)[0]
        for i in range(4)
    ]
    assert urteile == [True, True, True, False]


def test_zulassen_vormerkung_verfaellt_nach_anlauf(linux_mit_sperre: Path) -> None:
    for i in range(3):
        speicher.zulassen(None, frei=10 * GB - 1, sessions=0, jetzt=1000.0 + i)
    assert (
        speicher.zulassen(None, frei=10 * GB - 1, sessions=0, jetzt=1100.0)[0] is False
    )
    assert (
        speicher.zulassen(None, frei=10 * GB - 1, sessions=0, jetzt=1000.0 + 181)[0]
        is True
    )


def test_zulassen_nur_fragen_merkt_nichts_vor(linux_mit_sperre: Path) -> None:
    for _ in range(5):
        assert speicher.zulassen(
            None, reservieren=False, frei=6 * GB, sessions=0, jetzt=1000.0
        )[0]
    assert not linux_mit_sperre.exists()


def test_zulassen_notbremse_bleibt(linux_mit_sperre: Path) -> None:
    ok, text = speicher.zulassen(
        {"speicher": {"max_sessions": 20}}, frei=60 * GB, sessions=20, jetzt=1.0
    )
    assert ok is False and "Obergrenze 20" in text


def test_zulassen_kaputte_standdatei_zaehlt_als_leer(linux_mit_sperre: Path) -> None:
    linux_mit_sperre.write_text("{kaputt", encoding="utf-8")
    assert speicher.zulassen(None, frei=6 * GB, sessions=0, jetzt=5.0)[0] is True


def test_warten_fragt_standardmaessig_die_zulassung() -> None:
    import inspect

    assert (
        inspect.signature(speicher.auf_platz_warten).parameters["pruefen"].default
        is speicher.zulassen
    )


# --- Verwaiste Sessions (echte Aufzeichnung) ----------------------------------


def test_aufzeichnung_alle_sessions_im_fenster() -> None:
    assert verwaist.verwaiste_sessions(PS, PANES, min_alter_s=0) == []


def _ohne_bau_py(ps_text: str, bau_pid: str, claude_pid: str) -> str:
    """bau.py-Zeile streichen, claude an PID 1 hängen — so sieht ein toter Starter aus."""
    zeilen = []
    for zeile in ps_text.splitlines():
        teile = zeile.split(None, 3)
        if teile and teile[0] == bau_pid:
            continue
        if teile and teile[0] == claude_pid:
            zeile = f"{teile[0]} 1 {teile[2]} {teile[3]}"
        zeilen.append(zeile)
    return "\n".join(zeilen)


def test_claude_ohne_bau_py_ist_verwaist() -> None:
    ps = _ohne_bau_py(PS, "190573", "190586")
    waisen = verwaist.verwaiste_sessions(ps, PANES, min_alter_s=0)
    assert [(w.pid, w.ticket) for w in waisen] == [(190586, 1171)]
    assert verwaist.verwaiste_sessions(ps, PANES) == []  # 277 s alt < 10 min Schonfrist


def test_hand_session_ohne_fenster_bleibt() -> None:
    # Remote-Control-Session (kein bau-Settings-Pfad) an PID 1: nie anfassen.
    ps = _ohne_bau_py(PS, "886", "891")
    assert verwaist.verwaiste_sessions(ps, PANES, min_alter_s=0) == []


def test_ohne_tmux_bild_keine_waisen() -> None:
    ps = _ohne_bau_py(PS, "190573", "190586")
    assert verwaist.verwaiste_sessions(ps, "", min_alter_s=0) == []


def test_protokoll_zeile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TO_SPAWN_GESCHLOSSEN_ORDNER", str(tmp_path))
    verwaist.protokollieren("verwaist", "claude 1 (#7)", "12 min alt", jetzt=0.0)
    datei = verwaist.protokoll_pfad(0.0)
    assert datei.name.startswith("geschlossen-") and datei.suffix == ".txt"
    assert datei.read_text(encoding="utf-8").rstrip("\n").split("\t")[1:] == [
        "verwaist",
        "claude 1 (#7)",
        "12 min alt",
    ]


# --- Bau-Slice (Übergabe bau-schutz, Abschnitt 1) ----------------------------------


def test_slice_praefix_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    praefix = speicher.slice_praefix(
        None, "bau-7", uid=1000, hat=lambda _: True, bus_da=lambda _: True, anlegen=lambda _: None
    )
    text = " ".join(praefix)
    assert "systemd-run --user --scope --quiet --slice=bau.slice --unit=bau-7-" in text
    assert (
        "XDG_RUNTIME_DIR=/run/user/1000" in text
        and "unix:path=/run/user/1000/bus" in text
    )
    assert praefix[-6:] == ["choom", "-n", "300", "--", "env", "TMPDIR=/var/tmp/bau"]


def test_slice_praefix_aus_ohne_bus_oder_leer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    assert (
        speicher.slice_praefix(
            None, "x", uid=1, hat=lambda _: True, bus_da=lambda _: False
        )
        == []
    )
    assert (
        speicher.slice_praefix(
            None, "x", uid=1, hat=lambda _: None, bus_da=lambda _: True
        )
        == []
    )
    assert (
        speicher.slice_praefix(
            {"bau_schutz": {"slice": ""}},
            "x",
            uid=1,
            hat=lambda _: True,
            bus_da=lambda _: True,
        )
        == []
    )


def _server_praefix(monkeypatch: pytest.MonkeyPatch, anlegen) -> list[str]:  # noqa: ANN001
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    return speicher.slice_praefix(None, "bau-7", uid=1000, hat=lambda _: True, bus_da=lambda _: True, anlegen=anlegen)


def test_slice_praefix_unit_name_je_start_eindeutig(monkeypatch: pytest.MonkeyPatch) -> None:
    """S12a: zwei Staffel-Runden im selben bau.py bekommen verschiedene Scope-Namen."""
    namen = [next(w for w in _server_praefix(monkeypatch, lambda _: None) if w.startswith("--unit=")) for _ in range(2)]
    assert namen[0] != namen[1]
    assert all(n.startswith("--unit=bau-7-") for n in namen)


def test_slice_praefix_legt_tmpdir_an_und_faellt_weich(monkeypatch: pytest.MonkeyPatch) -> None:
    """S12b: TMPDIR wird angelegt; scheitert das, fehlt nur der TMPDIR-Teil."""
    angelegt: list[Path] = []
    assert _server_praefix(monkeypatch, angelegt.append)[-2:] == ["env", "TMPDIR=/var/tmp/bau"]
    assert angelegt == [Path("/var/tmp/bau")]

    def kaputt(_p: Path) -> None:
        raise PermissionError("nein")

    praefix = _server_praefix(monkeypatch, kaputt)
    assert praefix[-4:] == ["choom", "-n", "300", "--"]
    assert not any(w.startswith("TMPDIR=") for w in praefix)


# --- Platten-Sperre räumt nur mit Freigabe (#1184, Vorfall Bau-Server-Testlauf) ----------


@pytest.fixture
def platte_knapp(linux_mit_sperre: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Platte knapp + Befehls-Protokoll an allen Ausführ-Stellen (Subprozess, Löschen)."""
    import os
    import shutil
    import subprocess

    protokoll: list[str] = []

    def aufzeichnen(name: str):  # noqa: ANN202
        def attrappe(*args: object, **_kw: object) -> None:
            protokoll.append(f"{name} {args[:1]}")
            raise AssertionError(f"externer Befehl ohne Freigabe: {name} {args[:1]}")

        return attrappe

    monkeypatch.setattr(subprocess, "run", aufzeichnen("run"))
    monkeypatch.setattr(subprocess, "Popen", aufzeichnen("Popen"))
    monkeypatch.setattr(shutil, "rmtree", aufzeichnen("rmtree"))
    monkeypatch.setattr(os, "remove", aufzeichnen("remove"))
    monkeypatch.setattr(os, "unlink", aufzeichnen("unlink"))
    monkeypatch.setattr(speicher, "platte_frei_gb", lambda pfad=None: 0.5)
    return protokoll


@pytest.mark.parametrize("freigabe", [None, "aus", "echt"])
def test_platte_knapp_ohne_freigabe_kein_externer_befehl(
    platte_knapp: list[str], monkeypatch: pytest.MonkeyPatch, freigabe: str | None
) -> None:
    # "echt" zählt unter pytest nie (PYTEST_CURRENT_TEST erben auch Subprozesse).
    if freigabe is None:
        monkeypatch.delenv("TO_SPAWN_AUFRAEUMEN", raising=False)
    else:
        monkeypatch.setenv("TO_SPAWN_AUFRAEUMEN", freigabe)
    ok, grund = speicher.zulassen(None, frei=40 * GB, sessions=0)
    assert not ok and grund.startswith("Platte voll")
    assert platte_knapp == []


def test_platte_knapp_mit_freigabe_raeumt_auf(platte_knapp: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import aufraeumen

    gerufen: list[bool] = []
    monkeypatch.setattr(aufraeumen, "taeglich", lambda **_kw: gerufen.append(True) or 0)
    monkeypatch.setenv("TO_SPAWN_AUFRAEUMEN", "echt")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    speicher.zulassen(None, frei=40 * GB, sessions=0)
    assert gerufen == [True]
    assert platte_knapp == []
