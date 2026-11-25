"""#638: Umlaut-Dateinamen im Abbruch-Text von ``arbeitsstand`` + Timeout in ``gh.hauptzweig``.

Mit echtem ``git`` (kein Stub):

1. ``arbeitsstand.sicherstellen``: eine lokal geänderte (oder unversionierte) Datei ``ä.txt``,
   die beim Nachziehen überschrieben würde, muss im Abbruch-Text als ``ä.txt`` stehen —
   nicht als Oktal-Folge ``\\303\\244`` (auch wenn im Repo ``core.quotepath=true`` gilt).
2. ``gh.hauptzweig``: alle drei lokalen ``git``-Aufrufe (``symbolic-ref`` und ``rev-parse``
   für ``origin/main`` / ``origin/master``) laufen mit Zeitgrenze ``gh.GIT_ZEIT_S``.
"""

# ruff: noqa: F811 — Fixture ``klon`` kommt aus dem Nachbar-Test und wird als Parameter genannt.

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_arbeitsstand_283 import _git, _quelle_schiebt, klon  # noqa: F401

from to_spawn import arbeitsstand, gh


def test_kollidierende_umlaut_datei_im_abbruch_text_lesbar(klon: Path) -> None:
    """Lokal geänderte ``ä.txt`` steht lesbar im Abbruch-Text, nicht oktal maskiert."""
    _quelle_schiebt(klon, "ä.txt", "alt")
    _git(klon, "pull", "-q", "--ff-only", "origin", "main")
    _quelle_schiebt(klon, "ä.txt", "neu von origin")
    # Explizit aktivieren, damit der Test unabhängig von globalem ~/.gitconfig bleibt.
    _git(klon, "config", "core.quotepath", "true")
    (klon / "ä.txt").write_text("lokal geändert", encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")

    stand = arbeitsstand.sicherstellen(klon)

    assert stand.zustand == arbeitsstand.ABBRUCH and not stand.ok, stand.text
    assert "ä.txt" in stand.text, stand.text
    assert r"\303\244" not in stand.text, stand.text
    assert (klon / "ä.txt").read_text(encoding="utf-8") == "lokal geändert"
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_unversionierte_umlaut_datei_im_abbruch_text_lesbar(klon: Path) -> None:
    """Unversionierte ``ä.txt``, die origin neu einführt, steht lesbar im Abbruch-Text."""
    _quelle_schiebt(klon, "ä.txt", "neu von origin")
    _git(klon, "config", "core.quotepath", "true")
    (klon / "ä.txt").write_text("lokal unversioniert", encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")

    stand = arbeitsstand.sicherstellen(klon)

    assert stand.zustand == arbeitsstand.ABBRUCH and not stand.ok, stand.text
    assert "ä.txt" in stand.text, stand.text
    assert r"\303\244" not in stand.text, stand.text
    assert (klon / "ä.txt").read_text(encoding="utf-8") == "lokal unversioniert"
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_arbeitsstand_git_schaltet_core_quotepath_aus(klon: Path) -> None:
    """``arbeitsstand._git`` schaltet ``core.quotepath`` ab, auch wenn das Repo es setzt."""
    _git(klon, "config", "core.quotepath", "true")
    (klon / "ä.txt").write_text("neu", encoding="utf-8")

    code, aus = arbeitsstand._git(klon, "status", "--porcelain")

    assert code == 0, aus
    assert "ä.txt" in aus, aus
    assert r"\303\244" not in aus, aus


def test_gh_hauptzweig_setzt_timeout_fuer_alle_drei_git_aufrufe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alle drei lokalen git-Aufrufe in ``gh.hauptzweig`` laufen mit ``gh.GIT_ZEIT_S``."""
    repo = tmp_path / "leer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    # Kein origin → alle drei Prüfungen (origin/HEAD, origin/main, origin/master) laufen durch.

    original_run = subprocess.run
    gesehen: list[tuple[list[str], Any]] = []

    def beobachtend(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        gesehen.append((list(cmd), kwargs.get("timeout")))
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(gh.subprocess, "run", beobachtend)

    assert gh.hauptzweig(repo) == "master"
    assert len(gesehen) == 3, gesehen
    assert all(timeout == gh.GIT_ZEIT_S for _, timeout in gesehen), gesehen
