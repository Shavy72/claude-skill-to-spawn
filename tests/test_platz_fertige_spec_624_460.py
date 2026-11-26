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
def test_platz_schaffen_haelt_fertige_spec_unter_stille_schwelle(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda _s, _r, info, _sz: bool(info and info.aus_json and info.status == "idle" and schliessungen["n"] == 0),
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


# --- Nachfix Mini-Probe 6 (#624, #460): stand.json 'seit', Limit/Umfrage, TO_SPAWN_MASCHINE, Weg-Test ---

#: Echter tmux-Pane-Auszug von ``wache 41`` aus Mini-Probe 6 (26.11.2026): Wochenlimit-Hinweis
#: über leerem Eingabefeld bei ``status: idle`` und 491 min alter Session-JSON.
ECHTES_PANE_WACHE_41 = (
    "❯ Weiter als Bau-Aufseher Spec #41: Modell-Wechsel claude-opus-5-5[1m] →\n"
    "  claude-sonnet-5 wegen Nutzungs-Limit.\n"
    "  ⎿  You've hit your weekly limit · resets Nov 29, 11pm (Europe/Berlin) · out of\n"
    "     usage credits\n"
    "     /usage-credits to finish what you’re working on.\n"
    "\n"
    "                                          new task? /clear to save 133k tokens\n"
    + "─" * 80
    + "\n❯ \n"
    + "─" * 80
    + "\n  Sonnet 5 · 132.1k (13.0%) · 27m\n"
    "  📁 to-spawn-probe-5-mit · ⎇ main · 7d: 100%\n"
)

#: Echter Pane-Text mit Claude-Code-Feedback-Umfrage über dem Eingabefeld (UI-Rauschen).
ECHTES_PANE_MIT_UMFRAGE = (
    "● Spec #61 ist live und geschlossen.\n"
    "\n"
    "✻ Worked for 2m 59s · done 7:59 PM\n"
    "\n"
    "● How is Claude doing this session? (optional)\n"
    "  1: Bad    2: Fine   3: Good   0: Dismiss · Esc to cancel\n"
    "                                        new task? /clear to save 132.8k tokens\n"
    + "─" * 80
    + "\n❯ \n"
    + "─" * 80
    + "\n  Opus 5.5 · 132.8k (13.0%) · 3h52m\n"
)

ECHTES_PANE_OHNE_UMFRAGE = (
    "● Spec #61 ist live und geschlossen.\n"
    "\n"
    "✻ Worked for 2m 59s · done 7:59 PM\n" + "─" * 80 + "\n❯ \n" + "─" * 80 + "\n  Opus 5.5 · 132.8k (13.0%) · 3h52m\n"
)


def test_eintrag_seit_nur_bei_echtem_zustandswechsel_und_ohne_ui_rauschen(tmp_path: Path) -> None:
    """#624: ``stand.json``-``seit`` übernimmt ältere idle-``mtime`` auch bei bestehendem Eintrag
    ohne jüngeren Eingriff, ignoriert Feedback-Umfrage/``/clear``-Hinweis im Hash und schützt
    jüngere Eingriffe sowie echte Pane-Änderungen."""
    jetzt = time.time()
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp_path,
            tmux_socket="nie-benutzt",
            trocken=True,
            jetzt=lambda: jetzt,
        )
    )
    a.jetzt = jetzt
    schluessel = "spec-probe-5-ohne-61/wache 61"

    # (1) Flüchtiges UI-Rauschen (Umfrage + /clear-Hinweis) ändert pane_hash nicht.
    assert aufpasser.pane_hash(ECHTES_PANE_MIT_UMFRAGE) == aufpasser.pane_hash(ECHTES_PANE_OHNE_UMFRAGE)

    # (2) Bestehender Eintrag aus regulärem Aufpasser-Takt (9,5 min alt, ohne Eingriff),
    # reale Session-JSON-mtime liegt 90 min zurück → 'seit' wird auf die echte Stille gezogen.
    a.stand.fenster[schluessel] = {
        "hash": aufpasser.pane_hash(ECHTES_PANE_OHNE_UMFRAGE),
        "seit": jetzt - 9.5 * 60,
        "stufe": 0,
    }
    e = a.eintrag(schluessel, ECHTES_PANE_MIT_UMFRAGE, seit_hinweis=jetzt - 90 * 60)
    assert e["seit"] == pytest.approx(jetzt - 90 * 60, abs=1.0)

    # (3) Schutz: nach einem frischen Eingriff (stufe=1, eingriff vor 2 min) darf eine alte
    # Session-JSON-mtime 'seit' nie vor den Eingriff zurückziehen.
    a.stand.fenster[schluessel] = {
        "hash": aufpasser.pane_hash(ECHTES_PANE_OHNE_UMFRAGE),
        "seit": jetzt - 2 * 60,
        "stufe": 1,
        "eingriff": jetzt - 2 * 60,
    }
    e_eingriff = a.eintrag(schluessel, ECHTES_PANE_OHNE_UMFRAGE, seit_hinweis=jetzt - 90 * 60)
    assert e_eingriff["seit"] == pytest.approx(jetzt - 2 * 60, abs=1.0)

    # (4) Echte Pane-Änderung ohne neuere mtime (z. B. Tippen im Eingabefeld) setzt 'seit' auf jetzt
    # und hält es auch im Folgetakt stabil (kein Zurückschnappen auf alte mtime).
    a.stand.fenster[schluessel] = {
        "hash": aufpasser.pane_hash(ECHTES_PANE_OHNE_UMFRAGE),
        "seit": jetzt - 90 * 60,
        "stufe": 0,
    }
    geaendert = ECHTES_PANE_OHNE_UMFRAGE.replace("❯ \n", "❯ halb getippt\n")
    e_neu = a.eintrag(schluessel, geaendert, seit_hinweis=jetzt - 90 * 60)
    assert e_neu["seit"] == pytest.approx(jetzt, abs=1.0)
    a.jetzt = jetzt + 120
    e_folge = a.eintrag(schluessel, geaendert, seit_hinweis=jetzt - 90 * 60)
    assert e_folge["seit"] == pytest.approx(jetzt, abs=1.0)


def test_limit_und_umfrage_text_an_einer_stelle_als_nicht_arbeitend() -> None:
    """#624: Limit-Meldung (``wache 41``) und Feedback-Umfrage gelten in ``arbeitet_echt`` als
    nicht arbeitend; echte Arbeit (``busy``, ``esc to interrupt``, echte Rückfrage) bleibt geschützt."""
    assert aufpasser.arbeitet_echt(ECHTES_PANE_WACHE_41, status="idle") is False
    assert aufpasser.arbeitet_echt(ECHTES_PANE_MIT_UMFRAGE, status="idle") is False
    assert aufpasser.rueckfrage(ECHTES_PANE_MIT_UMFRAGE) is False
    assert aufpasser.arbeitet(ECHTES_PANE_MIT_UMFRAGE, status="idle") is False
    # Gegenproben: echte Arbeit / echte Rückfrage bleiben aktiv.
    assert aufpasser.arbeitet_echt(ECHTES_PANE_WACHE_41, status="busy") is True
    assert aufpasser.arbeitet_echt("✻ Denkt nach… (esc to interrupt)", status="idle") is True
    assert aufpasser.arbeitet_echt("Do you want to proceed?\nEsc to cancel", status="idle") is True


@pytest.mark.braucht("tmux")
def test_platz_schaffen_raeumt_fertige_spec_auch_nach_aufpasser_takt_und_mit_limit_text(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#624 (Mini-Probe-6-Befund):
    (1) Regulärer Aufpasser-Takt sieht fertige Fremd-Spec (``wache``/``leitstand``/``bau`` 90 min idle)
        zuerst — danach muss ``platz_schaffen()`` alle drei trotzdem schließen (kein ``seit = 9,5 min``).
    (2) ``wache`` mit echter Wochenlimit-Meldung im Pane (wie ``wache 41``) gilt bei ``status=idle``
        als nicht arbeitend und wird ebenfalls geschlossen."""
    _grenze(welt, monkeypatch, max_sessions=2)
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002,8861:8862,8871:8872")
    monkeypatch.setenv("GH_STUB_ZU", "8862,8872")

    sitzung_ohne = "spec-probe5-ohne-8861"
    _zweite_sitzung(
        welt,
        sitzung_ohne,
        "8861",
        "repo-probe5-ohne",
        [
            ("wache 8861", ""),
            ("leitstand 8861", f"{speicher.ANZEIGE_VARIABLE}=1"),
            ("bau 8862", ""),
        ],
    )
    sitzung_limit = "spec-probe5-limit-8871"
    _zweite_sitzung(
        welt,
        sitzung_limit,
        "8871",
        "repo-probe5-limit",
        [("wache 8871", "")],
    )
    _alle_sessions_altern(welt, 90 * 60)

    # Echte Wochenlimit-Meldung ins Pane von wache 8871 + vorbestehender stand.json-Eintrag
    # mit zu frischem 'seit' (9,5 min wie nach altem Aufpasser-Takt in Mini-Probe 6).
    a_takt = _aufpasser(welt)
    limit_fenster = a_takt.fenster(sitzung_limit, "8871")
    limit_ziele = {f.ziel for f in limit_fenster}

    def pane_text_hook(self: aufpasser.Aufpasser, ziel: str) -> str:
        if ziel in limit_ziele:
            return ECHTES_PANE_WACHE_41
        return self.tmux("capture-pane", "-p", "-t", ziel)

    monkeypatch.setattr(aufpasser.Aufpasser, "pane_text", pane_text_hook)

    # Vorbestehende stand.json mit 9,5 min altem 'seit' (simuliert Alt-Stand aus regulärem Takt).
    jetzt = time.time()
    stand_datei = welt.zustand / "stand.json"
    stand_daten = {"fenster": {}, "meldungen": {}, "starts": {}}
    for f in a_takt.fenster(sitzung_ohne, "8861"):
        stand_daten["fenster"][f"{sitzung_ohne}/{f.name}"] = {
            "hash": aufpasser.pane_hash(a_takt.pane_text(f.ziel)),
            "seit": jetzt - 9.5 * 60,
            "stufe": 0,
        }
    welt.zustand.mkdir(parents=True, exist_ok=True)
    stand_datei.write_text(json.dumps(stand_daten), encoding="utf-8")

    # Auch ein regulärer fenster_pruefen-Durchlauf auf wache 8861 darf 'seit' nicht zurücksetzen,
    # sondern übernimmt die 90 min alte Session-JSON-mtime.
    a_check = _aufpasser(welt)
    a_check.stand = a_check._stand_laden()
    a_check.jetzt = time.time()
    wache_8861 = next(f for f in a_check.fenster(sitzung_ohne, "8861") if f.ist_wache)
    a_check.fenster_pruefen(wache_8861, welt.tmp / "repo-probe5-ohne", set())
    assert (a_check.jetzt - a_check.stand.fenster[f"{sitzung_ohne}/wache 8861"]["seit"]) / 60 >= 80.0
    a_check._stand_schreiben()

    assert _aufpasser(welt).platz_schaffen() > 0
    time.sleep(0.6)
    assert _sitzung_fenster(sitzung_ohne) == [], welt.log()
    assert _sitzung_fenster(sitzung_limit) == [], welt.log()


@pytest.mark.braucht("bash", "posix")
def test_spawn_srv_exportiert_to_spawn_maschine_ins_tmux_fenster(tmp_path: Path) -> None:
    """#624: ``spawn_srv.sh`` reicht ``TO_SPAWN_MASCHINE`` über ``VORSPANN`` an jedes gestartete
    tmux-Fenster weiter (und lässt die Variable weg, wenn sie nicht gesetzt ist)."""
    maschine_pfad = tmp_path / "eigene-maschine.json"
    maschine_pfad.write_text('{"rolle": "server", "repos": []}', encoding="utf-8")
    repo, _welt, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": 6, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": False},
        },
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    aufrufe = bin_dir / "tmux_aufrufe.txt"
    fenster = bin_dir / "fenster.txt"
    sitzungen = bin_dir / "sitzungen.txt"
    sitzungen.write_text(f"spec-{e2.SPEC}\t/tmp/t624-env/default\t$1\n", encoding="utf-8")
    tmux_bin = bin_dir / "tmux"
    tmux_bin.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}\n'
        'name=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-n" ] && name="$a"; prev="$a"; done\n'
        'case "$1" in\n'
        f"  has-session) [ -s {str(fenster)!r} ] ;;\n"
        f"  list-windows) [ -s {str(fenster)!r} ] && cat {str(fenster)!r} ;;\n"
        f"  list-sessions) cat {str(sitzungen)!r} ;;\n"
        f'  new-session|new-window) printf "%s\\n" "$name" >> {str(fenster)!r} ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux_bin.chmod(0o755)
    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16777216 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8")
    umgebung.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_MASCHINE": str(maschine_pfad),
            "TO_SPAWN_TEST_BIN": str(bin_dir),
            "TO_SPAWN_SPEICHER_PROC": str(proc),
            "TO_SPAWN_SPEICHER_SESSIONS": str(sessions),
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PLATZ": str(tmp_path / "platz.json"),
            "TO_SPAWN_HAUSMEISTER_PLATTE": "0.5",
            "TO_SPAWN_ANLAUF_MAX_S": "1",
            "TO_SPAWN_ANLAUF_PRUEF_S": "0",
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
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    gestartet = aufrufe.read_text(encoding="utf-8")
    for name in (f"wache {e2.SPEC}", "bau 901"):
        zeile = next((z for z in gestartet.splitlines() if f"-n {name} " in z), "")
        assert zeile, gestartet
        assert f"TO_SPAWN_MASCHINE={maschine_pfad}" in zeile, zeile


@pytest.mark.braucht("bash", "posix")
def test_weg_460_kleine_grenze_wartet_und_aufseher_startet_ohne_ueberbuchung(tmp_path: Path) -> None:
    """#460 Weg-Test mit kleiner Session-Grenze (4 GB → 3 arbeitend / 6 gesamt):
    1. 6 fremde stille Sessions füllen die Gesamtgrenze → ``spawn_srv.sh`` liefert Exit 0,
       „wartet auf Platz:“ + Grund, öffnet ``wache <S>`` (mit ``TO_SPAWN_MASCHINE``), schreibt
       ``.to-spawn/wartet-auf-platz-<S>`` mit ``901,902`` und startet kein ``bau``-Fenster.
    2. Genau 1 Platz wird frei → Aufseher-Nachstart startet NUR ``bau 901`` (6/6, keine Überbuchung),
       ``902`` bleibt in ``wartet-auf-platz-<S>``.
    3. Ein weiterer Platz wird frei → Aufseher-Nachstart startet ``bau 902`` (6/6), Warte-Datei weg."""
    import importlib.util

    sockel = "/tmp/t460-weg-klein/default"
    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    # 4 GB MemTotal → rechner_grenze=3, gesamt_grenze=6. 6 fremde stille Sessions = voll (6/6).
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       4194304 kB\nMemAvailable:   3145728 kB\n", encoding="utf-8")
    for pid in range(201, 207):
        ordner = proc / str(pid)
        ordner.mkdir()
        (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0")
        (ordner / "environ").write_bytes(f"PATH=/usr/bin\0TMUX={sockel},1,9\0".encode())
        (sessions / f"{pid}.json").write_text(json.dumps({"pid": pid, "status": "idle"}), encoding="utf-8")

    maschine_pfad = tmp_path / "maschine-weg-460.json"
    maschine_pfad.write_text('{"rolle": "server", "repos": []}', encoding="utf-8")
    repo, welt_datei, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": 3, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": False},
        },
    )
    # Manifest + GitHub-Attrappen-Welt um zweites Ticket 902 ergänzen, damit schrittweises Nachstarten prüfbar ist.
    manifest_pfad = repo / "docs" / "agents" / "manifests" / f"spec-{e2.SPEC}.json"
    manifest_daten = json.loads(manifest_pfad.read_text(encoding="utf-8"))
    manifest_daten["tickets"]["902"] = dict(manifest_daten["tickets"]["901"], titel="Zweites Ticket")
    manifest_pfad.write_text(json.dumps(manifest_daten), encoding="utf-8")
    welt_daten = json.loads(welt_datei.read_text(encoding="utf-8"))
    welt_daten["issues"]["902"] = dict(welt_daten["issues"]["901"], number=902, title="Zweites Ticket")
    welt_datei.write_text(json.dumps(welt_daten), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    aufrufe = bin_dir / "tmux_aufrufe.txt"
    fenster = bin_dir / "fenster.txt"
    sitzungen = bin_dir / "sitzungen.txt"
    sitzungen.write_text(f"spec-{e2.SPEC}\t{sockel}\t$5\n", encoding="utf-8")
    wache_py = bin_dir / "attrappe" / "skripte" / "wache.py"
    wache_py.parent.mkdir(parents=True, exist_ok=True)
    wache_py.write_text(_WACHE_ATTRAPPE, encoding="utf-8")

    # Jeder gestartete Fenster-Start legt eine Claude-Session in der eigenen Sitzung ($5) an,
    # damit die Obergrenze 6/6 bei jedem weiteren Fenster echt greift.
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
        f"    pid=$((500 + $(wc -l < {str(fenster)!r})))\n"
        f'    mkdir -p {str(proc)!r}/"$pid"\n'
        f'    printf "/usr/bin/claude\\0" > {str(proc)!r}/"$pid/cmdline"\n'
        f'    printf "PATH=/usr/bin\\0TMUX={sockel},1,5\\0" > {str(proc)!r}/"$pid/environ"\n'
        '    case "$name" in "wache "*)\n'
        f'      ( cd "$ort" && exec {sys.executable!r} {str(wache_py)!r} "${{name#wache }}" "$$" )'
        " </dev/null >/dev/null 2>&1 & ;;\n"
        "    esac\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux_bin.chmod(0o755)
    # Aufseher-Attrappe bleibt für den ganzen Weg-Test an diesem pytest-Prozess gebunden.
    wache_py.write_text(_WACHE_ATTRAPPE.replace("int(sys.argv[2])", str(os.getpid())), encoding="utf-8")

    umgebung.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_MASCHINE": str(maschine_pfad),
            "TO_SPAWN_TEST_BIN": str(bin_dir),
            "TO_SPAWN_SPEICHER_PROC": str(proc),
            "TO_SPAWN_SPEICHER_SESSIONS": str(sessions),
            "TO_SPAWN_SPEICHER_MEMINFO": str(meminfo),
            "TO_SPAWN_SPEICHER_PLATZ": str(tmp_path / "platz.json"),
            "TO_SPAWN_HAUSMEISTER_PLATTE": "0.5",
            "TO_SPAWN_ANLAUF_MAX_S": "1",
            "TO_SPAWN_ANLAUF_PRUEF_S": "0",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    umgebung.pop("TMUX", None)

    def fremde_entfernen(pids: list[int]) -> None:
        for p in pids:
            for teil in (proc / str(p)).iterdir():
                teil.unlink()
            (proc / str(p)).rmdir()
            (sessions / f"{p}.json").unlink()

    def gesamt_claude() -> int:
        return len(list(proc.iterdir()))

    # 1. Start bei voller Gesamtgrenze 6/6 → wache öffnet, 901+902 warten, Exit 0.
    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901,902"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    ausgabe = lauf.stdout + lauf.stderr
    warte_datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    assert lauf.returncode == 0, ausgabe
    assert "nicht gestartet" not in ausgabe and "nichts gestartet" not in ausgabe, ausgabe
    assert "wartet auf Platz:" in ausgabe and speicher.GESAMT_VOLL_TEXT in ausgabe, ausgabe
    assert warte_datei.is_file() and warte_datei.read_text(encoding="utf-8").strip() == "901,902", ausgabe
    gestartet = aufrufe.read_text(encoding="utf-8")
    assert f"-n wache {e2.SPEC} " in gestartet and f"TO_SPAWN_MASCHINE={maschine_pfad}" in gestartet, gestartet
    assert "-n bau 901 " not in gestartet and "-n bau 902 " not in gestartet, gestartet

    spec_wache = importlib.util.spec_from_file_location("wache_weg_460", SKILL / "skripte" / "wache.py")
    assert spec_wache and spec_wache.loader
    wache_mod = importlib.util.module_from_spec(spec_wache)
    spec_wache.loader.exec_module(wache_mod)

    # 2. Noch voll (6 fremde + 1 wache = 7 >= 6) → Nachstart startet nichts.
    assert (
        wache_mod.wartende_nachstarten(warte_datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=1)
        == 1
    )
    assert warte_datei.read_text(encoding="utf-8").strip() == "901,902"

    # 3. Zwei fremde Sessions enden → 4 fremde + 1 wache = 5/6 (genau 1 Platz frei).
    # Nachstart darf NUR bau 901 starten (6/6, keine Überbuchung); 902 bleibt in der Warte-Datei.
    fremde_entfernen([201, 202])
    assert (
        wache_mod.wartende_nachstarten(warte_datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=1)
        == 1
    )
    gestartet = aufrufe.read_text(encoding="utf-8")
    assert "-n bau 901 " in gestartet and "-n bau 902 " not in gestartet, gestartet
    assert gesamt_claude() == 6, f"Überbuchung: {gesamt_claude()} > 6"
    assert warte_datei.is_file() and warte_datei.read_text(encoding="utf-8").strip() == "902"

    # 4. Eine weitere fremde Session endet → 3 fremde + 1 wache + 1 bau 901 = 5/6 (1 Platz frei).
    # Nachstart startet bau 902 (6/6) und räumt die Warte-Datei weg.
    fremde_entfernen([203])
    assert (
        wache_mod.wartende_nachstarten(warte_datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=2)
        == 1
    )
    gestartet = aufrufe.read_text(encoding="utf-8")
    assert "-n bau 902 " in gestartet, gestartet
    assert gesamt_claude() == 6, f"Überbuchung: {gesamt_claude()} > 6"
    assert not warte_datei.exists()
