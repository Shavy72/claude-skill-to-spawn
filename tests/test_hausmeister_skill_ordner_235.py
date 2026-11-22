"""Hausmeister #235: Helfer-Ordner des Skill-Repos (``skill-NNN``) werden geräumt.

Lastenheft L4/M14: Ist das Skill-Issue zu und nichts Ungesichertes im Ordner, räumt der
Hausmeister ihn weg; offene Issues, Prozesse und ungesicherte Arbeit halten ihn fest.
Die Nummer zählt nur im Ticket-Repo des Ordner-Repos (kein Rückfall in #135).

Grundlage ist die Vorgabe der Maschinen-Liste (``config.MASCHINE_DEFAULTS``), nicht eine
von Hand passend gemachte Liste — genau die Vorgabe macht heute Skill-Nummern zu App-Tickets.

Echte Git-Repos und Prozesse; gestellt ist nur ``gh`` über die Test-Tür
``TO_SPAWN_GH_STUB`` (Attrappe aus dem #176-Test, Ticket-Zustand je Repo).
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — ``welt`` ist ein Fixture
from test_hausmeister_ticket_repo_176 import APP, SKILL, TAG, WEGWERF, Repos, repos  # noqa: F401

from to_spawn import config, hausmeister


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _vorgabe(repos: Repos, *extra: tuple[str, str]) -> None:
    """Maschinen-Liste wie die Vorgabe (slug + ticket_repo), Pfade auf die Test-Klone."""
    repos.repo(SKILL)
    eintraege = [(e["slug"], e["ticket_repo"]) for e in config.MASCHINE_DEFAULTS["repos"]]
    repos.maschine([*eintraege, *extra])


def _ordner(
    repos: Repos,
    slug: str,
    name: str,
    zweig: str | None,
    *,
    gemergt: bool = False,
    push: bool = True,
) -> Path:
    """Worktree ``~/wt/<name>`` aus dem Hauptklon von ``slug``; ``zweig=None`` = abgehängt
    auf dem Hauptzweig. Sonst ein Commit auf ``zweig``, gepusht und auf Wunsch gemergt."""
    haupt = repos.haupt[slug]
    basis = f"origin/{repos.hauptzweig[slug]}"
    wt = repos.welt.heim / "wt" / name
    if zweig is None:
        git(haupt, "worktree", "add", "-q", "--detach", str(wt), basis)
        return wt
    git(haupt, "worktree", "add", "-q", "-b", zweig, str(wt), basis)
    (wt / "arbeit.txt").write_text(f"{name}\n", encoding="utf-8")
    git(wt, "add", "arbeit.txt")
    git(wt, "commit", "-q", "-m", "arbeit")
    if push:
        git(wt, "push", "-q", "-u", "origin", zweig)
    if gemergt:
        git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{repos.hauptzweig[slug]}")
    return wt


def _lauf(welt: Welt) -> dict[str, hausmeister.Ergebnis]:
    ergebnisse = hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)
    return {Path(e.ordner).name: e for e in ergebnisse}


# --- (a) Skill-Issue zu ⇒ Ordner wird geräumt --------------------------------------


def test_a_skill_issue_zu_ordner_wird_geraeumt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-ticket-repo-je-ordner")
    repos.ticket(SKILL, 176, "CLOSED", 48)

    r = _lauf(welt)["skill-176"]

    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert r.ticket == 176, r
    assert not wt.exists()


# --- (b) Skill-Issue offen ⇒ bleibt mit Grund „offen“ -------------------------------


def test_b_skill_issue_offen_bleibt_mit_grund_offen(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-214a", "l1/214a-stop-hook")
    repos.ticket(SKILL, 214, "OPEN")
    repos.ticket(APP, 214, "CLOSED", 24 * 400)  # gleiche Nummer im App-Repo zählt nicht

    r = _lauf(welt)["skill-214a"]

    assert r.art == "bleibt", r.grund
    assert "keine Ticket-Nummer" not in r.grund, r.grund
    assert "#214" in r.grund and "offen" in r.grund, r.grund
    assert r.ticket == 214, r
    assert wt.is_dir()


# --- (c) kein Rückfall in #135: Nummer zählt nur im eigenen Ticket-Repo -------------


def test_c1_skill_ordner_fragt_nur_das_skill_repo(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-ticket-repo-je-ordner")
    repos.ticket(SKILL, 176, "CLOSED", 48)
    repos.ticket(APP, 176, "OPEN")  # App-Ticket #176 offen: darf den Skill-Ordner nicht halten

    r = _lauf(welt)["skill-176"]

    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


def test_c2_schutz_app_ordner_nie_nach_dem_skill_issue(welt: Welt, repos: Repos) -> None:
    """Schutz-Test (heute grün): App-Ordner #176 bleibt, solange App-Ticket #176 offen ist."""
    _vorgabe(repos)
    wt = _ordner(repos, APP, "wt-176", "ticket/176-app")
    repos.ticket(APP, 176, "OPEN")
    repos.ticket(SKILL, 176, "CLOSED", 24 * 400)

    r = _lauf(welt)["wt-176"]

    assert r.art == "bleibt", r.grund
    assert "#176" in r.grund and "offen" in r.grund, r.grund
    assert wt.is_dir()


# --- (d) ohne Nummer: sauber und nachweislich in origin/main ⇒ wird geräumt --------
# Festlegung: Ein Skill-Ordner ohne Ticket-Nummer ist ein Wegwerf-Stand (Installation,
# Doku-Stand, Pull-Request-Probe). Liegt nichts Ungesichertes darin (Kopf in origin/main,
# sauber, kein Prozess, kein offener Pull-Request, 24 h unverändert), geht nichts verloren
# — Behalten hätte keinen Grund. Sonst bleibt er mit klarem Grund (Fall e).


@pytest.mark.parametrize(
    ("name", "zweig", "gemergt"),
    [
        ("skill-install83", None, False),
        ("skill-doku84", "doku/stand-84", True),
        ("skill-pr231", "l1/p7f-umzug-waechter-staffel", True),
    ],
)
def test_d_ohne_nummer_sauber_und_in_main_wird_geraeumt(
    welt: Welt, repos: Repos, name: str, zweig: str | None, gemergt: bool
) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, name, zweig, gemergt=gemergt)
    welt.jetzt += 30 * TAG

    r = _lauf(welt)[name]

    assert r.art == "gelöscht" and not r.fehler, r.grund
    assert not wt.exists()


# --- (e) ungesicherte Arbeit ⇒ bleibt immer -----------------------------------------


@pytest.mark.parametrize("art", ["ungepusht", "geaendert"])
def test_e_ungesichertes_bleibt_auch_wenn_issue_zu(welt: Welt, repos: Repos, art: str) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-ticket-repo-je-ordner", push=art != "ungepusht")
    if art == "geaendert":
        (wt / "arbeit.txt").write_text("ungesichert\n", encoding="utf-8")
    repos.ticket(SKILL, 176, "CLOSED", 24 * 400)
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-176"]

    assert r.art == "bleibt", r.grund
    assert wt.is_dir() and (wt / "arbeit.txt").is_file()


def test_e_ohne_nummer_mit_ungepushtem_commit_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-doku85", "doku/stand-85", push=False)
    welt.jetzt += 30 * TAG

    r = _lauf(welt)["skill-doku85"]

    assert r.art == "bleibt" and r.grund, r
    assert wt.is_dir()


# --- (f) laufender Prozess ⇒ bleibt -------------------------------------------------


def test_f_prozess_im_skill_ordner_bleibt(welt: Welt, repos: Repos) -> None:
    _vorgabe(repos)
    wt = _ordner(repos, SKILL, "skill-176", "l4/176-ticket-repo-je-ordner", gemergt=True)
    repos.ticket(SKILL, 176, "CLOSED", 48)
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=wt, stdin=subprocess.PIPE)
    try:
        r = _lauf(welt)["skill-176"]
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


# --- (g) Log: Anzahl je Grund, Fehler mit Ordner und Text ---------------------------


def test_g_lauf_loggt_gruende_mit_anzahl_und_fehler_mit_ordner(
    welt: Welt, repos: Repos, caplog: pytest.LogCaptureFixture
) -> None:
    repos.repo(SKILL)
    repos.repo(WEGWERF)
    # WEGWERF ohne Ticket-Repo ⇒ „Ticket-Repo nicht bestimmbar“ (fehler=True)
    repos.maschine([(APP, APP), (SKILL, SKILL), (WEGWERF, "")])
    # zwei App-Ordner ohne Nummer: gleicher Grund „keine Ticket-Nummer“ (App-Regel #1536)
    _ordner(repos, APP, "wt-notiz-a", "planung/notiz-a")
    _ordner(repos, APP, "wt-notiz-b", "planung/notiz-b")
    _ordner(repos, WEGWERF, "wt-303", "ticket/303-c")

    with caplog.at_level(logging.INFO):
        r = _lauf(welt)

    assert r["wt-notiz-a"].grund == r["wt-notiz-b"].grund == "keine Ticket-Nummer"
    assert r["wt-303"].fehler, r["wt-303"]
    meldungen = [rec.getMessage() for rec in caplog.records]
    text = "\n".join(meldungen)
    assert any(re.search(r"2\s*×\s*keine Ticket-Nummer|keine Ticket-Nummer\D{0,5}2\b", m) for m in meldungen), text
    assert any("wt-303" in m and "Ticket-Repo nicht bestimmbar" in m for m in meldungen), text


# --- (h) Vorgabe der Maschinen-Liste: Skill-Ordner fragen das Skill-Repo -------------


def test_h_vorgabe_skill_eintrag_hat_skill_repo_als_ticket_repo() -> None:
    eintrag = next(e for e in config.MASCHINE_DEFAULTS["repos"] if e["slug"] == SKILL)
    assert eintrag["ticket_repo"] == SKILL, eintrag
    assert "skill" in config.MASCHINE_DEFAULTS["hausmeister"]["ordner_praefixe"]
