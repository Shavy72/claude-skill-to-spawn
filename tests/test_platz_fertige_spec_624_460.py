"""#624/#460 (Linux): Platz schaffen räumt stille Sessions fertiger Specs ab,
schließt mehrere fertige Fenster in einer Gegenprobe, blockiert nicht in einer
Sperre-belegt-Schleife und hält den spawn-Wartepfad sauber.

Nur Linux mit tmux (wie ``test_platz_schaffen_624`` und ``test_obergrenze_rechner_460``).
"""

# ruff: noqa: F811 — ``welt``/``zu_9003`` kommen als Fixtures aus den Nachbar-Tests.
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
import test_spec_rand_e2 as e2
from test_aufpasser_236 import (  # noqa: F401 — Fixture
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    git,
    tmux,
    welt,
)
from test_aufpasser_platz_592 import _grenze, zu_9003  # noqa: F401
from test_einrichtungs_ticket_410 import _aufbau
from test_obergrenze_rechner_460 import _WACHE_ATTRAPPE

from to_spawn import aufpasser, bash_wahl, briefkasten, speicher

SKILL = Path(__file__).resolve().parents[1]
SPAWN_SH = SKILL / "skripte" / "spawn_srv.sh"


def _aufpasser(
    welt: Welt,
    *,
    karenz_s: float = 0.3,
    sperre_warten_s: float = 0.0,
    fertige_spec_still_min: float = 15.0,
) -> aufpasser.Aufpasser:
    e = aufpasser.Einstellungen(
        zustand=welt.zustand,
        tmux_socket=SOCKET,
        hang_min=90,
        bau_vorlage=welt.vorlage,
        deploy_muster=f"aufpasser-probe-niemals-{os.getpid()}",
        karenz_platznot_s=karenz_s,
        exit_warten_s=1,
    )
    e.sperre_warten_s = sperre_warten_s
    e.fertige_spec_still_min = fertige_spec_still_min
    return aufpasser.Aufpasser(e)


def _zweite_sitzung(
    welt: Welt,
    sitzung: str,
    spec: str,
    repo_name: str,
    fenster_specs: list[tuple[str, str]],
) -> Path:
    """Legt ein zweites Repo + zweite tmux-Sitzung mit Attrappen-Claude-Fenstern an."""
    repo2 = welt.tmp / repo_name
    repo2.mkdir()
    git(repo2, "init", "-q", "-b", "main")
    git(repo2, "config", "user.email", "test@invalid")
    git(repo2, "config", "user.name", "Test")
    (repo2 / ".to-spawn").mkdir()
    (repo2 / ".to-spawn" / "config.json").write_text('{"speicher": {"max_sessions": 2}}', encoding="utf-8")
    (repo2 / "README.md").write_text("probe\n", encoding="utf-8")
    git(repo2, "add", ".")
    git(repo2, "commit", "-q", "-m", "init")
    for idx, (fname, env_prefix) in enumerate(fenster_specs):
        sid = str(uuid.uuid4())
        cmd = f"FAKE_CLAUDE_TMUX_SOCKET={SOCKET} {env_prefix} {welt.bin}/claude --session-id {sid} x"
        if idx == 0:
            tmux("new-session", "-d", "-s", sitzung, "-n", fname, "-c", str(repo2), cmd)
        else:
            tmux("new-window", "-d", "-t", f"={sitzung}", "-n", fname, "-c", str(repo2), cmd)
    time.sleep(0.6)
    return repo2


def _alle_sessions_altern(welt: Welt, sekunden_alt: float) -> None:
    """Setzt die mtime aller Attrappen-Session-JSONs zurück (simuliert lange Idle-Zeit)."""
    ordner = welt.heim / ".claude" / "sessions"
    assert ordner.is_dir()
    alt = time.time() - sekunden_alt
    dateien = list(ordner.glob("*.json"))
    assert dateien, "erwartet mindestens eine Session-JSON"
    for datei in dateien:
        os.utime(datei, (alt, alt))


def _sitzung_fenster(sitzung: str) -> list[str]:
    erg = subprocess.run(
        ["tmux", "-L", SOCKET, "list-windows", "-t", f"={sitzung}", "-F", "#{window_name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if erg.returncode != 0:
        return []
    return [z for z in erg.stdout.splitlines() if z.strip()]


@pytest.mark.braucht("tmux")
def test_platz_schaffen_schliesst_stille_leitstand_und_wache_fertiger_spec_alle_zu(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#624/#460: Fremdes Repo, alle Tickets zu, leitstand + wache > 15 min idle (nie in stand.json) → schließen."""
    _grenze(welt, monkeypatch, max_sessions=2)
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002,8801:8802")
    monkeypatch.setenv("GH_STUB_ZU", "8802")
    sitzung2 = "spec-probealt-8801"
    _zweite_sitzung(
        welt,
        sitzung2,
        "8801",
        "repo-alt-zu",
        [("leitstand 8801", f"{speicher.ANZEIGE_VARIABLE}=1"), ("wache 8801", "")],
    )
    _alle_sessions_altern(welt, 25 * 60)

    assert _aufpasser(welt).platz_schaffen() > 0
    time.sleep(0.5)
    assert _sitzung_fenster(sitzung2) == [], welt.log()


@pytest.mark.braucht("tmux")
def test_platz_schaffen_schliesst_stille_sessions_bei_nur_abnahme_offen(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#624: Fremde Spec hat nur noch ein Abnahme-Ticket (checkpoint:human) offen, idle > Schwelle → schließen."""
    _grenze(welt, monkeypatch, max_sessions=2)
    daten = welt.tmp / "gh_abnahme.json"
    daten.write_text('{"8813": {"labels": ["checkpoint:human"]}}', encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002,8811:8812|8813")
    monkeypatch.setenv("GH_STUB_ZU", "8812")
    sitzung2 = "spec-probeabnahme-8811"
    _zweite_sitzung(
        welt,
        sitzung2,
        "8811",
        "repo-alt-abnahme",
        [("leitstand 8811", f"{speicher.ANZEIGE_VARIABLE}=1"), ("wache 8811", "")],
    )
    _alle_sessions_altern(welt, 25 * 60)

    assert _aufpasser(welt).platz_schaffen() > 0
    time.sleep(0.5)
    assert _sitzung_fenster(sitzung2) == [], welt.log()


@pytest.mark.braucht("tmux")
def test_platz_schaffen_haelt_fertige_spec_unter_stille_schwelle(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frisch idle gewordene leitstand-/wache-Fenster (< Schwelle) bleiben offen."""
    _grenze(welt, monkeypatch, max_sessions=2)
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002,8821:8822")
    monkeypatch.setenv("GH_STUB_ZU", "8822")
    sitzung2 = "spec-frisch-8821"
    _zweite_sitzung(
        welt,
        sitzung2,
        "8821",
        "repo-alt-frisch",
        [("leitstand 8821", f"{speicher.ANZEIGE_VARIABLE}=1"), ("wache 8821", "")],
    )

    assert _aufpasser(welt, fertige_spec_still_min=15.0).platz_schaffen() > 0
    time.sleep(0.3)
    assert sorted(_sitzung_fenster(sitzung2)) == ["leitstand 8821", "wache 8821"], welt.log()


@pytest.mark.braucht("tmux")
def test_platz_schaffen_haelt_fertige_spec_bei_briefkasten_oder_busy_oder_offenem_bauticket(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schutzregeln: offene Briefkasten-Frage, arbeitende Wache oder offenes Bau-Ticket halten die Fenster."""
    _grenze(welt, monkeypatch, max_sessions=2)
    daten = welt.tmp / "gh_schutz.json"
    daten.write_text('{"8833": {"labels": ["checkpoint:human"]}}', encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002,8831:8833,8841:8842,8851:8852")
    monkeypatch.setenv("GH_STUB_ZU", "8842")

    # (a) Nur Abnahme offen, aber offene Briefkasten-Frage
    repo_a = _zweite_sitzung(
        welt,
        "spec-frage-8831",
        "8831",
        "repo-schutz-frage",
        [("wache 8831", "")],
    )
    briefkasten.einwerfen(8833, "Live freigeben?", "ja", 2, ["Test"], hauptbaum=repo_a)

    # (b) Alle Tickets zu, aber wache arbeitet (status=busy)
    _zweite_sitzung(
        welt,
        "spec-busy-8841",
        "8841",
        "repo-schutz-busy",
        [("wache 8841", "FAKE_CLAUDE_STATUS=busy")],
    )

    # (c) Reguläres Bau-Ticket 8852 noch offen
    _zweite_sitzung(
        welt,
        "spec-offen-8851",
        "8851",
        "repo-schutz-offen",
        [("leitstand 8851", f"{speicher.ANZEIGE_VARIABLE}=1"), ("wache 8851", "")],
    )
    _alle_sessions_altern(welt, 25 * 60)

    assert _aufpasser(welt).platz_schaffen() > 0
    time.sleep(0.3)
    assert _sitzung_fenster("spec-frage-8831") == ["wache 8831"], welt.log()
    assert _sitzung_fenster("spec-busy-8841") == ["wache 8841"], welt.log()
    assert sorted(_sitzung_fenster("spec-offen-8851")) == ["leitstand 8851", "wache 8851"], welt.log()


@pytest.mark.braucht("tmux")
def test_platz_schaffen_schliesst_mehrere_fertige_bau_fenster_in_einer_gegenprobe(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#624: Zwei fertige stille bau-Fenster bei 2/2: beide schließen in EINEM Lauf mit EINER Gegenprobe."""
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003|9004")
    monkeypatch.setenv("GH_STUB_ZU", "9003,9004")
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))
    welt.fenster_fake_claude(9004, str(uuid.uuid4()))

    # Simuliert den Probe-5-Effekt: nach dem ersten Schließen sinkt die Zahl auf 1/2 (frei).
    # Trotzdem müssen BEIDE fertigen Fenster im selben Lauf schließen (einmal Gegenprobe).
    schlaefe: list[float] = []
    echt_sleep = aufpasser.time.sleep
    schliessungen = {"n": 0}
    echt_schliessen = aufpasser.Aufpasser._fenster_schliessen

    def zaehl_schliessen(self: aufpasser.Aufpasser, *args: object, **kwargs: object) -> str:
        res = echt_schliessen(self, *args, **kwargs)
        schliessungen["n"] += 1
        return res

    monkeypatch.setattr(aufpasser.Aufpasser, "_fenster_schliessen", zaehl_schliessen)
    monkeypatch.setattr(
        aufpasser.Aufpasser,
        "_platznot",
        lambda _s, _r, info, _sz: bool(
            info and info.aus_json and info.status == "idle" and schliessungen["n"] == 0
        ),
    )

    def merke_sleep(sek: float) -> None:
        if sek == 0.4:
            schlaefe.append(sek)
        echt_sleep(sek)

    monkeypatch.setattr(aufpasser.time, "sleep", merke_sleep)

    assert _aufpasser(welt, karenz_s=0.4).platz_schaffen() >= 2
    time.sleep(0.5)
    namen = fenster_namen()
    assert "bau 9003" not in namen and "bau 9004" not in namen, (namen, welt.log())
    assert len(schlaefe) == 1, f"Erwartet genau eine gemeinsame Gegenprobe, bekam {schlaefe}"


@pytest.mark.braucht("tmux")
def test_platz_schaffen_sperre_wartet_statt_sperre_belegt_schleife(
    zu_9003: Welt, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#624: Zweiter wartender Starter wartet auf den laufenden platz_schaffen-Lauf statt sofort
    'Sperre belegt' zu melden und 60 s leer zu schlafen."""
    welt = zu_9003
    _grenze(welt, monkeypatch, max_sessions=2)
    welt.fenster_fake_claude(9003, str(uuid.uuid4()))

    erst_drin = threading.Event()
    echt_sleep = aufpasser.time.sleep

    def melde_sleep(sek: float) -> None:
        if sek == 0.6:
            erst_drin.set()
        echt_sleep(sek)

    monkeypatch.setattr(aufpasser.time, "sleep", melde_sleep)

    ergebnis_1: list[int] = []
    ergebnis_2: list[int] = []

    def lauf_1() -> None:
        ergebnis_1.append(_aufpasser(welt, karenz_s=0.6, sperre_warten_s=15.0).platz_schaffen())

    def lauf_2() -> None:
        assert erst_drin.wait(timeout=10), "Lauf 1 erreichte Gegenprobe nicht"
        ergebnis_2.append(_aufpasser(welt, karenz_s=0.6, sperre_warten_s=15.0).platz_schaffen())

    with caplog.at_level("INFO", logger="aufpasser"):
        t1 = threading.Thread(target=lauf_1)
        t2 = threading.Thread(target=lauf_2)
        t1.start()
        t2.start()
        t1.join(timeout=20)
        t2.join(timeout=20)

    assert not t1.is_alive() and not t2.is_alive()
    assert ergebnis_1 and ergebnis_1[0] > 0
    assert ergebnis_2 == [0]
    assert "Sperre belegt" not in caplog.text, caplog.text
    assert "bau 9003" not in fenster_namen(), welt.log()


def test_speicher_cli_schafft_platz_bei_gesamtgrenze_voll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#624/#460: ``speicher.cli`` (nicht trocken) ruft bei voller Gesamtgrenze ``platz_schaffen``
    und liefert Exit 0, sobald danach Platz frei ist; ``--trocken`` ruft nichts."""
    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16384000 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8")
    for pid in range(201, 225):
        ordner = proc / str(pid)
        ordner.mkdir()
        (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0")
        (ordner / "environ").write_bytes(b"PATH=/usr/bin\0TMUX=/tmp/t,1,9\0")
        (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid, "status": "idle"}), encoding="utf-8")

    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PLATZ", str(tmp_path / "platz.json"))
    monkeypatch.setattr(speicher, "_platte_pruefen", lambda **_k: (True, "Platte frei"))
    monkeypatch.setattr(speicher, "_platte_je_start", lambda _s: (True, "Platte frei"))
    monkeypatch.setattr(speicher, "sitzung_nach_name", lambda _n, _sock=None: ("/tmp/t", "0"))
    monkeypatch.setattr(speicher, "_laufende_grosse_runde", lambda: None)

    gerufen: list[int] = []

    def schaffen() -> int:
        gerufen.append(1)
        # Simuliert das Schließen einer fertigen stillen Session
        for teil in (proc / "201").iterdir():
            teil.unlink()
        (proc / "201").rmdir()
        return 1

    konfig = {"speicher": {"max_sessions": 6, "min_frei_mib": 0, "staffel_s": 0}}
    # Gegenprobe: --trocken darf platz_schaffen nie aufrufen
    assert speicher.cli(konfig, trocken=True, sitzung="spec-1", platz_schaffen=schaffen) == speicher.EXIT_FREMD_VOLL
    assert gerufen == []

    # Echtlauf: platz_schaffen räumt eine stille Session ab → Exit 0
    assert speicher.cli(konfig, trocken=False, sitzung="spec-1", platz_schaffen=schaffen) == 0
    assert gerufen == [1]
    assert "Speicher frei" in capsys.readouterr().out


@pytest.mark.braucht("bash", "posix")
def test_spawn_srv_wartepfad_verzoegerter_wache_start_exit_0_und_wartedatei(tmp_path: Path) -> None:
    """#460: ``spawn_srv.sh`` mit ``staffel_s=0``, ``leitstand.aktiv=true`` und leicht verzögert
    anlaufender ``wache.py`` bei fremd vollem Rechner: Exit 0, „wartet auf Platz“ samt Grund,
    ``.to-spawn/wartet-auf-platz-<S>`` gesetzt, kein „nicht gestartet“."""
    sockel = "/tmp/t460-verzoegert/default"
    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    # 24 fremde stille Sessions → Gesamtgrenze 24 voll (EXIT_FREMD_VOLL = 3)
    for pid in range(201, 225):
        ordner = proc / str(pid)
        ordner.mkdir()
        (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0")
        (ordner / "environ").write_bytes(f"PATH=/usr/bin\0TMUX={sockel},1,4\0".encode())
        (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid, "status": "idle"}), encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16777216 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8")
    repo, _welt, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": 6, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": True},
        },
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    aufrufe = bin_dir / "tmux_aufrufe.txt"
    fenster = bin_dir / "fenster.txt"
    sitzungen = bin_dir / "sitzungen.txt"
    sitzungen.write_text(f"spec-{e2.SPEC}\t{sockel}\t$7\n", encoding="utf-8")
    wache_py = bin_dir / "attrappe" / "skripte" / "wache.py"
    wache_py.parent.mkdir(parents=True, exist_ok=True)
    wache_py.write_text(_WACHE_ATTRAPPE, encoding="utf-8")
    # Simuliert echtes tmux: wache.py erscheint erst 0,6 s nach new-session in ps.
    tmux_bin = bin_dir / "tmux"
    tmux_bin.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}\n'
        'name=""; ort="$PWD"; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-n" ] && name="$a"; [ "$prev" = "-c" ] && ort="$a"; prev="$a"; done\n'
        'case "$1" in\n'
        f"  has-session) [ -s {str(fenster)!r} ] ;;\n"
        f"  list-windows) [ -s {str(fenster)!r} ] && cat {str(fenster)!r} ;;\n"
        f"  list-sessions) cat {str(sitzungen)!r} ;;\n"
        "  new-session|new-window)\n"
        f'    printf "%s\\n" "$name" >> {str(fenster)!r}\n'
        '    case "$name" in "wache "*)\n'
        f'      ( sleep 0.6; cd "$ort" && exec {sys.executable!r} {str(wache_py)!r} "${{name#wache }}" "$PPID" )'
        " </dev/null >/dev/null 2>&1 & ;;\n"
        "    esac\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux_bin.chmod(0o755)
    umgebung.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(bin_dir),
            "TO_SPAWN_SPEICHER_PROC": str(proc),
            "TO_SPAWN_SPEICHER_SESSIONS": str(sessions),
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PLATZ": str(tmp_path / "platz.json"),
            "TO_SPAWN_HAUSMEISTER_PLATTE": "0.5",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    umgebung.pop("TMUX", None)

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901"],
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
    warte_datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    assert lauf.returncode == 0, ausgabe
    assert "nicht gestartet" not in ausgabe and "nichts gestartet" not in ausgabe, ausgabe
    assert f"wache {e2.SPEC}" in gestartet, gestartet
    assert f"leitstand {e2.SPEC}" not in gestartet and "bau 901" not in gestartet, gestartet
    assert "wartet auf Platz:" in ausgabe and speicher.GESAMT_VOLL_TEXT in ausgabe, ausgabe
    assert warte_datei.is_file() and warte_datei.read_text(encoding="utf-8").strip() == "901", ausgabe
