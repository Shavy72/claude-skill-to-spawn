"""#542: Einrichten trägt ``ticket_repo`` nur mit echtem Beleg ein (gh: Issues an), nie geraten (#66).

``gh`` ist ein Stub-Skript (externer Dienst), alles andere echt: git-Repo, Maschinen-Liste im tmp-Ordner.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, einrichten

SLUG = "Shavy72/probe-542"


def _welt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gh_code: int, gh_text: str
) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for a in (
        ["init", "-q"],
        ["remote", "add", "origin", f"https://github.com/{SLUG}.git"],
    ):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)
    stub = tmp_path / "gh_stub.py"
    stub.write_text(
        f"import sys\nprint({gh_text!r})\nsys.exit({gh_code})\n", encoding="utf-8"
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(tmp_path / "maschine.json"))
    return repo


def _eintrag(repo: Path) -> dict[str, str]:
    return next(
        e
        for e in config.maschine()["repos"]
        if Path(e["pfad"]).resolve() == repo.resolve()
    )


def _anlegen(repo: Path) -> str:
    aus = io.StringIO()
    einrichten._lege_maschine_an(einrichten.Ort(repo, aus))
    return aus.getvalue()


def test_a_beleg_da_wird_eingetragen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _welt(
        tmp_path,
        monkeypatch,
        0,
        json.dumps({"hasIssuesEnabled": True, "nameWithOwner": SLUG}),
    )
    assert "ticket_repo fehlt" not in _anlegen(repo)
    assert _eintrag(repo)["ticket_repo"] == SLUG


def test_b_gh_fehler_bleibt_leer_mit_hinweis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _welt(tmp_path, monkeypatch, 1, "")
    assert "ticket_repo fehlt" in _anlegen(repo)
    assert _eintrag(repo)["ticket_repo"] == ""


def test_c_issues_aus_bleibt_leer_mit_hinweis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _welt(
        tmp_path,
        monkeypatch,
        0,
        json.dumps({"hasIssuesEnabled": False, "nameWithOwner": SLUG}),
    )
    assert "ticket_repo fehlt" in _anlegen(repo)
    assert _eintrag(repo)["ticket_repo"] == ""


def test_d_vorhandener_wert_bleibt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _welt(
        tmp_path,
        monkeypatch,
        0,
        json.dumps({"hasIssuesEnabled": True, "nameWithOwner": SLUG}),
    )
    (tmp_path / "maschine.json").write_text(
        json.dumps(
            {
                "repos": [
                    {"slug": SLUG, "pfad": str(repo), "ticket_repo": "o/eigene-tickets"}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert "ticket_repo fehlt" not in _anlegen(repo)
    assert _eintrag(repo)["ticket_repo"] == "o/eigene-tickets"
