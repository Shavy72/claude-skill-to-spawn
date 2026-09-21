"""Fixrunde 2 #236 — Befunde aus dem Code-Review (R1–R7) als Weg-Tests.

Gleiche Welt wie ``test_aufpasser_236_fix.py`` (echtes tmux auf eigenem Socket,
echtes git, Temp-HOME); Fixtures und Helfer werden von dort importiert, nicht
kopiert. Die echten Sitzungen des Bau-Servers (``spec-*``) und
``~/.claude/sessions/`` werden nie berührt.
"""

# ruff: noqa: F811 — ``welt``/``repo_bau``/``_ohne_tmux_pane`` kommen als Fixtures aus den Erst-Tests.
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt``/``repo_bau`` sind Fixtures
    FAKE_CLAUDE_ARGV,
    SITZUNG,
    SKILL,
    SKRIPTE,
    SOCKET,
    SPEC,
    Welt,
    _bau,
    _origin_hat_branch,
    fenster_namen,
    fenster_ziel,
    git,
    pane_text,
    repo_bau,
    tmux,
    welt,
    welt_pane_pid,
)
from test_aufpasser_236_fix import (  # noqa: F401 — ``_ohne_tmux_pane`` ist ein Fixture
    BAU_CLAUDE,
    BAU_SOFORT_WEG,
    _bau_claude_vorlage,
    _fenster,
    _fenster_id,
    _ohne_tmux_pane,
    _still_seit,
)

from to_spawn import aufpasser, sessions_datei, waechter_lauf
from to_spawn.waechter_lauf import transkript_ordner

WACHE = f"wache {SPEC}"


def _wache_vorlage(welt: Welt, skript: str = BAU_CLAUDE) -> str:
    """Start-Attrappe für das Wächter-Fenster (``{s}`` statt ``{n}``)."""
    datei = welt.bin / "wache_claude.sh"
    datei.write_text(
        skript.format(protokoll=welt.protokoll_bau, claude=welt.bin / "claude"),
        encoding="utf-8",
    )
    datei.chmod(0o755)
    return f"{datei} {{s}}{{resume}}"


def _wache_protokoll_vorlage(welt: Welt) -> str:
    """Protokoll-Skript (schreibt argv, schläft) als Wächter-Start."""
    return f"{welt.bin}/bau_protokoll.sh {{s}}{{resume}}"


def _transkript_anlegen(welt: Welt, cwd: Path, sid: str) -> Path:
    ordner = transkript_ordner(cwd, welt.heim)
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / f"{sid}.jsonl"
    datei.write_text("", encoding="utf-8")
    return datei


def _argv_protokoll(welt: Welt) -> list[str]:
    if not welt.protokoll_bau.is_file():
        return []
    return welt.protokoll_bau.read_text(encoding="utf-8").splitlines()


def _einstellungen(welt: Welt, **extra: object) -> aufpasser.Einstellungen:
    felder: dict[str, object] = {
        "zustand": welt.zustand,
        "tmux_socket": SOCKET,
        "hang_min": 90,
        "bau_vorlage": welt.vorlage,
        "deploy_muster": f"aufpasser-probe-niemals-{os.getpid()}",
    }
    felder.update(extra)
    return aufpasser.Einstellungen(**felder)  # type: ignore[arg-type]


# --- R1: Wächter-Fenster bekommt die Sicherheitskette -------------------------


def test_r1_wache_fenster_wird_fortgesetzt(welt: Welt) -> None:
    """Stufe 1 + still: das Wächter-Fenster wird (ohne Sicherung — kein Worktree)
    per Respawn mit ``--resume <gleiche ID>`` fortgesetzt, Nachweis grün, Stufe 2."""
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", "sleep 3600")
    tmux("kill-window", "-t", fenster_ziel(WACHE))
    _fenster(welt, WACHE, f"{welt.bin}/claude --session-id {sid} x")
    wid, pid = _fenster_id(WACHE)
    alt_pane_pid = welt_pane_pid(WACHE)
    _still_seit(welt, WACHE, 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf("--wache-vorlage", _wache_vorlage(welt)) == 0
    assert fenster_namen().count(WACHE) == 1
    assert _fenster_id(WACHE) == (wid, pid), "Fenster/Pane müssen bleiben"
    neu_pane_pid = welt_pane_pid(WACHE)
    assert neu_pane_pid != alt_pane_pid
    assert _argv_protokoll(welt) == [f"{SPEC} --resume {sid}"]
    info = aufpasser.session_finden(neu_pane_pid, wid, pid, welt.heim)
    assert info and info.aus_json and info.session_id == sid
    assert welt.eintrag(WACHE)["stufe"] == 2
    assert any("fortgesetzt" in k and sid[:8] in k for k in welt.kommentare())
    assert not _origin_hat_branch(welt, 9001), "Wächter hat keinen Worktree"


def test_r1_wache_resume_ohne_transkript_bricht_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wache.py <S> --resume abc --dry-run`` zeigt ``--resume abc`` und keine
    ``--session-id``; ``waechter_lauf.fahre(session_id=…)`` ohne Transkript endet
    mit 2 und startet nie eine Session."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    git(arbeit, "init", "-q")
    heim = tmp_path / "heim"
    (heim / ".claude").mkdir(parents=True)
    echte_plugins = Path.home() / ".claude" / "plugins"
    if echte_plugins.exists():
        (heim / ".claude" / "plugins").symlink_to(echte_plugins)
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    umgebung["HOME"] = str(heim)
    ergebnis = subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "wache.py"),
            "9001",
            "--resume",
            "abc",
            "--dry-run",
        ],
        cwd=str(arbeit),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "--resume abc" in ergebnis.stdout
    assert "--session-id" not in ergebnis.stdout

    marke = tmp_path / "gestartet.txt"
    attrappe = tmp_path / "claude_marke.sh"
    attrappe.write_text(f"#!/bin/bash\ntouch {marke}\n", encoding="utf-8")
    attrappe.chmod(0o755)
    monkeypatch.setenv("HOME", str(heim))
    code = waechter_lauf.fahre(
        claude=str(attrappe),
        spec=9001,
        prompt="x",
        modell="m",
        ausweich="",
        remote_control=False,
        repo=arbeit,
        cwd=arbeit,
        session_id="abc",
    )
    assert code == 2
    assert not marke.exists(), "ohne Transkript darf nie eine Session starten"


# --- R2: Gesprächs-ID überlebt das Fenster -------------------------------------


def test_r2_fehlendes_fenster_startet_mit_resume_wenn_id_datei_liegt(
    welt: Welt,
) -> None:
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    assert welt.lauf() == 0
    assert "bau 9002" in fenster_namen()
    assert _argv_protokoll(welt) == [f"9002 --resume {sid}"]
    treffer = [
        k
        for k in welt.kommentare()
        if f"#9002 hatte keine Session, mit Gespräch {sid[:8]}… fortgesetzt." in k
    ]
    assert len(treffer) == 1, welt.kommentare()


def test_r2_ohne_transkript_frischer_start(welt: Welt) -> None:
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    assert welt.lauf() == 0
    assert _argv_protokoll(welt) == ["9002"]
    assert any("#9002 hatte keine Session, gestartet." in k for k in welt.kommentare())


def test_r2_fehlendes_wache_fenster_wird_gestartet(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "sleep 3600")
    tmux("kill-window", "-t", fenster_ziel(WACHE))
    assert WACHE not in fenster_namen()
    assert welt.lauf("--wache-vorlage", _wache_protokoll_vorlage(welt)) == 0
    assert WACHE in fenster_namen()
    assert _argv_protokoll(welt) == [SPEC]
    assert any("Wächter-Fenster fehlte, gestartet." in k for k in welt.kommentare())


def test_r2_fehlendes_wache_fenster_mit_resume(welt: Welt) -> None:
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, f"wache-{SPEC}", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    _fenster(welt, "bau 9002", "sleep 3600")
    tmux("kill-window", "-t", fenster_ziel(WACHE))
    assert welt.lauf("--wache-vorlage", _wache_protokoll_vorlage(welt)) == 0
    assert WACHE in fenster_namen()
    assert _argv_protokoll(welt) == [f"{SPEC} --resume {sid}"]
    assert any(
        f"Wächter-Fenster fehlte, mit Gespräch {sid[:8]}… fortgesetzt." in k
        for k in welt.kommentare()
    )


def test_r2_pane_nur_shell_kein_send_keys_sondern_fortsetzen(welt: Welt) -> None:
    """Nur noch eine Shell im Pane (Session beendet), Ticket offen, ≥ hang_min
    still: kein Anstupsen (Text liefe als Befehl), sondern sichern + Fortsetzen
    mit der ID aus ``.to-spawn/sessions/<N>.json``."""
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    _fenster(welt, "bau 9002", "bash --norc --noprofile")
    time.sleep(0.5)
    wid, pid = _fenster_id("bau 9002")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    assert _fenster_id("bau 9002") == (wid, pid)
    assert _argv_protokoll(welt) == [f"9002 --resume {sid}"]
    assert not any("angestupst" in k for k in welt.kommentare())
    assert _origin_hat_branch(welt, 9002)
    assert welt.eintrag("bau 9002")["stufe"] == 2
    assert any("fortgesetzt" in k and sid[:8] in k for k in welt.kommentare())


def test_r2_pane_nur_shell_ohne_id_datei_braucht_david(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "bash --norc --noprofile")
    time.sleep(0.5)
    text_vorher = pane_text("bau 9002")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf() == 0
    assert pane_text("bau 9002") == text_vorher, "nichts getippt"
    assert _argv_protokoll(welt) == []
    assert welt.eintrag("bau 9002")["stufe"] == 3
    treffer = [
        k
        for k in welt.kommentare()
        if "Session beendet, keine Gesprächs-ID — braucht David" in k
    ]
    assert len(treffer) == 1, welt.kommentare()


def test_r2_bau_schreibt_sessions_datei(repo_bau: Path, tmp_path: Path) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "claude").write_text(FAKE_CLAUDE_ARGV, encoding="utf-8")
    (binaer / "claude").chmod(0o755)
    beweis = tmp_path / "argv.json"
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = {
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ARGV": str(beweis),
        "TMPDIR": str(temp),
    }
    ergebnis = _bau(repo_bau, "--sofort", env=env)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    argv = json.loads(beweis.read_text(encoding="utf-8"))
    sid = argv[argv.index("--session-id") + 1]
    datei = repo_bau / ".to-spawn" / "sessions" / "901.json"
    assert datei.is_file(), "bau.py muss .to-spawn/sessions/<N>.json schreiben"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    assert daten["session_id"] == sid
    assert Path(daten["cwd"]).resolve() == repo_bau.resolve()
    assert daten["runde"] == 1 and daten["zeit"]


def test_r2_wache_schreibt_sessions_datei(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``waechter_lauf.fahre`` legt ``.to-spawn/sessions/wache-<S>.json`` an."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    attrappe = tmp_path / "claude_kurz.sh"
    attrappe.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    attrappe.chmod(0o755)
    code = waechter_lauf.fahre(
        claude=str(attrappe),
        spec=9001,
        prompt="x",
        modell="m",
        ausweich="",
        remote_control=False,
        repo=arbeit,
        cwd=arbeit,
        takt=0.2,
    )
    assert code == 0
    daten = sessions_datei.lesen(arbeit, "wache-9001")
    assert daten and uuid.UUID(daten["session_id"])
    assert Path(daten["cwd"]) == arbeit


# --- R3: jeder Eingriff = eine Issue-Zeile --------------------------------------


def test_r3_zwei_starts_am_selben_tag_zwei_kommentare(welt: Welt) -> None:
    assert welt.lauf() == 0
    assert "bau 9002" in fenster_namen()
    tmux("kill-window", "-t", fenster_ziel("bau 9002"))
    time.sleep(0.5)
    assert welt.lauf() == 0
    treffer = [
        k for k in welt.kommentare() if "#9002 hatte keine Session, gestartet." in k
    ]
    assert len(treffer) == 2, welt.kommentare()


# --- R4: Deploy-Wache direkt vor dem Eingriff ----------------------------------


def test_r4_deploy_startet_waehrend_verzoegerung_kein_eingriff(welt: Welt) -> None:
    name = f"aufpasser-deploy-probe-{os.getpid()}.sh"
    skript = welt.bin / name
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    _fenster(welt, "bau 9002", "cat")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    gestartet: list[subprocess.Popen[bytes]] = []
    zuender = threading.Timer(
        1.5, lambda: gestartet.append(subprocess.Popen([str(skript)]))
    )
    zuender.start()
    try:
        e = _einstellungen(
            welt, verzoegerung_s=4.0, deploy_muster=name.replace(".", r"\.")
        )
        assert aufpasser.lauf_mit_einstellungen(e) == 0
    finally:
        zuender.cancel()
        for p in gestartet:
            p.kill()
            p.wait()
    assert "Aufpasser" not in pane_text("bau 9002")
    assert "Deploy/Gate inzwischen gestartet" in welt.log()
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0
    assert welt.kommentare() == []


# --- R5: Anstups-Kette begrenzen -----------------------------------------------


def test_r5_anstupsen_zaehlt_stupser(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "cat")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf() == 0
    assert "Aufpasser" in pane_text("bau 9002")
    assert len(welt.eintrag("bau 9002")["stupser"]) == 1


def test_r5_drei_stupser_in_24h_dann_braucht_david(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "cat")
    jetzt = time.time()
    _still_seit(
        welt,
        "bau 9002",
        100,
        stufe=0,
        stupser=[jetzt - 20 * 3600, jetzt - 8 * 3600, jetzt - 3 * 3600],
    )
    text_vorher = pane_text("bau 9002")
    assert welt.lauf() == 0
    assert pane_text("bau 9002") == text_vorher, "kein vierter Anstupser"
    assert welt.eintrag("bau 9002")["stufe"] == 3
    erwartet = (
        "„bau 9002“ wurde 3× angestupst und wird immer wieder still — braucht David."
    )
    assert [k for k in welt.kommentare() if erwartet in k], welt.kommentare()
    assert welt.lauf() == 0
    assert len([k for k in welt.kommentare() if erwartet in k]) == 1


def test_r5_alte_stupser_zaehlen_nicht(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "cat")
    jetzt = time.time()
    _still_seit(
        welt,
        "bau 9002",
        100,
        stufe=0,
        stupser=[jetzt - 30 * 3600, jetzt - 26 * 3600, jetzt - 25 * 3600],
    )
    assert welt.lauf() == 0
    assert "Aufpasser" in pane_text("bau 9002")
    assert welt.eintrag("bau 9002")["stufe"] == 1


# --- R6: Start-Nachweis ---------------------------------------------------------


def test_r6_start_attrappe_endet_sofort_start_fehlgeschlagen(welt: Welt) -> None:
    vorlage = _bau_claude_vorlage(welt, BAU_SOFORT_WEG)
    assert welt.lauf("--bau-vorlage", vorlage) == 0
    assert _argv_protokoll(welt) == ["9002"]
    kommentare = welt.kommentare()
    assert not any("gestartet." in k for k in kommentare), kommentare
    assert any(
        "#9002: Start fehlgeschlagen (Fenster gleich wieder weg)" in k
        for k in kommentare
    ), kommentare
    assert len(welt.stand()["starts"]["9002"]) == 1


# --- R7: Aufräumen --------------------------------------------------------------


def test_r7_helfer_braucht_david_und_doku_120_s() -> None:
    assert callable(getattr(aufpasser.Aufpasser, "_braucht_david", None))
    quelle = (SKILL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    assert quelle.count("stufe=3") <= 1, "Stufe 3 nur im Helfer setzen"
    for datei in (SKILL / "SKILL.md", SKILL / "to_spawn" / "README.md"):
        text = datei.read_text(encoding="utf-8")
        assert "120 s" in text, datei
        assert "binnen 20 s" not in text, datei
        assert "--resume" in text and "sessions/" in text, datei


# --- D: Doppel-Resume-Wache (#236 Nachprüfung) ---------------------------------
#
# ``session_laeuft_schon`` (aufpasser.py ~Z. 391) schützt gegen ein zweites
# ``--resume <id>`` auf ein Gespräch, das laut ``~/.claude/sessions/<pid>.json``
# schon woanders lebt (andere tmux-Sitzung, von Hand fortgesetzt). Der Guard sitzt
# an zwei Stellen: in ``resume_aus_datei`` (~Z. 990, für Fenster, die noch stehen)
# und in ``fehlendes_fenster`` (~Z. 1530, für ganz verschwundene Fenster).


def _fremde_session_lebt(welt: Welt, sid: str) -> subprocess.Popen:
    """Eigener ``sleep``-Prozess außerhalb von tmux, als lebende Fremd-Session in
    ``~/.claude/sessions/<pid>.json`` hinterlegt — simuliert ein Gespräch, das
    laut Session-JSON anderswo lebt (D1–D2)."""
    prozess = subprocess.Popen(["sleep", "3600"])
    ordner = welt.heim / ".claude" / "sessions"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"{prozess.pid}.json").write_text(
        json.dumps({"pid": prozess.pid, "sessionId": sid, "cwd": str(welt.repo)}),
        encoding="utf-8",
    )
    return prozess


def test_d1_fehlendes_fenster_kein_start_wenn_gespraech_woanders_lebt(
    welt: Welt,
) -> None:
    """``.to-spawn/sessions/9002.json`` + Transkript liegen, aber laut
    ``~/.claude/sessions/<pid>.json`` läuft das Gespräch schon anderswo (lebende
    PID): kein zweites Fenster ``bau 9002``, Start-Attrappe bleibt unangetastet."""
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    fremd = _fremde_session_lebt(welt, sid)
    try:
        assert welt.lauf() == 0
    finally:
        fremd.terminate()
        fremd.wait(timeout=5)
    assert "bau 9002" not in fenster_namen()
    assert _argv_protokoll(welt) == []
    assert "läuft außerhalb dieser Sitzung" in welt.log()


def test_d2_shell_pane_kein_fortsetzen_wenn_gespraech_woanders_lebt(
    welt: Welt,
) -> None:
    """Wie ``test_r2_pane_nur_shell_kein_send_keys_sondern_fortsetzen`` (Pane nur
    noch Shell, Ticket offen, still), aber das Gespräch lebt laut
    ``~/.claude/sessions/`` schon anderswo: kein Respawn (Fenster/Pane-ID und der
    alte Pane-Prozess bleiben), stattdessen dieselbe „braucht David“-Meldung wie
    ohne Gesprächs-ID, Stufe 3."""
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    _fenster(welt, "bau 9002", "bash --norc --noprofile")
    time.sleep(0.5)
    wid, pid = _fenster_id("bau 9002")
    alter_pane_pid = welt_pane_pid("bau 9002")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    fremd = _fremde_session_lebt(welt, sid)
    try:
        assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    finally:
        fremd.terminate()
        fremd.wait(timeout=5)
    assert _fenster_id("bau 9002") == (wid, pid), "kein Respawn"
    assert welt_pane_pid("bau 9002") == alter_pane_pid, "alter Pane-Prozess lebt weiter"
    assert _argv_protokoll(welt) == []
    assert not _origin_hat_branch(welt, 9002), "keine Sicherung ohne Fortsetzen"
    assert welt.eintrag("bau 9002")["stufe"] == 3
    treffer = [
        k
        for k in welt.kommentare()
        if "Session beendet, keine Gesprächs-ID — braucht David" in k
    ]
    assert len(treffer) == 1, welt.kommentare()


def test_d3_pane_mit_wartendem_python_ist_keine_beendete_session(
    welt: Welt,
) -> None:
    """Pane läuft ``python3 -c "import time; time.sleep(3600)"`` unter einer bash
    (kein claude, kein Shell-only-Pane): ``session_beendet`` liefert False, die
    Doppel-Resume-Wache greift also gar nicht — die Stille geht stattdessen in die
    normale Anstupsen-Kette (Stufe 0→1), kein Fortsetzen."""
    sid = str(uuid.uuid4())
    sessions_datei.schreiben(welt.repo, "9002", sid, welt.repo, 1)
    _transkript_anlegen(welt, welt.repo, sid)
    _fenster(welt, "bau 9002", "bash --norc --noprofile")
    time.sleep(0.5)
    ziel = fenster_ziel("bau 9002")
    tmux("send-keys", "-t", ziel, "-l", 'python3 -c "import time; time.sleep(3600)"')
    tmux("send-keys", "-t", ziel, "Enter")
    time.sleep(1)
    pane_pid = welt_pane_pid("bau 9002")
    assert aufpasser.session_beendet(pane_pid) is False
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf() == 0
    assert "Aufpasser" in pane_text("bau 9002"), "Anstupsen statt Fortsetzen"
    assert welt.eintrag("bau 9002")["stufe"] == 1
    assert _argv_protokoll(welt) == []
