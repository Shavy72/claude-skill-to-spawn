"""#259 (Restpunkte aus PR-233-Nach-Review): Fall (f) nach Umbenennen und Lauf-Zwischenspeicher.

Die Hauptpunkte aus #259 (WOW64-Erkennung über ``IsWow64Process2``, 32-Bit-PEB, fail-closed,
echter ``SysWOW64/cmd.exe``-Test und Griff-Leck nach ``DuplicateHandle``) sind bereits auf
``main`` (#705). Diese Datei prüft die beiden offenen Niedrig-Punkte:

1. ``hausmeister_windows.py`` (Fall (f)): Ein veralteter PEB-cwd-Text nach Umbenennen eines
   Ordners sperrt kein neues leeres Ziel am alten Pfad mehr, wenn die echte Ordner-Identität
   (``befund.drin is False``) bereits beweist, dass der Prozess woanders steht.
2. Lauf-Zwischenspeicher (``prozessbaum.arbeitsordner_lauf_cache``): Kennung und End-Pfad
   je Prozess werden in einem Hausmeister-Rundgang unter Windows einmal ermittelt und für
   weitere Zielordner desselben Laufs wiederverwendet; die Gegenprobe vor dem Löschen
   (``probe=True``) liest immer frisch. Linux bleibt unberührt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from to_spawn import hausmeister, hausmeister_windows, prozessbaum

WINDOWS = sys.platform == "win32"
SYSWOW64_CMD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SysWOW64" / "cmd.exe"


# --- 1. Fall (f): veralteter cwd-Text nach Umbenennen sperrt kein neues Ziel --------------


def test_259_neues_leeres_ziel_nach_umbenennen_bleibt_nicht_wegen_altem_cwd_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fall (f) (#259): ``drin=False`` aus der Griff-Identität gilt — veralteter PEB-Text
    auf denselben Pfad (oder einen darin nicht mehr existierenden Unterordner) sperrt nicht."""
    ziel = tmp_path / "wt-259"
    ziel.mkdir()
    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        lambda _ordner: {
            4242: prozessbaum.ArbeitsordnerBefund(str(ziel), False),
            4243: prozessbaum.ArbeitsordnerBefund(str(ziel / "weg-unterordner"), False),
        },
    )
    hausmeister_windows._kein_arbeitsordner_drin(ziel)


def test_259_echter_treffer_und_fehler_sperren_weiterhin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kontrolle: ``drin=True``, ``drin=None`` und ``text=None`` sperren unverändert."""
    ziel = tmp_path / "wt-259-kontrolle"
    ziel.mkdir()

    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        lambda _ordner: {4244: prozessbaum.ArbeitsordnerBefund(str(tmp_path / "anders"), True)},
    )
    with pytest.raises(hausmeister_windows.Bleibt) as exc_drin:
        hausmeister_windows._kein_arbeitsordner_drin(ziel)
    assert not exc_drin.value.fehler and "4244" in exc_drin.value.grund

    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        lambda _ordner: {4245: prozessbaum.ArbeitsordnerBefund(str(tmp_path), None, "Griff unlesbar")},
    )
    with pytest.raises(hausmeister_windows.Bleibt) as exc_unpruefbar:
        hausmeister_windows._kein_arbeitsordner_drin(ziel)
    assert exc_unpruefbar.value.fehler and "4245" in exc_unpruefbar.value.grund

    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        lambda _ordner: {4246: prozessbaum.ArbeitsordnerBefund(None, None, "PEB unlesbar")},
    )
    with pytest.raises(hausmeister_windows.Bleibt) as exc_unlesbar:
        hausmeister_windows._kein_arbeitsordner_drin(ziel)
    assert exc_unlesbar.value.fehler and "4246" in exc_unlesbar.value.grund


# --- 2. Zwischenspeicher je Lauf + frische Gegenprobe -------------------------------------


def _installiere_fake_prozesswelt(
    monkeypatch: pytest.MonkeyPatch,
    prozesse: dict[int, tuple[str, tuple[int, int], str | None, OSError | None]],
    ziele: dict[str, tuple[tuple[int, int], str]],
) -> dict[str, int]:
    """Stellt Momentaufnahme, PEB-Text, Griff, Kennung und End-Pfad nach und zählt Aufrufe."""
    zaehler = {"momentaufnahme": 0, "text": 0, "griff": 0, "kennung": 0, "endpfad": 0, "geschlossen": 0}
    monkeypatch.setattr(
        prozessbaum,
        "arbeitsordner_in_windows",
        getattr(prozessbaum.arbeitsordner_in_windows, "__wrapped__", prozessbaum.arbeitsordner_in_windows),
    )
    kernel = types.SimpleNamespace(
        CloseHandle=lambda _g: zaehler.__setitem__("geschlossen", zaehler["geschlossen"] + 1) or 1
    )
    monkeypatch.setattr(prozessbaum, "_win32", lambda: (kernel, types.SimpleNamespace()))

    def momentaufnahme() -> dict[int, tuple[int, int]]:
        zaehler["momentaufnahme"] += 1
        return {pid: (os.getpid(), 1000 + pid) for pid in prozesse}

    def text_lesen(pid: int) -> str | None:
        zaehler["text"] += 1
        return prozesse[pid][0]

    def griff_lesen(pid: int) -> int | None:
        zaehler["griff"] += 1
        return pid  # Griff-ID == PID für die Zuordnung in kennung/endpfad

    def kennung_lesen(griff: int) -> tuple[int, int]:
        zaehler["kennung"] += 1
        return prozesse[griff][1]

    def endpfad_lesen(griff: int) -> str:
        zaehler["endpfad"] += 1
        pfad, fehler = prozesse[griff][2], prozesse[griff][3]
        if fehler is not None:
            raise fehler
        assert pfad is not None
        return pfad

    monkeypatch.setattr(prozessbaum, "_momentaufnahme_windows", momentaufnahme)
    monkeypatch.setattr(prozessbaum, "_arbeitsordner_windows", text_lesen)
    monkeypatch.setattr(prozessbaum, "_arbeitsordner_griff_windows", griff_lesen)
    monkeypatch.setattr(prozessbaum, "_kennung", kennung_lesen)
    monkeypatch.setattr(prozessbaum, "_endpfad", endpfad_lesen)
    monkeypatch.setattr(prozessbaum, "_ordner_von", lambda pfad: ziele[pfad])
    monkeypatch.setattr(prozessbaum, "_ist_weg_windows", lambda _pid: False)
    monkeypatch.setattr(prozessbaum, "_ist_fremd_windows", lambda _pid: False)
    return zaehler


def test_259_lauf_cache_liest_prozesse_nur_einmal_je_lauf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Im Lauf-Zwischenspeicher werden Momentaufnahme, PEB, Griff, Kennung und End-Pfad
    über mehrere Zielordner nur einmal gelesen; nach dem Lauf liest der nächste Aufruf frisch."""
    prozesse: dict[int, tuple[str, tuple[int, int], str | None, OSError | None]] = {
        5001: (r"C:\dev\wt-1", (1, 101), r"\\?\C:\dev\wt-1", None),
        5002: (r"C:\dev\wt-2\sub", (1, 202), r"\\?\C:\dev\wt-2\sub", None),
        5003: (r"C:\dev\wt-3", (1, 303), r"\\?\C:\dev\wt-3", None),
    }
    ziele = {
        r"C:\dev\wt-1": ((1, 101), r"\\?\C:\dev\wt-1"),
        r"C:\dev\wt-2": ((1, 200), r"\\?\C:\dev\wt-2"),
        r"C:\dev\wt-3": ((1, 303), r"\\?\C:\dev\wt-3"),
    }
    zaehler = _installiere_fake_prozesswelt(monkeypatch, prozesse, ziele)

    with prozessbaum.arbeitsordner_lauf_cache():
        b1 = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-1")
        b2 = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-2")
        b3 = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-3")

    assert {p: b.drin for p, b in b1.items()} == {5001: True, 5002: False, 5003: False}
    assert {p: b.drin for p, b in b2.items()} == {5001: False, 5002: True, 5003: False}
    assert {p: b.drin for p, b in b3.items()} == {5001: False, 5002: False, 5003: True}
    assert zaehler["momentaufnahme"] == 1
    assert zaehler["text"] == 3
    assert zaehler["griff"] == 3
    assert zaehler["kennung"] == 3
    assert zaehler["endpfad"] == 3
    assert zaehler["geschlossen"] == 3

    # Nach dem Lauf ist der Cache leer — nächster Aufruf fragt wieder frisch.
    prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-1")
    assert zaehler["momentaufnahme"] == 2


def test_259_lauf_cache_aktiv_false_liest_frisch_fuer_gegenprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    """``arbeitsordner_lauf_cache(aktiv=False)`` umgeht den laufenden Cache (Gegenprobe vor
    dem Löschen sieht neu eingetretene Prozesse), ohne den äußeren Lauf-Cache zu zerstören."""
    prozesse: dict[int, tuple[str, tuple[int, int], str | None, OSError | None]] = {
        6001: (r"C:\anders", (1, 900), r"\\?\C:\anders", None),
    }
    ziele = {
        r"C:\dev\wt-1": ((1, 101), r"\\?\C:\dev\wt-1"),
        r"C:\dev\wt-2": ((1, 202), r"\\?\C:\dev\wt-2"),
    }
    zaehler = _installiere_fake_prozesswelt(monkeypatch, prozesse, ziele)

    with prozessbaum.arbeitsordner_lauf_cache():
        assert prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-1")[6001].drin is False
        # Prozess wechselt während des Laufs in wt-1.
        prozesse[6001] = (r"C:\dev\wt-1", (1, 101), r"\\?\C:\dev\wt-1", None)
        with prozessbaum.arbeitsordner_lauf_cache(aktiv=False):
            frisch = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-1")
            assert frisch[6001].drin is True
        assert zaehler["momentaufnahme"] == 2
        # Danach greift wieder der äußere Lauf-Cache für den nächsten Kandidaten.
        assert prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-2")[6001].drin is False
        assert zaehler["momentaufnahme"] == 2


def test_259_lauf_cache_fail_closed_bei_endpfad_fehler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheitert ``_endpfad`` beim Füllen des Caches, erkennt die Kennung direkte Treffer
    weiterhin, und andere Zielordner bleiben fail-closed (``drin=None``)."""
    prozesse: dict[int, tuple[str, tuple[int, int], str | None, OSError | None]] = {
        7001: (r"C:\dev\wt-1", (1, 101), None, OSError(5, "GetFinalPathNameByHandleW gescheitert")),
    }
    ziele = {
        r"C:\dev\wt-1": ((1, 101), r"\\?\C:\dev\wt-1"),
        r"C:\dev\wt-2": ((1, 202), r"\\?\C:\dev\wt-2"),
    }
    _installiere_fake_prozesswelt(monkeypatch, prozesse, ziele)

    with prozessbaum.arbeitsordner_lauf_cache():
        b1 = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-1")
        b2 = prozessbaum.arbeitsordner_in_windows(r"C:\dev\wt-2")

    assert b1[7001].drin is True
    assert b2[7001].drin is None and "GetFinalPathNameByHandleW" in b2[7001].grund


def test_259_hausmeister_alle_ordner_nutzt_cache_und_gegenprobe_frisch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hausmeister._alle_ordner`` aktiviert unter Windows den Lauf-Cache; ``_kein_prozess``
    mit ``probe=True`` umgeht ihn immer. Unter Linux bleibt der Cache aus."""
    wt1 = tmp_path / "wt-1"
    wt2 = tmp_path / "wt-2"
    wt1.mkdir()
    wt2.mkdir()
    beobachtet: list[tuple[str, bool]] = []

    monkeypatch.setattr(hausmeister, "_PLATTFORM", "win32")
    monkeypatch.setattr(hausmeister, "_kandidaten", lambda: [wt1, wt2])
    monkeypatch.setattr(hausmeister, "_frei_anteil", lambda: 0.5)
    monkeypatch.setattr(hausmeister, "_kennzahl_schreiben", lambda *_a, **_kw: None)
    monkeypatch.setattr(hausmeister.temp_ordner, "aufraeumen", lambda **_kw: None)
    monkeypatch.setattr(hausmeister, "_temp_inodes_im_takt", lambda: None)
    monkeypatch.setattr(hausmeister, "_zweige_im_takt", lambda *_a, **_kw: None)
    monkeypatch.setattr(hausmeister.uebergabe_sichern, "sichern", lambda **_kw: None)

    def fake_kein_prozess(ordner: Path) -> None:
        beobachtet.append((ordner.name, prozessbaum._ARBEITSORDNER_CACHE.get() is not None))

    monkeypatch.setattr(hausmeister_windows, "kein_prozess", fake_kein_prozess)
    monkeypatch.setattr(hausmeister_windows, "umbenennen_probe", lambda _o: None)

    def fake_aufraeumen(ordner: Path, **_kw: object) -> hausmeister.Ergebnis:
        hausmeister._kein_prozess(ordner, probe=False)
        hausmeister._kein_prozess(ordner, probe=True)
        return hausmeister.Ergebnis(ordner, "bleibt", "test", None, trocken=True)

    monkeypatch.setattr(hausmeister, "_aufraeumen", fake_aufraeumen)

    assert prozessbaum._ARBEITSORDNER_CACHE.get() is None
    hausmeister._alle_ordner(loeschen=False, jetzt=0.0)
    assert prozessbaum._ARBEITSORDNER_CACHE.get() is None
    assert beobachtet == [
        ("wt-1", True),
        ("wt-1", False),
        ("wt-2", True),
        ("wt-2", False),
    ]

    # Kontrolle Linux: Cache wird gar nicht gesetzt.
    linux_cache: list[bool] = []
    monkeypatch.setattr(hausmeister, "_PLATTFORM", "linux")
    monkeypatch.setattr(
        hausmeister,
        "_aufraeumen",
        lambda ordner, **_kw: (
            linux_cache.append(prozessbaum._ARBEITSORDNER_CACHE.get() is not None)
            or hausmeister.Ergebnis(ordner, "bleibt", "test", None, trocken=True)
        ),
    )
    hausmeister._alle_ordner(loeschen=False, jetzt=0.0)
    assert linux_cache == [False, False]


# --- 3. Echte Windows-Prozesse (laufen auf dem GitHub-Windows-Läufer) ---------------------


@pytest.mark.skipif(not WINDOWS, reason="Echter Windows-PEB-/Griff-Fall (f) nur unter Windows")
def test_259_echt_windows_neues_leeres_ziel_nach_umbenennen_frei(tmp_path: Path) -> None:
    """Fall (f) mit echtem Git-Bash-``sleep``: Ordner wird umbenannt, am alten Pfad entsteht
    ein neuer leerer Ordner. Der umbenannte Ordner bleibt gesperrt, das neue leere Ziel ist
    frei, obwohl der veraltete PEB-cwd-Text noch auf den alten Pfad zeigt."""
    ziel = tmp_path / "wt-259-fall-f"
    ziel.mkdir()
    umbenannt = tmp_path / "wt-259-fall-f-alt"
    proc = subprocess.Popen(["sleep", "60"], cwd=str(ziel), **prozessbaum.ohne_fenster())
    try:
        ende = time.monotonic() + 10
        while time.monotonic() < ende:
            cwd = prozessbaum._arbeitsordner_windows(proc.pid)
            if cwd and hausmeister_windows.pfad_norm(cwd) == hausmeister_windows.pfad_norm(ziel):
                break
            time.sleep(0.1)
        assert proc.poll() is None, "MSYS-sleep lief nicht an"
        os.rename(ziel, umbenannt)
        ziel.mkdir()

        # Der umbenannte Ordner (wo der Prozess wirklich steht) bleibt gesperrt.
        with pytest.raises(hausmeister_windows.Bleibt, match=str(proc.pid)):
            hausmeister_windows._kein_arbeitsordner_drin(umbenannt)

        # Das neue leere Ziel am alten Pfad ist frei, trotz veraltetem PEB-Text.
        befunde = prozessbaum.arbeitsordner_in_windows(ziel)
        assert proc.pid in befunde
        assert befunde[proc.pid].drin is False, befunde[proc.pid]
        assert befunde[proc.pid].text is not None
        assert hausmeister_windows.pfad_norm(befunde[proc.pid].text) == hausmeister_windows.pfad_norm(ziel)
        hausmeister_windows._kein_arbeitsordner_drin(ziel)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert not prozessbaum.lebt(proc.pid)


@pytest.mark.skipif(not WINDOWS, reason="Echter WOW64-Lauf-Cache-Test nur unter Windows")
def test_259_echt_windows_lauf_cache_mit_echtem_wow64_cmd(tmp_path: Path) -> None:
    """Lauf-Zwischenspeicher mit echtem 32-Bit-``SysWOW64/cmd.exe``: trifft Ziel 1, gibt
    Ziel 2 frei und liest die Prozess-Momentaufnahme im Lauf nur einmal."""
    if not SYSWOW64_CMD.is_file():
        pytest.skip(f"{SYSWOW64_CMD} fehlt auf diesem System")
    wt1 = tmp_path / "wt-259-cache-1"
    wt2 = tmp_path / "wt-259-cache-2"
    wt1.mkdir()
    wt2.mkdir()
    proc = subprocess.Popen(
        [str(SYSWOW64_CMD), "/Q", "/K"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        **prozessbaum.ohne_fenster(),
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(f'cd /d "{wt1}"\necho BEREIT259\n')
        proc.stdin.flush()
        ende = time.monotonic() + 15
        while time.monotonic() < ende:
            zeile = proc.stdout.readline()
            if not zeile or "BEREIT259" in zeile:
                break
        assert proc.poll() is None, "32-Bit-cmd lief nicht an"

        with prozessbaum.arbeitsordner_lauf_cache():
            bef1 = prozessbaum.arbeitsordner_in_windows(wt1)
            bef2 = prozessbaum.arbeitsordner_in_windows(wt2)
            assert proc.pid in bef1 and bef1[proc.pid].drin is True, bef1.get(proc.pid)
            assert proc.pid in bef2 and bef2[proc.pid].drin is False, bef2.get(proc.pid)
    finally:
        proc.kill()
        proc.wait(timeout=15)
        for strom in (proc.stdin, proc.stdout):
            if strom is not None:
                strom.close()
    assert not prozessbaum.lebt(proc.pid)
