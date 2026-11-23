"""Hausmeister-Bündel #234, #248, #247, #252.

- #234: Im Zweig-Pfad zählt eine Ticket-Nummer nur, wenn sie im Ticket-Repo des eigenen
  Repos gilt (dieselbe Regel wie im Ordner-Pfad, :func:`hausmeister._nummer_zaehlt`).
- #248: Eine Stelle für „Zweigname → Ticket-Nummer“ (``_ZWEIG_FORMEN``); Datums-Formen
  ergeben keine Nummer; ein Test je Form.
- #247: Nach dem Sichern wird ``sicherung/<N>`` per ``ls-remote`` am Server gegengeprüft;
  fehlt der Zweig dort, bleibt der Ordner.
- #252: Zeigt ``origin`` nach allen Umleitungen (gleich aus welcher Konfiguration) auf ein
  Ziel im Ordner selbst, ist nichts gesichert ⇒ fremdes Repo ⇒ bleibt.

Echte Git-Repos wie in den #176-/#235-Tests; gestellt ist nur ``gh``.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_skill_ordner_235 import _lauf, _vorgabe
from test_hausmeister_ticket_repo_176 import APP, SKILL, TAG, Repos, repos  # noqa: F401

from conftest import attrappe
from to_spawn import hausmeister

ECHT = f"https://github.com/{SKILL}"


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _auf_server(bare: Path, ref: str) -> str:
    return git(bare, "for-each-ref", "--format=%(objectname)", ref).strip()


# --- #248: eine Stelle, Datums-Formen ohne Nummer, ein Test je Form ------------------------


@pytest.mark.parametrize(
    ("zweig", "nr"),
    [
        ("ticket/1525-hausmeister", 1525),
        ("ticket/7", 7),
        ("fix/1406", 1406),
        ("wip/1368-stand", 1368),
        ("aufseher/1279-abschluss", 1279),
        ("fix-1292", 1292),
        ("abschluss-1248", 1248),
        ("l4/176-ticket-repo", 176),
        ("l1/214a-schritt", 214),
        ("l3/3b-1b-schritt", None),
        ("l1/p7f-x", None),
        ("main", None),
        ("fix/hausmeister-234", None),
    ],
)
def test_248_form_ergibt_nummer(zweig: str, nr: int | None) -> None:
    assert hausmeister._nummer(zweig, hausmeister._ZWEIG_FORMEN) == nr


@pytest.mark.parametrize(
    "zweig",
    [
        "l1/2026-11-21-stand",
        "l2/2026-11",
        "ticket/2026-11-21-abend",
        "fix/2026-11-21",
        "wip/2026-11",
        "aufseher/2026-11-22-nacht",
        "fix-2026-11-21",
        "abschluss-2026-11",
        "ticket/20261121",
        "wip/993675",
    ],
)
def test_248_datum_und_lange_zahl_ergeben_keine_nummer(zweig: str) -> None:
    assert hausmeister._nummer(zweig, hausmeister._ZWEIG_FORMEN) is None
    assert hausmeister._zweig_ticket(zweig) is None


@pytest.mark.parametrize(
    "zweig",
    ["ticket/1525-hausmeister", "ticket/7", "ticket/2026-11-21-abend", "ticket/20261121", "ticket/2026-x"],
)
def test_248_zweig_pfad_liest_dieselbe_stelle(zweig: str) -> None:
    """Zweig-Aufräumen hat keine eigene Teilregel: für ``ticket/…`` dieselbe Antwort."""
    assert hausmeister._zweig_ticket(zweig) == hausmeister._nummer(zweig, hausmeister._ZWEIG_FORMEN)


@pytest.mark.parametrize("zweig", ["l4/176-x", "fix/1406", "wip/1368", "fix-1292"])
def test_248_zweig_pfad_nur_ticket_zweige(zweig: str) -> None:
    assert hausmeister._zweig_ticket(zweig) is None


# --- #234: Zweig-Pfad prüft, ob die Nummer im eigenen Repo zählt ---------------------------


def _skill_unter_app(repos: Repos) -> None:
    """Skill-Eintrag mit Ticket-Repo App (widerspricht #135): Skill #251 offen, App #251 zu."""
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, APP)])
    repos.worktree(SKILL, 251)
    repos.ticket(SKILL, 251, "OPEN")
    repos.ticket(APP, 251, "CLOSED", 24 * 400)


def test_234_remote_zweig_bleibt_wenn_nummer_im_repo_nicht_zaehlt(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(hausmeister.ZWEIGE_SCHALTER, "1")
    _skill_unter_app(repos)
    bare = welt.tmp / "claude-skill-to-spawn.git"
    vorher = _auf_server(bare, "refs/heads/ticket/251-test")
    assert vorher

    r = hausmeister.zweig_aufraeumen(ECHT, "ticket/251-test", loeschen=True)

    assert r.art == "bleibt", r.grund
    assert _auf_server(bare, "refs/heads/ticket/251-test") == vorher
    assert not _auf_server(bare, "refs/heads/sicherung/251")


def test_234_lokaler_zweig_nicht_fertig_wenn_nummer_im_repo_nicht_zaehlt(welt: Welt, repos: Repos) -> None:
    _skill_unter_app(repos)

    fertig, _, grund, _ = hausmeister.zweig_fertig(repos.haupt[SKILL], "ticket/251-test", ort="lokal")

    assert not fertig, grund


def test_234_gegenprobe_eigenes_ticket_repo_zu_ist_fertig(welt: Welt, repos: Repos) -> None:
    repos.repo(SKILL)
    repos.maschine([(APP, APP), (SKILL, SKILL)])
    repos.worktree(SKILL, 252, gemergt=True)
    repos.ticket(SKILL, 252, "CLOSED", 48)

    fertig, fehlende, grund, _ = hausmeister.zweig_fertig(ECHT, "ticket/252-test")

    assert fertig and not fehlende, grund


# --- #247: Sicherung am Server gegenprüfen -------------------------------------------------


def _server_loescht_sicherungen(bare: Path) -> None:
    """Server nimmt ``sicherung/*`` an und entfernt den Zweig gleich wieder."""
    attrappe(
        bare / "hooks",
        "post-receive",
        """
        import subprocess
        import sys

        for zeile in sys.stdin:
            ref = zeile.split()[2]
            if ref.startswith("refs/heads/sicherung/"):
                subprocess.run(["git", "update-ref", "-d", ref], check=True)
        """,
    )


@pytest.mark.skipif(os.name == "nt", reason="post-receive-Haken als sh-Skript")
def test_247_server_entfernt_sicherung_ordner_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    bare = welt.tmp / "claude-skill-to-spawn.git"
    wt = repos.worktree(SKILL, 261)
    (wt / "ungesichert.txt").write_text("nur hier\n", encoding="utf-8")
    repos.ticket(SKILL, 261, "CLOSED", 48)
    _server_loescht_sicherungen(bare)

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert "sicherung/261" in r.grund, r.grund
    assert wt.is_dir() and (wt / "ungesichert.txt").is_file()


# --- #252: Umleitung der Maschine auf ein Ziel im Ordner -----------------------------------


def _klon_mit_versteck(welt: Welt, name: str) -> tuple[Path, Path]:
    """Klon von SKILL plus bare-Kopie in seinem eigenen ``.git`` (Ziel im Ordner selbst)."""
    wt = welt.heim / "wt" / name
    git(welt.tmp, "clone", "-q", ECHT, str(wt))
    git(wt, "config", "remote.origin.url", ECHT + ".git")
    versteck = wt / ".git" / "versteck.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(welt.tmp / "claude-skill-to-spawn.git"), str(versteck)], check=True
    )
    return wt, versteck


def _arbeit_pushen(wt: Path, zweig: str) -> str:
    git(wt, "fetch", "-q", "--prune", "origin")
    git(wt, "checkout", "-q", "-b", zweig)
    (wt / "arbeit.txt").write_text("nur hier\n", encoding="utf-8")
    git(wt, "add", "arbeit.txt")
    git(wt, "commit", "-q", "-m", "arbeit")
    git(wt, "push", "-q", "-u", "origin", zweig)
    return git(wt, "rev-parse", "HEAD").strip()


def test_252_umleitung_per_umgebung_auf_ziel_im_ordner_bleibt(
    welt: Welt, repos: Repos, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vorgabe(repos)
    wt, versteck = _klon_mit_versteck(welt, "skill-umgebung")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{versteck.as_posix()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", ECHT + ".git")
    sha = _arbeit_pushen(wt, "doku/stand-94")
    assert _auf_server(versteck, "refs/heads/doku/stand-94") == sha
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-umgebung"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir()


def test_252_umleitung_per_include_if_in_maschinen_konfig_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt, versteck = _klon_mit_versteck(welt, "skill-includeif")
    zusatz = welt.tmp / "umleitung.gitconfig"
    zusatz.write_text(f'[url "{versteck.as_posix()}"]\n\tinsteadOf = {ECHT}.git\n', encoding="utf-8")
    with (welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:
        f.write(f'[includeIf "gitdir:{wt.as_posix()}/"]\n\tpath = {zusatz.as_posix()}\n')
    sha = _arbeit_pushen(wt, "doku/stand-95")
    assert _auf_server(versteck, "refs/heads/doku/stand-95") == sha
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-includeif"]

    assert r.art == "bleibt" and "fremdes Repo" in r.grund, r
    assert wt.is_dir()


def test_252_ziel_im_ordner_auch_relativ_und_als_file_url(tmp_path: Path) -> None:
    ordner = tmp_path / "wt"
    (ordner / ".git").mkdir(parents=True)
    assert hausmeister._ziel_im_ordner(ordner, str(ordner / ".git" / "x.git"))
    assert hausmeister._ziel_im_ordner(ordner, f"file://{(ordner / 'x.git').as_posix()}")
    assert hausmeister._ziel_im_ordner(ordner, ".git/x.git")
    assert not hausmeister._ziel_im_ordner(ordner, str(tmp_path / "anders.git"))
    assert not hausmeister._ziel_im_ordner(ordner, ECHT)
    assert not hausmeister._ziel_im_ordner(ordner, f"git@github.com:{SKILL}.git")
