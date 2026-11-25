"""#602 Teil B: Die GraphQL-Abfrage des Briefkasten-Weckers geht echt über ``gh.lauf``.

Gestellt ist nur GitHub: ``gh`` ist eine Programm-Attrappe (``conftest.attrappe``) über die
vorhandene Test-Tür ``TO_SPAWN_GH_STUB``. Sie schreibt ihre Argumente mit und antwortet wie
``gh api graphql``. Der Weg Wecker → ``gh.lauf`` → Kindprozess ist echt, auf jedem Betriebssystem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import attrappe, stub_pfad
from to_spawn import briefkasten_wecker

FAKE_GH = """
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_GH_PROTOKOLL"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print(json.dumps({"data": {"repository": {
    "t11": {"state": "OPEN", "comments": {"nodes": []}},
    "t12": {"state": "CLOSED", "comments": {"nodes": [{"databaseId": 7, "body": "ä", "authorAssociation": "OWNER"}]}},
}}}))
"""


def test_graphql_abfrage_laeuft_ueber_echten_gh_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abfrage-Text kommt unverändert beim gh-Prozess an, die Antwort wird je Ticket gelesen."""
    protokoll = tmp_path / "gh-argv.json"
    stub = attrappe(tmp_path / "bin", "gh", FAKE_GH)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub_pfad(stub)))
    monkeypatch.setenv("FAKE_GH_PROTOKOLL", str(protokoll))
    w = briefkasten_wecker.Wecker(4140, tmp_path, "%77", gh_repo="test-org/test-repo")

    lage = w._lage({"11": {}, "12": {}})

    assert lage is not None, "GraphQL-Abfrage über gh.lauf gescheitert"
    assert lage["11"]["state"] == "OPEN"
    assert lage["12"]["comments"]["nodes"][0]["body"] == "ä"
    argv = json.loads(protokoll.read_text(encoding="utf-8"))
    assert argv[:2] == ["api", "graphql"]
    assert "owner=test-org" in argv and "name=test-repo" in argv
    abfrage = next(a for a in argv if a.startswith("query="))
    assert "query($owner: String!, $name: String!)" in abfrage
    assert "t11: issue(number: 11) { state comments(last: " in abfrage
