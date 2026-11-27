"""Weg-Tests für #865 (aus App #1396) — PreToolUse-Bash-Hook ``hook-push-pruefer``.

Prüft echt (echtes Git-Repo, echte Bash über ``tests/hilfen/git_bash.py``, echter CLI-Aufruf):
- Hook wird ausschließlich über ``bau.staffel_hooks()`` in die Session-Settings eingetragen
  (kein globales ``core.hooksPath``).
- Läuft ausschließlich bei ``git push`` auf ``ticket/*``-Zweige (explizite Refspec,
  ``HEAD:ticket/*``, ``refs/heads/ticket/*``, impliziter Push auf ausgechecktem ``ticket/*``-Zweig,
  ``git -C`` / ``cd &&`` / ``bash -c``).
- Andere Befehle und Pushes auf ``main``/``master``/``wip/*``/``--delete``/``:ticket/*`` gehen
  ungeprüft mit Exit 0 durch.
- Delegiert ausschließlich an :func:`to_spawn.schliess_pruefer.pruefe` (#864 / #1389) ohne eigene
  Regel-Kopie und bricht bei Ablehnung mit Exit 2 und den konkreten Gründen auf stderr ab.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import types
import zlib
from pathlib import Path
from typing import Any

import pytest
from hilfen.git_bash import git_bash

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

BASH = git_bash()
TICKET = 1396


# --- Hilfen (echtes Git, echte PNG-Bilder, echter bau.py-Import) ----------------


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_push_pruefer_1396", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_push_pruefer_1396"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_push_pruefer_1396", None)
    return modul


def _git(repo: Path, *args: str) -> str:
    umgebung = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test-Bauer",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test-Bauer",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=umgebung,
        timeout=60,
    )
    return fertig.stdout.strip()


def _png_bytes(breite: int, hoehe: int) -> bytes:
    """Kleinstes gültiges PNG mit echter IHDR-Breite und -Höhe."""

    def _chunk(typ: bytes, daten: bytes) -> bytes:
        crc = zlib.crc32(typ + daten) & 0xFFFFFFFF
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    roh = b"".join(b"\x00" + b"\x00\x00\x00" * breite for _ in range(hoehe))
    idat = zlib.compress(roh)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _repo_basis(tmp_path: Path, zweig: str = f"ticket/{TICKET}-push-hook") -> Path:
    """Echtes Git-Repo mit Basis-Commit auf ``master`` und ausgechecktem Ticket-Zweig."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "README.md").write_text("# Test-Repo\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_basis.py").write_text("def test_basis():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: Anfang")
    _git(repo, "checkout", "-q", "-b", zweig)
    return repo


def _commit(repo: Path, betreff: str, dateien: dict[str, str | bytes | None]) -> str:
    for rel, inhalt in dateien.items():
        ziel = repo / rel
        if inhalt is None:
            _git(repo, "rm", "-q", rel)
            continue
        ziel.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(inhalt, bytes):
            ziel.write_bytes(inhalt)
        else:
            ziel.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "--allow-empty", "-m", betreff)
    return _git(repo, "rev-parse", "HEAD")


def _gueltige_beleg_dateien(ticket: int = TICKET, commit_sha: str = "abcdef1") -> dict[str, str | bytes]:
    beleg = (
        f"# Beleg #{ticket}\n\n"
        f"- Ticket: #{ticket}\n"
        f"- Commit-Stand: `{commit_sha}`\n"
        "- Staging-Adresse: https://staging.example.test/app\n"
        "- Rolle: Nutzer\n"
        "- Datum: 2026-11-27\n\n"
        "## Staging-Klickweg\n\n"
        "1. Seite geöffnet und Ablauf geprüft.\n"
        f"![Desktop]({ticket}_desktop.png)\n"
        f"![Handy]({ticket}_handy.png)\n"
    )
    return {
        f"docs/verify-hard/{ticket}_beleg.md": beleg,
        f"docs/verify-hard/{ticket}_desktop.png": _png_bytes(1280, 720),
        f"docs/verify-hard/{ticket}_handy.png": _png_bytes(390, 844),
    }


def _hook_befehl_aus_staffel(bau: types.ModuleType) -> str:
    pre = bau.staffel_hooks()["PreToolUse"]
    befehle = [h["command"] for b in pre if b["matcher"] == "Bash" for h in b["hooks"]]
    treffer = [b for b in befehle if "hook-push-pruefer" in b]
    assert len(treffer) == 1, f"hook-push-pruefer fehlt in staffel_hooks()['PreToolUse'][Bash]: {befehle}"
    return treffer[0]


def _fuehre_hook_in_bash_aus(
    hook_befehl: str,
    bash_befehl: str,
    cwd: Path,
    *,
    tool_name: str = "Bash",
    roh_eingabe: str | None = None,
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    if roh_eingabe is None:
        eingabe = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": {"command": bash_befehl},
                "cwd": str(cwd),
            }
        )
    else:
        eingabe = roh_eingabe
    return subprocess.run(
        [BASH, "-c", hook_befehl],
        input=eingabe,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


# --- 1. Eintragung ausschließlich über staffel_hooks() -------------------------


def test_staffel_hooks_registriert_push_pruefer_fuer_bash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    hook_befehl = _hook_befehl_aus_staffel(bau)
    assert hook_befehl.endswith("hook-push-pruefer")
    settings = bau.session_settings({})
    assert settings["hooks"] == bau.staffel_hooks()
    # Kein globales core.hooksPath in bau.py oder hooks.py
    assert "core.hooksPath" not in (SKRIPTE / "bau.py").read_text(encoding="utf-8")
    assert "core.hooksPath" not in (SKILL / "to_spawn" / "hooks.py").read_text(encoding="utf-8")


# --- 2. Ablehnung und Durchlass echt über Bash (Linux + Windows Git Bash) ------


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_push_auf_ticket_zweig_abgelehnt_bei_maengeln_mit_gruenden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_basis(tmp_path)
    # Commit ohne Ticketnummer und ohne Belegseite
    _commit(repo, "feat: Aenderung ohne Nummer", {"app.py": "x = 1\n"})
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    erg = _fuehre_hook_in_bash_aus(hook, f"git push origin ticket/{TICKET}-push-hook", repo)
    assert erg.returncode == 2, (erg.stdout, erg.stderr)
    assert "commit_ohne_nummer" in erg.stderr
    assert "beweis_fehlt" in erg.stderr


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_push_auf_ticket_zweig_abgelehnt_bei_kaputter_beleg_form_und_testverlust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_basis(tmp_path)
    # Commit MIT Ticketnummer, aber entfernt test_basis und hat unvollständige Belegseite
    _commit(
        repo,
        f"feat: Bau (#{TICKET})",
        {
            "app.py": "x = 2\n",
            "tests/test_basis.py": "def test_neu():\n    assert True\n",
            f"docs/verify-hard/{TICKET}_beleg.md": f"# Beleg #{TICKET}\n\n- Ticket: #{TICKET}\n- Datum: folgt\n",
        },
    )
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    erg = _fuehre_hook_in_bash_aus(hook, f"git push -u origin ticket/{TICKET}-push-hook", repo)
    assert erg.returncode == 2, (erg.stdout, erg.stderr)
    assert "beleg_form" in erg.stderr
    assert "test_ersetzt" in erg.stderr
    assert "test_basis" in erg.stderr


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_push_auf_ticket_zweig_erlaubt_wenn_pruefer_gruen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_basis(tmp_path)
    sha = _commit(repo, f"feat: Umsetzung (#{TICKET})", {"app.py": "x = 1\n"})
    _commit(repo, f"docs: Beleg (#{TICKET})", _gueltige_beleg_dateien(TICKET, sha))
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    erg = _fuehre_hook_in_bash_aus(hook, f"git push origin ticket/{TICKET}-push-hook", repo)
    assert erg.returncode == 0, (erg.stdout, erg.stderr)


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_push_auf_ticket_zweig_erlaubt_fuer_reines_doku_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_basis(tmp_path)
    _commit(repo, f"docs: Anleitung aktualisiert (#{TICKET})", {"docs/anleitung.md": "# Neu\n"})
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    erg = _fuehre_hook_in_bash_aus(hook, f"git push origin ticket/{TICKET}-push-hook", repo)
    assert erg.returncode == 0, (erg.stdout, erg.stderr)


# --- 3. Geltungsbereich: ausschließlich git push auf ticket/* -------------------


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_andere_befehle_und_nicht_ticket_zweige_bleiben_ungeprueft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Repo mit Mängeln auf wip/*-Zweig: nicht-ticket-Pushes müssen alle mit Exit 0 durch
    repo = _repo_basis(tmp_path, zweig=f"wip/{TICKET}-entwurf")
    _commit(repo, "wip: zwischenstand ohne Nummer", {"app.py": "x = 1\n"})
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    for erlaubt in (
        "git status",
        "git push",
        "git push origin",
        f"git push origin wip/{TICKET}-entwurf",
        "git push origin master",
        "git push origin main",
        f"git push origin fix/{TICKET}-klein",
        f"git push origin --delete ticket/{TICKET}-push-hook",
        f"git push origin -d ticket/{TICKET}-push-hook",
        f"git push origin :ticket/{TICKET}-push-hook",
        f'echo "git push origin ticket/{TICKET}-push-hook"',
    ):
        erg = _fuehre_hook_in_bash_aus(hook, erlaubt, repo)
        assert erg.returncode == 0, (erlaubt, erg.stdout, erg.stderr)


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_impliziter_push_und_refspec_varianten_auf_ticket_zweig_werden_geprueft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_basis(tmp_path)
    _commit(repo, "feat: ohne Nummer", {"app.py": "x = 1\n"})
    bau = _lade_bau(monkeypatch, repo)
    hook = _hook_befehl_aus_staffel(bau)

    for blockiert in (
        "git push",
        "git push origin",
        "git push origin HEAD",
        f"git push origin HEAD:ticket/{TICKET}-push-hook",
        f"git push origin HEAD:refs/heads/ticket/{TICKET}-push-hook",
        f'bash -c "git push origin ticket/{TICKET}-push-hook"',
    ):
        erg = _fuehre_hook_in_bash_aus(hook, blockiert, repo)
        assert erg.returncode == 2, (blockiert, erg.stdout, erg.stderr)
        assert "commit_ohne_nummer" in erg.stderr

    # Auch git -C <repo> und cd <repo> && git push von außerhalb des Repos
    for extern in (
        f"git -C {repo.as_posix()} push origin ticket/{TICKET}-push-hook",
        f"cd {repo.as_posix()} && git push origin ticket/{TICKET}-push-hook",
    ):
        erg = _fuehre_hook_in_bash_aus(hook, extern, tmp_path)
        assert erg.returncode == 2, (extern, erg.stdout, erg.stderr)
        assert "commit_ohne_nummer" in erg.stderr


# --- 4. Delegation an schliess_pruefer.pruefe & Fehlertoleranz ------------------


def test_hook_delegiert_an_schliess_pruefer_pruefe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import hooks, schliess_pruefer

    repo = _repo_basis(tmp_path)
    aufrufe: list[Any] = []

    def spion(stand: Any) -> schliess_pruefer.PruefErgebnis:
        aufrufe.append(stand)
        return schliess_pruefer.PruefErgebnis(
            ok=False,
            gruende=[schliess_pruefer.Grund(regel="beweis_fehlt", text="Spion-Grund für #1396")],
        )

    monkeypatch.setattr(schliess_pruefer, "pruefe", spion)
    eingabe = io.StringIO(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": f"git push origin ticket/{TICKET}-push-hook"},
                "cwd": str(repo),
            }
        )
    )
    fehler = io.StringIO()
    code = hooks.hook_push_pruefer(strom=eingabe, fehler=fehler)
    assert code == 2
    assert len(aufrufe) == 1
    stand = aufrufe[0]
    assert stand.ticket == TICKET
    assert stand.gruen_pruefen is False
    assert "beweis_fehlt" in fehler.getvalue()
    assert "Spion-Grund für #1396" in fehler.getvalue()


@pytest.mark.skipif(BASH is None, reason="bash / Git Bash fehlt")
def test_hook_fehlertolerant_bei_kaputtem_json_oder_fremdem_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    hook = _hook_befehl_aus_staffel(bau)

    assert _fuehre_hook_in_bash_aus(hook, "", tmp_path, roh_eingabe="{kein json").returncode == 0
    assert _fuehre_hook_in_bash_aus(hook, "", tmp_path, roh_eingabe=json.dumps({"tool_name": "Bash"})).returncode == 0
    assert (
        _fuehre_hook_in_bash_aus(
            hook, f"git push origin ticket/{TICKET}-push-hook", tmp_path, tool_name="Read"
        ).returncode
        == 0
    )
