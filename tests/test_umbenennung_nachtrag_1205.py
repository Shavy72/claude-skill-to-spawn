"""Tests für #1205 — Umbenennungs-Vermerk in einem späteren Commit desselben Tickets.

Anlass: #1190 wurde bei jedem Schließen wieder geöffnet. Commit 6944a03 benannte in der
Bestandsdatei ``tests/test_bestand_nachholen_1060_weg.py`` (aus #1060) den Test
``test_schnittstelle_standard_aus_und_sechs_konten`` in
``test_schnittstelle_standard_aus_und_konten_aus_regel_datei`` um — ohne Vermerk
``test-umbenannt: alt -> neu`` (Regel #415). Ein gepushter Commit lässt sich nicht mehr
ändern, also muss ein späterer Commit desselben Tickets den Vermerk nachtragen können.

Eine stille Umbenennung ohne jeden Vermerk bleibt ein Verstoß (#213).

Echt laufen: ein temporäres Git-Repo und ``capo.regel_tests`` samt ``git show``.
Nichts ist gestellt; die Testdatei ist ein Auszug aus dem echten Stand vor 6944a03.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from to_spawn import capo

N = 1190
FREMD = 1205
DATEI = "tests/test_bestand_nachholen_1060_weg.py"
ALT = "test_schnittstelle_standard_aus_und_sechs_konten"
NEU = "test_schnittstelle_standard_aus_und_konten_aus_regel_datei"

KOERPER = (
    "(nachhol_welt: Netz) -> None:\n"
    '    assert bestand.NACHHOL_SCHALTER == "dm_funnel.nachholen"\n'
    '    assert not hasattr(bestand, "NACHHOL_KONTEN")\n'
    "    assert not bestand._nachhol_lage().gesperrt(PROFIL)\n"
    "    _regel_konten([])\n"
    "    assert bestand._nachhol_lage().gesperrt(PROFIL)\n"
)
NACHBAR = "\n\ndef test_bis_24h_schalter_an_genau_eine_nachricht(nachhol_welt: Netz) -> None:\n    assert True\n"
VORHER = f"def {ALT}{KOERPER}{NACHBAR}"
NACHHER = f"def {NEU}{KOERPER}{NACHBAR}"
VERMERK = f"test-umbenannt: {DATEI}::{ALT} -> {NEU}"


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


def _commit(repo: Path, nachricht: str, dateien: dict[str, str] | None = None) -> None:
    for name, inhalt in (dateien or {}).items():
        pfad = repo / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", nachricht)


def _repo_mit_stiller_umbenennung(tmp_path: Path) -> Path:
    """Bestandsdatei aus #1060, dann die stille Umbenennung im Ticket #1190."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _commit(repo, "test(dm-funnel): Bestand nachholen (#1060)", {DATEI: VORHER})
    _commit(
        repo,
        f"test(dm-funnel): Schalter- und Fehlerzweig beim Nachholen direkt prüfen (#{N})",
        {DATEI: NACHHER},
    )
    return repo


def _eigene(repo: Path) -> list[capo.Commit]:
    return capo.ticket_commits(capo.alle_commits(repo, "HEAD"), N)


def test_stille_umbenennung_in_bestandsdatei_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert ALT in verstoss.text


def test_nachgetragener_vermerk_im_selben_ticket_ist_kein_verlust(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    _commit(repo, f"chore(tests): Umbenennungs-Vermerk nachgetragen (#{N})\n\n{VERMERK}\n")
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_vermerk_aus_fremdem_ticket_zaehlt_nicht(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    _commit(repo, f"chore(tests): Vermerk im falschen Ticket (#{FREMD})\n\n{VERMERK}\n")
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert ALT in verstoss.text


def test_nachgetragener_vermerk_mit_falschem_ziel_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    falsch = f"test-umbenannt: {DATEI}::{ALT} -> test_gibt_es_nicht"
    _commit(repo, f"chore(tests): Vermerk mit falschem Ziel (#{N})\n\n{falsch}\n")
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert ALT in verstoss.text


def test_vermerk_fuer_andere_datei_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    anders = "tests/test_anders.py"
    _commit(
        repo,
        f"test: Umbenennung in anderer Datei (#{N})\n\ntest-umbenannt: {anders}::{ALT} -> {NEU}\n",
        {anders: f"def {NEU}():\n    assert True\n"},
    )
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert ALT in verstoss.text


def test_vermerk_in_frueherem_ticket_commit_zaehlt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _commit(repo, "test(dm-funnel): Bestand nachholen (#1060)", {DATEI: VORHER})
    _commit(repo, f"docs: Umbenennung angekündigt (#{N})\n\n{VERMERK}\n")
    _commit(repo, f"test(dm-funnel): stille Umbenennung (#{N})", {DATEI: NACHHER})
    assert capo.regel_tests(repo, N, _eigene(repo)) is None


def test_ein_vermerk_deckt_nur_seine_umbenennung(tmp_path: Path) -> None:
    zweit_alt, zweit_neu = "test_zweiter_alt", "test_zweiter_neu"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _commit(
        repo,
        "test: Bestand (#1060)",
        {DATEI: VORHER + f"\n\ndef {zweit_alt}():\n    assert 1\n"},
    )
    _commit(
        repo,
        f"test: zwei stille Umbenennungen (#{N})",
        {DATEI: NACHHER + f"\n\ndef {zweit_neu}():\n    assert 1\n"},
    )
    _commit(repo, f"chore(tests): Vermerk nur für einen (#{N})\n\n{VERMERK}\n")
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert zweit_alt in verstoss.text
    assert ALT not in verstoss.text


def test_umbenannt_mit_vermerk_dann_geloescht_bleibt_verstoss(tmp_path: Path) -> None:
    repo = _repo_mit_stiller_umbenennung(tmp_path)
    _commit(repo, f"chore(tests): Umbenennungs-Vermerk nachgetragen (#{N})\n\n{VERMERK}\n")
    _commit(repo, f"test: neuen Test wieder weg (#{N})", {DATEI: NACHBAR.lstrip()})
    verstoss = capo.regel_tests(repo, N, _eigene(repo))
    assert verstoss is not None
    assert ALT in verstoss.text
