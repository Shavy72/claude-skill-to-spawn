"""Ticketstand: Tabelle „X von Y fertig“ für jeden Aufseher-Tick.

Rechenlogik (Zustand je Ticket, Kurztitel, Tabelle) mit Attrappen der Lage;
der Kommandoweg ``to_spawn.py ticketstand`` läuft als echter Prozess gegen eine
echte Manifest-Datei, nur ``gh`` ist ein Stub-Skript (externer Dienst).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, ticketstand  # noqa: E402

SPEC = 77


def issue(n: int, *, zu: bool = False, blocker: int = 0, labels: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "number": n,
        "state": "closed" if zu else "open",
        "title": f"Reel-Tresor #{SPEC}: Ticket {n}",
        "labels": [{"name": x} for x in labels],
        "comments": 0,
        "issue_dependencies_summary": {"blocked_by": blocker},
    }


def lage(n: int, fenster: str, still: int | None = None) -> aufseher_stand.TicketLage:
    return aufseher_stand.TicketLage(nummer=n, stufe="offen", fenster=fenster, still_min=still)


# --- Rechenlogik --------------------------------------------------------------


def test_zustand_fertig_gewinnt() -> None:
    assert ticketstand.zustand(issue(1, zu=True, blocker=2), lage(1, aufseher_stand.ARBEITET)) == ("fertig", "")


def test_zustand_blockiert_mit_namen() -> None:
    z = ticketstand.zustand(issue(2, blocker=1, labels=("checkpoint:human",)), None, [5])
    assert z == ("blockiert", "wartet auf #5")


def test_zustand_checkpoint() -> None:
    z = ticketstand.zustand(issue(3, labels=("checkpoint:human",)), None)
    assert z[0] == "checkpoint"


@pytest.mark.parametrize(
    ("fenster", "still", "erwartet"),
    [
        (aufseher_stand.ARBEITET, None, ("läuft", "arbeitet")),
        (aufseher_stand.STILL, 25, ("läuft", "still 25 min")),
        (aufseher_stand.RUECKFRAGE, 3, ("läuft", "Rückfrage 3 min")),
        (aufseher_stand.KEIN_FENSTER, None, ("wartet", "noch keine Session")),
        (aufseher_stand.FENSTER_UNBEKANNT, None, ("wartet", "Session-Lage unbekannt")),
    ],
)
def test_zustand_session(fenster: str, still: int | None, erwartet: tuple[str, str]) -> None:
    assert ticketstand.zustand(issue(4), lage(4, fenster, still)) == erwartet


def test_kurztitel_ohne_spec_vorsatz_und_gekuerzt() -> None:
    assert ticketstand.kurztitel(f"Reel-Tresor #{SPEC}: Plan-Bahn | 5 Plätze", SPEC) == "Plan-Bahn / 5 Plätze"
    lang = ticketstand.kurztitel("x" * 100, SPEC)
    assert len(lang) == ticketstand.TITEL_MAX and lang.endswith("…")


def test_tabelle_kopf_zaehlt() -> None:
    zeilen = [
        ticketstand.Zeile(1, "A", "fertig"),
        ticketstand.Zeile(2, "B", "läuft", "arbeitet"),
        ticketstand.Zeile(3, "C", "blockiert", "wartet auf #2"),
    ]
    text = ticketstand.tabelle(SPEC, zeilen, "16.11. 21:00")
    kopf = text.splitlines()[0]
    assert "1 von 3 fertig" in kopf and "1 läuft" in kopf and "1 blockiert" in kopf
    assert "| #2 | B | läuft | arbeitet |" in text
    assert "| #1 | A | fertig | — |" in text


# --- Kommandoweg: echter Prozess, echte Manifest-Datei, gh-Stub ---------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    (r / ".to-spawn").mkdir()
    (r / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": str(tmp_path / "wts")}), encoding="utf-8"
    )
    mani = r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    mani.parent.mkdir(parents=True)
    mani.write_text(
        json.dumps(
            {
                "spec": SPEC,
                "tickets": {
                    "101": {"title": f"Reel-Tresor #{SPEC}: Schalter"},
                    "102": {"title": f"Reel-Tresor #{SPEC}: Bestand"},
                    "103": {"title": f"Reel-Tresor #{SPEC}: [Checkpoint] Abnahme"},
                    "110": {"title": "Gelb aus #101: Zusatz-Test", "gelb_von": "101"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return r


def gh_stub(tmp_path: Path) -> Path:
    daten = {
        f"repos/o/r/issues/{SPEC}/sub_issues?per_page=100": [
            issue(101, zu=True),
            issue(102),
            issue(103, blocker=1, labels=("checkpoint:human",)),
        ],
        # Ohne Titel → Kurztitel kommt aus dem Manifest.
        "repos/o/r/issues/110": {**issue(110), "title": ""},
        "repos/o/r/issues/103/dependencies/blocked_by": [
            {"number": 102, "state": "open"},
            {"number": 101, "state": "closed"},
        ],
    }
    stub = tmp_path / "gh_stub.py"
    stub.write_text(
        textwrap.dedent(
            f"""
            import json, sys
            DATEN = json.loads({json.dumps(json.dumps(daten))})
            pfad = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "api" else ""
            if pfad not in DATEN:
                sys.exit(1)
            print(json.dumps(DATEN[pfad]))
            """
        ),
        encoding="utf-8",
    )
    return stub


def test_kommandoweg_echte_manifest_datei(repo: Path, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(
        TO_SPAWN_REPO=str(repo),
        TO_SPAWN_GH_STUB=str(gh_stub(tmp_path)),
        TO_SPAWN_AUFSEHER_STAND=str(tmp_path / "stand"),
        PYTHONIOENCODING="utf-8",
    )
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "ticketstand", str(SPEC), "--gh-repo", "o/r"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=repo,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    text = lauf.stdout
    assert text.splitlines()[0].startswith(f"**Ticketstand Spec #{SPEC}: 1 von 4 fertig**")
    assert "| #101 | Ticket 101 | fertig | — |" in text
    assert "| #103 | Ticket 103 | blockiert | wartet auf #102 |" in text
    # Gelbes Folge-Ticket nur im Manifest → einzeln nachgeladen.
    assert "| #110 | Gelb aus #101: Zusatz-Test | wartet |" in text
    assert "| #102 | Ticket 102 | wartet |" in text
    # Nur lesen: keine Stand-Datei angelegt.
    assert not (tmp_path / "stand").exists()


def test_kommandoweg_gh_kaputt_exit_1(repo: Path, tmp_path: Path) -> None:
    stub = tmp_path / "gh_kaputt.py"
    stub.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(TO_SPAWN_REPO=str(repo), TO_SPAWN_GH_STUB=str(stub), PYTHONIOENCODING="utf-8")
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "ticketstand", str(SPEC), "--gh-repo", "o/r"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=repo,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 1
    assert "FEHLER" in lauf.stderr
