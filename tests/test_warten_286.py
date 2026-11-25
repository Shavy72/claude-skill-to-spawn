"""#286: Abbruchfester Warte-Schritt (Anomalie 8).

Prüft alle vier Abnahme-Punkte von Issue #286:
1. Warte-Befehl wird von außen beendet → Neustart nach Pause, höchstens N Versuche,
   danach Eintrag „blockiert“ ins Bau-Log für den Aufseher, 0 Fragen an den Nutzer.
2. Zustand liegt in einer Datei (``.to-spawn/warten/<ticket>.json``): fällt der
   Hintergrund-Läufer von außen weg, geht der Stand nicht verloren; ein Weckruf
   (``to_spawn.py warten <N> --pruefen``) erkennt den Abbruch, startet nach Pause neu
   und meldet nach N Abbrüchen „blockiert“.
3. Jeder Abbruch wird mit Zeitstempel und freiem Speicher sowohl in der Zustandsdatei
   als auch im lokalen Geräte-Logbuch (Grundlage L7) festgehalten.
4. Linux-Gegenprobe mit echtem ``/proc/meminfo``, echtem ``SIGKILL``/``SIGTERM`` und
   echten Kind-/Hintergrund-Prozessen.
5. Start-Vorlage (``repo-scripts/_default.json``) nennt den abbruchfesten Warte-Weg;
   ``aufpasser.frage_am_prompt`` erkennt auch Fragen mit angehängter „Vorschlag:“-Zeile.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, bau_log, cli_parser


def _skript_schreiben(pfad: Path, quelltext: str) -> Path:
    pfad.write_text(quelltext, encoding="utf-8")
    return pfad


def _meminfo_schreiben(tmp_path: Path, frei_mib: int = 5800, gesamt_mib: int = 16384) -> Path:
    datei = tmp_path / "meminfo"
    datei.write_text(
        f"MemTotal:       {gesamt_mib * 1024} kB\n"
        f"MemAvailable:   {frei_mib * 1024} kB\n",
        encoding="utf-8",
    )
    return datei


def _warte_auf_datei(pfad: Path, frist_s: float = 10.0) -> str:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if pfad.is_file():
            text = pfad.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"Datei {pfad} erschien nicht binnen {frist_s} s")


def _warte_auf_stand(
    repo: Path, ticket: str, bedingung: Any, frist_s: float = 10.0
) -> dict[str, Any]:
    from to_spawn import warten

    pfad = warten.zustand_pfad(repo, ticket)
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        stand = warten._lies_zustand(pfad)
        if stand is not None and bedingung(stand):
            return stand
        time.sleep(0.05)
    raise AssertionError(f"Zustand {pfad} erfüllte Bedingung nicht binnen {frist_s} s")


def _warte_auf_pid_tot(pid: int, frist_s: float = 10.0) -> None:
    from to_spawn import warten

    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        if not warten._prozess_aktiv(pid):
            return
        time.sleep(0.05)
    raise AssertionError(f"Prozess {pid} lebt nach {frist_s} s noch")


# --- 1. Kind wird von außen beendet → Pause, Neustart, danach blockiert ----------------


def test_kind_einmal_beendet_startet_mit_pause_neu_und_wird_gruen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wird der Warte-Befehl von außen beendet, pausiert ``warten``, startet neu und meldet Erfolg."""
    from to_spawn import warten

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(_meminfo_schreiben(tmp_path, 5800, 32768)))
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    zaehler_datei = tmp_path / "versuch.txt"
    pid_datei = tmp_path / "kind1.pid"
    skript = _skript_schreiben(
        tmp_path / "warte_ziel.py",
        (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "z = Path(sys.argv[1])\n"
            "p = Path(sys.argv[2])\n"
            "n = int(z.read_text()) + 1 if z.exists() else 1\n"
            "z.write_text(str(n))\n"
            "if n == 1:\n"
            "    p.write_text(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            "print(f'ok im Versuch {n}')\n"
        ),
    )

    pausen: list[float] = []

    def killer() -> None:
        pid = int(_warte_auf_datei(pid_datei))
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
        os.kill(pid, sig)

    faden = threading.Thread(target=killer, daemon=True)
    faden.start()
    erg = warten.ausfuehren(
        repo,
        "1477",
        [sys.executable, str(skript), str(zaehler_datei), str(pid_datei)],
        max_versuche=3,
        pause_s=12.0,
        schlafen=pausen.append,
    )
    faden.join(timeout=5.0)

    assert erg.exit == warten.EXIT_OK, erg.zeile
    assert erg.status == "fertig"
    assert pausen == [12.0]
    assert int(zaehler_datei.read_text(encoding="utf-8")) == 2
    assert "?" not in erg.zeile

    stand = json.loads(warten.zustand_pfad(repo, "1477").read_text(encoding="utf-8"))
    assert stand["status"] == "fertig"
    assert stand["versuch"] == 2
    assert len(stand["abbrueche"]) == 1
    assert stand["abbrueche"][0]["frei_mib"] == 5800
    assert stand["abbrueche"][0]["ts"]

    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 1
    assert eintraege[0]["ereignis"] == "warte_abbruch"
    assert eintraege[0]["ticket"] == "1477"
    assert eintraege[0]["frei_mib"] == 5800
    assert eintraege[0]["gesamt_mib"] == 32768
    assert eintraege[0]["ts"]
    assert bau_log.lese(repo, "1477") == []  # nicht blockiert, da Versuch 2 klappte


def test_kind_n_mal_beendet_schreibt_blockiert_ins_bau_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nach N Abbrüchen von außen: keine weiteren Versuche, Eintrag ``blockiert`` im Bau-Log, 0 Fragen."""
    from to_spawn import warten

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(_meminfo_schreiben(tmp_path, 4100, 32768)))
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    skript = _skript_schreiben(
        tmp_path / "haengt.py",
        (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "d = Path(sys.argv[1])\n"
            "nr = len(list(d.glob('*.pid'))) + 1\n"
            "(d / f'{nr}.pid').write_text(str(os.getpid()))\n"
            "time.sleep(30)\n"
        ),
    )

    pausen: list[float] = []

    def killer() -> None:
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
        for nr in range(1, 4):
            pid = int(_warte_auf_datei(pid_dir / f"{nr}.pid"))
            os.kill(pid, sig)

    faden = threading.Thread(target=killer, daemon=True)
    faden.start()
    erg = warten.ausfuehren(
        repo,
        "1477",
        [sys.executable, str(skript), str(pid_dir)],
        max_versuche=3,
        pause_s=5.0,
        schlafen=pausen.append,
    )
    faden.join(timeout=5.0)

    assert erg.exit == warten.EXIT_BLOCKIERT, erg.zeile
    assert erg.status == "blockiert"
    assert pausen == [5.0, 5.0]  # 2 Pausen zwischen 3 Versuchen, keine 3. Pause nach dem Ende
    assert len(list(pid_dir.glob("*.pid"))) == 3
    assert "?" not in erg.zeile

    zeilen = bau_log.lese(repo, "1477")
    blockiert = [z for z in zeilen if z.get("typ") == "blockiert"]
    assert len(blockiert) == 1, zeilen
    assert "3" in str(blockiert[0].get("grund", ""))
    assert blockiert[0].get("frei_mib") == 4100

    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 3
    assert [e["versuch"] for e in eintraege] == [1, 2, 3]
    assert all(e["frei_mib"] == 4100 and e["ts"] for e in eintraege)


def test_regulaerer_anwendungsfehler_wird_nicht_als_abbruch_wiederholt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 (z. B. Prüfung rot) ist kein Abbruch von außen → kein Neustart, kein Logbuch-Abbruch."""
    from to_spawn import warten

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    skript = _skript_schreiben(tmp_path / "rot.py", "import sys; print('Schlange rot'); sys.exit(1)\n")
    pausen: list[float] = []
    erg = warten.ausfuehren(
        repo, "1477", [sys.executable, str(skript)], max_versuche=3, pause_s=5.0, schlafen=pausen.append
    )
    assert erg.exit == warten.EXIT_FEHLER
    assert erg.status == "fehlgeschlagen"
    assert pausen == []
    assert warten.logbuch_lesen(logbuch) == []
    assert bau_log.lese(repo, "1477") == []


# --- 2. Entkoppelter Hintergrund-Läufer + Weckruf (--pruefen) --------------------------


def test_hintergrund_laeufer_getoetet_weckruf_erkennt_und_startet_neu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fällt der Hintergrund-Läufer von außen weg, bleibt der Zustand in der Datei;
    ``pruefen`` (Weckruf) protokolliert den Abbruch, pausiert und startet den nächsten Versuch."""
    from to_spawn import warten

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(_meminfo_schreiben(tmp_path, 5200, 16384)))
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    zaehler = tmp_path / "count.txt"
    bereit = tmp_path / "ready1.txt"
    skript = _skript_schreiben(
        tmp_path / "bg_ziel.py",
        (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "z, b = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "n = int(z.read_text()) + 1 if z.exists() else 1\n"
            "z.write_text(str(n))\n"
            "if n == 1:\n"
            "    b.write_text(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            "print('fertig in runde 2')\n"
        ),
    )

    start = warten.starte_hintergrund(
        repo,
        "1477",
        [sys.executable, str(skript), str(zaehler), str(bereit)],
        max_versuche=3,
        pause_s=7.0,
    )
    assert start.exit == warten.EXIT_LAEUFT, start.zeile
    _warte_auf_datei(bereit)

    stand_vorher = _warte_auf_stand(
        repo, "1477", lambda s: bool(s.get("pid")) and bool(s.get("kind_pid"))
    )
    laeufer_pid = int(stand_vorher["pid"])
    kind_pid = int(stand_vorher["kind_pid"])
    assert warten._prozess_aktiv(laeufer_pid)

    # Läufer + Kind von außen hart beenden (Nachstellung Claude-Code-Speicherabbruch).
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    os.kill(laeufer_pid, sig)
    if warten._prozess_aktiv(kind_pid):
        os.kill(kind_pid, sig)
    _warte_auf_pid_tot(laeufer_pid)
    _warte_auf_pid_tot(kind_pid)

    # Weckruf fragt nach: erkennt den Wegfall, protokolliert Abbruch, pausiert, startet Versuch 2.
    pausen: list[float] = []
    nach_kill = warten.pruefen(repo, "1477", schlafen=pausen.append)
    assert nach_kill.exit == warten.EXIT_LAEUFT, nach_kill.zeile
    assert pausen == [7.0]

    # Versuch 2 läuft kurz durch und endet mit Exit 0.
    ende = time.monotonic() + 10.0
    erg = nach_kill
    while time.monotonic() < ende:
        erg = warten.pruefen(repo, "1477", schlafen=pausen.append)
        if erg.exit != warten.EXIT_LAEUFT:
            break
        time.sleep(0.1)

    assert erg.exit == warten.EXIT_OK, erg.zeile
    assert erg.status == "fertig"
    assert "fertig in runde 2" in erg.zeile

    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 1
    assert eintraege[0]["frei_mib"] == 5200
    assert eintraege[0]["ticket"] == "1477"


def test_hintergrund_laeufer_n_mal_getoetet_blockiert_beim_weckruf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stirbt der Hintergrund-Läufer N-mal von außen, meldet ``pruefen`` beim N-ten Mal ``blockiert``."""
    from to_spawn import warten

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(_meminfo_schreiben(tmp_path, 2900, 16384)))
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    skript = _skript_schreiben(tmp_path / "immer_lang.py", "import time; time.sleep(30)\n")
    pausen: list[float] = []
    start = warten.starte_hintergrund(
        repo, "1477", [sys.executable, str(skript)], max_versuche=2, pause_s=3.0
    )
    assert start.exit == warten.EXIT_LAEUFT
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)

    for versuch in (1, 2):
        # Warten bis kind_pid steht, dann Läufer und Kind hart beenden.
        stand = _warte_auf_stand(
            repo,
            "1477",
            lambda s, v=versuch: s.get("versuch") == v and bool(s.get("pid")) and bool(s.get("kind_pid")),
        )
        for p in (stand.get("pid"), stand.get("kind_pid")):
            if isinstance(p, int) and warten._prozess_aktiv(p):
                os.kill(p, sig)
                _warte_auf_pid_tot(p)
        erg = warten.pruefen(repo, "1477", schlafen=pausen.append)
        if versuch == 1:
            assert erg.exit == warten.EXIT_LAEUFT, erg.zeile
        else:
            assert erg.exit == warten.EXIT_BLOCKIERT, erg.zeile
            assert erg.status == "blockiert"

    assert pausen == [3.0]
    blockiert = [z for z in bau_log.lese(repo, "1477") if z.get("typ") == "blockiert"]
    assert len(blockiert) == 1
    # Erneutes --pruefen bleibt blockiert und schreibt keinen zweiten Bau-Log-Eintrag.
    nochmal = warten.pruefen(repo, "1477", schlafen=pausen.append)
    assert nochmal.exit == warten.EXIT_BLOCKIERT
    assert len([z for z in bau_log.lese(repo, "1477") if z.get("typ") == "blockiert"]) == 1
    assert len(warten.logbuch_lesen(logbuch)) == 2


# --- 3. CLI-Anbindung & explizites --abbruch -------------------------------------------


def test_cli_warten_registriert_und_abbruch_protokolliert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``to_spawn.py warten`` ist im CLI-Parser registriert und protokolliert ``--abbruch`` mit Grund."""
    from to_spawn import warten

    ap = cli_parser.baue_parser()
    args = ap.parse_args(["warten", "1477", "--pruefen"])
    assert args.befehl == "warten"
    assert args.ticket == 1477
    assert args.pruefen is True

    args_bg = ap.parse_args(["warten", "1477", "--hintergrund", "--", "python", "-c", "print(1)", "--flag"])
    assert args_bg.befehl == "warten"
    assert args_bg.hintergrund is True
    assert args_bg.kommando == ["python", "-c", "print(1)", "--flag"]

    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "geraet.jsonl"
    env = {
        **os.environ,
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_SPEICHER_MEMINFO": str(_meminfo_schreiben(tmp_path, 5734, 32768)),
        "TO_SPAWN_GERAETE_LOGBUCH": str(logbuch),
    }
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "warten",
            "1477",
            "--abbruch",
            "--max-versuche",
            "1",
            "--grund",
            "was stopped because the system is running low on memory",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == warten.EXIT_BLOCKIERT, lauf.stderr
    assert "?" not in lauf.stdout
    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 1
    assert "running low on memory" in eintraege[0]["grund"]
    assert eintraege[0]["frei_mib"] == 5734
    assert len([z for z in bau_log.lese(repo, "1477") if z.get("typ") == "blockiert"]) == 1


# --- 4. Linux-Gegenprobe mit echtem /proc/meminfo und echtem SIGKILL -------------------


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-Gegenprobe mit echtem /proc/meminfo und SIGKILL")
def test_linux_gegenprobe_echter_speicher_und_sigkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme 4: Gegenprobe auf Linux — echte /proc/meminfo-Messung (kein Stub) + echtes SIGKILL."""
    from to_spawn import warten

    monkeypatch.delenv("TO_SPAWN_SPEICHER_MEMINFO", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    logbuch = tmp_path / "linux_geraet.jsonl"
    monkeypatch.setenv("TO_SPAWN_GERAETE_LOGBUCH", str(logbuch))

    zaehler = tmp_path / "z.txt"
    pid_datei = tmp_path / "p.pid"
    skript = _skript_schreiben(
        tmp_path / "linux_probe.py",
        (
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "z, p = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "n = int(z.read_text()) + 1 if z.exists() else 1\n"
            "z.write_text(str(n))\n"
            "if n == 1:\n"
            "    p.write_text(str(os.getpid()))\n"
            "    time.sleep(30)\n"
            "print('linux-ok')\n"
        ),
    )

    def killer() -> None:
        os.kill(int(_warte_auf_datei(pid_datei)), signal.SIGKILL)

    faden = threading.Thread(target=killer, daemon=True)
    faden.start()
    erg = warten.ausfuehren(
        repo,
        "286",
        [sys.executable, str(skript), str(zaehler), str(pid_datei)],
        max_versuche=2,
        pause_s=0.1,
    )
    faden.join(timeout=5.0)

    assert erg.exit == warten.EXIT_OK, erg.zeile
    eintraege = warten.logbuch_lesen(logbuch)
    assert len(eintraege) == 1
    assert eintraege[0]["plattform"] == "linux"
    assert isinstance(eintraege[0]["frei_mib"], int) and eintraege[0]["frei_mib"] > 0
    assert isinstance(eintraege[0]["gesamt_mib"], int) and eintraege[0]["gesamt_mib"] > 0
    assert eintraege[0]["exit_code"] == -signal.SIGKILL


# --- 5. Start-Vorlage und Frage-Erkennung (Anomalie 8 Wortlaut) ------------------------


def test_start_vorlage_nennt_abbruchfestes_warten() -> None:
    """Start-Vorlage verweist Bau-Sessions auf ``to_spawn.py warten {N}`` statt freien Hintergrund-Befehl."""
    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))
    prompt = vorlage["prompt_template"]
    assert "to_spawn.py warten {N}" in prompt
    assert "--pruefen" in prompt


def test_aufpasser_erkennt_frage_mit_vorschlag_zeile_aus_anomalie_8() -> None:
    """Wortlaut aus Anomalie 8 (#286): Frage gefolgt von „Vorschlag: …“ am Prompt wird erkannt."""
    feld = "─" * 30 + "\n❯ \n" + "─" * 30 + "\n  Statuszeile\n"
    einzeilig = "Warten auf die Schlange wieder starten? Vorschlag: Ja, sobald wieder Speicher frei ist.\n"
    zweizeilig = "Warten auf die Schlange wieder starten?\nVorschlag: Ja, sobald wieder Speicher frei ist.\n"
    assert aufpasser.frage_am_prompt(einzeilig + feld)
    assert aufpasser.frage_am_prompt(zweizeilig + feld)
    assert not aufpasser.frage_am_prompt(einzeilig + "x\n" * 4 + feld)


@pytest.mark.parametrize(
    ("code", "plattform", "erwartet"),
    [
        (0, "linux", False),
        (1, "linux", False),
        (2, "linux", False),
        (-9, "linux", True),
        (-15, "linux", True),
        (137, "linux", True),
        (143, "linux", True),
        (0, "win32", False),
        (1, "win32", False),
        (15, "win32", True),
        (0xC000013A, "win32", True),
        (0xC0000017, "win32", True),
        (0x40010004, "win32", True),
    ],
)
def test_ist_abbruch_exit_linux_und_windows(code: int, plattform: str, erwartet: bool) -> None:
    """Signal- und harte Abbruch-Codes werden auf Linux und Windows von regulären Exits getrennt."""
    from to_spawn import warten

    assert warten.ist_abbruch_exit(code, plattform=plattform) is erwartet
