"""Paket #407, #420, #430: lokale Exclude-Zeilen, Ticket-Repo-Nachzug, Einreich-Marker.

Deckt alle drei Issues über echte Git-Repos in ``tmp_path`` ab; gestellt ist nur
die externe ``gh``-CLI über ``TO_SPAWN_GH_STUB``:

- #407: ``config.gitignore_ergaenzen`` und ``regularien``/``anhaengen`` schreiben
  Ignore-Zeilen lokal nach ``.git/info/exclude`` statt in die getrackte ``.gitignore``.
- #420: frisch eingerichtete Repos (mit ``gh``-Beleg) und Altbestand mit ``stand.json``
  Version 1 erhalten ihr ``ticket_repo`` und laufen ohne Warnung im Hausmeister;
  echtes leeres ``ticket_repo`` bleibt fail-closed (#66/#112/#176).
- #430: eigener Einreich-Dienst nur per ausdrücklichem Marker ``einreichen.eigener_dienst``
  in ``origin/<hauptzweig>:.to-spawn/config.json``, nie aus ``nest.GIT_HOOKS_INSTALLER``.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
from pathlib import Path

import pytest
import test_einreichen_411 as e411
import test_einrichtungs_ticket_410 as e410
import test_spec_rand_e2 as e2
from test_einreichen_411 import welt as einreichen_welt  # noqa: F401 — Fixture
from test_hausmeister_1368 import Welt, git, welt  # noqa: F401 — Fixture

from to_spawn import config, einrichten, hausmeister


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _frisches_repo(ort: Path, *, origin: str = "https://github.com/test-org/probe.git") -> Path:
    ort.mkdir(parents=True, exist_ok=True)
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "config", "user.email", "test@example.org")
    _git(ort, "config", "user.name", "Test")
    _git(ort, "remote", "add", "origin", origin)
    (ort / "README.md").write_text("Probe\n", encoding="utf-8")
    (ort / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", "chore: Start")
    return ort


# --- #407: .git/info/exclude statt Repo-.gitignore -------------------------------------------


def test_407_gitignore_ergaenzen_schreibt_in_info_exclude_statt_gitignore(tmp_path: Path) -> None:
    repo = _frisches_repo(tmp_path / "repo")
    gitignore_vorher = (repo / ".gitignore").read_bytes()

    assert config.gitignore_ergaenzen(repo) is True
    assert config.gitignore_ergaenzen(repo) is False  # idempotent

    assert (repo / ".gitignore").read_bytes() == gitignore_vorher
    assert _git(repo, "status", "--short") == ""
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".to-spawn/*" in exclude
    assert "!.to-spawn/config.json" in exclude

    # Laufdatei unter .to-spawn/ wird über info/exclude ignoriert.
    lauf = repo / ".to-spawn" / "bau_log" / "1.jsonl"
    lauf.parent.mkdir(parents=True, exist_ok=True)
    lauf.write_text("{}\n", encoding="utf-8")
    assert _git(repo, "status", "--short") == ""


def test_407_gitignore_ergaenzen_im_worktree_schreibt_in_common_exclude(tmp_path: Path) -> None:
    repo = _frisches_repo(tmp_path / "haupt")
    wt = tmp_path / "wt-7"
    _git(repo, "worktree", "add", "-q", "-b", "ticket/7-probe", str(wt), "main")
    gitignore_vorher = (wt / ".gitignore").read_bytes()

    assert config.gitignore_ergaenzen(wt) is True
    assert (wt / ".gitignore").read_bytes() == gitignore_vorher
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".to-spawn/*" in exclude
    assert "!.to-spawn/config.json" in exclude


def test_407_gitignore_ergaenzen_symlink_exclude_wird_nicht_beschrieben(tmp_path: Path) -> None:
    repo = _frisches_repo(tmp_path / "repo")
    aussen = tmp_path / "aussen.txt"
    aussen.write_text("unberuehrt\n", encoding="utf-8")
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.unlink(missing_ok=True)
    exclude.symlink_to(aussen)

    assert config.gitignore_ergaenzen(repo) is False
    assert aussen.read_text(encoding="utf-8") == "unberuehrt\n"
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "*.pyc\n"


def test_407_anhaengen_und_regularien_lassen_gitignore_unberuehrt(tmp_path: Path) -> None:
    """Abnahme #407: frisches Repo, ``anhaengen`` + ``regularien`` → ``.gitignore`` unverändert."""
    repo, _weltdatei, umgebung = e410._aufbau(tmp_path)
    (repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "chore: gitignore")
    gitignore_vorher = (repo / ".gitignore").read_bytes()

    lauf_anh = e410._cli(
        repo,
        umgebung,
        "anhaengen",
        str(e2.SPEC),
        str(e2.NEU),
        "--blockiert-von",
        "901",
        "--schaetzung",
        "40",
        "--umfang",
        "klein",
    )
    assert lauf_anh.returncode == 0, lauf_anh.stdout + lauf_anh.stderr

    lauf_reg = e410._cli(repo, umgebung, "regularien", str(e2.SPEC), "--ohne-einrichtung")
    assert lauf_reg.returncode == 0, lauf_reg.stdout + lauf_reg.stderr

    status_zeilen = _git(repo, "status", "--short").splitlines()
    assert not any(zeile.endswith(".gitignore") for zeile in status_zeilen), status_zeilen
    assert (repo / ".gitignore").read_bytes() == gitignore_vorher
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".to-spawn/*" in exclude


# --- #420: Ticket-Repo bei Einrichtung + Nachzug Altbestand ----------------------------------


def _gh_stub_mit_issues(tmp_path: Path, slug: str, tickets: Path) -> Path:
    stub = tmp_path / "gh_stub_420.py"
    stub.write_text(
        f"""
import json, sys
args = sys.argv[1:]
if args[:2] == ["repo", "view"]:
    print(json.dumps({{"hasIssuesEnabled": True, "nameWithOwner": {slug!r}}}))
    sys.exit(0)
if args[:1] == ["api"] and "/labels/" in args[1]:
    sys.exit(0)
if args[:2] == ["issue", "view"]:
    repo = args[args.index("-R") + 1] if "-R" in args else ""
    daten = json.loads(open({str(tickets)!r}, encoding="utf-8").read())
    e = daten.get(repo.lower(), {{}}).get(args[2])
    if e is None:
        sys.exit(1)
    print(json.dumps(e))
    sys.exit(0)
if args[:2] == ["pr", "list"]:
    print("[]")
    sys.exit(0)
sys.exit(0)
""",
        encoding="utf-8",
    )
    return stub


def test_420_frisch_eingerichtetes_repo_ohne_warnung_im_hausmeister(
    welt: Welt,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    slug = "Shavy72/duoplus-management"
    tickets = welt.tmp / "tickets_420.json"
    tickets.write_text(
        json.dumps(
            {
                slug.lower(): {
                    "77": {"state": "CLOSED", "closedAt": "2026-11-20T00:00:00Z"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(_gh_stub_mit_issues(welt.tmp, slug, tickets)))
    # Frische Maschinen-Liste (nur über einrichten befüllt)
    maschine_datei = welt.tmp / "frisch_maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine_datei))
    (welt.haupt / ".to-spawn" / "stand.json").unlink(missing_ok=True)

    aus = io.StringIO()
    assert einrichten.einrichten(welt.haupt, ausgabe=aus) == 0, aus.getvalue()

    wt = welt.worktree(77)
    hausmeister._ABGELEHNT_GEMELDET.clear()
    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        erg = welt.aufraeumen(wt)

    assert (erg.art, erg.fehler) == ("gelöscht", False), erg.grund
    warnungen = [r.getMessage() for r in caplog.records if "hat kein Ticket-Repo" in r.getMessage()]
    assert warnungen == [], warnungen


def test_420_altbestand_stand_v1_mit_leerem_ticket_repo_wird_nachgezogen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repo mit altem ``stand.json`` (``maschinen_liste: 1``) und leerem ``ticket_repo`` wird nachgezogen."""
    slug = "test-org/altbestand"
    repo = _frisches_repo(tmp_path / "altbestand", origin=f"https://github.com/{slug}.git")
    maschine_datei = tmp_path / "maschine.json"
    maschine_datei.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "slug": slug,
                        "pfad": repo.resolve().as_posix(),
                        "ticket_repo": "",
                        "worktree_basis": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine_datei))
    tickets = tmp_path / "tickets.json"
    tickets.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(_gh_stub_mit_issues(tmp_path, slug, tickets)))

    # Stand-Datei wie vor #542: maschinen_liste hatte Version 1.
    stand = {
        "skill_stand": "alt",
        "bausteine": {b.name: (1 if b.name == "maschinen_liste" else b.version) for b in einrichten._BAUSTEINE},
    }
    (repo / ".to-spawn").mkdir(parents=True, exist_ok=True)
    (repo / ".to-spawn" / "stand.json").write_text(json.dumps(stand), encoding="utf-8")

    aus = io.StringIO()
    assert einrichten.einrichten(repo, ausgabe=aus) == 0, aus.getvalue()
    assert "maschinen_liste: nachgezogen" in aus.getvalue(), aus.getvalue()

    hausmeister._ABGELEHNT_GEMELDET.clear()
    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        assert hausmeister._ticket_repo_fuer(slug) == slug
    warnungen = [r.getMessage() for r in caplog.records if "hat kein Ticket-Repo" in r.getMessage()]
    assert warnungen == [], warnungen


def test_420_config_doku_widerspricht_hausmeister_nicht() -> None:
    """Eine Regel in Doku und Code: leeres ``ticket_repo`` wird nie aus ``slug`` abgeleitet."""
    doc = config.maschine_eintragen.__doc__ or ""
    assert "``ticket_repo`` leer (= ableiten)" not in doc


# --- #430: ausdrücklicher Konfig-Marker statt nest.GIT_HOOKS_INSTALLER -----------------------


def test_430_hook_installer_ohne_konfig_marker_nimmt_normalen_weg(
    einreichen_welt: dict[str, Path],  # noqa: F811
) -> None:
    """Hook-Installer allein reicht nicht mehr: ohne Konfig-Marker läuft Pull-Request + Merge."""
    klon = einreichen_welt["klon"]
    e411._commit(klon, "scripts/hooks/install_git_hooks.sh", "# eigene Sperre\n", "chore: Hook-Installer")
    e411._git(klon, "push", "-q", "origin", "main")
    wt = e411._ticket_wt(einreichen_welt)
    sha = e411._commit(wt, "README.md", "normale Einreichung\n", "docs: normal (#7)")

    erg = e411._einreichen(wt, "7")

    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert e411._ist_vorfahr(einreichen_welt["remote"], sha, e411._remote_main(einreichen_welt))
    assert any(a[:2] == ["pr", "merge"] for a in e411._gh_aufrufe(einreichen_welt))


def test_430_ausdruecklicher_konfig_marker_uebergibt_an_einreich_dienst(
    einreichen_welt: dict[str, Path],  # noqa: F811
) -> None:
    """``einreichen.eigener_dienst: true`` auf ``origin/main`` übergibt an den Einreich-Dienst."""
    klon = einreichen_welt["klon"]
    e411._commit(
        klon,
        ".to-spawn/config.json",
        json.dumps({"einreichen": {"eigener_dienst": True}}) + "\n",
        "chore: eigener Einreich-Dienst",
    )
    e411._git(klon, "push", "-q", "origin", "main")
    vorher = e411._remote_main(einreichen_welt)
    wt = e411._ticket_wt(einreichen_welt)
    sha = e411._commit(wt, "README.md", "ueber Dienst\n", "docs: Dienst (#7)")

    erg = e411._einreichen(wt, "7")

    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert "Einreich-Dienst" in erg.stdout
    assert e411._remote_main(einreichen_welt) == vorher
    assert e411._git(einreichen_welt["remote"], "rev-parse", "ticket/7-plus-doku") == sha
    assert e411._gh_aufrufe(einreichen_welt) == []


def test_430_marker_nur_im_worktree_nicht_auf_origin_zaehlt_nicht(
    einreichen_welt: dict[str, Path],  # noqa: F811
) -> None:
    """Ein Marker nur im Ticket-Zweig darf den normalen Weg nicht aushebeln."""
    wt = e411._ticket_wt(einreichen_welt)
    e411._commit(
        wt,
        ".to-spawn/config.json",
        json.dumps({"einreichen": {"eigener_dienst": True}}) + "\n",
        "chore: nur im Ticket (#7)",
    )
    sha = e411._commit(wt, "README.md", "aenderung\n", "docs: Text (#7)")

    erg = e411._einreichen(wt, "7")

    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert e411._ist_vorfahr(einreichen_welt["remote"], sha, e411._remote_main(einreichen_welt))
    assert any(a[:2] == ["pr", "merge"] for a in e411._gh_aufrufe(einreichen_welt))


def test_430_hat_einreich_dienst_streng_bool_und_vorgabe_false() -> None:
    assert config.DEFAULTS["einreichen"]["eigener_dienst"] is False
    assert config.hat_einreich_dienst(config.DEFAULTS) is False
    assert config.hat_einreich_dienst({"einreichen": {"eigener_dienst": True}}) is True
    for ungleich in (False, "true", 1, None, []):
        assert config.hat_einreich_dienst({"einreichen": {"eigener_dienst": ungleich}}) is False
    assert config.hat_einreich_dienst({"einreichen": "kaputt"}) is False
    assert config.hat_einreich_dienst({}) is False
