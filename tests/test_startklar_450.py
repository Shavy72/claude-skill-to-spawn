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
import shlex
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


_MINI_VENV: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def _mini_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Echtes Mini-venv (F15): ``sys.prefix`` hat bei System-Python kein ``bin/python``."""
    ziel = tmp_path_factory.mktemp("mini") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(ziel)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    _MINI_VENV.append(ziel)
    return ziel


@pytest.fixture(autouse=True)
def _gh_still(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kein echtes ``gh`` gegen Temp-Repos: seit Fixrunde 2 macht ein unlesbares Issue
    den Befund rot. Tests, die das Lesen prüfen, geben ``issue_leser`` selbst mit."""
    monkeypatch.setattr(startklar, "_gh_issue_text", lambda _ordner, _nummer: "")


def _repo_mit_worktree(
    tmp_path: Path, venv_im_haupt: bool = True, name: str = "haupt"
) -> tuple[Path, Path]:
    """Echtes Hauptrepo + Worktree; Hauptrepo-.venv = Symlink auf ein echtes Mini-venv."""
    haupt = tmp_path / name
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
        os.symlink(_MINI_VENV[0], haupt / ".venv", target_is_directory=True)
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
    assert (haupt / ".venv").resolve() == _MINI_VENV[0].resolve()


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
    # F14: nicht nur Namen — die echten Werkzeuge müssen durch die echten Hooks.
    assert all(b.ok for b in befunde), befunde


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
    # Temp-Repo hat kein GitHub: stilles ``gh`` im PATH (leerer Issue-Text, Exit 0).
    gh = heim / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    gh.chmod(0o755)
    env = {"PATH": f"{gh.parent}{os.pathsep}{os.environ['PATH']}"}
    rot = _cli(wt, heim, env)
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert "❌ schlüssel" in rot.stdout and "FAL_KEY" in rot.stdout
    (wt / ".env").write_text(
        "FAL_KEY=geheim123\nELEVENLABS_API_KEY=geheim456\n", encoding="utf-8"
    )
    gruen = _cli(wt, heim, env)
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


# --- Fixrunde 1 (Prüfpanel Runde 1, docs/verify-hard/450/panel_runde1.md) --------


def _wache(
    cwd: Path, repo: Path, heim: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "wache.py"), str(SPEC), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "TO_SPAWN_REPO": str(repo), "HOME": str(heim)},
        timeout=180,
        check=False,
    )


def _konfig_schluessel(ordner: Path, name: str) -> None:
    (ordner / ".to-spawn").mkdir(exist_ok=True)
    (ordner / ".to-spawn" / "config.json").write_text(
        json.dumps({"startklar": {"schluessel": [name]}}), encoding="utf-8"
    )


def test_f1_wache_prueft_den_ordner_des_aufsehers(tmp_path: Path) -> None:
    """Gate prüft ``Path.cwd()`` (dort läuft der Aufseher), nicht ``TO_SPAWN_REPO``."""
    haupt, wt = _repo_mit_worktree(tmp_path)
    _git(haupt, "remote", "add", "origin", "https://github.com/t/s.git")
    _konfig_schluessel(haupt, "GIBTS_NICHT_HAUPT")
    _konfig_schluessel(wt, "GIBTS_NICHT_CWD")
    ergebnis = _wache(wt, haupt, _heim_mit_werkzeugen(tmp_path))
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 2, ausgabe
    assert "GIBTS_NICHT_CWD" in ausgabe and "GIBTS_NICHT_HAUPT" not in ausgabe


def _leere_venv(ordner: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(ordner / ".venv")],
        check=True,
        timeout=300,
    )


def test_f2_venv_ohne_pakete_aus_requirements_ist_rot(tmp_path: Path) -> None:
    ordner = tmp_path / "o"
    ordner.mkdir()
    _leere_venv(ordner)
    (ordner / "requirements.txt").write_text(
        "# x\ngibts-nicht-450==1.0\n", encoding="utf-8"
    )
    befund = startklar.venv_sicherstellen(ordner)
    assert not befund.ok, befund
    assert "gibts-nicht-450" in befund.text
    assert "requirements.txt" in befund.behebung


def test_f2_frische_venv_installiert_requirements_scheitern_rot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rückfall ``-m venv`` + requirements.txt → ``pip install -r``; scheitert es → rot."""
    _haupt, wt = _repo_mit_worktree(tmp_path, venv_im_haupt=False)
    (wt / "requirements.txt").write_text("gibts-nicht-450==1.0\n", encoding="utf-8")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    befund = startklar.venv_sicherstellen(wt)
    assert not befund.ok, befund
    assert "requirements.txt" in befund.text + befund.behebung


def test_f2_venv_mit_allen_paketen_gruen(tmp_path: Path) -> None:
    ordner = tmp_path / "o"
    ordner.mkdir()
    _leere_venv(ordner)
    (ordner / "requirements.txt").write_text(
        "# nur Kommentar\n-r andere.txt\nwinpaket; sys_platform == 'win32'\n",
        encoding="utf-8",
    )
    assert startklar.venv_sicherstellen(ordner).ok


@pytest.mark.parametrize(
    "inhalt", ["{kaputt", json.dumps({"tickets": [1, 2]}), json.dumps([1])]
)
def test_f3_kaputtes_manifest_ist_rot(tmp_path: Path, inhalt: str) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    pfad = wt / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    pfad.parent.mkdir(parents=True)
    pfad.write_text(inhalt, encoding="utf-8")
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    rot = [b for b in befunde if not b.ok]
    assert rot and "Manifest" in rot[0].text, befunde


def test_f4_ohne_bash_ist_probe_rot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(startklar, "_bash_pfad", lambda: None, raising=False)
    befunde = _probe(tmp_path, [_settings(tmp_path, _HOOK_HARMLOS)])
    assert befunde and not any(b.ok for b in befunde), befunde
    assert all("bash" in b.text + b.behebung for b in befunde), befunde


# Muster aus dem Git-Sperre-Hook (Vorfall V7): Skript-Sprache (node/python…) +
# geschützter Pfad ``~/.claude/`` im Rohtext → Exit 2.
_HOOK_V7 = """import json, re, sys
d = json.load(sys.stdin)
cmd = d["tool_input"]["command"]
if cmd.split()[0].startswith(("python", "perl", "node", "ruby")) and re.search(r"(^|\\s)~/\\.claude/", cmd):
    print("Git-Sperre (#345): node-Skript schreibt auf geschützte Datei", file=sys.stderr)
    sys.exit(2)
"""

_HOOK_MITSCHNITT = """import json, sys
d = json.load(sys.stdin)
with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(d["tool_input"]["command"] + "\\n")
"""


def _settings_mit_argument(tmp_path: Path, hook_code: str, argument: str) -> Path:
    hook = tmp_path / "hook_mitschnitt.py"
    hook.write_text(hook_code, encoding="utf-8")
    datei = tmp_path / "settings_mitschnitt.json"
    befehl = f"{Path(sys.executable).as_posix()} {hook.as_posix()} {argument}"
    datei.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"command": befehl}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    return datei


@pytest.mark.skipif(not shutil.which("node"), reason="node fehlt")
def test_f5_v7_hook_sieht_tilde_wie_im_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vorfall V7 nachgestellt: Hook blockt ``node ~/.claude/…`` — Probe muss das sehen."""
    heim = _heim_mit_werkzeugen(tmp_path)
    monkeypatch.setenv("HOME", str(heim))
    befunde = startklar.werkzeug_probe(
        tmp_path, SPEC, settings_dateien=[_settings(tmp_path, _HOOK_V7)]
    )
    rot = {b.text.split(":")[0] for b in befunde if not b.ok}
    assert rot == {"aufraeumen"}, befunde
    assert "Git-Sperre" in " ".join(b.text for b in befunde)


@pytest.mark.skipif(not shutil.which("node"), reason="node fehlt")
def test_f5_hooks_bekommen_prompt_text_woertlich(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heim = _heim_mit_werkzeugen(tmp_path)
    monkeypatch.setenv("HOME", str(heim))
    _manifest(tmp_path, {"451": {"title": "a"}, "452": {"title": "b"}})
    mitschnitt = tmp_path / "mitschnitt.txt"
    befunde = startklar.werkzeug_probe(
        tmp_path,
        SPEC,
        settings_dateien=[
            _settings_mit_argument(tmp_path, _HOOK_MITSCHNITT, mitschnitt.as_posix())
        ],
    )
    assert all(b.ok for b in befunde), befunde
    py = "python" if os.name == "nt" else "python3"
    assert mitschnitt.read_text(encoding="utf-8").splitlines() == [
        f"node ~/.claude/hooks/smart-zone/staffel/aufraeumen.mjs --spec {SPEC}",
        f"{py} {SKILL.as_posix()}/to_spawn.py neustart {SPEC} 451",
    ]


def test_f5_prompt_und_probe_aus_einer_quelle(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    _git(wt, "remote", "add", "origin", "https://github.com/t/s.git")
    ergebnis = _wache(wt, wt, _heim_mit_werkzeugen(tmp_path), "--print-prompt")
    assert ergebnis.returncode == 0, ergebnis.stderr
    for werkzeug in startklar.WERKZEUGE:
        assert startklar.befehl_text(werkzeug, SPEC, "<N>") in ergebnis.stdout, werkzeug


def test_f7_schluessel_nur_in_umgebung_ist_warnung(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    (wt / ".env").write_text("FAL_KEY=a\n", encoding="utf-8")
    befunde = startklar.schluessel_pruefen(
        wt, SPEC, environ={"ELEVENLABS_API_KEY": "geheim456"}
    )
    assert all(b.ok for b in befunde), befunde
    text = " ".join(b.text for b in befunde)
    assert "Warnung" in text and "ELEVENLABS_API_KEY" in text
    assert "geheim456" not in text


def test_f6_issue_text_wird_gescannt_fehler_ist_warnung(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    (wt / ".env").write_text("FAL_KEY=a\nELEVENLABS_API_KEY=b\n", encoding="utf-8")

    def leser(_ordner: Path, nummer: str) -> str:
        if nummer == "453":
            raise RuntimeError("gh: nicht angemeldet")
        return "braucht OPENAI_KEY" if nummer == "451" else ""

    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={}, issue_leser=leser)
    rot = [b for b in befunde if not b.ok]
    assert rot and "OPENAI_KEY" in rot[0].text, befunde
    assert "453" in " ".join(b.text for b in befunde)


@pytest.mark.parametrize(
    "antwort",
    [
        {"hookSpecificOutput": {"permissionDecision": "ask"}},
        {"continue": False, "stopReason": "halt"},
    ],
)
def test_f8_ask_und_continue_false_blocken(
    tmp_path: Path, antwort: dict[str, object]
) -> None:
    code = (
        "import json, sys\nd = json.load(sys.stdin)\n"
        f"if 'aufraeumen' in d['tool_input']['command']: print(json.dumps({antwort!r}))\n"
    )
    rot = [b for b in _probe(tmp_path, [_settings(tmp_path, code)]) if not b.ok]
    assert len(rot) == 1 and "aufraeumen" in rot[0].text, rot


def test_f9_unlesbare_settings_sind_rot(tmp_path: Path) -> None:
    kaputt = tmp_path / "settings_kaputt.json"
    kaputt.write_text("{nicht json", encoding="utf-8")
    befunde = _probe(tmp_path, [kaputt])
    assert any(not b.ok and "settings_kaputt" in b.text for b in befunde), befunde


def test_f9_permissions_deny_bash_regel_blockt(tmp_path: Path) -> None:
    datei = tmp_path / "settings_deny.json"
    neustart = _werkzeuge(tmp_path)[1]
    praefix = startklar.befehl_text(neustart, SPEC, 0).rsplit(" pass", 1)[0]
    datei.write_text(
        json.dumps({"permissions": {"deny": [f"Bash({praefix}:*)", "Read(./x)"]}}),
        encoding="utf-8",
    )
    rot = [b for b in _probe(tmp_path, [datei]) if not b.ok]
    assert len(rot) == 1 and "neustart" in rot[0].text and "deny" in rot[0].text, rot


def test_f10_hook_timeout_ist_rot(tmp_path: Path) -> None:
    datei = _settings(tmp_path, "import time; time.sleep(5)\n")
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 1
    datei.write_text(json.dumps(daten), encoding="utf-8")
    befunde = _probe(tmp_path, [datei])
    assert befunde and not any(b.ok for b in befunde), befunde
    assert "Timeout" in befunde[0].text


def test_n4_hook_timeout_kein_zahlwert(tmp_path: Path) -> None:
    datei = _settings(tmp_path, _HOOK_HARMLOS)
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = "viel"
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert all(b.ok for b in _probe(tmp_path, [datei]))


def test_f11_unlink_scheitert_gibt_roten_befund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    os.symlink(tmp_path / "gibts_nicht", wt / ".venv", target_is_directory=True)

    def nein(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("gesperrt")

    monkeypatch.setattr(Path, "unlink", nein)
    befund = startklar.venv_sicherstellen(wt)
    assert not befund.ok and "gesperrt" in befund.text, befund


def test_f12_pfade_mit_leerzeichen(tmp_path: Path) -> None:
    (tmp_path / "mit leer").mkdir()
    _haupt, wt = _repo_mit_worktree(tmp_path / "mit leer")
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    assert (wt / ".venv").is_symlink(), befund


def test_f13_platzhalter_gequotet(monkeypatch: pytest.MonkeyPatch) -> None:
    import shlex

    monkeypatch.setattr(startklar, "SKILL", Path("/tmp/mit leer/skill"))
    text = startklar.befehl_text(startklar.WERKZEUGE[1], SPEC, "7")
    assert shlex.split(text)[1:] == [
        "/tmp/mit leer/skill/to_spawn.py",
        "neustart",
        str(SPEC),
        "7",
    ]


def test_symlink_wird_in_info_exclude_eingetragen(tmp_path: Path) -> None:
    haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    _git(wt, "add", ".gitignore")
    _git(wt, "commit", "-q", "-m", "gi")
    assert startklar.venv_sicherstellen(wt).ok
    assert startklar.venv_sicherstellen(wt).ok
    assert ".venv" not in _git(wt, "status", "--porcelain")
    exclude = (haupt / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count("/.venv") == 1


def test_n1_behebung_nennt_passenden_python() -> None:
    py = "python" if os.name == "nt" else "python3"
    assert f"{py} " in startklar.schluessel_behebung_befehl(Path("/x"))


def test_n2_hook_stderr_ohne_schluesselwerte(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FAL_KEY=supergeheim99\n", encoding="utf-8")
    code = (
        "import sys; sys.stdin.read(); "
        "print('FAL_KEY=supergeheim99', file=sys.stderr); sys.exit(1)\n"
    )
    befunde = _probe(tmp_path, [_settings(tmp_path, code)])
    alles = " ".join(b.text + b.behebung for b in befunde)
    assert "FAL_KEY" in alles and "supergeheim99" not in alles, befunde


# --- Fixrunde 2 (Prüfpanel Runde 2) ----------------------------------------------


def test_r2_gate_fehlendes_manifest_ist_rot(tmp_path: Path) -> None:
    """Gate-Pfad: ohne Manifest ist der Schlüssel-Bedarf unbekannt → rot mit Behebung."""
    _haupt, wt = _repo_mit_worktree(tmp_path)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={}, manifest_pflicht=True)
    rot = [b for b in befunde if not b.ok]
    assert rot, befunde
    assert "Manifest" in rot[0].text
    assert "/to-tickets" in rot[0].behebung
    assert f"docs/agents/manifests/spec-{SPEC}.json" in rot[0].behebung


def test_r2_cli_fehlendes_manifest_bleibt_gruen_mit_hinweis(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    assert all(b.ok for b in befunde) and "kein Manifest" in befunde[0].text


def test_r2_gate_setzt_manifest_pflicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gesehen: dict[str, object] = {}

    def pruefe(ordner: Path, spec: int, **kw: object) -> list[startklar.Befund]:
        gesehen.update(kw)
        return [startklar.Befund("venv", True, "ok")]

    monkeypatch.setattr(startklar, "pruefe", pruefe)
    assert startklar.gate(tmp_path, SPEC, environ={}) == 0
    assert gesehen.get("manifest_pflicht") is True


def test_r2_ohne_env_example_warnung_issue_scan_uebersprungen(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    _manifest(wt, {"451": {"title": "x"}})
    befunde = startklar.schluessel_pruefen(
        wt, SPEC, environ={}, issue_leser=lambda o, n: ""
    )
    text = " ".join(b.text for b in befunde)
    assert "Issue-Scan übersprungen, .env.example fehlt" in text, befunde


def test_r2_issue_nicht_lesbar_ist_rot_und_bricht_ab(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)
    (wt / ".env").write_text("FAL_KEY=a\nELEVENLABS_API_KEY=b\n", encoding="utf-8")
    gefragt: list[str] = []

    def leser(_ordner: Path, nummer: str) -> str:
        gefragt.append(nummer)
        raise subprocess.TimeoutExpired("gh", 30)

    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={}, issue_leser=leser)
    assert gefragt == ["451"], gefragt
    rot = [b for b in befunde if not b.ok]
    assert rot, befunde
    text = " ".join(b.text for b in rot)
    assert "Schlüssel-Prüfung unvollständig: Issue #451 nicht lesbar" in text
    assert "452" in text and "453" in text
    assert "gh auth status" in rot[-1].behebung


def test_r2_gh_fehlt_klare_meldung(tmp_path: Path) -> None:
    wt = _schluessel_welt(tmp_path)

    def leser(_ordner: Path, _nummer: str) -> str:
        raise FileNotFoundError(2, "No such file or directory", "gh")

    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={}, issue_leser=leser)
    assert "gh nicht installiert" in " ".join(b.text for b in befunde if not b.ok)


def test_r2_kaputte_konfig_ist_rot(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".to-spawn").mkdir()
    (wt / ".to-spawn" / "config.json").write_text("{kaputt", encoding="utf-8")
    befunde = startklar.konfig_pruefen(wt)
    assert befunde and not befunde[0].ok and "config.json" in befunde[0].text
    (wt / ".to-spawn" / "config.json").write_text("[1, 2]", encoding="utf-8")
    befunde = startklar.konfig_pruefen(wt)
    assert befunde and not befunde[0].ok
    (wt / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    assert startklar.konfig_pruefen(wt) == []


def test_r2_pruefe_meldet_kaputte_konfig(tmp_path: Path) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".to-spawn").mkdir()
    (wt / ".to-spawn" / "config.json").write_text("{kaputt", encoding="utf-8")
    befunde = startklar.pruefe(
        wt,
        SPEC,
        settings_dateien=[],
        environ={},
        werkzeuge=(),
        issue_leser=lambda o, n: "",
    )
    assert any(not b.ok and "config.json" in b.text for b in befunde), befunde


def test_r2_umgebungswerte_werden_geschwaerzt(tmp_path: Path) -> None:
    _konfig_schluessel(tmp_path, "UMGEBUNGS_KEY")
    code = (
        "import sys; sys.stdin.read(); "
        "print('Wert umgebungsgeheim777', file=sys.stderr); sys.exit(1)\n"
    )
    befunde = startklar.werkzeug_probe(
        tmp_path,
        SPEC,
        settings_dateien=[_settings(tmp_path, code)],
        werkzeuge=_werkzeuge(tmp_path),
        environ={"UMGEBUNGS_KEY": "umgebungsgeheim777"},
    )
    alles = " ".join(b.text + b.behebung for b in befunde)
    assert "Wert" in alles and "umgebungsgeheim777" not in alles, befunde


_GEHEIM64 = "g" * 20 + "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQR"[:44]


@pytest.mark.parametrize(("exit_code", "vorlauf"), [(1, 100), (2, 150)])
def test_r2_erst_schwaerzen_dann_kuerzen(
    tmp_path: Path, exit_code: int, vorlauf: int
) -> None:
    assert len(_GEHEIM64) == 64
    (tmp_path / ".env").write_text(f"FAL_KEY={_GEHEIM64}\n", encoding="utf-8")
    code = (
        "import sys; sys.stdin.read(); "
        f"print('x' * {vorlauf} + {_GEHEIM64!r}, file=sys.stderr); sys.exit({exit_code})\n"
    )
    befunde = _probe(tmp_path, [_settings(tmp_path, code)])
    alles = " ".join(b.text + b.behebung for b in befunde)
    assert "xxxx" in alles, befunde
    assert _GEHEIM64[:8] not in alles, befunde


def test_r2_trockenlauf_erst_schwaerzen_dann_kuerzen(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"FAL_KEY={_GEHEIM64}\n", encoding="utf-8")
    skript = tmp_path / "laut.py"
    skript.write_text(
        f"import sys; print('x' * 120 + {_GEHEIM64!r}, file=sys.stderr); sys.exit(3)\n",
        encoding="utf-8",
    )
    werkzeug = startklar.Werkzeug(
        "laut", f"{shlex.quote(sys.executable)} {shlex.quote(str(skript))}", str(skript)
    )
    befunde = startklar.werkzeug_probe(
        tmp_path, SPEC, settings_dateien=[], werkzeuge=(werkzeug,)
    )
    alles = " ".join(b.text for b in befunde)
    assert "Exit 3" in alles and _GEHEIM64[:8] not in alles, befunde


def test_r2_symlink_venv_paket_probe(tmp_path: Path) -> None:
    """Symlink aufs Hauptrepo: Pakete aus requirements.txt werden trotzdem geprüft."""
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / "requirements.txt").write_text("gibts-nicht-450==1.0\n", encoding="utf-8")
    befund = startklar.venv_sicherstellen(wt)
    assert (wt / ".venv").is_symlink()
    assert not befund.ok and "gibts-nicht-450" in befund.text, befund
    befund = startklar.venv_sicherstellen(wt)  # vorhandener Symlink
    assert not befund.ok and "gibts-nicht-450" in befund.text, befund


def test_r2_pip_fehler_wird_geschwaerzt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path, venv_im_haupt=False)
    (wt / ".env").write_text("FAL_KEY=geheimpaketxyz123\n", encoding="utf-8")
    (wt / "requirements.txt").write_text("geheimpaketxyz123==1.0\n", encoding="utf-8")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    befund = startklar.venv_sicherstellen(wt)
    assert not befund.ok, befund
    assert "geheimpaketxyz123" not in befund.text + befund.behebung, befund


def test_r2_vorhandener_symlink_nicht_ignoriert_kommt_in_exclude(
    tmp_path: Path,
) -> None:
    haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".gitignore").write_text(".env\n", encoding="utf-8")
    os.symlink(haupt / ".venv", wt / ".venv", target_is_directory=True)
    befund = startklar.venv_sicherstellen(wt)
    assert befund.ok, befund
    exclude = (haupt / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.venv" in exclude.splitlines()
    assert ".venv" not in _git(wt, "status", "--porcelain", "--", ".venv")


def test_r2_anforderungen_ueberspringt_urls(tmp_path: Path) -> None:
    datei = tmp_path / "requirements.txt"
    datei.write_text(
        "requests==2\ngit+https://github.com/x/y.git\nhttps://x.de/p.whl\n",
        encoding="utf-8",
    )
    assert startklar._anforderungen(datei) == ["requests"]


# --- Fixrunde 3: echtes gh, Gate ohne Manifest ---------------------------------

_GH_ECHT = startklar._gh_issue_text


def _gh_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Worktree mit ``.env.example`` + 2 Tickets, echtes ``_gh_issue_text`` aktiv."""
    monkeypatch.setattr(startklar, "_gh_issue_text", _GH_ECHT)
    _haupt, wt = _repo_mit_worktree(tmp_path)
    (wt / ".env.example").write_text("FAL_KEY=\n", encoding="utf-8")
    _manifest(wt, {"451": {"title": "a"}, "452": {"title": "b"}})
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    monkeypatch.setenv("PATH", str(bin_ordner))
    return wt, bin_ordner


def test_r3_ohne_gh_im_path_ist_rot_gh_nicht_installiert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt, _bin = _gh_welt(tmp_path, monkeypatch)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    rot = [b for b in befunde if not b.ok]
    assert rot and "gh nicht installiert" in rot[0].text
    assert "unvollständig" in rot[0].text


def test_r3_gh_exit1_ist_rot_und_fragt_nur_ein_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt, bin_ordner = _gh_welt(tmp_path, monkeypatch)
    zaehl = tmp_path / "aufrufe.txt"
    gh = bin_ordner / "gh"
    gh.write_text(
        f'#!/bin/sh\necho "$3" >> {shlex.quote(str(zaehl))}\necho "not logged in" >&2\nexit 1\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    rot = [b for b in befunde if not b.ok]
    assert rot and "unvollständig" in rot[0].text
    assert "not logged in" in rot[0].text
    assert zaehl.read_text(encoding="utf-8").split() == ["451"]
    assert "452" in rot[0].text  # nicht geprüft, aber genannt


def test_r3_gh_fehlertext_ist_geschwaerzt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt, bin_ordner = _gh_welt(tmp_path, monkeypatch)
    (wt / ".env").write_text("FAL_KEY=geheimer-wert-12345\n", encoding="utf-8")
    gh = bin_ordner / "gh"
    gh.write_text(
        "#!/bin/sh\necho 'token geheimer-wert-12345 abgelehnt' >&2\nexit 1\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    befunde = startklar.schluessel_pruefen(wt, SPEC, environ={})
    text = " ".join(b.text + b.behebung for b in befunde)
    assert "unvollständig" in text
    assert "geheimer-wert-12345" not in text


def test_r3_gate_ohne_manifest_laeuft_durch_und_ist_rot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    echt = startklar.pruefe

    def ohne_werkzeuge(ordner: Path, spec: int, **kw: object) -> list[startklar.Befund]:
        # echtes pruefe samt manifest_pflicht des Gates; nur Werkzeug-Probe neutralisiert
        return echt(ordner, spec, **{**kw, "werkzeuge": (), "environ": {}})  # type: ignore[arg-type]

    monkeypatch.setattr(startklar, "pruefe", ohne_werkzeuge)
    assert startklar.gate(wt, SPEC, environ={}) == 2
    befunde = startklar.pruefe(wt, SPEC, manifest_pflicht=True)
    rot = [b for b in befunde if not b.ok]
    assert len(rot) == 1 and "fehlt" in rot[0].text and rot[0].bereich == "schlüssel"


# --- Fixrunde 4: Konfig mit falschem Typ ----------------------------------------


@pytest.mark.parametrize(
    "inhalt",
    ['{"startklar": "abc"}', '{"startklar": {"schluessel": "FAL_KEY"}}'],
)
def test_r4_konfig_falscher_typ_rot_ohne_absturz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inhalt: str
) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    datei = startklar.konfig_pfad(wt)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(inhalt, encoding="utf-8")
    befunde = startklar.pruefe(wt, SPEC, werkzeuge=(), environ={})
    rot = [b for b in befunde if not b.ok and b.bereich == "konfig"]
    assert len(rot) == 1 and "startklar" in rot[0].text


# --- Fixrunde 5: Manifest-Feld schluessel mit falschem Typ ----------------------


@pytest.mark.parametrize("wert", [5, "FAL_KEY", ["FAL_KEY", 7]])
def test_r5_manifest_schluessel_falscher_typ_rot_ohne_absturz(
    tmp_path: Path, wert: object
) -> None:
    _haupt, wt = _repo_mit_worktree(tmp_path)
    _manifest(wt, {"1": {"title": "x", "schluessel": wert}})
    befunde = startklar.pruefe(
        wt, SPEC, werkzeuge=(), environ={}, issue_leser=lambda _o, _n: ""
    )
    rot = [b for b in befunde if not b.ok and b.bereich == "schlüssel"]
    assert len(rot) == 1 and "schluessel" in rot[0].text, befunde
