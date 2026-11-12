"""#1184: Pause-Guard vor jedem bau.py-Start, Wiederaufnahme nach Neustart, Aufräum-Hook.

1. ``bau.main`` startet ein Ticket mit ``bau:pause`` bzw. ein geschlossenes nie — auch
   nicht mit ``--resume`` (Aufpasser-Fortsetzen, respawn, Kette laufen alle hier durch).
   GitHub ist gestellt (externer Dienst), ``main()`` läuft echt.
2. ``wiederaufnahme.offene_specs`` findet nur Klone mit ``.to-spawn/sessions/`` und
   offenem Spec-Issue (gh-Antwort gestellt).
3. Der Aufpasser ruft nach „Ticket geschlossen“ ``aufraeumen.nach_ende`` für den
   Ticket-Worktree — ein Fehler dort stoppt ihn nicht.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from test_bau_ohne_prompt_431 import bau  # noqa: F401 — Fixture

import to_spawn
from to_spawn import aufpasser, gh, wiederaufnahme

# -- 1. Pause-Guard ------------------------------------------------------------


def _gh_antwort(monkeypatch: pytest.MonkeyPatch, daten: dict | None) -> None:
    monkeypatch.delenv("TO_SPAWN_START_SPERRE", raising=False)
    monkeypatch.setattr(gh, "ticket_daten", lambda *_a, **_k: daten)


@pytest.mark.parametrize(
    "daten",
    [
        {"state": "OPEN", "labels": [{"name": "bau:pause"}]},
        {"state": "CLOSED", "labels": []},
        None,  # GitHub nicht lesbar → fail-closed
    ],
)
@pytest.mark.parametrize("extra", [[], ["--resume", "abc"]])
def test_bau_startet_gesperrtes_ticket_nie(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    daten: dict | None,
    extra: list[str],
    tmp_path: Path,
) -> None:
    gestartet: list[list[str]] = []
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (0, None))[1])
    (tmp_path / "tr").mkdir()
    (tmp_path / "tr" / "abc.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bau, "transkript_ordner", lambda _p: tmp_path / "tr")
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort", *extra])
    _gh_antwort(monkeypatch, daten)
    assert bau.main() == 0
    assert gestartet == []


def test_bau_startet_freies_ticket(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gestartet: list[list[str]] = []
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_uebergabe", lambda _d: None)
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (0, None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort"])
    _gh_antwort(monkeypatch, {"state": "OPEN", "labels": [{"name": "bau:lokal"}]})
    assert bau.main() == 0
    assert len(gestartet) == 1


# -- 2. Offene Specs -------------------------------------------------------------


def test_offene_specs_aus_klonen(tmp_path: Path) -> None:
    for name in ("duoplus-1109", "duoplus-784", "duoplus-900", "duoplus-ohne"):
        (tmp_path / name / ".to-spawn" / "sessions").mkdir(parents=True)
    (tmp_path / "duoplus-555").mkdir()  # kein sessions-Ordner → kein Spec-Klon
    (tmp_path / "datei-77").write_text("x", encoding="utf-8")
    offen = {"1109": True, "784": False, "900": None, "555": True}
    gefragt: list[str] = []

    def ist_offen(spec: str, _klon: Path) -> bool | None:
        gefragt.append(spec)
        return offen[spec]

    treffer = wiederaufnahme.offene_specs(tmp_path, ist_offen)
    assert treffer == [("1109", tmp_path / "duoplus-1109")]
    assert sorted(gefragt) == ["1109", "784", "900"]


def test_wiederaufnahme_startet_je_spec_einmal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiederaufnahme, "offene_specs", lambda _h, **_k: [("1", tmp_path), ("2", tmp_path)])
    monkeypatch.setattr(wiederaufnahme, "sitzung_da", lambda spec: spec == "2")
    gestartet: list[str] = []
    monkeypatch.setattr(wiederaufnahme, "aufseher_starten", lambda spec, _k, trocken: gestartet.append(spec) or True)
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True) == 0
    assert gestartet == ["1"]


def test_wiederaufnahme_wartet_auf_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S7: gh erst unlesbar, dann offen → nach Wiederholung aufgenommen, Exit 0."""
    (tmp_path / "duoplus-1109" / ".to-spawn" / "sessions").mkdir(parents=True)
    antworten = iter([None, None, True])
    monkeypatch.setattr(gh, "ticket_offen", lambda *_a, **_k: next(antworten))
    monkeypatch.setattr(wiederaufnahme, "sitzung_da", lambda _s: False)
    gestartet: list[str] = []
    monkeypatch.setattr(wiederaufnahme, "aufseher_starten", lambda spec, _k, trocken: gestartet.append(spec) or True)
    geschlafen: list[float] = []
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True, takt_s=7, schlafen=geschlafen.append) == 0
    assert gestartet == ["1109"]
    assert geschlafen == [7, 7]


def test_wiederaufnahme_github_dauerhaft_unlesbar_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S7: gh bleibt unlesbar → nach allen Versuchen Exit ≠ 0, kein Start."""
    (tmp_path / "duoplus-1109" / ".to-spawn" / "sessions").mkdir(parents=True)
    gefragt: list[str] = []
    monkeypatch.setattr(gh, "ticket_offen", lambda spec, **_k: gefragt.append(spec))
    gestartet: list[str] = []
    monkeypatch.setattr(wiederaufnahme, "aufseher_starten", lambda spec, _k, trocken: gestartet.append(spec) or True)
    assert wiederaufnahme.wiederaufnehmen(tmp_path, trocken=True, versuche=3, takt_s=0, schlafen=lambda _s: None) != 0
    assert gefragt == ["1109"] * 3
    assert gestartet == []


def test_bau_prueft_sperre_nach_speicher_warten(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S10: bau:pause, gesetzt während des Speicher-Wartens, verhindert den Start."""
    gestartet: list[list[str]] = []
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (0, None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort"])
    _gh_antwort(monkeypatch, {"state": "OPEN", "labels": []})

    def warten(*_a: object, **_k: object) -> int:
        _gh_antwort(monkeypatch, {"state": "OPEN", "labels": [{"name": "bau:pause"}]})
        return 3

    monkeypatch.setattr(bau, "auf_speicher_warten", warten)
    assert bau.main() == 0
    assert gestartet == []


def test_bau_slice_fehlstart_einmal_ohne_praefix(
    bau: types.ModuleType,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S12c: scheitert der Slice-Start sofort, folgt genau ein Start ohne Präfix."""
    gestartet: list[list[str]] = []
    codes = iter([1, 0])
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_uebergabe", lambda _d: None)
    monkeypatch.setattr(bau.speicher, "slice_praefix", lambda *_a, **_k: ["systemd-run", "--scope"])
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (next(codes), None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1183", "--sofort"])
    _gh_antwort(monkeypatch, {"state": "OPEN", "labels": []})
    assert bau.main() == 0
    assert len(gestartet) == 2
    assert gestartet[0][:2] == ["systemd-run", "--scope"]
    assert "systemd-run" not in gestartet[1]


# -- 3. Aufräum-Hook -------------------------------------------------------------


def _aufpasser_zum_schliessen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worktree: Path | None
) -> aufpasser.Aufpasser:
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))
    monkeypatch.setattr(a, "sichern_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(a, "vor_eingriff", lambda *_a, **_k: True)
    monkeypatch.setattr(a, "_fenster_schliessen", lambda *_a, **_k: "exit")
    monkeypatch.setattr(a, "melden", lambda *_a, **_k: None)
    monkeypatch.setattr(aufpasser, "kette_folge", lambda *_a: [])
    monkeypatch.setattr(aufpasser.verwaist, "protokollieren", lambda *_a, **_k: None)
    monkeypatch.setattr(aufpasser, "worktree_finden", lambda *_a: worktree)
    return a


def _attrappe(monkeypatch: pytest.MonkeyPatch, nach_ende) -> None:  # noqa: ANN001
    modul = types.ModuleType("to_spawn.aufraeumen")
    modul.nach_ende = nach_ende  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "to_spawn.aufraeumen", modul)
    monkeypatch.setattr(to_spawn, "aufraeumen", modul, raising=False)


def test_aufpasser_raeumt_nach_schliessen_auf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = tmp_path / "wt-1183"
    a = _aufpasser_zum_schliessen(tmp_path, monkeypatch, wt)
    aufrufe: list[tuple[Path, bool]] = []
    _attrappe(monkeypatch, lambda pfad, *, geschlossen: aufrufe.append((pfad, geschlossen)) or True)
    f = aufpasser.Fenster("spec-9", "9", "1", "bau 1183", str(wt), 4242, "@1")
    a.ticket_zu_schliessen(f, 1183, tmp_path, "spec-9/bau 1183", "h", None)
    assert aufrufe == [(wt, True)]


def test_aufraeumfehler_und_fehlender_worktree_stoppen_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(_pfad: Path, *, geschlossen: bool) -> bool:
        raise OSError("Platte weg")

    _attrappe(monkeypatch, kaputt)
    f = aufpasser.Fenster("spec-9", "9", "1", "bau 1183", str(tmp_path), 4242, "@1")
    a = _aufpasser_zum_schliessen(tmp_path, monkeypatch, tmp_path / "wt-1183")
    a.ticket_zu_schliessen(f, 1183, tmp_path, "spec-9/bau 1183", "h", None)  # kein Raise
    aufrufe: list[Path] = []
    _attrappe(monkeypatch, lambda pfad, *, geschlossen: aufrufe.append(pfad) or True)
    a = _aufpasser_zum_schliessen(tmp_path, monkeypatch, None)
    a.ticket_zu_schliessen(f, 1183, tmp_path, "spec-9/bau 1183", "h", None)
    assert aufrufe == []
