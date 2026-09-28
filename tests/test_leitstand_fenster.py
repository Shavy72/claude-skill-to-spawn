"""Bau-Leitstand als Fenster von /to-spawn: Takt-Anweisung, Seite, Start-Mail, Schalter, Wiring.

Rechenteile direkt (Anweisung, Seite, Befehl), der Rest als echter Prozess gegen ein
Wegwerf-Repo. Die Mail geht nie raus: ``mail.befehl`` zeigt auf ein Test-Skript, das die
Nutzlast in eine Datei schreibt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)
URL = "https://claude.ai/artifact/Dmu7nZQH8E5XzCdFXq3TRi"
MANIFEST = {"spec": 376, "feature": "Test", "tickets": {"377": {"title": "A"}, "378": {"title": "B"}}}


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location("leitstand_fenster_test", SKRIPTE / "leitstand.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def _repo(tmp_path: Path, konfig: dict | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs" / "agents" / "manifests").mkdir(parents=True)
    (repo / "docs" / "agents" / "manifests" / "spec-376.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    if konfig is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, creationflags=OHNE_FENSTER)
    return repo


def _cli(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(
        TO_SPAWN_REPO=str(repo),
        TO_SPAWN_WAECHTER_ORDNER=str(tmp_path / "waechter"),
        TO_SPAWN_GH_REPO="test-org/test-repo",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(  # noqa: S603 — fester Befehl
        [sys.executable, str(SKRIPTE / "leitstand.py"), "376", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        creationflags=OHNE_FENSTER,
    )


def _mail_konfig(tmp_path: Path) -> tuple[dict, Path]:
    """``mail.befehl`` = Test-Skript, das jede Nutzlast als Zeile in eine Datei hängt."""
    ausgang = tmp_path / "mails.jsonl"
    stub = tmp_path / "mail_stub.py"
    stub.write_text(
        "import sys\n"
        f"with open({str(ausgang)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(sys.stdin.read().replace('\\n', ' ') + '\\n')\n",
        encoding="utf-8",
    )
    return {"mail": {"nur_kritisch": True, "befehl": [sys.executable, str(stub)]}}, ausgang


# ---------------------------------------------------------------- Anweisung (reine Rechnung)


def test_anweisung_ersteinrichtung_lokal(ls: ModuleType) -> None:
    text = ls.anweisung_text(
        "376",
        "python",
        "C:/Users/x/.claude/skills/to-spawn/skripte/leitstand.py",
        "C:/r/.to-spawn/leitstand",
        None,
        seed_fehlt=True,
        mail_fehlt=True,
    )
    ls_befehl = "python C:/Users/x/.claude/skills/to-spawn/skripte/leitstand.py 376"
    for teil in (
        f"`{ls_befehl} url`",
        f"`{ls_befehl} seite`",
        f"`{ls_befehl} url --setzen <URL>`",
        f"`{ls_befehl} seed`",
        f"`{ls_befehl} bestaetigen`",
        f"`{ls_befehl} mail`",
    ):
        assert teil in text
    assert "Artifact, publish" in text and '{"db":{}}' in text and "`chart`" in text
    assert "Exit 4 → weiter" in text and "Genau EIN ArtifactData" in text
    assert "vorbereiten" not in text


def test_anweisung_takt_server(ls: ModuleType) -> None:
    py = "/home/bau/duoplus-management/.venv/bin/python"
    skript = "/home/bau/.claude/skills/to-spawn/skripte/leitstand.py"
    text = ls.anweisung_text(
        "376", py, skript, "/home/bau/r/.to-spawn/leitstand", URL, seed_fehlt=False, mail_fehlt=False
    )
    assert f"`{py} {skript} 376 vorbereiten`" in text
    assert f"url `{URL}`" in text and "nichts Neues" in text and "FEHLER <Grund>" in text
    assert " mail`" not in text and " seed`" not in text and "Artifact, publish" not in text


def test_anweisung_takt_holt_fehlendes_seed_und_mail_nach(ls: ModuleType) -> None:
    text = ls.anweisung_text("376", "python", "C:/s/leitstand.py", "C:/o", URL, seed_fehlt=True, mail_fehlt=True)
    assert "`python C:/s/leitstand.py 376 mail`" in text
    assert text.index(" seed`") < text.index(" vorbereiten`")


def test_python_befehl_je_os(ls: ModuleType) -> None:
    assert ls.python_befehl("win32") == "python"
    assert ls.python_befehl("linux") == Path(sys.executable).as_posix()


# ---------------------------------------------------------------- Seite


def test_seite_titel_je_spec(ls: ModuleType) -> None:
    vorlage = ls.VORLAGE.read_text(encoding="utf-8")
    assert vorlage.startswith("<title>")
    seite = ls.rendere_seite(vorlage, "376")
    assert seite.startswith("<title>Bau-Leitstand #376</title>")
    assert seite.count("<title>") == 1 and len(seite) - len(vorlage) == len(" #376")


def test_cli_seite_schreibt_datei(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    erg = _cli(repo, tmp_path, "seite")
    assert erg.returncode == 0, erg.stderr
    pfad = Path(erg.stdout.strip())
    assert pfad.name == "seite-376.html"
    assert pfad.read_text(encoding="utf-8").startswith("<title>Bau-Leitstand #376</title>")


# ---------------------------------------------------------------- Start-Mail


def test_mail_genau_einmal_auch_bei_nur_kritisch(tmp_path: Path) -> None:
    konfig, ausgang = _mail_konfig(tmp_path)
    repo = _repo(tmp_path, konfig)
    assert _cli(repo, tmp_path, "url", "--setzen", URL).returncode == 0
    erste = _cli(repo, tmp_path, "mail")
    assert erste.returncode == 0, erste.stderr
    zweite = _cli(repo, tmp_path, "mail")
    assert zweite.returncode == 0, zweite.stderr
    mails = [json.loads(z) for z in ausgang.read_text(encoding="utf-8").splitlines()]
    assert len(mails) == 1
    assert mails[0]["art"] == "leitstand_start" and URL in mails[0]["text"] and "376" in mails[0]["betreff"]
    assert "keine zweite Mail" in zweite.stderr
    assert json.loads((repo / ".to-spawn/leitstand/mail-376.json").read_text(encoding="utf-8"))["status"] == "gesendet"


def test_mail_ohne_befehl_nur_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"mail": {"befehl": []}})
    _cli(repo, tmp_path, "url", "--setzen", URL)
    erg = _cli(repo, tmp_path, "mail")
    assert erg.returncode == 0, erg.stderr
    assert "mail.befehl fehlt" in erg.stderr
    assert json.loads((repo / ".to-spawn/leitstand/mail-376.json").read_text(encoding="utf-8"))["status"] == "ohne_mail"


def test_mail_ohne_url_exit4(tmp_path: Path) -> None:
    konfig, ausgang = _mail_konfig(tmp_path)
    repo = _repo(tmp_path, konfig)
    assert _cli(repo, tmp_path, "mail").returncode == 4
    assert not ausgang.exists()


def test_mail_befehl_scheitert_exit1_ohne_schluessel(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"mail": {"befehl": [sys.executable, "-c", "raise SystemExit(7)"]}})
    _cli(repo, tmp_path, "url", "--setzen", URL)
    assert _cli(repo, tmp_path, "mail").returncode == 1
    assert not (repo / ".to-spawn/leitstand/mail-376.json").exists()


# ---------------------------------------------------------------- Anweisung folgt dem Stand


def test_anweisung_wechselt_nach_url_und_mail(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"mail": {"befehl": []}})
    erg = _cli(repo, tmp_path, "anweisung")
    assert erg.returncode == 0, erg.stderr
    datei = Path(erg.stdout.strip())
    assert datei.name == "takt-376.md" and "Ersteinrichtung" in datei.read_text(encoding="utf-8")
    _cli(repo, tmp_path, "url", "--setzen", URL)
    text = datei.read_text(encoding="utf-8")
    assert "## Takt" in text and URL in text and " mail`" in text
    _cli(repo, tmp_path, "mail")
    assert " mail`" not in datei.read_text(encoding="utf-8")


# ---------------------------------------------------------------- Schalter + Sitzungsbefehl


def test_schalter_aus_kein_fenster(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"leitstand": {"aktiv": False}})
    assert _cli(repo, tmp_path, "aktiv").returncode == 5
    erg = _cli(repo, tmp_path, "sitzung", "--probe")
    assert erg.returncode == 5 and erg.stdout.strip() == ""


def test_sitzung_probe_befehl_und_werkzeuge(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert _cli(repo, tmp_path, "aktiv").returncode == 0
    erg = _cli(repo, tmp_path, "sitzung", "--probe")
    assert erg.returncode == 0, erg.stderr
    befehl = json.loads(erg.stdout)
    ordner = (repo / ".to-spawn" / "leitstand").resolve().as_posix()
    py = "python" if sys.platform == "win32" else Path(sys.executable).as_posix()
    skript = (SKRIPTE / "leitstand.py").resolve().as_posix()
    assert befehl[:3] == ["claude", "--model", "claude-sonnet-5"]
    werkzeuge = befehl[befehl.index("--allowedTools") + 1 : befehl.index("--")]
    assert werkzeuge == [f"Bash({py} {skript} *)", "Artifact", "ArtifactData", "ToolSearch", f"Read({ordner}/**)"]
    assert befehl[-1] == f"/loop 3m Lies {ordner}/takt-376.md und führe genau das aus"
    assert (repo / ".to-spawn/leitstand/takt-376.md").is_file()


# ---------------------------------------------------------------- Übernahme Prototyp


def test_uebernehmen_prototyp_zustand(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alt = tmp_path / "zustand-376.json"
    alt.write_text(
        json.dumps(
            {
                "gesendet": ["20260927174305-288c3fc29b"],
                "start": {"377": "2026-09-27T17:43:05+02:00", "378": "2026-09-27T18:27:15+02:00"},
                "ende": {"377": "2026-09-27T18:19:41+02:00"},
                "live_inhalt": "{}",
                "live_zeit": "2026-09-27T19:10:29+02:00",
            }
        ),
        encoding="utf-8",
    )
    erg = _cli(repo, tmp_path, "uebernehmen", "--von", str(alt), "--url", URL)
    assert erg.returncode == 0, erg.stderr
    z = json.loads((repo / ".to-spawn/leitstand/zustand-376.json").read_text(encoding="utf-8"))
    assert z["gesendet"] == ["20260927174305-288c3fc29b"] and z["ende"]["377"].startswith("2026-09-27T18:19")
    assert z["seed_gesendet"] == ["meta/stand", "tickets/377", "tickets/378"]
    assert z["gestartet"] == "2026-09-27T17:43:05+02:00" and z["live_zeit"] == "2026-09-27T19:10:29+02:00"
    assert _cli(repo, tmp_path, "url").stdout.strip() == URL


def test_doc_id_gleich_wie_prototyp(ls: ModuleType) -> None:
    """Prototyp ``C:/dev/leitstand/leitstand_takt.py``: sha1 der Quelle, Zeitstempel 14 Ziffern."""
    assert (
        ls.doc_id("2026-09-27T17:43:05+02:00", "start|376|377")
        == "20260927174305-" + __import__("hashlib").sha1(b"start|376|377").hexdigest()[:10]
    )


# ---------------------------------------------------------------- Wiring lokal + Server


def test_spawn_local_oeffnet_leitstand_tab() -> None:
    text = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert '$cmds += "leitstand $Spec"' in text
    assert 'skripte/leitstand.py" "$Spec" aktiv' in text and "$lsAktiv -eq 0" in text
    assert "DETACHED_PROCESS" not in text


def test_spawn_srv_legt_leitstand_fenster_an() -> None:
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    assert 'KURZ+=("leitstand $SPEC")' in text
    assert '/skripte/leitstand.py") $SPEC sitzung' in text
    assert '"$SKILL_HOME/skripte/leitstand.py" "$SPEC" aktiv' in text
    assert 'grep -qx "leitstand $SPEC"' in text
    from git_bash import git_bash

    bash = git_bash() or "bash"  # unter Windows nie das WSL-bash
    erg = subprocess.run([bash, "-n", str(SKRIPTE / "spawn_srv.sh")], capture_output=True, text=True, check=False)
    assert erg.returncode == 0, erg.stderr


def test_alias_leitstand_im_installer() -> None:
    text = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert "function leitstand {" in text
    assert 'skripte/leitstand.py") $args[0] @rest' in text and '$rest = @("sitzung")' in text


def test_melder_leitstand_start_immer() -> None:
    from to_spawn import melder

    assert melder.darf_raus("leitstand_start", {"mail": {"nur_kritisch": True}})
