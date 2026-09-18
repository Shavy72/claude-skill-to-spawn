"""Tests für ``/to-spawn-of`` — Umzug einer Bau-Session auf den Bau-Server (duoplus-management#212).

Git läuft echt (bare ``origin`` + Klon + Worktree im Temp-Ordner). Gestellt ist nur
``ssh`` (kleines Stub-Programm im PATH), weil hier die Reihenfolge-Logik geprüft wird —
der echte Weg lokal → Server ist der Weg-Test auf dem Bau-Server (Belegseite).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"

sys.path.insert(0, str(SKILL))

from to_spawn import config, umzug  # noqa: E402

HEUTE = "2026-09-18"
TICKET = "9121"
SPEC = "9120"
HANDOFF_REL = f"docs/handoffs/HANDOFF_{HEUTE}_{TICKET}.md"
HANDOFF_OK = "# Handoff #9121\n\nStand: Hälfte gebaut.\n\n**Umzug:** server\n"

SSH_STUB = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ["SSH_STUB_LOG"])
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
modus = os.environ.get("SSH_STUB_MODUS", "ok")
befehl = sys.argv[-1]
if modus == "fehler":
    sys.stderr.write("ssh: connect to host bau-server port 22: Connection refused\n")
    sys.exit(255)
if "spawn_srv.sh" in befehl and modus == "laeuft_bereits":
    sys.stderr.write("Ticket #9121 läuft auf dem Server bereits (wartet) — Umzug abgebrochen.\n")
    sys.exit(4)
if "spawn_srv.sh" in befehl and modus in ("abbruch_nach_start", "abbruch_ohne_server"):
    sys.stderr.write("client_loop: send disconnect: Broken pipe\n")
    sys.exit(255)
if "tmux list-windows" in befehl:
    if modus in ("ohne_fenster", "abbruch_ohne_server"):
        sys.exit(1)
    print("wache 9120")
    print("bau 9121")
    sys.exit(0)
if "sessions_stand.py" in befehl:
    print("Ticket     Zustand            Prozess    Claude         Titel")
    print("#9121       läuft seit 12:00   pid 4711   session 4712   Wegwerf")
    sys.exit(0)
sys.exit(0)
"""

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
ausgabe = Path(os.environ["FAKE_AUSGABE"])
ausgabe.mkdir(parents=True, exist_ok=True)
zaehler = ausgabe / "aufrufe.txt"
n = int(zaehler.read_text()) + 1 if zaehler.is_file() else 1
zaehler.write_text(str(n))
(ausgabe / "pid.txt").write_text(str(os.getpid()))
(ausgabe / f"prompt-{n}.txt").write_text(sys.argv[-1], encoding="utf-8")
(ausgabe / "umgebung.json").write_text(json.dumps({
    k: os.environ.get(k) for k in ("BAU_UMZUG_DATEI", "BAU_UMZUG_ANFRAGE", "BAU_TICKET")
}), encoding="utf-8")
if os.environ.get("FAKE_MODUS") == "umzug":
    # Staffel-Übergabe zusätzlich — darf trotz Umzug KEINE zweite Runde auslösen.
    handoff = ausgabe / "HANDOFF_2026-09-18_9121.md"
    handoff.write_text("Staffel: weiter\n", encoding="utf-8")
    Path(os.environ["BAU_STAFFEL_DATEI"]).write_text(json.dumps({"handoff": str(handoff)}))
    Path(os.environ["BAU_UMZUG_DATEI"]).write_text(json.dumps({
        "ticket": "9121", "handoff": "x", "branch": "ticket-9121", "ziel": "bau-server", "zeit": "jetzt"
    }))
    time.sleep(120)
sys.exit(0)
"""


# --- Hilfen -----------------------------------------------------------------


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(ort), check=True, capture_output=True, text=True
    ).stdout.strip()


def _ausfuehrbar(pfad: Path, inhalt: str) -> None:
    pfad.write_text(inhalt, encoding="utf-8")
    pfad.chmod(0o755)


def _manifest(repo: Path) -> None:
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True, exist_ok=True)
    (manifeste / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {
                "spec": int(SPEC),
                "tickets": {
                    "9121": {"title": "Wegwerf", "schaetzung_k": 120, "umfang": "Kern."},
                    "9122": {"title": "Zweites", "schaetzung_k": 120, "umfang": "Rest."},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (manifeste / "_default.json").write_text(
        (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"), encoding="utf-8"
    )


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """bare origin + Haupt-Klon (master) + Worktree ``wt-9121`` auf Branch ``ticket-9121``."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "master", str(origin))
    haupt = tmp_path / "duoplus-management"
    _git(tmp_path, "clone", "-q", str(origin), str(haupt))
    for schluessel, wert in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
    ):
        _git(haupt, "config", schluessel, wert)
    _git(haupt, "checkout", "-q", "-B", "master")
    _manifest(haupt)
    (haupt / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(haupt, "add", "-A")
    _git(haupt, "commit", "-q", "-m", "start")
    _git(haupt, "push", "-q", "-u", "origin", "master")
    wt = tmp_path / "wt" / "wt-9121"
    _git(haupt, "worktree", "add", "-q", "-b", "ticket-9121", str(wt), "master")

    binaer = tmp_path / "bin"
    binaer.mkdir()
    _ausfuehrbar(binaer / "ssh", SSH_STUB)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SSH_STUB_LOG", str(tmp_path / "ssh.log"))
    monkeypatch.setenv("SSH_STUB_MODUS", "ok")
    monkeypatch.setenv("TO_SPAWN_SSH_ZIEL", "bau-server")
    monkeypatch.setenv("BAU_UMZUG_DATEI", str(tmp_path / "out" / "umzug.json"))
    (tmp_path / "out").mkdir()
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.delenv("BAU_UMZUG_ANFRAGE", raising=False)
    monkeypatch.setattr(umzug, "BEWEIS_TAKT", 0.01)
    monkeypatch.setattr(umzug, "BEWEIS_MAX", 0.5)
    monkeypatch.chdir(wt)
    return {"origin": origin, "haupt": haupt, "wt": wt, "tmp": tmp_path}


def _handoff(wt: Path, inhalt: str = HANDOFF_OK, rel: str = HANDOFF_REL) -> Path:
    pfad = wt / rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    return pfad


def _remote_sha(welt: dict[str, Path], branch: str = "ticket-9121") -> str:
    zeile = _git(welt["haupt"], "ls-remote", "origin", f"refs/heads/{branch}")
    return zeile.split()[0] if zeile else ""


def _ssh_aufrufe(welt: dict[str, Path]) -> list[list[str]]:
    log = welt["tmp"] / "ssh.log"
    if not log.is_file():
        return []
    return [json.loads(z) for z in log.read_text(encoding="utf-8").splitlines() if z.strip()]


def _umzug(welt: dict[str, Path], handoff: Path, **kw: object) -> int:
    return umzug.umzug_einzel(
        TICKET, handoff, worktree=welt["wt"], konfig=config.lade(welt["haupt"]), **kw
    )


# --- 1. Handoff-Prüfung -------------------------------------------------------


def test_handoff_ohne_umzug_zeile_weigert(welt: dict[str, Path]) -> None:
    pfad = _handoff(welt["wt"], "Stand: fertig bis zur Hälfte.\n")
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _remote_sha(welt) == ""
    assert _ssh_aufrufe(welt) == []


def test_handoff_mit_staffel_weiter_weigert(welt: dict[str, Path]) -> None:
    pfad = _handoff(welt["wt"], HANDOFF_OK + "**Staffel:** weiter\n")
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _remote_sha(welt) == ""


@pytest.mark.parametrize(
    "rel", ["docs/handoffs/notiz.md", f"docs/handoffs/HANDOFF_{HEUTE}_9122.md"]
)
def test_handoff_falscher_name_weigert(welt: dict[str, Path], rel: str) -> None:
    pfad = _handoff(welt["wt"], rel=rel)
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _remote_sha(welt) == ""


def test_handoff_fehlt_weigert(welt: dict[str, Path]) -> None:
    assert _umzug(welt, welt["wt"] / HANDOFF_REL) == umzug.EXIT_WEIGERUNG


# --- 2. Git-Stand ---------------------------------------------------------------


def test_dreckiger_baum_weigert_und_pusht_nicht(welt: dict[str, Path]) -> None:
    (welt["wt"] / "code.py").write_text("x = 2\n", encoding="utf-8")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _remote_sha(welt) == ""
    assert _ssh_aufrufe(welt) == []
    assert _git(welt["wt"], "log", "-1", "--format=%s") == "start"


def test_branch_master_weigert(welt: dict[str, Path]) -> None:
    rel = f"docs/handoffs/HANDOFF_{HEUTE}_{TICKET}.md"
    pfad = _handoff(welt["haupt"], rel=rel)
    ergebnis = umzug.umzug_einzel(
        TICKET, pfad, worktree=welt["haupt"], konfig=config.lade(welt["haupt"])
    )
    assert ergebnis == umzug.EXIT_WEIGERUNG
    assert _ssh_aufrufe(welt) == []


def test_handoff_wird_committet_gepusht_und_session_beendet(welt: dict[str, Path]) -> None:
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == 0
    betreff = _git(welt["wt"], "log", "-1", "--format=%s")
    assert betreff.endswith(f"(#{TICKET}) [skip ci]"), betreff
    assert _git(welt["wt"], "show", "--name-only", "--format=", "HEAD") == HANDOFF_REL
    assert _remote_sha(welt) == _git(welt["wt"], "rev-parse", "HEAD")

    aufrufe = _ssh_aufrufe(welt)
    start = aufrufe[0]
    assert start[:3] == ["-o", "BatchMode=yes", "bau-server"]
    assert "cd ~/duoplus-management && bash scripts/spawn_srv.sh 9120 --tickets 9121" in start[-1]
    # Fixrunde #212 (Befund 7): die Referenz trägt den Commit-SHA mit.
    kopf = _git(welt["wt"], "rev-parse", "HEAD")
    assert f"--ohne-wache --umzug ticket-9121@{kopf}:{HANDOFF_REL}" in start[-1]
    assert any("tmux list-windows -t =spec-9120" in a[-1] for a in aufrufe[1:])
    assert any("sessions_stand.py 9120" in a[-1] for a in aufrufe[1:])

    daten = json.loads(Path(os.environ["BAU_UMZUG_DATEI"]).read_text(encoding="utf-8"))
    assert {"ticket", "handoff", "branch", "ziel", "zeit"} <= set(daten)
    assert daten["ticket"] == TICKET
    assert daten["branch"] == "ticket-9121"
    assert daten["ziel"] == "bau-server"


def test_schon_committeter_handoff_wird_nur_gepusht(welt: dict[str, Path]) -> None:
    pfad = _handoff(welt["wt"])
    _git(welt["wt"], "add", HANDOFF_REL)
    _git(welt["wt"], "commit", "-q", "-m", "docs: Handoff (#9121) [skip ci]")
    vorher = _git(welt["wt"], "rev-parse", "HEAD")
    assert _umzug(welt, pfad) == 0
    assert _git(welt["wt"], "rev-parse", "HEAD") == vorher
    assert _remote_sha(welt) == vorher


def test_server_start_scheitert_kein_umzug_json(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_STUB_MODUS", "fehler")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_FEHLER
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_server_ohne_beweis_kein_umzug_json(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_STUB_MODUS", "ohne_fenster")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_FEHLER
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_ohne_bau_umzug_datei_warnt(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("BAU_UMZUG_DATEI")
    pfad = _handoff(welt["wt"])
    with caplog.at_level("WARNING"):
        assert _umzug(welt, pfad) == 0
    assert "von Hand beenden" in caplog.text


def test_dry_run_macht_nichts(welt: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad, dry_run=True) == 0
    assert _remote_sha(welt) == ""
    assert _ssh_aufrufe(welt) == []
    assert _git(welt["wt"], "log", "-1", "--format=%s") == "start"
    assert "spawn_srv.sh 9120" in capsys.readouterr().out
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_server_repo_aus_konfig(welt: dict[str, Path]) -> None:
    konfig = config.lade(welt["haupt"])
    assert umzug.server_repo(konfig, welt["haupt"]) == "~/duoplus-management"
    konfig["server_repo"] = "/srv/repo"
    assert umzug.server_repo(konfig, welt["haupt"]) == "/srv/repo"
    assert "server_repo" in config.DEFAULTS


# --- 3. Stop-Hook ``hook-umzug`` --------------------------------------------


def test_hook_umzug_anfrage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    anfrage = tmp_path / "umzug-anfrage-9121"
    anfrage.write_text("1", encoding="utf-8")
    monkeypatch.setenv("BAU_UMZUG_ANFRAGE", str(anfrage))
    monkeypatch.setenv("BAU_TICKET", "9121")

    assert umzug.hook_umzug_anfrage(json.dumps({"stop_hook_active": True})) == 0
    assert capsys.readouterr().out == ""
    assert anfrage.exists()

    assert umzug.hook_umzug_anfrage("{}") == 0
    antwort = json.loads(capsys.readouterr().out)
    assert antwort["decision"] == "block"
    assert "Umzug: server" in antwort["reason"]
    assert "Staffel: weiter" in antwort["reason"]
    assert "to_spawn.py umzug 9121 --handoff" in antwort["reason"]
    assert "Bau-Server" in antwort["reason"]
    assert not anfrage.exists()
    assert Path(f"{anfrage}.laeuft").exists()

    assert umzug.hook_umzug_anfrage("{}") == 0
    assert capsys.readouterr().out == ""


def test_hook_umzug_ohne_umgebung_still(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BAU_UMZUG_ANFRAGE", raising=False)
    monkeypatch.delenv("BAU_TICKET", raising=False)
    assert umzug.hook_umzug_anfrage("{}") == 0
    assert capsys.readouterr().out == ""


def test_hook_umzug_ueber_cli(tmp_path: Path) -> None:
    anfrage = tmp_path / "umzug-anfrage-9121"
    anfrage.write_text("1", encoding="utf-8")
    ergebnis = subprocess.run(
        [sys.executable, str(CLI), "hook-umzug"],
        input="{}",
        cwd=str(tmp_path),
        env={**os.environ, "BAU_UMZUG_ANFRAGE": str(anfrage), "BAU_TICKET": "9121"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert json.loads(ergebnis.stdout)["decision"] == "block"


@pytest.mark.parametrize("befehl", ["hook-stop", "hook-umzug"])
def test_hook_cli_traegt_die_umzug_anfrage(tmp_path: Path, befehl: str) -> None:
    """``hook-stop`` (Stop-Hook jeder Bau-Session) und ``hook-umzug`` geben die Anfrage weiter."""
    anfrage = tmp_path / "umzug-anfrage-9121"
    anfrage.write_text("1", encoding="utf-8")
    umgebung = {**os.environ, "BAU_UMZUG_ANFRAGE": str(anfrage), "BAU_TICKET": "9121"}
    for weg in ("TO_SPAWN_TICKET", "TO_SPAWN_LOG_REPO"):
        umgebung.pop(weg, None)
    ergebnis = subprocess.run(
        [sys.executable, str(CLI), befehl],
        input="{}",
        cwd=str(tmp_path),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert json.loads(ergebnis.stdout)["decision"] == "block"
    assert Path(f"{anfrage}.laeuft").exists()


def test_cli_hat_umzug_befehle() -> None:
    for befehl in ("umzug", "umzug-alle"):
        ergebnis = subprocess.run(
            [sys.executable, str(CLI), befehl, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert ergebnis.returncode == 0, ergebnis.stderr
    assert "--handoff" in subprocess.run(
        [sys.executable, str(CLI), "umzug", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout


# --- 4. bau.py: Wach-Thread beendet die Session nach dem Umzug ------------------


def _bau(
    repo: Path, tmp_path: Path, *args: str, modus: str = "fertig", timeout: int = 90
) -> subprocess.CompletedProcess[str]:
    binaer = tmp_path / "bin-claude"
    binaer.mkdir(exist_ok=True)
    _ausfuehrbar(binaer / "claude", FAKE_CLAUDE)
    temp = tmp_path / "tmp"
    temp.mkdir(exist_ok=True)
    umgebung = {
        **os.environ,
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "TO_SPAWN_REPO": str(repo),
        "FAKE_AUSGABE": str(tmp_path / "fake"),
        "FAKE_MODUS": modus,
        "TMPDIR": str(temp),
        "BAU_WT_DIR": str(tmp_path / "wt"),
    }
    for weg in ("LOCALAPPDATA", "BAU_UMZUG_DATEI", "BAU_UMZUG_ANFRAGE"):
        umgebung.pop(weg, None)
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), TICKET, *args],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _lebt(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Zombie zählt als tot (Eltern hat noch nicht abgeholt).
    status = Path(f"/proc/{pid}/status")
    return not (status.is_file() and "\nState:\tZ" in status.read_text())


def test_bau_beendet_session_nach_umzug_ohne_staffel(welt: dict[str, Path]) -> None:
    tmp = welt["tmp"]
    start = time.monotonic()
    ergebnis = _bau(welt["haupt"], tmp, "--sofort", modus="umzug")
    dauer = time.monotonic() - start
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert dauer < 60, dauer
    assert "Umzug nach bau-server bestätigt — lokale Session beendet" in ausgabe
    assert (tmp / "fake" / "aufrufe.txt").read_text() == "1"
    pid = int((tmp / "fake" / "pid.txt").read_text())
    assert not _lebt(pid)

    umgebung = json.loads((tmp / "fake" / "umgebung.json").read_text(encoding="utf-8"))
    assert umgebung["BAU_UMZUG_DATEI"].endswith("umzug.json")
    assert umgebung["BAU_UMZUG_ANFRAGE"] == str(
        welt["haupt"] / ".to-spawn" / f"umzug-anfrage-{TICKET}"
    )

    einstellungen = next((tmp / "tmp").rglob("settings.json"))
    stop = json.loads(einstellungen.read_text(encoding="utf-8"))["hooks"]["Stop"]
    befehle = [h["command"] for block in stop for h in block["hooks"]]
    assert any("staffel_stop.py" in b for b in befehle)
    # Die Umzug-Anfrage fährt im Bau-Log-Stop-Hook mit (ein Befehl, #204-Hookzahl bleibt).
    assert any(b.endswith("hook-stop") and "to_spawn.py" in b for b in befehle)


def test_bau_loescht_alte_anfragen(welt: dict[str, Path]) -> None:
    ordner = welt["haupt"] / ".to-spawn"
    ordner.mkdir(exist_ok=True)
    alt = ordner / f"umzug-anfrage-{TICKET}"
    alt.write_text("1", encoding="utf-8")
    Path(f"{alt}.laeuft").write_text("1", encoding="utf-8")
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--sofort")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert not alt.exists()
    assert not Path(f"{alt}.laeuft").exists()


def _handoff_auf_branch(welt: dict[str, Path]) -> None:
    _handoff(welt["wt"])
    _git(welt["wt"], "add", HANDOFF_REL)
    _git(welt["wt"], "commit", "-q", "-m", "docs(#9121): Umzug-Handoff (#9121) [skip ci]")
    _git(welt["wt"], "push", "-q", "origin", "HEAD:refs/heads/ticket-9121")


def test_bau_umzug_startet_mit_handoff_vom_branch(welt: dict[str, Path]) -> None:
    _handoff_auf_branch(welt)
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--umzug", f"ticket-9121:{HANDOFF_REL}")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    prompt = (welt["tmp"] / "fake" / "prompt-1.txt").read_text(encoding="utf-8")
    assert "vom lokalen PC auf den Bau-Server umgezogen" in prompt
    assert "Stand: Hälfte gebaut." in prompt
    assert "origin/ticket-9121" in prompt
    assert "`" not in prompt.split("---\n\n", 1)[0]
    # --umzug impliziert --sofort: keine Blocker-Wache (gh fehlt im Test ohnehin).
    assert "wartet" not in ergebnis.stderr


def test_bau_umzug_handoff_fehlt_exit_2(welt: dict[str, Path]) -> None:
    _handoff_auf_branch(welt)
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--umzug", "ticket-9121:docs/handoffs/fehlt.md")
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "Umzug-Handoff nicht lesbar" in ergebnis.stderr
    assert not (welt["tmp"] / "fake" / "aufrufe.txt").exists()


# --- 5. spawn_srv.sh ------------------------------------------------------------


def _spawn_srv(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SKRIPTE / "spawn_srv.sh"), *args],
        cwd=str(repo),
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def test_spawn_srv_umzug_nur_mit_einem_ticket(welt: dict[str, Path]) -> None:
    ergebnis = _spawn_srv(
        welt["haupt"], SPEC, "--tickets", "9121,9122", "--umzug", "ticket-9121:x.md", "--dry-run"
    )
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "genau ein" in ergebnis.stderr


def test_spawn_srv_umzug_probelauf_zeigt_fenster(welt: dict[str, Path]) -> None:
    ergebnis = _spawn_srv(
        welt["haupt"],
        SPEC,
        "--tickets",
        "9121",
        "--ohne-wache",
        "--umzug",
        f"ticket-9121:{HANDOFF_REL}",
        "--dry-run",
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert f"bau 9121 --umzug ticket-9121:{HANDOFF_REL}" in ausgabe
    assert "Umzug: Regularien galten beim ersten Start" in ausgabe
    assert "wache 9120" not in ausgabe


def test_spawn_srv_nur_wache(welt: dict[str, Path]) -> None:
    ergebnis = _spawn_srv(welt["haupt"], SPEC, "--nur-wache", "--ohne-regularien", "--dry-run")
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert "- wache 9120" in ausgabe
    assert "- bau 9121" not in ausgabe
    assert "- bau 9122" not in ausgabe


def test_spawn_srv_hilfe_nennt_neue_schalter() -> None:
    text = (SKRIPTE / "spawn_srv.sh").read_text(encoding="utf-8")
    for schalter in ("--umzug", "--ohne-regularien", "--nur-wache"):
        assert schalter in text


# --- 6. umzug_alle: streng nacheinander ---------------------------------------


class Welt:
    """Gestellte Zustände für die Reihenfolge-Logik der Wächter-Variante."""

    def __init__(self, zustaende: dict[str, str]) -> None:
        self.zustaende = dict(zustaende)
        self.ereignisse: list[str] = []
        self.server: set[str] = set()
        self.umzieht: set[str] = set()

    def lokal(self, repo: Path, spec: str) -> dict[str, tuple[str, int | None]]:
        # Eine Session mit Anfrage zieht beim nächsten Blick um (wie die echte Session).
        for n in list(self.umzieht):
            self.zustaende[n] = "aus"
            self.server.add(n)
            self.umzieht.discard(n)
        return {n: (z, 1000 + int(n)) for n, z in self.zustaende.items()}

    def beenden(self, pid: int) -> None:
        n = str(pid - 1000)
        self.ereignisse.append(f"beenden {n}")
        self.zustaende[n] = "aus"

    def starten(self, spec: str, ticket: str | None, **kw: object) -> bool:
        ziel = ticket or f"wache {spec}"
        self.ereignisse.append(f"server {ziel}")
        self.server.add(ziel)
        return True

    def laeuft(self, spec: str, ticket: str | None, **kw: object) -> bool:
        return (ticket or f"wache {spec}") in self.server


@pytest.fixture()
def alle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".to-spawn").mkdir()
    monkeypatch.setenv("BAU_UMZUG_DATEI", str(tmp_path / "umzug.json"))

    def machen(zustaende: dict[str, str], zieht_um: bool = True) -> Welt:
        w = Welt(zustaende)
        monkeypatch.setattr(umzug, "lokale_zustaende", w.lokal)
        monkeypatch.setattr(umzug, "prozess_beenden", w.beenden)
        monkeypatch.setattr(umzug, "server_starten", w.starten)
        monkeypatch.setattr(umzug, "server_laeuft", w.laeuft)
        orig_schreiben = umzug.anfrage_schreiben

        def anfrage(repo: Path, ticket: str) -> Path:
            w.ereignisse.append(f"anfrage {ticket}")
            if zieht_um:
                w.umzieht.add(ticket)
            return orig_schreiben(repo, ticket)

        monkeypatch.setattr(umzug, "anfrage_schreiben", anfrage)
        return w

    return machen


def _alle(tmp_path: Path, **kw: object) -> int:
    return umzug.umzug_alle(
        SPEC, repo=tmp_path, konfig=dict(config.DEFAULTS), takt=0.01, **kw
    )


def test_umzug_alle_reihenfolge_und_wache(
    alle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = alle({"9123": "aus", "9122": "läuft seit 10:00", "9121": "wartet"})
    assert _alle(tmp_path, warte_max=5) == 0
    # Fixrunde #212 (Befund 3): erst Server starten + beweisen, dann lokal beenden.
    assert w.ereignisse == [
        "server 9121",
        "beenden 9121",
        "anfrage 9122",
        "server wache 9120",
    ]
    ausgabe = capsys.readouterr().out
    assert "#9121 umgezogen" in ausgabe
    assert "#9122 umgezogen" in ausgabe
    assert "#9123 übersprungen" in ausgabe
    assert Path(os.environ["BAU_UMZUG_DATEI"]).is_file()


def test_umzug_alle_stopp_bei_verwaist(alle, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    w = alle({"9121": "VERWAIST seit 09:00", "9122": "wartet"})
    assert _alle(tmp_path, warte_max=5) == umzug.EXIT_FEHLER
    assert w.ereignisse == []
    assert "Stopp bei #9121" in capsys.readouterr().out
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_umzug_alle_timeout_stoppt_vor_naechstem(
    alle, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = alle({"9121": "läuft seit 10:00", "9122": "wartet"}, zieht_um=False)
    assert _alle(tmp_path, warte_max=0.2) == umzug.EXIT_FEHLER
    assert w.ereignisse == ["anfrage 9121"]
    assert not (tmp_path / ".to-spawn" / "umzug-anfrage-9121").exists()
    assert "Stopp bei #9121 — Rest bleibt lokal" in capsys.readouterr().out
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_umzug_alle_ohne_wache(alle, tmp_path: Path) -> None:
    w = alle({"9121": "wartet"})
    assert _alle(tmp_path, warte_max=5, ohne_wache=True) == 0
    assert w.ereignisse == ["server 9121", "beenden 9121"]  # Befund 3: Server zuerst
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()


def test_umzug_alle_dry_run(alle, tmp_path: Path) -> None:
    w = alle({"9121": "wartet", "9122": "läuft seit 10:00"})
    assert _alle(tmp_path, dry_run=True) == 0
    assert w.ereignisse == []


def test_lokale_zustaende_aus_sessions_stand(tmp_path: Path) -> None:
    """Echte Prozessliste: ein laufendes ``skripte/bau.py <N>`` zählt als ``wartet``.

    Eigene, seltene Nummern — parallele Testläufe anderer Sessions starten ``bau.py 9121``.
    """
    manifeste = tmp_path / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-97530.json").write_text(
        json.dumps({"spec": 97530, "tickets": {"97531": {"title": "a"}, "97532": {"title": "b"}}}),
        encoding="utf-8",
    )
    schlaefer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", str(SKRIPTE / "bau.py"), "97531"],
        cwd=str(tmp_path),  # bau.py läuft immer im Repo-Wurzelordner
    )
    try:
        # ``-c``-Programme zeigen ihre Argumente in der Kommandozeile, der Name bleibt python.
        stand: dict[str, tuple[str, int | None]] = {}
        for _ in range(50):
            stand = umzug.lokale_zustaende(tmp_path, "97530")
            if stand.get("97531", ("aus", None))[0] != "aus":
                break
            time.sleep(0.1)
        assert stand["97531"] == ("wartet", schlaefer.pid)
        assert stand["97532"][0] == "aus"
        assert "97530" not in stand
    finally:
        schlaefer.send_signal(signal.SIGTERM)
        schlaefer.wait(timeout=10)


# --- 7. Alias-Skill + Manifest-Vorlage ------------------------------------------


def test_alias_to_spawn_of() -> None:
    datei = SKILL / "aliase" / "to-spawn-of" / "SKILL.md"
    assert datei.is_file()
    text = datei.read_text(encoding="utf-8")
    kopf = text.split("---\n", 2)[1]
    assert "name: to-spawn-of" in kopf
    assert "description:" in kopf
    for muss in (
        "BAU_TICKET",
        "TO_SPAWN_WACHE_SPEC",
        "Umzug: server",
        "Staffel: weiter",
        "to_spawn.py umzug",
        "umzug-alle",
        "Ganzen Bau auf den Bau-Server verschieben?",
    ):
        assert muss in text, muss
    assert "Umzug `/to-spawn-of`" in (SKILL / "SKILL.md").read_text(encoding="utf-8")
    readme = (SKILL / "to_spawn" / "README.md").read_text(encoding="utf-8")
    assert "umzug-alle" in readme and "hook-umzug" in readme


def test_default_manifest_hat_to_spawn_of() -> None:
    daten = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    assert "to-spawn-of" in daten["core_skills"]


def test_wache_setzt_umzug_umgebung(tmp_path: Path) -> None:
    """wache.py gibt der Wächter-Session ``BAU_UMZUG_DATEI`` + ``TO_SPAWN_WACHE_SPEC`` mit und
    endet mit Exit 0, sobald die Umzug-Datei auftaucht."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _manifest(repo)
    binaer = tmp_path / "bin"
    binaer.mkdir()
    _ausfuehrbar(
        binaer / "claude",
        r"""#!/usr/bin/env python3
import json, os, time
from pathlib import Path
Path(os.environ["FAKE_UMGEBUNG"]).write_text(json.dumps({
    "datei": os.environ.get("BAU_UMZUG_DATEI"), "spec": os.environ.get("TO_SPAWN_WACHE_SPEC")}))
Path(os.environ["BAU_UMZUG_DATEI"]).write_text(json.dumps({"ziel": "bau-server"}))
time.sleep(120)
""",
    )
    beweis = tmp_path / "umgebung.json"
    temp = tmp_path / "tmp"
    temp.mkdir()
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "wache.py"), SPEC],
        cwd=str(repo),
        env={
            **os.environ,
            "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "FAKE_UMGEBUNG": str(beweis),
            "TMPDIR": str(temp),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = json.loads(beweis.read_text(encoding="utf-8"))
    assert daten["spec"] == SPEC
    assert daten["datei"].endswith("umzug.json")


# --- sessions_stand: Prozesse fremder Repos zählen nicht (#212) --------------


def test_sessions_stand_zaehlt_nur_prozesse_im_eigenen_repo(tmp_path: Path) -> None:
    """Gleiche Ticket-Nummer in zwei Repos auf einem Rechner: nur das eigene zählt."""
    if not Path("/proc/self/cwd").exists():
        pytest.skip("braucht /proc (Linux)")
    import importlib.util

    datei = SKILL / "skripte" / "sessions_stand.py"
    spec = importlib.util.spec_from_file_location("_stand_212_test", datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul  # dataclass braucht das Modul in sys.modules
    spec.loader.exec_module(modul)
    eigen, fremd = tmp_path / "eigen", tmp_path / "fremd"
    (eigen / "unter").mkdir(parents=True)
    fremd.mkdir()
    prozesse = []
    for ordner in (eigen / "unter", fremd):
        prozesse.append(
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)", "bau.py", "9123"],
                cwd=str(ordner),
            )
        )
    try:
        modul.REPO = eigen
        assert modul.fremdes_repo(prozesse[0].pid) is False
        assert modul.fremdes_repo(prozesse[1].pid) is True
        assert modul.fremdes_repo(2**22 + 12345) is False  # unlesbar = zählt wie bisher
    finally:
        for proz in prozesse:
            proz.kill()
            proz.wait()


# === Fixrunde #212 (Prüfpanel-Befunde 1–12) ===================================


def _kind_mit_umzug(datei: Path) -> list[str]:
    """Kind, das die Umzug-Datei schreibt und dann weiterläuft (wie eine Session nach Exit 0)."""
    return [
        sys.executable,
        "-c",
        "import pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text('{\"ziel\": \"bau-server\"}'); time.sleep(60)",
        str(datei),
    ]


# --- Befund 1: Windows — ganzen Prozessbaum beenden, danach prüfen ---------------


def test_umzug_wache_windows_beendet_ganzen_baum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datei = tmp_path / "umzug.json"
    aufrufe: list[list[str]] = []
    echt = subprocess.run

    def run(cmd: object, *args: object, **kw: object) -> object:
        if isinstance(cmd, list) and cmd and cmd[0] == "taskkill":
            aufrufe.append([str(c) for c in cmd])
            os.kill(int(cmd[2]), signal.SIGKILL)
            return subprocess.CompletedProcess(cmd, 0, "ERFOLGREICH", "")
        return echt(cmd, *args, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(umzug, "_ist_windows", lambda: True, raising=False)
    monkeypatch.setattr(umzug.subprocess, "run", run)
    _code, daten = umzug.starte_mit_umzug_wache(_kind_mit_umzug(datei), datei, takt=0.05, frist=5)
    assert len(aufrufe) == 1, aufrufe
    assert aufrufe[0][:2] == ["taskkill", "/PID"]
    assert aufrufe[0][3:] == ["/T", "/F"]
    assert daten is not None
    assert daten.get("lokal_beendet") is True


def test_umzug_wache_windows_baum_bleibt_meldet_laut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    datei = tmp_path / "umzug.json"
    echt = subprocess.run

    def run(cmd: object, *args: object, **kw: object) -> object:
        if isinstance(cmd, list) and cmd and cmd[0] == "taskkill":
            return subprocess.CompletedProcess(cmd, 1, "", "FEHLER: Zugriff verweigert")
        return echt(cmd, *args, **kw)  # type: ignore[call-overload]

    monkeypatch.setattr(umzug, "_ist_windows", lambda: True, raising=False)
    monkeypatch.setattr(umzug.subprocess, "run", run)
    with caplog.at_level("ERROR"):
        _code, daten = umzug.starte_mit_umzug_wache(
            _kind_mit_umzug(datei), datei, takt=0.05, frist=0.5
        )
    assert daten is not None
    assert daten.get("lokal_beendet") is False
    assert "Doppel-Lauf" in caplog.text


# --- Befund 2 + 3: umzug_alle liest frisch und startet den Server zuerst -------


def test_umzug_alle_liest_zustand_vor_jeder_aktion_neu(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = alle({"9121": "läuft seit 10:00", "9122": "wartet"})
    vorher = umzug.anfrage_schreiben

    def anfrage(repo: Path, ticket: str) -> Path:
        if ticket == "9121":  # während #9121 umzieht, startet #9122 sein Claude
            w.zustaende["9122"] = "läuft seit 11:00"
        return vorher(repo, ticket)

    monkeypatch.setattr(umzug, "anfrage_schreiben", anfrage)
    assert _alle(tmp_path, warte_max=5) == 0
    assert w.ereignisse == ["anfrage 9121", "anfrage 9122", "server wache 9120"]


def test_umzug_alle_wartet_server_scheitert_lokal_unangetastet(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    w = alle({"9121": "wartet", "9122": "wartet"})

    def starten(spec: str, ticket: str | None, **kw: object) -> bool:
        w.ereignisse.append(f"server {ticket}")
        return False

    monkeypatch.setattr(umzug, "server_starten", starten)
    assert _alle(tmp_path, warte_max=5) == umzug.EXIT_FEHLER
    assert w.ereignisse == ["server 9121"]
    assert w.zustaende["9121"] == "wartet"
    assert "Stopp bei #9121" in capsys.readouterr().out


def test_umzug_alle_wartet_wird_laeuft_nach_serverstart(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = alle({"9121": "wartet"})
    vorher = w.starten

    def starten(spec: str, ticket: str | None, **kw: object) -> bool:
        ok = vorher(spec, ticket, **kw)
        if ticket == "9121":  # lokal startet Claude, während der Server hochfährt
            w.zustaende["9121"] = "läuft seit 11:00"
        return ok

    def schliessen(spec: str, ticket: str, **kw: object) -> bool:
        w.ereignisse.append(f"schliessen {ticket}")
        w.server.discard(ticket)
        return True

    monkeypatch.setattr(umzug, "server_starten", starten)
    monkeypatch.setattr(umzug, "server_fenster_schliessen", schliessen, raising=False)
    assert _alle(tmp_path, warte_max=5, ohne_wache=True) == 0
    assert w.ereignisse == ["server 9121", "schliessen 9121", "anfrage 9121"]


# --- Befund 4: läuft auf dem Server schon → Umzug bricht ab -------------------------


def test_spawn_srv_umzug_laeuft_bereits_exit_4(welt: dict[str, Path]) -> None:
    schlaefer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", str(SKRIPTE / "bau.py"), TICKET],
        cwd=str(welt["haupt"]),
    )
    try:
        for _ in range(50):
            if umzug.lokale_zustaende(welt["haupt"], SPEC).get(TICKET, ("aus", None))[0] != "aus":
                break
            time.sleep(0.1)
        ergebnis = _spawn_srv(
            welt["haupt"], SPEC, "--tickets", TICKET, "--ohne-wache",
            "--umzug", f"ticket-9121:{HANDOFF_REL}", "--dry-run",
        )
        assert ergebnis.returncode == 4, ergebnis.stdout + ergebnis.stderr
        assert "Umzug abgebrochen" in ergebnis.stderr
        assert "nichts gestartet" not in ergebnis.stdout
        # Ohne --umzug bleibt es beim Überspringen.
        # --ohne-regularien: die Regularien-Prüfung bräuchte GitHub und ist hier nicht das Thema.
        normal = _spawn_srv(
            welt["haupt"], SPEC, "--tickets", TICKET, "--ohne-wache", "--ohne-regularien", "--dry-run"
        )
        assert normal.returncode == 0, normal.stdout + normal.stderr
        assert "übersprungen" in normal.stdout
    finally:
        schlaefer.kill()
        schlaefer.wait()


def test_umzug_einzel_server_laeuft_schon_bricht_ab(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SSH_STUB_MODUS", "laeuft_bereits")
    pfad = _handoff(welt["wt"])
    with caplog.at_level("ERROR"):
        assert _umzug(welt, pfad) != 0
    assert "läuft auf dem Server schon" in caplog.text
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()
    assert not any("tmux list-windows" in a[-1] for a in _ssh_aufrufe(welt))


# --- Befund 5: Hintergrund-Aufruf + Aufräumen bei Abbruch --------------------------


def test_alias_umzug_alle_im_hintergrund() -> None:
    text = (SKILL / "aliase" / "to-spawn-of" / "SKILL.md").read_text(encoding="utf-8")
    assert "run_in_background" in text


def test_umzug_alle_raeumt_anfrage_bei_strg_c(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alle({"9121": "läuft seit 10:00"}, zieht_um=False)

    def sleep(_s: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(umzug.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        _alle(tmp_path, warte_max=60)
    anfrage = tmp_path / ".to-spawn" / "umzug-anfrage-9121"
    assert not anfrage.exists()
    assert not Path(f"{anfrage}.laeuft").exists()


def test_umzug_alle_raeumt_anfrage_bei_sigterm(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alle({"9121": "läuft seit 10:00"}, zieht_um=False)

    def still(_n: int, _f: object) -> None:
        return None

    vorher = signal.signal(signal.SIGTERM, still)
    echt_sleep = time.sleep

    def sleep(_s: float) -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        echt_sleep(0.01)

    try:
        monkeypatch.setattr(umzug.time, "sleep", sleep)
        with pytest.raises(SystemExit):
            _alle(tmp_path, warte_max=0.5)
        monkeypatch.setattr(umzug.time, "sleep", echt_sleep)
        assert signal.getsignal(signal.SIGTERM) is still
    finally:
        signal.signal(signal.SIGTERM, vorher)
    assert not (tmp_path / ".to-spawn" / "umzug-anfrage-9121").exists()


# --- Befund 6: SSH-Abbruch nach dem Start → Server einmal prüfen -------------------


def test_ssh_abbruch_nach_start_server_laeuft_doch(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SSH_STUB_MODUS", "abbruch_nach_start")
    pfad = _handoff(welt["wt"])
    with caplog.at_level("WARNING"):
        assert _umzug(welt, pfad) == 0
    assert "läuft trotzdem" in caplog.text
    assert Path(os.environ["BAU_UMZUG_DATEI"]).is_file()


def test_ssh_abbruch_ohne_server_bleibt_fehler(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_STUB_MODUS", "abbruch_ohne_server")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_FEHLER
    assert not Path(os.environ["BAU_UMZUG_DATEI"]).exists()
    assert any("tmux list-windows" in a[-1] for a in _ssh_aufrufe(welt))


# --- Befund 7: Handoff-Referenz mit Commit-SHA, Fetch-Fehler = Exit 2 ---------------


def test_bau_umzug_sha_liest_genau_diesen_stand(welt: dict[str, Path]) -> None:
    _handoff_auf_branch(welt)
    sha1 = _git(welt["wt"], "rev-parse", "HEAD")
    _handoff(welt["wt"], HANDOFF_OK + "Zweiter Umzug: neuer Stand.\n")
    _git(welt["wt"], "commit", "-q", "-m", "docs(#9121): zweiter Umzug (#9121) [skip ci]", "--", HANDOFF_REL)
    _git(welt["wt"], "push", "-q", "origin", "HEAD:refs/heads/ticket-9121")
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--umzug", f"ticket-9121@{sha1}:{HANDOFF_REL}")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    prompt = (welt["tmp"] / "fake" / "prompt-1.txt").read_text(encoding="utf-8")
    assert "Stand: Hälfte gebaut." in prompt
    assert "Zweiter Umzug" not in prompt
    assert "origin/ticket-9121" in prompt


def test_bau_umzug_sha_nicht_auf_branch_exit_2(welt: dict[str, Path]) -> None:
    _handoff_auf_branch(welt)
    _handoff(welt["haupt"], HANDOFF_OK + "fremd\n")
    _git(welt["haupt"], "add", HANDOFF_REL)
    _git(welt["haupt"], "commit", "-q", "-m", "fremd")
    fremd = _git(welt["haupt"], "rev-parse", "HEAD")
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--umzug", f"ticket-9121@{fremd}:{HANDOFF_REL}")
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "nicht in origin/ticket-9121" in ergebnis.stderr
    assert not (welt["tmp"] / "fake" / "aufrufe.txt").exists()


def test_bau_umzug_fetch_fehler_exit_2(welt: dict[str, Path]) -> None:
    _handoff_auf_branch(welt)
    _git(welt["haupt"], "fetch", "-q", "origin")
    _git(welt["haupt"], "remote", "set-url", "origin", str(welt["tmp"] / "weg.git"))
    ergebnis = _bau(welt["haupt"], welt["tmp"], "--umzug", f"ticket-9121:{HANDOFF_REL}")
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "git fetch" in ergebnis.stderr
    assert not (welt["tmp"] / "fake" / "aufrufe.txt").exists()


def test_spawn_srv_reicht_sha_ref_durch(welt: dict[str, Path]) -> None:
    ref = f"ticket-9121@{'a' * 40}:{HANDOFF_REL}"
    ergebnis = _spawn_srv(
        welt["haupt"], SPEC, "--tickets", TICKET, "--ohne-wache", "--umzug", ref, "--dry-run"
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert f"bau 9121 --umzug {ref}" in ergebnis.stdout


# --- Befund 8: ungetrackte Arbeitsdateien -----------------------------------------


def test_ungetrackte_arbeitsdatei_weigert(welt: dict[str, Path]) -> None:
    (welt["wt"] / "neu.py").write_text("y = 1\n", encoding="utf-8")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _remote_sha(welt) == ""
    assert _ssh_aufrufe(welt) == []


def test_gitignorierte_datei_zaehlt_nicht(welt: dict[str, Path]) -> None:
    gemeinsam = Path(
        _git(welt["wt"], "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    exclude = gemeinsam / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write("*.log\n")
    (welt["wt"] / "lauf.log").write_text("x\n", encoding="utf-8")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == 0


# --- Befund 9: ls-remote-Fehler ehrlich melden ---------------------------------


def test_ls_remote_fehler_wird_gemeldet(
    welt: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    _git(welt["wt"], "remote", "set-url", "--push", "origin", str(welt["origin"]))
    _git(welt["wt"], "remote", "set-url", "origin", str(welt["tmp"] / "weg.git"))
    pfad = _handoff(welt["wt"])
    with caplog.at_level("ERROR"):
        assert _umzug(welt, pfad) == umzug.EXIT_FEHLER
    assert "ls-remote scheiterte" in caplog.text
    assert _ssh_aufrufe(welt) == []


# --- Befund 10: Ergebnis der Session an den Wächter --------------------------------


def test_umzug_einzel_schreibt_ergebnis_in_laeuft_datei(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    anfrage = welt["tmp"] / "umzug-anfrage-9121"
    laeuft = Path(f"{anfrage}.laeuft")
    laeuft.write_text("2026-09-18T20:00:00+02:00", encoding="utf-8")
    monkeypatch.setenv("BAU_UMZUG_ANFRAGE", str(anfrage))
    monkeypatch.setenv("SSH_STUB_MODUS", "fehler")
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == umzug.EXIT_FEHLER
    daten = json.loads(laeuft.read_text(encoding="utf-8"))
    assert daten["exit"] == umzug.EXIT_FEHLER
    assert "Server-Start" in daten["grund"]


def test_umzug_einzel_ohne_laeuft_datei_legt_keine_an(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    anfrage = welt["tmp"] / "umzug-anfrage-9121"
    monkeypatch.setenv("BAU_UMZUG_ANFRAGE", str(anfrage))
    pfad = _handoff(welt["wt"])
    assert _umzug(welt, pfad) == 0
    assert not Path(f"{anfrage}.laeuft").exists()


def test_umzug_alle_stoppt_sofort_bei_fehlschlag_der_session(
    alle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    w = alle({"9121": "läuft seit 10:00", "9122": "wartet"}, zieht_um=False)
    vorher = umzug.anfrage_schreiben

    def anfrage(repo: Path, ticket: str) -> Path:
        pfad = vorher(repo, ticket)
        laeuft = Path(f"{pfad}.laeuft")
        pfad.rename(laeuft)  # wie der Stop-Hook
        laeuft.write_text(
            json.dumps({"exit": 1, "grund": "Server-Start scheiterte — lokale Session läuft weiter."}),
            encoding="utf-8",
        )
        return pfad

    monkeypatch.setattr(umzug, "anfrage_schreiben", anfrage)
    start = time.monotonic()
    assert _alle(tmp_path, warte_max=3) == umzug.EXIT_FEHLER
    assert time.monotonic() - start < 2
    ausgabe = capsys.readouterr().out
    assert "Server-Start scheiterte" in ausgabe
    assert "Stopp bei #9121" in ausgabe
    assert w.ereignisse == ["anfrage 9121"]
    assert not (tmp_path / ".to-spawn" / "umzug-anfrage-9121.laeuft").exists()


# --- Befund 11: Pfade in der Hook-Anweisung gequotet ---------------------------------


def test_hook_anweisung_quotet_pfade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    anfrage = tmp_path / "umzug-anfrage-9121"
    anfrage.write_text("1", encoding="utf-8")
    monkeypatch.setenv("BAU_UMZUG_ANFRAGE", str(anfrage))
    monkeypatch.setenv("BAU_TICKET", "9121")
    monkeypatch.setattr(umzug.sys, "executable", "C:/Program Files/Python312/python.exe")
    monkeypatch.setattr(umzug, "SKILL", Path("/opt/mein skill"))
    assert umzug.hook_umzug_anfrage("{}") == 0
    grund = json.loads(capsys.readouterr().out)["reason"]
    assert (
        "'C:/Program Files/Python312/python.exe' '/opt/mein skill/to_spawn.py' umzug 9121"
        in grund
    )


# --- Befund 12: shell=True-Notnagel nur, wenn das Programm fehlt ---------------------


def test_start_notnagel_nur_bei_datei_fehlt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aufrufe: list[bool] = []
    echt = subprocess.Popen

    def popen(cmd: object, *args: object, **kw: object) -> object:
        aufrufe.append(bool(kw.get("shell")))
        if not kw.get("shell"):
            raise FileNotFoundError(2, "claude nicht gefunden")
        return echt([sys.executable, "-c", "pass"])

    monkeypatch.setattr(umzug.subprocess, "Popen", popen)
    code, daten = umzug.starte_mit_umzug_wache(["claude", "x"], tmp_path / "u.json", takt=0.05)
    assert aufrufe == [False, True]
    assert code == 0 and daten is None


def test_start_andere_oserror_wird_geworfen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    aufrufe: list[bool] = []
    echt = subprocess.Popen

    def popen(cmd: object, *args: object, **kw: object) -> object:
        aufrufe.append(bool(kw.get("shell")))
        if not kw.get("shell"):
            raise PermissionError(13, "Zugriff verweigert")
        return echt([sys.executable, "-c", "pass"])

    monkeypatch.setattr(umzug.subprocess, "Popen", popen)
    with caplog.at_level("ERROR"), pytest.raises(PermissionError):
        umzug.starte_mit_umzug_wache(["claude", "x"], tmp_path / "u.json", takt=0.05)
    assert aufrufe == [False]
    assert "Zugriff verweigert" in caplog.text


# --- Befund 13: Pflichtzeile als Markdown-Überschrift ------------------------------


@pytest.mark.parametrize("zeile", ["## Umzug: server", "# Umzug: server", "### **Umzug:** server"])
def test_umzug_zeile_als_ueberschrift_gilt(welt: dict[str, Path], zeile: str) -> None:
    pfad = _handoff(welt["wt"], f"# Handoff #9121\n\nStand: Hälfte gebaut.\n\n{zeile}\n")
    assert _umzug(welt, pfad) == 0
    assert Path(os.environ["BAU_UMZUG_DATEI"]).is_file()


def test_umzug_nur_im_fliesstext_bleibt_weigerung(welt: dict[str, Path]) -> None:
    pfad = _handoff(welt["wt"], "# Handoff\n\nDer Umzug: server kommt später.\n")
    assert _umzug(welt, pfad) == umzug.EXIT_WEIGERUNG
    assert _ssh_aufrufe(welt) == []
