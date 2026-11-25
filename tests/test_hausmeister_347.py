"""Hausmeister #347: Rest-Lücken aus den unabhängigen Prüfungen zu PR #352.

Ergänzt ``tests/test_hausmeister_luecken_347_254.py`` (W1–W4 bereits auf ``main``):
- ``extensions.refStorage`` ≠ ``files`` in ``_nur_im_reflog`` ⇒ Ordner bleibt (``fuer_david``).
- Unlesbare Reflog-Datei, unlesbare Reflog-Zeile und gesperrte Unterordner unter ``logs/`` bzw.
  ``worktrees/*/logs`` ⇒ Ordner bleibt mit Fehler (auch unter Python 3.13, wo ``rglob``
  unlesbare Ordner still überspränge).
- Normaler Klon liest alle Reflogs (z. B. ``logs/refs/heads/<zweig>``), nicht nur ``logs/HEAD``.
- Konfig-Scope ``worktree`` bei ``url.<x>.insteadOf`` ⇒ gilt als fremd (``_origin_slug == ""``).
- Umleitung der Maschine auf ein fremdes URL-Schema (``<schema>://``, startet ``git-remote-<x>``)
  ⇒ gilt nicht als GitHub.
- Ordner mit Datums-Zweig (``ticket/2026-13-x``) läuft über den Weg „ohne Nummer“: ungesichert
  bleibt er stehen, sauber + alt + auf ``origin`` darf er weg.
- Rest #248: optionale Maschinen-Listen-Angabe ``zweig_ticket_repo`` je Repo (Klartext-Präfixe,
  längster Präfix gewinnt, leer = kein Ticket-Repo, ungültig/widersprüchlich = fail-closed) in
  Konfig, Einrichter, Zweig- und Ordner-Pfad sowie ``_alle_ticket_repos``.
"""

# ruff: noqa: F811 — ``welt``/``repos`` kommen als Fixtures aus den #1368-/#176-Tests.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_235_fix2 import _alt
from test_hausmeister_1368 import ORIGIN, Welt, git, welt  # noqa: F401 — Fixture
from test_hausmeister_ticket_repo_176 import APP, SKILL, TAG, Repos, repos  # noqa: F401

from to_spawn import config, einrichten, hausmeister


def _commit(ordner: Path, datei: str, inhalt: str | None = None) -> str:
    (ordner / datei).write_text(inhalt if inhalt is not None else f"{datei}\n", encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", datei)
    return git(ordner, "rev-parse", "HEAD").strip()


def _git_dir(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--absolute-git-dir").strip())


def _ohne_root() -> None:
    if sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("POSIX-Dateirechte ohne root nötig")


# --- Reflog-Schutz: unlesbare Ordner, Dateien, Zeilen, refStorage, alle Reflogs ------------


def test_347_ref_storage_nicht_files_haelt_ordner(welt: Welt) -> None:
    """``extensions.refStorage`` ≠ ``files`` (z. B. ``reftable``) ⇒ Verlauf nicht prüfbar ⇒ bleibt."""
    wt = welt.worktree(3471)
    welt.ticket(3471, "CLOSED", 48)
    # Konfig-Wert wie bei reftable-Repos (ohne dass git beim Lesen abstürzt)
    echt = hausmeister._git_oder_none

    def mit_reftable(cwd: Path, *args: str) -> str | None:
        if args == ("config", "--get", "extensions.refStorage"):
            return "reftable"
        return echt(cwd, *args)

    # Direkter Beweis auf _nur_im_reflog und über die öffentliche Tür aufraeumen()
    original = hausmeister._git_oder_none
    hausmeister._git_oder_none = mit_reftable  # type: ignore[assignment]
    try:
        with pytest.raises(hausmeister._Bleibt, match="Ref-Speicher") as exc:
            hausmeister._nur_im_reflog(wt, True)
        assert exc.value.fuer_david
        r = welt.aufraeumen(wt, loeschen=True)
    finally:
        hausmeister._git_oder_none = original  # type: ignore[assignment]

    assert r.art == "bleibt" and r.fuer_david, r
    assert "Ref-Speicher" in r.grund, r.grund
    assert wt.is_dir()


def test_347_echtes_reftable_repo_bleibt(tmp_path: Path) -> None:
    """Echtes ``git init --ref-format=reftable`` (ab git 2.45): ``_nur_im_reflog`` lehnt ab."""
    repo = tmp_path / "reftable-repo"
    lauf = subprocess.run(
        ["git", "init", "-q", "--ref-format=reftable", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    if lauf.returncode != 0:
        pytest.skip(f"git unterstützt --ref-format=reftable nicht: {lauf.stderr.strip()}")
    with pytest.raises(hausmeister._Bleibt, match="Ref-Speicher") as exc:
        hausmeister._nur_im_reflog(repo, False)
    assert exc.value.fuer_david


@pytest.mark.braucht("posix")
def test_347_reflog_datei_unlesbar_bleibt(welt: Welt) -> None:
    """Eine unlesbare Reflog-Datei (``logs/HEAD`` ohne Leserecht) ⇒ bleibt mit Fehler."""
    _ohne_root()
    wt = welt.worktree(3472)
    welt.ticket(3472, "CLOSED", 48)
    head_log = _git_dir(wt) / "logs" / "HEAD"
    assert head_log.is_file()
    head_log.chmod(0o000)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
    finally:
        head_log.chmod(0o644)

    assert r.art == "bleibt" and r.fehler, r
    assert "Reflog nicht lesbar" in r.grund, r.grund
    assert wt.is_dir()


def test_347_unlesbare_reflog_zeile_bleibt(welt: Welt) -> None:
    """Kaputte Zeile in einer Reflog-Datei ⇒ bleibt mit Fehler (fail-closed)."""
    wt = welt.worktree(3473)
    welt.ticket(3473, "CLOSED", 48)
    head_log = _git_dir(wt) / "logs" / "HEAD"
    head_log.write_text("kaputte zeile ohne shas\n", encoding="utf-8")

    r = welt.aufraeumen(wt, loeschen=True)

    assert r.art == "bleibt" and r.fehler, r
    assert "unlesbare Reflog-Zeile" in r.grund, r.grund
    assert wt.is_dir()


@pytest.mark.braucht("posix")
def test_347_gesperrter_unterordner_in_logs_bleibt(welt: Welt) -> None:
    """Unter Python 3.13 überspringt ``rglob`` unlesbare Unterordner still — ein gesperrter
    Unterordner unter ``logs/`` muss trotzdem fail-closed ``_Bleibt`` mit Fehler auslösen."""
    _ohne_root()
    klon = welt.klon("duoplus-3474")
    welt.ticket(3474, "CLOSED", 48)
    gesperrt = _git_dir(klon) / "logs" / "refs" / "heads"
    assert gesperrt.is_dir()
    gesperrt.chmod(0o000)
    try:
        with pytest.raises(hausmeister._Bleibt, match="Reflog nicht lesbar") as exc:
            hausmeister._nur_im_reflog(klon, False)
        assert exc.value.fehler
        r = welt.aufraeumen(klon, loeschen=True)
    finally:
        gesperrt.chmod(0o755)

    assert r.art == "bleibt" and r.fehler, r
    assert "Reflog nicht lesbar" in r.grund, r.grund
    assert klon.is_dir()


@pytest.mark.braucht("posix")
def test_347_gesperrter_unterordner_in_worktrees_logs_bleibt(welt: Welt) -> None:
    """Gesperrter ``worktrees/<wt>/logs``-Ordner in einem Klon ⇒ fail-closed mit Fehler."""
    _ohne_root()
    klon = welt.klon("duoplus-3475")
    nebenan = welt.tmp / "nebenan-3475"
    git(klon, "worktree", "add", "-q", "--detach", str(nebenan), "origin/master")
    _commit(nebenan, "arbeit.txt")
    shutil.rmtree(nebenan)
    wt_logs = next((_git_dir(klon) / "worktrees").iterdir()) / "logs"
    assert wt_logs.is_dir()
    wt_logs.chmod(0o000)
    try:
        with pytest.raises(hausmeister._Bleibt, match="Reflog nicht lesbar") as exc:
            hausmeister._nur_im_reflog(klon, False)
        assert exc.value.fehler
    finally:
        wt_logs.chmod(0o755)


def test_347_normaler_klon_liest_alle_reflogs_nicht_nur_head(welt: Welt) -> None:
    """Commit steht nur im Zweig-Reflog (``logs/refs/heads/merk``), nie in ``logs/HEAD``:
    beim Klon stirbt ``logs/refs/`` mit dem Ordner ⇒ Klon muss bleiben."""
    klon = welt.klon("duoplus-3476")
    welt.ticket(3476, "CLOSED", 48)
    sha = git(klon, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "nur im zweig-reflog").strip()
    # Direkt per update-ref am Zweig (HEAD-Reflog sieht ``sha`` nie)
    git(klon, "update-ref", "--create-reflog", "refs/heads/merk", sha)
    git(klon, "update-ref", "refs/heads/merk", "HEAD")
    head_log_text = (_git_dir(klon) / "logs" / "HEAD").read_text(encoding="utf-8")
    assert sha not in head_log_text, "Vorbedingung: SHA darf nur im Zweig-Reflog stehen"

    assert hausmeister._nur_im_reflog(klon, False) is True
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r
    assert "Reflog" in r.grund, r.grund
    assert klon.is_dir()


# --- Umleitungs-Prüfung: worktree-Scope und git-remote-<x> URL-Schema ----------------------


def test_347_worktree_config_scope_instead_of_ist_fremd(welt: Welt) -> None:
    """``url.<x>.insteadOf`` im Konfig-Scope ``worktree`` ⇒ ``_origin_slug`` liefert ``""``."""
    wt = welt.worktree(3477)
    git(welt.haupt, "config", "extensions.worktreeConfig", "true")
    assert hausmeister._origin_slug(wt) == APP  # Gegenprobe vor Umleitung
    git(wt, "config", "--worktree", f"url.{welt.bare.as_posix()}.insteadOf", ORIGIN)

    assert hausmeister._origin_slug(wt) == ""


def test_347_umleitung_auf_fremdes_url_schema_ist_kein_github(welt: Welt) -> None:
    """Maschinen-Umleitung auf ein eigenes URL-Schema (``meinhelfer://…`` → ``git-remote-meinhelfer``)
    darf nicht mehr als GitHub gelten."""
    klon = welt.klon("duoplus-schema")
    git(klon, "config", "remote.origin.url", ORIGIN + ".git")
    assert hausmeister._origin_slug(klon) == APP  # Gegenprobe: Spiegel-Pfad gilt
    with (welt.tmp / "gitconfig").open("a", encoding="utf-8") as f:
        f.write(f'[url "meinhelfer://spiegel/repo"]\n\tinsteadOf = {ORIGIN}.git\n')

    assert hausmeister._origin_slug(klon) == ""


# --- Datums-Zweig im Ordner-Pfad -----------------------------------------------------------


def test_347_ordner_mit_datums_zweig_laeuft_ueber_ohne_nummer(welt: Welt) -> None:
    """Ein Ordner auf ``ticket/2026-13-x`` (Datums-Form → keine Ticket-Nummer) bleibt mit
    ungesicherten Änderungen stehen und darf erst sauber, alt und auf ``origin`` weg."""
    klon = welt.klon("duoplus-datum")
    git(klon, "checkout", "-q", "-b", "ticket/2026-13-x")
    _commit(klon, "lokal.txt")
    _alt(welt)

    # Ungesichert: bleibt mit „keine Ticket-Nummer“
    r1 = welt.aufraeumen(klon, loeschen=True)
    assert r1.art == "bleibt" and r1.ticket is None, r1
    assert hausmeister.OHNE_NUMMER in r1.grund, r1.grund
    assert klon.is_dir()

    # Auf origin gepusht, sauber und alt: Weg „ohne Nummer“ räumt ihn weg
    git(klon, "push", "-q", "-u", "origin", "ticket/2026-13-x")
    _alt(welt)
    r2 = welt.aufraeumen(klon, loeschen=True)
    assert r2.art == "gelöscht" and r2.ticket is None, r2
    assert hausmeister.OHNE_NUMMER in r2.grund, r2.grund
    assert not klon.exists()


# --- Rest #248: Maschinen-Listen-Angabe zweig_ticket_repo ----------------------------------


def _maschine_schreiben(eintraege: list[dict[str, object]]) -> Path:
    datei = config.maschine_datei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps({"repos": eintraege}, indent=2) + "\n", encoding="utf-8")
    return datei


def test_347_zweig_ticket_repo_pruefen_gueltig_und_ungueltig() -> None:
    """Prüfung an genau einer Stelle: gültige Zuordnung normalisiert, kaputte abgelehnt."""
    assert config.zweig_ticket_repo_pruefen(None) == ({}, "")
    assert config.zweig_ticket_repo_pruefen({}) == ({}, "")
    assert config.zweig_ticket_repo_pruefen({"ticket/": f"  {APP}  ", "fix/": ""}) == (
        {"ticket/": APP, "fix/": ""},
        "",
    )
    for kaputt in (
        "ticket/",
        ["ticket/"],
        {"": APP},
        {"fix/ ": APP},
        {"fix /": APP},
        {"fix/": "kein-slash"},
        {"fix/": "a/b/c"},
        {"fix/": 123},
        {"fix/": None},
    ):
        zuordnung, fehler = config.zweig_ticket_repo_pruefen(kaputt)
        assert zuordnung is None and fehler, f"erwartet ungültig für {kaputt!r}"


def test_347_maschine_bewahrt_zweig_ticket_repo_und_einrichter_prueft(tmp_path: Path) -> None:
    """``zweig_ticket_repo`` überlebt Lesen, ``maschine_eintragen`` und ``ticket_repo_setzen``;
    der Einrichter meldet ungültige Einträge als Lücke."""
    repo = tmp_path / "skill"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "remote", "add", "origin", f"https://github.com/{SKILL}.git")
    regeln = {"fix/": APP, "wip/": ""}
    datei = _maschine_schreiben(
        [
            {"slug": APP, "pfad": (tmp_path / "app").as_posix(), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": repo.as_posix(),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": regeln,
            },
        ]
    )

    geladen = config.maschine()["repos"]
    assert "zweig_ticket_repo" not in geladen[0]
    assert geladen[1]["zweig_ticket_repo"] == regeln

    # Wiederholtes Eintragen und ticket_repo-Setzen dürfen zweig_ticket_repo nie verlieren.
    config.maschine_eintragen(repo)
    config.maschine_ticket_repo_setzen(SKILL, APP)
    config.maschine_ticket_repo_setzen(SKILL, SKILL)
    gespeichert = json.loads(datei.read_text(encoding="utf-8"))["repos"][1]
    assert gespeichert["zweig_ticket_repo"] == regeln

    # Einrichter: gültig → DA, ungültig → LUECKE (und kaputte Angabe bleibt unüberschrieben).
    befund_ok, _ = einrichten._pruefe_maschine(einrichten.Ort(repo, None))  # type: ignore[arg-type]
    assert befund_ok == einrichten.DA

    _maschine_schreiben(
        [
            {
                "slug": SKILL,
                "pfad": repo.as_posix(),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"fix/": "kein-slash"},
            }
        ]
    )
    befund_kaputt, text = einrichten._pruefe_maschine(einrichten.Ort(repo, None))  # type: ignore[arg-type]
    assert befund_kaputt == einrichten.LUECKE
    assert "zweig_ticket_repo" in text

    # Widersprüchliche Einträge zum selben slug meldet der Einrichter ebenfalls als Lücke.
    _maschine_schreiben(
        [
            {
                "slug": SKILL,
                "pfad": repo.as_posix(),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"fix/": APP},
            },
            {
                "slug": SKILL,
                "pfad": (tmp_path / "skill-zweit").as_posix(),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"fix/": SKILL},
            },
        ]
    )
    befund_konflikt, text_konflikt = einrichten._pruefe_maschine(einrichten.Ort(repo, None))  # type: ignore[arg-type]
    assert befund_konflikt == einrichten.LUECKE
    assert "widersprüchlich" in text_konflikt


def test_347_zweig_zu_ticket_nutzt_zweig_ticket_repo_und_laengsten_praefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Präfix-Zuordnung je Repo lenkt App-Nummern im Skill-Repo gezielt um; längster Präfix gewinnt."""
    eintraege = [
        {"slug": APP, "ticket_repo": APP, "pfad": ""},
        {
            "slug": SKILL,
            "ticket_repo": SKILL,
            "pfad": "",
            "zweig_ticket_repo": {
                "ticket/": SKILL,
                "ticket/14": APP,
                "fix/": APP,
                "wip/": "",
            },
        },
    ]
    monkeypatch.setattr(hausmeister, "_maschinen_repos", lambda: eintraege)

    assert hausmeister.zweig_zu_ticket("fix/1406", SKILL) == (APP, 1406)
    assert hausmeister.zweig_zu_ticket("ticket/1406-alt", SKILL) == (APP, 1406)
    assert hausmeister.zweig_zu_ticket("ticket/347-neu", SKILL) == (SKILL, 347)
    assert hausmeister.zweig_zu_ticket("l4/176-x", SKILL) == (SKILL, 176)
    assert hausmeister.zweig_zu_ticket("wip/1368-stand", SKILL) is None
    # Datums-Sperre gilt unverändert trotz passendem Präfix.
    assert hausmeister.zweig_zu_ticket("ticket/2026-13-x", SKILL) is None
    assert hausmeister.zweig_zu_ticket("fix/2026-11", SKILL) is None


def test_347_zweig_ticket_repo_ungueltig_oder_widerspruechlich_ist_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kaputtes oder widersprüchliches ``zweig_ticket_repo`` liefert fail-closed ``None``."""
    hausmeister._ABGELEHNT_GEMELDET.clear()
    monkeypatch.setattr(
        hausmeister,
        "_maschinen_repos",
        lambda: [{"slug": SKILL, "ticket_repo": SKILL, "pfad": "", "zweig_ticket_repo": {"fix/": "kein-slash"}}],
    )
    assert hausmeister.zweig_zu_ticket("l4/176-x", SKILL) is None
    assert hausmeister.zweig_zu_ticket("fix/1406", SKILL) is None

    hausmeister._ABGELEHNT_GEMELDET.clear()
    monkeypatch.setattr(
        hausmeister,
        "_maschinen_repos",
        lambda: [
            {"slug": SKILL, "ticket_repo": SKILL, "pfad": "/a", "zweig_ticket_repo": {"fix/": APP}},
            {"slug": SKILL, "ticket_repo": SKILL, "pfad": "/b", "zweig_ticket_repo": {"fix/": SKILL}},
        ],
    )
    assert hausmeister.zweig_zu_ticket("fix/1406", SKILL) is None


def test_347_zweig_pfad_nutzt_zweig_ticket_repo(welt: Welt, repos: Repos) -> None:
    """Im Zweig-Pfad fragt ``ticket/<N>`` im Skill-Repo das in ``zweig_ticket_repo`` genannte App-Repo."""
    repos.repo(SKILL)
    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"ticket/": APP},
            },
        ]
    )
    repos.worktree(SKILL, 1525, gemergt=True)
    repos.ticket(SKILL, 1525, "CLOSED", 48)
    repos.ticket(APP, 1525, "OPEN")

    # App-Ticket offen hält den Skill-Zweig trotz geschlossenem Skill-Issue #1525.
    fertig1, _, grund1, _ = hausmeister.zweig_fertig(f"https://github.com/{SKILL}", "ticket/1525-test")
    assert not fertig1 and "offen" in grund1, grund1

    # App-Ticket zu macht ihn fertig, auch wenn Skill-Issue #1525 offen ist.
    repos.ticket(SKILL, 1525, "OPEN")
    repos.ticket(APP, 1525, "CLOSED", 48)
    fertig2, fehlende2, grund2, _ = hausmeister.zweig_fertig(f"https://github.com/{SKILL}", "ticket/1525-test")
    assert fertig2 and not fehlende2, grund2

    # Leerer Ziel-Wert schaltet die Zuordnung fail-closed ab.
    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"ticket/": ""},
            },
        ]
    )
    fertig3, _, grund3, _ = hausmeister.zweig_fertig(f"https://github.com/{SKILL}", "ticket/1525-test")
    assert not fertig3 and "zählt nicht" in grund3, grund3


def test_347_ordner_pfad_nutzt_zweig_ticket_repo_und_bleibt_bei_ungueltig(welt: Welt, repos: Repos) -> None:
    """Im Ordner-Pfad gilt ``zweig_ticket_repo`` für den ausgecheckten Zweig; kaputte Regel ⇒ Fehler."""
    repos.repo(SKILL)
    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"fix/": APP, "wip/": ""},
            },
        ]
    )
    haupt = repos.haupt[SKILL]

    def wt_anlegen(name: str, zweig: str, *, gemergt: bool = False) -> Path:
        wt = welt.heim / "wt" / name
        git(haupt, "worktree", "add", "-q", "-b", zweig, str(wt), "origin/main")
        _commit(wt, "arbeit.txt")
        git(wt, "push", "-q", "-u", "origin", zweig)
        if gemergt:
            git(wt, "push", "-q", "origin", "HEAD:refs/heads/main")
        return wt

    # fix/1406 im Skill-Repo fragt das App-Repo: App offen + Skill zu ⇒ bleibt.
    wt_fix = wt_anlegen("skill-1406", "fix/1406")
    repos.ticket(SKILL, 1406, "CLOSED", 48)
    repos.ticket(APP, 1406, "OPEN")
    r_offen = welt.aufraeumen(wt_fix, loeschen=True)
    assert r_offen.art == "bleibt" and "#1406" in r_offen.grund and "offen" in r_offen.grund, r_offen
    assert wt_fix.is_dir()

    # App zu + Skill offen ⇒ wird geräumt.
    repos.ticket(SKILL, 1406, "OPEN")
    repos.ticket(APP, 1406, "CLOSED", 48)
    r_zu = welt.aufraeumen(wt_fix, loeschen=True)
    assert r_zu.art == "gelöscht" and not r_zu.fehler, r_zu
    assert not wt_fix.exists()

    # wip/1368 mit leerem Ziel ⇒ zählt als fremd, ungemergt bleibt er trotz geschlossener Tickets.
    wt_wip = wt_anlegen("skill-1368", "wip/1368", gemergt=False)
    repos.ticket(SKILL, 1368, "CLOSED", 48)
    repos.ticket(APP, 1368, "CLOSED", 48)
    r_wip = welt.aufraeumen(wt_wip, loeschen=True)
    assert r_wip.art == "bleibt" and not r_wip.fehler, r_wip
    assert wt_wip.is_dir()

    # Ungültiges zweig_ticket_repo ⇒ bleibt mit Fehler, selbst gemergt + alt (nie über _fremd_fertig weg).
    wt_kaputt = wt_anlegen("skill-176", "l4/176-kaputt", gemergt=True)
    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": "kaputt",
            },
        ]
    )
    hausmeister._ABGELEHNT_GEMELDET.clear()
    welt.jetzt += 30 * TAG
    r_kaputt = welt.aufraeumen(wt_kaputt, loeschen=True)
    assert r_kaputt.art == "bleibt" and r_kaputt.fehler, r_kaputt
    assert "Ticket-Repo" in r_kaputt.grund, r_kaputt
    assert wt_kaputt.is_dir()


def test_347_alle_ticket_repos_enthaelt_zweig_ticket_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ticket-Repos aus ``zweig_ticket_repo`` zählen zu ``_alle_ticket_repos`` (Schutz auch ohne Git)."""
    monkeypatch.setattr(
        hausmeister,
        "_maschinen_repos",
        lambda: [
            {
                "slug": SKILL,
                "ticket_repo": SKILL,
                "pfad": "",
                "zweig_ticket_repo": {"fix/": "Org/Extra-Tickets", "wip/": ""},
            }
        ],
    )
    assert hausmeister._alle_ticket_repos() == [SKILL, "Org/Extra-Tickets"]


def test_347_schliesszeit_nutzt_zweig_ticket_repo_und_bleibt_bei_leer_oder_kaputt_none(
    welt: Welt, repos: Repos
) -> None:
    """``_schliesszeit`` nutzt ``zweig_ticket_repo`` und liefert bei leerer/ungültiger Regel ``None``."""
    repos.repo(SKILL)
    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": {"fix/": APP, "wip/": ""},
            },
        ]
    )
    haupt = repos.haupt[SKILL]
    wt_fix = welt.heim / "wt" / "skill-1407"
    git(haupt, "worktree", "add", "-q", "-b", "fix/1407", str(wt_fix), "origin/main")
    repos.ticket(SKILL, 1407, "OPEN")
    repos.ticket(APP, 1407, "CLOSED", 48)
    assert hausmeister._schliesszeit(wt_fix) is not None

    wt_wip = welt.heim / "wt" / "skill-1408"
    git(haupt, "worktree", "add", "-q", "-b", "wip/1408", str(wt_wip), "origin/main")
    repos.ticket(SKILL, 1408, "CLOSED", 48)
    repos.ticket(APP, 1408, "CLOSED", 48)
    assert hausmeister._schliesszeit(wt_wip) is None

    _maschine_schreiben(
        [
            {"slug": APP, "pfad": str(repos.haupt[APP]), "ticket_repo": APP, "worktree_basis": ""},
            {
                "slug": SKILL,
                "pfad": str(repos.haupt[SKILL]),
                "ticket_repo": SKILL,
                "worktree_basis": "",
                "zweig_ticket_repo": "kaputt",
            },
        ]
    )
    hausmeister._ABGELEHNT_GEMELDET.clear()
    assert hausmeister._schliesszeit(wt_fix) is None
