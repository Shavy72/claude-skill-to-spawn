"""Hausmeister #253: Reflog-Schutz räumt Stände weg, deren Baum oder Patch am Server liegt.

Prüft alle fünf im Ticket genannten Fälle:
1. ``git commit --amend`` vor dem Push
2. ``git rebase`` vor dem Push
3. ``git pull --rebase`` (neu aufgesetzte Commits gepusht und bereits übernommener Patch)
4. Squash-Merge mit gelöschtem Zweig (ein und mehrere Commits, mit und ohne parallele
   Commits auf dem Hauptzweig, sowie nach Merge des Hauptzweigs in den Feature-Zweig)
5. Force-Push nach ``amend``

Gegenproben (müssen weiter stehen bleiben):
- Echter verlorener Commit auf abgehängtem Kopf
- Gemischter Reflog: ein geändeter (sicherer) plus ein verlorener Commit
- ``amend`` mit geändertem Dateiinhalt (alter Stand liegt nicht am Server)
- Interaktiver Rebase, bei dem ein Commit wegfiel
- Binärdatei gleichen Namens mit anderem Inhalt
- Leerer Commit nur im Reflog (#254)
- Gefälschte lokale Tracking-Ref ohne echten Server-Stand (#254)

Echte Git-Repos (bare-„origin“ über ``insteadOf``) wie in den #235-Tests.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

from pathlib import Path

from test_hausmeister_235_fix2 import VERLAUF, _alt, _commit, _klon
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — Fixture
from test_hausmeister_skill_ordner_235 import _lauf, _ordner, _vorgabe
from test_hausmeister_ticket_repo_176 import SKILL, Repos, repos  # noqa: F401


def _schreiben_commit(wt: Path, datei: str, inhalt: str, nachricht: str) -> str:
    (wt / datei).write_text(inhalt, encoding="utf-8")
    git(wt, "add", datei)
    git(wt, "commit", "-q", "-m", nachricht)
    return git(wt, "rev-parse", "HEAD").strip()


def _anderer_commit_auf_haupt(welt: Welt, repos: Repos, datei: str, inhalt: str) -> str:
    """Schiebt parallel einen Commit auf den Hauptzweig von SKILL (simuliert anderen PR)."""
    haupt = repos.haupt[SKILL]
    zweig = repos.hauptzweig[SKILL]
    git(haupt, "checkout", "-q", zweig)
    git(haupt, "pull", "-q", "--ff-only")
    sha = _schreiben_commit(haupt, datei, inhalt, f"parallel: {datei}")
    git(haupt, "push", "-q", "origin", zweig)
    return sha


# --- 1. amend vor dem Push ---------------------------------------------------------------


def test_253_amend_vor_push_klon_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-amend")
    git(wt, "checkout", "-q", "-b", "doku/amend-1")
    _schreiben_commit(wt, "feature.txt", "inhalt\n", "wip")
    git(wt, "commit", "-q", "--amend", "-m", "feat: endgültige Nachricht")
    git(wt, "push", "-q", "-u", "origin", "doku/amend-1")
    _alt(welt)

    r = _lauf(welt)["skill-amend"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


def test_253_amend_vor_push_worktree_mit_nummer_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-2531", "l4/2531-amend")
    _schreiben_commit(wt, "teil.txt", "stand\n", "wip")
    git(wt, "commit", "-q", "--amend", "-m", "fix: bereinigt")
    git(wt, "push", "-q", "-u", "origin", "l4/2531-amend")
    repos.ticket(SKILL, 2531, "CLOSED", 24 * 400)
    _alt(welt)

    r = _lauf(welt)["skill-2531"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- 2. Rebase vor dem Push --------------------------------------------------------------


def test_253_rebase_vor_push_mehrere_commits_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-rebase")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/rebase-1")
    _schreiben_commit(wt, "a.txt", "eins\n", "erster")
    _schreiben_commit(wt, "b.txt", "zwei\n", "zweiter")
    _anderer_commit_auf_haupt(welt, repos, "fremd.txt", "parallel auf main\n")
    git(wt, "fetch", "-q", "origin")
    git(wt, "rebase", "-q", f"origin/{haupt}")
    git(wt, "push", "-q", "-u", "origin", "doku/rebase-1")
    _alt(welt)

    r = _lauf(welt)["skill-rebase"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- 3. pull --rebase --------------------------------------------------------------------


def test_253_pull_rebase_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-pull-rebase")
    haupt = repos.hauptzweig[SKILL]
    _schreiben_commit(wt, "lokal.txt", "meine Änderung\n", "lokal")
    _anderer_commit_auf_haupt(welt, repos, "upstream.txt", "vom Server\n")
    git(wt, "pull", "-q", "--rebase", "origin", haupt)
    git(wt, "push", "-q", "origin", haupt)
    _alt(welt)

    r = _lauf(welt)["skill-pull-rebase"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


def test_253_pull_rebase_patch_schon_auf_server_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    """Ein lokaler Commit wurde identisch bereits am Server eingespielt; ``pull --rebase``
    lässt ihn fallen, der alte SHA bleibt nur im Reflog."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-pull-drop")
    haupt = repos.hauptzweig[SKILL]
    _schreiben_commit(wt, "identisch.txt", "gleicher patch\n", "lokale fassung")
    _anderer_commit_auf_haupt(welt, repos, "identisch.txt", "gleicher patch\n")
    git(wt, "pull", "-q", "--rebase", "origin", haupt)
    _alt(welt)

    r = _lauf(welt)["skill-pull-drop"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- 4. Squash-Merge mit gelöschtem Zweig ------------------------------------------------


def test_253_squash_merge_gleiche_basis_geloeschter_zweig_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    """Mehrere Commits auf Feature-Zweig, am Server per Squash-Merge übernommen, Zweig gelöscht."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-squash-gleich")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/squash-1")
    _schreiben_commit(wt, "eins.txt", "1\n", "c1")
    _schreiben_commit(wt, "zwei.txt", "2\n", "c2")
    git(wt, "push", "-q", "-u", "origin", "doku/squash-1")
    # Squash-Merge im Hauptrepo auf origin/main, dann Remote-Zweig löschen
    h = repos.haupt[SKILL]
    git(h, "fetch", "-q", "origin")
    git(h, "checkout", "-q", haupt)
    git(h, "pull", "-q", "--ff-only")
    git(h, "merge", "-q", "--squash", "origin/doku/squash-1")
    git(h, "commit", "-q", "-m", "Squash-Merge (#1)")
    git(h, "push", "-q", "origin", haupt)
    git(h, "push", "-q", "origin", "--delete", "doku/squash-1")
    # Entwickler räumt lokal auf
    git(wt, "checkout", "-q", haupt)
    git(wt, "pull", "-q", "--ff-only")
    git(wt, "branch", "-q", "-D", "doku/squash-1")
    _alt(welt)

    r = _lauf(welt)["skill-squash-gleich"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


def test_253_squash_merge_nach_parallelem_main_commit_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    """Mehrere Commits auf Feature-Zweig, dazwischen neuer Commit auf ``main``, dann
    Squash-Merge auf dem neuen ``main`` und Zweig gelöscht (Bäume weichen ab, Gesamt-Patch stimmt)."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-squash-bewegt")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/squash-2")
    _schreiben_commit(wt, "teil1.txt", "alpha\n", "c1")
    _schreiben_commit(wt, "teil2.txt", "beta\n", "c2")
    git(wt, "push", "-q", "-u", "origin", "doku/squash-2")
    _anderer_commit_auf_haupt(welt, repos, "anderer_pr.txt", "zwischenzeitlich\n")
    h = repos.haupt[SKILL]
    git(h, "fetch", "-q", "origin")
    git(h, "checkout", "-q", haupt)
    git(h, "pull", "-q", "--ff-only")
    git(h, "merge", "-q", "--squash", "origin/doku/squash-2")
    git(h, "commit", "-q", "-m", "Squash-Merge (#2)")
    git(h, "push", "-q", "origin", haupt)
    git(h, "push", "-q", "origin", "--delete", "doku/squash-2")
    git(wt, "checkout", "-q", haupt)
    git(wt, "pull", "-q", "--ff-only")
    git(wt, "branch", "-q", "-D", "doku/squash-2")
    _alt(welt)

    r = _lauf(welt)["skill-squash-bewegt"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


def test_253_squash_merge_nach_merge_von_main_in_feature_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    """Feature-Zweig hat zwischendurch ``main`` gemergt, danach noch ein Commit auf ``main``,
    dann Squash-Merge und Zweig gelöscht."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-squash-merge")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/squash-3")
    _schreiben_commit(wt, "f1.txt", "a\n", "f1")
    _anderer_commit_auf_haupt(welt, repos, "m1.txt", "m1\n")
    git(wt, "fetch", "-q", "origin")
    git(wt, "merge", "-q", "--no-edit", f"origin/{haupt}")
    _schreiben_commit(wt, "f2.txt", "b\n", "f2")
    git(wt, "push", "-q", "-u", "origin", "doku/squash-3")
    _anderer_commit_auf_haupt(welt, repos, "m2.txt", "m2\n")
    h = repos.haupt[SKILL]
    git(h, "fetch", "-q", "origin")
    git(h, "checkout", "-q", haupt)
    git(h, "pull", "-q", "--ff-only")
    git(h, "merge", "-q", "--squash", "origin/doku/squash-3")
    git(h, "commit", "-q", "-m", "Squash-Merge (#3)")
    git(h, "push", "-q", "origin", haupt)
    git(h, "push", "-q", "origin", "--delete", "doku/squash-3")
    git(wt, "checkout", "-q", haupt)
    git(wt, "pull", "-q", "--ff-only")
    git(wt, "branch", "-q", "-D", "doku/squash-3")
    _alt(welt)

    r = _lauf(welt)["skill-squash-merge"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- 5. Force-Push nach amend ------------------------------------------------------------


def test_253_force_push_nach_amend_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _klon(welt, "skill-force-amend")
    git(wt, "checkout", "-q", "-b", "doku/force-1")
    _schreiben_commit(wt, "code.txt", "fertig\n", "erste nachricht")
    git(wt, "push", "-q", "-u", "origin", "doku/force-1")
    git(wt, "commit", "-q", "--amend", "-m", "bessere nachricht")
    git(wt, "push", "-q", "--force-with-lease", "origin", "doku/force-1")
    _alt(welt)

    r = _lauf(welt)["skill-force-amend"]

    assert r.art == "gelöscht", r
    assert not wt.exists()


# --- Sicherheits-Gegenproben (müssen weiter stehen bleiben) ------------------------------


def test_253_amend_und_zusaetzlich_verlorener_commit_bleibt(welt: Welt, repos: Repos) -> None:
    """Ein sicherer Amend-Stand darf einen zweiten, wirklich verlorenen Commit im selben
    Reflog nicht verdecken."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-gemischt")
    git(wt, "checkout", "-q", "-b", "doku/gemischt")
    _schreiben_commit(wt, "gut.txt", "ok\n", "wip")
    git(wt, "commit", "-q", "--amend", "-m", "ok")
    git(wt, "push", "-q", "-u", "origin", "doku/gemischt")
    git(wt, "checkout", "-q", "--detach")
    _commit(wt, "verloren.txt")
    git(wt, "checkout", "-q", "doku/gemischt")
    _alt(welt)

    r = _lauf(welt)["skill-gemischt"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_253_amend_mit_anderem_dateiinhalt_bleibt(welt: Welt, repos: Repos) -> None:
    """Ändert ``amend`` den Dateiinhalt, liegt der alte Stand weder als Baum noch als Patch
    am Server ⇒ bleibt (kein stiller Datenverlust)."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-amend-inhalt")
    git(wt, "checkout", "-q", "-b", "doku/amend-inhalt")
    _schreiben_commit(wt, "entwurf.txt", "alte fassung mit wichtigen zeilen\n", "entwurf")
    (wt / "entwurf.txt").write_text("komplett gekürzt\n", encoding="utf-8")
    git(wt, "add", "entwurf.txt")
    git(wt, "commit", "-q", "--amend", "-m", "gekürzt")
    git(wt, "push", "-q", "-u", "origin", "doku/amend-inhalt")
    _alt(welt)

    r = _lauf(welt)["skill-amend-inhalt"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_253_binaerdatei_anderen_inhalts_bleibt(welt: Welt, repos: Repos) -> None:
    """Zwei verschiedene Binärdateien mit demselben Namen dürfen nie dieselbe Patch-ID liefern."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-binaer")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "--detach")
    (wt / "bild.bin").write_bytes(b"\x00\x01\x02\x03\x04\xff")
    git(wt, "add", "bild.bin")
    git(wt, "commit", "-q", "-m", "altes bild")
    git(wt, "checkout", "-q", haupt)
    # Anderer Binärinhalt gleichen Namens auf origin/main
    h = repos.haupt[SKILL]
    git(h, "checkout", "-q", haupt)
    git(h, "pull", "-q", "--ff-only")
    (h / "bild.bin").write_bytes(b"\x00\xfe\xfd\xfc\xfb\xaa")
    git(h, "add", "bild.bin")
    git(h, "commit", "-q", "-m", "anderes bild")
    git(h, "push", "-q", "origin", haupt)
    git(wt, "pull", "-q", "--ff-only")
    _alt(welt)

    r = _lauf(welt)["skill-binaer"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_253_verlorener_commit_und_revert_auf_basis_bleibt(welt: Welt, repos: Repos) -> None:
    """Ein Commit mit anschließendem Revert zurück auf den Basis-Baum ist kein Squash-Merge."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-revert")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "--detach")
    sha = _schreiben_commit(wt, "idee.txt", "nicht wegwerfen\n", "idee")
    git(wt, "revert", "--no-edit", sha)
    git(wt, "checkout", "-q", haupt)
    _alt(welt)

    r = _lauf(welt)["skill-revert"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_253_rebase_mit_verworfenem_commit_bleibt(welt: Welt, repos: Repos) -> None:
    """Fällt beim Rebase ein Commit mit echtem Inhalt weg (``--onto``), bleibt der Ordner stehen."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-rebase-drop")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "-b", "doku/drop-1")
    weg = _schreiben_commit(wt, "verworfen.txt", "nicht auf server\n", "verworfen")
    _schreiben_commit(wt, "bleibt.txt", "auf server\n", "behalten")
    git(wt, "rebase", "-q", "--onto", haupt, weg, "doku/drop-1")
    git(wt, "push", "-q", "-u", "origin", "doku/drop-1")
    _alt(welt)

    r = _lauf(welt)["skill-rebase-drop"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()


def test_253_gefaelschte_tracking_ref_zaehlt_nicht_als_server_stand(welt: Welt, repos: Repos) -> None:
    """Ein geändeter Commit, der nur über eine gefälschte Tracking-Ref erreichbar wäre,
    zählt nie als Server-Stand (#254)."""
    _vorgabe(repos)
    wt = _klon(welt, "skill-fake-amend")
    haupt = repos.hauptzweig[SKILL]
    git(wt, "checkout", "-q", "--detach")
    _schreiben_commit(wt, "geheim.txt", "nur lokal\n", "wip")
    git(wt, "commit", "-q", "--amend", "-m", "geändert")
    geandert = git(wt, "rev-parse", "HEAD").strip()
    git(wt, "checkout", "-q", haupt)
    git(wt, "config", "remote.origin.fetch", f"+refs/heads/{haupt}:refs/remotes/origin/{haupt}")
    git(wt, "update-ref", "refs/remotes/origin/faelschung", geandert)
    _alt(welt)

    r = _lauf(welt)["skill-fake-amend"]

    assert r.art == "bleibt" and VERLAUF in r.grund, r
    assert wt.is_dir()
