"""Hausmeister-Lücken #347 (W1–W4) und #254 — im Zweifel bleibt Zweig/Ordner liegen.

- W1: lokaler Zweig in einem Repo ohne GitHub-origin fragt nie das Ticket-Repo der Maschine.
- W2: ``_sicherung_auf_server`` — anderer Stand, fremder Ref mit gleichem Ende, ls-remote scheitert.
- W3: ``<präfix>/20JJ-<ziffer>…`` ist ein Datum, nie eine Ticket-Nummer (auch Monat 13).
- W4: Umleitung der Maschine auf ``ext::`` (Befehl statt Server) ist kein GitHub; auf einen
  Pfad außerhalb des Ordners (Spiegel der Maschine) bleibt sie erlaubt.
- #254: Umleitungs-Prüfung nicht lesbar ⇒ fremd; Reflog auf fehlendes Objekt,
  ``core.logAllRefUpdates=false`` und gefälschte Tracking-Refs ⇒ Ordner bleibt.

Echte Git-Repos wie in den #1368-/#176-Tests; gestellt ist nur ``gh`` (und in einem Test
die Ausgabe von ``git config --show-scope`` wie bei git < 2.26).
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

from pathlib import Path

import pytest
from test_hausmeister_1368 import ORIGIN, Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_ticket_repo_176 import APP, Repos, repos  # noqa: F401

from to_spawn import hausmeister


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _commit(ordner: Path, datei: str) -> str:
    (ordner / datei).write_text(f"{datei}\n", encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", datei)
    return git(ordner, "rev-parse", "HEAD").strip()


# --- W1: lokaler Zweig ohne GitHub-origin --------------------------------------------------


def test_w1_lokaler_zweig_ohne_github_origin_nie_nach_ticket_repo_der_maschine(welt: Welt, repos: Repos) -> None:
    repos.maschine([(APP, APP)])
    repos.ticket(APP, 271, "CLOSED", 48)
    klon = welt.tmp / "pfad-klon"
    git(welt.tmp, "clone", "-q", str(welt.bare), str(klon))
    git(klon, "checkout", "-q", "-b", "ticket/271-test")
    _commit(klon, "arbeit.txt")
    git(klon, "push", "-q", "-u", "origin", "ticket/271-test")
    git(klon, "checkout", "-q", "master")

    fertig, _, grund, _ = hausmeister.zweig_fertig(klon, "ticket/271-test", ort="lokal")

    assert not fertig, grund
    assert "zählt nicht" in grund, grund


# --- W2: Sicherung am Server gegenprüfen --------------------------------------------------


def test_w2_sicherung_auf_anderem_stand_bleibt(welt: Welt) -> None:
    alt = _commit(welt.haupt, "a.txt")
    neu = _commit(welt.haupt, "b.txt")
    git(welt.haupt, "push", "-q", "origin", f"{neu}:refs/heads/sicherung/7")

    hausmeister._sicherung_auf_server(welt.haupt, 7, neu)  # Gegenprobe: gleicher Stand passt
    with pytest.raises(hausmeister._Bleibt, match="sicherung/7"):
        hausmeister._sicherung_auf_server(welt.haupt, 7, alt)


def test_w2_nur_fremder_ref_mit_gleichem_ende_bleibt(welt: Welt) -> None:
    sha = _commit(welt.haupt, "a.txt")
    git(welt.haupt, "push", "-q", "origin", f"{sha}:refs/heads/alt/refs/heads/sicherung/8")

    with pytest.raises(hausmeister._Bleibt, match="sicherung/8"):
        hausmeister._sicherung_auf_server(welt.haupt, 8, sha)


def test_w2_ls_remote_scheitert_bleibt(welt: Welt) -> None:
    sha = _commit(welt.haupt, "a.txt")
    git(welt.haupt, "config", "remote.origin.url", str(welt.tmp / "gibt-es-nicht.git"))

    with pytest.raises(hausmeister._Bleibt, match="ls-remote") as b:
        hausmeister._sicherung_auf_server(welt.haupt, 9, sha)
    assert b.value.fehler


# --- W3: Datums-Formen --------------------------------------------------------------------


@pytest.mark.parametrize(
    "zweig",
    ["ticket/2026-13-x", "ticket/2012-3-teil", "fix/2030-0", "l1/2026-99-stand", "wip/2026-1"],
)
def test_w3_jahr_mit_ziffer_dahinter_ist_nie_eine_nummer(zweig: str) -> None:
    assert hausmeister._nummer(zweig, hausmeister._ZWEIG_FORMEN) is None


@pytest.mark.parametrize(("zweig", "nr"), [("ticket/2026-x", 2026), ("ticket/1999-3-teil", 1999)])
def test_w3_gegenprobe_jahreszahl_ohne_ziffer_dahinter_bleibt_nummer(zweig: str, nr: int) -> None:
    assert hausmeister._nummer(zweig, hausmeister._ZWEIG_FORMEN) == nr


# --- W4: Umleitung der Maschine -----------------------------------------------------------


def test_w4_umleitung_der_maschine_auf_ext_ist_kein_github(welt: Welt) -> None:
    klon = welt.klon("duoplus-ext")
    git(klon, "config", "remote.origin.url", ORIGIN + ".git")
    assert hausmeister._origin_slug(klon) == APP  # Gegenprobe: Spiegel-Pfad außerhalb gilt
    with (welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:  # längere Umleitung gewinnt
        f.write(f'[url "ext::git %s {welt.bare.as_posix()}"]\n\tinsteadOf = {ORIGIN}.git\n')

    assert hausmeister._origin_slug(klon) == ""


# --- #254: Umleitungs- und Reflog-Prüfung -------------------------------------------------


def test_254_umleitungs_pruefung_nicht_lesbar_gilt_als_fremd(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    klon = welt.klon("duoplus-altgit")
    git(klon, "config", f"url.{welt.bare.as_posix()}.insteadOf", ORIGIN)
    echt = hausmeister._git_oder_none

    def altes_git(cwd: Path, *args: str) -> str | None:
        if "--show-scope" in args:  # git < 2.26 kennt die Option nicht
            return None
        return echt(cwd, *args)

    monkeypatch.setattr(hausmeister, "_git_oder_none", altes_git)

    assert hausmeister._origin_slug(klon) == ""


def _verlorener_commit(wt: Path) -> str:
    """Commit, der nur noch im Reflog von ``HEAD`` steht (danach zurückgesetzt)."""
    sha = _commit(wt, "verloren.txt")
    git(wt, "reset", "-q", "--hard", "HEAD~1")
    return sha


def test_254_reflog_auf_fehlendes_objekt_ordner_bleibt(welt: Welt) -> None:
    wt = welt.worktree(281)
    welt.ticket(281, "CLOSED", 48)
    sha = _verlorener_commit(wt)
    objekt = Path(git(wt, "rev-parse", "--git-path", f"objects/{sha[:2]}/{sha[2:]}").strip())
    objekt = objekt if objekt.is_absolute() else wt / objekt
    objekt.chmod(0o644)
    objekt.unlink()

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert wt.is_dir()


def test_254_log_all_ref_updates_aus_ordner_bleibt(welt: Welt) -> None:
    git(welt.haupt, "config", "core.logAllRefUpdates", "false")
    wt = welt.worktree(282)
    welt.ticket(282, "CLOSED", 48)
    git(wt, "checkout", "-q", "--detach")
    _commit(wt, "verloren.txt")
    git(wt, "checkout", "-q", "ticket/282-test")

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert wt.is_dir()


def test_254_gefaelschter_tracking_ref_haelt_reflog_commit_nicht_fuer_gesichert(welt: Welt) -> None:
    wt = welt.worktree(283)
    welt.ticket(283, "CLOSED", 48)
    sha = _verlorener_commit(wt)
    # Refspec verengt: fetch --prune lässt den gefälschten Tracking-Ref stehen
    git(welt.haupt, "config", "remote.origin.fetch", "+refs/heads/master:refs/remotes/origin/master")
    git(welt.haupt, "update-ref", "refs/remotes/origin/faelschung", sha)

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt", r.grund
    assert wt.is_dir()
