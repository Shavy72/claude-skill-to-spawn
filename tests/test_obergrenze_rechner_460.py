"""#460: „Rechner voll: schon N Claude-Sessions (Obergrenze 9)“ — drei Fehler aus Probe-Lauf 2.

1. H2: stille Sessions (``~/.claude/sessions/<pid>.json`` mit ``status: idle``) und das
   Anzeige-Fenster ``leitstand`` zählten wie arbeitende — 3 Bau-Fenster standen 16 min, obwohl
   10 GB RAM frei waren.
2. H2b: „Platz frei?“ und „Platz belegen“ waren zwei Schritte — 3 wartende Bau-Fenster
   starteten im selben Takt, danach 11 Sessions bei Obergrenze 9.
3. K2: ``spawn_srv.sh`` endete mit Exit 5 („nichts gestartet“), obwohl der Aufseher schon lief
   und die restlichen Fenster selbst nachstartet.

Attrappe ist nur ``/proc``, ``meminfo`` und der Sessions-Ordner (Messwerte) sowie tmux
(zeichnet auf, startet nichts); ``speicher``, ``spawn_srv.sh`` und die Sperre sind echt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_spec_rand_e2 as e2
from test_einrichtungs_ticket_410 import _aufbau

from to_spawn import speicher

SKILL = Path(__file__).resolve().parents[1]
_KONFIG = {"speicher": {"max_sessions": 2, "min_frei_mib": 0, "staffel_s": 0}}


def _welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    proc.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:   10485760 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))
    monkeypatch.delenv("TMUX", raising=False)
    return proc, sessions


def _claude(proc: Path, sessions: Path, pid: int, status: str | None, *, anzeige: bool = False) -> None:
    """Claude-Prozess ``pid``; ``status`` = Feld der Session-JSON (``None`` = keine JSON)."""
    ordner = proc / str(pid)
    ordner.mkdir()
    (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0--resume\0abc\0")
    umgebung = [b"PATH=/usr/bin"]
    if anzeige:
        umgebung.append(f"{speicher.ANZEIGE_VARIABLE}=1".encode())
    (ordner / "environ").write_bytes(b"\0".join(umgebung) + b"\0")
    if status is not None:
        (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid, "status": status}), encoding="utf-8")


# --- 1. H2: nur arbeitende Sessions zählen --------------------------------------


def test_stille_sessions_zaehlen_nicht(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """5 stille + 1 arbeitende, Obergrenze 2: Platz frei (früher „schon 6 Claude-Sessions“)."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    for pid in range(201, 206):
        _claude(proc, sessions, pid, "idle")
    _claude(proc, sessions, 301, "busy")
    assert speicher.claude_sessions() == 1
    frei, text = speicher.platz_frei(_KONFIG)
    assert frei, text


def test_session_ohne_json_zaehlt_vorsichtshalber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kein Status lesbar (frisch gestartet, PC) → zählt wie arbeitend; die Grenze bleibt hart."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _claude(proc, sessions, 201, None)
    _claude(proc, sessions, 202, "busy")
    assert speicher.platz_frei(_KONFIG) == (False, "schon 2 Claude-Sessions (Obergrenze 2)")


def test_anzeige_fenster_zaehlt_nie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leitstand (Anzeige-Fenster) arbeitet gerade, zählt aber nicht; der Leitstand setzt die Marke."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _claude(proc, sessions, 201, "busy", anzeige=True)
    _claude(proc, sessions, 202, None, anzeige=True)
    assert speicher.claude_sessions() == 0
    text = (SKILL / "skripte" / "leitstand_fenster.py").read_text(encoding="utf-8")
    assert "speicher.ANZEIGE_VARIABLE" in text


# --- 2. H2b: Prüfen und Belegen in einem Schritt --------------------------------

_WARTER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from to_spawn import speicher
start = Path(sys.argv[2])
while not start.exists():
    time.sleep(0.01)
konfig = {"speicher": {"max_sessions": 3, "min_frei_mib": 0}}
def schlafen(_s):
    sys.exit(9)
speicher.auf_platz_warten(konfig, wer="bau", schlafen=schlafen)
sys.exit(0)
"""


def test_drei_wartende_nehmen_nur_den_einen_freien_platz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2 arbeitende, Obergrenze 3: drei gleichzeitig wartende Fenster — genau eines startet."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _claude(proc, sessions, 201, "busy")
    _claude(proc, sessions, 202, "busy")
    start = tmp_path / "los"
    laeufe = [
        subprocess.Popen(
            [sys.executable, "-c", _WARTER, str(SKILL), str(start)],
            env={**os.environ},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    time.sleep(0.5)
    start.touch()
    codes = sorted(lauf.wait(timeout=60) for lauf in laeufe)
    fehler = [lauf.stderr.read() for lauf in laeufe if lauf.stderr]
    assert codes == [0, 9, 9], (codes, fehler)
    frei, text = speicher.platz_frei({"speicher": {"max_sessions": 3, "min_frei_mib": 0}})
    assert not frei and "schon 3 Claude-Sessions" in text, text


def test_belegter_platz_verfaellt_von_selbst(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Startet das Fenster doch nicht, gibt die Reservierung den Platz nach Ablauf frei."""
    proc, sessions = _welt(tmp_path, monkeypatch)
    _claude(proc, sessions, 201, "busy")
    assert speicher.auf_platz_warten(_KONFIG, schlafen=lambda _s: pytest.fail("darf nicht warten")) == 0
    assert speicher.platz_frei(_KONFIG)[0] is False
    jetzt = time.time()
    monkeypatch.setattr(speicher.time, "time", lambda: jetzt + speicher.RESERVIERUNG_S + 1)
    assert speicher.platz_frei(_KONFIG)[0] is True


# --- 3. K2: spawn_srv.sh mit laufendem Aufseher ---------------------------------


#: Aufseher-Attrappe: ein Python-Prozess ``…/skripte/wache.py <S>`` (so erkennt ihn
#: ``sessions_stand``), lebt, solange ``spawn_srv.sh`` (argv[2]) läuft, höchstens 60 s.
_WACHE_ATTRAPPE = """
import os, sys, time
ende = time.time() + 60
while time.time() < ende:
    try:
        os.kill(int(sys.argv[2]), 0)
    except OSError:
        break
    time.sleep(0.2)
"""


def _tmux_attrappe(ordner: Path, proc: Path, *, wache_prozess: bool = True) -> Path:
    """tmux im PATH: merkt sich Fenster, legt je Fenster einen arbeitenden Claude-Prozess an.

    ``wache_prozess``: ein Fenster ``wache <S>`` startet zusätzlich die Aufseher-Attrappe
    (echter Prozess im Arbeitsordner ``-c``). ``list-sessions`` liest ``sitzungen.txt``.
    """
    ordner.mkdir(parents=True, exist_ok=True)
    aufrufe = ordner / "tmux_aufrufe.txt"
    fenster = ordner / "fenster.txt"
    sitzungen = ordner / "sitzungen.txt"
    wache_py = ordner / "attrappe" / "skripte" / "wache.py"
    wache_py.parent.mkdir(parents=True, exist_ok=True)
    wache_py.write_text(_WACHE_ATTRAPPE, encoding="utf-8")
    wache_start = (
        '    case "$name" in "wache "*)\n'
        f'      ( cd "$ort" && exec {sys.executable!r} {str(wache_py)!r} "${{name#wache }}" "$PPID" )'
        " </dev/null >/dev/null 2>&1 & ;;\n"
        "    esac\n"
        if wache_prozess
        else ""
    )
    tmux = ordner / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}\n'
        'name=""; ort="$PWD"; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-n" ] && name="$a"; [ "$prev" = "-c" ] && ort="$a"; prev="$a"; done\n'
        'case "$1" in\n'
        f"  has-session) [ -s {str(fenster)!r} ] ;;\n"
        f"  list-windows) [ -s {str(fenster)!r} ] && cat {str(fenster)!r} ;;\n"
        f"  list-sessions) [ -s {str(sitzungen)!r} ] && cat {str(sitzungen)!r} ;;\n"
        "  new-session|new-window)\n"
        f'    printf "%s\\n" "$name" >> {str(fenster)!r}\n'
        f"    d={str(proc)!r}/$((1000 + $(wc -l < {str(fenster)!r})))\n"
        '    mkdir -p "$d"; printf "/usr/bin/claude\\0" > "$d/cmdline"; printf "PATH=/usr/bin\\0" > "$d/environ"\n'
        f"{wache_start}"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return aufrufe


@pytest.mark.braucht("bash", "posix")
def test_spawn_srv_rest_wartet_auf_aufseher_kein_fehler_exit(tmp_path: Path) -> None:
    """Rechner-Grenze 12 (11 belegt): Aufseher startet, dann ist der Rechner voll — Exit 0, ehrliche Meldung."""
    from to_spawn import bash_wahl

    proc = tmp_path / "proc"
    proc.mkdir()
    (tmp_path / "sessions").mkdir()
    # 284: Rechner-Grenze ist 12 je 16 GB (Messung 25.11.2026, vorher aus max_sessions) → 11 fremde belegen
    # vor, der Aufseher-Start ist der 12. Platz.
    for pid in range(201, 212):
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "cmdline").write_bytes(b"/usr/bin/claude\0")
        (proc / str(pid) / "environ").write_bytes(b"PATH=/usr/bin\0")
    repo, _welt_datei, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": 1, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": False},
        },
    )
    aufrufe = _tmux_attrappe(tmp_path / "bin", proc)
    umgebung.update(
        {
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(tmp_path / "bin"),
            "TO_SPAWN_SPEICHER_PROC": str(proc),
            "TO_SPAWN_SPEICHER_SESSIONS": str(tmp_path / "sessions"),
            "TO_SPAWN_SPEICHER_PLATZ": str(tmp_path / "platz.json"),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    lauf = bash_wahl.lauf(
        [str(e2.CLI.parent / "skripte" / "spawn_srv.sh"), str(e2.SPEC), "--tickets", "901"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    ausgabe = lauf.stdout + lauf.stderr
    gestartet = aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""
    assert f"wache {e2.SPEC}" in gestartet and "bau 901" not in gestartet, gestartet
    assert lauf.returncode == 0, ausgabe
    assert "wartet auf Platz: bau 901" in ausgabe and "Aufseher startet nach" in ausgabe, ausgabe
