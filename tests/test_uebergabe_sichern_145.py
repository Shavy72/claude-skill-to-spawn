"""L5 #145: Übergaben + Entscheidungs-Log von selbst sichern, Zweige hochladen, Stand aktuell.

Alle Dateien liegen in ``tmp_path``; die Maschinen-Liste zeigt per ``TO_SPAWN_MASCHINE`` auf eine
Test-Datei. Einzige Ausnahme: der Stand-Prüftest liest ``docs/WIEDEREINSTIEG.md`` dieses Repos.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from to_spawn import konsole
from to_spawn import uebergabe_sichern as us

WURZEL = Path(__file__).resolve().parent.parent
#: Frist zwischen jüngstem Merge auf main und jüngstem Stand-Eintrag (Wissen liegt in ``uebergabe_sichern``).
STAND_FRIST = us.STAND_FRIST
STAND_ZEILE = us.STAND_ZEILE


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
    ).stdout.strip()


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "home"
    repo = home / "leit"
    repo.mkdir(parents=True)
    (repo / ".claude-handoff-loop.md").write_text("Übergabe 1\n", encoding="utf-8")
    (repo / "entscheidungen.md").write_text("E1 Grund\n", encoding="utf-8")
    extra = home / "systemcheck" / "notiz.md"
    extra.parent.mkdir()
    extra.write_text("Schnitt\n", encoding="utf-8")
    maschine = tmp_path / "maschine.json"
    maschine.write_text(
        json.dumps(
            {
                "repos": [{"slug": "test/leit", "pfad": str(repo)}],
                "sicherung": {"dateien": [str(home / "systemcheck" / "*.md")]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "tresor-fehlt"))
    monkeypatch.setenv(us.UMGEBUNG_ZIEL, str(tmp_path / "tresor"))
    return {"home": home, "repo": repo, "extra": extra, "tmp": tmp_path}


def test_sichert_uebergabe_log_und_muster_nur_bei_aenderung(
    welt: dict[str, Path],
) -> None:
    erg = us.sichern()
    assert erg.fehler == []
    assert erg.ziel is not None and erg.ziel.parent == welt["tmp"] / "tresor"
    namen = sorted(p.name for p in erg.ziel.iterdir())
    assert namen == [
        "leit__claude-handoff-loop.md",
        "leit__entscheidungen.md",
        "systemcheck__notiz.md",
    ]
    assert len(erg.kopiert) == 3
    assert us.sichern().kopiert == []  # unverändert → nichts neu geschrieben
    (welt["repo"] / ".claude-handoff-loop.md").write_text("Übergabe 2\n", encoding="utf-8")
    zweit = us.sichern()
    assert [p.name for p in zweit.kopiert] == [".claude-handoff-loop.md"]
    assert (erg.ziel / "leit__claude-handoff-loop.md").read_text(encoding="utf-8") == "Übergabe 2\n"


def test_ausdrueckliche_datei_wird_mitgesichert(welt: dict[str, Path]) -> None:
    handoff = welt["tmp"] / "wt" / "docs" / "handoffs" / "HANDOFF_x_waechter_7.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("Aufseher\n", encoding="utf-8")
    erg = us.sichern([handoff])
    assert erg.ziel is not None and (erg.ziel / "handoffs__HANDOFF_x_waechter_7.md").is_file()


def test_ziel_im_skill_repo_wird_verweigert(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(us, "_skill_wurzel", lambda: welt["tmp"] / "skill")
    monkeypatch.setenv(us.UMGEBUNG_ZIEL, str(welt["tmp"] / "skill" / "docs" / "sicherung"))
    erg = us.sichern()
    assert erg.ziel is None and erg.kopiert == []
    assert not (welt["tmp"] / "skill").exists()


def test_ziel_in_oeffentlichem_klon_wird_verweigert_privater_tresor_nicht(
    tmp_path: Path,
) -> None:
    klon = tmp_path / "klon"
    klon.mkdir()
    _git(klon, "init", "-q")
    _git(
        klon,
        "remote",
        "add",
        "origin",
        "https://github.com/Shavy72/claude-skill-to-spawn.git",
    )
    assert us.ist_oeffentlich(klon / "sicherung")
    tresor = tmp_path / "tresor"
    tresor.mkdir()
    _git(tresor, "init", "-q")
    _git(tresor, "remote", "add", "origin", "git@github.com:someone/obsidian-vault.git")
    assert not us.ist_oeffentlich(tresor / "to-spawn" / "sicherung")


def test_vorgabe_ziel_nie_im_oeffentlichen_repo(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Rot, sobald eine Vorgabe (ohne Konfig) ins Skill-Repo zeigt."""
    monkeypatch.delenv(us.UMGEBUNG_ZIEL)
    ohne_tresor = us.ziel_ordner()
    assert ohne_tresor.is_relative_to(welt["home"] / ".local" / "share")
    tresor = welt["tmp"] / "vault"
    tresor.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tresor))
    mit_tresor = us.ziel_ordner()
    assert mit_tresor.is_relative_to(tresor)
    for ziel in (ohne_tresor, mit_tresor):
        assert not us.ist_oeffentlich(ziel)
        assert not ziel.resolve().is_relative_to(WURZEL)


def test_zweige_hochladen_nie_ticket_oder_hauptzweig(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = welt["tmp"]
    origin = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp / "skill"
    _git(tmp, "clone", "-q", str(origin), str(repo))
    for k, v in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", k, v)
    _git(repo, "checkout", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "basis")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main lokal")
    for zweig in ("l5/arbeit", "ticket/9-x"):
        wt = tmp / zweig.replace("/", "-")
        _git(repo, "worktree", "add", "-q", "-b", zweig, str(wt), "main")
        _git(wt, "commit", "-q", "--allow-empty", "-m", f"wip {zweig}")
    monkeypatch.setenv(us.UMGEBUNG_ZWEIG_REPOS, str(repo))
    erg = us.sichern()
    assert erg.hochgeladen == ["skill:l5/arbeit"], erg.fehler
    entfernt = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    assert sorted(entfernt.split()) == ["l5/arbeit", "main"]
    assert _git(origin, "rev-list", "--count", "main") == "1"  # main lokal nicht hochgeladen
    assert us.sichern().hochgeladen == []  # nichts Neues → kein zweiter Push


def test_tuer_sichern_ueber_cli(welt: dict[str, Path]) -> None:
    datei = welt["tmp"] / "ober" / "ober_aufsicht.md"
    datei.parent.mkdir()
    datei.write_text("PC\n", encoding="utf-8")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    aus = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "sichern", str(datei)],
        cwd=welt["repo"],
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=60,
        check=False,
    )
    assert aus.returncode == 0, aus.stderr
    assert "4 Datei(en)" in aus.stdout
    assert list((welt["tmp"] / "tresor").glob("*/ober__ober_aufsicht.md"))


def _stand_veraltet(text: str, merge: datetime, frist: timedelta = STAND_FRIST) -> bool:
    """``True``, wenn der jüngste Stand-Eintrag älter ist als ``merge`` minus ``frist``."""
    return us.stand_veraltet(text, merge, frist)


def test_stand_pruefung_logik() -> None:
    text = "## 3. Stand\n\n- **2026-11-21 22:37 (Server-Uhr):** x\n- **2026-11-20 10:00:** y\n\n## 4. Weiter\n"
    assert not _stand_veraltet(text, datetime(2026, 11, 22, 12, 0))
    assert _stand_veraltet(text, datetime(2026, 11, 23, 0, 0))
    assert _stand_veraltet("## 3. Stand\n\nnichts\n", datetime(2026, 1, 1))


def test_stand_pruefung_zeitzone_berlin() -> None:
    """Stand-Zeiten gelten in Europe/Berlin (MEZ = UTC+1 im November), nicht als rohe Rechner-Ortszeit."""
    text = "## 3. Stand\n\n- **2026-11-24 01:30 (Europe/Berlin):** x\n\n## 4. Weiter\n"
    # 2026-11-24 01:30 MEZ entspricht 2026-11-24 00:30 UTC; +24 h = 2026-11-25 00:30 UTC.
    assert not us.stand_veraltet(text, datetime(2026, 11, 25, 0, 29, tzinfo=UTC))
    assert us.stand_veraltet(text, datetime(2026, 11, 25, 0, 31, tzinfo=UTC))


def test_juengster_main_stand_erkennt_squash_merge(tmp_path: Path) -> None:
    """Squash-Merges auf ``origin/main`` zählen mit; ein neuerer Feature-Commit ohne Merge nicht."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    for k, v in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", k, v)
    _git(repo, "checkout", "-q", "-b", "main")
    alt_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-11-20T10:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-11-20T10:00:00+00:00",
    }
    _git(repo, "commit", "-q", "--allow-empty", "-m", "basis", env=alt_env)
    squash_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-11-25T03:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-11-25T03:00:00+00:00",
    }
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fix: squash (#99)", env=squash_env)
    _git(repo, "push", "-q", "origin", "main")
    # Feature-Zweig mit viel neuerem Commit, noch nicht auf origin/main: darf nicht auslösen.
    _git(repo, "checkout", "-q", "-b", "feat/offen")
    zukunft_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-11-28T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-11-28T12:00:00+00:00",
    }
    _git(repo, "commit", "-q", "--allow-empty", "-m", "wip", env=zukunft_env)

    stand = us.juengster_main_stand(repo)
    assert stand is not None
    assert stand.astimezone(UTC) == datetime(2026, 11, 25, 3, 0, tzinfo=UTC)
    veraltet = "## 3. Stand\n\n- **2026-11-23 22:16 (Europe/Berlin):** alt\n\n## 4. Weiter\n"
    aktuell = "## 3. Stand\n\n- **2026-11-25 03:30 (Europe/Berlin):** neu\n\n## 4. Weiter\n"
    assert us.stand_veraltet(veraltet, stand)
    assert not us.stand_veraltet(aktuell, stand)


def test_zweig_repo_pfad_mit_leerzeichen(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Repo-Pfad mit Leerzeichen (z. B. Windows-Benutzerordner) wird in ``UMGEBUNG_ZWEIG_REPOS`` nicht zerschnitten."""
    basis = welt["tmp"] / "mein ordner"
    basis.mkdir()
    origin = basis / "origin.git"
    _git(basis, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = basis / "skill repo"
    _git(basis, "clone", "-q", str(origin), str(repo))
    for k, v in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", k, v)
    _git(repo, "checkout", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "basis")
    _git(repo, "push", "-q", "origin", "main")
    wt = basis / "wt arbeit"
    _git(repo, "worktree", "add", "-q", "-b", "l5/leerzeichen", str(wt), "main")
    _git(wt, "commit", "-q", "--allow-empty", "-m", "wip")
    monkeypatch.setenv(us.UMGEBUNG_ZWEIG_REPOS, str(repo))
    erg = us.sichern()
    assert erg.fehler == [], erg.fehler
    assert erg.hochgeladen == ["skill repo:l5/leerzeichen"]


def test_wiedereinstieg_enthaelt_kaltstart_bausteine() -> None:
    """M15 (#145): WIEDEREINSTIEG nennt Zeitzone, Erstinstallation (Linux+Windows), SSH-Alias, Rückfall, Glossar."""
    text = (WURZEL / "docs" / "WIEDEREINSTIEG.md").read_text(encoding="utf-8")
    for pflicht in (
        "Europe/Berlin",
        "install.sh",
        "install.ps1",
        "~/.ssh/config",
        "sitzung_env.py",
        "Hartes: nein",
        "Probelauf",
        "## 6. Glossar",
    ):
        assert pflicht in text, f"docs/WIEDEREINSTIEG.md fehlt Kaltstart-Baustein {pflicht!r}"


def test_wiedereinstieg_stand_nicht_aelter_als_juengster_merge() -> None:
    """Deterministischer Stand-Check (#145, #353): prüft Format, absteigende Reihenfolge und
    Konsistenz mit dem im obersten Eintrag zitierten ``main``-Commit, damit fremde PR-Merges
    auf ``origin/main`` die Code-Suite nicht mehr 24 h später rot schalten. Die Live-Frische
    gegen ``origin/main`` meldet ``to_spawn.py sichern --pruefen`` als Hinweis (und lässt sich
    per ``TO_SPAWN_PRUEFE_STAND_FRISCHE=1`` hier ausdrücklich scharfschalten)."""
    text = (WURZEL / "docs" / "WIEDEREINSTIEG.md").read_text(encoding="utf-8")
    zeiten = us.stand_zeitpunkte(text)
    assert zeiten, "docs/WIEDEREINSTIEG.md: kein lesbarer Zeitstempel im Abschnitt „## 3. Stand“."
    assert zeiten[0] == max(zeiten), (
        f"docs/WIEDEREINSTIEG.md: oberster Stand-Eintrag ({zeiten[0]:%Y-%m-%d %H:%M}) "
        f"ist nicht der jüngste ({max(zeiten):%Y-%m-%d %H:%M})."
    )
    ref = us.stand_referenz_commit(text)
    if ref is not None:
        ref_zeit = us.commit_zeitpunkt(WURZEL, ref)
        if ref_zeit is not None:
            assert not _stand_veraltet(text, ref_zeit), (
                f"docs/WIEDEREINSTIEG.md: Stand-Eintrag älter als zitierter main-Commit {ref} "
                f"({ref_zeit:%Y-%m-%d %H:%M %Z}) minus {STAND_FRIST}."
            )
    if os.environ.get("TO_SPAWN_PRUEFE_STAND_FRISCHE") == "1":
        merge = us.juengster_main_stand(WURZEL)
        if merge is None:
            pytest.skip("kein vollständiger git-Verlauf (flacher Klon)")
        assert not _stand_veraltet(text, merge), (
            f"docs/WIEDEREINSTIEG.md: jüngster Stand-Eintrag älter als jüngster main-Stand "
            f"{merge:%Y-%m-%d %H:%M %Z} minus {STAND_FRIST} — Abschnitt „Stand“ fortschreiben (L5)."
        )
