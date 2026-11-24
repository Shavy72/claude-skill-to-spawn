"""#460 Fix-Runde 1, Teil B: ``spawn_srv.sh`` bei vollem Rechner — ehrliche Ausgabe, echter Aufseher.

1. Warte-Zweig (Exit 0, Aufseher startet nach) druckte zusätzlich „nicht gestartet“ — die
   Ausgabe widersprach sich.
2. Als „Aufseher läuft“ galt schon ein Fenster ``wache <S>``; ein Fenster ohne lebenden
   ``wache.py``-Prozess startet niemanden nach → muss wie „ohne Aufseher“ enden (Exit 5).
3. Gesetztes ``TMUX`` (Aufruf aus einer anderen tmux-Sitzung) mit zwei Sitzungen: es zählen nur
   die Claude-Sessions der Spec-Sitzung.
4. Leitstand: das Anzeige-Fenster trägt die Marke wirklich in die Umgebung der gestarteten
   Session (Verhalten statt Quelltext-Suche).

Attrappen: ``/proc``, ``meminfo``, Sessions-Ordner (Messwerte), tmux (zeichnet auf) und
``claude`` (schreibt nur seine Umgebung weg); ``spawn_srv.sh``, ``speicher``,
``sessions_stand`` und ``leitstand.py`` sind echt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import test_spec_rand_e2 as e2
from test_einrichtungs_ticket_410 import _aufbau
from test_obergrenze_rechner_460 import _tmux_attrappe

from to_spawn import speicher

SKILL = Path(__file__).resolve().parents[1]
#: Sitzungen auf dem Attrappen-tmux-Server: die fremde (aus der aufgerufen wird) und die Spec.
SOCKEL = "/tmp/l173b-attrappe/default"
FREMD, EIGEN = "4", "7"


def _claude(proc: Path, sessions: Path, pid: int, tmux_wert: str | None = None) -> None:
    """Arbeitender Claude-Prozess ``pid``, optional in der tmux-Sitzung ``tmux_wert``."""
    ordner = proc / str(pid)
    ordner.mkdir(parents=True)
    (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0")
    umgebung = [
        b"PATH=/usr/bin",
        *([f"TMUX={tmux_wert}".encode()] if tmux_wert else []),
    ]
    (ordner / "environ").write_bytes(b"\0".join(umgebung) + b"\0")
    (sessions / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "status": "busy"}), encoding="utf-8"
    )


def _spawn(
    tmp_path: Path,
    *args: str,
    max_sessions: int = 1,
    belegt: int = 0,
    belegt_tmux: str | None = None,
    fenster: list[str] | None = None,
    wache_prozess: bool = True,
    tmux_wert: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """``spawn_srv.sh <S> --tickets 901 <args>`` mit ``belegt`` schon arbeitenden Sessions."""
    from to_spawn import bash_wahl

    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    for pid in range(201, 201 + belegt):
        _claude(proc, sessions, pid, belegt_tmux)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16777216 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8"
    )
    repo, _welt, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {
                "max_sessions": max_sessions,
                "min_frei_mib": 0,
                "staffel_s": 0,
            },
            "leitstand": {"aktiv": False},
        },
    )
    aufrufe = _tmux_attrappe(tmp_path / "bin", proc, wache_prozess=wache_prozess)
    if fenster:
        (tmp_path / "bin" / "fenster.txt").write_text(
            "".join(f"{f}\n" for f in fenster), encoding="utf-8"
        )
    (tmp_path / "bin" / "sitzungen.txt").write_text(
        f"leit\t{SOCKEL}\t${FREMD}\nspec-{e2.SPEC}\t{SOCKEL}\t${EIGEN}\n",
        encoding="utf-8",
    )
    umgebung.update(
        {
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(tmp_path / "bin"),
            "TO_SPAWN_SPEICHER_PROC": str(proc),
            "TO_SPAWN_SPEICHER_SESSIONS": str(sessions),
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PLATZ": str(tmp_path / "platz.json"),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    umgebung.pop("TMUX", None)
    if tmux_wert:
        umgebung["TMUX"] = tmux_wert
    lauf = bash_wahl.lauf(
        [
            str(SKILL / "skripte" / "spawn_srv.sh"),
            str(e2.SPEC),
            "--tickets",
            "901",
            *args,
        ],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return lauf, aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""


@pytest.mark.braucht("bash", "posix")
def test_voll_ohne_aufseher_exit_5(tmp_path: Path) -> None:
    lauf, gestartet = _spawn(tmp_path, "--ohne-wache", belegt=1)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 5, ausgabe
    assert (
        "nicht gestartet: bau 901" in ausgabe and "wartet auf Platz" not in ausgabe
    ), ausgabe
    assert "new-" not in gestartet, gestartet


@pytest.mark.braucht("bash", "posix")
def test_voll_wache_fenster_ohne_prozess_exit_5(tmp_path: Path) -> None:
    """Fenster ``wache <S>`` steht, aber kein ``wache.py`` lebt → niemand startet nach."""
    lauf, gestartet = _spawn(
        tmp_path, "--ohne-wache", belegt=1, fenster=[f"wache {e2.SPEC}"]
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 5, ausgabe
    assert (
        "nicht gestartet: bau 901" in ausgabe and "wartet auf Platz" not in ausgabe
    ), ausgabe
    assert "new-" not in gestartet, gestartet


@pytest.mark.braucht("bash", "posix")
def test_warte_zweig_ohne_nicht_gestartet(tmp_path: Path) -> None:
    """Aufseher-Prozess lebt, Rechner voll: nur die Warte-Zeile, kein „nicht gestartet“."""
    lauf, gestartet = _spawn(tmp_path)
    ausgabe = lauf.stdout + lauf.stderr
    assert f"wache {e2.SPEC}" in gestartet and "bau 901" not in gestartet, gestartet
    assert lauf.returncode == 0, ausgabe
    assert "wartet auf Platz: bau 901" in ausgabe, ausgabe
    assert "nicht gestartet" not in ausgabe and "Rest bleibt aus" not in ausgabe, (
        ausgabe
    )


@pytest.mark.braucht("bash", "posix")
def test_tmux_gesetzt_fremde_sitzung_zaehlt_nicht(tmp_path: Path) -> None:
    """Aufruf aus Sitzung ``leit`` (TMUX gesetzt), dort 2 Sessions, Obergrenze 2: Spec-Sitzung frei."""
    lauf, gestartet = _spawn(
        tmp_path,
        "--ohne-wache",
        max_sessions=2,
        belegt=2,
        belegt_tmux=f"{SOCKEL},1,{FREMD}",
        tmux_wert=f"{SOCKEL},1,{FREMD}",
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "gestartet: bau 901" in ausgabe and "bau 901" in gestartet, (
        ausgabe,
        gestartet,
    )


@pytest.mark.braucht("bash", "posix")
def test_tmux_gesetzt_eigene_sitzung_voll(tmp_path: Path) -> None:
    """Dieselben 2 Sessions in der Spec-Sitzung: voll, ohne Aufseher Exit 5."""
    lauf, gestartet = _spawn(
        tmp_path,
        "--ohne-wache",
        max_sessions=2,
        belegt=2,
        belegt_tmux=f"{SOCKEL},1,{EIGEN}",
        tmux_wert=f"{SOCKEL},1,{FREMD}",
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 5, ausgabe
    assert "nicht gestartet: bau 901" in ausgabe and "new-" not in gestartet, (
        ausgabe,
        gestartet,
    )


# --- Leitstand: Marke wirkt im Verhalten ------------------------------------------


@pytest.mark.braucht("posix")
def test_leitstand_session_zaehlt_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echter ``leitstand.py <S> sitzung`` startet die ``claude``-Attrappe; ihre Umgebung als
    Prozess in ``/proc`` belegt keinen Platz (ohne Marke zählt derselbe Prozess)."""
    import test_leitstand_fenster as lf

    repo = lf._repo(tmp_path)
    proc = tmp_path / "proc"
    (proc / "1").mkdir(parents=True)
    (proc / "1" / "cmdline").write_bytes(b"/usr/bin/claude\0")
    test_bin = Path(os.environ["TO_SPAWN_TEST_BIN"])
    claude = test_bin / "claude"
    claude.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(proc / '1' / 'environ')!r}).write_bytes(Path('/proc/self/environ').read_bytes())\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(
        TO_SPAWN_REPO=str(repo),
        TO_SPAWN_WAECHTER_ORDNER=str(tmp_path / "waechter"),
        TO_SPAWN_GH_REPO="test-org/test-repo",
        TO_SPAWN_TESTLAUF="1",
        TO_SPAWN_TEST_BIN=str(test_bin),
        PYTHONIOENCODING="utf-8",
    )
    erg = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "leitstand.py"), "376", "sitzung"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert erg.returncode == 0, erg.stdout + erg.stderr
    umgebung = (proc / "1" / "environ").read_bytes()
    assert f"{speicher.ANZEIGE_VARIABLE}=1".encode() in umgebung.split(b"\0")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(tmp_path / "keine-sessions"))
    assert speicher.claude_sessions(proc=proc) == 0
    (proc / "1" / "environ").write_bytes(b"PATH=/usr/bin\0")
    assert speicher.claude_sessions(proc=proc) == 1
