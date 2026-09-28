"""Ticket #316: Wächter-Takt — Prüf-Skript alle 5 Minuten, Claude nur bei Bedarf.

Weg-Tests ohne Attrappen: echter Unterprozess ``python to_spawn.py takt …``,
echter Leitstand in ``tmp_path``, echtes Git-Repo mit echten Bau-Logs von Spec
#293 (``tests/hilfen/takt_316/bau_log``, Entscheidungs-Zeilen + letzte
``session_ende`` je Ticket). Externe Dienste nur als echte Aufzeichnung:
GitHub-Unter-Tickets von #293 einmal echt mit ``gh`` geholt
(``tests/hilfen/takt_316/sub_issues_293.json``), Claude = Aufzeichnungs-Skript.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
HILFEN = SKILL / "tests" / "hilfen"
FIXTURE = HILFEN / "takt_316"
CLAUDE_AUFZ = HILFEN / "claude_aufzeichnung_316.py"
PY = sys.executable


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def welt(tmp_path: Path) -> dict[str, Any]:
    """Echtes Repo mit #293-Bau-Logs auf ``origin/master`` + Leitstand in tmp."""
    repo = tmp_path / "repo"
    ziel = repo / "docs" / "agents" / "bau_log"
    ziel.mkdir(parents=True)
    for datei in (FIXTURE / "bau_log").glob("*.jsonl"):
        shutil.copy(datei, ziel / datei.name)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}),
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "bau-logs #293")
    # Nicht erreichbarer origin: fetch scheitert schnell, Name liefert owner/name.
    fern = tmp_path / "fern" / "Shavy72" / "duoplus-management.git"
    _git(repo, "remote", "add", "origin", fern.as_posix())
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_LEITSTAND_ORDNER": str(tmp_path / "leitstand"),
            "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "waechter"),
            "TO_SPAWN_GH_STUB": str(HILFEN / "gh_aufzeichnung_316.py"),
            "GH_AUFZEICHNUNG_ORDNER": str(FIXTURE),
            "CLAUDE_AUFZEICHNUNG": str(tmp_path / "claude_aufruf.jsonl"),
            "CLAUDE_AUFZEICHNUNG_SPEC": "293",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return {"repo": repo, "env": env, "tmp": tmp_path}


def _takt(
    welt: dict[str, Any], *extra: str, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            PY,
            str(SKILL / "to_spawn.py"),
            "takt",
            "293",
            "--repo-dir",
            str(welt["repo"]),
            *extra,
        ],
        cwd=str(SKILL),
        env=welt["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _aufrufe(welt: dict[str, Any]) -> list[dict[str, Any]]:
    datei = Path(welt["env"]["CLAUDE_AUFZEICHNUNG"])
    if not datei.exists():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _merker(welt: dict[str, Any]) -> dict[str, Any] | None:
    datei = Path(welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"]) / "zustand.json"
    if not datei.exists():
        return None
    try:
        return json.loads(datei.read_text(encoding="utf-8")).get("takt", {}).get("293")
    except ValueError:
        return None


def _alt_merker(welt: dict[str, Any], merker: dict[str, Any] | None = None) -> None:
    """Merker aus einem früheren Takt (vorhanden, Vorgabe leer) in den Leitstand legen."""
    ordner = Path(welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"])
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "zustand.json").write_text(
        json.dumps({"takt": {"293": merker or {}}}), encoding="utf-8"
    )


def _frage(welt: dict[str, Any], ticket: int, *texte: str, typ: str = "frage") -> None:
    """Neue Bau-Log-Zeilen einer Bau-Session (Laufdatei, wie sie die Hooks schreiben)."""
    lauf = welt["repo"] / ".to-spawn" / "bau_log" / f"{ticket}.jsonl"
    lauf.parent.mkdir(parents=True, exist_ok=True)
    with lauf.open("a", encoding="utf-8") as fh:
        for text in texte:
            zeile = {
                "ts": "2026-09-24T20:00:00+02:00",
                "typ": typ,
                "ticket": str(ticket),
                "text": text,
            }
            fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def test_a_dry_run_293_nennt_entscheidungen(welt: dict[str, Any]) -> None:
    # Fixrunde (Befund 7): ohne Merker ist alles Ausgangsstand — Alt-Merker, leer.
    _alt_merker(welt)
    lauf = _takt(welt, "--dry-run")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    aus = lauf.stdout
    assert "Takt #293" in aus
    # Echte Stände: 301 hat eine entscheidung-Zeile (geschlossene = Ausgangsstand).
    assert "#301" in aus and "entscheidung" in aus, aus
    assert not _aufrufe(welt), "Probelauf darf kein Claude starten"
    assert _merker(welt) == {}, "Probelauf darf keinen Merker schreiben"


def test_b_leerer_takt_startet_kein_claude(welt: dict[str, Any]) -> None:
    _alt_merker(welt)  # Fixrunde (Befund 7): sonst hat schon der erste Takt nichts
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    assert len(_aufrufe(welt)) == 1
    Path(welt["env"]["CLAUDE_AUFZEICHNUNG"]).unlink()
    zweiter = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    assert "nichts zu entscheiden" in zweiter.stdout
    assert not Path(welt["env"]["CLAUDE_AUFZEICHNUNG"]).exists(), (
        "leerer Takt startete Claude"
    )


def test_c_zweiter_takt_steigt_sofort_aus(welt: dict[str, Any]) -> None:
    los = welt["tmp"] / "los"
    halter = subprocess.Popen(
        [
            PY,
            "-m",
            "to_spawn.leitstand",
            "halte",
            "takt-293",
            "erster-takt",
            "--bis",
            str(los),
        ],
        cwd=str(SKILL),
        env=welt["env"],
    )
    try:
        ende = time.monotonic() + 20
        while not Path(f"{los}.hat").exists() and time.monotonic() < ende:
            time.sleep(0.05)
        assert Path(f"{los}.hat").exists(), "Halte-Prozess hält die Sperre nicht"
        beginn = time.monotonic()
        lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ), timeout=30)
        dauer = time.monotonic() - beginn
        assert lauf.returncode == 0, lauf.stdout + lauf.stderr
        assert "Takt #293 läuft schon" in lauf.stdout
        assert dauer < 5, f"Aussteigen dauerte {dauer:.1f} s"
        assert not _aufrufe(welt)
    finally:
        los.touch()
        halter.wait(timeout=20)


def test_d_hook_stop_startet_takt(welt: dict[str, Any]) -> None:
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    vorher = (_merker(welt) or {}).get("zuletzt")
    assert vorher
    time.sleep(1.1)  # Merker-Zeit hat Sekunden-Auflösung
    env = dict(welt["env"])
    env.update(
        {
            "TO_SPAWN_LOG_REPO": str(welt["repo"]),
            "TO_SPAWN_REPO": str(welt["repo"]),
            "TO_SPAWN_TICKET": "296",
            "TO_SPAWN_SPEC": "293",
            "TO_SPAWN_CLAUDE": str(CLAUDE_AUFZ),
        }
    )
    eingabe = json.dumps(
        {
            "session_id": "sess-316",
            "transcript_path": "",
            "last_assistant_message": "fertig",
        }
    )
    hook = subprocess.run(
        [PY, str(SKILL / "to_spawn.py"), "hook-stop"],
        cwd=str(welt["repo"]),
        env=env,
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert hook.returncode == 0, hook.stderr
    logdatei = Path(env["TO_SPAWN_LEITSTAND_ORDNER"]) / "takt" / "293.log"
    ende = time.monotonic() + 60
    while time.monotonic() < ende:
        merker = _merker(welt) or {}
        if merker.get("zuletzt") and merker["zuletzt"] != vorher:
            break
        time.sleep(0.2)
    assert (_merker(welt) or {}).get("zuletzt") != vorher, (
        "Takt nach Hook-Stop nicht gelaufen"
    )
    assert logdatei.exists()
    assert "Takt #293" in logdatei.read_text(encoding="utf-8")


def test_e_notizzettel_wandert_in_den_naechsten_prompt(welt: dict[str, Any]) -> None:
    _alt_merker(welt)  # Fixrunde (Befund 7): sonst startet der erste Takt kein Claude
    welt["env"]["CLAUDE_AUFZEICHNUNG_NOTIZ"] = "Notiz aus Takt 1: #301 wartet auf David"
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    del welt["env"]["CLAUDE_AUFZEICHNUNG_NOTIZ"]
    # Neue Rückfrage einer Bau-Session (Laufdatei, wie sie die Hooks schreiben).
    lauf = welt["repo"] / ".to-spawn" / "bau_log" / "297.jsonl"
    lauf.parent.mkdir(parents=True, exist_ok=True)
    zeile = {
        "ts": "2026-09-24T20:00:00+02:00",
        "typ": "frage",
        "ticket": "297",
        "text": "Welche Musik-Länge?",
    }
    lauf.write_text(json.dumps(zeile, ensure_ascii=False) + "\n", encoding="utf-8")
    zweiter = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2
    argv = aufrufe[1]["argv"]
    # Fixrunde (Befund 3): Prompt kommt über stdin, nicht mehr als Argument.
    assert argv[-1] == "-p", argv
    prompt = aufrufe[1]["stdin"]
    assert "Notiz aus Takt 1: #301 wartet auf David" in prompt
    assert "#297" in prompt and "Welche Musik-Länge?" in prompt
    assert "#296" not in prompt, "alte Entscheidungen gehören nicht in den zweiten Takt"
    assert not prompt.lstrip().startswith("/loop")
    assert "HANDOFF_" not in prompt and "ScheduleWakeup {" not in prompt
    assert "kein Handoff" in prompt and "kein /loop" in prompt
    assert "takt 293 --notiz" in prompt


def test_f_takt_einrichten_trocken_idempotent(welt: dict[str, Any]) -> None:
    from to_spawn import waechter_takt

    zeilen = ["0 3 * * * echo fremd"]
    einmal = waechter_takt.cron_zeilen(zeilen, 293, "*/5 * * * * takt 293")
    zweimal = waechter_takt.cron_zeilen(einmal, 293, "*/5 * * * * takt 293")
    assert einmal == zweimal
    assert sum(waechter_takt.cron_marke(293) in z for z in zweimal) == 1
    assert "0 3 * * * echo fremd" in zweimal
    if sys.platform == "win32" or shutil.which("crontab") is None:
        pytest.skip("takt-einrichten nur auf dem Linux-Bau-Server")
    for _ in range(2):
        lauf = subprocess.run(
            [
                PY,
                str(SKILL / "to_spawn.py"),
                "takt-einrichten",
                "293",
                "--trocken",
                "--repo-dir",
                str(welt["repo"]),
            ],
            cwd=str(SKILL),
            env=welt["env"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert lauf.returncode == 0, lauf.stdout + lauf.stderr
        treffer = [
            z for z in lauf.stdout.splitlines() if waechter_takt.cron_marke(293) in z
        ]
        assert len(treffer) == 1, lauf.stdout


def test_g_erster_takt_geschlossene_sind_ausgangsstand(welt: dict[str, Any]) -> None:
    """Fixrunde (Befund 7): ohne Merker ist alles Ausgangsstand — nur offene Fragen zählen."""
    probe = _takt(welt, "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "neu geschlossen" not in probe.stdout, probe.stdout
    assert "0 zu entscheiden" in probe.stdout, probe.stdout
    _frage(welt, 297, "Welche Musik-Länge?")  # offen: Frage geht nie verloren
    _frage(welt, 296, "Alte Frage am geschlossenen Ticket")
    _frage(welt, 297, "Schon entschieden", typ="entscheidung")
    erster = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert erster.returncode == 0, erster.stdout + erster.stderr
    prompt = _aufrufe(welt)[0]["stdin"]
    assert "Welche Musik-Länge?" in prompt
    assert "neu geschlossen" not in prompt and "#301" not in prompt
    assert "Alte Frage" not in prompt and "Schon entschieden" not in prompt
    assert sorted((_merker(welt) or {}).get("zu") or []) == [296, 298, 299, 300]


# --- Fixrunde Prüfpanel -------------------------------------------------------


def test_h_capo_abbruch_ohne_hauptzweig_ist_fehler(welt: dict[str, Any]) -> None:
    """Befund 1: fehlt origin/master, bricht capo früh ab — Takt meldet Fehler, Merker bleibt."""
    alt = {"zeilen": ["x"], "zu": [296], "verstoesse": [], "fertig": False}
    _alt_merker(welt, alt)
    _git(welt["repo"], "update-ref", "-d", "refs/remotes/origin/master")
    lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "FEHLER" in lauf.stdout and "nichts zu entscheiden" not in lauf.stdout
    assert _merker(welt) == alt
    assert not _aufrufe(welt)


def test_i_gekappte_eintraege_gehen_nicht_verloren(welt: dict[str, Any]) -> None:
    """Befund 2: nur gezeigte Einträge gelten als gesehen, der Rest kommt im nächsten Takt."""
    _alt_merker(welt)
    _frage(welt, 297, *[f"Frage Nummer {i:02d}" for i in range(35)])
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    zweiter = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, "Rest ab Eintrag 31 wurde verschluckt"
    # Jede Frage genau einmal: erster Takt die ersten 30 Einträge, zweiter den Rest.
    for i in range(35):
        treffer = [a for a in aufrufe if f"Frage Nummer {i:02d}" in a["stdin"]]
        assert len(treffer) == 1, f"Frage {i:02d} in {len(treffer)} Takten"
    assert "Frage Nummer 34" in aufrufe[1]["stdin"] and "#301" in aufrufe[1]["stdin"]
    assert "nichts zu entscheiden" in _takt(welt, "--claude", str(CLAUDE_AUFZ)).stdout


def test_j_prompt_ueber_stdin_mit_sonderzeichen(welt: dict[str, Any]) -> None:
    """Befund 3 + 5 + 11: Prompt über stdin unverändert, keine Ticket-Variablen, Notiz-Warnung."""
    _alt_merker(welt)
    boese = 'Ist <text> & | "zitiert" ^ %PATH% richtig?'
    _frage(welt, 297, boese)
    welt["env"].update(
        {
            "TO_SPAWN_TICKET": "296",
            "TO_SPAWN_SPEC": "293",
            "TO_SPAWN_LOG_REPO": str(welt["repo"]),
        }
    )
    lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    aufruf = _aufrufe(welt)[0]
    assert aufruf["argv"][-1] == "-p", aufruf["argv"]
    assert not any(boese in a for a in aufruf["argv"])
    assert boese in aufruf["stdin"]
    assert aufruf["env"] == {
        "TO_SPAWN_TICKET": None,
        "TO_SPAWN_SPEC": None,
        "TO_SPAWN_LOG_REPO": None,
    }
    assert "Notizzettel" in lauf.stdout and "nicht fortgeschrieben" in lauf.stdout


def test_k_haengender_claude_endet_nach_timeout(welt: dict[str, Any]) -> None:
    """Befund 4: hängender Claude wird beendet, Exit 1, Merker bleibt, Sperre wieder frei."""
    _alt_merker(welt)
    welt["env"].update(
        {"CLAUDE_AUFZEICHNUNG_SCHLAF": "60", "TO_SPAWN_TAKT_TIMEOUT_S": "2"}
    )
    beginn = time.monotonic()
    lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ), timeout=50)
    assert time.monotonic() - beginn < 40
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "Zeitlimit" in lauf.stdout
    assert _merker(welt) == {}
    del welt["env"]["CLAUDE_AUFZEICHNUNG_SCHLAF"]
    nochmal = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert "läuft schon" not in nochmal.stdout


def _hook_stop(welt: dict[str, Any], sid: str) -> subprocess.CompletedProcess[str]:
    env = dict(welt["env"])
    env.update(
        {
            "TO_SPAWN_LOG_REPO": str(welt["repo"]),
            "TO_SPAWN_REPO": str(welt["repo"]),
            "TO_SPAWN_TICKET": "296",
            "TO_SPAWN_SPEC": "293",
            "TO_SPAWN_CLAUDE": str(CLAUDE_AUFZ),
        }
    )
    eingabe = json.dumps({"session_id": sid, "transcript_path": ""})
    return subprocess.run(
        [PY, str(SKILL / "to_spawn.py"), "hook-stop"],
        cwd=str(welt["repo"]),
        env=env,
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_l_zwei_stop_hooks_hoechstens_ein_takt(welt: dict[str, Any]) -> None:
    """Befund 6: Stop feuert je Runde — Mindestabstand je Spec, höchstens ein Takt-Start."""
    log = Path(welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"]) / "takt" / "293.log"
    assert _hook_stop(welt, "sess-a").returncode == 0
    # Erst den ersten Takt enden lassen: parallele Kinder überschreiben sich unter
    # Windows gegenseitig im Log (kein echtes Anhängen) — die Zählung wäre blind.
    ende = time.monotonic() + 60
    while (
        "zu entscheiden"
        not in (log.read_text(encoding="utf-8") if log.exists() else "")
        and time.monotonic() < ende
    ):
        time.sleep(0.2)
    time.sleep(1)
    assert _hook_stop(welt, "sess-a").returncode == 0
    time.sleep(8)
    assert log.read_text(encoding="utf-8").count("ausgelöst") == 1, log.read_text(
        encoding="utf-8"
    )


def test_m_claude_nicht_startbar_ist_fehler(welt: dict[str, Any]) -> None:
    """Befund 8: Startfehler (OSError) → Exit 1, Log-Zeile, Merker bleibt."""
    _alt_merker(welt)
    fehlt = welt["tmp"] / "gibt-es-nicht" / "claude"
    lauf = _takt(welt, "--claude", str(fehlt))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "FEHLER" in lauf.stdout and "Traceback" not in lauf.stderr
    assert _merker(welt) == {}


def test_n_cron_zeile_mit_claude_pfad_und_quoting() -> None:
    """Befund 8: absoluter Claude-Pfad in der Cron-Zeile, alle Pfade gequotet."""
    from to_spawn import waechter_takt

    zeile = waechter_takt.cron_befehl(
        293, Path("/srv/mein repo"), "/opt/claude bin/claude", Path("/l s/293.log")
    )
    assert "TO_SPAWN_CLAUDE='/opt/claude bin/claude' " in zeile
    assert f"--repo-dir {shlex.quote(str(Path('/srv/mein repo')))} " in zeile
    assert f">> {shlex.quote(str(Path('/l s/293.log')))} 2>&1" in zeile
    assert zeile.startswith(waechter_takt.CRON_TAKT + " ")


def test_o_cmd_shim_startet_ohne_shell(tmp_path: Path) -> None:
    """Befund 3: ein .cmd-Shim wird auf node + cli.js aufgelöst (kein cmd.exe)."""
    from to_spawn import waechter_takt

    if sys.platform != "win32" or shutil.which("node") is None:
        pytest.skip("nur Windows mit node")
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    cli = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    assert waechter_takt.claude_start(str(shim)) == [shutil.which("node"), str(cli)]


def test_p_leitstand_kaputt_ist_fehler(welt: dict[str, Any]) -> None:
    """Befund 12: Sperre nicht prüfbar (OSError) → FEHLER-Zeile, Exit 1, kein Traceback."""
    kaputt = welt["tmp"] / "leitstand-datei"
    kaputt.write_text("kein Ordner", encoding="utf-8")
    welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"] = str(kaputt)
    lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ), timeout=60)
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "FEHLER" in lauf.stdout and "Traceback" not in lauf.stderr
    assert not _aufrufe(welt)
