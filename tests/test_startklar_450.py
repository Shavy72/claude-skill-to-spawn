"""Startklar-Prüfung vor Spec-Start (#450, E32/E33/E37/E39).

Echt laufen: Git (Hauptrepo + Worktree), venv-Anlage, Shell-Funktion ``_bau_py``,
Hook-Skripte als Prozesse, ``to_spawn.py``/``wache.py`` als Prozesse. Gestellt sind
nur die Werkzeugliste (eigene Mini-Werkzeuge statt ``aufraeumen.mjs``) und die
Settings-Dateien mit Fake-Hooks.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import startklar

SPEC = 450


def _git(ordner: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ordner), check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo_mit_worktree(tmp_path: Path, venv_im_haupt: bool = True) -> tuple[Path, Path]:
    """Echtes Hauptrepo + Worktree; Hauptrepo-.venv = Symlink auf den Test-Interpreter."""
    haupt = tmp_path / "haupt"
    haupt.mkdir()
    _git(haupt, "init", "-q", "-b", "master")
    _git(haupt, "config", "user.email", "t@t")
    _git(haupt, "config", "user.name", "t")
    (haupt / ".gitignore").write_text(".venv\n.env\n", encoding="utf-8")
    (haupt / "scripts").mkdir()
    (haupt / "scripts" / "x.py").write_text("print('x läuft')\n", encoding="utf-8")
    _git(haupt, "add", ".")
    _git(haupt, "commit", "-q", "-m", "start")
    if venv_im_haupt:
        os.symlink(sys.prefix, haupt / ".venv", target_is_directory=True)
    wt = tmp_path / "wt-1"
    _git(haupt, "worktree", "add", "-q", str(wt), "-b", "ticket/1")
    return haupt, wt


def _py(ordner: Path) -> Path:
    return (
        ordner / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )


# --- venv ----------------------------------------------------------------------


def test_venv_worktree_bekommt_symlink_aufs_hauptrepo(tmp_path: Path) -> None:
    haupt, wt = _repo_mit_worktree(tmp_path)
    assert not (wt / ".venv").exists()
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    assert befund.bereich == "venv"
    assert (wt / ".venv").is_symlink()
    assert (wt / ".venv").resolve() == (haupt / ".venv").resolve()
    assert subprocess.run([str(_py(wt)), "-c", "pass"], check=False).returncode == 0


def test_venv_kaputter_symlink_wird_ersetzt(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    os.symlink(tmp_path / "gibts_nicht", wt / ".venv", target_is_directory=True)
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    assert subprocess.run([str(_py(wt)), "-c", "pass"], check=False).returncode == 0


def test_venv_vorhanden_bleibt_unberuehrt(tmp_path: Path) -> None:
    haupt, _wt = _repo_mit_worktree(tmp_path)
    befund = startklar.venv_sicherstellen(haupt)
    assert befund.ok, befund
    assert (haupt / ".venv").resolve() == Path(sys.prefix).resolve()


def test_venv_ohne_hauptrepo_venv_wird_neu_angelegt(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path, venv_im_haupt=False)
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    assert (wt / ".venv").is_dir() and not (wt / ".venv").is_symlink()
    assert subprocess.run([str(_py(wt)), "-c", "pass"], check=False).returncode == 0


def test_venv_nicht_ignoriert_gibt_warnung(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".gitignore").write_text(".env\n", encoding="utf-8")
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    assert ".gitignore" in befund.text


# Quelle: ~/.bashrc auf dem Bau-Server, Z. 7-14 (Stand 2026-10-04), unverändert kopiert.
_BAU_PY = r"""
_bau_py() {  # gemeinsamer Aufruf fuer bau/wache/sessions
  local skript="$1"; shift
  if [ ! -f "$REPO/scripts/$skript" ]; then
    echo "scripts/$skript nicht gefunden (Repo unter $REPO?)" >&2; return 1
  fi
  ( cd "$REPO" && ./.venv/bin/python "scripts/$skript" "$@" )
}
"""


@pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash"), reason="bash-Funktion nur unter Linux"
)
def test_weg_bau_funktion_exit_127_dann_0(tmp_path: Path) -> None:
    """Vorfall V2: ``bau <N>`` im Aufseher-Worktree ohne .venv → Exit 127."""
    _haupt, wt = _repo_mit_worktree(tmp_path)

    def lauf() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", _BAU_PY + "\n_bau_py x.py"],
            env={**os.environ, "REPO": str(wt)},
            capture_output=True,
            text=True,
            check=False,
        )

    vorher = lauf()
    assert vorher.returncode == 127, vorher
    assert startklar.venv_sicherstellen(wt).ok
    nachher = lauf()
    assert nachher.returncode == 0, nachher
    assert "x läuft" in nachher.stdout


# --- Schlüssel -----------------------------------------------------------------


def _manifest(ordner: Path, tickets: dict[str, dict[str, object]]) -> None:
    pfad = ordner / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8")


def _schluessel_welt(tmp_path: Path) -> Path:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".env.example").write_text(
        "# Beispiel\nELEVENLABS_API_KEY=\nexport OPENAI_KEY=\nANDERER_KEY=xyz\n",
        encoding="utf-8",
    )
    _manifest(
        wt,
        {
            "451": {
                "title": "Bilder per Fal",
                "schluessel": ["FAL_KEY"],
                "umfang": "nur Bild",
            },
            "452": {
                "title": "Ton",
                "umfang": "Sprache über ELEVENLABS_API_KEY holen",
                "files": [],
            },
            "453": {"title": "OPENAI_KEY_ALT ist kein Treffer", "umfang": ""},
        },
    )
    return wt


def test_schluessel_fehlen_beide_namen_genannt(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    rot = [b for b in befunde if not b.ok]
    text = " ".join(b.text + b.behebung for b in rot)
    assert "FAL_KEY" in text and "ELEVENLABS_API_KEY" in text, befunde
    assert "OPENAI_KEY" not in text and "ANDERER_KEY" not in text
    assert "nest secrets" in text and "Bitwarden" in text


def test_schluessel_aus_env_und_umgebung_ok_ohne_wert_in_ausgabe(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    wt = _schluessel_welt(tmp_path)
    (wt / ".env").write_text('# x\nexport FAL_KEY="geheim123"\n', encoding="utf-8")
    caplog.set_level(logging.DEBUG)
    befunde = startklar.schluessel_pruefen(
        wt, SPEC, environ={"ELEVENLABS_API_KEY": "geheim456"}
    )
    assert all(b.ok for b in befunde), befunde
    _code, ausgabe = startklar.ausgabe(befunde)
    alles = ausgabe + caplog.text + repr(befunde)
    assert "geheim123" not in alles and "geheim456" not in alles


def test_schluessel_aus_hauptrepo_env_zaehlt(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    haupt = tmp_path / "haupt"
    (haupt / ".env").write_text("FAL_KEY=a\nELEVENLABS_API_KEY=b\n", encoding="utf-8")
    assert all(b.ok for b in startklar.schluessel_pruefen(wt, SPEC, environ={}))


def test_schluessel_leerer_wert_zaehlt_als_fehlend(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    (wt / ".env").write_text("FAL_KEY=\nELEVENLABS_API_KEY=''\n", encoding="utf-8")
    assert not all(b.ok for b in startklar.schluessel_pruefen(wt, SPEC, environ={}))


def test_schluessel_ohne_manifest_nur_konfig(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    assert all(b.ok for b in befunde)
    assert any("kein Manifest" in b.text for b in befunde)
    (wt / ".to-spawn").mkdir()
    (wt / ".to-spawn" / "config.json").write_text(
        json.dumps({"startklar": {"schluessel": ["WERKSTATT_TOKEN"]}}), encoding="utf-8"
    )
    rot = [b for b in startklar.schluessel_pruefen(wt, SPEC, environ={}) if not b.ok]
    assert rot and "WERKSTATT_TOKEN" in rot[0].text


# --- Werkzeug-Probe ------------------------------------------------------------

_HOOK_EXIT2 = """import json, sys
d = json.load(sys.stdin)
if "aufraeumen" in d["tool_input"]["command"]:
    print("Git-Sperre: node-Skript schreibt auf geschützte Datei", file=sys.stderr)
    sys.exit(2)
"""
_HOOK_DENY = """import json, sys
d = json.load(sys.stdin)
if "aufraeumen" in d["tool_input"]["command"]:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": "verboten per JSON"}}))
"""
_HOOK_HARMLOS = "import sys; sys.stdin.read()\n"
_HOOK_KAPUTT = (
    "import sys; sys.stdin.read(); print('kaputt', file=sys.stderr); sys.exit(1)\n"
)


def _werkzeuge(tmp_path: Path) -> tuple[startklar.Werkzeug, ...]:
    """Mini-Werkzeuge mit echtem Python statt node, gleiche Namen wie die echten."""
    skript = tmp_path / "aufraeumen.py"
    skript.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    py = Path(sys.executable).as_posix()
    return (
        startklar.Werkzeug(
            "aufraeumen",
            f"{py} {skript.as_posix()} --spec {{spec}} --dry-run",
            skript.as_posix(),
        ),
        startklar.Werkzeug("neustart", f"{py} -c pass", py),
    )


def _settings(tmp_path: Path, hook_code: str, matcher: str = "Bash") -> Path:
    hook = tmp_path / f"hook_{abs(hash(hook_code))}.py"
    hook.write_text(hook_code, encoding="utf-8")
    datei = tmp_path / f"settings_{matcher}_{abs(hash(hook_code))}.json"
    befehl = f"{Path(sys.executable).as_posix()} {hook.as_posix()}"
    datei.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": matcher,
                            "hooks": [{"type": "command", "command": befehl}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return datei


def _probe(tmp_path: Path, settings: list[Path]) -> list[startklar.Befund]:
    return startklar.werkzeug_probe(
        tmp_path, SPEC, settings_dateien=settings, werkzeuge=_werkzeuge(tmp_path)
    )


def test_hook_exit2_blockt_werkzeug(tmp_path: Path) -> None:
    befunde = _probe(tmp_path, [_settings(tmp_path, _HOOK_EXIT2)])
    rot = [b for b in befunde if not b.ok]
    assert len(rot) == 1 and "aufraeumen" in rot[0].text, befunde
    assert "Git-Sperre" in rot[0].text and "hook_" in rot[0].text
    assert all(b.bereich == "werkzeug" for b in befunde)


def test_hook_deny_json_blockt_werkzeug(tmp_path: Path) -> None:
    rot = [b for b in _probe(tmp_path, [_settings(tmp_path, _HOOK_DENY)]) if not b.ok]
    assert len(rot) == 1 and "verboten per JSON" in rot[0].text, rot


def test_hook_mit_anderem_matcher_greift_nicht(tmp_path: Path) -> None:
    assert all(
        b.ok
        for b in _probe(
            tmp_path, [_settings(tmp_path, _HOOK_EXIT2, matcher="Edit|Write")]
        )
    )


def test_hook_harmlos_und_kaputt_kein_block(tmp_path: Path) -> None:
    befunde = _probe(
        tmp_path,
        [_settings(tmp_path, _HOOK_HARMLOS), _settings(tmp_path, _HOOK_KAPUTT, "*")],
    )
    assert all(b.ok for b in befunde), befunde
    assert any("Warnung" in b.text and "kaputt" in b.text for b in befunde), befunde


def test_werkzeug_fehlt_oder_scheitert(tmp_path: Path) -> None:
    py = Path(sys.executable).as_posix()
    werkzeuge = (
        startklar.Werkzeug(
            "weg",
            f"{py} {tmp_path.as_posix()}/nicht_da.py",
            f"{tmp_path.as_posix()}/nicht_da.py",
        ),
        startklar.Werkzeug("rot", f'{py} -c "import sys; sys.exit(3)"', py),
    )
    befunde = startklar.werkzeug_probe(
        tmp_path, SPEC, settings_dateien=[], werkzeuge=werkzeuge
    )
    assert [b.ok for b in befunde] == [False, False], befunde
    assert "fehlt" in befunde[0].text and "Exit 3" in befunde[1].text


def test_echte_werkzeuge_mit_echten_hooks(tmp_path: Path) -> None:
    """Echtes ``aufraeumen.mjs`` + echte Settings, falls vorhanden — Befund je Werkzeug."""
    if not (startklar.AUFRAEUMEN.is_file() and shutil.which("node")):
        pytest.skip("aufraeumen.mjs oder node fehlt")
    _haupt, wt = _repo_mit_worktree(tmp_path)
    befunde = startklar.werkzeug_probe(wt, 999999)
    namen = " ".join(b.text for b in befunde)
    assert "aufraeumen" in namen and "neustart" in namen, befunde


# --- Ausgabe, Gate, CLI ----------------------------------------------------------


def test_ausgabe_exit_und_zeilen() -> None:
    gut = startklar.Befund("venv", True, ".venv da", "")
    schlecht = startklar.Befund("schlüssel", False, "FAL_KEY fehlt", "Bitwarden prüfen")
    assert startklar.ausgabe([gut])[0] == 0
    code, text = startklar.ausgabe([gut, schlecht])
    assert code == 1
    assert (
        "✅ venv: .venv da" in text
        and "❌ schlüssel: FAL_KEY fehlt → Bitwarden prüfen" in text
    )


def test_gate_verweigert_bei_rot_und_ist_abschaltbar(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rot = [startklar.Befund("schlüssel", False, "FAL_KEY fehlt", "x")]
    caplog.set_level(logging.INFO)
    assert startklar.gate(tmp_path, SPEC, environ={}, pruefer=lambda o, s: rot) == 2
    assert "Start verweigert" in caplog.text
    assert (
        startklar.gate(
            tmp_path,
            SPEC,
            environ={"TO_SPAWN_STARTKLAR": "aus"},
            pruefer=lambda o, s: rot,
        )
        == 0
    )
    assert "abgeschaltet" in caplog.text
    gruen = [startklar.Befund("venv", True, "ok", "")]
    assert startklar.gate(tmp_path, SPEC, environ={}, pruefer=lambda o, s: gruen) == 0


def _heim_mit_werkzeugen(tmp_path: Path) -> Path:
    heim = tmp_path / "heim"
    ziel = heim / ".claude" / "hooks" / "smart-zone" / "staffel" / "aufraeumen.mjs"
    ziel.parent.mkdir(parents=True)
    ziel.write_text("process.exit(0);\n", encoding="utf-8")
    return heim


def _cli(
    ordner: Path, heim: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "startklar",
            str(SPEC),
            "--ordner",
            str(ordner),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "HOME": str(heim), "USERPROFILE": str(heim), **(env or {})},
        timeout=180,
        check=False,
    )


@pytest.mark.skipif(not shutil.which("node"), reason="node fehlt")
def test_cli_startklar_exit_1_und_0(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    heim = _heim_mit_werkzeugen(tmp_path)
    rot = _cli(wt, heim)
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert "❌ schlüssel" in rot.stdout and "FAL_KEY" in rot.stdout
    (wt / ".env").write_text(
        "FAL_KEY=geheim123\nELEVENLABS_API_KEY=geheim456\n", encoding="utf-8"
    )
    gruen = _cli(wt, heim)
    if gruen.returncode != 0 and "Hook" in gruen.stdout:
        pytest.skip(f"Systemweiter Hook blockt hier: {gruen.stdout}")
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert "geheim123" not in gruen.stdout + gruen.stderr


def test_weg_wache_verweigert_start_bei_rotem_befund(tmp_path: Path) -> None:
    """``wache.py <S>`` ohne --dry-run: rote Startklar-Prüfung → Exit 2, kein Claude-Start."""
    _haupt, wt = _repo_mit_worktree(tmp_path)
    _git(wt, "remote", "add", "origin", "https://github.com/test/startklar.git")
    (wt / ".to-spawn").mkdir()
    (wt / ".to-spawn" / "config.json").write_text(
        json.dumps({"startklar": {"schluessel": ["GIBTS_NICHT_450"]}}), encoding="utf-8"
    )
    heim = _heim_mit_werkzeugen(tmp_path)
    ergebnis = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "wache.py"), str(SPEC)],
        cwd=str(wt),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "TO_SPAWN_REPO": str(wt),
            "HOME": str(heim),
            "PATH": os.environ["PATH"],
        },
        timeout=180,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 2, ausgabe
    assert "Start verweigert" in ausgabe and "GIBTS_NICHT_450" in ausgabe
