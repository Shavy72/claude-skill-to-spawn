"""Probesitz (#214) — echter Weg ohne Attrappe: Login-Abfrage und Sandbox-Sperre.

Übersprungen nur, wenn das Werkzeug auf der Maschine fehlt (``claude``, ``srt``/``bwrap``).
Der Wegwerf-Lauf (Punkte 2/5/6) und die Mail (Punkt 7) laufen hier bewusst nicht:
sie kosten Token/Mail und gehören zum Live-Probesitz der Hauptsession.
"""

from __future__ import annotations

import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import config, probesitz  # noqa: E402


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(arbeit), check=True, capture_output=True)
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "zustand"))
    return arbeit


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude nicht installiert")
def test_weg_p1_echter_login_status() -> None:
    erg = probesitz.pruefe_login()
    # Kein Urteil über den Login selbst — nur: die Prüfung liefert ein belastbares Ergebnis.
    assert erg.nummer == 1
    assert erg.grund
    if not erg.ok:
        assert erg.fehlt_noch


@pytest.mark.skipif(
    shutil.which("srt") is None or shutil.which("bwrap") is None,
    reason="srt/bwrap nicht installiert",
)
def test_weg_p4_echte_sandbox_sperrt_aussen(repo: Path) -> None:
    # Eigener Rahmenordner unter HOME (#260): zwei parallele Test-Suiten sehen sich beim
    # Aufräum-Check sonst gegenseitig ihre laufenden .probesitz-*-Ordner weg.
    with tempfile.TemporaryDirectory(prefix=".to-spawn-test-p4-", dir=Path.home()) as heim_str:
        heim = Path(heim_str)
        erg = probesitz.pruefe_sandbox(config.lade(repo), repo, home=heim)
        assert erg.ok, f"{erg.grund} · fehlt noch: {erg.fehlt_noch}"
        assert "innen Exit 0" in erg.beleg and "außen Exit" in erg.beleg
        assert not list(heim.glob(".probesitz-*")), "Temp-Ordner werden aufgeräumt"


@pytest.mark.skipif(
    shutil.which("srt") is None or shutil.which("bwrap") is None,
    reason="srt/bwrap nicht installiert",
)
def test_weg_p4_parallele_sandbox_pruefungen_stoeren_sich_nicht_260(repo: Path) -> None:
    """Zwei und mehr gleichzeitige Sandbox-Prüfungen auf demselben Home (#260).

    Ohne eigenen Sandbox-Heimordner je Lauf legt ``srt`` für fehlendes
    ``<home>/.claude/settings.local.json`` kurz eine leere Mount-Datei an und löscht sie
    beim Ende — der parallele Lauf bricht mit ``bwrap: Can't find source path …`` ab.
    """
    konfig = config.lade(repo)
    with tempfile.TemporaryDirectory(prefix=".to-spawn-test-260-", dir=Path.home()) as heim_str:
        heim = Path(heim_str)
        claude = heim / ".claude"
        claude.mkdir()
        # Wie auf dem Bau-Server: settings.json und Ordner da, nur settings.local.json fehlt.
        (claude / "settings.json").write_text("{}\n", encoding="utf-8")
        for unter in ("hooks", "skills", "agents"):
            (claude / unter).mkdir()

        def lauf(verzoegerung_s: float) -> probesitz.Ergebnis:
            if verzoegerung_s:
                time.sleep(verzoegerung_s)
            return probesitz.pruefe_sandbox(konfig, repo, home=heim)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            ergebnisse = list(pool.map(lauf, (0.0, 0.02, 0.04, 0.06, 0.08, 0.10)))

        fehler = [f"{e.grund} ({e.beleg})" for e in ergebnisse if not e.ok]
        assert not fehler, "Parallele Sandbox-Prüfungen gestört: " + " | ".join(fehler)
        assert not (claude / "settings.local.json").exists()
        assert not list(heim.glob(".probesitz-*")), "Temp-Ordner werden aufgeräumt"
