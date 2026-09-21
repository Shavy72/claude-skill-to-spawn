"""Fixrunde #236 — Befunde des Prüfpanels als Weg-Tests (F1–F15).

Gleiche Welt wie ``test_aufpasser_236.py``: echtes tmux auf eigenem Socket, echtes
git (Bare-Origin + Worktree), Temp-HOME. Gestellt sind nur ``gh`` (Stub) und
``claude`` (Schlaf-Attrappe, schreibt wie das echte Programm
``$HOME/.claude/sessions/<pid>.json``). Die echten Sitzungen des Bau-Servers
(``spec-*``) und ``~/.claude/sessions/`` werden nie berührt.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    CRONTAB_FAKE,
    GH_WRAPPER,
    HILFEN,
    SITZUNG,
    SKILL,
    SKRIPTE,
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    fenster_ziel,
    git,
    pane_text,
    tmux,
    welt,
    welt_pane_pid,
)

from to_spawn import aufpasser
from to_spawn.waechter_lauf import transkript_ordner

BAU_CLAUDE = """#!/bin/bash
# Start-Attrappe, die wie bau.py eine Claude-Session startet (hier: die Schlaf-Attrappe).
echo "$@" >> "{protokoll}"
exec "{claude}" "$@"
"""

BAU_SOFORT_WEG = """#!/bin/bash
# Start-Attrappe, die sofort endet — das Fenster verschwindet wieder (F10).
echo "$@" >> "{protokoll}"
exit 0
"""


@pytest.fixture(autouse=True)
def _ohne_tmux_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Tests laufen selbst in einer tmux-Sitzung: ``TMUX_PANE`` darf nicht erben."""
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setenv("FAKE_CLAUDE_TMUX_SOCKET", SOCKET)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def _still_seit(welt: Welt, name: str, minuten: float, **felder: object) -> None:
    """Stand so vorbelegen, als stünde das Fenster seit ``minuten`` unverändert."""
    welt.stand_setzen(
        name,
        hash=_hash(pane_text(name)),
        seit=time.time() - minuten * 60,
        **felder,
    )


def _fenster(welt: Welt, name: str, befehl: str, **env: str) -> None:
    vorspann = " ".join(f"{k}={v}" for k, v in env.items())
    tmux(
        "new-window",
        "-d",
        "-t",
        f"={SITZUNG}",
        "-n",
        name,
        "-c",
        str(welt.repo),
        f"{vorspann} {befehl}".strip(),
    )
    time.sleep(0.8)


def _fenster_id(name: str) -> tuple[str, str]:
    for zeile in tmux(
        "list-windows",
        "-t",
        f"={SITZUNG}",
        "-F",
        "#{window_name}\t#{window_id}\t#{pane_id}",
    ).splitlines():
        nm, wid, pid = zeile.split("\t")
        if nm == name:
            return wid, pid
    raise AssertionError(f"Fenster {name!r} fehlt")


def _lauf_direkt(
    welt: Welt, jetzt: float | None = None, hang_min: float = 90, **extra: object
) -> int:
    """Lauf mit einspritzbarer Uhr (``jetzt``) und weiteren Einstellungen."""
    e = aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=hang_min,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
        jetzt=(lambda: jetzt) if jetzt else time.time,
        **extra,  # type: ignore[arg-type]
    )
    return aufpasser.lauf_mit_einstellungen(e)


def _bau_claude_vorlage(welt: Welt, skript: str = BAU_CLAUDE) -> str:
    datei = welt.bin / "bau_claude.sh"
    datei.write_text(
        skript.format(protokoll=welt.protokoll_bau, claude=welt.bin / "claude"),
        encoding="utf-8",
    )
    datei.chmod(0o755)
    return f"{datei} {{n}}{{resume}}"


def _sessions(welt: Welt) -> list[dict]:
    ordner = welt.heim / ".claude" / "sessions"
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(ordner.glob("*.json"))
    ]


# --- F1: Gesprächs-ID + Zustand aus ~/.claude/sessions/ ---------------------


def test_f1_zwei_fenster_gleicher_cwd_richtige_id(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beide Sessions laufen im selben cwd, keine trägt die ID in argv — die ID
    kommt nur aus der eigenen ``sessions/<pid>.json``, nie aus der des Nachbarn."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    welt.worktree(9002, schmutzig=False)
    sid_a, sid_b = str(uuid.uuid4()), str(uuid.uuid4())
    _fenster(welt, "bau 9003", f"{welt.bin}/claude nur-prompt", FAKE_CLAUDE_SID=sid_b)
    _fenster(welt, "bau 9002", f"{welt.bin}/claude nur-prompt", FAKE_CLAUDE_SID=sid_a)
    assert {s["sessionId"] for s in _sessions(welt)} == {sid_a, sid_b}
    wid, pid = _fenster_id("bau 9002")
    info = aufpasser.session_finden(welt_pane_pid("bau 9002"), wid, pid, welt.heim)
    assert info is not None and info.session_id == sid_a
    assert info.status == "idle" and info.cwd == welt.repo and info.aus_json
    # Nachbar-Fenster-IDs ⇒ die JSON passt nicht ⇒ keine ID (nie die fremde).
    wid_b, pid_b = _fenster_id("bau 9003")
    assert (
        aufpasser.session_finden(welt_pane_pid("bau 9002"), wid_b, pid_b, welt.heim)
        is None
    )

    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == [f"9002 --resume {sid_a}"], argv
    assert sid_b not in welt.protokoll_bau.read_text(encoding="utf-8")


def test_f1_ohne_json_ohne_argv_keine_id_braucht_david(welt: Welt) -> None:
    """Kein Transkript-Fallback mehr: selbst ein frisches ``.jsonl`` im Projekt-Ordner
    liefert keine ID. Ergebnis: nichts anfassen, Meldung „braucht David“, Stufe 3."""
    welt.worktree(9002, schmutzig=True)
    _fenster(
        welt, "bau 9002", f"{welt.bin}/claude nur-prompt", FAKE_CLAUDE_OHNE_JSON="1"
    )
    ordner = transkript_ordner(welt.repo, welt.heim)
    ordner.mkdir(parents=True)
    (ordner / f"{uuid.uuid4()}.jsonl").write_text("{}\n", encoding="utf-8")
    wid, pid = _fenster_id("bau 9002")
    assert (
        aufpasser.session_finden(welt_pane_pid("bau 9002"), wid, pid, welt.heim) is None
    )
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf() == 0
    assert "bau 9002" in fenster_namen()
    assert not welt.protokoll_bau.exists()
    assert not git(welt.origin, "branch", "--list", "sicherung/*").strip()
    treffer = [
        k
        for k in welt.kommentare()
        if "keine Gesprächs-ID gefunden — braucht David" in k
    ]
    assert len(treffer) == 1, welt.kommentare()
    assert welt.eintrag("bau 9002")["stufe"] == 3


def test_f1_argv_id_muss_zur_json_passen(welt: Welt) -> None:
    sid = str(uuid.uuid4())
    _fenster(
        welt,
        "bau 9002",
        f"{welt.bin}/claude --session-id {sid} x",
        FAKE_CLAUDE_OHNE_JSON="1",
    )
    pane_pid = welt_pane_pid("bau 9002")
    wid, pid = _fenster_id("bau 9002")
    # Nur argv ⇒ ID aus argv, nicht aus JSON.
    info = aufpasser.session_finden(pane_pid, wid, pid, welt.heim)
    assert info and info.session_id == sid and not info.aus_json and info.status is None
    claude_pid = next(
        p
        for p in aufpasser.prozess_baum(pane_pid)
        if aufpasser.session_id_aus_argv(aufpasser._argv(p))
    )
    ordner = welt.heim / ".claude" / "sessions"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / f"{claude_pid}.json"
    # JSON mit anderer ID ⇒ Widerspruch ⇒ None.
    datei.write_text(
        json.dumps(
            {
                "pid": claude_pid,
                "sessionId": "andere",
                "cwd": str(welt.repo),
                "status": "idle",
            }
        )
    )
    assert aufpasser.session_finden(pane_pid, wid, pid, welt.heim) is None
    # JSON passend, ohne tmux-Feld ⇒ gültig, Zustand aus JSON.
    datei.write_text(
        json.dumps(
            {
                "pid": claude_pid,
                "sessionId": sid,
                "cwd": str(welt.repo),
                "status": "busy",
            }
        )
    )
    info = aufpasser.session_finden(pane_pid, wid, pid, welt.heim)
    assert info and info.aus_json and info.status == "busy"
    # JSON mit fremdem Pane ⇒ ungültig ⇒ argv-ID bleibt, aber ohne Zustand.
    datei.write_text(
        json.dumps(
            {
                "pid": claude_pid,
                "sessionId": sid,
                "cwd": str(welt.repo),
                "status": "busy",
                "tmux": "spec-1:@99.%99",
            }
        )
    )
    info = aufpasser.session_finden(pane_pid, wid, pid, welt.heim)
    assert info and not info.aus_json and info.status is None
    # JSON eines toten Prozesses ⇒ ungültig.
    (ordner / "999999.json").write_text(
        json.dumps({"pid": 999999, "sessionId": "tot", "cwd": "/"})
    )
    assert aufpasser._session_json_lesen(999999, wid, pid, welt.heim) is None


def test_f1_list_windows_liefert_fenster_und_pane_id(welt: Welt) -> None:
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(zustand=welt.zustand, tmux_socket=SOCKET, trocken=True)
    )
    (f,) = a.fenster(SITZUNG, SPEC)
    assert re.fullmatch(r"@\d+", f.window_id) and re.fullmatch(r"%\d+", f.pane_id)


# --- F2: busy, Limit-Zeile, Rückfrage-Marker ----------------------------------


def test_f2_busy_gilt_als_arbeitend(welt: Welt) -> None:
    sid = str(uuid.uuid4())
    _fenster(
        welt,
        "bau 9002",
        f"{welt.bin}/claude --session-id {sid} x",
        FAKE_CLAUDE_STATUS="busy",
    )
    vorher = pane_text("bau 9002")
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert pane_text("bau 9002") == vorher
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0
    assert welt.kommentare() == []


def test_f2_limit_zeile_echt_und_neue_marker() -> None:
    eintrag = json.loads(
        (HILFEN / "limit_zeile_echt.jsonl").read_text(encoding="utf-8")
    )
    text = " ".join(
        b["text"] for b in eintrag["message"]["content"] if b.get("type") == "text"
    )
    assert "hit your session limit" in text
    assert aufpasser.arbeitet(f"…\n{text}\n❯")
    assert aufpasser.LIMIT_TEXT.search(text)
    for marker in (
        "Do you want to",
        "Yes, and don't ask again",
        "(y/n)",
        "Trust",
        "Esc to cancel",
        "Do you want to proceed?",
    ):
        assert aufpasser.arbeitet(f"…\n{marker}\n…"), marker
        assert aufpasser.rueckfrage(f"…\n{marker}\n…"), marker
    assert aufpasser.arbeitet("nur Text", status="busy")
    assert not aufpasser.arbeitet("nur Text", status="idle")


def test_f2_rueckfrage_nie_send_keys_einmal_meldung(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "printf 'Do you want to proceed?\\n'; sleep 3600")
    vorher = pane_text("bau 9002")
    assert "Do you want to proceed?" in vorher
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert welt.lauf() == 0
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert pane_text("bau 9002") == vorher
    treffer = [k for k in welt.kommentare() if "braucht David" in k and "bau 9002" in k]
    assert len(treffer) == 1, welt.kommentare()
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0


# --- F3: Stufen-Logik — Text geändert nach Eingriff = Erfolg ----------------


def test_f3_text_geaendert_nach_eingriff_setzt_stufe_zurueck(welt: Welt) -> None:
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    t0 = time.time()
    welt.stand_setzen(
        "bau 9002", hash="alt", stufe=1, seit=t0 - 100 * 60, eingriff=t0 - 100 * 60
    )
    # Lauf A: Text hat sich seit dem Anstupsen geändert ⇒ Eingriff hat gewirkt.
    assert _lauf_direkt(welt, jetzt=t0) == 0
    e = welt.eintrag("bau 9002")
    assert e["stufe"] == 0 and abs(e["seit"] - t0) < 1
    assert not welt.protokoll_bau.exists()
    # Lauf B: 100 min später still ⇒ Anstupsen, kein Fortsetzen.
    assert _lauf_direkt(welt, jetzt=t0 + 100 * 60) == 0
    time.sleep(1)
    assert "Aufpasser: Du stehst seit 100 min still" in pane_text("bau 9002")
    assert welt.eintrag("bau 9002")["stufe"] == 1
    assert not welt.protokoll_bau.exists()
    assert not git(welt.origin, "branch", "--list", "sicherung/*").strip()


def test_f3_stufe_3_bleibt_ohne_textaenderung(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "sleep 3600")
    t0 = time.time()
    _still_seit(welt, "bau 9002", 100, stufe=3, eingriff=t0 - 100 * 60)
    assert _lauf_direkt(welt, jetzt=t0 + 8 * 3600) == 0
    assert welt.eintrag("bau 9002")["stufe"] == 3
    assert welt.kommentare() == []
    # Text ändert sich ⇒ Stufe 0.
    tmux("send-keys", "-t", fenster_ziel("bau 9002"), "-l", "tipp")
    time.sleep(0.3)
    assert _lauf_direkt(welt, jetzt=t0 + 8 * 3600 + 60) == 0
    assert welt.eintrag("bau 9002")["stufe"] == 0


# --- F4/F5: Fortsetzen per respawn-window + Nachweis -------------------------


def test_f4_f5_fortsetzen_respawn_fenster_bleibt_nachweis_gruen(welt: Welt) -> None:
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    wid, pid = _fenster_id("bau 9002")
    alt_pane_pid = welt_pane_pid("bau 9002")
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    assert fenster_namen().count("bau 9002") == 1
    assert _fenster_id("bau 9002") == (wid, pid), "Fenster/Pane müssen bleiben"
    neu_pane_pid = welt_pane_pid("bau 9002")
    assert neu_pane_pid != alt_pane_pid
    assert not Path(f"/proc/{alt_pane_pid}").exists()
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == [f"9002 --resume {sid}"]
    info = aufpasser.session_finden(neu_pane_pid, wid, pid, welt.heim)
    assert info and info.aus_json and info.session_id == sid
    assert welt.eintrag("bau 9002")["stufe"] == 2
    assert any("fortgesetzt" in k and sid[:8] in k for k in welt.kommentare())


def test_f4_fortsetzen_auch_als_letztes_fenster_sitzung_lebt(welt: Welt) -> None:
    """Das Wächter-Fenster fehlt: Fortsetzen darf die Sitzung nicht sterben lassen."""
    welt.worktree(9002, schmutzig=False)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    tmux("kill-window", "-t", fenster_ziel(f"wache {SPEC}"))
    assert fenster_namen() == ["bau 9002"]
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    assert fenster_namen() == ["bau 9002"]
    assert welt.eintrag("bau 9002")["stufe"] == 2


def test_f5_nachweis_fehlt_meldung_stufe_3(welt: Welt) -> None:
    welt.worktree(9002, schmutzig=False)
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    vorlage = _bau_claude_vorlage(
        welt,
        BAU_CLAUDE.replace(
            'exec "{claude}"', 'FAKE_CLAUDE_STATUS=exit exec "{claude}"'
        ),
    )
    assert welt.lauf("--bau-vorlage", vorlage) == 0
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == [f"9002 --resume {sid}"]
    treffer = [
        k
        for k in welt.kommentare()
        if f"Fortsetzen fehlgeschlagen (Session {sid[:8]} nicht wieder da) — braucht David"
        in k
    ]
    assert len(treffer) == 1, welt.kommentare()
    assert welt.eintrag("bau 9002")["stufe"] == 3


def test_f4_respawn_und_kill_nur_mit_freigabe() -> None:
    quelle = (SKILL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    assert quelle.count("kill-window") == 1
    assert quelle.count("respawn-window") == 1
    baum = ast.parse(quelle)
    funktionen = {
        k.name: k
        for k in ast.walk(baum)
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name, wort in (
        ("_fenster_schliessen", "kill-window"),
        ("_fenster_fortsetzen", "respawn-window"),
    ):
        fn = funktionen[name]
        konstanten = [
            k.value
            for k in ast.walk(fn)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        ]
        assert any(wort in k for k in konstanten), name
        assert any(a.arg == "freigabe" for a in fn.args.args), name
        assert any(isinstance(k, ast.Raise) for k in ast.walk(fn)), (
            f"{name} muss ohne Freigabe abbrechen"
        )


def test_f5_bau_resume_ohne_transkript_bricht_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bau.py --resume <id>``: fehlt ``~/.claude/projects/<cwd-slug>/<id>.jsonl``,
    Exit 2 mit Fehlerzeile — nie frisch starten."""
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    git(arbeit, "init", "-q")
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-900.json").write_text(
        json.dumps(
            {
                "spec": 900,
                "tickets": {
                    "901": {"title": "Wegwerf", "schaetzung_k": 1, "umfang": "x"}
                },
            }
        ),
        encoding="utf-8",
    )
    shutil.copy2(SKILL / "repo-scripts" / "_default.json", manifeste / "_default.json")
    heim = tmp_path / "heim"
    (heim / ".claude").mkdir(parents=True)
    # Plugins bleiben echt (bau.py verlangt context-mode), alles andere Temp.
    echte_plugins = Path.home() / ".claude" / "plugins"
    if echte_plugins.exists():
        (heim / ".claude" / "plugins").symlink_to(echte_plugins)
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    umgebung["HOME"] = str(heim)
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", "--resume", "abc"],
        cwd=str(arbeit),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "abc" in ergebnis.stderr and "Transkript" in ergebnis.stderr


# --- F6: direkt vor dem Eingriff neu lesen -----------------------------------


def test_f6_text_aendert_sich_kurz_vor_eingriff_kein_eingriff(welt: Welt) -> None:
    _fenster(welt, "bau 9002", "sleep 3; echo aktiv; sleep 3600")
    vorher = pane_text("bau 9002")
    assert "aktiv" not in vorher
    _still_seit(welt, "bau 9002", 100, stufe=0)
    assert _lauf_direkt(welt, verzoegerung_s=4.0) == 0
    time.sleep(0.5)
    nachher = pane_text("bau 9002")
    assert "aktiv" in nachher and "Aufpasser" not in nachher
    assert welt.eintrag("bau 9002")["stufe"] == 0
    assert welt.kommentare() == []
    assert "inzwischen aktiv" in welt.log()


# --- F7: Worktree aus ``git worktree list`` -----------------------------------


def test_f7_worktree_aus_git_liste_trotz_falschem_bau_wt_dir(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAU_WT_DIR", str(welt.tmp / "falsch"))
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    wt = welt.worktree(9003, schmutzig=True)
    assert aufpasser.worktree_finden(welt.repo, 9003) == wt.resolve()
    assert aufpasser.worktree_finden(welt.repo, 9004) is None
    assert aufpasser.worktree_finden(welt.repo, 9004, welt.repo) is None, (
        "Hauptrepo nie"
    )
    assert aufpasser.worktree_finden(welt.repo, 9004, wt) == wt.resolve()
    _fenster(welt, "bau 9003", "sleep 3600")
    _still_seit(welt, "bau 9003", 20)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9003" not in fenster_namen()
    baum = git(welt.origin, "ls-tree", "-r", "--name-only", "refs/heads/sicherung/9003")
    assert "neu.txt" in baum.split()


def test_f7_hauptrepo_wird_nie_gesichert(welt: Welt) -> None:
    (welt.repo / "schmutz.txt").write_text("x", encoding="utf-8")
    sid = str(uuid.uuid4())
    _fenster(welt, "bau 9002", f"{welt.bin}/claude --session-id {sid} x")
    _still_seit(welt, "bau 9002", 100, stufe=1, eingriff=time.time() - 100 * 60)
    assert welt.lauf("--bau-vorlage", _bau_claude_vorlage(welt)) == 0
    assert not git(welt.origin, "branch", "--list", "sicherung/*").strip()
    assert any("nichts zu sichern" in k for k in welt.kommentare())
    assert "?? schmutz.txt" in git(welt.repo, "status", "--porcelain")


# --- F8: Sicherung ohne Geheimnisse, Kette ------------------------------------


def test_f8_sicherung_ohne_env_und_mit_kette(welt: Welt) -> None:
    wt = welt.worktree(9002, schmutzig=True)
    (wt / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    (wt / "prod.env").write_text("GEHEIM=2\n", encoding="utf-8")
    (wt / "zugang_server.txt").write_text("GEHEIM=3\n", encoding="utf-8")
    (wt / "schluessel.pem").write_text("GEHEIM=4\n", encoding="utf-8")
    (wt / "id.key").write_text("GEHEIM=5\n", encoding="utf-8")
    (wt / ".credentials.json").write_text("{}", encoding="utf-8")
    erster = aufpasser.sichern(wt, 9002)
    assert erster
    dateien = git(
        welt.origin, "ls-tree", "-r", "--name-only", "refs/heads/sicherung/9002"
    ).split()
    assert "neu.txt" in dateien and "README.md" in dateien
    for geheim in (
        ".env",
        "prod.env",
        "zugang_server.txt",
        "schluessel.pem",
        "id.key",
        ".credentials.json",
    ):
        assert geheim not in dateien, geheim
    assert git(
        welt.origin, "log", "-1", "--format=%P", "refs/heads/sicherung/9002"
    ).split() == [git(wt, "rev-parse", "HEAD").strip()]
    # Zweite Sicherung: zwei Eltern (HEAD + vorige Sicherung), Push ohne Zwang.
    (wt / "zweite.txt").write_text("mehr\n", encoding="utf-8")
    zweiter = aufpasser.sichern(wt, 9002)
    assert zweiter and zweiter != erster
    eltern = git(
        welt.origin, "log", "-1", "--format=%P", "refs/heads/sicherung/9002"
    ).split()
    assert sorted(eltern) == sorted([git(wt, "rev-parse", "HEAD").strip(), erster])
    assert "?? .env" in git(wt, "status", "--porcelain")


# --- F9: Lauf-Sperre ------------------------------------------------------------


def test_f9_lauf_sperre_zweiter_lauf_geht_sofort(welt: Welt) -> None:
    welt.zustand.mkdir(parents=True, exist_ok=True)
    with open(welt.zustand / "lock", "w", encoding="utf-8") as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert welt.lauf() == 0
    assert fenster_namen() == [f"wache {SPEC}"], "gesperrt ⇒ kein Start"
    assert "läuft schon" in welt.log()
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9002" in fenster_namen()


# --- F10: Start-Regel ------------------------------------------------------------


def test_f10_labels_verhindern_start(
    welt: Welt, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003|9004|9005")
    daten = tmp_path / "gh_daten.json"
    daten.write_text(
        json.dumps(
            {
                "9003": {"labels": ["ready-for-human"]},
                "9004": {"labels": ["needs-info"]},
                "9005": {"labels": ["wontfix"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert sorted(fenster_namen()) == ["bau 9002", f"wache {SPEC}"]


def test_f10_start_gedaechtnis_zwei_starts_dann_meldung(welt: Welt) -> None:
    vorlage = _bau_claude_vorlage(welt, BAU_SOFORT_WEG)
    for _ in range(3):
        assert welt.lauf("--bau-vorlage", vorlage) == 0
        time.sleep(0.5)
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == ["9002", "9002"], argv
    treffer = [
        k
        for k in welt.kommentare()
        if "#9002 startet nicht (Fenster verschwindet wieder) — braucht David" in k
    ]
    assert len(treffer) == 1, welt.kommentare()
    assert len(welt.stand()["starts"]["9002"]) == 2


# --- F11: Schließen nur nach Karenz -------------------------------------------------


def test_f11_ticket_zu_schliesst_erst_nach_karenz(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    _fenster(welt, "bau 9003", "sleep 3600")
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen(), "frisch ⇒ Karenz läuft"
    assert welt.kommentare() == []
    _still_seit(welt, "bau 9003", aufpasser.KARENZ_MIN + 1)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9003" not in fenster_namen()
    assert any("geschlossen (Ticket zu)" in k for k in welt.kommentare())


def test_f11_ticket_zu_busy_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    sid = str(uuid.uuid4())
    _fenster(
        welt,
        "bau 9003",
        f"{welt.bin}/claude --session-id {sid} x",
        FAKE_CLAUDE_STATUS="busy",
    )
    _still_seit(welt, "bau 9003", 30)
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen()
    assert welt.kommentare() == []


# --- F12: Deploy-Wache -----------------------------------------------------------


def _deploy_attrappe(welt: Welt, name: str) -> subprocess.Popen[bytes]:
    skript = welt.bin / name
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    return subprocess.Popen([str(skript)])


def test_f12_waise_aelter_6h_wird_ignoriert(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Gefälschtes ``ps``: jeder Prozess ist „7 Stunden alt“.
    (welt.bin / "ps").write_text("#!/bin/bash\necho 25200\n", encoding="utf-8")
    (welt.bin / "ps").chmod(0o755)
    prozess = _deploy_attrappe(welt, "safe_deploy_vps.sh")
    try:
        assert welt.lauf("--deploy-muster", aufpasser.DEPLOY_MUSTER) == 0
        time.sleep(0.5)
        assert "Waise ignoriert" in welt.log()
        assert "bau 9002" in fenster_namen(), "Waise blockiert nicht"
    finally:
        prozess.terminate()
        prozess.wait(timeout=10)


def test_f12_junger_deploy_blockiert(welt: Welt) -> None:
    prozess = _deploy_attrappe(welt, "staging_deploy.sh")
    try:
        assert welt.lauf("--deploy-muster", aufpasser.DEPLOY_MUSTER) == 0
        time.sleep(0.5)
        assert "Deploy/Gate läuft" in welt.log()
        assert fenster_namen() == [f"wache {SPEC}"]
    finally:
        prozess.terminate()
        prozess.wait(timeout=10)


def test_f12_muster_aus_repo_config(welt: Welt) -> None:
    (welt.repo / ".to-spawn" / "config.json").write_text(
        json.dumps(
            {
                "deploy_befehl": "bash scripts/mein_deploy.sh --alles",
                "staging_start": "tools/nest_hoch.sh",
            }
        ),
        encoding="utf-8",
    )
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(zustand=welt.zustand, tmux_socket=SOCKET, trocken=True)
    )
    muster = a.deploy_muster_fuer([welt.repo])
    assert re.search(muster, "bash /x/mein_deploy.sh") and re.search(
        muster, "nest_hoch.sh"
    )
    assert re.search(muster, "safe_deploy_vps.sh")
    prozess = _deploy_attrappe(welt, "mein_deploy.sh")
    try:
        assert welt.lauf("--deploy-muster", aufpasser.DEPLOY_MUSTER) == 0
        assert "Deploy/Gate läuft" in welt.log()
        assert fenster_namen() == [f"wache {SPEC}"]
    finally:
        prozess.terminate()
        prozess.wait(timeout=10)


def test_f12_sh_fehlendes_programm_wird_runtime_error(welt: Welt) -> None:
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(zustand=welt.zustand, tmux_socket=SOCKET, trocken=True)
    )
    with pytest.raises(RuntimeError, match="gibt-es-nicht"):
        a.sh("programm-gibt-es-nicht-4711")
    with pytest.raises(RuntimeError, match="Zeit"):
        a.sh("sleep", "5", timeout=0.2)
    with pytest.raises(RuntimeError):
        aufpasser._git(welt.tmp / "kein-repo", "status")


# --- F13: Cron -------------------------------------------------------------------


def _crontab_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inhalt: str) -> Path:
    binaer = tmp_path / "cronbin"
    binaer.mkdir()
    (binaer / "crontab").write_text(CRONTAB_FAKE, encoding="utf-8")
    (binaer / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    datei = tmp_path / "crontab.txt"
    monkeypatch.setenv("CRONTAB_DATEI", str(datei))
    datei.write_text(inhalt, encoding="utf-8")
    return datei


ALT_ZEILE = "*/15 * * * * /usr/bin/python3 /home/bau/aufpasser/aufpasser.py >> /home/bau/aufpasser/cron.out 2>&1"


def test_f13_alt_zeile_ohne_marke_wird_ersetzt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    datei = _crontab_fake(
        tmp_path, monkeypatch, f"0 3 * * * /usr/bin/backup.sh\n{ALT_ZEILE}\n"
    )
    zustand = tmp_path / "zustand"
    assert aufpasser.main(["--zustand", str(zustand), "--cron-einrichten"]) == 0
    zeilen = datei.read_text(encoding="utf-8").splitlines()
    treffer = [z for z in zeilen if "aufpasser.py" in z]
    assert len(treffer) == 1 and treffer[0].endswith(aufpasser.CRON_MARKE), zeilen
    assert "/home/bau/aufpasser" not in treffer[0]
    assert str(SKRIPTE / "aufpasser.py") in treffer[0]
    assert "0 3 * * * /usr/bin/backup.sh" in zeilen and len(zeilen) == 2


def test_f13_trocken_zeigt_nur_und_legt_nichts_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    datei = _crontab_fake(tmp_path, monkeypatch, f"{ALT_ZEILE}\n")
    zustand = tmp_path / "zustand"
    assert (
        aufpasser.main(["--zustand", str(zustand), "--trocken", "--cron-einrichten"])
        == 0
    )
    aus = capsys.readouterr()
    assert "[trocken]" in aus.out and str(SKRIPTE / "aufpasser.py") in aus.out
    assert datei.read_text(encoding="utf-8") == f"{ALT_ZEILE}\n"
    assert not zustand.exists()


def test_f13_trocken_lauf_legt_keinen_zustandsordner_an(welt: Welt) -> None:
    assert welt.lauf("--trocken") == 0
    assert not welt.zustand.exists()
    assert fenster_namen() == [f"wache {SPEC}"]


# --- F14: Meldungs-Doppelschutz erst nach Erfolg ---------------------------------


def test_f14_schluessel_erst_nach_erfolgreichem_kommentar(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaputt = welt.bin / "gh"
    original = kaputt.read_text(encoding="utf-8")
    kaputt.write_text(
        '#!/bin/sh\nif [ "$1" = "issue" ] && [ "$2" = "comment" ]; then echo "gh: kaputt" >&2; exit 1; fi\n'
        + original.split("\n", 1)[1],
        encoding="utf-8",
    )
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert "bau 9002" in fenster_namen()
    assert welt.kommentare() == []
    assert welt.stand().get("meldungen", {}) == {}
    assert "Kommentar fehlgeschlagen" in welt.log()
    kaputt.write_text(original, encoding="utf-8")
    tmux("kill-window", "-t", fenster_ziel("bau 9002"))
    assert welt.lauf() == 0
    assert any("#9002 hatte keine Session, gestartet." in k for k in welt.kommentare())
    assert len(welt.stand()["meldungen"]) == 1


# --- F15: hang_min ≥ 61 -----------------------------------------------------------


def test_f15_hang_min_untergrenze(welt: Welt) -> None:
    assert aufpasser.HANG_MIN_UNTERGRENZE == 61
    with pytest.raises(ValueError, match="61"):
        aufpasser.Einstellungen(zustand=welt.zustand, hang_min=60)
    aufpasser.Einstellungen(zustand=welt.zustand, hang_min=61)
    with pytest.raises(SystemExit) as fehler:
        welt.lauf(hang_min=30)
    assert fehler.value.code == 2
    assert fenster_namen() == [f"wache {SPEC}"]


def test_f15_ohne_hang_min_gilt_vorgabe(welt: Welt) -> None:
    assert aufpasser.HANG_MIN >= aufpasser.HANG_MIN_UNTERGRENZE
    assert "hang_min" in aufpasser.__doc__ or "61" in aufpasser.__doc__
