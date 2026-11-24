# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""Runde 6: Eigener Commit einer per Skript geänderten Datei fehlt in der Stop-Liste.

Beleg 2026-09-24, Session 9b4c66fe: ``skills/to-spawn/skripte/bau.py`` wurde per Bash
(``python - <<EOF ... p.write_text(...)``) geändert, nie per Edit/Write → Merker kennt die
Datei nicht. Die Session committete sie selbst (82a90cb, ``git commit ... -- bau.py
sessions_stand.py test_review_pflicht_skills.py``). Stop-Hook Runde 2 meldete nur
``--files test_review_pflicht_skills.py sessions_stand.py``: bau.py landet in ``nur_committet``
und wird wegen der Runde-5-Regel („nur-committet zählt nur, wenn Merker kennt“) verworfen.

Runde-5-Absicht bleibt: fremde Commits paralleler Sessions zählen nicht
(``test_stop_runde5.test_fremder_commit_im_selben_ordner_zaehlt_nicht``).
Echte Hook-Prozesse (merker.py, commit.py, stop.py), echtes Git, keine Attrappen."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hooks import Umgebung, git, py_aendern, repo, u  # noqa: F401, E402


def _skript_aendern(pfad: Path) -> None:
    """Wie die Session per ``python - <<EOF``: Datei direkt schreiben, kein Edit-Werkzeug."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("PFLICHT_SKILLS = ['review-dirigent']\n", encoding="utf-8")


def _eigener_commit(u: Umgebung, repo: Path, *dateien: str) -> None:
    """Commit so, wie ihn die Session absetzt: Bash-Befehl läuft durch den commit.py-Hook
    (PreToolUse), danach echtes ``git commit`` mit Pathspec."""
    befehl = f"git add -f {' '.join(dateien)} && git commit -q -m bau -- {' '.join(dateien)}"
    u.commit(repo, befehl)
    git(repo, "add", "-f", *dateien)
    git(repo, "commit", "-q", "-m", "bau", "--", *dateien)


def test_eigener_commit_skript_datei_im_bekannten_ordner(u: Umgebung, repo: Path) -> None:
    """Runde 2 der Session: bau.py liegt im selben Ordner wie die gemerkte sessions_stand.py."""
    (repo / "skripte").mkdir()
    (repo / "skripte" / "sessions_stand.py").write_text("def stand():\n    return 1\n", encoding="utf-8")
    (repo / "skripte" / "bau.py").write_text("PFLICHT_SKILLS = []\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "basis")

    (repo / "skripte" / "sessions_stand.py").write_text("def stand():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "skripte/sessions_stand.py")  # Edit-Werkzeug → Merker kennt die Datei
    _skript_aendern(repo / "skripte" / "bau.py")  # Bash-Skript → Merker blind
    _eigener_commit(u, repo, "skripte/bau.py", "skripte/sessions_stand.py")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "skripte/sessions_stand.py" in aus["reason"]
    assert "skripte/bau.py" in aus["reason"], f"eigener Commit fehlt in --files: {aus['reason']}"


def test_eigener_commit_skript_datei_im_fremden_ordner(u: Umgebung, repo: Path) -> None:
    """Runde 1 der Session: gemerkt war nur tests/…, bau.py liegt in skripte/ → Pathspec
    der Elternordner schließt den Ordner aus, obwohl die Session selbst committet hat."""
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    _skript_aendern(repo / "skripte" / "bau.py")
    _eigener_commit(u, repo, "skripte/bau.py", "rechnen.py")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "rechnen.py" in aus["reason"]
    assert "skripte/bau.py" in aus["reason"], f"eigener Commit fehlt in --files: {aus['reason']}"


def test_eigener_commit_ohne_pathspec_nimmt_index(u: Umgebung, repo: Path) -> None:
    """`git add` vorab, dann `git commit -m` ohne `--`: der Hook liest die gestagten Dateien."""
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    _skript_aendern(repo / "skripte" / "bau.py")
    git(repo, "add", "skripte/bau.py", "rechnen.py")
    u.commit(repo, "git commit -q -m bau")
    git(repo, "commit", "-q", "-m", "bau")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "skripte/bau.py" in aus["reason"], f"gestagte Datei fehlt in --files: {aus['reason']}"


def _commit_aus_fremdem_cwd(u: Umgebung, cwd: Path, befehl: str) -> None:
    code, _ = u.hook(
        "commit.py",
        {"session_id": "s1", "cwd": str(cwd), "tool_name": "Bash", "tool_input": {"command": befehl}},
    )
    assert code == 0


def test_eigener_commit_mit_cd_davor(u: Umgebung, repo: Path) -> None:
    """Session-cwd ist ein anderes Repo, Befehl `cd <repo> && git commit -- …` (Beleg 2026-09-24,
    Session 7ae7722b: Pfade landeten im Session-Repo duoplus-management statt in ~/.claude)."""
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    _skript_aendern(repo / "skripte" / "bau.py")
    anderes = u.tmp / "anderes"
    anderes.mkdir()
    git(anderes, "init", "-q")
    for schreibweise in (repo.as_posix(), f'"{repo}"'):
        _commit_aus_fremdem_cwd(u, anderes, f"cd {schreibweise} && git add -f skripte/bau.py && git commit -q -m bau -- skripte/bau.py")
    git(repo, "add", "-f", "skripte/bau.py")
    git(repo, "commit", "-q", "-m", "bau", "--", "skripte/bau.py")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "skripte/bau.py" in aus["reason"], f"Commit nach cd fehlt in --files: {aus['reason']}"
