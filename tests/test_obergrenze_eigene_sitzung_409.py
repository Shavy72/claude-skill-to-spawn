"""#409: Die Obergrenze ``speicher.max_sessions`` zählt nur Claude-Sessions des eigenen Laufs.

Probe-Lauf 24.11.: Leit-, Beobachter- und Fremd-Spec-Sessions derselben Maschine füllten die
Obergrenze 6 — Bau-Fenster warteten, ``spawn_srv.sh`` brach nach dem Aufseher ab, der Aufpasser
startete ausgefallene Fenster nicht nach („schon 6 Claude-Sessions (Obergrenze 6)“).

Soll: Gegen die Obergrenze zählen nur Claude-Prozesse, die in der tmux-Sitzung des Laufs
(``spec-<S>``) leben. Fremde Sessions bremsen nur über das Mindestmaß an freiem RAM.

Echt sind tmux (eigener Socket), ``speicher``, ``skripte/bau.py`` und die CLI
``to_spawn.py speicher``. Attrappe ist nur ``/proc`` (``TO_SPAWN_SPEICHER_PROC``): Ordner je PID
mit ``cmdline`` und ``environ`` — die Variable ``TMUX`` darin ist das, was tmux jedem Prozess
eines Fensters mitgibt (``<socket>,<server-pid>,<sitzungs-nr>``).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from to_spawn import speicher

pytestmark = pytest.mark.braucht("tmux")

WURZEL = Path(__file__).resolve().parent.parent
EIGEN = "spec-409"
FREMD = "leit-fremd"
_ZAEHLER = itertools.count()


class Abbruch(Exception):
    """Schlafen aufgerufen = das Fenster hätte gewartet."""


@dataclass
class Welt:
    tmp: Path
    proc: Path
    tmux_wert: dict[str, str]  # Sitzungsname → TMUX-Wert eines Prozesses darin
    socket: str


def _tmux(socket: str, *args: str) -> str:
    """Führt ``tmux -L <socket> …`` aus; bei Fehler mit ``stderr`` in der Meldung (#428)."""
    befehl = ["tmux", "-L", socket, *args]
    lauf = subprocess.run(befehl, capture_output=True, text=True, check=False, timeout=30)
    if lauf.returncode != 0:
        details = [teil.strip() for teil in (lauf.stderr, lauf.stdout) if teil and teil.strip()]
        anhang = f" — {' | '.join(details)}" if details else ""
        raise RuntimeError(f"tmux fehlgeschlagen (Exit {lauf.returncode}): {befehl!r}{anhang}")
    return lauf.stdout


def _tmux_beenden(socket: str, warte_s: float = 5.0) -> None:
    """Beendet den tmux-Server auf ``socket`` und wartet, bis er nicht mehr antwortet (#428)."""
    subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, text=True, check=False, timeout=30)
    ende = time.monotonic() + warte_s
    while time.monotonic() < ende:
        probe = subprocess.run(
            ["tmux", "-L", socket, "list-sessions"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if probe.returncode != 0 and "no sessions" not in probe.stderr:
            return
        time.sleep(0.05)


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    socket = f"obergrenze409-{os.getpid()}-{next(_ZAEHLER)}"
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps(["tmux", "-L", socket]))
    try:
        for name in (EIGEN, FREMD):
            _tmux(socket, "new-session", "-d", "-s", name, "sleep 600")
        werte: dict[str, str] = {}
        for zeile in _tmux(
            socket, "list-sessions", "-F", "#{session_name}\t#{socket_path},#{pid},#{session_id}"
        ).splitlines():
            name, wert = zeile.split("\t")
            werte[name] = wert.replace("$", "")
        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemAvailable:   33554432 kB\n", encoding="utf-8")
        proc = tmp_path / "proc"
        proc.mkdir()
        monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc))
        monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
        yield Welt(tmp_path, proc, werte, socket)
    finally:
        _tmux_beenden(socket)


def _claude(welt: Welt, pid: int, sitzung: str | None) -> None:
    """Ein Claude-Prozess; ``sitzung`` = tmux-Sitzung, ``None`` = außerhalb von tmux."""
    ordner = welt.proc / str(pid)
    ordner.mkdir()
    (ordner / "cmdline").write_bytes(b"/usr/bin/claude\0--resume\0abc\0")
    umgebung = [b"PATH=/usr/bin", b"HOME=/home/x"]
    if sitzung is not None:
        umgebung.append(f"TMUX={welt.tmux_wert[sitzung]}".encode())
    (ordner / "environ").write_bytes(b"\0".join(umgebung) + b"\0")


def _vier_fremde_zwei_eigene(welt: Welt) -> None:
    """Probe-Lauf im Kleinen: 3 fremde in einer anderen tmux-Sitzung, 1 ohne tmux, 2 eigene."""
    for pid in (201, 202, 203):
        _claude(welt, pid, FREMD)
    _claude(welt, 204, None)
    for pid in (301, 302):
        _claude(welt, pid, EIGEN)


_KONFIG = {"speicher": {"min_frei_mib": 2048, "max_sessions": 5, "staffel_s": 0}}


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> object:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    modulname = f"bau_409_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modulname, WURZEL / "skripte" / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(modulname, None)
    return modul


def _nie_schlafen(_s: float) -> None:
    raise Abbruch


# --- Bau-Fenster (bau.py) -----------------------------------------------------


def test_bau_fenster_startet_trotz_fremder_sessions(
    welt: Welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4 fremde + 2 eigene, Obergrenze 5: das Bau-Fenster in ``spec-409`` wartet nicht."""
    _vier_fremde_zwei_eigene(welt)
    repo = tmp_path / "repo"
    repo.mkdir()
    modul = _lade_bau(monkeypatch, repo)
    monkeypatch.setenv("TMUX", welt.tmux_wert[EIGEN])  # bau.py läuft im Fenster der eigenen Sitzung
    try:
        zyklen = modul.auf_speicher_warten(_KONFIG, schlafen=_nie_schlafen)  # type: ignore[attr-defined]
    except Abbruch:
        pytest.fail("bau 1 wartet auf Speicher, obwohl nur 2 eigene Sessions laufen (Obergrenze 5)")
    assert zyklen == 0


def test_bau_fenster_wartet_bei_voller_eigener_grenze(
    welt: Welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Obergrenze wirkt weiter — nur eben auf die eigene Sitzung."""
    for pid in range(301, 306):
        _claude(welt, pid, EIGEN)
    repo = tmp_path / "repo"
    repo.mkdir()
    modul = _lade_bau(monkeypatch, repo)
    monkeypatch.setenv("TMUX", welt.tmux_wert[EIGEN])
    with pytest.raises(Abbruch):
        modul.auf_speicher_warten(_KONFIG, schlafen=_nie_schlafen)  # type: ignore[attr-defined]


# --- Aufpasser-Weg (Sitzung per Name) ----------------------------------------


def test_platz_frei_mit_sitzungsname_zaehlt_nur_eigene(welt: Welt) -> None:
    _vier_fremde_zwei_eigene(welt)
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=welt.socket)
    assert frei, text
    assert f"2 Claude-Sessions in {EIGEN}" in text, text


def test_platz_frei_mit_sitzungsname_volle_eigene_grenze(welt: Welt) -> None:
    _vier_fremde_zwei_eigene(welt)
    for pid in (303, 304, 305):
        _claude(welt, pid, EIGEN)
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=welt.socket)
    assert not frei
    assert text == f"schon 5 Claude-Sessions in {EIGEN} (Obergrenze 5)"


def test_unlesbare_umgebung_zaehlt_als_eigen(welt: Welt) -> None:
    """Nicht nachweislich fremd (``environ`` fehlt/unlesbar) zählt mit — lieber vorsichtig."""
    for pid in (401, 402, 403, 404, 405):
        ordner = welt.proc / str(pid)
        ordner.mkdir()
        (ordner / "cmdline").write_bytes(b"claude\0")
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=welt.socket)
    assert not frei, text


def test_sitzung_noch_nicht_da_fremde_zaehlen_nicht(welt: Welt) -> None:
    """Erster Start einer Spec: ``spec-<S>`` gibt es noch nicht — fremde Sessions blockieren nicht."""
    _vier_fremde_zwei_eigene(welt)
    frei, text = speicher.platz_frei(_KONFIG, sitzung="spec-gibt-es-nicht", tmux_socket=welt.socket)
    assert frei, text


def test_ram_schutz_bleibt_fuer_fremde_sessions(welt: Welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fremde Sessions fressen RAM: das Mindestmaß greift weiter, maschinenweit."""
    _vier_fremde_zwei_eigene(welt)
    knapp = tmp_path / "meminfo-knapp"
    knapp.write_text("MemAvailable:   1228800 kB\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(knapp))
    frei, text = speicher.platz_frei(_KONFIG, sitzung=EIGEN, tmux_socket=welt.socket)
    assert not frei
    assert text.startswith("Speicher knapp: 1,2 GB frei"), text


def test_ohne_sitzung_bleibt_maschinenweit(welt: Welt) -> None:
    """Wer keine Sitzung nennt (z. B. von Hand außerhalb von tmux), zählt wie bisher alle."""
    _vier_fremde_zwei_eigene(welt)
    frei, text = speicher.platz_frei(_KONFIG)
    assert not frei
    assert text == "schon 6 Claude-Sessions (Obergrenze 5)"


# --- Start-Skript (to_spawn.py speicher --sitzung) ---------------------------


def test_cli_speicher_mit_sitzung_zaehlt_nur_eigene(welt: Welt, tmp_path: Path) -> None:
    _vier_fremde_zwei_eigene(welt)
    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(_KONFIG), encoding="utf-8")
    umgebung = {
        **os.environ,
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_HAUSMEISTER_PLATTE": "1",
    }
    lauf = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "speicher", "--sitzung", EIGEN, "--trocken"],
        capture_output=True,
        text=True,
        env=umgebung,
        timeout=120,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert f"in {EIGEN}" in lauf.stdout, lauf.stdout


def test_alle_zaehler_fragen_die_eigene_sitzung() -> None:
    """Starter, Aufpasser und Start-Skript nennen ihre Sitzung — keine eigene Zähl-Regel."""
    bau = (WURZEL / "skripte" / "bau.py").read_text(encoding="utf-8")
    wache = (WURZEL / "skripte" / "wache.py").read_text(encoding="utf-8")
    aufpasser = (WURZEL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    spawn_srv = (WURZEL / "skripte" / "spawn_srv.sh").read_text(encoding="utf-8")
    assert "pruefen=speicher.platz_frei_hier" in bau
    assert "pruefen=speicher.platz_frei_hier" in wache
    assert aufpasser.count("sitzung=") >= 1 and "speicher.platz_frei(konfig)" not in aufpasser
    assert 'speicher --sitzung "$SESSION"' in spawn_srv


# --- Fixture welt (#428) ------------------------------------------------------


def test_welt_eigener_socket_je_aufruf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#428: jeder ``welt``-Aufruf nutzt einen eigenen Socket — kein ``duplicate session`` im selben Worker."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    gen_a = welt.__wrapped__(a, monkeypatch)
    gen_b = welt.__wrapped__(b, monkeypatch)
    socket_a = ""
    socket_b = ""
    try:
        w_a = next(gen_a)
        socket_a = json.loads(os.environ["TO_SPAWN_TMUX"])[-1]
        w_b = next(gen_b)
        socket_b = json.loads(os.environ["TO_SPAWN_TMUX"])[-1]
        assert socket_a != socket_b
        assert w_a.socket == socket_a
        assert w_b.socket == socket_b
    finally:
        next(gen_b, None)
        next(gen_a, None)
    for socket in (socket_a, socket_b):
        probe = subprocess.run(
            ["tmux", "-L", socket, "list-sessions"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert probe.returncode != 0, f"tmux-Server auf {socket} läuft nach Teardown weiter: {probe.stdout}"


def test_tmux_fehler_enthaelt_stderr(welt: Welt) -> None:
    """#428: Scheitert ein tmux-Aufruf, steht ``stderr`` in der Fehlermeldung."""
    with pytest.raises(RuntimeError) as fehler:
        _tmux(welt.socket, "new-session", "-d", "-s", EIGEN, "sleep 600")
    assert "duplicate session" in str(fehler.value)


def test_welt_setup_fehler_meldet_stderr_und_raeumt_auf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#428: Scheitert das zweite ``new-session``, steht ``stderr`` in der Meldung und der Server wird beendet."""
    monkeypatch.setattr(sys.modules[__name__], "FREMD", EIGEN)
    gen = welt.__wrapped__(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError) as fehler:
        next(gen)
    socket = json.loads(os.environ["TO_SPAWN_TMUX"])[-1]
    probe = subprocess.run(
        ["tmux", "-L", socket, "list-sessions"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert "duplicate session" in str(fehler.value)
    assert probe.returncode != 0, f"tmux-Server auf {socket} läuft nach Setup-Fehler weiter: {probe.stdout}"
