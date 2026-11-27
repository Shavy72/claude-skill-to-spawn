"""#854: Abnahme-Ticket (``checkpoint:human``) startet nie als Bau-Session.

Prüft die eine Auswahl-Regel in ``to_spawn.ticket_zustand`` und alle Startwege:
1. Baustein ``ticket_zustand.bau_startbar`` / ``ist_abnahme``: ``checkpoint:human``
   (bzw. ``regularien.checkpoint_label``) schließt den Bau-Start aus, auch wenn
   ``ready-for-agent`` zusätzlich am Ticket hängt.
2. Weg-Test mit echtem ``skripte/bau.py``-Prozess (``tests/hilfen/bau_lauf_97.py`` +
   ``tests/hilfen/gh_stub.py``): Ticket mit beiden Labels (``checkpoint:human`` UND
   ``ready-for-agent``) startet keine Bau-Session (Exit 0, 0 Starts).
3. Aufpasser (``zu_bauen`` und ``start_erlaubt``) sowie ``to_spawn.py zu-bauen``
   (Windows-Pfad ``spawn_local.ps1``): Abnahme-Ticket mit beiden Labels wird nicht
   zum Bau ausgewählt.
4. ``kette/skills/to-tickets/SKILL.md`` und ``probe/gemeinsam/docs/GRILL_probe.md``:
   ``/to-tickets`` setzt ``ready-for-agent`` nicht auf Abnahme-Tickets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from to_spawn import aufpasser, cli_befehle, startstelle, ticket_zustand

SKILL = Path(__file__).resolve().parent.parent
LAUF = SKILL / "tests" / "hilfen" / "bau_lauf_97.py"
GH_STUB = SKILL / "tests" / "hilfen" / "gh_stub.py"
TO_TICKETS = SKILL / "kette" / "skills" / "to-tickets" / "SKILL.md"
GRILL_PROBE = SKILL / "probe" / "gemeinsam" / "docs" / "GRILL_probe.md"


# --- 1. Die eine Regel: ticket_zustand.bau_startbar / ist_abnahme -------------


@pytest.mark.parametrize(
    ("ticket", "erwartet_abnahme", "erwartet_startbar"),
    [
        ({"state": "OPEN", "labels": [{"name": "ready-for-agent"}]}, False, True),
        ({"state": "OPEN", "labels": [{"name": "checkpoint:human"}]}, True, False),
        (
            {"state": "OPEN", "labels": [{"name": "checkpoint:human"}, {"name": "ready-for-agent"}]},
            True,
            False,
        ),
        ({"state": "open", "labels": ["ready-for-agent", "checkpoint:human"]}, True, False),
        ({"state": "OPEN", "labels": {"checkpoint:human", "ready-for-agent"}}, True, False),
        ({"state": "OPEN", "labels": ["staging:fertig", "ready-for-agent"]}, False, False),
        ({"state": "CLOSED", "labels": ["ready-for-agent"]}, False, False),
    ],
)
def test_bau_startbar_schliesst_checkpoint_human_immer_aus(
    ticket: dict[str, Any],
    erwartet_abnahme: bool,
    erwartet_startbar: bool,
) -> None:
    assert ticket_zustand.ist_abnahme(ticket) is erwartet_abnahme
    assert ticket_zustand.bau_startbar(ticket) is erwartet_startbar


def test_bau_startbar_respektiert_eigenes_checkpoint_label_aus_konfig(tmp_path: Path) -> None:
    (tmp_path / ".to-spawn").mkdir()
    (tmp_path / ".to-spawn" / "config.json").write_text(
        json.dumps({"regularien": {"checkpoint_label": "abnahme:nutzer"}}),
        encoding="utf-8",
    )
    ticket = {"state": "OPEN", "labels": ["ready-for-agent", "abnahme:nutzer"]}
    assert ticket_zustand.ist_abnahme(ticket, repo=tmp_path) is True
    assert ticket_zustand.bau_startbar(ticket, repo=tmp_path) is False


# --- 2. Weg-Test: echter bau.py-Prozess mit beiden Labels ---------------------


def _bau_umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daten: dict[str, Any]) -> dict[str, str]:
    daten_pfad = tmp_path / "gh_daten.json"
    daten_pfad.write_text(json.dumps(daten), encoding="utf-8")
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("GH_STUB_DATEN", str(daten_pfad))
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    return dict(os.environ)


def _starts(beleg: Path) -> int:
    return len(beleg.read_text(encoding="utf-8").splitlines()) if beleg.is_file() else 0


def test_bau_weg_test_abnahme_mit_ready_for_agent_startet_nie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weg-Test: Ticket mit ``checkpoint:human`` UND ``ready-for-agent`` → kein Bau-Start."""
    beleg = tmp_path / "beleg.txt"
    env = _bau_umgebung(
        tmp_path,
        monkeypatch,
        {"8541": {"labels": ["checkpoint:human", "ready-for-agent"]}},
    )
    lauf = subprocess.run(
        [sys.executable, str(LAUF), str(beleg), "8541", "--sofort"],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert _starts(beleg) == 0, ausgabe
    assert lauf.returncode == 0, ausgabe
    assert "checkpoint:human" in ausgabe or "Abnahme" in ausgabe, ausgabe


def test_bau_weg_test_eigenes_checkpoint_label_startet_nie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weg-Test: konfiguriertes ``regularien.checkpoint_label`` + ``ready-for-agent`` → kein Bau-Start."""
    (tmp_path / ".to-spawn").mkdir()
    (tmp_path / ".to-spawn" / "config.json").write_text(
        json.dumps({"regularien": {"checkpoint_label": "abnahme:nutzer"}}),
        encoding="utf-8",
    )
    beleg = tmp_path / "beleg.txt"
    env = _bau_umgebung(
        tmp_path,
        monkeypatch,
        {"8542": {"labels": ["abnahme:nutzer", "ready-for-agent"]}},
    )
    lauf = subprocess.run(
        [sys.executable, str(LAUF), str(beleg), "8542", "--sofort"],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert _starts(beleg) == 0, ausgabe
    assert lauf.returncode == 0, ausgabe
    assert "abnahme:nutzer" in ausgabe or "Abnahme" in ausgabe, ausgabe


# --- 3. Aufpasser und CLI ``zu-bauen`` ----------------------------------------


def test_aufpasser_zu_bauen_und_start_erlaubt_schliessen_abnahme_aus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waechter = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", trocken=True))
    offen = {
        10: {"number": 10, "state": "open", "labels": ["ready-for-agent"]},
        11: {"number": 11, "state": "open", "labels": ["checkpoint:human", "ready-for-agent"]},
    }
    assert waechter.zu_bauen(offen, repo=tmp_path) == {10}

    monkeypatch.setattr(
        waechter,
        "ticket_lesen",
        lambda _ticket, _repo: {"state": "OPEN", "labels": {"checkpoint:human", "ready-for-agent"}},
    )
    assert waechter.start_erlaubt("1", tmp_path, 11) is False


def test_cli_zu_bauen_filtert_abnahme_mit_ready_for_agent_aus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    mani = tmp_path / "docs" / "agents" / "manifests" / "spec-1.json"
    mani.parent.mkdir(parents=True)
    mani.write_text(
        json.dumps({"spec": 1, "tickets": {"10": {"title": "Bau"}, "11": {"title": "Abnahme"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    tickets = {
        "10": {"state": "OPEN", "labels": [{"name": "ready-for-agent"}]},
        "11": {"state": "OPEN", "labels": [{"name": "checkpoint:human"}, {"name": "ready-for-agent"}]},
    }
    monkeypatch.setattr(ticket_zustand, "lesen", lambda nr, _repo=None: tickets[str(nr)])
    rc = cli_befehle._zu_bauen(SimpleNamespace(spec="1"))
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == ["10"]


# --- 4. /to-tickets setzt ready-for-agent nicht auf Abnahme-Tickets -----------


def test_to_tickets_verbietet_ready_for_agent_am_abnahme_ticket() -> None:
    text = TO_TICKETS.read_text(encoding="utf-8")
    schritt_5 = text[text.index("### 5.") : text.index("<local-ticket-template>")]
    assert "kein `ready-for-agent`" in schritt_5, "Schritt 5 muss ready-for-agent am Abnahme-Ticket ausschließen"
    assert "zusätzlich `checkpoint:human`" not in schritt_5, (
        "Schritt 5 darf checkpoint:human nicht mehr zusätzlich zu ready-for-agent verlangen"
    )
    lokal_vorlage = text[text.index("<local-ticket-template>") : text.index("</local-ticket-template>")]
    assert "kein ready-for-agent" in lokal_vorlage, (
        "Lokale Ticket-Vorlage muss ready-for-agent beim Abnahme-Ticket ausschließen"
    )
    assert "zusätzlich: checkpoint:human" not in lokal_vorlage


def test_grill_probe_e9_kein_ready_for_agent_am_abnahme_ticket() -> None:
    text = GRILL_PROBE.read_text(encoding="utf-8")
    abschnitt_e9 = text[text.index("### E9") : text.index("## Verworfen")]
    assert "kein `ready-for-agent`" in abschnitt_e9, (
        "E9 in probe/gemeinsam/docs/GRILL_probe.md darf kein ready-for-agent am Abnahme-Ticket verlangen"
    )
