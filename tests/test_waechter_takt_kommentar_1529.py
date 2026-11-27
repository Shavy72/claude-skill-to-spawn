"""Tests für #899 (aus App #1529, Folge #1522): Aufseher-Kommentare über CLI-Kürz-Stelle.

Prüft:
1. Der Aufseher-Prompt (``waechter_takt._PROMPT_ROH`` / ``PROMPT``) enthält kein direktes
   ``gh issue comment`` und kein ``gh issue reopen``, sondern verweist auf ``to_spawn.py kommentiere``.
2. Der CLI-Unterbefehl ``to_spawn.py kommentiere <N> <text>`` (mit optionalem ``--reopen``)
   läuft zwingend durch :func:`to_spawn.befund.kommentiere` inkl. 2-Zeilen-Kürzung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from to_spawn import befund, cli_befehle, cli_parser, gh, waechter_takt


def _cli(argv: list[str]) -> int:
    ap = cli_parser.baue_parser()
    return cli_befehle.verteilen(ap, ap.parse_args(argv))


GH_REPO = "test/wegwerf"


class FakeGh:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.aufrufe: list[list[str]] = []

    def lauf(self, args: list[str], cwd: Path | None = None, timeout: float | None = None) -> tuple[int, str]:
        self.aufrufe.append(list(args))
        return self.code, ""


@pytest.fixture()
def fake_gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(gh, "lauf", fake.lauf)
    befund._gesendet.clear()
    return fake


def test_aufseher_prompt_kein_direktes_gh_issue_comment_oder_reopen() -> None:
    for text in (waechter_takt._PROMPT_ROH, waechter_takt.PROMPT):
        assert "gh issue comment" not in text, "Aufseher-Prompt darf kein direktes `gh issue comment` enthalten (#899)"
        assert "gh issue reopen" not in text, "Aufseher-Prompt darf kein direktes `gh issue reopen` enthalten (#899)"
        assert "to_spawn.py kommentiere" in text, "Aufseher-Prompt muss auf `to_spawn.py kommentiere` verweisen (#899)"


def test_cli_kommentiere_laeuft_durch_befund_kommentiere_mit_kuerzung(
    fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "Aufseher: Zeile 1\nZeile 2\nZeile 3\nZeile 4"
    code = _cli(["kommentiere", "42", text, "--gh-repo", GH_REPO])
    assert code == 0
    (aufruf,) = fake_gh.aufrufe
    assert aufruf == [
        "issue",
        "comment",
        "42",
        "--repo",
        GH_REPO,
        "--body",
        "Aufseher: Zeile 1\nZeile 2 … +2 Zeilen gekürzt",
    ]
    assert "#42 kommentiert" in capsys.readouterr().out


def test_cli_kommentiere_mit_reopen_kuerzt_ebenfalls(fake_gh: FakeGh, capsys: pytest.CaptureFixture[str]) -> None:
    text = "Aufseher: Beleg fehlt\nDetail 2\nDetail 3"
    code = _cli(["kommentiere", "42", text, "--reopen", "--gh-repo", GH_REPO])
    assert code == 0
    (aufruf,) = fake_gh.aufrufe
    assert aufruf == [
        "issue",
        "reopen",
        "42",
        "--repo",
        GH_REPO,
        "--comment",
        "Aufseher: Beleg fehlt\nDetail 2 … +1 Zeilen gekürzt",
    ]
    assert "#42 wieder geöffnet" in capsys.readouterr().out


def test_cli_kommentiere_fehler_bei_gh_abbruch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGh(code=1)
    monkeypatch.setattr(gh, "lauf", fake.lauf)
    befund._gesendet.clear()
    code = _cli(["kommentiere", "42", "Aufseher: kurz", "--gh-repo", GH_REPO])
    assert code == 1


def test_cli_kommentiere_ohne_gh_repo_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "repo_aus_origin", lambda _repo: None)
    code = _cli(["kommentiere", "42", "Aufseher: kurz"])
    assert code == 2
