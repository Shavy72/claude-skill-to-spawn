"""#257 Fixrunde 1 — Rot-Beweis vor dem Bau (executor-opus baut danach).

Deckt die Tabelle „Fixrunde 1“ aus ``docs/PLAN_257_fremdrepo.md`` ab: F1 (nest_server.sh
Symlink für den transienten Dienst), F2 (``gh.label_sicherstellen`` idempotent, kein
``--force`` mehr), F3 (``auf_speicher_warten`` statt Exit 5 in bau.py/wache.py), F4
(``spawn_srv.sh`` meldet eine kaputte Speicherprüfung statt sie zu ignorieren), F5
(kein GitHub-Origin → Abbruch statt DuoPlus-Fallback), F6 (Sperrdatei um
``~/.claude.json``), F7 (``main`` vor ``master``, wenn beide da sind), F8
(``spec_stand.py`` repo-neutral), F9 (``nest.py`` nutzt ``gh.hauptzweig``), plus die
zwei fehlenden Tests aus Paket B (``gitignore_ergaenzen`` idempotent, B9-Leser mit
``hauptbaum``).

Ein Teil der Tests prüft neue Funktionen (``auf_speicher_warten``,
``repo_slug_oder_abbruch``), die es in dieser Fixrunde noch nicht gibt — die
``AttributeError`` IST der Rot-Beweis, kein Test-Fehler. Andere Tests (Paket-B-Reste)
sind schon grün, weil die Funktion längst da ist; das ist beabsichtigt (siehe
Rückgabe der Bau-Session).

Attrappen nur für ``gh`` (PATH-Stub, wie in ``test_fremdrepo_257.py``) — Git-Repos sind
echt (``tmp_path``).
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, config, gh

_ZAEHLER = itertools.count()


# --- Bausteine (Vorbild test_fremdrepo_257.py) --------------------------------


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ort), check=True, capture_output=True, text=True).stdout.strip()


def _identity(repo: Path) -> None:
    for schluessel, wert in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", schluessel, wert)


def _commit(repo: Path, datei: str = "a.txt", text: str = "x\n") -> None:
    (repo / datei).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "start")


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> object:
    """``skripte/bau.py`` frisch laden, mit ``TO_SPAWN_REPO`` = ``repo`` (Vorbild ``test_fremdrepo_257.py``)."""
    import importlib.util

    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    modulname = f"bau_257_fix1_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(modulname, None)
    return modul


def _gh_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """gh-Stub im PATH (kein ``TO_SPAWN_GH_STUB``, wie ``test_label_sicherstellen_mit_gh_stub``).

    Reagiert auf ``gh api …`` mit Exit 0 (Label vorhanden, ``LABEL_VORHANDEN`` gesetzt)
    oder Exit 1 (404, Variable fehlt); jeder Aufruf landet roh im Protokoll.
    """
    monkeypatch.delenv("TO_SPAWN_GH_STUB", raising=False)
    binaer = tmp_path / "bin"
    binaer.mkdir()
    protokoll = tmp_path / "gh-aufrufe.txt"
    stub = binaer / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{protokoll}"\n'
        'if [ "$1" = "api" ]; then\n'
        '  if [ -n "$LABEL_VORHANDEN" ]; then exit 0; else exit 1; fi\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ.get('PATH', '')}")
    return protokoll, stub


# --- F1: nest/nest_server.sh — transienter Dienst per Symlink ------------------


def test_nest_server_bash_syntax_ok() -> None:
    ergebnis = subprocess.run(
        ["bash", "-n", str(SKILL / "nest" / "nest_server.sh")], capture_output=True, text=True, check=False
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_nest_server_legt_symlink_unter_multi_user_target_an() -> None:
    text = (SKILL / "nest" / "nest_server.sh").read_text(encoding="utf-8")
    assert "multi-user.target.wants" in text, "F1 fehlt noch: Symlink für den transienten Dienst"


# --- F2: gh.label_sicherstellen — erst prüfen, dann anlegen (ohne --force) -----


def test_label_sicherstellen_vorhanden_kein_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protokoll, _stub = _gh_stub(tmp_path, monkeypatch)
    monkeypatch.setenv("LABEL_VORHANDEN", "1")
    repo = tmp_path / "label-repo-da"
    repo.mkdir()
    assert gh.label_sicherstellen(repo, "checkpoint:human") is True
    text = protokoll.read_text(encoding="utf-8")
    assert "label create" not in text, f"F2 fehlt noch: legt trotz vorhandenem Label an — {text}"


def test_label_sicherstellen_404_legt_an_ohne_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    protokoll, _stub = _gh_stub(tmp_path, monkeypatch)
    monkeypatch.delenv("LABEL_VORHANDEN", raising=False)
    repo = tmp_path / "label-repo-404"
    repo.mkdir()
    assert gh.label_sicherstellen(repo, "checkpoint:human") is True
    zeilen = [z for z in protokoll.read_text(encoding="utf-8").splitlines() if z.startswith("label create")]
    assert len(zeilen) == 1, zeilen
    assert "--force" not in zeilen[0], f"F2 fehlt noch: --force noch dabei — {zeilen[0]}"


# --- F3: auf_speicher_warten statt Exit 5 ---------------------------------------


def test_auf_speicher_warten_zwei_zyklen_dann_frei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "warte-repo"
    (repo / ".git").mkdir(parents=True)
    modul = _lade_bau(monkeypatch, repo)
    antworten = iter([(False, "voll"), (False, "voll"), (True, "frei")])
    schlaf_aufrufe: list[int] = []
    zyklen = modul.auf_speicher_warten(
        {}, pruefen=lambda konfig: next(antworten), schlafen=schlaf_aufrufe.append, takt_s=60
    )
    assert zyklen == 2, zyklen
    assert schlaf_aufrufe == [60, 60], schlaf_aufrufe


def test_wache_hat_auf_speicher_warten_statt_exit5() -> None:
    text = (SKRIPTE / "wache.py").read_text(encoding="utf-8")
    assert "auf_speicher_warten" in text, "F3 fehlt noch in wache.py"


# --- F5: kein GitHub-Origin → Abbruch statt DuoPlus-Fallback -------------------


def test_repo_slug_oder_abbruch_ohne_origin_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "ohne-origin"
    repo.mkdir()
    _git(repo, "init", "-q")
    modul = _lade_bau(monkeypatch, repo)
    with pytest.raises(SystemExit) as fehler:
        modul.repo_slug_oder_abbruch(repo)
    assert fehler.value.code == 2
    ausgabe = capsys.readouterr()
    assert "Kein GitHub-Repo erkannt" in ausgabe.err, ausgabe.err


# --- F6: Sperrdatei um ~/.claude.json -------------------------------------------


def test_vertrauen_sicherstellen_legt_sperrdatei_an_und_bleibt_konsistent(tmp_path: Path) -> None:
    # Sperrdatei-Logik kommt erst in dieser Fixrunde (#257).
    from to_spawn import vertrauen

    claude_json = tmp_path / ".claude.json"
    pfad = tmp_path / "arbeit"
    pfad.mkdir()
    vertrauen.sicherstellen(pfad, claude_json=claude_json)
    sperre = claude_json.parent / f"{claude_json.name}.to-spawn.lock"
    assert sperre.is_file(), "F6 fehlt noch: keine Sperrdatei"

    vorher = claude_json.read_bytes()
    vertrauen.sicherstellen(pfad, claude_json=claude_json)
    assert claude_json.read_bytes() == vorher, "zweiter Aufruf ändert die Datei"

    daten = json.loads(claude_json.read_text(encoding="utf-8"))
    assert isinstance(daten, dict)
    assert daten["projects"][str(pfad)]["hasTrustDialogAccepted"] is True


# --- F7: gh.hauptzweig — main vor master, wenn beide vorhanden ------------------


def test_hauptzweig_main_und_master_ohne_head_main_gewinnt(tmp_path: Path) -> None:
    bare = tmp_path / "bare-beide.git"
    _git(tmp_path, "init", "--bare", "-q", str(bare))
    work = tmp_path / "work-beide"
    _git(tmp_path, "init", "-q", "-b", "main", str(work))
    _identity(work)
    _commit(work)
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "main")
    _git(work, "checkout", "-q", "-b", "master")
    _commit(work, "b.txt")
    _git(work, "push", "-q", "-u", "origin", "master")
    assert gh.hauptzweig(work) == "main", "F7 fehlt noch: master gewinnt noch vor main"


# --- F8: spec_stand.py repo-neutral ---------------------------------------------


def test_spec_stand_repo_neutral() -> None:
    text = (SKRIPTE / "spec_stand.py").read_text(encoding="utf-8")
    for verboten in ("Shavy72/duoplus-management", "clawy-vps", "origin/master"):
        assert verboten not in text, f"F8 fehlt noch: {verboten!r} noch hart verdrahtet"


def test_spec_stand_kompiliert() -> None:
    ergebnis = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SKRIPTE / "spec_stand.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


# --- F9: nest.py nutzt gh.hauptzweig statt eigener Suche ------------------------


def test_nest_verwendet_gh_hauptzweig() -> None:
    text = (SKILL / "to_spawn" / "nest.py").read_text(encoding="utf-8")
    assert "hauptzweig(" in text, "F9 fehlt noch: nest.py sucht main/master noch selbst"


# --- F4: spawn_srv.sh meldet eine kaputte Speicherprüfung statt sie zu ignorieren


def test_spawn_srv_kaputte_speicherpruefung_meldet_text_und_exit6() -> None:
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    assert "Speicherprüfung kaputt" in text, "F4 fehlt noch: Meldung bei kaputter Speicherprüfung"
    assert "exit 6" in text, "F4 fehlt noch: Exit 6 bei kaputter Speicherprüfung"


# --- F10 Rest Paket B: gitignore_ergaenzen idempotent ---------------------------


def test_gitignore_ergaenzen_idempotent(tmp_path: Path) -> None:
    erster = config.gitignore_ergaenzen(tmp_path)
    zweiter = config.gitignore_ergaenzen(tmp_path)
    assert erster is True
    assert zweiter is False
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".to-spawn/*" in text
    assert "!.to-spawn/config.json" in text


def test_gitignore_ergaenzen_ohne_abschliessenden_zeilenumbruch(tmp_path: Path) -> None:
    datei = tmp_path / ".gitignore"
    datei.write_text("bestehend.txt", encoding="utf-8")  # bewusst kein \n am Ende
    ergebnis = config.gitignore_ergaenzen(tmp_path)
    assert ergebnis is True
    text = datei.read_text(encoding="utf-8")
    assert "bestehend.txt\n" in text, "Trenner vor dem Block fehlt"
    assert ".to-spawn/*" in text


# --- F10 Rest Paket B: B9-Leser mit hauptbaum -----------------------------------


def _laufzeile(ticket: str, note: str) -> str:
    return json.dumps(
        {"ts": "2026-09-21T10:00:00+00:00", "typ": "session_start", "ticket": ticket, "note": note},
        ensure_ascii=False,
    )


def test_bau_log_lese_und_eintrag_schreiben_mit_hauptbaum(tmp_path: Path) -> None:
    hauptbaum = tmp_path / "hauptbaum"
    worktree = tmp_path / "worktree"
    hauptbaum.mkdir()
    worktree.mkdir()
    hp = bau_log.lauf_pfad(hauptbaum, "99")
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(_laufzeile("99", "haupt") + "\n", encoding="utf-8")
    wp = bau_log.lauf_pfad(worktree, "99")
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(_laufzeile("99", "wt") + "\n", encoding="utf-8")

    gelesen = bau_log.lese(worktree, "99", hauptbaum=hauptbaum)
    assert len(gelesen) == 2, gelesen

    bau_log.eintrag_schreiben(worktree, "99", "zusammenfassung", hauptbaum=hauptbaum, umfang="x")
    fest = bau_log.log_pfad(worktree, "99").read_text(encoding="utf-8").splitlines()
    assert len(fest) == 3, fest  # 2 aus den Laufdateien übertragen + die neue Zeile
