"""#604: ``spawn_srv.sh`` — ereignisbasierter Anlauf, kurze Sperre, Exit 10, ohne flock, Rest-Befunde.

Deckt alle fünf Punkte aus Issue #604 ab:
1. Ereignis-Anlauf statt festem ``sleep 10`` (wartet auf gestartete tmux-Fenster, sonst sofort weiter).
2. Start-Sperre (``flock``) nur um Lesen/Schreiben der Warteliste und Start-Entscheidung, nicht über
   Staffel-Pausen gehalten.
3. Eine Stelle für alle Exit-Codes von ``spawn_srv.sh`` (``to_spawn/spawn_exit.py``); Aufrufer
   (Skill-Befehl ``spawn``, ``umzug``, Nachstarter in ``wache.py``) behandeln Exit 10 als
   „später erneut“, nicht als Fehler.
4. Ohne ``flock`` im PATH gibt ``spawn_srv.sh`` eine klare Hinweis-Zeile auf stderr aus.
5. Rest-Befunde:
   a) ``--umzug``-Fenster werden samt Referenz in der Warteliste vermerkt und nachgestartet.
   b) Ein vom Aufpasser gestarteter Aufseher kennt die Warteliste (``TO_SPAWN_WARTE_DATEI`` in
      ``wache_befehl`` und Rückfall in ``wache._nachstarter_starten``).
   c) Echter ``tmux``-Lauf (eigener Server per ``-L``) statt nur Attrappe.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import test_spec_rand_e2 as e2
from test_einrichtungs_ticket_410 import _aufbau
from test_obergrenze_rechner_460 import _WACHE_ATTRAPPE, _tmux_attrappe
from test_obergrenze_rechner_460_fix1_spawn import _claude, _spawn
from test_warten_auf_platz_560 import _gestartet, _platz_frei, _spawn_fremd_voll

from to_spawn import aufpasser, bash_wahl, spawn, umzug

SKILL = Path(__file__).resolve().parents[1]
SPAWN_SH = SKILL / "skripte" / "spawn_srv.sh"


def _wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_604", SKILL / "skripte" / "wache.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _umgebung_mit_speicher(
    tmp_path: Path,
    *,
    max_sessions: int = 6,
    staffel_s: int = 0,
    belegt: int = 0,
    belegt_eigen: int = 0,
) -> tuple[Path, dict[str, str], Path, Path]:
    """Repo + Konfig + gefälschte Speicher-Messwerte (wie ``_spawn``)."""
    sockel = "/tmp/t604-attrappe/default"
    proc = tmp_path / "proc"
    sessions = tmp_path / "sessions"
    proc.mkdir()
    sessions.mkdir()
    for pid in range(201, 201 + belegt):
        _claude(proc, sessions, pid, None)
    for pid in range(301, 301 + belegt_eigen):
        _claude(proc, sessions, pid, f"{sockel},1,7")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16777216 kB\nMemAvailable:   10485760 kB\n", encoding="utf-8")
    repo, _welt, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"max_sessions": max_sessions, "min_frei_mib": 0, "staffel_s": staffel_s},
            "leitstand": {"aktiv": False},
        },
    )
    (tmp_path / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bin" / "sitzungen.txt").write_text(
        f"spec-{e2.SPEC}\t{sockel}\t$7\n",
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
            "TO_SPAWN_HAUSMEISTER_PLATTE": "0.5",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    umgebung.pop("TMUX", None)
    return repo, umgebung, proc, sessions


# --- Punkt 1: Ereignis-Anlauf statt festem „sleep 10“ ---------------------------


@pytest.mark.braucht("bash", "posix")
def test_anlauf_wartet_auf_fenster_statt_fest_10s(tmp_path: Path) -> None:
    """Punkt 1: Fenster erscheinen nach kurzem Takt → sofort weiter, kein festes ``sleep 10``."""
    repo, umgebung, proc, _sessions = _umgebung_mit_speicher(tmp_path)
    zaehler = tmp_path / "list_polls.txt"
    fenster = tmp_path / "bin" / "fenster.txt"
    aufrufe = tmp_path / "bin" / "tmux_aufrufe.txt"
    tmux = tmp_path / "bin" / "tmux"
    # list-windows zeigt die gestarteten Fenster erst beim 2. Abruf (verzögertes Ereignis).
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}\n'
        'name=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "-n" ] && name="$a"; prev="$a"; done\n'
        'case "$1" in\n'
        f"  has-session) [ -s {str(fenster)!r} ] ;;\n"
        f"  list-sessions) cat {str(tmp_path / 'bin' / 'sitzungen.txt')!r} ;;\n"
        "  list-windows)\n"
        f"    n=$(($(cat {str(zaehler)!r} 2>/dev/null || echo 0) + 1))\n"
        f'    echo "$n" > {str(zaehler)!r}\n'
        f'    [ "$n" -ge 2 ] && [ -s {str(fenster)!r} ] && cat {str(fenster)!r}\n'
        "    ;;\n"
        "  new-session|new-window)\n"
        f'    printf "%s\\n" "$name" >> {str(fenster)!r}\n'
        "    ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    t0 = time.monotonic()
    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    dauer = time.monotonic() - t0
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "gestartet: bau 901" in ausgabe, ausgabe
    assert zaehler.is_file() and int(zaehler.read_text(encoding="utf-8").strip()) >= 2
    assert dauer < 8.0, f"Anlauf dauerte {dauer:.2f} s — wartet offenbar noch fest 10 s"


# --- Punkt 2: Sperre nicht über Staffel-Pausen gehalten -------------------------


@pytest.mark.braucht("bash", "posix")
def test_sperre_ist_waehrend_staffel_pause_frei(tmp_path: Path) -> None:
    """Punkt 2: während der Staffel-Pause (3 s) ist die Spec-Sperre frei — zweiter Lauf blockiert nicht."""
    import fcntl

    repo, umgebung, proc, _sessions = _umgebung_mit_speicher(tmp_path, staffel_s=3)
    aufrufe = _tmux_attrappe(tmp_path / "bin", proc, wache_prozess=True)
    sperre = repo / ".to-spawn" / f"spawn-{e2.SPEC}.sperre"
    # Erster Lauf startet wache <S>, dann 3 s Staffel-Pause, dann bau 901.
    erster = subprocess.Popen(
        [bash_wahl.bash(), str(SPAWN_SH), str(e2.SPEC), "--tickets", "901"],
        cwd=str(repo),
        env=umgebung,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        # Warten bis das erste Fenster (wache) gestartet wurde — direkt danach beginnt die 3-s-Pause.
        frist = time.monotonic() + 30
        while time.monotonic() < frist:
            if aufrufe.is_file() and f"wache {e2.SPEC}" in aufrufe.read_text(encoding="utf-8"):
                break
            assert erster.poll() is None, f"Erster Lauf endete zu früh: {erster.communicate()}"
            time.sleep(0.05)
        assert aufrufe.is_file() and f"wache {e2.SPEC}" in aufrufe.read_text(encoding="utf-8")
        # Während der Staffel-Pause (bevor bau 901 startet) muss die Sperre sofort frei sein!
        time.sleep(0.3)
        assert "bau 901" not in aufrufe.read_text(encoding="utf-8"), "Pause zu kurz zum Messen"
        assert sperre.is_file()
        fd = os.open(sperre, os.O_RDWR)
        try:
            erhalten = False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                erhalten = True
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                erhalten = False
        finally:
            os.close(fd)
        assert erhalten, "Start-Sperre blieb während der Staffel-Pause belegt (#604 Punkt 2)"
    finally:
        out, err = erster.communicate(timeout=30)
    assert erster.returncode == 0, out + err
    assert "bau 901" in aufrufe.read_text(encoding="utf-8")


# --- Punkt 3: Exit 10 — eine Stelle + Aufrufer behandeln 10 als „später erneut“ -


def test_exit_codes_eine_stelle() -> None:
    """Punkt 3: ``to_spawn/spawn_exit.py`` beschreibt alle Exit-Codes von ``spawn_srv.sh``."""
    from to_spawn import spawn_exit

    assert spawn_exit.EXIT_SPERRE == 10
    assert spawn_exit.ist_spaeter_erneut(10) is True
    assert spawn_exit.ist_spaeter_erneut(5) is False
    assert spawn_exit.ist_spaeter_erneut(0) is False
    for code in (0, 1, 2, 3, 4, 5, 6, 7, 9, 10):
        assert code in spawn_exit.BESCHREIBUNG
        assert spawn_exit.beschreibung(code)


def test_spawn_befehl_exit_10_kein_fehler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Punkt 3: ``spawn.spawn`` meldet Exit 10 als „später erneut“, nicht als Fehler."""
    from to_spawn import spawn_exit

    sauber = type("Bericht", (), {"sauber": True, "text": lambda self: "sauber"})()
    monkeypatch.setattr(spawn.manifest, "pruefe", lambda *_a, **_k: sauber)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        spawn.starter,
        "starte",
        lambda *_a, **_k: subprocess.CompletedProcess([], spawn_exit.EXIT_SPERRE),
    )
    with caplog.at_level(logging.INFO, logger="to_spawn.spawn"):
        rc = spawn.spawn(tmp_path, 1, {}, ziel="local", dry_run=False)
    assert rc == spawn_exit.EXIT_SPERRE
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text
    assert "später erneut" in caplog.text.lower()


def test_umzug_exit_10_wiederholt_und_kein_fehler(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Punkt 3: ``umzug.server_starten`` wiederholt bei Exit 10 und loggt keinen Fehler."""
    from to_spawn import spawn_exit

    monkeypatch.setattr(umzug, "SPERRE_PAUSE_S", 0.0)
    # 1. Erst Exit 10 (Sperre), dann Exit 0 → gelingt ohne Fehler-Log.
    antworten = iter(
        [
            subprocess.CompletedProcess([], spawn_exit.EXIT_SPERRE, "", "anderer Lauf"),
            subprocess.CompletedProcess([], 0, "gestartet", ""),
        ]
    )
    monkeypatch.setattr(umzug, "_ssh", lambda *_a, **_k: next(antworten))
    with caplog.at_level(logging.INFO, logger="to_spawn.umzug"):
        ok = umzug.server_starten("900", "901", ziel="bau-server", ordner="~/repo")
    assert ok is True
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text

    # 2. Bleibt Exit 10 → kein ERROR-Log, sondern „später erneut“.
    caplog.clear()
    monkeypatch.setattr(
        umzug,
        "_ssh",
        lambda *_a, **_k: subprocess.CompletedProcess([], spawn_exit.EXIT_SPERRE, "", "Sperre belegt"),
    )
    with caplog.at_level(logging.INFO, logger="to_spawn.umzug"):
        ok2 = umzug.server_starten("900", "901", ziel="bau-server", ordner="~/repo")
    assert ok2 is False
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text
    assert "später erneut" in caplog.text.lower()


def test_wache_nachstarter_exit_10_spaeter_erneut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Punkt 3: Nachstarter in ``wache.py`` behandelt Exit 10 als „später erneut“."""
    from to_spawn import spawn_exit

    wache = _wache()
    datei = tmp_path / ".to-spawn" / "wartet-auf-platz-900"
    datei.parent.mkdir(parents=True)
    datei.write_text("901\n", encoding="utf-8")
    monkeypatch.setattr(
        bash_wahl,
        "lauf",
        lambda *_a, **_k: subprocess.CompletedProcess([], spawn_exit.EXIT_SPERRE, "", "Sperre"),
    )
    with caplog.at_level(logging.INFO, logger="wache"):
        laeufe = wache.wartende_nachstarten(datei, 900, tmp_path, schlafen=lambda _s: None, max_laeufe=1)
    assert laeufe == 1
    assert datei.is_file()
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), caplog.text
    assert "später erneut" in caplog.text.lower()


# --- Punkt 4: Ohne „flock“ eine Hinweis-Zeile im Log ----------------------------


@pytest.mark.braucht("bash", "posix")
def test_ohne_flock_meldet_zeile_im_log(tmp_path: Path) -> None:
    """Punkt 4: fehlt ``flock`` im PATH, startet ``spawn_srv.sh`` mit einer Hinweis-Zeile auf stderr."""
    repo, umgebung, proc, _sessions = _umgebung_mit_speicher(tmp_path)
    _tmux_attrappe(tmp_path / "bin", proc, wache_prozess=True)
    ohne_flock = tmp_path / "ohne_flock"
    ohne_flock.mkdir()
    # Alle nötigen Werkzeuge aus dem System-PATH verlinken — ausdrücklich OHNE flock.
    for dir_text in umgebung["PATH"].split(os.pathsep):
        ordner = Path(dir_text)
        if not ordner.is_dir():
            continue
        for eintrag in ordner.iterdir():
            if eintrag.name == "flock":
                continue
            ziel = ohne_flock / eintrag.name
            if not ziel.exists():
                try:
                    ziel.symlink_to(eintrag)
                except OSError:
                    continue
    assert not (ohne_flock / "flock").exists()
    env_ohne = dict(umgebung, PATH=str(ohne_flock))
    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901"],
        cwd=str(repo),
        env=env_ohne,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    hinweise = [z for z in lauf.stderr.splitlines() if "flock fehlt" in z.lower()]
    assert len(hinweise) == 1, f"Erwartet genau eine flock-Hinweiszeile, bekam: {lauf.stderr!r}"


# --- Punkt 5a: „--umzug“-Fenster in der Warteliste vermerkt und nachgestartet ---


@pytest.mark.braucht("bash", "posix")
def test_umzug_fenster_in_warteliste_vermerkt_und_nachgestartet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Punkt 5a: ``--umzug`` bei vollem Rechner → Warteliste trägt Ref ein, Nachstarter startet mit ``--umzug``."""
    _lauf, umgebung, repo = _spawn_fremd_voll(tmp_path, monkeypatch)
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    datei.unlink()  # nur den Umzug-Fall prüfen
    ref = "ticket-901@deadbeef:docs/handoffs/HANDOFF_2026-11-25_901.md"
    # Aufseher-Prozess simulieren (wie beim laufenden Aufseher).
    wache_py = tmp_path / "bin" / "attrappe" / "skripte" / "wache.py"
    wache_proc = subprocess.Popen(
        [sys.executable, str(wache_py), str(e2.SPEC), str(os.getpid())],
        cwd=str(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        umzug_lauf = bash_wahl.lauf(
            [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache", "--umzug", ref],
            cwd=str(repo),
            env=umgebung,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        ausgabe = umzug_lauf.stdout + umzug_lauf.stderr
        assert umzug_lauf.returncode == 0 and "wartet auf Platz" in ausgabe, ausgabe
        assert datei.is_file(), "Warte-Datei fehlt trotz --umzug und laufendem Aufseher"
        inhalt = datei.read_text(encoding="utf-8")
        assert "901" in inhalt and ref in inhalt, inhalt

        # Platz wird frei → Nachstarter startet mit --umzug <ref> nach und räumt die Datei auf.
        _platz_frei(umgebung)
        wache = _wache()
        laeufe = wache.wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=2)
        assert laeufe == 1
        assert not datei.exists(), f"Warte-Datei blieb stehen: {datei.read_text(encoding='utf-8')!r}"
        aufrufe = _gestartet(tmp_path)
        assert "-n bau 901" in aufrufe and f"--umzug {ref}" in aufrufe, aufrufe
    finally:
        wache_proc.terminate()
        wache_proc.wait(timeout=5)


# --- Punkt 5b: Vom Aufpasser gestarteter Aufseher kennt die Warteliste ----------


def test_aufpasser_aufseher_kennt_warteliste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Punkt 5b: ``aufpasser.wache_befehl`` exportiert ``TO_SPAWN_WARTE_DATEI``; ``wache`` hat Rückfall."""
    from to_spawn import spawn_exit

    repo = tmp_path / "app"
    repo.mkdir()
    erwartet = spawn_exit.warte_datei(repo, 900)
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    befehl = a.wache_befehl(repo, "900", resume=None)
    assert f"{spawn_exit.WARTE_ENV}=" in befehl and str(erwartet) in befehl, befehl

    # Auch ohne gesetzte Umgebungsvariable (z. B. Direktstart) startet wache den Nachstarter-Faden.
    wache = _wache()
    monkeypatch.delenv(wache.WARTE_ENV, raising=False)
    gesehen: list[Path] = []
    monkeypatch.setattr(
        wache,
        "wartende_nachstarten",
        lambda d, *_a, **_k: (gesehen.append(d), time.sleep(0.5))[1],
    )
    faden = wache._nachstarter_starten(900, repo)
    assert faden is not None, "Aufseher ohne TO_SPAWN_WARTE_DATEI startete keinen Nachstarter"
    frist = time.monotonic() + 5
    while not gesehen and time.monotonic() < frist:
        time.sleep(0.05)
    assert gesehen == [erwartet], gesehen


# --- Punkt 5c: Echter tmux-Lauf (eigener Server per -L) -------------------------


@pytest.fixture()
def tmux_socket() -> Iterator[str]:
    """Eigener tmux-Server nur für diesen Test; am Ende geschlossen."""
    name = f"t604-{uuid.uuid4().hex[:8]}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], check=False, capture_output=True)


@pytest.mark.braucht("bash", "posix")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")
def test_echter_tmux_lauf_wartet_und_startet_nach(tmp_path: Path, tmux_socket: str) -> None:
    """Punkt 5c: echtes ``tmux`` (isolierter Socket) + echtes ``spawn_srv.sh`` + Nachstarter."""
    echtes_tmux = shutil.which("tmux")
    assert echtes_tmux is not None
    # Rechner fremd voll (12 belegt): Aufseher läuft im echten tmux-Fenster, Ticket 902 wartet.
    repo, umgebung, proc, sessions = _umgebung_mit_speicher(tmp_path, max_sessions=6, belegt=12)
    bin_ordner = tmp_path / "bin"
    huelle = bin_ordner / "tmux"
    huelle.write_text(
        f'#!/usr/bin/env bash\nexec {echtes_tmux!r} -L {tmux_socket!r} "$@"\n',
        encoding="utf-8",
    )
    huelle.chmod(0o755)
    # Echte tmux-Sitzung (exakter Name aus to_spawn.py sitzung) mit laufendem Aufseher-Prozess.
    sitzung = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "sitzung", str(e2.SPEC)],
        cwd=str(repo),
        env=dict(umgebung, TO_SPAWN_TMUX=""),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wache_py = bin_ordner / "attrappe" / "skripte" / "wache.py"
    wache_py.parent.mkdir(parents=True, exist_ok=True)
    wache_py.write_text(_WACHE_ATTRAPPE, encoding="utf-8")
    start_sitz = subprocess.run(
        [
            echtes_tmux,
            "-L",
            tmux_socket,
            "new-session",
            "-d",
            "-s",
            sitzung,
            "-n",
            f"wache {e2.SPEC}",
            "-c",
            str(repo),
            sys.executable,
            str(wache_py),
            str(e2.SPEC),
            str(os.getpid()),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert start_sitz.returncode == 0, start_sitz.stderr

    # Warten bis der echte Aufseher-Prozess in tmux angelaufen ist.
    frist = time.monotonic() + 10
    while time.monotonic() < frist:
        stand = subprocess.run(
            [sys.executable, str(SKILL / "skripte" / "sessions_stand.py"), str(e2.SPEC), "--alle"],
            cwd=str(repo),
            env=umgebung,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        if f"Spec #{e2.SPEC}" in stand and "aus" not in next(
            (z for z in stand.splitlines() if z.startswith(f"Spec #{e2.SPEC}")), "aus"
        ):
            break
        time.sleep(0.1)

    # 1. Rechner voll: geblocktes Ticket (e2.CHECKPOINT) wird in der Warteliste vermerkt, kein Fenster.
    ziel_ticket = str(e2.CHECKPOINT)
    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", ziel_ticket, "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0 and f"wartet auf Platz: bau {ziel_ticket}" in ausgabe, ausgabe
    datei = repo / ".to-spawn" / f"wartet-auf-platz-{e2.SPEC}"
    assert datei.read_text(encoding="utf-8").strip() == ziel_ticket
    fenster = subprocess.run(
        [echtes_tmux, "-L", tmux_socket, "list-windows", "-t", f"={sitzung}", "-F", "#W"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert f"bau {ziel_ticket}" not in fenster, fenster

    # 2. Platz frei: Nachstarter startet im echten tmux das Fenster ``bau <ziel_ticket>`` (geblockt → 0 Token).
    for pid in range(201, 213):
        for teil in (proc / str(pid)).iterdir():
            teil.unlink()
        (proc / str(pid)).rmdir()
        (sessions / f"{pid}.json").unlink()
    t0 = time.monotonic()
    laeufe = _wache().wartende_nachstarten(datei, e2.SPEC, repo, env=umgebung, schlafen=lambda _s: None, max_laeufe=2)
    dauer = time.monotonic() - t0
    assert laeufe == 1
    assert not datei.exists()
    fenster_nachher = subprocess.run(
        [echtes_tmux, "-L", tmux_socket, "list-windows", "-t", f"={sitzung}", "-F", "#W"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert f"bau {ziel_ticket}" in fenster_nachher, fenster_nachher
    assert dauer < 10.0, f"Echter tmux-Nachstart dauerte {dauer:.2f} s"
