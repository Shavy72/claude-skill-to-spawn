"""#887 (aus App #1184): Wiederaufnahme nach Neustart, Pause-Guard, cgroup-Slices & Nest-Schutz.

Deckt alle verbleibenden Bausteine von #1184 auf dem Stand von ``origin/main`` ab:
1. ``to_spawn.wiederaufnahme``: findet offene Specs unter ``heim`` (Klon mit ``.to-spawn/sessions/``
   und nicht geschlossenem Spec-Issue), wiederholt bei unlesbarem GitHub, achtet auf
   ``auswahl`` und ``tmux_aufruf.sitzung_aufloesen``, startet je offener Spec einmal den Aufseher.
2. Pause-Guard (``bau:pause`` / ``wontfix``): ``ticket_zustand.sperre_grund`` + ``startstelle``
   verhindern Start und Fortsetzung — auch wenn das Label während des Speicher-Wartens gesetzt wird.
3. cgroup-Slice-Präfix (``speicher.slice_praefix`` / ``gate_slice_praefix``) + einmaliger
   Rückfall in ``bau.main`` bei sofortigem Slice-Fehlstart.
4. Nest-Einheiten (``bau-wiederaufnahme.service``, ``bau-sessions.slice``, ``bau-gate.slice``),
   ``nest/nest_server.sh`` (Swap 4 GiB, cgroup-Delegation, ``systemd-oomd``, SSH-OOM-Schutz,
   Wiederaufnahme-Unit) und Abnahme-/Swap-Doku (``docs/BAU_SERVER_SPEICHERSCHUTZ.md``).
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from test_bau_ohne_prompt_431 import bau  # noqa: F401 — Fixture

SKILL = Path(__file__).resolve().parent.parent
NEST = SKILL / "nest"
DOCS = SKILL / "docs"

from to_spawn import auswahl, config, speicher, startstelle, ticket_zustand, tmux_aufruf, wiederaufnahme

# -- 1. Wiederaufnahme nach Neustart -------------------------------------------


def test_offene_specs_aus_klonen(tmp_path: Path) -> None:
    for name in ("projekt-1109", "projekt-784", "projekt-900", "projekt-ohne"):
        (tmp_path / name / ".to-spawn" / "sessions").mkdir(parents=True)
    (tmp_path / "projekt-555").mkdir()  # kein sessions-Ordner → kein Spec-Klon
    (tmp_path / "datei-77").write_text("x", encoding="utf-8")
    offen: dict[str, bool | None] = {"1109": True, "784": False, "900": None, "555": True}
    gefragt: list[str] = []

    def ist_offen(spec: str, _klon: Path) -> bool | None:
        gefragt.append(spec)
        return offen[spec]

    treffer = wiederaufnahme.offene_specs(tmp_path, ist_offen)
    assert treffer == [("1109", tmp_path / "projekt-1109")]
    assert sorted(gefragt) == ["1109", "784", "900"]


def test_wiederaufnahme_startet_je_spec_einmal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    klon1 = tmp_path / "app-1"
    klon2 = tmp_path / "app-2"
    klon1.mkdir()
    klon2.mkdir()
    monkeypatch.setattr(wiederaufnahme, "offene_specs", lambda _h, **_k: [("1", klon1), ("2", klon2)])
    monkeypatch.setattr(tmux_aufruf, "sitzung_aufloesen", lambda _k, s: (f"spec-app-{s}", None))
    monkeypatch.setattr(wiederaufnahme, "sitzung_da", lambda name: name == "spec-app-2")
    gestartet: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        wiederaufnahme,
        "aufseher_starten",
        lambda spec, _k, *, sitzung=None, trocken=False: gestartet.append((spec, sitzung)) or True,
    )
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True) == 0
    assert gestartet == [("1", "spec-app-1")]


def test_wiederaufnahme_respektiert_auswahl_ohne_wache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    klon = tmp_path / "app-42"
    (klon / ".to-spawn" / "sessions").mkdir(parents=True)
    auswahl.pfad(klon, 42).write_text(
        json.dumps({"spec": 42, "tickets": [101], "ohne_wache": True, "rechner": auswahl.aktueller_rechner()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(wiederaufnahme, "offene_specs", lambda _h, **_k: [("42", klon)])
    monkeypatch.setattr(tmux_aufruf, "sitzung_aufloesen", lambda _k, s: (f"spec-app-{s}", None))
    monkeypatch.setattr(wiederaufnahme, "sitzung_da", lambda _n: False)
    gestartet: list[str] = []
    monkeypatch.setattr(
        wiederaufnahme,
        "aufseher_starten",
        lambda spec, _k, **_kw: gestartet.append(spec) or True,
    )
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True) == 0
    assert gestartet == []


def test_wiederaufnahme_wartet_auf_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub erst unlesbar, dann offen → nach Wiederholung aufgenommen, Exit 0."""
    (tmp_path / "projekt-1109" / ".to-spawn" / "sessions").mkdir(parents=True)
    antworten = iter([None, None, {"state": "OPEN", "labels": []}])
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *_a, **_k: next(antworten))
    monkeypatch.setattr(tmux_aufruf, "sitzung_aufloesen", lambda _k, s: (f"spec-projekt-{s}", None))
    monkeypatch.setattr(wiederaufnahme, "sitzung_da", lambda _s: False)
    gestartet: list[str] = []
    monkeypatch.setattr(
        wiederaufnahme,
        "aufseher_starten",
        lambda spec, _k, **_kw: gestartet.append(spec) or True,
    )
    geschlafen: list[float] = []
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True, takt_s=7, schlafen=geschlafen.append) == 0
    assert gestartet == ["1109"]
    assert geschlafen == [7, 7]


def test_wiederaufnahme_github_dauerhaft_unlesbar_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub bleibt unlesbar → nach allen Versuchen Exit ≠ 0, kein Start."""
    (tmp_path / "projekt-1109" / ".to-spawn" / "sessions").mkdir(parents=True)
    gefragt: list[str] = []

    def unlesbar(spec: int | str, **_k: object) -> None:
        gefragt.append(str(spec))

    monkeypatch.setattr(ticket_zustand, "lesen", unlesbar)
    gestartet: list[str] = []
    monkeypatch.setattr(
        wiederaufnahme,
        "aufseher_starten",
        lambda spec, _k, **_kw: gestartet.append(spec) or True,
    )
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True, versuche=3, takt_s=0, schlafen=lambda _s: None) != 0
    assert gefragt == ["1109"] * 3
    assert gestartet == []


def test_aufseher_starten_fremde_sitzung_verweigert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    klon = tmp_path / "app-55"
    klon.mkdir()
    monkeypatch.setattr(tmux_aufruf, "sitzung_aufloesen", lambda _k, _s: ("spec-app-55", "WEIGERUNG: fremdes Repo"))
    assert wiederaufnahme.aufseher_starten("55", klon, trocken=False) is False


def test_tmux_dienst_abwarten_ausserhalb_linux_sofort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiederaufnahme.sys, "platform", "win32")
    gerufen: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: gerufen.append(a))
    wiederaufnahme.tmux_dienst_abwarten(warten_s=10, takt_s=1)
    assert gerufen == []


# -- 2. Pause-Guard (bau:pause / wontfix) --------------------------------------


@pytest.mark.parametrize("label", ["bau:pause", "wontfix"])
def test_startstelle_sperrt_pausierte_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setattr(
        ticket_zustand,
        "lesen",
        lambda *_a, **_k: {"state": "OPEN", "labels": [{"name": label}]},
    )
    erg = startstelle.betreten("1183", "org/repo", tmp_path)
    assert erg.erlaubt is False
    assert erg.exit_code == 0
    assert label in erg.grund
    weiter = startstelle.weiter_entscheidung("1183", tmp_path)
    assert weiter.erlaubt is False
    assert weiter.exit_code == 0
    assert label in weiter.grund


def test_ticket_zustand_sperre_grund_erlaubt_bau_lokal() -> None:
    assert ticket_zustand.sperre_grund({"state": "OPEN", "labels": [{"name": "bau:lokal"}]}) is None
    assert ticket_zustand.sperre_grund({"state": "OPEN", "labels": [{"name": "bau:pause"}]}) == "Label bau:pause"
    assert ticket_zustand.sperre_grund({"state": "OPEN", "labels": [{"name": "wontfix"}]}) == "Label wontfix"


def test_bau_prueft_sperre_nach_speicher_warten(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bau:pause, gesetzt während des Speicher-Wartens, verhindert den Start."""
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    stand: dict[str, object] = {"state": "OPEN", "labels": []}
    monkeypatch.setattr(ticket_zustand, "lesen", lambda *_a, **_k: dict(stand))
    gestartet: list[list[str]] = []
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (0, None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort"])

    def warten(*_a: object, **_k: object) -> int:
        stand["labels"] = [{"name": "bau:pause"}]
        return 3

    monkeypatch.setattr(bau, "auf_speicher_warten", warten)
    assert bau.main() == 0
    assert gestartet == []


# -- 3. cgroup-Slice-Präfix & Bau-Fallback -------------------------------------


def test_slice_praefix_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    praefix = speicher.slice_praefix(None, "bau-7", uid=1000, hat=lambda _w: "/usr/bin/x", bus_da=lambda _p: True)
    text = " ".join(praefix)
    assert "systemd-run --user --scope --quiet --slice=bau-sessions.slice --unit=bau-7-" in text
    assert "XDG_RUNTIME_DIR=/run/user/1000" in text
    assert "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus" in text
    assert praefix[-4:] == ["choom", "-n", "300", "--"]


def test_gate_slice_praefix_trennt_gate_von_bau_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    praefix = speicher.gate_slice_praefix(None, "gate-1", uid=1000, hat=lambda _w: "/usr/bin/x", bus_da=lambda _p: True)
    text = " ".join(praefix)
    assert "--slice=bau-gate.slice" in text
    assert "bau-sessions.slice" not in text
    assert "choom" not in praefix


def test_slice_praefix_aus_auf_windows_ohne_bus_oder_leer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    monkeypatch.setattr(speicher.sys, "platform", "win32")
    assert speicher.slice_praefix(None, "x", uid=1, hat=lambda _w: "ok", bus_da=lambda _p: True) == []
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    assert speicher.slice_praefix(None, "x", uid=1, hat=lambda _w: "ok", bus_da=lambda _p: False) == []
    assert speicher.slice_praefix(None, "x", uid=1, hat=lambda _w: None, bus_da=lambda _p: True) == []
    assert (
        speicher.slice_praefix({"bau_schutz": {"slice": ""}}, "x", uid=1, hat=lambda _w: "ok", bus_da=lambda _p: True)
        == []
    )


def test_slice_praefix_unit_name_je_start_eindeutig(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zwei Staffel-Runden im selben bau.py bekommen verschiedene Scope-Namen."""
    monkeypatch.setattr(speicher.sys, "platform", "linux")
    monkeypatch.delenv("TO_SPAWN_BAU_SLICE", raising=False)
    namen = [
        next(
            w
            for w in speicher.slice_praefix(None, "bau-7", uid=1000, hat=lambda _w: "ok", bus_da=lambda _p: True)
            if w.startswith("--unit=")
        )
        for _ in range(2)
    ]
    assert namen[0] != namen[1]
    assert all(n.startswith("--unit=bau-7-") for n in namen)


def test_bau_slice_fehlstart_einmal_ohne_praefix(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheitert der Slice-Start sofort, folgt genau ein Start ohne Präfix."""
    gestartet: list[list[str]] = []
    codes = iter([1, 0])
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_uebergabe", lambda _d: None)
    monkeypatch.setattr(bau.speicher, "slice_praefix", lambda *_a, **_k: ["systemd-run", "--scope"])
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (next(codes), None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort"])
    assert bau.main() == 0
    assert len(gestartet) == 2
    assert gestartet[0][:2] == ["systemd-run", "--scope"]
    assert "systemd-run" not in gestartet[1]


def test_defaults_hat_bau_schutz_block() -> None:
    assert config.DEFAULTS["bau_schutz"] == {
        "slice": "bau-sessions.slice",
        "gate_slice": "bau-gate.slice",
        "choom": 300,
    }


# -- 4. Nest-Einheiten, nest_server.sh & Abnahme-Doku --------------------------


def test_nest_einheiten_und_slices_vorhanden() -> None:
    wieder = (NEST / "bau-wiederaufnahme.service").read_text(encoding="utf-8")
    assert "to_spawn.wiederaufnahme" in wieder
    assert "Restart=on-failure" in wieder
    assert "KillMode=process" in wieder

    bau_slice = (NEST / "bau-sessions.slice").read_text(encoding="utf-8")
    for stueck in ("MemoryAccounting=yes", "MemoryHigh=10G", "MemoryMax=12G", "ManagedOOMMemoryPressure=kill"):
        assert stueck in bau_slice, stueck

    gate_slice = (NEST / "bau-gate.slice").read_text(encoding="utf-8")
    gate_direktiven = "\n".join(z for z in gate_slice.splitlines() if not z.lstrip().startswith("#"))
    for stueck in ("MemoryAccounting=yes", "MemoryMax=6G"):
        assert stueck in gate_direktiven, stueck
    assert "ManagedOOMMemoryPressure=kill" not in gate_direktiven


def test_nest_server_swap_4g_slices_oomd_und_wiederaufnahme() -> None:
    text = (NEST / "nest_server.sh").read_text(encoding="utf-8")
    for stueck in (
        "SWAP_GIB=4",
        "ssh.service.d/oom.conf",
        "user@.service.d/delegate.conf",
        "Delegate=cpu cpuset io memory pids",
        "systemd-oomd",
        "bau-sessions.slice",
        "bau-gate.slice",
        "bau-wiederaufnahme.service",
        "enable-linger",
    ):
        assert stueck in text, stueck


@pytest.mark.braucht("bash")
def test_nest_server_trocken_nennt_4g_slices_oomd_und_wiederaufnahme() -> None:
    ergebnis = subprocess.run(
        ["bash", str(NEST / "nest_server.sh"), "--repo", "x/y", "--trocken"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    for stueck in ("4 GiB", "bau-sessions.slice", "bau-gate.slice", "systemd-oomd", "bau-wiederaufnahme.service"):
        assert stueck in ergebnis.stdout, stueck


def test_doku_swap_4g_und_abnahme_messprotokoll() -> None:
    doku = (DOCS / "BAU_SERVER_SPEICHERSCHUTZ.md").read_text(encoding="utf-8")
    for stueck in (
        "4 GB",
        "swapoff",
        "bau-sessions.slice",
        "bau-gate.slice",
        "systemd-oomd",
        "bau-wiederaufnahme.service",
        "Lasttest",
        "Neustart-Probe",
        "Messprotokoll",
    ):
        assert stueck in doku, stueck
