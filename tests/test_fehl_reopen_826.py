"""Tests für #826 — ``regel_tests`` zählt nur den Netto-Verlust eines Tickets.

Anlass: #803 wurde bei jedem Schließen wieder geöffnet. Commit c409fc2 entfernte
``test_e11_alte_pruefung_am_dran_tag`` u. a., spätere Commits desselben Tickets
ergänzten sie wieder in derselben Datei — der Verstoß ``test_ersetzt`` blieb trotzdem.

Echt laufen: ein temporäres Git-Repo und ``capo.regel_tests`` samt ``git show``.
Nichts ist gestellt.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from to_spawn import capo

N = 826
TEST_A = "def test_a():\n    assert True\n"
TEST_AB = TEST_A + "\n\ndef test_b():\n    assert True\n"


def _git(repo: Path, *args: str) -> str:
    umgebung = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env=umgebung,
        timeout=60,
    )
    return fertig.stdout.strip()


def _commit(repo: Path, betreff: str, dateien: dict[str, str | None]) -> None:
    """Dateien schreiben (``None`` = löschen) und committen."""
    for name, inhalt in dateien.items():
        if inhalt is None:
            _git(repo, "rm", "-q", name)
            continue
        pfad = repo / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", betreff)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _commit(repo, "test: Grundstock", {"tests/test_x.py": TEST_AB})
    return repo


def _eigene(repo: Path) -> list[capo.Commit]:
    return capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)


def test_wieder_ergaenzter_test_ist_kein_verlust(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, f"feat: Umbau (#{N})", {"tests/test_x.py": TEST_A})
    _commit(repo, f"fix: Test zurück (#{N})", {"tests/test_x.py": TEST_AB})
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_geloeschte_und_wieder_angelegte_testdatei_ist_kein_verlust(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "test: leere Datei", {"tests/test_leer.py": "# leer\n"})
    _commit(repo, f"feat: Aufräumen (#{N})", {"tests/test_leer.py": None})
    _commit(repo, f"fix: Datei zurück (#{N})", {"tests/test_leer.py": "# leer\n"})
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_entfernter_test_ohne_rueckkehr_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, f"feat: Umbau (#{N})", {"tests/test_x.py": TEST_A})
    _commit(repo, f"fix: Nachtrag (#{N})", {"src.py": "x = 1\n"})
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert verstoss.regel == "test_ersetzt"
    assert "test_b" in verstoss.text


def test_rueckkehr_in_anderer_datei_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, f"feat: Umbau (#{N})", {"tests/test_x.py": TEST_A})
    _commit(
        repo,
        f"fix: woanders (#{N})",
        {"tests/test_y.py": "def test_b():\n    assert True\n"},
    )
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert "test_b" in verstoss.text


JS_ALT = "it('Lead -> Meta CAPI sendet', () => {});\n"
JS_NEU = "it('RechnerAnfrage -> Meta CAPI sendet', () => {});\n"


def test_js_umbenennung_mit_vermerk_ist_kein_verlust(tmp_path: Path) -> None:
    """traffic-panda #45: ``test-umbenannt`` gilt auch für ``it('…')``-Namen."""
    repo = _repo(tmp_path)
    _commit(repo, "test: JS", {"tests/capi.test.js": JS_ALT})
    _commit(
        repo,
        f"feat: Umbenennung (#{N})\n\ntest-umbenannt: 'Lead -> Meta CAPI sendet' -> "
        "'RechnerAnfrage -> Meta CAPI sendet'",
        {"tests/capi.test.js": JS_NEU},
    )
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_js_umbenennung_ohne_vermerk_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "test: JS", {"tests/capi.test.js": JS_ALT})
    _commit(repo, f"feat: still (#{N})", {"tests/capi.test.js": JS_NEU})
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert "Lead -> Meta CAPI sendet" in verstoss.text


def test_im_ticket_angelegt_und_ersetzt_ist_kein_verlust(tmp_path: Path) -> None:
    """traffic-panda #45: Test in f18ce92 angelegt, in 677814c strenger ersetzt."""
    repo = _repo(tmp_path)
    _commit(repo, f"test: erster Wurf (#{N})", {"tests/capi.test.js": JS_ALT})
    _commit(repo, f"test: strenger (#{N})", {"tests/capi.test.js": JS_NEU})
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_js_test_aus_basis_entfernt_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "test: JS", {"tests/capi.test.js": JS_ALT + JS_NEU})
    _commit(repo, f"feat: weg (#{N})", {"tests/capi.test.js": JS_NEU})
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert "Lead -> Meta CAPI sendet" in verstoss.text
