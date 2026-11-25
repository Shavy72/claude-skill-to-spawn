"""Stand-Abfrage nach dem Merge scheitert selbst (#492, Folge aus #476/#487).

Gleiche Welt wie ``test_einreichen_411`` (echte Git-Repos, nur ``gh`` ist eine Attrappe).
Die Attrappe mergt den Pull-Request echt, hält danach ``origin/main`` gesperrt (Nachziehen
scheitert) und lässt ``gh pr view`` wie bei Netz-/Rate-Limit-Ausfall scheitern.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_einreichen_411 und wird als Parameter genannt.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_einreichen_411 import (  # noqa: F401 — ``welt`` ist ein Fixture
    GH_ATTRAPPE,
    SKILL,
    _commit,
    _einreichen,
    _gh_aufrufe,
    _git,
    _ist_vorfahr,
    _remote_main,
    _ticket_wt,
    welt,
)

from conftest import attrappe, stub_pfad

_VIEW = 'elif args[:2] == ["pr", "view"]:'
#: ``gh pr view`` scheitert so oft, wie ``EINREICHEN_PR_VIEW_KAPUTT`` sagt (Netz/Rate-Limit).
GH_VIEW_KAPUTT = GH_ATTRAPPE.replace(
    _VIEW,
    'elif args[:2] == ["pr", "view"] and sum(1 for z in log.read_text(encoding="utf-8").splitlines()'
    ' if json.loads(z)[:2] == ["pr", "view"]) <= int(os.environ.get("EINREICHEN_PR_VIEW_KAPUTT", "0")):\n'
    '    print("HTTP 502: Bad Gateway (https://api.github.com/graphql)", file=sys.stderr)\n'
    "    sys.exit(1)\n" + _VIEW,
)
STAND_UNBEKANNT = 2
PR_VIEW = ["pr", "view", "41", "--json", "state", "--jq", ".state"]


@pytest.fixture
def kaputte_abfrage(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    assert _VIEW in GH_ATTRAPPE, "gh-Attrappe aus #411 geändert — Ersetzung greift nicht"
    stub = attrappe(welt["tmp"] / "gh-bin-492", "gh", GH_VIEW_KAPUTT)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub_pfad(stub)))
    monkeypatch.setenv("EINREICHEN_SPERRE_NACH_MERGE", "1")
    return welt


def test_stand_abfrage_ohne_antwort_nach_merge_ist_nicht_rot(
    kaputte_abfrage: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge echt, Nachziehen gesperrt, ``pr view`` stumm → „Stand unbekannt“, kein „NICHT EINGEREICHT“."""
    welt = kaputte_abfrage
    monkeypatch.setenv("EINREICHEN_PR_VIEW_KAPUTT", "99")
    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "x\n", "docs: x (#7)")
    erg = _einreichen(wt, "7")
    assert _ist_vorfahr(welt["remote"], sha, _remote_main(welt)), "Attrappe muss echt gemergt haben"
    assert not erg.stdout.startswith("NICHT EINGEREICHT"), erg.stdout
    assert erg.stdout.startswith("Stand unbekannt – erneut prüfen"), erg.stdout
    assert erg.returncode == STAND_UNBEKANNT, erg.stdout + erg.stderr
    assert _gh_aufrufe(welt).count(PR_VIEW) == 3, "Stand-Abfrage wird kurz wiederholt"
    # „erneut prüfen“: derselbe Befehl reicht nicht doppelt ein, sondern erkennt den Merge
    sperre = Path(_git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "refs/remotes/origin/main.lock"
    sperre.unlink()
    zweit = _einreichen(wt, "7")
    assert zweit.returncode == 0, zweit.stdout + zweit.stderr
    assert "schon auf origin/main" in zweit.stdout
    assert sum(1 for a in _gh_aufrufe(welt) if a[:2] == ["pr", "merge"]) == 1


def test_stand_abfrage_scheitert_einmal_wird_wiederholt(
    kaputte_abfrage: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Aussetzer bei ``pr view``: zweiter Versuch antwortet MERGED → EINGEREICHT (#492)."""
    welt = kaputte_abfrage
    monkeypatch.setenv("EINREICHEN_PR_VIEW_KAPUTT", "1")
    wt = _ticket_wt(welt)
    _commit(wt, "README.md", "x\n", "docs: x (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert erg.stdout.startswith("EINGEREICHT"), erg.stdout
    assert _gh_aufrufe(welt).count(PR_VIEW) == 2


def test_vorlagen_sagen_was_bei_stand_unbekannt_zu_tun_ist() -> None:
    """Bau-Vorlage und Aufseher kennen Exit 2: erneut einreichen prüft nur, kein Stillstand (#492)."""
    sys.path.insert(0, str(SKILL / "skripte"))
    import wache

    bau = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    for text in (bau, wache.PROMPT):
        assert "Exit 2 → Stand unbekannt" in text
