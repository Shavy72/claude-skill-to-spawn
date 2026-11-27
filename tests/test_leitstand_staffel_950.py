"""#950: Leitstand-Loop Staffelwechsel an der Handoff-Grenze + Übergabe-Dateien blockieren Live nicht.

Weg-Tests ohne echte Claude-CLI:
1. Leitstand-Session überschreitet im Transkript die Handoff-Grenze → alte Session wird ruhig
   beendet und eine frische Session mit neuer ``--session-id`` startet weiter.
2. Klon mit ungetrackter Session-Übergabe-Datei (``.claude-handoff.md`` / ``.claude-handoff-*.md``) →
   Sauberkeits-Prüfung (``sofort.ungespeichert``) übergeht sie, ``komplett_lauf.komplett_einmal``
   und ``live_tuer.starte`` starten trotzdem; andere ungetrackte oder gestagte Dateien sperren weiter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import config, komplett_lauf, live_tuer, starter, waechter_lauf
from to_spawn.pruef import cache, sofort
from to_spawn.pruefplan import Moment

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture(scope="module")
def lf() -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location("leitstand_fenster_950", SKRIPTE / "leitstand_fenster.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        creationflags=OHNE_FENSTER,
    ).stdout.strip()


def _repo_mit_commit(tmp_path: Path) -> Path:
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.txt").write_text("stand 1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "-c", "user.name=Bau", "-c", "user.email=bau@example.invalid", "commit", "-q", "-m", "start")
    return repo


def _zaehl_befehl(zaehl: Path) -> str:
    code = f"import pathlib; pathlib.Path({str(zaehl)!r}).open('a', encoding='utf-8').write('ok\\n')"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


# ---------------------------------------------------------------- Teil 1: Leitstand-Staffelwechsel


def test_leitstand_sitzung_staffelwechsel_ueber_handoff_grenze(
    lf: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test: Leitstand-Session überschreitet Handoff-Grenze → neue Session startet und läuft weiter."""
    repo = _repo_mit_commit(tmp_path)
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    # #950: Transkript-Ordner zeigt ins temporäre Heim.
    transkript_basis = waechter_lauf.transkript_ordner(repo, heim=heim)
    transkript_basis.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(waechter_lauf, "transkript_ordner", lambda cwd, _h=None: transkript_basis)

    protokoll = tmp_path / "runden.jsonl"
    # Fake-Claude im Attrappen-Ordner (#1406): liest --session-id, schreibt in Runde 1 einen Kontext
    # über der Grenze ins Transkript und wartet; in Runde 2 schreibt er niedrigen Kontext und endet 0.
    fake_claude = Path(os.environ[starter.TEST_BIN_ENV]) / "fake_claude_950.py"
    fake_claude.write_text(
        "import json, pathlib, sys, time\n"
        f"prot = pathlib.Path({str(protokoll)!r})\n"
        f"tdir = pathlib.Path({str(transkript_basis)!r})\n"
        "args = sys.argv[1:]\n"
        "sid = args[args.index('--session-id') + 1] if '--session-id' in args else ''\n"
        "runde = len(prot.read_text(encoding='utf-8').splitlines()) + 1 if prot.exists() else 1\n"
        "with prot.open('a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'runde': runde, 'sid': sid, 'args': args}) + '\\n')\n"
        "if sid:\n"
        "    tokens = 250000 if runde == 1 else 10000\n"
        "    zeile = {\n"
        "        'type': 'assistant',\n"
        "        'message': {\n"
        "            'model': 'claude-sonnet-5',\n"
        "            'usage': {'input_tokens': tokens, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0},\n"
        "            'content': [{'type': 'text', 'text': 'ok'}],\n"
        "        },\n"
        "    }\n"
        "    (tdir / f'{sid}.jsonl').write_text(json.dumps(zeile) + '\\n', encoding='utf-8')\n"
        "if runde == 1:\n"
        "    time.sleep(30)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(lf.starter, "finde", lambda _name: sys.executable)
    orig_befehl = lf.sitzung_befehl
    monkeypatch.setattr(
        lf,
        "sitzung_befehl",
        lambda *a, **k: [sys.executable, str(fake_claude), *orig_befehl(*a, **k)[1:]],
    )

    ablage = lf.Ablage(repo=repo, spec="950")
    code = lf.sitzung(ablage, grenze=190_000, aufsicht_takt=0.05, ruhe_warte_max=1.0)
    assert code == 0

    laeufe = [json.loads(z) for z in protokoll.read_text(encoding="utf-8").splitlines()]
    assert len(laeufe) == 2, f"Erwartet 2 Runden (Staffelwechsel), bekam {len(laeufe)}: {laeufe}"
    assert laeufe[0]["sid"] and laeufe[1]["sid"]
    assert laeufe[0]["sid"] != laeufe[1]["sid"]


def test_leitstand_sitzung_ohne_grenze_kein_neustart(
    lf: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beendet sich die Leitstand-Session unterhalb der Grenze regulär, startet keine neue Runde."""
    repo = _repo_mit_commit(tmp_path)
    protokoll = tmp_path / "runden_normal.txt"
    fake_claude = Path(os.environ[starter.TEST_BIN_ENV]) / "fake_claude_normal_950.py"
    fake_claude.write_text(
        f"import pathlib, sys; pathlib.Path({str(protokoll)!r}).open('a').write('lauf\\n'); sys.exit(0)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(lf.starter, "finde", lambda _name: sys.executable)
    orig_befehl = lf.sitzung_befehl
    monkeypatch.setattr(
        lf,
        "sitzung_befehl",
        lambda *a, **k: [sys.executable, str(fake_claude), *orig_befehl(*a, **k)[1:]],
    )

    ablage = lf.Ablage(repo=repo, spec="950")
    code = lf.sitzung(ablage, grenze=190_000, aufsicht_takt=0.05)
    assert code == 0
    assert protokoll.read_text(encoding="utf-8").splitlines() == ["lauf"]


def test_leitstand_sitzung_attrappe_ohne_timeout_parameter(
    lf: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prozess-Attrappen mit ``wait(self)`` ohne ``timeout`` laufen durch (Vertrag #869/#1428)."""
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("", encoding="utf-8")

    class _FakeProc:
        pid = 12345

        def wait(self) -> int:
            return 0

    class _NullCtx:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(lf.starter, "finde", lambda _name: str(fake_claude))
    monkeypatch.setattr(lf.starter, "starte", lambda *_a, **_k: _FakeProc())
    monkeypatch.setattr(lf.sitzungsjob, "gebunden", lambda _proc: _NullCtx())

    assert lf.sitzung(lf.Ablage(repo=tmp_path, spec="950"), aufsicht_takt=0.05) == 0


def test_leitstand_anweisung_untersagt_claude_handoff_datei(lf: ModuleType) -> None:
    """Takt-Anweisung untersagt dem Leitstand-Loop das Schreiben von ``.claude-handoff*.md``."""
    text = lf.anweisung_text(
        "950",
        "python",
        "/skill/skripte/leitstand.py",
        "/repo/.to-spawn/leitstand",
        "https://claude.ai/artifact/Dmu7nZQH8E5XzCdFXq3TRi",
        seed_fehlt=False,
        mail_fehlt=False,
    )
    assert ".claude-handoff" in text


# ---------------------------------------------------------------- Teil 2: Sauberkeits-Prüfung vor Komplett/Live


def test_klon_mit_session_uebergabe_datei_komplett_und_live_starten_trotzdem(tmp_path: Path) -> None:
    """Weg-Test: ungetrackte ``.claude-handoff*.md`` im Klon blockiert weder Komplett-Prüfung noch Live."""
    klon = _repo_mit_commit(tmp_path)
    (klon / ".claude-handoff.md").write_text("# Übergabe\n", encoding="utf-8")
    (klon / ".claude-handoff-loop.md").write_text("# Übergabe Loop\n", encoding="utf-8")

    assert cache.ist_werkzeug_rest(klon, ".claude-handoff.md") is True
    assert cache.ist_werkzeug_rest(klon, ".claude-handoff-loop.md") is True
    assert sofort.ungespeichert(klon) == []

    zaehl_komplett = tmp_path / "komplett.txt"
    zaehl_live = tmp_path / "live.txt"
    konfig = config._mische(
        config.DEFAULTS,
        {
            "staging_start": "",
            "staging": {"url": "", "ohne": True},
            "pruefung": {"schnell": "true", "komplett": _zaehl_befehl(zaehl_komplett)},
            "live": {"befehl": _zaehl_befehl(zaehl_live)},
        },
    )

    erg = komplett_lauf.komplett_einmal(klon, 950, Moment.VOR_LIVE, konfig)
    assert erg.ok and erg.gelaufen, erg.grund
    assert zaehl_komplett.read_text(encoding="utf-8").splitlines() == ["ok"]

    code, zeilen = live_tuer.starte(klon, 950, konfig)
    assert code == 0, zeilen
    assert zaehl_live.read_text(encoding="utf-8").splitlines() == ["ok"]


def test_echter_schmutz_und_gestagte_handoff_datei_sperren_weiter(tmp_path: Path) -> None:
    """Andere ungetrackte Dateien und eine absichtlich gestagte ``.claude-handoff.md`` zählen weiter als schmutzig."""
    klon = _repo_mit_commit(tmp_path)
    (klon / "fremd.py").write_text("x = 1\n", encoding="utf-8")
    assert "fremd.py" in sofort.ungespeichert(klon)
    (klon / "fremd.py").unlink()

    (klon / ".claude-handoff.md").write_text("# Übergabe\n", encoding="utf-8")
    _git(klon, "add", "-f", ".claude-handoff.md")
    assert ".claude-handoff.md" in sofort.ungespeichert(klon)
