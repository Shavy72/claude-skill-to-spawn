"""#436: Aufsicht erzwingt die Aufseher-Ablösung und kehrt nach dem Limit auf Opus zurück.

Weg-Tests über die echte CLI ``skripte/wache.py`` (Aufsicht, Bau-Log, Stand-Datei,
respawn-Tür echt). Gestellt sind nur ``claude`` (schreibt Transkript-Zeilen) und
``tmux`` (über die vorhandene Naht ``TO_SPAWN_TMUX``: nimmt getippten Text auf und
spielt den Aufseher, der Handoff + Start-Prompt schreibt). Die Tür-Tests geben ein
Fake-Werkzeug mit virtueller Uhr hinein — wie ``test_respawn_431*.py``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _ausfuehrbar,
    _text,
    _wache,
    _wache_welt,
    welt,
)

SKILL = Path(__file__).resolve().parent.parent
ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"
SPEC = "900"
AUSWEICH = "claude-sonnet-5"


def _aufrufe(welt: dict[str, Path]) -> list[list[str]]:
    datei = welt["tmp"] / "claude_aufrufe.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def _log_zeilen(welt: dict[str, Path], typ: str) -> list[dict[str, Any]]:
    from to_spawn import bau_log

    return [z for z in bau_log.lese(welt["repo"], SPEC) if z["typ"] == typ]


# --- 1. Limit vorbei → Modell wieder Opus ---------------------------------------

FAKE_CLAUDE_RUECKKEHR = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_PROTOKOLL"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
modell = args[args.index("--model") + 1]
if "--resume" in args:
    # Ausweich-Modell arbeitet weiter (bis die Aufsicht es ablöst); Haupt-Modell endet.
    if modell == os.environ["FAKE_AUSWEICH"]:
        time.sleep(float(os.environ.get("FAKE_SCHLAF", "8")))
    sys.exit(0)
sid = args[args.index("--session-id") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
with (ordner / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"content": "los"}}) + "\n")
    fh.flush()
    time.sleep(0.5)
    fh.write(os.environ["FAKE_LIMIT"] + "\n")
time.sleep(30)
"""


def _limit_mit_reset(in_sekunden: float) -> str:
    zeile: dict[str, Any] = json.loads(ECHT.read_bytes())
    zeile["quotaLimits"]["resetsAt"] = int(time.time() + in_sekunden)
    return json.dumps(zeile, ensure_ascii=False)


def test_limit_vorbei_modell_wieder_opus(welt: dict[str, Path]) -> None:
    """Haupt-Modell → Limit → Ausweich; nach dem Reset fährt der Aufseher wieder auf Opus."""
    env = _wache_welt(welt)
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_RUECKKEHR)
    env.update(
        {
            "FAKE_LIMIT": _limit_mit_reset(2),
            "FAKE_AUSWEICH": AUSWEICH,
            # Lange Schlafzeit: die Aufsicht beendet die Ausweich-Session ohnehin — unter Last
            # darf sie nicht von selbst enden, bevor die Rückkehr greift.
            "FAKE_SCHLAF": "45",
            "TO_SPAWN_RESET_PUFFER_S": "1",
        }
    )
    ergebnis = _wache(welt["repo"], env=env, timeout=120)
    assert ergebnis.returncode == 0, _text(ergebnis)

    aufrufe = _aufrufe(welt)
    haupt = aufrufe[0][aufrufe[0].index("--model") + 1]
    assert "opus" in haupt
    modelle = [a[a.index("--model") + 1] for a in aufrufe]
    assert modelle == [haupt, AUSWEICH, haupt], modelle
    sid = aufrufe[0][aufrufe[0].index("--session-id") + 1]
    assert aufrufe[2][aufrufe[2].index("--resume") + 1] == sid

    wechsel = _log_zeilen(welt, "waechter_modell")
    assert [(z["von"], z["nach"]) for z in wechsel] == [(haupt, AUSWEICH), (AUSWEICH, haupt)]
    assert wechsel[1]["grund"] == "Limit vorbei"


# --- 2. Kontext über Grenze → Ablösung ohne Zutun des Aufsehers -------------------

FAKE_CLAUDE_KONTEXT = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_PROTOKOLL"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
zaehler = Path(os.environ["FAKE_PROTOKOLL"]).with_suffix(".zaehler")
aufruf = int(zaehler.read_text()) + 1 if zaehler.is_file() else 1
zaehler.write_text(str(aufruf))
if aufruf > 1:
    sys.exit(0)  # Nachfolger: Prompt steht im Protokoll, mehr braucht der Test nicht.
sid = args[args.index("--session-id") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
zeile = {
    "type": "assistant",
    "message": {
        "id": "msg-436",
        "model": "claude-opus-5-5",
        "usage": {"input_tokens": 12, "cache_read_input_tokens": 60000,
                  "cache_creation_input_tokens": 500, "output_tokens": 20},
        "content": [{"type": "text", "text": "Tick 7 ok"}],
    },
}
with (ordner / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(zeile) + "\n")
time.sleep(float(os.environ.get("FAKE_SCHLAF", "100")))
"""

#: Gestelltes tmux: nimmt getippten Text auf; ein Handoff-Auftrag lässt den „Aufseher“
#: Handoff und Start-Prompt an die genannten Pfade schreiben (relativ zum Repo).
FAKE_TMUX = r"""#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
ordner = Path(os.environ["FAKE_TMUX_ORDNER"])
ordner.mkdir(parents=True, exist_ok=True)
if args[:1] == ["load-buffer"]:
    (ordner / "puffer.txt").write_text(sys.stdin.read(), encoding="utf-8")
elif args[:1] == ["paste-buffer"]:
    text = (ordner / "puffer.txt").read_text(encoding="utf-8")
    ziel = args[args.index("-t") + 1]
    with (ordner / "getippt.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ziel": ziel, "text": text}, ensure_ascii=False) + "\n")
    treffer = re.search(r"Handoff nach (\S+) und den Start-Prompt .*? nach (\S+) ", text)
    if treffer and os.environ.get("FAKE_TMUX_SCHREIBT", "1") == "1":
        handoff, start = (Path.cwd() / treffer[1], Path.cwd() / treffer[2])
        for pfad in (handoff, start):
            pfad.parent.mkdir(parents=True, exist_ok=True)
        handoff.write_text("HANDOFF-436: 901 zu, 902 läuft, nächster Tick 12:30.\n", encoding="utf-8")
        start.write_text("START-436: Weiter als Bau-Aufseher, erst capo.\n", encoding="utf-8")
sys.exit(0)
"""


def _abloese_welt(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    env = _wache_welt(welt)
    _ausfuehrbar(welt["tmp"] / "claude_bin" / "claude", FAKE_CLAUDE_KONTEXT)
    tmux = _ausfuehrbar(welt["tmp"] / "fake_tmux.py", FAKE_TMUX)
    heim = Path(env["HOME"])
    (heim / ".claude").mkdir(parents=True, exist_ok=True)
    # SSOT der Grenze: Handoff-Grenze für Opus 50k (Fake-Kontext 60,5k liegt drüber).
    (heim / ".claude" / "smart-zone.json").write_text(
        json.dumps({"haupt": {"handoff_k": 50}, "haupt_nicht_opus": {"handoff_k": 40}}),
        encoding="utf-8",
    )
    stand = welt["tmp"] / "stand"
    stand.mkdir()
    (stand / f"stand-{SPEC}.jsonl").write_text(
        json.dumps({"zeit": "12:00", "zeilen": ["902 läuft STANDMARKE-436"]}, ensure_ascii=False)
        + "\n"
        + json.dumps({"zeit": "12:30", "noop": True})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TMUX_PANE", raising=False)
    env.update(
        {
            "TO_SPAWN_TMUX": json.dumps([sys.executable, str(tmux)]),
            "FAKE_TMUX_ORDNER": str(welt["tmp"] / "tmux"),
            "TO_SPAWN_AUFSEHER_STAND": str(stand),
            "TMUX_PANE": "%77",
        }
    )
    return env


def _getippt(welt: dict[str, Path]) -> list[dict[str, str]]:
    datei = welt["tmp"] / "tmux" / "getippt.jsonl"
    if not datei.is_file():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def test_kontext_ueber_grenze_abloesung_ohne_zutun(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grenze erreicht → respawn-Tür tippt den Handoff-Auftrag, Nachfolger startet mit Handoff + Stand."""
    env = _abloese_welt(welt, monkeypatch)
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], env=env, timeout=150)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 90, "Ablösung kam nicht — Aufseher lief bis zum Ende"

    # Ablösung lief über die respawn-Tür: Handoff-Auftrag ins Pane des Aufsehers.
    getippt = _getippt(welt)
    assert len(getippt) == 1, getippt
    assert getippt[0]["ziel"] == "%77"
    assert "Ablösung dieser Session (respawn" in getippt[0]["text"]
    assert f"_waechter_{SPEC}.md" in getippt[0]["text"]

    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, aufrufe
    erster, zweiter = aufrufe
    assert "--resume" not in zweiter, "Nachfolger startet frisch"
    assert zweiter[zweiter.index("--model") + 1] == erster[erster.index("--model") + 1]
    prompt = zweiter[-1]
    assert "HANDOFF-436: 901 zu, 902 läuft" in prompt
    assert "STANDMARKE-436" in prompt
    assert "START-436: Weiter als Bau-Aufseher" in prompt

    zeilen = _log_zeilen(welt, "aufseher_abloesung")
    assert len(zeilen) == 1, zeilen
    assert zeilen[0]["exit"] == 0 and zeilen[0]["grenze"] == 50_000
    assert zeilen[0]["kontext"] >= 60_000


def test_ohne_tmux_keine_abloesung_nur_spur(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne ``TMUX_PANE`` (kein Bau-Server-Fenster) kein Tippen — nur eine Bau-Log-Zeile."""
    env = _abloese_welt(welt, monkeypatch)
    env.pop("TMUX_PANE")
    env["FAKE_SCHLAF"] = "3"
    ergebnis = _wache(welt["repo"], env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _getippt(welt) == []
    assert len(_aufrufe(welt)) == 1
    zeilen = _log_zeilen(welt, "aufseher_abloesung")
    assert len(zeilen) == 1 and zeilen[0]["exit"] is None, zeilen
    assert "tmux" in zeilen[0]["zeile"]


# --- 3. respawn-Tür für den Aufseher ----------------------------------------------


class _FakeWerkzeug:
    """Nur was die Aufseher-Tür braucht: tippen, Uhr (virtuell), schlafen."""

    def __init__(self, wurzel: Path, schreibt: bool) -> None:
        self.wurzel = wurzel
        self.schreibt = schreibt
        self.uhr = time.time()
        self.getippt: list[tuple[str, str]] = []

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))
        treffer = re.search(r"Handoff nach (\S+) und den Start-Prompt .*? nach (\S+) ", text)
        if treffer and self.schreibt:
            for rel, inhalt in ((treffer[1], "Handoff\n"), (treffer[2], "Start bitte\n")):
                pfad = self.wurzel / rel
                pfad.parent.mkdir(parents=True, exist_ok=True)
                pfad.write_text(inhalt, encoding="utf-8")

    def jetzt(self) -> float:
        return self.uhr

    def schlafen(self, s: float) -> None:
        self.uhr += s


def test_aufseher_tuer_exit_0_liefert_handoff_und_start(tmp_path: Path) -> None:
    from to_spawn import respawn_aufseher

    w = _FakeWerkzeug(tmp_path, schreibt=True)
    erg = respawn_aufseher.aufseher_abloesen(tmp_path, 900, "%5", werkzeug=w, warte_max=60)
    assert erg.exit == 0, erg.zeile
    assert erg.handoff is not None and erg.handoff.name.endswith("_waechter_900.md")
    assert erg.handoff.parent == tmp_path / "docs"
    assert erg.start_prompt == "Start bitte"
    assert [z for z, _ in w.getippt] == ["%5"]
    assert "\n" not in erg.zeile


def test_aufseher_tuer_exit_2_tippt_weiter_auftrag(tmp_path: Path) -> None:
    from to_spawn import respawn_aufseher

    w = _FakeWerkzeug(tmp_path, schreibt=False)
    erg = respawn_aufseher.aufseher_abloesen(tmp_path, 900, "%5", werkzeug=w, warte_max=10)
    assert erg.exit == 2, erg.zeile
    assert erg.handoff is None and erg.start_prompt == ""
    assert len(w.getippt) == 2
    assert w.getippt[1] == ("%5", respawn_aufseher.WEITER_AUFTRAG_AUFSEHER)
    assert "fehlt" in erg.zeile


def test_abloese_datei_nur_zwei_schreiber() -> None:
    """Kein dritter Weg: nur ``wache.py --abloesen`` und die Aufsicht (nach der respawn-Tür)."""
    treffer = sorted(
        str(p.relative_to(SKILL))
        for ordner in ("to_spawn", "skripte")
        for p in (SKILL / ordner).rglob("*.py")
        if "TO_SPAWN_WACHE_ABLOESUNG" in p.read_text(encoding="utf-8")
    )
    assert treffer == ["skripte/wache.py", "to_spawn/waechter_lauf.py"], treffer
    quelle = (SKILL / "to_spawn" / "waechter_lauf.py").read_text(encoding="utf-8")
    assert quelle.count("respawn_aufseher.aufseher_abloesen(") == 1
    assert quelle.count("def abloesung_schreiben(") == 1
    wache = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "waechter_lauf.abloesung_schreiben(" in wache
    assert ".write_text(json.dumps({\"handoff\"" not in wache


def test_bau_log_kennt_aufseher_abloesung() -> None:
    from to_spawn import bau_log

    assert "aufseher_abloesung" in bau_log.TYPEN


# --- 4. Ergänzungen aus dem Review (#436) ------------------------------------------


def _smart_zone(env: dict[str, str], haupt_k: int, nicht_opus_k: int) -> None:
    (Path(env["HOME"]) / ".claude" / "smart-zone.json").write_text(
        json.dumps({"haupt": {"handoff_k": haupt_k}, "haupt_nicht_opus": {"handoff_k": nicht_opus_k}}),
        encoding="utf-8",
    )


def test_kontext_unter_grenze_keine_abloesung(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kontext 60,5k unter der Opus-Grenze 100k → nichts getippt, keine Bau-Log-Zeile."""
    env = _abloese_welt(welt, monkeypatch)
    _smart_zone(env, 100, 40)
    env["FAKE_SCHLAF"] = "3"
    ergebnis = _wache(welt["repo"], env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _getippt(welt) == []
    assert len(_aufrufe(welt)) == 1
    assert _log_zeilen(welt, "aufseher_abloesung") == []


def test_nicht_opus_modell_nimmt_nicht_opus_grenze(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aufseher auf Nicht-Opus: ``haupt_nicht_opus.handoff_k`` (40k) greift, nicht ``haupt`` (100k)."""
    env = _abloese_welt(welt, monkeypatch)
    _smart_zone(env, 100, 40)
    ergebnis = _wache(welt["repo"], "--model", AUSWEICH, env=env, timeout=150)
    assert ergebnis.returncode == 0, _text(ergebnis)
    zeilen = _log_zeilen(welt, "aufseher_abloesung")
    assert len(zeilen) == 1, zeilen
    assert zeilen[0]["exit"] == 0 and zeilen[0]["grenze"] == 40_000
    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, aufrufe
    assert aufrufe[1][aufrufe[1].index("--model") + 1] == AUSWEICH


def test_abloese_datei_nur_ueber_abloesung_schreiben() -> None:
    """AST: jedes Dict mit Schlüssel ``"handoff"`` in wache.py/waechter_lauf.py liegt in
    ``abloesung_schreiben`` — kein zweiter Schreiber der Ablöse-Datei."""
    import ast

    for rel in ("skripte/wache.py", "to_spawn/waechter_lauf.py"):
        baum = ast.parse((SKILL / rel).read_text(encoding="utf-8"))
        for funktion in ast.walk(baum):
            if not isinstance(funktion, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for knoten in ast.walk(funktion):
                if isinstance(knoten, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "handoff" for k in knoten.keys
                ):
                    assert funktion.name == "abloesung_schreiben", (rel, funktion.name, knoten.lineno)


def test_abloesen_cli_schreibt_ueber_abloesung_schreiben(tmp_path: Path) -> None:
    """Weg-Test ``wache.py --abloesen``: die Datei trägt beide Felder von ``abloesung_schreiben``."""
    import os
    import subprocess

    handoff = tmp_path / "h.md"
    handoff.write_text("Stand\n", encoding="utf-8")
    ziel = tmp_path / "abloesung.json"
    ergebnis = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "wache.py"), SPEC, "--abloesen", str(handoff)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "TO_SPAWN_REPO": str(tmp_path), "TO_SPAWN_WACHE_ABLOESUNG": str(ziel)},
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert json.loads(ziel.read_text(encoding="utf-8")) == {"handoff": str(handoff), "start": ""}
    assert not ziel.with_name(ziel.name + ".neu").exists()
