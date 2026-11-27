"""Tests für #1210 — gescheitertes ``git show`` in ``_test_verluste`` ist „nicht prüfbar“.

Befund (Prüfpanel #1205): Scheiterte ``git show`` (Zeitablauf, kaputter Commit-Ref),
gab ``_test_verluste`` still ``[]`` zurück. ``regel_tests`` wertete das als „kein
Verlust“, ein echter Testverlust fiel weg und das Ticket blieb zu.

Soll: Warnung loggen und den Commit als „nicht prüfbar“ nach oben melden.

Echt laufen: ein temporäres Git-Repo und ``capo.regel_tests`` samt ``git show``.
Der kaputte Commit-Ref ist ein SHA, den es im Repo nicht gibt.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

from to_spawn import capo

N = 1210
KAPUTT = "0123456789abcdef0123456789abcdef01234567"


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


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    datei = repo / "tests" / "test_a.py"
    datei.parent.mkdir(parents=True)
    datei.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", "tests/test_a.py")
    _git(repo, "commit", "-q", "-m", f"test: a angelegt (#{N})")
    return repo


def test_kaputter_commit_ref_ist_nicht_pruefbar_statt_sauber(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo = _repo(tmp_path)
    eigene = [capo.Commit(KAPUTT, f"fix: kaputt (#{N})", 2_000_000_000)]
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        verstoss = capo.regel_tests(repo, N, eigene)
    assert verstoss is not None, "gescheitertes git show wurde still als sauber gewertet"
    assert verstoss.regel == "test_ersetzt"
    assert "nicht prüfbar" in verstoss.text
    assert KAPUTT[:7] in verstoss.text
    assert any("nicht prüfbar" in r.getMessage() and KAPUTT[:7] in r.getMessage() for r in caplog.records)


def test_lesbarer_commit_ohne_verlust_bleibt_sauber(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    assert eigene
    assert capo.regel_tests(repo, N, eigene) is None


def test_echter_verlust_und_nicht_pruefbar_werden_beide_gemeldet(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", f"test: a entfernt (#{N})")
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    eigene.append(capo.Commit(KAPUTT, f"fix: kaputt (#{N})", 1))
    verstoss = capo.regel_tests(repo, N, eigene)
    assert verstoss is not None
    assert "test_a" in verstoss.text
    assert "nicht prüfbar" in verstoss.text
    assert KAPUTT[:7] in verstoss.text


# --- #861 (aus App #1211): weitere git-Fehler in Netto-Prüfung & _dateien ------


def test_861_datei_an_spitze_trennt_fehlt_von_git_fehler(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Fehlende Datei im gültigen Ref liefert None ohne Warnung; kaputter Ref wirft _NichtPruefbar."""
    repo = _repo(tmp_path)
    kopf = _git(repo, "rev-parse", "HEAD")
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        assert capo._datei_an_spitze(repo, kopf, "tests/gibt_es_nicht.py") is None
    assert caplog.records == []

    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        with pytest.raises(capo._NichtPruefbar):
            capo._datei_an_spitze(repo, KAPUTT, "tests/test_a.py")
    assert caplog.records


def test_861_kein_netto_verlust_propagiert_git_fehler_an_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Git-Fehler beim Lesen der Basis-Datei gilt nicht still als „gab es vor dem Ticket nicht“."""
    repo = _repo(tmp_path)
    basis = _git(repo, "rev-parse", "HEAD")
    (repo / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", f"test: a entfernt (#{N})")
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    # Nur der ältere Commit hatte test_a (vor dem Ticket): jetzt simulieren wir einen git-Fehler
    # beim Lesen des Basis-Stands in _kein_netto_verlust.
    echt = capo._git

    def fehler_an_basis(r: Path, *args: str) -> tuple[int, str]:
        if args and args[0] == "show" and any(a.startswith(f"{basis}:") for a in args):
            return 128, ""
        if args and args[0] == "ls-tree" and basis in args:
            return 128, ""
        return echt(r, *args)

    monkeypatch.setattr(capo, "_git", fehler_an_basis)
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        verstoss = capo.regel_tests(repo, N, [eigene[0]])
    assert verstoss is not None
    assert verstoss.regel == "test_ersetzt"
    assert "nicht prüfbar" in verstoss.text
    assert caplog.records


def test_861_datei_neu_im_ticket_und_wieder_entfernt_bleibt_kein_verlust(tmp_path: Path) -> None:
    """Gegenprobe: Datei fehlte an der Basis wirklich → weiter kein Verlust."""
    repo = _repo(tmp_path)
    neu = repo / "tests" / "test_neu.py"
    neu.write_text("def test_kurz():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", "tests/test_neu.py")
    _git(repo, "commit", "-q", "-m", f"test: neu (#{N + 1})")
    neu.write_text("def test_anders():\n    assert True\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", f"test: umgebaut (#{N + 1})")
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N + 1)
    assert capo.regel_tests(repo, N + 1, eigene) is None


def test_861_dateien_git_fehler_im_testverlust_pfad_ist_nicht_pruefbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Scheitert ``git show --name-status`` in _dateien im Testverlust-Pfad → _NichtPruefbar statt []."""
    repo = _repo(tmp_path)
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    echt = capo._git

    def name_status_kaputt(r: Path, *args: str) -> tuple[int, str]:
        if "--name-status" in args:
            return 128, ""
        return echt(r, *args)

    monkeypatch.setattr(capo, "_git", name_status_kaputt)
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        verstoss = capo.regel_tests(repo, N, eigene)
    assert verstoss is not None
    assert verstoss.regel == "test_ersetzt"
    assert "nicht prüfbar" in verstoss.text
    assert caplog.records


def test_861_timeout_124_wiederholt_einmal_und_meldet_infrastruktur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Exit 124 wird genau einmal wiederholt; scheitert auch der zweite Versuch, steht Infrastruktur in der Meldung."""
    repo = _repo(tmp_path)
    eigene = capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)
    echt = capo._git

    # Fall 1: einmaliger Aussetzer (124), Wiederholung klappt → kein Verstoß.
    zaehler = {"n": 0}

    def einmal_timeout(r: Path, *args: str) -> tuple[int, str]:
        if args[:1] == ("show",) and "--unified=0" in args:
            zaehler["n"] += 1
            if zaehler["n"] == 1:
                return 124, ""
        return echt(r, *args)

    monkeypatch.setattr(capo, "_git", einmal_timeout)
    assert capo.regel_tests(repo, N, eigene) is None
    assert zaehler["n"] == 2

    # Fall 2: dauerhafter Timeout (124) → genau zwei Versuche, Meldung als Infrastruktur gekennzeichnet.
    zaehler["n"] = 0

    def dauer_timeout(r: Path, *args: str) -> tuple[int, str]:
        if args[:1] == ("show",) and "--unified=0" in args:
            zaehler["n"] += 1
            return 124, ""
        return echt(r, *args)

    monkeypatch.setattr(capo, "_git", dauer_timeout)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        verstoss = capo.regel_tests(repo, N, eigene)
    assert zaehler["n"] == 2
    assert verstoss is not None
    assert "nicht prüfbar" in verstoss.text
    assert "Infrastruktur" in verstoss.text
    assert caplog.records
