"""Fertig einreichen ohne Frage (#411): der eine Weg eines fertigen Stands auf den Hauptzweig.

Echte Git-Repos (Bare-Remote + Klon + Ticket-Worktree) in Temp-Ordnern; nur ``gh`` ist eine
Attrappe, die einen Pull-Request wie GitHub mit Merge-Commit in das Bare-Remote mergt.
Der Befehl läuft als Kind-Prozess über die CLI, genau wie eine Bau-Session/der Aufseher ihn ruft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import attrappe

from to_spawn import anleitung, befugnis

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

GH_ATTRAPPE = r"""
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["EINREICHEN_GH_LOG"])
with log.open("a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
if args[:2] == ["pr", "list"]:
    print("")
elif args[:2] == ["pr", "create"]:
    print("https://github.com/test-org/test-repo/pull/41")
elif args[:2] == ["pr", "merge"]:
    head = args[args.index("--match-head-commit") + 1]
    origin = git("remote", "get-url", "origin")
    stand = json.loads(log.with_suffix(".json").read_text(encoding="utf-8"))
    tmp = Path(stand["kratz"])
    if not tmp.exists():
        git("clone", "-q", origin, str(tmp))
        git("config", "user.email", "gh@test", cwd=tmp)
        git("config", "user.name", "gh", cwd=tmp)
    git("fetch", "-q", "origin", cwd=tmp)
    git("checkout", "-q", "-B", stand["haupt"], "origin/" + stand["haupt"], cwd=tmp)
    git("merge", "-q", "--no-ff", "-m", "Merge pull request #41", head, cwd=tmp)
    git("push", "-q", "origin", stand["haupt"], cwd=tmp)
    log.with_suffix(".merged").write_text(head, encoding="utf-8")
    if os.environ.get("EINREICHEN_SPERRE_NACH_MERGE"):  # #476: paralleles Ticket hält origin/<haupt>
        common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        sperre = common / "refs" / "remotes" / "origin" / (stand["haupt"] + ".lock")
        sperre.parent.mkdir(parents=True, exist_ok=True)
        sperre.write_text("", encoding="utf-8")
elif args[:2] == ["pr", "view"]:
    print("MERGED" if log.with_suffix(".merged").exists() else "OPEN")
else:
    sys.exit(9)
"""


def _git(ordner: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ordner), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(ordner: Path, datei: str, inhalt: str, betreff: str) -> str:
    pfad = ordner / datei
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    _git(ordner, "add", "--", datei)
    _git(ordner, "commit", "-q", "-m", betreff)
    return _git(ordner, "rev-parse", "HEAD")


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Bare-Remote ``main`` mit kleinem Python-Projekt, Klon und gh-Attrappe."""
    remote, klon = tmp_path / "origin.git", tmp_path / "klon"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(klon)], check=True, capture_output=True)
    _git(klon, "config", "user.email", "t@test")
    _git(klon, "config", "user.name", "t")
    _git(klon, "checkout", "-q", "-b", "main")
    _commit(klon, "pyproject.toml", "[tool.pytest.ini_options]\n", "chore: Projekt")
    _commit(klon, "rechnen.py", "def plus(a, b):\n    return a + b\n", "chore: Start")
    _commit(
        klon,
        "test_rechnen.py",
        "from rechnen import plus\n\n\ndef test_plus():\n    assert plus(1, 2) == 3\n",
        "test: Start",
    )
    _git(klon, "push", "-q", "-u", "origin", "main")
    gh_ordner = tmp_path / "gh-bin"
    stub = attrappe(gh_ordner, "gh", GH_ATTRAPPE)
    log = tmp_path / "gh.log"
    log.with_suffix(".json").write_text(
        json.dumps({"kratz": str(tmp_path / "gh-kratz"), "haupt": "main"}), encoding="utf-8"
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("EINREICHEN_GH_LOG", str(log))
    return {"remote": remote, "klon": klon, "log": log, "tmp": tmp_path}


def _ticket_wt(welt: dict[str, Path], zweig: str = "ticket/7-plus-doku") -> Path:
    wt = welt["tmp"] / "wt-7"
    _git(welt["klon"], "worktree", "add", "-q", "-b", zweig, str(wt), "origin/main")
    return wt


def _einreichen(ordner: Path, nummer: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "einreichen", nummer],
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def _remote_main(welt: dict[str, Path]) -> str:
    return _git(welt["remote"], "rev-parse", "main")


def _ist_vorfahr(ordner: Path, a: str, b: str) -> bool:
    return subprocess.run(["git", "-C", str(ordner), "merge-base", "--is-ancestor", a, b], check=False).returncode == 0


def _gh_aufrufe(welt: dict[str, Path]) -> list[list[str]]:
    if not welt["log"].exists():
        return []
    return [json.loads(z) for z in welt["log"].read_text(encoding="utf-8").splitlines() if z]


# --- Anleitungen: keine Bau-Session wird mehr zum Fragen geschickt ---------------------------


def test_bau_vorlage_reicht_ein_statt_fast_forward_push() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    text = vorlage["prompt_template"]
    assert "to_spawn.py einreichen {N}" in text
    assert "fast-forward nach origin/{HAUPTZWEIG}" not in text


def test_befugnis_einreichen_ist_klasse_1_ohne_frage() -> None:
    start = befugnis.start_text(7)
    assert "to_spawn.py einreichen" in start
    assert "jeder Push oder Merge auf den Hauptzweig oder fremde Zweige" not in start
    assert befugnis.einordnen("hauptzweig", "einreichen") == 1


def test_aufseher_und_abloese_sop_reichen_ein() -> None:
    sys.path.insert(0, str(SKILL / "skripte"))
    import wache  # noqa: PLC0415

    assert "to_spawn.py einreichen" in anleitung.ABLOESE_SOP
    assert wache.PROMPT.count("einreichen {S}") >= 4
    assert "committen + pushen" not in wache.PROMPT
    assert "[skip ci], Push." not in wache.PROMPT
    assert "`git diff --quiet`), Push." not in wache.PROMPT


@pytest.mark.parametrize("art", ["pushen", "mergen", "merge", "push"])
def test_direkter_push_oder_merge_auf_hauptzweig_bleibt_klasse_3(art: str) -> None:
    assert befugnis.einordnen("hauptzweig", art) == 3
    assert befugnis.einordnen("master", "einreichen") == 3


def test_aufseher_takt_reicht_ein_statt_push() -> None:
    from to_spawn import waechter_takt  # noqa: PLC0415

    assert waechter_takt.PROMPT.count("to_spawn.py einreichen {S}") >= 2
    assert "committen + pushen" not in waechter_takt.PROMPT


# --- Der Weg selbst, echte Repos -------------------------------------------------------------


def test_ticket_kommt_ohne_frage_auf_hauptzweig(welt: dict[str, Path]) -> None:
    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "plus rechnet\n", "docs: plus erklärt (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert "EINGEREICHT" in erg.stdout
    assert _ist_vorfahr(welt["remote"], sha, _remote_main(welt))
    aufrufe = _gh_aufrufe(welt)
    assert ["pr", "merge", "41", "--merge", "--match-head-commit", sha] in aufrufe
    assert not any("--admin" in a for a in aufrufe)


def test_aufseher_uebergabe_master_gleich_origin(welt: dict[str, Path]) -> None:
    klon = welt["klon"]
    _commit(klon, "docs/HANDOFF_2026-11-24_waechter_1.md", "Stand\n", "docs(aufseher): Handoff [skip ci]")
    erg = _einreichen(klon, "1")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    _git(klon, "fetch", "-q", "origin")
    assert _git(klon, "rev-parse", "main") == _git(klon, "rev-parse", "origin/main") == _remote_main(welt)
    assert "ticket/1-uebergabe" in _git(welt["remote"], "branch", "--list", "ticket/*")


def test_aufseher_darf_vom_hauptzweig_keinen_code_einreichen(welt: dict[str, Path]) -> None:
    vorher = _remote_main(welt)
    _commit(welt["klon"], "rechnen.py", "def plus(a, b):\n    return a - b\n", "fix: still (#1)")
    erg = _einreichen(welt["klon"], "1")
    assert erg.returncode == 1
    assert "Code gehört in ein Ticket" in erg.stdout
    assert _remote_main(welt) == vorher
    assert _gh_aufrufe(welt) == []


def test_rote_pruefung_pusht_nichts(welt: dict[str, Path]) -> None:
    wt = _ticket_wt(welt)
    vorher = _remote_main(welt)
    # #425: je Ticket läuft nur die schnelle Prüfung, nie der volle Lauf. Ohne Test-Auswahl
    # (pytest-impacted fehlt) prüft darum der eingerichtete Befehl pruefung.schnell die Tests.
    schnell = f"{Path(sys.executable).as_posix()} -m pytest -q"
    _commit(wt, ".to-spawn/config.json", json.dumps({"pruefung": {"schnell": schnell}}), "chore: Prüfung (#7)")
    _commit(wt, "rechnen.py", "def plus(a, b):\n    return a - b\n", "fix: kaputt (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 1
    assert "Prüfung rot (Exit 1)" in erg.stdout
    assert _remote_main(welt) == vorher
    assert _git(welt["remote"], "branch", "--list", "ticket/*") == ""


def test_hauptzweig_weiter_erst_einarbeiten(welt: dict[str, Path]) -> None:
    wt = _ticket_wt(welt)
    _commit(welt["klon"], "docs/a.md", "a\n", "docs: fremd")
    _git(welt["klon"], "push", "-q", "origin", "main")
    _commit(wt, "README.md", "x\n", "docs: x (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 1
    assert "einarbeiten" in erg.stdout


def test_fremder_zweig_ist_aufruf_falsch(welt: dict[str, Path]) -> None:
    wt = _ticket_wt(welt, zweig="ticket/8-fremd")
    _commit(wt, "README.md", "x\n", "docs: x (#8)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 5
    assert _gh_aufrufe(welt) == []


def test_projekt_mit_eigenem_einreich_dienst_nur_ticket_zweig(welt: dict[str, Path]) -> None:
    klon = welt["klon"]
    _commit(klon, "scripts/hooks/install_git_hooks.sh", "# eigene Sperre\n", "chore: eigene Sperre")
    _git(klon, "push", "-q", "origin", "main")
    vorher = _remote_main(welt)
    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "x\n", "docs: x (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert _remote_main(welt) == vorher
    assert _git(welt["remote"], "rev-parse", "ticket/7-plus-doku") == sha
    assert _gh_aufrufe(welt) == []


def test_repo_ohne_python_und_node_reicht_ein(welt: dict[str, Path]) -> None:
    """Reines Doku-/Skript-Repo: nichts zu testen ist kein Werkzeug-Fehler (#411)."""
    klon = welt["klon"]
    _git(klon, "rm", "-q", "pyproject.toml", "rechnen.py", "test_rechnen.py")
    _git(klon, "commit", "-q", "-m", "chore: nur Doku")
    _git(klon, "push", "-q", "origin", "main")
    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "nur Text\n", "docs: Text (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert "ohne Tests" in erg.stdout
    assert _ist_vorfahr(welt["klon"], sha, _remote_main(welt))


@pytest.mark.parametrize("stelle", ["gesperrte_pfade", "merge-base", "cat-file"])
def test_werkzeug_bricht_ab_meldet_rot_statt_traceback(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, stelle: str
) -> None:
    """OSError/Zeitüberschreitung an jeder Git-Stelle → ``Ergebnis`` mit Exit 1, kein Traceback."""
    from to_spawn import einreichen  # noqa: PLC0415

    klon = welt["klon"]
    if stelle == "cat-file":  # erst ab Push erreicht: Doku-Übergabe des Aufsehers, ohne Prüf-Tür
        _commit(klon, "docs/u.md", "u\n", "docs: u")
        ordner = klon
    else:
        ordner = _ticket_wt(welt)
        _commit(ordner, "README.md", "x\n", "docs: x (#7)")
    if stelle == "gesperrte_pfade":

        def kaputt(*_a: object, **_k: object) -> list[str]:
            raise OSError("Platte weg")

        monkeypatch.setattr(einreichen.uebergabe_sichern, "gesperrte_pfade", kaputt)
    else:
        echt = subprocess.run

        def lauf(befehl: list[str], *a: object, **k: object) -> object:
            if stelle in befehl:
                raise subprocess.TimeoutExpired(befehl, 1)
            return echt(befehl, *a, **k)

        monkeypatch.setattr(einreichen.subprocess, "run", lauf)
    erg = einreichen.einreichen(ordner, "1" if stelle == "cat-file" else "7")
    assert erg.exit == 1
    assert erg.text


# --- Parallele Tickets im selben Klon (#476, F15) --------------------------------------------


def test_sperre_auf_origin_nach_merge_meldet_eingereicht(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hält ein paralleles Ticket ``origin/main`` gesperrt, zählt der echte Pull-Request-Stand.

    Die gh-Attrappe legt nach dem Merge die Ref-Sperre im gemeinsamen Git-Ordner an — genau
    wie ein gleichzeitiges ``git fetch`` eines anderen Tickets im selben Klon.
    """
    monkeypatch.setenv("EINREICHEN_SPERRE_NACH_MERGE", "1")
    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "plus rechnet\n", "docs: plus erklärt (#7)")
    erg = _einreichen(wt, "7")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    assert erg.stdout.startswith("EINGEREICHT"), erg.stdout
    assert "#41" in erg.stdout
    assert _ist_vorfahr(welt["remote"], sha, _remote_main(welt))


def test_sperre_beim_fetch_wird_kurz_wiederholt(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Vorübergehende Ref-Sperre beim ersten ``fetch``: kurz warten, erneut versuchen (#476)."""
    from to_spawn import einreichen

    wt = _ticket_wt(welt)
    sha = _commit(wt, "README.md", "x\n", "docs: x (#7)")
    echt = subprocess.run
    fehlschlaege = {"fetch": 0}

    def lauf(befehl: list[str], *a: object, **k: object) -> object:
        if "fetch" in befehl and fehlschlaege["fetch"] == 0:
            fehlschlaege["fetch"] += 1
            return subprocess.CompletedProcess(
                befehl, 1, "", "error: cannot lock ref 'refs/remotes/origin/main': Unable to create "
                "'.git/refs/remotes/origin/main.lock': File exists."
            )  # fmt: skip
        return echt(befehl, *a, **k)

    monkeypatch.setattr(einreichen.subprocess, "run", lauf)
    monkeypatch.setattr(einreichen, "SPERRE_PAUSE_S", 0.0, raising=False)
    erg = einreichen.einreichen(wt, "7")
    assert erg.exit == 0, erg.text
    assert fehlschlaege["fetch"] == 1
    assert _ist_vorfahr(welt["remote"], sha, _remote_main(welt))
