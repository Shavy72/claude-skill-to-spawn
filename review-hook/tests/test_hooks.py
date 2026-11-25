"""Review-Hooks mit echten temporären Git-Repos und echtem Hook-Aufruf per subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parent.parent
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
from _hookbasis import REVIEW

BASIS_CONFIG = json.loads((REVIEW / "config.standard.json").read_text(encoding="utf-8"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


class Umgebung:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.config = tmp / "config.json"
        self.sitzungen = tmp / "sitzungen"
        self.fehler_log = tmp / "hook-fehler.log"
        fremdtemp = tmp / "fremdtemp"
        fremdtemp.mkdir()
        self.env = {
            **os.environ,
            "REVIEW_CONFIG": str(self.config),
            "REVIEW_LOG": str(tmp / "ausloeser.jsonl"),
            "REVIEW_SITZUNGEN": str(self.sitzungen),
            "REVIEW_HOOK_LOG": str(self.fehler_log),
            "REVIEW_MENSCH_NOETIG": str(tmp / "mensch_noetig.jsonl"),
            "GIT_CEILING_DIRECTORIES": str(tmp),
            "TEMP": str(fremdtemp),
            "TMP": str(fremdtemp),
            "TMPDIR": str(fremdtemp),
        }
        self.env.pop("REVIEW_AUS", None)
        self.modus("hinweis")

    def modus(self, modus: str, **extra: Any) -> None:
        self.config.write_text(json.dumps({**BASIS_CONFIG, "modus": modus, **extra}), encoding="utf-8")

    def hook(self, skript: str, eingabe: dict[str, Any] | str, **env: str) -> tuple[int, dict[str, Any] | None]:
        roh = eingabe if isinstance(eingabe, str) else json.dumps(eingabe)
        ergebnis = subprocess.run(
            [sys.executable, str(HOOKS / skript)],
            input=roh.encode("utf-8"),
            capture_output=True,
            env={**self.env, **env},
            timeout=60,
        )
        text = ergebnis.stdout.decode("utf-8").strip()
        return ergebnis.returncode, (json.loads(text) if text else None)

    def merke(self, repo: Path, datei: str, sid: str = "s1", agent_id: str | None = None) -> None:
        eingabe: dict[str, Any] = {
            "session_id": sid,
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / datei)},
        }
        if agent_id is not None:
            eingabe["agent_id"] = agent_id
        code, aus = self.hook("merker.py", eingabe)
        assert (code, aus) == (0, None)

    def stop(
        self,
        repo: Path,
        sid: str = "s1",
        aktiv: bool = False,
        transcript: Path | str = "",
    ) -> dict | None:
        code, aus = self.hook(
            "stop.py",
            {
                "session_id": sid,
                "cwd": str(repo),
                "stop_hook_active": aktiv,
                "transcript_path": str(transcript),
            },
        )
        assert code == 0
        return aus

    def commit(self, repo: Path, befehl: str) -> dict | None:
        code, aus = self.hook(
            "commit.py",
            {
                "session_id": "s1",
                "cwd": str(repo),
                "tool_name": "Bash",
                "tool_input": {"command": befehl},
            },
        )
        assert code == 0
        return aus

    def route(self, repo: Path, *args: str) -> dict[str, Any]:
        ergebnis = subprocess.run(
            [
                sys.executable,
                str(REVIEW / "review_route.py"),
                "--repo",
                str(repo),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=True,
        )
        return json.loads(ergebnis.stdout)

    def beleg(self, repo: Path, r: dict[str, Any], befunde: list[dict]) -> None:
        pfad = repo / ".review" / f"beleg-{r['sha'][:7]}-{r['moment']}.json"
        pfad.parent.mkdir(exist_ok=True)
        pfad.write_text(
            json.dumps(
                {
                    "version": 1,
                    "moment": r["moment"],
                    "stufe": r["stufe"],
                    "modus": r["modus"],
                    "sha": r["sha"],
                    "fingerprint": r["fingerprint"],
                    "dateien": r["dateien"],
                    "befunde": befunde,
                    "status": "rot" if befunde else "gruen",
                    "fix_runde": 0,
                }
            ),
            encoding="utf-8",
        )


ECHT_MENSCH_NOETIG = Path.home() / ".claude" / ".review" / "mensch_noetig.jsonl"


def _zeilen_dieses_tests(tmp_path: Path) -> list[str]:
    """Zeilen der echten mensch_noetig.jsonl, deren repo im tmp_path dieses Tests liegt.
    Nur die zählen: parallele Bau-Sessions schreiben dort laufend echte Zeilen (Rest 3)."""
    try:
        text = ECHT_MENSCH_NOETIG.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []
    marken = {tmp_path.as_posix().casefold(), tmp_path.resolve().as_posix().casefold()}
    return [z for z in text.splitlines() if any(m in z.replace("\\\\", "/").casefold() for m in marken)]


@pytest.fixture
def u(tmp_path: Path) -> Iterator[Umgebung]:
    """Schutznetz (Befund 1): kein Hook-Test über u darf in die echte mensch_noetig.jsonl schreiben."""
    yield Umgebung(tmp_path)
    eigene = _zeilen_dieses_tests(tmp_path)
    assert not eigene, f"Test hat in {ECHT_MENSCH_NOETIG} geschrieben: {eigene[:2]}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    pfad = tmp_path / "repo"
    pfad.mkdir()
    git(pfad, "init", "-q")
    git(pfad, "config", "user.name", "Test")
    git(pfad, "config", "user.email", "test@example.invalid")
    git(pfad, "config", "core.autocrlf", "false")
    (pfad / "README.md").write_text("# Test\n", encoding="utf-8")
    (pfad / "rechnen.py").write_text("def eins():\n    return 1\n", encoding="utf-8")
    git(pfad, "add", "-A")
    git(pfad, "commit", "-q", "-m", "start")
    return pfad


def py_aendern(repo: Path) -> None:
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")


ROT = [
    {
        "datei": "rechnen.py",
        "zeile": 5,
        "schwere": "hoch",
        "konfidenz": 95,
        "pruefer": "python-reviewer",
        "text": "falsch",
        "status": "behalten",
    }
]


def test_merker_schreibt_und_dedupliziert(u: Umgebung, repo: Path) -> None:
    u.merke(repo, "rechnen.py")
    u.merke(repo, "rechnen.py")
    u.merke(repo, ".review/x.json")
    daten = json.loads((u.sitzungen / "s1.json").read_text(encoding="utf-8"))
    assert daten["dateien"] == [(repo / "rechnen.py").resolve().as_posix()]


def test_stop_ohne_aenderungen_still(u: Umgebung, repo: Path) -> None:
    u.merke(repo, "rechnen.py")
    assert u.stop(repo) is None


def test_stop_py_block_dann_still_bei_gleichem_fingerprint(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"
    assert "Review fällig (sitzung, Stufe klein)" in aus["reason"]
    assert "--files rechnen.py" in aus["reason"] and "moment=sitzung" in aus["reason"]
    # Runde 2 (Befund B): kein zweiter Block, aber einmal sichtbarer Hinweis, danach still.
    zweit = u.stop(repo)
    assert zweit is not None and "decision" not in zweit
    assert "Mensch nötig" in zweit["systemMessage"]
    assert u.stop(repo) is None


def test_stop_gueltiger_beleg_still(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py", sid="s2")
    u.beleg(repo, u.route(repo, "--moment", "sitzung", "--files", "rechnen.py"), [])
    assert u.stop(repo, sid="s2") is None


def test_stop_scharf_rot_block_bis_max_fix_runden(u: Umgebung, repo: Path) -> None:
    u.modus("scharf", max_fix_runden=2)
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    u.beleg(repo, u.route(repo, "--moment", "sitzung", "--files", "rechnen.py"), ROT)
    for _ in range(2):
        aus = u.stop(repo, aktiv=True)
        assert aus is not None and aus["decision"] == "block"
        assert "Beleg rot" in aus["reason"]
    aus = u.stop(repo, aktiv=True)
    assert aus is not None and "decision" not in aus
    assert "Review: Mensch nötig" in aus["systemMessage"]


def test_stop_fremde_aenderung_ignoriert(u: Umgebung, repo: Path) -> None:
    (repo / "fremd.py").write_text("x = 1\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    assert u.stop(repo) is None


def test_commit_nur_doku_still(u: Umgebung, repo: Path) -> None:
    (repo / "README.md").write_text("# Neu\n", encoding="utf-8")
    git(repo, "add", "-A")
    assert u.commit(repo, 'git commit -m "doku"') is None


def test_commit_py_hinweis_kontext_ohne_permission(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    git(repo, "add", "-A")
    aus = u.commit(repo.parent, f'cd x && git -C "{repo}" commit -m "code"')
    spezifisch = aus["hookSpecificOutput"]
    assert spezifisch["hookEventName"] == "PreToolUse"
    assert "Review fällig (commit, Stufe klein)" in spezifisch["additionalContext"]
    assert "permissionDecision" not in spezifisch


def test_commit_scharf_ohne_beleg_deny(u: Umgebung, repo: Path) -> None:
    u.modus("scharf")
    py_aendern(repo)
    git(repo, "add", "-A")
    spezifisch = u.commit(repo, "git commit -m code")["hookSpecificOutput"]
    assert spezifisch["permissionDecision"] == "deny"
    assert "Beleg fehlt" in spezifisch["permissionDecisionReason"]


def test_commit_tree_still(u: Umgebung, repo: Path) -> None:
    u.modus("scharf")
    py_aendern(repo)
    git(repo, "add", "-A")
    assert u.commit(repo, "git commit-tree HEAD^{tree} -m x") is None


def test_review_aus_still(u: Umgebung, repo: Path) -> None:
    py_aendern(repo)
    u.merke(repo, "rechnen.py")
    code, aus = u.hook("stop.py", {"session_id": "s1", "cwd": str(repo)}, REVIEW_AUS="1")
    assert (code, aus) == (0, None)


def test_kaputtes_stdin_exit0_mit_logzeile(u: Umgebung) -> None:
    code, aus = u.hook("stop.py", "{kaputt")
    assert (code, aus) == (0, None)
    assert "JSONDecodeError" in u.fehler_log.read_text(encoding="utf-8")
