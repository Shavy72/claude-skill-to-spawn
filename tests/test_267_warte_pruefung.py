"""Warte-Prüfung der Bau-Session fragt dieselbe Regel wie die Start-Schranke (#267).

Die Start-Schranke (``bau.py`` → ``gh.blocker_erledigt``) lässt ein Ticket los, sobald
jeder Vorgänger zu ist oder ``staging:fertig`` trägt. Die Warte-Schleife im Loop-Prompt
fragte bisher GitHubs Zähler ``issue_dependencies_summary.blocked_by`` — der kennt das
Label nicht, die Session wartete zu Unrecht. Jetzt fragt der Prompt den Unterbefehl
``to_spawn.py blocker <N>``; Zustand und Commit-Prüfung liegen nur in
``gh.blocker_gruende``. Echt ist die ganze Tür samt git, ersetzt wird nur ``gh``
(externer Dienst, Stub über ``TO_SPAWN_GH_STUB`` bzw. im PATH) und das Schlafen.
"""

from __future__ import annotations

import functools
import importlib.util
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import capo  # noqa: E402

_ZAEHLER = itertools.count()

#: Muster, mit denen ein Prompt oder eine Doku die Blocker-Regel selbst ausformuliert,
#: statt ``to_spawn.py blocker <N>`` zu nennen. Eng gehalten: nur Abfrage-/Prüfbefehle,
#: keine Fließtext-Wörter wie „Blocker offen“ (die stehen zu Recht in Lastenheft/Plan).
REGEL_MUSTER = {
    "GitHub-Zähler kennt staging:fertig nicht": re.compile(r"issue_dependencies_summary"),
    # #269: auch Kurzform ``-q`` und Filter weiter hinten in der Zeile (``… --paginate --jq``).
    "eigene Blocker-Abfrage per jq": re.compile(r"dependencies/blocked_by\S*[^\n]*?\s(?:--jq|-q)\b"),
    # #269: jede Nummern-Form — ``(#<Blocker>)``, ``(#5)``, ``(#{N})`` —, mit ``=``, Leerzeichen, Anführung.
    "eigene Commit-Prüfung des Blockers": re.compile(
        r"--grep[= ]\\?[\"']?\(#(?:<\w+>|\{\w+\}|\d+)\)"  # \" = JSON-Escape
    ),
}
#: Stellen, die ein Muster tragen dürfen, weil sie damit nichts freigeben.
ERLAUBT = {
    # Nur Vorfilter/Anzeige; ob blockiert, entscheidet dort gh.offene_blocker (#1527).
    "to_spawn/ticketstand.py",
    # Die eine Stelle der Regel (#267).
    "to_spawn/gh.py",
    # Dieser Wächter: Muster-Proben stehen hier als Text (#269).
    "tests/test_267_warte_pruefung.py",
    # Aufgezeichnete GitHub-Antworten mit dem Zähler-Feld (Vorfilter von ticketstand).
    "tests/test_ticketstand.py",
    "tests/test_blocker_staging_1527.py",
}
#: Testdaten (aufgezeichnete GitHub-Antworten, Attrappen) sind keine Entscheidung. Nur die
#: Attrappen-Ordner, nicht ganz ``tests/`` (#269): ein Test, der die Regel nachbaut, fällt auf.
ERLAUBT_ORDNER = ("tests/hilfen/",)


def _dep(nr: int, state: str, *labels: str) -> dict[str, Any]:
    return {"number": nr, "state": state, "labels": [{"name": x} for x in labels]}


def _repo(tmp_path: Path, *betreffe: str) -> Path:
    """Echtes git-Repo mit ``origin/master``, das die Commits ``betreffe`` trägt."""
    repo = tmp_path / "repo"
    ursprung = tmp_path / "ursprung.git"

    def git(*args: str, cwd: Path = repo) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    repo.mkdir()
    git("init", "-q", "--bare", "-b", "master", str(ursprung), cwd=tmp_path)
    git("init", "-q", "-b", "master")
    for betreff in betreffe or ("chore: Anfang",):
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", betreff)
    git("remote", "add", "origin", str(ursprung))
    git("push", "-q", "origin", "master")
    git("fetch", "-q", "origin")
    return repo


def _stub(tmp_path: Path, deps: object | None) -> Path:
    """gh-Attrappe: liefert ``deps`` als JSON; ``None`` = Abfrage scheitert (HTTP 502)."""
    stub = tmp_path / "gh_stub.py"
    if deps is None:
        stub.write_text("import sys\nsys.stderr.write('HTTP 502')\nsys.exit(1)\n", encoding="utf-8")
    else:
        daten = tmp_path / "deps.json"
        daten.write_text(json.dumps(deps), encoding="utf-8")
        stub.write_text(
            f"import sys\nsys.stdout.write(open({str(daten)!r}, encoding='utf-8').read())\n",
            encoding="utf-8",
        )
    return stub


def _lauf(argv: list[str], repo: Path, stub: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TO_SPAWN_GH_STUB": str(stub), "TO_SPAWN_REPO": str(repo)}
    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True, encoding="utf-8", env=env, cwd=repo, check=False
    )


def _blocker(repo: Path, stub: Path) -> subprocess.CompletedProcess[str]:
    return _lauf([str(SKILL / "to_spawn.py"), "blocker", "7", "--gh-repo", "test/spiegel"], repo, stub)


# --- Der Befehl: eine Zeile, Exit 0 = FREI, sonst WARTET --------------------------


@pytest.mark.parametrize(
    ("deps", "code", "zeile"),
    [
        ([_dep(5, "open", "staging:fertig")], 0, "FREI"),
        ([_dep(5, "closed")], 0, "FREI"),
        ([], 0, "FREI"),
        ([_dep(5, "open")], 1, "WARTET: #5 offen"),
        ([_dep(6, "open"), _dep(5, "open", "staging:fertig"), _dep(4, "open")], 1, "WARTET: #6 offen; #4 offen"),
        (None, 1, "WARTET: Blocker-Abfrage fehlgeschlagen"),
    ],
)
def test_blocker_befehl(tmp_path: Path, deps: object | None, code: int, zeile: str) -> None:
    repo = _repo(tmp_path, "feat: Vorgänger (#5) [skip ci]")
    erg = _blocker(repo, _stub(tmp_path, deps))
    assert erg.returncode == code, erg.stderr
    assert erg.stdout.strip() == zeile


@pytest.mark.parametrize("dep", [_dep(5, "open", "staging:fertig"), _dep(5, "closed")])
def test_blocker_befehl_ohne_commit_auf_hauptzweig_wartet(tmp_path: Path, dep: dict[str, Any]) -> None:
    """Commit-Prüfung gilt für staging:fertig genauso wie für zu — keine Sonderregel."""
    repo = _repo(tmp_path, "chore: anderes (#9)")
    erg = _blocker(repo, _stub(tmp_path, [dep]))
    assert erg.returncode == 1, erg.stderr
    wort = "zu" if dep["state"] == "closed" else "fertig gebaut"  # Wortlaut je Zustand (#269)
    assert erg.stdout.strip().startswith(f"WARTET: #5 {wort}, aber kein Commit „(#5)“ auf origin/master")


# --- Eine Stelle: bau.py und der Befehl fragen dieselbe Funktion -------------------


def _lade_bau(repo: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test/spiegel")
    monkeypatch.chdir(repo)
    modulname = f"bau_267_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKILL / "skripte" / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = bau
    try:
        spec.loader.exec_module(bau)
    finally:
        sys.modules.pop(modulname, None)
    bau.GH_REPO = "test/spiegel"
    return bau


#: Commit-Suche nach Ticketnummer in beiden Schreibweisen: ``"--grep=(#…)"`` und
#: getrennt ``"--grep", f"(#…)"`` (#320: so stand sie in ``skripte/spec_stand.py``).
COMMIT_SUCHE = re.compile(r"""--grep(?:=|["']\s*,\s*f?["'])\(#""")


def test_commit_pruefung_nur_in_gh() -> None:
    """Die Commit-Suche ``--grep=(#<Blocker>)`` steht nur in gh.py (keine zweite Wahrheit)."""
    treffer = [
        p.relative_to(SKILL).as_posix()
        for p in [*SKILL.glob("to_spawn/*.py"), *SKILL.glob("skripte/*.py"), SKILL / "to_spawn.py"]
        if COMMIT_SUCHE.search(p.read_text(encoding="utf-8"))
    ]
    assert treffer == ["to_spawn/gh.py"]


@pytest.mark.parametrize(
    "zeile",
    [
        '["log", "--format=%s", "--fixed-strings", f"--grep=(#{nummer})"]',
        'sh("git", "log", haupt, "--oneline", "-1", "--fixed-strings", "--grep", f"(#{n})")',
        "['--grep', '(#5)']",
    ],
)
def test_commit_suche_muster_findet_beide_schreibweisen(zeile: str) -> None:
    assert COMMIT_SUCHE.search(zeile)


# --- Weg über respawn/--sofort (#267-Nachtrag): Start-Schranke → Session-Prüfung ----


@pytest.mark.parametrize(
    ("labels", "frei"),
    [(("staging:fertig",), True), ((), False)],
)
def test_weg_respawn_sofort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, labels: tuple[str, ...], frei: bool
) -> None:
    repo = _repo(tmp_path, "feat: Vorgänger (#5) [skip ci]")
    stub = _stub(tmp_path, [_dep(5, "open", *labels)])
    # 1) respawn startet die Folge-Runde mit ``bau.py <N> --sofort``.
    start = capo.folge_befehl(repo, 9, 7, None)
    assert start[-2:] == ["7", "--sofort"] and start[1].endswith("bau.py")
    # 2) bau.py --sofort: Start-Schranke (echt, nur gh im PATH ersetzt und Schlafen gesperrt).
    bau = _lade_bau(repo, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    attrappe(bin_dir, "gh", f"import runpy\nrunpy.run_path({str(stub)!r}, run_name='__main__')\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    class Wartet(Exception):
        pass

    def _schlafen(_s: float) -> None:
        raise Wartet

    # Echtes auf_blocker_warten, nur das Schlafen (Vorgabe-Argument) gesperrt.
    monkeypatch.setattr(bau, "auf_blocker_warten", functools.partial(bau.auf_blocker_warten, schlafen=_schlafen))
    args = Namespace(sofort=True, umzug=None, resume=None, probesitz=False, takt=60)
    if frei:
        bau.blocker_schranke("7", args)
    else:
        with pytest.raises(Wartet):
            bau.blocker_schranke("7", args)
        return
    # 3) Die Session prüft selbst — mit dem Befehl aus ihrem echten Prompt.
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    prompt = bau.build_prompt(vorlage["prompt_template"], "7", "9", "Titel", "", konfig={})
    befehl = re.search(r"prüfe Blocker \((python \S+ blocker 7 --gh-repo \S+)", prompt)
    assert befehl is not None, "Prompt nennt den Blocker-Befehl nicht"
    argv = shlex.split(befehl.group(1))
    assert argv[1].endswith("to-spawn/to_spawn.py")
    erg = _lauf([str(SKILL / "to_spawn.py"), *argv[2:]], repo, stub)
    assert (erg.returncode, erg.stdout.strip()) == (0, "FREI"), erg.stderr


def test_weg_session_wartet_ohne_label(tmp_path: Path) -> None:
    """Session-Seite ohne Label: der Befehl aus dem Prompt meldet WARTET."""
    repo = _repo(tmp_path, "feat: Vorgänger (#5) [skip ci]")
    erg = _blocker(repo, _stub(tmp_path, [_dep(5, "open")]))
    assert (erg.returncode, erg.stdout.strip()) == (1, "WARTET: #5 offen")


# --- Prompt und Doku: nennen den Befehl, formulieren die Regel nicht selbst ---------


def test_prompt_fragt_den_blocker_befehl() -> None:
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    text = vorlage["prompt_template"]
    assert "to_spawn.py blocker {N}" in text
    assert "git log origin/" not in text


def test_keine_eigene_blocker_regel() -> None:
    """Wächter: kein Prompt, keine Doku, kein Code formuliert die Regel neben gh.py aus."""
    treffer = []
    for pfad in SKILL.rglob("*"):
        rel = pfad.relative_to(SKILL).as_posix()
        if not pfad.is_file() or rel.startswith((".git", *ERLAUBT_ORDNER)) or rel in ERLAUBT:
            continue
        try:
            text = pfad.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        treffer += [f"{rel}: {grund}" for grund, muster in REGEL_MUSTER.items() if muster.search(text)]
    assert treffer == [], f"Regel selbst ausformuliert statt `to_spawn.py blocker <N>` (#267): {treffer}"


# --- #269: Wächter-Muster fangen abgewandelte Eigenregeln --------------------------


@pytest.mark.parametrize(
    "zeile",
    [
        "gh api repos/o/r/issues/7/dependencies/blocked_by --jq '.[].number'",
        "gh api repos/o/r/issues/7/dependencies/blocked_by -q '.[].state'",
        "gh api repos/o/r/issues/{N}/dependencies/blocked_by --paginate --jq length",
        "git log origin/master --oneline --grep=(#<Blocker>)",
        'git log origin/master --oneline --grep="(#5)"',
        "git log origin/main --grep '(#{N})'",
        'git log --grep=\\"(#<Blocker>)\\"',
    ],
)
def test_regel_muster_fangen_abwandlungen(zeile: str) -> None:
    assert any(m.search(zeile) for m in REGEL_MUSTER.values()), zeile


@pytest.mark.parametrize(
    "zeile",
    [
        "Blocker offen — Ticket wartet",
        "gh.blocked_by(repo, ticket) liefert die Kanten",
        'gh api "repos/o/r/issues/7/dependencies/blocked_by"',
    ],
)
def test_regel_muster_lassen_fliesstext_und_reine_abfrage(zeile: str) -> None:
    assert not any(m.search(zeile) for m in REGEL_MUSTER.values()), zeile


def test_ausnahmen_haben_fundstellen() -> None:
    """Jede erlaubte Datei trägt wirklich ein Muster — tote Ausnahmen fallen auf (#269)."""
    tot = [
        rel
        for rel in sorted(ERLAUBT)
        if not any(m.search((SKILL / rel).read_text(encoding="utf-8")) for m in REGEL_MUSTER.values())
    ]
    assert tot == [], f"ERLAUBT-Eintrag ohne Fundstelle — streichen: {tot}"


# --- #269: Start-Schranke ohne --sofort, Repo nicht ermittelbar, Wortlaut je Zustand ---


def test_start_schranke_voll_wartet_ohne_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne ``--sofort`` hält die Start-Schranke auch einen erledigten Blocker ohne Commit."""
    repo = _repo(tmp_path, "chore: anderes (#9)")
    stub = _stub(tmp_path, [_dep(5, "open", "staging:fertig")])
    bau = _lade_bau(repo, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    attrappe(bin_dir, "gh", f"import runpy\nrunpy.run_path({str(stub)!r}, run_name='__main__')\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    class Wartet(Exception):
        pass

    def _schlafen(_s: float) -> None:
        raise Wartet

    monkeypatch.setattr(bau, "auf_blocker_warten", functools.partial(bau.auf_blocker_warten, schlafen=_schlafen))
    args = Namespace(sofort=False, umzug=None, resume=None, probesitz=False, takt=60)
    with pytest.raises(Wartet):
        bau.blocker_schranke("7", args)


def test_blocker_befehl_repo_nicht_ermittelbar_wartet(tmp_path: Path) -> None:
    """Kein ``--gh-repo`` und kein origin: nie „frei“, sondern „Abfrage fehlgeschlagen“."""
    repo = tmp_path / "ohne_origin"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True, capture_output=True)
    erg = _lauf([str(SKILL / "to_spawn.py"), "blocker", "7"], repo, _stub(tmp_path, []))
    assert (erg.returncode, erg.stdout.strip()) == (1, "WARTET: Blocker-Abfrage fehlgeschlagen"), erg.stderr


@pytest.mark.parametrize(
    ("dep", "wort"),
    [(_dep(5, "open", "staging:fertig"), "fertig gebaut"), (_dep(5, "closed"), "zu")],
)
def test_ohne_commit_nennt_den_echten_zustand(tmp_path: Path, dep: dict[str, Any], wort: str) -> None:
    """„zu“ nur für geschlossene Blocker; ``staging:fertig`` heißt „fertig gebaut“ (#269)."""
    repo = _repo(tmp_path, "chore: anderes (#9)")
    erg = _blocker(repo, _stub(tmp_path, [dep]))
    assert erg.stdout.strip().startswith(f"WARTET: #5 {wort}, aber kein Commit „(#5)“"), erg.stdout
