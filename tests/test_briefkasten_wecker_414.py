"""#414: Frage und Antwort im Briefkasten wecken den Empfänger — nicht erst der Takt.

Echt sind Briefkasten (Dateien im Hauptbaum), tmux-Tür (``tmux_aufruf``) und ``wache.main``.
Gestellt ist nur ``tmux`` über die vorhandene Naht ``TO_SPAWN_TMUX`` (Attrappe aus
``conftest.attrappe``): sie liefert Fensterliste und Bildschirm aus Dateien und schreibt jeden
eingefügten Text mit Ziel nach ``getippt.jsonl``.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from conftest import attrappe
from to_spawn import briefkasten, respawn

SPEC = 4140
TICKET = 4141
PANE = "%77"
LINIE = "─" * 40
#: Echtes Claude-Bild: Eingabefeld zwischen zwei Rahmenlinien, darunter die Statuszeile.
STILL = f"● Frage eingeworfen, warte.\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode · 1h 2m\n"
RUECKFRAGE = "Do you want to proceed?\n❯ 1. Yes\n"
HALB = f"● Frage eingeworfen, warte.\n\n{LINIE}\n❯ ich tippe gerade\n{LINIE}\n  ⏵⏵ auto mode\n"
ARBEITET = f"✻ Baut… (esc to interrupt)\n\n{LINIE}\n❯ \n{LINIE}\n  ⏵⏵ auto mode\n"
SHELL = "bau@server:~$ \n"

FAKE_TMUX = """
import json, os, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")  # tmux spricht UTF-8, auch unter Windows (#602)
sys.stdin.reconfigure(encoding="utf-8")
args = sys.argv[1:]
ordner = Path(os.environ["FAKE_TMUX_ORDNER"])
ordner.mkdir(parents=True, exist_ok=True)
with (ordner / "aufrufe.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
befehl = args[:1]
if befehl == ["list-windows"]:
    datei = ordner / "fenster.txt"
    print(datei.read_text(encoding="utf-8") if datei.is_file() else "", end="")
elif befehl == ["display-message"]:
    print((ordner / "pane.txt").read_text(encoding="utf-8"))
elif befehl == ["capture-pane"]:
    datei = ordner / "bild.txt"
    print(datei.read_text(encoding="utf-8") if datei.is_file() else "", end="")
elif befehl == ["load-buffer"]:
    (ordner / "puffer.txt").write_text(sys.stdin.read(), encoding="utf-8")
elif befehl == ["paste-buffer"]:
    text = (ordner / "puffer.txt").read_text(encoding="utf-8")
    ziel = args[args.index("-t") + 1]
    with (ordner / "getippt.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ziel": ziel, "text": text}, ensure_ascii=False) + "\\n")
sys.exit(0)
"""


def _schlaefer(ordner: Path, name: str) -> subprocess.Popen[bytes]:
    """Echter Prozess namens ``name`` (``claude`` → :func:`prozessbaum.ist_claude` erkennt ihn)."""
    skript = attrappe(ordner, name, "import time\ntime.sleep(120)\n")
    return subprocess.Popen([str(skript)])


def _pane(welt: dict[str, Path], pid: int | None = None, aktiv: float = 0.0) -> None:
    """Was ``display-message`` über das Pane sagt: Prozess-PID und ``window_activity``."""
    (welt["tmux"] / "pane.txt").write_text(f"{pid or welt['claude_pid']}\t{aktiv:.0f}", encoding="utf-8")


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """Hauptbaum mit Manifest der Spec, gestelltes tmux mit Fenster ``bau <TICKET>`` (still),
    in dem ein echter Prozess ``claude`` läuft und seit Langem nichts passiert ist."""
    haupt = tmp_path / "haupt"
    datei = haupt / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    datei.parent.mkdir(parents=True)
    datei.write_text(json.dumps({"spec": SPEC, "tickets": {str(TICKET): {"titel": "Probe"}}}), encoding="utf-8")
    ordner = tmp_path / "tmux"
    ordner.mkdir()
    (ordner / "fenster.txt").write_text(f"@1\twache {SPEC}\t0\n@2\tbau {TICKET}\t0\n", encoding="utf-8")
    (ordner / "bild.txt").write_text(STILL, encoding="utf-8")
    tmux = attrappe(tmp_path / "bin", "tmux", FAKE_TMUX)
    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([sys.executable, str(tmux)]))
    monkeypatch.setenv("FAKE_TMUX_ORDNER", str(ordner))
    monkeypatch.setattr(respawn, "TIPP_PAUSE_S", 0.0)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    claude = _schlaefer(tmp_path / "claude-bin", "claude")
    w: dict[str, Any] = {"haupt": haupt, "tmux": ordner, "claude_pid": claude.pid, "bin": tmp_path}
    _pane(w)
    try:
        yield w
    finally:
        claude.kill()
        claude.wait()


def _getippt(welt: dict[str, Path]) -> list[dict[str, str]]:
    datei = welt["tmux"] / "getippt.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _frage(welt: dict[str, Path]) -> str:
    return briefkasten.einwerfen(TICKET, "Zweig A oder B?", "A", None, ["Lastenheft"], hauptbaum=welt["haupt"])


def _wecker_modul() -> Any:
    # Im Test geladen: vor dem Fix fehlt das Modul — jeder Test hier ist rot, nicht übersprungen.
    return importlib.import_module("to_spawn.briefkasten_wecker")


def test_wache_frage_weckt_aufseher_ohne_takt(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test über ``wache.main``: eine Frage, die während des laufenden Aufsehers eingeht,
    landet binnen Sekunden als Weckruf im eigenen Pane des Aufsehers — kein Takt fällig."""
    wecker = _wecker_modul()
    monkeypatch.setattr(wecker, "TAKT_S", 0.2)
    quelle = Path(__file__).resolve().parent.parent / "skripte" / "wache.py"
    modul_spec = importlib.util.spec_from_file_location("wache_wecker_414", quelle)
    assert modul_spec is not None and modul_spec.loader is not None
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    @contextmanager
    def ohne_blick(*_a: Any, **_k: Any):  # noqa: ANN202 — Handoff-Blick ist nicht Thema hier
        yield None

    gesehen: list[list[dict[str, str]]] = []

    def fahre(**_kw: Any) -> int:
        frage_id = _frage(welt)  # Frage kommt erst, als der Aufseher schon läuft
        ende = time.monotonic() + 10
        while time.monotonic() < ende and not _getippt(welt):
            time.sleep(0.1)
        gesehen.append(_getippt(welt))
        gesehen.append([{"id": frage_id}])
        return 0

    monkeypatch.setattr(wache.handoff_blick, "laeuft", ohne_blick)
    monkeypatch.setattr(wache.waechter_lauf, "fahre", fahre)
    monkeypatch.setattr(wache, "REPO_ORDNER", welt["haupt"])
    monkeypatch.setattr(wache, "repo_slug_oder_abbruch", lambda: "o/r")
    monkeypatch.setattr(wache, "start_ordner", lambda: tmp_path)
    monkeypatch.setattr(wache, "auf_speicher_warten", lambda *a, **k: 0)
    monkeypatch.setattr(wache.config, "sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.config, "lade", lambda *a, **k: {})
    monkeypatch.setattr(wache.startklar, "gate", lambda *a, **k: 0)
    monkeypatch.setattr(wache.context_mode, "pruefen", lambda **k: tmp_path)
    monkeypatch.setattr(wache.vertrauen, "still_sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.nest, "git_hooks_einrichten", lambda *a, **k: None)
    monkeypatch.setattr(wache.tempfile, "mkdtemp", lambda **k: str(tmp_path))
    for name in ("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "BAU_UMZUG_DATEI", "TO_SPAWN_WACHE_SPEC", wache.ABLOESE_ENV):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("TMUX_PANE", PANE)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "argv", ["wache.py", str(SPEC)])

    assert wache.main() == 0
    getippt, (frage,) = gesehen
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage["id"] in getippt[0]["text"] and f"#{TICKET}" in getippt[0]["text"]


def test_antwort_weckt_bau_fenster_einmal(welt: dict[str, Path]) -> None:
    """Antwort des Aufsehers → Weckruf ins Fenster ``bau <N>``, genau einmal."""
    wecker = _wecker_modul()
    frage_id = _frage(welt)
    w = wecker.Wecker(SPEC, welt["haupt"], PANE)  # Frage war schon offen: Ausgangsstand
    w.einmal()
    assert _getippt(welt) == [], "schon offene Frage weckt beim Start nicht"
    briefkasten.antworten(TICKET, frage_id, "A", ["Beleg"], "passt", hauptbaum=welt["haupt"])
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [f"=spec-{SPEC}:@2"], getippt
    assert f"antwort {TICKET} --id {frage_id}" in getippt[0]["text"]


def test_frage_wartet_bei_rueckfrage_und_weckt_dann_einmal(welt: dict[str, Path]) -> None:
    """Rückfrage-Fenster im Aufseher-Pane → nichts tippen; danach still → ein Weckruf, kein zweiter."""
    wecker = _wecker_modul()
    w = wecker.Wecker(SPEC, welt["haupt"], PANE)
    frage_id = _frage(welt)
    (welt["tmux"] / "bild.txt").write_text(RUECKFRAGE, encoding="utf-8")
    w.einmal()
    assert _getippt(welt) == []
    (welt["tmux"] / "bild.txt").write_text(STILL, encoding="utf-8")
    w.einmal()
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [PANE], getippt
    assert frage_id in getippt[0]["text"]


def test_ohne_tmux_pane_kein_wecker_und_kein_tmux(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """PC ohne tmux (kein ``TMUX_PANE``): kein Faden, kein tmux-Aufruf — der Takt bleibt."""
    wecker = _wecker_modul()
    monkeypatch.delenv("TMUX_PANE", raising=False)
    with wecker.laeuft(SPEC, welt["haupt"]) as w:
        _frage(welt)
        assert w is None
    assert not (welt["tmux"] / "aufrufe.jsonl").exists()
    assert os.environ.get("TMUX_PANE") is None


def _antwort(welt: dict[str, Any], frage_id: str, ticket: int = TICKET) -> None:
    briefkasten.antworten(ticket, frage_id, "A", ["Beleg"], "passt", hauptbaum=welt["haupt"])


def _bild(welt: dict[str, Any], text: str) -> None:
    (welt["tmux"] / "bild.txt").write_text(text, encoding="utf-8")


def test_halber_text_im_eingabefeld_wird_nicht_ueberschrieben(welt: dict[str, Any]) -> None:
    """Mensch tippt gerade (oder Prompt-Vorschlag steht hinter ``❯``) → kein Weckruf; Feld leer → einer."""
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _frage(welt)
    _bild(welt, HALB)
    w.einmal()
    assert _getippt(welt) == []
    _bild(welt, STILL)
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [PANE]


def test_nur_shell_im_pane_kein_tippen(welt: dict[str, Any]) -> None:
    """Zwischen zwei Aufseher-Runden läuft im Pane nur die Shell → kein Weckruf (Bash würde ihn ausführen)."""
    shell = _schlaefer(welt["bin"] / "shell-bin", "bash")
    try:
        w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
        _frage(welt)
        _pane(welt, pid=shell.pid)
        for bild in (SHELL, STILL):  # auch ein stehengebliebenes Claude-Bild zählt nicht
            _bild(welt, bild)
            w.einmal()
        assert _getippt(welt) == []
        _pane(welt)
        w.einmal()
        assert [g["ziel"] for g in _getippt(welt)] == [PANE]
    finally:
        shell.kill()
        shell.wait()


def test_fenster_zu_frisch_aktiv_erst_spaeter(welt: dict[str, Any]) -> None:
    """``window_activity`` jünger als die Ruhe-Schwelle → warten; danach genau ein Weckruf."""
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _frage(welt)
    _pane(welt, aktiv=time.time())
    w.einmal()
    assert _getippt(welt) == []
    _pane(welt, aktiv=time.time() - 120)
    w.einmal()
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [PANE]


def test_tickende_statuszeile_zaehlt_nicht_als_aktivitaet(welt: dict[str, Any]) -> None:
    """tmux meldet wegen der Statuszeile ständig Aktivität: steht das Bild ohne Statuszeile
    seit der Ruhe-Schwelle gleich, gilt das Fenster trotzdem als ruhig."""
    modul = _wecker_modul()

    class Uhr(respawn.TmuxWerkzeug):
        t = 1_000_000.0

        def jetzt(self) -> float:
            return self.t

    uhr = Uhr()
    w = modul.Wecker(SPEC, welt["haupt"], PANE, werkzeug=uhr)
    _frage(welt)
    for sekunde in range(3):  # Statuszeile tickt, tmux-Aktivität ist immer „gerade eben“
        _bild(welt, STILL.replace("1h 2m", f"1h {sekunde}m"))
        _pane(welt, aktiv=uhr.t)
        w.einmal()
        if sekunde < 2:
            assert _getippt(welt) == [], sekunde
        uhr.t += modul.RUHE_S / 2 + 1
    assert [g["ziel"] for g in _getippt(welt)] == [PANE]


def test_bau_richtung_wartet_bei_arbeitendem_fenster(welt: dict[str, Any]) -> None:
    """Bau-Session arbeitet → Antwort weckt noch nicht; danach still → genau ein Weckruf."""
    frage_id = _frage(welt)
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _antwort(welt, frage_id)
    _bild(welt, ARBEITET)
    w.einmal()
    assert _getippt(welt) == []
    _bild(welt, STILL)
    w.einmal()
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [f"=spec-{SPEC}:@2"]


def test_bau_1_und_bau_10_werden_nicht_verwechselt(welt: dict[str, Any]) -> None:
    """``bau <N>`` wird exakt gelesen: die Antwort an #N landet nie bei ``bau <N>0``."""
    (welt["tmux"] / "fenster.txt").write_text(f"@3\tbau {TICKET}0\t0\n@2\tbau {TICKET}\t0\n", encoding="utf-8")
    frage_id = _frage(welt)
    w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
    _antwort(welt, frage_id)
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [f"=spec-{SPEC}:@2"]


def test_kaputtes_ticket_bricht_andere_weckrufe_nicht_ab(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wirft das Lesen der Antwort eines Tickets, wird das andere trotzdem geweckt."""
    modul = _wecker_modul()
    andere = TICKET + 1
    datei = welt["haupt"] / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    datei.write_text(
        json.dumps({"spec": SPEC, "tickets": {str(TICKET): {"titel": "A"}, str(andere): {"titel": "B"}}}),
        encoding="utf-8",
    )
    (welt["tmux"] / "fenster.txt").write_text(f"@2\tbau {TICKET}\t0\n@5\tbau {andere}\t0\n", encoding="utf-8")
    kaputt = _frage(welt)
    heil = briefkasten.einwerfen(andere, "C oder D?", "C", None, ["Lastenheft"], hauptbaum=welt["haupt"])
    w = modul.Wecker(SPEC, welt["haupt"], PANE)
    _antwort(welt, kaputt)
    _antwort(welt, heil, andere)
    echt = briefkasten.lesen

    def lesen(t: Any, fid: str, **kw: Any) -> Any:
        if str(t) == str(TICKET):
            raise ValueError("kaputte Antwort-Datei")
        return echt(t, fid, **kw)

    monkeypatch.setattr(modul.briefkasten, "lesen", lesen)
    w.einmal()
    assert [g["ziel"] for g in _getippt(welt)] == [f"=spec-{SPEC}:@5"]


def test_erster_blick_gescheitert_spaetere_frage_weckt_trotzdem(
    welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Scheitert der erste Blick beim Start, steht es im Log, und Fragen dieser Zeit wecken danach."""
    modul = _wecker_modul()
    echt = modul.manifest.lade_manifest

    def kaputt(*_a: Any, **_k: Any) -> Any:
        raise OSError("Manifest gerade nicht lesbar")

    monkeypatch.setattr(modul.manifest, "lade_manifest", kaputt)
    with caplog.at_level("WARNING", logger=modul.__name__):
        w = modul.Wecker(SPEC, welt["haupt"], PANE)
    assert "Manifest gerade nicht lesbar" in caplog.text
    frage_id = _frage(welt)
    monkeypatch.setattr(modul.manifest, "lade_manifest", echt)
    w.einmal()
    getippt = _getippt(welt)
    assert [g["ziel"] for g in getippt] == [PANE] and frage_id in getippt[0]["text"]


def test_tippen_eigener_puffer_und_kein_verschraenken(welt: dict[str, Any]) -> None:
    """Zwei Fäden im selben Prozess (Wecker + Ablösung): je Aufruf eigener Puffer, und
    load-buffer → paste-buffer → Enter laufen nie ineinander."""
    werkzeug = respawn.TmuxWerkzeug()

    def tippe(i: int) -> None:
        for k in range(4):
            werkzeug.tippen(PANE, f"text {i}-{k}")

    faeden = [threading.Thread(target=tippe, args=(i,)) for i in range(2)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    aufrufe = [json.loads(z) for z in (welt["tmux"] / "aufrufe.jsonl").read_text(encoding="utf-8").splitlines()]
    tipp = [a for a in aufrufe if a[0] in ("load-buffer", "paste-buffer", "send-keys")]
    assert len(tipp) == 3 * 8
    puffer = set()
    for i in range(0, len(tipp), 3):
        laden, einfuegen, enter = tipp[i : i + 3]
        assert (laden[0], einfuegen[0], enter[0]) == ("load-buffer", "paste-buffer", "send-keys"), tipp
        assert laden[2] == einfuegen[einfuegen.index("-b") + 1]
        puffer.add(laden[2])
    assert len(puffer) == 8, puffer
