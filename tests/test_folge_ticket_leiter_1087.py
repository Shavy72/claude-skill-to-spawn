"""Weg-Test (#867 / App #1087): Manifest-Folge-Ticket ohne GitHub-Sub-Issue-Verknüpfung.

Prüft, dass ein Folge-Ticket aus ``docs/agents/manifests/spec-<S>.json`` auch ohne
GitHub-Sub-Issue-Kante durch ``aufseher_stand``, ``leiter`` (inkl. ``folge_handoff``
nach committetem eigenem Handoff) und ``aufpasser.offene_tickets`` erfasst wird.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, aufseher_stand, eigener_handoff, leiter, prozessbaum

SPEC = 77
SUB_TICKET = 101
FOLGE_TICKET = 102
ZU_FOLGE_TICKET = 103
UNBEKANNT = 999
JETZT = 1_800_000_000.0
MINUTE = 60.0
STILL_BILD = "● Fertig, Handoff committet.\n\n❯ \n"


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> None:
    umgebung = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        **(env or {}),
    }
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=umgebung,
        check=True,
        capture_output=True,
        text=True,
        **prozessbaum.fensterlos(),
    )


def _schreibe_gh_stub(tmp_path: Path, antworten: dict[str, Any]) -> Path:
    """Kleiner ``gh``-Stub für ``api repos/.../sub_issues``, ``api repos/.../issues/<N>`` und ``issue view``."""
    daten_datei = tmp_path / "gh_antworten.json"
    daten_datei.write_text(json.dumps(antworten, ensure_ascii=False), encoding="utf-8")
    stub = tmp_path / "gh_stub.py"
    stub.write_text(
        textwrap.dedent(
            f"""\
            import json, sys
            from pathlib import Path

            daten = json.loads(Path({str(daten_datei)!r}).read_text(encoding="utf-8"))
            args = sys.argv[1:]
            if args[:1] == ["api"] and len(args) >= 2:
                ziel = args[1]
                if ziel in daten:
                    print(json.dumps(daten[ziel]))
                    sys.exit(0)
            if args[:2] == ["issue", "view"] and len(args) >= 3:
                schluessel = f"issue view {{args[2]}}"
                if schluessel in daten:
                    print(json.dumps(daten[schluessel]))
                    sys.exit(0)
            sys.stderr.write(f"unerwarteter gh-Aufruf: {{args!r}}\\n")
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )
    return stub


def _manifest_schreiben(repo: Path, tickets: list[int]) -> None:
    datei = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    datei.parent.mkdir(parents=True, exist_ok=True)
    daten = {
        "spec": SPEC,
        "tickets": {
            str(n): {"schaetzung_k": 15, "umfang": f"Ticket #{n}"}
            for n in tickets
        },
    }
    datei.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


def _fake_tmux(ticket: int, aktiv: float) -> Any:
    def _tmux(args: list[str]) -> str | None:
        if args[0] == "list-windows":
            return f"@1\tbau {ticket}\t{int(aktiv)}\t\n"
        if args[0] == "capture-pane":
            return STILL_BILD
        return None

    return _tmux


@pytest.fixture()
def test_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    haupt = tmp_path / "repo"
    wt_basis = tmp_path / "wts"
    haupt.mkdir()
    wt_basis.mkdir()
    _git(haupt, "init", "-b", "main")
    (haupt / "README.md").write_text("Basis\n", encoding="utf-8")
    _git(haupt, "add", "README.md")
    _git(haupt, "commit", "-m", "Basis")

    (haupt / ".to-spawn").mkdir()
    (haupt / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": str(wt_basis)}),
        encoding="utf-8",
    )
    _manifest_schreiben(haupt, [SUB_TICKET, FOLGE_TICKET, ZU_FOLGE_TICKET])

    # Worktree für das Manifest-Folge-Ticket mit committetem eigenem Handoff
    wt = wt_basis / f"wt-{FOLGE_TICKET}"
    _git(haupt, "worktree", "add", "-b", f"ticket/{FOLGE_TICKET}", str(wt))
    handoff = wt / eigener_handoff.ORDNER / f"HANDOFF_2027-01-15_{FOLGE_TICKET}.md"
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text("# Handoff\n\nWeiter ab Schritt 2.\n", encoding="utf-8")
    commit_zeit = str(int(JETZT - (eigener_handoff.STILL_MIN + 5) * MINUTE))
    _git(wt, "add", handoff.relative_to(wt).as_posix())
    _git(
        wt,
        "commit",
        "-m",
        f"docs(handoff): Übergabe #{FOLGE_TICKET}",
        env={"GIT_AUTHOR_DATE": f"{commit_zeit} +0000", "GIT_COMMITTER_DATE": f"{commit_zeit} +0000"},
    )

    # Bau-Log mit session_start vor dem Handoff
    log_datei = wt / ".to-spawn" / "bau_log" / f"{FOLGE_TICKET}.jsonl"
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    start_ts = "2027-01-15T07:00:00+00:00"
    log_datei.write_text(
        json.dumps({"ticket": str(FOLGE_TICKET), "ts": start_ts, "typ": "session_start", "text": "Start"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("TO_SPAWN_REPO", str(haupt))
    monkeypatch.setenv(aufseher_stand.STAND_ENV, str(tmp_path / "stand"))
    monkeypatch.setenv("TO_SPAWN_LEITSTAND", str(tmp_path / "leitstand.json"))
    return {"haupt": haupt, "wt": wt, "stand": tmp_path / "stand"}


def test_aufseher_stand_erfasst_manifest_folge_ticket_ohne_sub_issue(
    test_repo: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``aufseher_stand`` (stand, ticket_lage, beleg_fuer) erfasst Manifest-Folge-Tickets ohne Sub-Issue-Kante."""
    stub = _schreibe_gh_stub(
        tmp_path,
        {
            f"repos/o/r/issues/{SPEC}/sub_issues?per_page=100": [
                {"number": SUB_TICKET, "state": "open", "comments": 0, "labels": []},
            ],
            f"repos/o/r/issues/{FOLGE_TICKET}": {
                "number": FOLGE_TICKET,
                "state": "open",
                "comments": 2,
                "labels": [],
            },
            f"repos/o/r/issues/{ZU_FOLGE_TICKET}": {
                "number": ZU_FOLGE_TICKET,
                "state": "closed",
                "state_reason": "completed",
                "comments": 1,
                "labels": [],
            },
        },
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    q = replace(
        aufseher_stand.echte_quellen(test_repo["haupt"], "o/r"),
        tmux=_fake_tmux(FOLGE_TICKET, JETZT - 10 * MINUTE),
        jetzt=lambda: JETZT,
    )

    ausgabe = aufseher_stand.stand(SPEC, q, ordner=test_repo["stand"], alle=True)
    assert f"#{SUB_TICKET} offen" in ausgabe
    assert f"#{FOLGE_TICKET} offen · still 10 min" in ausgabe
    assert f"#{ZU_FOLGE_TICKET} zu · kein Fenster" in ausgabe

    blick = aufseher_stand.ticket_lage(SPEC, FOLGE_TICKET, q, ordner=test_repo["stand"])
    assert blick.lage is not None
    assert blick.lage.nummer == FOLGE_TICKET
    assert blick.lage.stufe == "offen"
    assert blick.lage.fenster == aufseher_stand.STILL
    assert blick.ziel is not None

    beleg = aufseher_stand.beleg_fuer(SPEC, FOLGE_TICKET, q, ordner=test_repo["stand"])
    assert beleg.ticket == FOLGE_TICKET
    assert beleg.zustand == aufseher_stand.WARTET


def test_leiter_plant_folge_handoff_fuer_manifest_folge_ticket_ohne_sub_issue(
    test_repo: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``leiter`` bricht bei Manifest-Folge-Ticket ohne Sub-Issue nicht mit RuntimeError ab und plant ``folge_handoff``."""
    stub = _schreibe_gh_stub(
        tmp_path,
        {
            f"repos/o/r/issues/{SPEC}/sub_issues?per_page=100": [
                {"number": SUB_TICKET, "state": "open", "comments": 0, "labels": []},
            ],
            f"repos/o/r/issues/{FOLGE_TICKET}": {
                "number": FOLGE_TICKET,
                "state": "open",
                "comments": 0,
                "labels": [],
            },
            f"repos/o/r/issues/{ZU_FOLGE_TICKET}": {
                "number": ZU_FOLGE_TICKET,
                "state": "closed",
                "state_reason": "completed",
                "comments": 0,
                "labels": [],
            },
        },
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    q = replace(
        aufseher_stand.echte_quellen(test_repo["haupt"], "o/r"),
        tmux=_fake_tmux(FOLGE_TICKET, JETZT - 10 * MINUTE),
        jetzt=lambda: JETZT,
    )
    umwelt = leiter._EchteUmwelt(test_repo["haupt"], q)

    erg = leiter.eingreifen(test_repo["haupt"], SPEC, FOLGE_TICKET, umwelt=umwelt, dry_run=True)
    assert erg.exit == leiter.EXIT_OK, erg.zeile
    assert "folge_handoff geplant" in erg.zeile, erg.zeile


def test_aufpasser_offene_tickets_erfasst_manifest_folge_ticket_ohne_sub_issue(
    test_repo: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``aufpasser.offene_tickets`` nimmt offene Manifest-Folge-Tickets ohne Sub-Issue-Kante auf."""
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "aufpasser_zustand"))
    sub_zeile = json.dumps({"number": SUB_TICKET, "state": "open", "state_reason": None, "labels": []})
    monkeypatch.setattr(a, "sh", lambda *args, **kwargs: sub_zeile + "\n")

    gelesen: list[int] = []

    def _fake_ticket_lesen(ticket: int, repo: Path) -> dict[str, Any]:
        gelesen.append(ticket)
        if ticket == FOLGE_TICKET:
            return {"state": "OPEN", "stateReason": None, "labels": set()}
        if ticket == ZU_FOLGE_TICKET:
            return {"state": "CLOSED", "stateReason": "COMPLETED", "labels": set()}
        raise RuntimeError(f"unerwartetes Ticket #{ticket}")

    monkeypatch.setattr(a, "ticket_lesen", _fake_ticket_lesen)

    offen = a.offene_tickets(str(SPEC), test_repo["haupt"])
    assert SUB_TICKET in offen
    assert FOLGE_TICKET in offen
    assert ZU_FOLGE_TICKET not in offen
    # Sub-Issue #101 wurde nicht doppelt einzeln abgefragt.
    assert gelesen == [FOLGE_TICKET, ZU_FOLGE_TICKET]


def test_unbekanntes_ticket_weder_sub_issue_noch_manifest_bricht_ab(
    test_repo: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Ticket, das weder Sub-Issue noch Manifest-Ticket ist, bleibt weiterhin ein Fehler."""
    stub = _schreibe_gh_stub(
        tmp_path,
        {
            f"repos/o/r/issues/{SPEC}/sub_issues?per_page=100": [
                {"number": SUB_TICKET, "state": "open", "comments": 0, "labels": []},
            ],
            f"repos/o/r/issues/{FOLGE_TICKET}": {
                "number": FOLGE_TICKET,
                "state": "open",
                "comments": 0,
                "labels": [],
            },
            f"repos/o/r/issues/{ZU_FOLGE_TICKET}": {
                "number": ZU_FOLGE_TICKET,
                "state": "closed",
                "comments": 0,
                "labels": [],
            },
        },
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    q = replace(
        aufseher_stand.echte_quellen(test_repo["haupt"], "o/r"),
        tmux=_fake_tmux(UNBEKANNT, JETZT - 10 * MINUTE),
        jetzt=lambda: JETZT,
    )
    umwelt = leiter._EchteUmwelt(test_repo["haupt"], q)

    erg = leiter.eingreifen(test_repo["haupt"], SPEC, UNBEKANNT, umwelt=umwelt, dry_run=True)
    assert erg.exit == leiter.EXIT_FEHLER
    assert f"#{UNBEKANNT}" in erg.zeile
