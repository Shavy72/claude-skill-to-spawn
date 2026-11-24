"""#499: Zwei Repos ohne eigenes ``worktree_basis`` teilen sich ``~/wt/wt-<N>``.

Jedes frische Repo beginnt bei Ticket #1; ohne ``worktree_basis`` in der Repo-Konfig zeigt
``config.worktree_pfad`` für beide Repos auf denselben Ordner. Sicherer Kern (Muster wie
``spec-<S>`` in #457): die Start-Stelle verweigert mit Exit 9, wenn der Ordner schon ein
Worktree eines anderen Repos ist (Repo-Kennung = ``origin``-Adresse, ohne ``origin`` der
gemeinsame Git-Ordner). Eigenes Repo, ein anderer Klon desselben Repos und ein noch
nicht vorhandener Ordner starten wie bisher — laufende ``wt-<N>`` werden weiter gefunden.

Echt: git (Wegwerf-Repos im Temp-Ordner), ``startstelle.betreten``. Keine Attrappe für git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn import config, startstelle

TICKET = "5"


def _git(ort: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(ort), *args], check=True, capture_output=True, text=True
    )


def _repo(tmp: Path, name: str) -> Path:
    """Bare-Remote + Klon mit einem Commit; ``origin`` zeigt auf das eigene Bare-Repo."""
    fern = tmp / f"{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(fern)], check=True)
    repo = tmp / name
    subprocess.run(
        ["git", "clone", "-q", str(fern), str(repo)], check=True, capture_output=True
    )
    _git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "start",
    )
    _git(repo, "push", "-q", "origin", "HEAD")
    return repo


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    a, b = _repo(tmp_path, "repo-a"), _repo(tmp_path, "repo-b")
    wt = Path(config.worktree_pfad(TICKET, a)).expanduser()
    assert wt == Path(config.worktree_pfad(TICKET, b)).expanduser(), (
        "Ausgangslage: beide Repos teilen wt-5"
    )
    _git(a, "worktree", "add", "-q", "-b", f"ticket/{TICKET}-a", str(wt))
    return {"tmp": tmp_path, "a": a, "b": b, "wt": wt}


def _start(repo: Path, slug: str, ticket: str = TICKET) -> startstelle.Entscheidung:
    return startstelle.betreten(ticket, slug, repo, pruefer=lambda _t, _c: True)


def test_fremdes_repo_wird_beim_start_verweigert(welt: dict[str, Path]) -> None:
    entscheidung = _start(welt["b"], "test/repo-b-499")
    assert not entscheidung.erlaubt, entscheidung.grund
    assert entscheidung.exit_code == 9
    assert str(welt["wt"]) in entscheidung.grund and "repo-a" in entscheidung.grund


def test_eigenes_repo_und_zweiter_klon_starten_weiter(welt: dict[str, Path]) -> None:
    assert config.worktree_fremd(TICKET, welt["a"]) is None
    assert _start(welt["a"], "test/repo-a-499").erlaubt
    klon = welt["tmp"] / "repo-a-klon"
    subprocess.run(
        ["git", "clone", "-q", str(welt["tmp"] / "repo-a.git"), str(klon)], check=True
    )
    assert config.worktree_fremd(TICKET, klon) is None, (
        "anderer Klon desselben Repos ist dasselbe Repo"
    )


def test_ohne_origin_entscheidet_der_git_ordner(welt: dict[str, Path]) -> None:
    for repo in (welt["a"], welt["b"]):
        _git(repo, "remote", "remove", "origin")
    assert config.worktree_fremd(TICKET, welt["a"]) is None
    assert config.worktree_fremd(TICKET, welt["b"]) is not None


def test_fehlender_ordner_ist_frei(welt: dict[str, Path]) -> None:
    assert config.worktree_fremd("6", welt["b"]) is None
    assert _start(welt["b"], "test/repo-b-499-frei", "6").erlaubt is True


def test_probesitz_verweigert_fremden_ordner_und_loescht_ihn_nicht(welt: dict[str, Path]) -> None:
    from to_spawn import probesitz

    (welt["wt"] / "arbeit.txt").write_text("fremd", encoding="utf-8")

    def laeufer(argv: list[str], cwd: Path | None = None, timeout: float = 0) -> subprocess.CompletedProcess[str]:
        if Path(argv[0]).name == "gh":
            ausgabe = "https://github.com/x/y/issues/5\n" if "create" in argv else ""
            return subprocess.CompletedProcess(argv, 0, ausgabe, "")
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)

    ergebnisse = probesitz.wegwerf_lauf(
        welt["b"],
        config.lade(welt["b"]),
        laeufer=laeufer,
        which=lambda name: f"/usr/bin/{name}",
        worktree_anlegen=lambda *_a: pytest.fail("darf im fremden Ordner nicht anlegen"),
        session_starter=lambda *_a, **_k: pytest.fail("darf nicht starten"),
    )
    assert not ergebnisse[2].ok and "anderen Repos" in ergebnisse[2].grund
    assert (welt["wt"] / "arbeit.txt").read_text(encoding="utf-8") == "fremd"


@pytest.mark.parametrize(
    "adresse",
    [
        "git@github.com:Wer/Repo.git",
        "https://github.com/wer/repo/",
        "https://user@github.com/Wer/Repo.git",
        "ssh://git@github.com/wer/repo",
        "https://github.com/wer/repo",
    ],
)
def test_adresse_normal_gleiche_kennung_je_schreibweise(adresse: str) -> None:
    assert config._adresse_normal(adresse) == "github.com/wer/repo"


def test_adresse_normal_verschiedene_repos_bleiben_verschieden() -> None:
    assert config._adresse_normal("git@github.com:wer/repo-a.git") != config._adresse_normal("git@github.com:wer/repo-b.git")
    assert config._adresse_normal("https://github.com/a/repo") != config._adresse_normal("https://github.com/b/repo")


def test_adresse_normal_github_pfad_gross_klein_egal() -> None:
    assert config._adresse_normal("https://github.com/Shavy72/x") == config._adresse_normal("git@GitHub.com:shavy72/x.git")


def test_adresse_normal_fremder_host_pfad_unterscheidet_gross_klein() -> None:
    assert config._adresse_normal("https://git.example.org/Wer/Repo") != config._adresse_normal("https://git.example.org/wer/repo")


def test_adresse_normal_host_gross_klein_egal() -> None:
    assert config._adresse_normal("https://Git.Example.ORG/Wer/Repo") == config._adresse_normal("ssh://git@git.example.org/Wer/Repo.git")
