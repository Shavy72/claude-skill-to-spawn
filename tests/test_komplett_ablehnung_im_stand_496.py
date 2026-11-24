"""#496 Teil B: Jede Ablehnung der Komplett-Sperre ist für den Aufseher sichtbar.

Bisher stand eine Ablehnung (:func:`to_spawn.komplett_lauf.bau_ticket_ablehnung`) nur im Log. Jetzt
legt die eine Stelle sie als Bau-Log-Zeile des Tickets ab, und der Aufseher-Stand
(``to_spawn.py aufseher-stand <S>``) zeigt in der Kurz-Zeile des Tickets, wie oft abgelehnt wurde.

Echt ohne Attrappen: App-Repo mit eigenem Komplett-Skript, beide Skill-Wege (Prüf-Tür und
``komplett_lauf``), echtes Bau-Log, echte Stand-Datei. Gefaked sind nur GitHub (Sub-Issues) und tmux.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_komplett_sperre_eigenes_skript_496 import _app_repo, _laeufe
from to_spawn import aufseher_stand, bau_log, config, komplett_lauf, pruef
from to_spawn.pruefplan import Moment

SPEC = 496
TICKET = 13


def _quellen(repo: Path) -> aufseher_stand.Quellen:
    def kinder(spec: int) -> list[dict[str, Any]]:
        assert spec == SPEC
        return [{"number": TICKET, "state": "open", "title": "t", "comments": 0}]

    return aufseher_stand.Quellen(repo=repo, kinder=kinder, tmux=lambda _args: None)


def test_ablehnungen_stehen_im_aufseher_stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zaehl = tmp_path / "zaehl.txt"
    repo = _app_repo(tmp_path, zaehl)
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    # Bau-Session wie bau.py sie startet: Ticket-Nummer + Ziel des Bau-Logs.
    monkeypatch.setenv("TO_SPAWN_TICKET", str(TICKET))
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(repo))
    pruef.pruefen(repo, "base", "HEAD", art="komplett")
    komplett_lauf.komplett_einmal(repo, SPEC, Moment.VOR_LIVE, config.lade(repo))
    assert _laeufe(zaehl) == 0
    monkeypatch.delenv("TO_SPAWN_TICKET")
    monkeypatch.delenv("TO_SPAWN_LOG_REPO")

    ausgabe = aufseher_stand.stand(SPEC, _quellen(repo), ordner=tmp_path / "stand")
    zeile = next(z for z in ausgabe.splitlines() if z.startswith(f"#{TICKET} "))
    assert "2× Komplett abgelehnt" in zeile, ausgabe


def test_ohne_ablehnung_bleibt_die_zeile_wie_bisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _app_repo(tmp_path, tmp_path / "zaehl.txt")
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    ausgabe = aufseher_stand.stand(SPEC, _quellen(repo), ordner=tmp_path / "stand")
    assert "abgelehnt" not in ausgabe, ausgabe


def test_ablehnung_ohne_bau_log_ziel_schreibt_nichts_ins_skill_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne ``TO_SPAWN_LOG_REPO`` (Aufruf von Hand) nur die Warnung im Log, keine Datei im aktuellen Repo."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TO_SPAWN_TICKET", str(TICKET))
    monkeypatch.delenv("TO_SPAWN_LOG_REPO", raising=False)
    assert komplett_lauf.bau_ticket_ablehnung(SPEC, "test")
    assert komplett_lauf.ablehnungen([]) == 0
    assert not (tmp_path / ".to-spawn").exists()


def test_pruef_tuer_nennt_die_spec_aus_der_umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _app_repo(tmp_path, tmp_path / "zaehl.txt")
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    monkeypatch.setenv("TO_SPAWN_TICKET", str(TICKET))
    monkeypatch.setenv("TO_SPAWN_LOG_REPO", str(repo))
    monkeypatch.setenv("TO_SPAWN_SPEC", str(SPEC))
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    meldung = urteil.zeile["fehler_kurz"]
    assert f"komplett {SPEC} --moment vor-live" in meldung and "<S>" not in meldung, meldung
    zeilen = [json.loads(z) for p in repo.rglob("*.jsonl") for z in p.read_text(encoding="utf-8").splitlines()]
    abgelehnt = [z for z in zeilen if z.get("typ") == bau_log.KOMPLETT_ABGELEHNT]
    assert abgelehnt and abgelehnt[0].get("spec") == SPEC, zeilen


def test_ablehnungs_typ_hat_eine_definition() -> None:
    assert komplett_lauf.bau_log.KOMPLETT_ABGELEHNT is bau_log.KOMPLETT_ABGELEHNT
    assert bau_log.KOMPLETT_ABGELEHNT in bau_log.TYPEN
    assert not hasattr(komplett_lauf, "ABLEHNUNG_TYP")
