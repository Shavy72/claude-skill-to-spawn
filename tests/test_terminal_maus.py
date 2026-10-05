"""Klick-Sperre nur für lokal gespawnte Sessions (Maus-Müll ``[555;10;1M`` im Eingabefeld).

Claude Code im Vollbild-Modus meldet jede Mausbewegung (DEC 1003). Unter Last trennt
Windows das ESC ab, Bewegungs-Codes landen als Text im Eingabefeld. Lokal gespawnte
Sessions (``bau <N>``, ``wache <S>``, ``leitstand <S>``) setzen deshalb selbst
``CLAUDE_CODE_DISABLE_MOUSE_CLICKS=1`` — nur auf Windows, der Bau-Server bleibt unverändert.

Rechenteil direkt (Plattform per ``sys.platform``), die Wege echt: ``bau.main`` und
``wache.main`` laufen im Prozess bis zum echten Prozessstart, ``leitstand.py sitzung`` als
echter Unterprozess. Statt ``claude`` startet jeweils ein Mess-Programm aus einem
vorangestellten PATH-Ordner und schreibt, was es in seiner Umgebung sieht.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

VARIABLE = "CLAUDE_CODE_DISABLE_MOUSE_CLICKS"
FEHLT = "<fehlt>"
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)
#: Was das Kind sehen muss: auf Windows die Sperre, sonst unverändert (keine Variable).
ERWARTET = "1" if sys.platform == "win32" else FEHLT
#: Session-Variablen, die das Mess-Programm zusätzlich in ``<Ergebnis>.env.json`` schreibt
#: (Transkript/Resume: Kind-Markierung weg, Persistenz erzwungen — wie ``bau.py``).
SESSION_VARIABLEN = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE")


# ---------------------------------------------------------------- Rechenteil


def test_windows_setzt_klick_sperre(monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import terminal_maus

    monkeypatch.setattr(sys, "platform", "win32")
    umgebung: dict[str, str] = {}
    terminal_maus.maus_ruhig(umgebung)
    assert umgebung == {VARIABLE: "1"}


def test_windows_ueberschreibt_abweichenden_wert(monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import terminal_maus

    monkeypatch.setattr(sys, "platform", "win32")
    umgebung = {VARIABLE: "0", "ANDERES": "x"}
    terminal_maus.maus_ruhig(umgebung)
    assert umgebung == {VARIABLE: "1", "ANDERES": "x"}


def test_linux_laesst_umgebung_unveraendert(monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import terminal_maus

    monkeypatch.setattr(sys, "platform", "linux")
    umgebung = {"ANDERES": "x"}
    terminal_maus.maus_ruhig(umgebung)
    assert umgebung == {"ANDERES": "x"}


def test_vorgabe_ist_prozess_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import terminal_maus

    # Erst setzen, dann löschen: so merkt monkeypatch den Ausgangszustand (auch „fehlt“)
    # und stellt ihn am Ende wieder her — maus_ruhig schreibt direkt in os.environ.
    monkeypatch.setenv(VARIABLE, "x")
    monkeypatch.delenv(VARIABLE)
    monkeypatch.setattr(sys, "platform", "win32")
    terminal_maus.maus_ruhig()
    assert os.environ.get(VARIABLE) == "1"


# ---------------------------------------------------------------- Mess-Programm statt claude


def _mess_claude(ordner: Path) -> Path:
    """Legt ein ausführbares ``claude`` in ``ordner`` an; es schreibt den Wert der Variable
    in die Datei aus ``MAUS_PROBE_DATEI``, die ``SESSION_VARIABLEN`` (``None`` = fehlt) in
    ``<Datei>.env.json`` und endet mit 0. Rückgabe: die Ergebnis-Datei."""
    ordner.mkdir(parents=True, exist_ok=True)
    ergebnis = ordner / "gesehen.txt"
    code = (
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        f"Path(os.environ['MAUS_PROBE_DATEI']).write_text(os.environ.get({VARIABLE!r}, {FEHLT!r}), encoding='utf-8')\n"
        "Path(os.environ['MAUS_PROBE_DATEI'] + '.env.json').write_text("
        f"json.dumps({{n: os.environ.get(n) for n in {SESSION_VARIABLEN!r}}}), encoding='utf-8')\n"
    )
    if sys.platform == "win32":
        (ordner / "mess_claude.py").write_text(code, encoding="utf-8")
        (ordner / "claude.cmd").write_text(f'@"{sys.executable}" "{ordner / "mess_claude.py"}"\r\n', encoding="utf-8")
    else:
        programm = ordner / "claude"
        programm.write_text(f"#!{sys.executable}\n{code}", encoding="utf-8")
        programm.chmod(0o755)
    return ergebnis


@pytest.fixture()
def saubere_umgebung(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``bau.main``/``wache.main`` schreiben direkt in ``os.environ`` — danach alles zurück.
    Die Variable wird vorher entfernt: die aufrufende Session kann sie schon tragen."""
    vorher = dict(os.environ)
    monkeypatch.delenv(VARIABLE, raising=False)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(vorher)


def _lade_skript(name: str, datei: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


# ---------------------------------------------------------------- Weg: bau.py


def test_bau_session_sieht_klick_sperre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saubere_umgebung: None
) -> None:
    """``bau.main`` bis zum echten Start über ``umzug.starte_mit_umzug_wache`` (Popen)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    ergebnis = _mess_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("MAUS_PROBE_DATEI", str(ergebnis))
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    bau = _lade_skript("bau_terminal_maus", SKRIPTE / "bau.py")
    # Außengrenzen wie in test_bau_ohne_prompt_431 (gh, Manifest, Sandbox, Speicher, Vertrauen);
    # Umgebungs-Vorbereitung, Staffel-Schleife und Prozessstart laufen echt.
    monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test/terminal-maus")
    monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(bau, "load_default", lambda *_a, **_k: {"prompt_template": "TEMPLATE {ticket}"})
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Titel"}))
    monkeypatch.setattr(bau, "build_prompt", lambda *_a, **_k: "GRUND-PROMPT")
    monkeypatch.setattr(bau, "known_skill_names", lambda: set())
    monkeypatch.setattr(bau, "mcp_catalog", dict)
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(bau.nest, "sandbox_start", lambda *_a, **_k: [])
    monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "anfrage.json")
    monkeypatch.setattr(sys, "argv", ["bau.py", "431", "--sofort", "--ohne-prompt"])

    assert bau.main() == 0
    assert ergebnis.read_text(encoding="utf-8") == ERWARTET


# ---------------------------------------------------------------- Weg: wache.py


def test_wache_session_sieht_klick_sperre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saubere_umgebung: None
) -> None:
    """``wache.main`` bis zum echten Start über ``waechter_lauf.fahre``."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, creationflags=OHNE_FENSTER)
    ergebnis = _mess_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("MAUS_PROBE_DATEI", str(ergebnis))
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test/terminal-maus")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.chdir(repo)
    wache = _lade_skript("wache_terminal_maus", SKRIPTE / "wache.py")
    try:
        wache.context_mode.pruefen(streng=True)
    except wache.context_mode.ContextModeFehlt:
        pytest.skip("context-mode fehlt auf diesem Rechner")
    # Außengrenzen: Startklar-Prüfung (venv/Schlüssel), Speicher-Sperre, Vertrauens-Dialog.
    monkeypatch.setattr(wache.startklar, "gate", lambda *_a, **_k: 0)
    monkeypatch.setattr(wache, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(wache.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    # Kurzer Prompt: das Mess-Programm ist unter Windows ein .cmd, cmd.exe kappt bei 8191 Zeichen.
    monkeypatch.setattr(wache, "prompt_bauen", lambda *_a, **_k: "PROMPT")
    monkeypatch.setattr(sys, "argv", ["wache.py", "900"])

    assert wache.main() == 0
    assert ergebnis.read_text(encoding="utf-8") == ERWARTET


# ---------------------------------------------------------------- Weg: leitstand.py sitzung


def test_leitstand_session_sieht_klick_sperre(tmp_path: Path) -> None:
    """``leitstand.py <S> sitzung`` als echter Unterprozess, startet ``claude`` über PATH."""
    repo = tmp_path / "repo"
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-376.json").write_text(
        json.dumps({"spec": 376, "feature": "Test", "tickets": {"377": {"title": "A"}}}), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, creationflags=OHNE_FENSTER)
    ergebnis = _mess_claude(tmp_path / "bin")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_") and k != VARIABLE}
    env.update(
        PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        MAUS_PROBE_DATEI=str(ergebnis),
        TO_SPAWN_REPO=str(repo),
        TO_SPAWN_WAECHTER_ORDNER=str(tmp_path / "waechter"),
        TO_SPAWN_GH_REPO="test-org/test-repo",
        PYTHONIOENCODING="utf-8",
        # Aus einer Claude-Session gestartet: ohne Gegenmaßnahme kein Transkript/Resume.
        CLAUDE_CODE_CHILD_SESSION="1",
    )
    env.pop("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", None)
    lauf = subprocess.run(  # noqa: S603 — fester Befehl
        [sys.executable, str(SKRIPTE / "leitstand.py"), "376", "sitzung"],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        creationflags=OHNE_FENSTER,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert ergebnis.read_text(encoding="utf-8") == ERWARTET
    gesehen = json.loads(Path(f"{ergebnis}.env.json").read_text(encoding="utf-8"))
    assert gesehen["CLAUDE_CODE_CHILD_SESSION"] is None
    assert gesehen["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"
