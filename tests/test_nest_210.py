"""#210 Nest-Bau generisch: ``nest/`` im Skill, Sandbox je Worktree, bws, Werkzeuge, Rechte.

Echt laufen: die CLI ``to_spawn.py nest …`` als Prozess, Git (Worktrees, Klon mit
``origin``), Dateien mit echten Rechten, die Shell-Skripte unter ``nest/``. Gestellt
sind nur Programme im Wegwerf-``PATH`` (``bws``, ``socat``, ``srt``, ``bwrap``,
``claude``), ein Wegwerf-``HOME`` und der Ausführer für apt/npm (Rechenlogik).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
NEST = SKILL / "nest"
SKRIPTE = SKILL / "skripte"

sys.path.insert(0, str(SKILL))

from to_spawn import nest

GEHEIM_1 = "dummy-geheim-wert-123"
GEHEIM_2 = "dummy noch geheimer 456"
TOKEN = "dummy-token-nur-test"


# --- Helfer --------------------------------------------------------------------


def _git(ordner: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ordner), check=True, capture_output=True, text=True
    ).stdout.strip()


def _neues_repo(ordner: Path) -> Path:
    ordner.mkdir(parents=True)
    _git(ordner, "init", "-b", "master")
    _git(ordner, "config", "user.email", "test@example.invalid")
    _git(ordner, "config", "user.name", "Test")
    (ordner / "README.md").write_text("probe\n", encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-m", "Start")
    return ordner


def _umgebung(heim: Path, **extra: str) -> dict[str, str]:
    umgebung = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BWS_ACCESS_TOKEN", "TO_SPAWN_REPO", "BAU_WT_DIR")
    }
    umgebung["HOME"] = str(heim)
    umgebung.update(extra)
    return umgebung


def _cli(
    *args: str, cwd: Path, env: dict[str, str], stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "nest", *args],
        cwd=str(cwd),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _programm(ordner: Path, name: str, inhalt: str) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / name
    datei.write_text("#!/bin/sh\n" + inhalt, encoding="utf-8")
    datei.chmod(0o755)
    return datei


# --- onboarding ------------------------------------------------------------------


def test_onboarding_setzt_flags_und_laesst_fremde_schluessel(tmp_path: Path) -> None:
    datei = tmp_path / ".claude.json"
    datei.write_text(
        json.dumps({"fremd": 1, "projects": {"/alt": {"a": 1}}}), encoding="utf-8"
    )
    ergebnis = _cli(
        "onboarding",
        "--claude-json",
        str(datei),
        "--trust",
        "/home/x/repo",
        "--trust",
        "/home/x/wt",
        cwd=tmp_path,
        env=_umgebung(tmp_path),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    daten = json.loads(datei.read_text(encoding="utf-8"))
    assert daten["hasCompletedOnboarding"] is True
    assert daten["theme"] == "dark"
    assert daten["preferredNotifChannel"] == "terminal_bell"
    assert daten["fremd"] == 1
    assert daten["projects"]["/alt"] == {"a": 1}
    assert daten["projects"]["/home/x/repo"]["hasTrustDialogAccepted"] is True
    assert daten["projects"]["/home/x/wt"]["hasTrustDialogAccepted"] is True


def test_onboarding_legt_fehlende_datei_an(tmp_path: Path) -> None:
    datei = tmp_path / "neu" / ".claude.json"
    ergebnis = _cli(
        "onboarding", "--claude-json", str(datei), "--trust", "/r",
        cwd=tmp_path, env=_umgebung(tmp_path),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    daten = json.loads(datei.read_text(encoding="utf-8"))
    assert daten["hasCompletedOnboarding"] is True
    assert daten["projects"]["/r"]["hasTrustDialogAccepted"] is True


def test_onboarding_kaputtes_json_bricht_ab_ohne_ueberschreiben(tmp_path: Path) -> None:
    datei = tmp_path / ".claude.json"
    datei.write_text("{kaputt", encoding="utf-8")
    ergebnis = _cli(
        "onboarding", "--claude-json", str(datei), "--trust", "/r",
        cwd=tmp_path, env=_umgebung(tmp_path),
    )
    assert ergebnis.returncode != 0
    assert datei.read_text(encoding="utf-8") == "{kaputt"


# --- sandbox ----------------------------------------------------------------------


@pytest.fixture()
def haupt(tmp_path: Path) -> Path:
    repo = _neues_repo(tmp_path / "haupt")
    konfig = repo / ".to-spawn" / "config.json"
    konfig.parent.mkdir()
    konfig.write_text(
        json.dumps({"sandbox": {"netz_zusatz": ["10.0.0.9:22", "beispiel.invalid"]}}),
        encoding="utf-8",
    )
    return repo


def _worktree(haupt: Path, pfad: Path, zweig: str) -> Path:
    _git(haupt, "worktree", "add", "-b", zweig, str(pfad))
    return pfad


def _sandbox_datei(worktree: Path) -> dict:
    return json.loads((worktree / ".to-spawn" / "sandbox.json").read_text(encoding="utf-8"))


def test_sandbox_datei_je_worktree_mit_eigenem_pfad(tmp_path: Path, haupt: Path) -> None:
    wt = tmp_path / "wt"
    sieben = _worktree(haupt, wt / "wt-7", "ticket-7")
    skill_sieben = _worktree(haupt, wt / "skill-7", "skill-7")
    acht = _worktree(haupt, wt / "wt-8", "ticket-8")
    heim = tmp_path / "heim"
    heim.mkdir()
    for ziel in (sieben, acht):
        ergebnis = _cli(
            "sandbox", str(ziel), "--repo", str(haupt), cwd=haupt, env=_umgebung(heim)
        )
        assert ergebnis.returncode == 0, ergebnis.stderr

    a = _sandbox_datei(sieben)
    b = _sandbox_datei(acht)
    schreiben_a = a["filesystem"]["allowWrite"]
    schreiben_b = b["filesystem"]["allowWrite"]
    assert str(sieben) in schreiben_a and str(acht) not in schreiben_a
    assert str(acht) in schreiben_b and str(sieben) not in schreiben_b
    assert str(skill_sieben) in schreiben_a, "weiterer Worktree desselben Tickets fehlt"
    assert str(skill_sieben) not in schreiben_b
    gemeinsam = str((haupt / ".git").resolve())
    for liste in (schreiben_a, schreiben_b):
        assert gemeinsam in liste
        assert str(haupt / ".to-spawn") in liste
        assert str(heim / ".claude") in liste
        assert str(heim / ".claude.json") in liste
        assert "/tmp" in liste
        assert str(haupt) not in liste, "Hauptbaum muss schreibgeschützt bleiben"
    netz = a["network"]["allowedDomains"]
    assert "api.anthropic.com" in netz and "github.com" in netz
    assert "10.0.0.9:22" in netz and "beispiel.invalid" in netz
    assert a["network"]["deniedDomains"] == []
    assert a["filesystem"]["denyRead"] and a["filesystem"]["denyWrite"]  # Fixrunde: Sperren


def test_sandbox_legt_fehlenden_worktree_an(tmp_path: Path) -> None:
    quelle = _neues_repo(tmp_path / "quelle")
    haupt = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(quelle), str(haupt)], check=True)
    wt_basis = tmp_path / "wt"
    heim = tmp_path / "heim"
    heim.mkdir()
    ergebnis = _cli(
        "sandbox", "9", "--repo", str(haupt),
        cwd=haupt, env=_umgebung(heim, BAU_WT_DIR=str(wt_basis)),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    ziel = wt_basis / "wt-9"
    assert (ziel / "README.md").is_file()
    assert _git(ziel, "branch", "--show-current") == "ticket-9"
    assert str(ziel) in _sandbox_datei(ziel)["filesystem"]["allowWrite"]


def test_sandbox_vorhandener_worktree_bleibt_unberuehrt(tmp_path: Path, haupt: Path) -> None:
    ziel = _worktree(haupt, tmp_path / "wt" / "wt-5", "eigener-zweig")
    (ziel / "arbeit.txt").write_text("halb fertig\n", encoding="utf-8")
    ergebnis = _cli(
        "sandbox", str(ziel), "--repo", str(haupt), cwd=haupt, env=_umgebung(tmp_path)
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert _git(ziel, "branch", "--show-current") == "eigener-zweig"
    assert (ziel / "arbeit.txt").read_text(encoding="utf-8") == "halb fertig\n"


def test_sandbox_praefix_modus_aus_und_fehlendes_srt(tmp_path: Path, haupt: Path) -> None:
    ziel = _worktree(haupt, tmp_path / "wt" / "wt-3", "ticket-3")
    alles_da = {"srt": "/x/srt", "bwrap": "/x/bwrap"}
    aus = {"sandbox": {"modus": "aus"}}
    assert nest.sandbox_praefix(aus, str(ziel), haupt, which=alles_da.get) == []
    an = {"sandbox": {"modus": "an", "netz_zusatz": []}}
    ohne_bwrap = {"srt": "/x/srt"}
    assert nest.sandbox_praefix(an, str(ziel), haupt, which=ohne_bwrap.get) == []
    assert not (ziel / ".to-spawn" / "sandbox.json").exists()
    praefix = nest.sandbox_praefix(an, str(ziel), haupt, which=alles_da.get)
    datei = ziel / ".to-spawn" / "sandbox.json"
    assert praefix == ["/x/srt", "--settings", str(datei), "--", *nest.SANDBOX_KENNUNG]
    assert datei.is_file()


def test_sandbox_praefix_trocken_schreibt_nichts(tmp_path: Path, haupt: Path) -> None:
    ziel = tmp_path / "wt" / "wt-4"
    an = {"sandbox": {"modus": "an"}}
    praefix = nest.sandbox_praefix(
        an, str(ziel), haupt, which={"srt": "/x/srt", "bwrap": "/x/bwrap"}.get, trocken=True
    )
    assert praefix == [
        "/x/srt", "--settings", str(ziel / ".to-spawn" / "sandbox.json"), "--",
        *nest.SANDBOX_KENNUNG,
    ]
    assert not ziel.exists()


def _bau_repo(tmp_path: Path, modus: str) -> Path:
    repo = _neues_repo(tmp_path / "bau-repo")
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-900.json").write_text(
        json.dumps(
            {"spec": 900, "feature": "wegwerf",
             "tickets": {"901": {"title": "Wegwerf", "schaetzung_k": 50, "umfang": "x"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (manifeste / "_default.json").write_text(
        (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    konfig = repo / ".to-spawn" / "config.json"
    konfig.parent.mkdir()
    konfig.write_text(json.dumps({"sandbox": {"modus": modus}}), encoding="utf-8")
    return repo


@pytest.mark.parametrize("modus", ["an", "aus"])
def test_bau_probelauf_zeigt_sandbox_praefix(tmp_path: Path, modus: str) -> None:
    repo = _bau_repo(tmp_path, modus)
    binaer = tmp_path / "bin"
    for name in ("srt", "bwrap", "claude"):
        _programm(binaer, name, "exit 0\n")
    wt_basis = tmp_path / "wt"
    umgebung = _umgebung(
        tmp_path,
        PATH=f"{binaer}{os.pathsep}{os.environ['PATH']}",
        BAU_WT_DIR=str(wt_basis),
    )
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", "--dry-run"],
        cwd=str(repo), env=umgebung, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    datei = wt_basis / "wt-901" / ".to-spawn" / "sandbox.json"
    if modus == "an":
        assert f"{binaer / 'srt'} --settings {datei}" in ausgabe, ausgabe
    else:
        assert "--settings " + str(datei) not in ausgabe
    assert not (wt_basis / "wt-901").exists(), "Probelauf darf nichts anlegen"


# --- secrets ----------------------------------------------------------------------

BWS_STUB = f"""
[ "$BWS_ACCESS_TOKEN" = "{TOKEN}" ] || {{ echo "Token fehlt" >&2; exit 9; }}
echo "$@" > "$FAKE_BWS_ARGS"
if [ -n "$FAKE_BWS_EXIT" ]; then echo "Fehler: gestellt" >&2; exit "$FAKE_BWS_EXIT"; fi
if [ -n "$FAKE_BWS_KAPUTT" ]; then echo "kein json"; exit 0; fi
printf '%s' '[{{"id":"1","key":"API_KEY","value":"{GEHEIM_1}"}},{{"id":"2","key":"ZWEI","value":"{GEHEIM_2}"}}]'
"""


@pytest.fixture()
def bws_welt(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    binaer = tmp_path / "bin"
    _programm(binaer, "bws", BWS_STUB)
    heim = tmp_path / "heim"
    heim.mkdir()
    umgebung = _umgebung(
        heim,
        PATH=f"{binaer}{os.pathsep}{os.environ['PATH']}",
        FAKE_BWS_ARGS=str(tmp_path / "bws_args.txt"),
    )
    return heim, umgebung


def test_secrets_schreibt_env_600_ohne_werte_in_ausgabe(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]]
) -> None:
    _heim, umgebung = bws_welt
    ziel = tmp_path / "repo" / ".env"
    ziel.parent.mkdir()
    ziel.write_text("ALT=1\nAPI_KEY=alter-wert\n# Kommentar\n", encoding="utf-8")
    ergebnis = _cli(
        "secrets", "--ziel", str(ziel), "--projekt", "proj-42",
        cwd=tmp_path, env={**umgebung, "BWS_ACCESS_TOKEN": TOKEN},
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    for geheim in (GEHEIM_1, GEHEIM_2, TOKEN, "alter-wert"):
        assert geheim not in ausgabe
    assert "API_KEY" in ausgabe and "ZWEI" in ausgabe
    assert "ALT" in ausgabe, "Schlüssel nur in .env muss gemeldet werden"
    text = ziel.read_text(encoding="utf-8")
    assert f"API_KEY={GEHEIM_1}" in text
    assert "ALT=1" in text and "# Kommentar" in text
    assert "alter-wert" not in text
    assert "ZWEI=" in text and GEHEIM_2 in text
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600
    argumente = (tmp_path / "bws_args.txt").read_text(encoding="utf-8")
    assert "proj-42" in argumente and TOKEN not in argumente


def test_secrets_token_aus_datei(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]]
) -> None:
    heim, umgebung = bws_welt
    token_datei = heim / ".config" / "to-spawn" / "bws_token"
    token_datei.parent.mkdir(parents=True)
    token_datei.write_text(TOKEN + "\n", encoding="utf-8")
    token_datei.chmod(0o600)
    ziel = tmp_path / ".env"
    ergebnis = _cli("secrets", "--ziel", str(ziel), cwd=tmp_path, env=umgebung)
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600
    assert TOKEN not in ergebnis.stdout + ergebnis.stderr


@pytest.mark.parametrize("stoerung", [{"FAKE_BWS_EXIT": "3"}, {"FAKE_BWS_KAPUTT": "1"}])
def test_secrets_bws_fehler_laesst_env_unveraendert(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]], stoerung: dict[str, str]
) -> None:
    _heim, umgebung = bws_welt
    ziel = tmp_path / ".env"
    ziel.write_text("ALT=1\n", encoding="utf-8")
    ergebnis = _cli(
        "secrets", "--ziel", str(ziel),
        cwd=tmp_path, env={**umgebung, "BWS_ACCESS_TOKEN": TOKEN, **stoerung},
    )
    assert ergebnis.returncode != 0
    assert ziel.read_text(encoding="utf-8") == "ALT=1\n"
    assert TOKEN not in ergebnis.stdout + ergebnis.stderr


def test_secrets_ohne_token_setup_zeile(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]]
) -> None:
    _heim, umgebung = bws_welt
    ziel = tmp_path / ".env"
    ergebnis = _cli("secrets", "--ziel", str(ziel), cwd=tmp_path, env=umgebung)
    assert ergebnis.returncode == 1
    assert "Machine-Account-Token fehlt" in ergebnis.stdout + ergebnis.stderr
    assert not ziel.exists()


# --- werkzeuge --------------------------------------------------------------------


def _werkzeug_welt(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "werkzeuge.json").write_text(
        json.dumps(
            {
                "standard": "alles an",
                "abgewaehlt": ["skill-ab", "adb"],
                "unterbau": ["ffmpeg", "gh", "adb"],
                "kategorien": {"bauen": ["skill-da", "skill-fehlt", "skill-ab"]},
            }
        ),
        encoding="utf-8",
    )
    claude_home = tmp_path / "heim" / ".claude"
    ordner = claude_home / "skills" / "skill-da"
    ordner.mkdir(parents=True)
    (ordner / "SKILL.md").write_text(
        "---\nname: skill-da\ndescription: probe\n---\n", encoding="utf-8"
    )
    home_json = tmp_path / "heim" / ".claude.json"
    return repo, claude_home, home_json


def test_werkzeuge_nur_pruefen_ruft_nie_installation(tmp_path: Path) -> None:
    repo, claude_home, home_json = _werkzeug_welt(tmp_path)
    aufrufe: list[list[str]] = []

    def ausfuehren(befehl: list[str]) -> int:
        aufrufe.append(befehl)
        return 0

    def which(name: str) -> str | None:
        return None if name == "ffmpeg" else f"/x/{name}"

    bericht = nest.pruefe_werkzeuge(
        repo, claude_home=claude_home, home_json=home_json,
        which=which, ausfuehren=ausfuehren, installieren=False,
    )
    assert aufrufe == []
    assert bericht.exit_code == 1
    text = bericht.text()
    assert "ffmpeg" in text and "fehlt" in text
    assert "skill-fehlt" in text and "nest_push.sh" in text
    assert "skill-ab" not in text, "abgewähltes Werkzeug darf nicht als fehlend gelten"
    zeilen = {z.name: z.zustand for z in bericht.unterbau}
    assert zeilen["ffmpeg"] == "fehlt"
    assert "adb" not in zeilen, "abgewählter Unterbau wird nicht geprüft"
    assert zeilen["gh"] == "da"


def test_werkzeuge_installieren_ruft_ausfuehrer(tmp_path: Path) -> None:
    repo, claude_home, home_json = _werkzeug_welt(tmp_path)
    installiert: set[str] = set()
    aufrufe: list[list[str]] = []

    def ausfuehren(befehl: list[str]) -> int:
        aufrufe.append(befehl)
        installiert.add(befehl[-1])
        return 0

    def which(name: str) -> str | None:
        if name == "ffmpeg" and "ffmpeg" not in installiert:
            return None
        return f"/x/{name}"

    bericht = nest.pruefe_werkzeuge(
        repo, claude_home=claude_home, home_json=home_json,
        which=which, ausfuehren=ausfuehren, installieren=True,
    )
    assert len(aufrufe) == 1 and "apt-get" in aufrufe[0] and aufrufe[0][-1] == "ffmpeg"
    zeilen = {z.name: z.zustand for z in bericht.unterbau}
    assert zeilen["ffmpeg"] == "installiert"
    assert bericht.exit_code == 1, "skill-fehlt fehlt weiterhin"


def test_werkzeuge_rezept_fuer_jeden_befehls_unterbau() -> None:
    from to_spawn import inventur

    namen = {r.name for r in nest.REZEPTE}
    for unterbau in inventur.UNTERBAUTEN:
        if unterbau.befehle:
            assert unterbau.name in namen, f"Rezept fehlt für {unterbau.name}"


def test_werkzeuge_cli_alles_da_exit_0(tmp_path: Path) -> None:
    repo, claude_home, _home_json = _werkzeug_welt(tmp_path)
    daten = json.loads((repo / ".to-spawn" / "werkzeuge.json").read_text(encoding="utf-8"))
    daten["kategorien"]["bauen"] = ["skill-da", "skill-ab"]
    (repo / ".to-spawn" / "werkzeuge.json").write_text(json.dumps(daten), encoding="utf-8")
    binaer = tmp_path / "bin"
    protokoll = tmp_path / "aufrufe.txt"
    for rezept in nest.REZEPTE:
        _programm(binaer, rezept.befehl, f'echo "{rezept.befehl} $*" >> "{protokoll}"\n')
    for name in ("apt-get", "npm", "sudo"):
        _programm(binaer, name, f'echo "{name} $*" >> "{protokoll}"\n')
    ergebnis = _cli(
        "werkzeuge", "--repo", str(repo),
        cwd=tmp_path, env=_umgebung(claude_home.parent, PATH=str(binaer)),
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    aufrufe = protokoll.read_text(encoding="utf-8") if protokoll.exists() else ""
    assert "apt-get" not in aufrufe and "npm" not in aufrufe


# --- auswahl (für nest_push.sh) -----------------------------------------------------


def test_auswahl_skills_und_mcps_aus_werkzeugen(tmp_path: Path) -> None:
    repo, claude_home, home_json = _werkzeug_welt(tmp_path)
    ordner = claude_home / "skills" / "skill-ab"
    ordner.mkdir()
    (ordner / "SKILL.md").write_text("---\nname: skill-ab\n---\n", encoding="utf-8")
    home_json.write_text(
        json.dumps({"mcpServers": {"mcp-an": {}, "mcp-fremd": {}}}), encoding="utf-8"
    )
    datei = repo / ".to-spawn" / "werkzeuge.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["kategorien"]["recherche"] = ["mcp-an"]
    datei.write_text(json.dumps(daten), encoding="utf-8")
    env = _umgebung(claude_home.parent)
    skills = _cli("auswahl", "--art", "skill", "--repo", str(repo), cwd=tmp_path, env=env)
    assert skills.returncode == 0, skills.stderr
    assert skills.stdout.split() == ["skill-da"]
    mcps = _cli("auswahl", "--art", "mcp", "--repo", str(repo), cwd=tmp_path, env=env)
    assert mcps.stdout.split() == ["mcp-an"]
    datei.unlink()
    alle = _cli("auswahl", "--art", "skill", "--repo", str(repo), cwd=tmp_path, env=env)
    assert alle.stdout.split() == ["skill-ab", "skill-da"]


# --- rechte ------------------------------------------------------------------------


def test_rechte_ohne_terminal_exit_3_datei_unveraendert(tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    einstellungen = heim / ".claude" / "settings.json"
    einstellungen.parent.mkdir(parents=True)
    einstellungen.write_text('{"model": "x"}\n', encoding="utf-8")
    ergebnis = _cli(
        "rechte", "--eintragen", cwd=tmp_path, env=_umgebung(heim), stdin="JA\n"
    )
    assert ergebnis.returncode == 3
    assert "Das darf nur ein Mensch im eigenen Terminal" in ergebnis.stdout + ergebnis.stderr
    assert einstellungen.read_text(encoding="utf-8") == '{"model": "x"}\n'


def test_rechte_anzeige_enthaelt_worktree_ordner(tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    wt = tmp_path / "wt-ordner"
    ergebnis = _cli("rechte", cwd=tmp_path, env=_umgebung(heim, BAU_WT_DIR=str(wt)))
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert str(wt) in ergebnis.stdout
    assert "Bash(git status:*)" in ergebnis.stdout
    assert "Bash(gh issue edit:*)" in ergebnis.stdout
    assert "additionalDirectories" in ergebnis.stdout
    assert str(heim / ".claude" / "settings.json") in ergebnis.stdout
    assert not (heim / ".claude" / "settings.json").exists()


def test_rechte_eintragen_nur_mit_ja_ohne_doppelte(tmp_path: Path) -> None:
    datei = tmp_path / "settings.json"
    datei.write_text(
        json.dumps({"model": "x", "permissions": {"allow": ["Bash(ls:*)"]}}),
        encoding="utf-8",
    )
    vorschlag = nest.rechte_vorschlag(tmp_path / "wt", tmp_path / "skill", Path("/tmp"))
    code = nest.rechte_eintragen(datei, vorschlag, ist_tty=True, eingabe=lambda _: "ja")
    assert code == 3
    assert "Bash(git status:*)" not in datei.read_text(encoding="utf-8")
    for _ in range(2):
        code = nest.rechte_eintragen(datei, vorschlag, ist_tty=True, eingabe=lambda _: "JA")
        assert code == 0
    daten = json.loads(datei.read_text(encoding="utf-8"))
    erlaubt = daten["permissions"]["allow"]
    assert erlaubt[0] == "Bash(ls:*)" and daten["model"] == "x"
    assert len(erlaubt) == len(set(erlaubt))
    assert "Bash(git status:*)" in erlaubt
    ordner = daten["permissions"]["additionalDirectories"]
    assert str(tmp_path / "wt") in ordner and len(ordner) == len(set(ordner))


# --- Shell-Skripte ------------------------------------------------------------------


def test_nest_ordner_ohne_repo_eigene_namen() -> None:
    for datei in [*NEST.rglob("*"), SKILL / "to_spawn" / "nest.py"]:
        if datei.is_file():
            assert "duoplus" not in datei.read_text(encoding="utf-8").lower(), datei


@pytest.mark.parametrize("name", ["nest_server.sh", "nest_push.sh", "ssh_durch_sandbox.sh"])
def test_nest_skripte_syntax_sauber(name: str) -> None:
    ergebnis = subprocess.run(
        ["bash", "-n", str(NEST / name)], capture_output=True, text=True, check=False
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_nest_server_trocken_zeigt_parameter(tmp_path: Path) -> None:
    ergebnis = subprocess.run(
        ["bash", str(NEST / "nest_server.sh"), "--trocken", "--repo", "probe-org/probe-repo",
         "--nutzer", "probenutzer", "--git-name", "Probe", "--git-mail", "p@example.invalid"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert "duoplus" not in ausgabe.lower()
    for erwartet in (
        "probe-org/probe-repo",
        "/home/probenutzer/probe-repo",
        "/home/probenutzer/stage",
        "/home/probenutzer/wt",
        "Probe",
        "p@example.invalid",
        ".to-spawn/nest_repo.sh",
        "nest rechte",
    ):
        assert erwartet in ausgabe, erwartet


def test_nest_server_ohne_repo_bricht_ab() -> None:
    ergebnis = subprocess.run(
        ["bash", str(NEST / "nest_server.sh"), "--trocken"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert ergebnis.returncode == 2
    assert "--repo" in ergebnis.stderr


def test_nest_push_trocken_zeigt_auswahl(tmp_path: Path) -> None:
    repo, claude_home, _home_json = _werkzeug_welt(tmp_path)
    ergebnis = subprocess.run(
        ["bash", str(NEST / "nest_push.sh"), "--trocken", "--host-root", "probe-root",
         "--repo", str(repo)],
        capture_output=True, text=True, timeout=60, check=False,
        env=_umgebung(claude_home.parent),
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert "probe-root" in ausgabe and "skill-da" in ausgabe
    assert "skill-ab" not in ausgabe


def test_ssh_durch_sandbox_ohne_proxy_bricht_ab(tmp_path: Path) -> None:
    ergebnis = subprocess.run(
        ["bash", str(NEST / "ssh_durch_sandbox.sh"), "h", "22"],
        env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert ergebnis.returncode != 0


def test_install_kopiert_nest() -> None:
    sh = (SKILL / "install.sh").read_text(encoding="utf-8")
    ps1 = (SKILL / "install.ps1").read_text(encoding="utf-8")
    kopier_zeile = sh.split("for eintrag in", 1)[1].split("; do", 1)[0]
    assert " nest" in kopier_zeile
    assert '"nest"' in ps1



# --- Übertragung: settings.json + MCPs ----------------------------------------------


def test_einstellungen_rechte_nie_aus_quelle_und_bleiben_am_ziel(tmp_path: Path) -> None:
    quelle = tmp_path / "quelle.json"
    quelle.write_text(
        json.dumps({"model": "neu", "permissions": {"allow": ["Bash(rm:*)"]}}),
        encoding="utf-8",
    )
    ziel = tmp_path / "ziel.json"
    ergebnis = _cli(
        "einstellungen", "--quelle", str(quelle), "--ziel", str(ziel),
        cwd=tmp_path, env=_umgebung(tmp_path),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert json.loads(ziel.read_text(encoding="utf-8")) == {"model": "neu"}
    ziel.write_text(
        json.dumps({"model": "alt", "permissions": {"allow": ["Bash(git status:*)"]}}),
        encoding="utf-8",
    )
    _cli("einstellungen", "--quelle", str(quelle), "--ziel", str(ziel),
         cwd=tmp_path, env=_umgebung(tmp_path))
    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert daten["model"] == "neu"
    assert daten["permissions"] == {"allow": ["Bash(git status:*)"]}


def test_mcp_export_600_ohne_werte_in_ausgabe(tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    (heim / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "suche": {"command": "npx", "env": {"KEY": GEHEIM_1}, "fremd": 1},
                    "chrome-devtools": {"command": "npx", "args": ["x"]},
                }
            }
        ),
        encoding="utf-8",
    )
    ziel = tmp_path / "mcp.json"
    ergebnis = _cli(
        "mcp-export", "--ziel", str(ziel), "suche", "chrome-devtools", "fehlt",
        cwd=tmp_path, env=_umgebung(heim),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert GEHEIM_1 not in ergebnis.stdout + ergebnis.stderr
    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert daten["suche"] == {"command": "npx", "env": {"KEY": GEHEIM_1}}
    assert daten["chrome-devtools"]["args"] == ["x", "--headless"]
    assert "fehlt" not in daten
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600


# === Fixrunde nach Prüfpanel (#210) ==================================================


def test_fix_vorgabe_sandbox_aus_und_pflicht() -> None:
    from to_spawn import config

    assert config.DEFAULTS["sandbox"]["modus"] == "aus"
    assert config.DEFAULTS["sandbox"]["pflicht"] is True


def test_fix_bau_probelauf_ohne_sandbox_konfig_ohne_srt(tmp_path: Path) -> None:
    repo = _bau_repo(tmp_path, "an")
    (repo / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    binaer = tmp_path / "bin"
    for name in ("srt", "bwrap", "claude"):
        _programm(binaer, name, "exit 0\n")
    umgebung = _umgebung(
        tmp_path, PATH=f"{binaer}{os.pathsep}{os.environ['PATH']}",
        BAU_WT_DIR=str(tmp_path / "wt"),
    )
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", "--dry-run"],
        cwd=str(repo), env=umgebung, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "--settings " + str(tmp_path / "wt") not in ergebnis.stdout + ergebnis.stderr


def test_fix_secrets_null_schluessel_schreibt_nichts(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]]
) -> None:
    _heim, umgebung = bws_welt
    binaer = Path(umgebung["PATH"].split(os.pathsep)[0])
    _programm(binaer, "bws", "printf '[]'\n")
    ziel = tmp_path / ".env"
    ergebnis = _cli(
        "secrets", "--ziel", str(ziel), cwd=tmp_path,
        env={**umgebung, "BWS_ACCESS_TOKEN": TOKEN},
    )
    assert ergebnis.returncode != 0
    assert not ziel.exists()
    ziel.write_text("ALT=1\n", encoding="utf-8")
    _cli("secrets", "--ziel", str(ziel), cwd=tmp_path, env={**umgebung, "BWS_ACCESS_TOKEN": TOKEN})
    assert ziel.read_text(encoding="utf-8") == "ALT=1\n"


def test_fix_env_wert_einfache_anfuehrung_rundreise(
    tmp_path: Path, bws_welt: tuple[Path, dict[str, str]]
) -> None:
    dotenv = pytest.importorskip("dotenv")
    _heim, umgebung = bws_welt
    binaer = Path(umgebung["PATH"].split(os.pathsep)[0])
    schwierig = "dummy it's $HOME \"x\" #y"
    daten = json.dumps([{"key": "SCHWER", "value": schwierig}])
    _programm(binaer, "bws", f"cat <<'JSON'\n{daten}\nJSON\n")
    ziel = tmp_path / ".env"
    ergebnis = _cli(
        "secrets", "--ziel", str(ziel), cwd=tmp_path,
        env={**umgebung, "BWS_ACCESS_TOKEN": TOKEN},
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "SCHWER='" in ziel.read_text(encoding="utf-8")
    assert dotenv.dotenv_values(ziel)["SCHWER"] == schwierig


def test_fix_mcp_import_merged_atomar_600(tmp_path: Path) -> None:
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(
        json.dumps({"fremd": 1, "mcpServers": {"alt": {"command": "behalten"}}}),
        encoding="utf-8",
    )
    quelle = tmp_path / "mcp.json"
    quelle.write_text(
        json.dumps(
            {"alt": {"command": "neu"}, "suche": {"command": "npx", "env": {"K": GEHEIM_1}}}
        ),
        encoding="utf-8",
    )
    ergebnis = _cli(
        "mcp-import", "--quelle", str(quelle), "--claude-json", str(claude_json),
        cwd=tmp_path, env=_umgebung(tmp_path),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert GEHEIM_1 not in ergebnis.stdout + ergebnis.stderr
    daten = json.loads(claude_json.read_text(encoding="utf-8"))
    assert daten["fremd"] == 1
    assert daten["mcpServers"]["alt"] == {"command": "behalten"}
    assert daten["mcpServers"]["suche"]["env"]["K"] == GEHEIM_1
    assert stat.S_IMODE(claude_json.stat().st_mode) == 0o600


def test_fix_nest_server_ohne_mcp_add_json_in_argv() -> None:
    text = (NEST / "nest_server.sh").read_text(encoding="utf-8")
    assert "add-json" not in text
    assert "mcp-import" in text


def test_fix_sandbox_sperren(tmp_path: Path, haupt: Path) -> None:
    ziel = _worktree(haupt, tmp_path / "wt" / "wt-6", "ticket-6")
    heim = tmp_path / "heim"
    heim.mkdir()
    ergebnis = _cli("sandbox", str(ziel), "--repo", str(haupt), cwd=haupt, env=_umgebung(heim))
    assert ergebnis.returncode == 0, ergebnis.stderr
    daten = _sandbox_datei(ziel)
    gemeinsam = (haupt / ".git").resolve()
    sperre_schreiben = daten["filesystem"]["denyWrite"]
    for pfad in (
        gemeinsam / "hooks",
        gemeinsam / "config",
        heim / ".claude" / "settings.json",
        heim / ".claude" / "settings.local.json",
        heim / ".claude" / "hooks",
        heim / ".claude" / "skills",
        heim / ".claude" / "agents",
    ):
        assert str(pfad) in sperre_schreiben, pfad
    assert str(heim / ".config" / "to-spawn") in daten["filesystem"]["denyRead"]
    assert not any(".ssh" in p for p in daten["filesystem"]["denyRead"])


def test_fix_sandbox_kennung_im_praefix_und_ssh_match(tmp_path: Path, haupt: Path) -> None:
    assert "TO_SPAWN_SANDBOX=1" in nest.SANDBOX_KENNUNG
    text = (NEST / "nest_server.sh").read_text(encoding="utf-8")
    match_zeilen = [z for z in text.splitlines() if "Match exec" in z]
    assert match_zeilen and all("TO_SPAWN_SANDBOX" in z for z in match_zeilen)
    assert not any("HTTPS_PROXY" in z for z in match_zeilen)


def test_fix_sandbox_start_pflicht_bricht_ab(tmp_path: Path, haupt: Path) -> None:
    ziel = _worktree(haupt, tmp_path / "wt" / "wt-2", "ticket-2")
    ohne_srt = {"bwrap": "/x/bwrap"}.get
    an = {"sandbox": {"modus": "an", "pflicht": True}}
    assert nest.sandbox_start(an, str(ziel), haupt, which=ohne_srt) is None
    locker = {"sandbox": {"modus": "an", "pflicht": False}}
    assert nest.sandbox_start(locker, str(ziel), haupt, which=ohne_srt) == []
    aus = {"sandbox": {"modus": "aus"}}
    assert nest.sandbox_start(aus, str(ziel), haupt, which=ohne_srt) == []
    # Worktree scheitert (kein origin) + Pflicht → Abbruch
    fehlt = tmp_path / "wt" / "wt-77"
    alles = {"srt": "/x/srt", "bwrap": "/x/bwrap"}.get
    assert nest.sandbox_start(an, str(fehlt), haupt, which=alles) is None
    assert nest.sandbox_start(locker, str(fehlt), haupt, which=alles) == []


def test_fix_bau_py_praefix_nach_blocker_warten() -> None:
    text = (SKRIPTE / "bau.py").read_text(encoding="utf-8")
    warten = text.index("auf_blocker_warten(ticket, max(60, args.takt))")
    echt = text.index("nest.sandbox_start(konfig, worktree_pfad(ticket), REPO)")
    assert echt > warten


def test_fix_worktree_anlegen_holt_origin(tmp_path: Path) -> None:
    quelle = _neues_repo(tmp_path / "quelle")
    haupt = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(quelle), str(haupt)], check=True)
    (quelle / "neu.txt").write_text("frisch\n", encoding="utf-8")
    _git(quelle, "add", "-A")
    _git(quelle, "commit", "-m", "neu")
    heim = tmp_path / "heim"
    heim.mkdir()
    ergebnis = _cli(
        "sandbox", "12", "--repo", str(haupt),
        cwd=haupt, env=_umgebung(heim, BAU_WT_DIR=str(tmp_path / "wt")),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert (tmp_path / "wt" / "wt-12" / "neu.txt").is_file(), "vorher git fetch origin"


def test_fix_zweigname_ohne_ticket_gesaeubert(tmp_path: Path) -> None:
    quelle = _neues_repo(tmp_path / "quelle")
    haupt = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(quelle), str(haupt)], check=True)
    ziel = tmp_path / "wt" / "mein wt~x"
    ergebnis = _cli(
        "sandbox", str(ziel), "--repo", str(haupt), cwd=haupt, env=_umgebung(tmp_path)
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert _git(ziel, "branch", "--show-current") == "mein-wt-x"


def test_fix_werkzeuge_nur_genannte_unterbauten(tmp_path: Path) -> None:
    repo, claude_home, home_json = _werkzeug_welt(tmp_path)
    datei = repo / ".to-spawn" / "werkzeuge.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten.pop("unterbau")
    daten["setup_zeilen"] = [{"name": "codex", "fehlgrund": "x", "abhilfe": "y"}]
    datei.write_text(json.dumps(daten), encoding="utf-8")
    aufrufe: list[list[str]] = []

    def ausfuehren(befehl: list[str]) -> int:
        aufrufe.append(befehl)
        return 1

    bericht = nest.pruefe_werkzeuge(
        repo, claude_home=claude_home, home_json=home_json,
        which=lambda _n: None, ausfuehren=ausfuehren, installieren=True, ist_root=True,
    )
    namen = {z.name for z in bericht.unterbau}
    assert {"bubblewrap", "socat", "ripgrep", "srt"} <= namen
    assert "codex" in namen, "in setup_zeilen genannt"
    assert "ffmpeg" not in namen and "gh" not in namen and "adb" not in namen
    installiert = {b[-1] for b in aufrufe}
    assert "ffmpeg" not in installiert and "@openai/codex" in installiert
    datei.unlink()
    ohne = nest.pruefe_werkzeuge(
        repo, claude_home=claude_home, home_json=home_json,
        which=lambda _n: None, ausfuehren=ausfuehren, installieren=False,
    )
    assert {z.name for z in ohne.unterbau} == {"bubblewrap", "socat", "ripgrep", "srt"}
    assert ohne.hinweise


def test_fix_rechte_ergaenzt(tmp_path: Path) -> None:
    for eintrag in (
        "Bash(git push:*)", "Bash(gh issue close:*)", "Bash(python3 */to_spawn.py eintrag:*)"
    ):
        assert eintrag in nest.RECHTE_ERLAUBEN


def test_fix_geteuid_windows_sicher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "geteuid", raising=False)
    assert nest.ist_root() is False


def test_fix_staging_geheimnisse_werden_geraeumt() -> None:
    server = (NEST / "nest_server.sh").read_text(encoding="utf-8")
    for name in ("env", "gh_token", "bws_token", "credentials.json", "mcp.json"):
        assert f'rm -f "$STAGE/{name}"' in server, name
    push = (NEST / "nest_push.sh").read_text(encoding="utf-8")
    assert "-mindepth 1 -delete" in push
    assert "schon eine .env" in server


def test_fix_nest_server_fehlerliste_exit_3() -> None:
    text = (NEST / "nest_server.sh").read_text(encoding="utf-8")
    assert "FEHLER_LISTE" in text and "exit 3" in text
    assert "|| echo 'FEHLT'" not in text and "|| echo FEHLT" not in text
    assert "git status --porcelain" in text


class _Proxy:
    """Kleiner HTTP-CONNECT-Proxy: merkt sich die Anfrage, antwortet 200, spiegelt Daten."""

    def __init__(self) -> None:
        import socket
        import threading

        self.anfrage = b""
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.faden = threading.Thread(target=self._lauf, daemon=True)
        self.faden.start()

    def _lauf(self) -> None:
        verbindung, _ = self.server.accept()
        while b"\r\n\r\n" not in self.anfrage:
            teil = verbindung.recv(1024)
            if not teil:
                return
            self.anfrage += teil
        verbindung.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        while True:
            teil = verbindung.recv(1024)
            if not teil:
                break
            verbindung.sendall(teil)
        verbindung.close()


def test_fix_ssh_helfer_connect_ohne_anmeldung_in_argv(tmp_path: Path) -> None:
    import base64

    proxy = _Proxy()
    ergebnis = subprocess.run(
        ["bash", str(NEST / "ssh_durch_sandbox.sh"), "10.0.0.9", "22"],
        input=b"hallo\n",
        env={"PATH": "/usr/bin:/bin",
             "HTTPS_PROXY": f"http://nutzer:dummy-auth@127.0.0.1:{proxy.port}"},
        capture_output=True, timeout=30, check=False,
    )
    proxy.faden.join(5)
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert ergebnis.stdout == b"hallo\n"
    kopf = proxy.anfrage.decode()
    assert kopf.startswith("CONNECT 10.0.0.9:22 HTTP/1.1\r\n")
    erwartet = base64.b64encode(b"nutzer:dummy-auth").decode()
    assert f"Proxy-Authorization: Basic {erwartet}" in kopf
    assert b"dummy-auth" not in ergebnis.stderr
    skript = (NEST / "ssh_durch_sandbox.sh").read_text(encoding="utf-8")
    assert "socat" not in skript and "proxyauth" not in skript


def test_fix_readme_nest_verweist_auf_214() -> None:
    text = (SKILL / "README.md").read_text(encoding="utf-8")
    assert "Nest" in text and "#214" in text
