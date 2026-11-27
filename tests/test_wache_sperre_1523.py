"""#898 (aus App #1523): Aufseher-Sperre je Spec (``wache-<S>``) mit zwei echten Prozessen.

Prüft:
- Zwei parallele ``wache.py``-Prozesse für dieselbe Spec: der zweite endet sofort mit klarer
  Meldung und ohne Claude-Start (kein Doppelstart).
- Nach Ende des ersten Prozesses darf ein neuer Aufseher für dieselbe Spec wieder starten.
- Zwei verschiedene Specs laufen ungehindert parallel.
- Kontrollierte Ablöse-Ausnahme (``respawn_aufseher.ABLOESE_AUSNAHME_ENV``): genau eine
  Ablösung darf neben dem laufenden Aufseher starten und übernimmt die Haupt-Sperre nach
  dessen Ende; eine zweite parallele Ablösung wird abgewiesen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent

_KIND_SKRIPT = textwrap.dedent(
    """
    import importlib.util, os, sys, time
    from pathlib import Path

    skill = Path(os.environ["TEST_SKILL"])
    if str(skill) not in sys.path:
        sys.path.insert(0, str(skill))

    spec_obj = importlib.util.spec_from_file_location("wache_kind", skill / "skripte" / "wache.py")
    assert spec_obj is not None and spec_obj.loader is not None
    wache = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(wache)

    bereit = Path(os.environ["TEST_BEREIT"])
    stopp = Path(os.environ["TEST_STOPP"])
    gestartet = Path(os.environ["TEST_GESTARTET"])

    wache.startklar.gate = lambda *_a, **_k: 0
    wache.nest.git_hooks_einrichten = lambda *_a, **_k: None
    wache.starter.finde = lambda *_a, **_k: "claude-attrappe"
    wache.context_mode.pruefen = lambda **_k: Path(os.environ["TO_SPAWN_REPO"])
    wache.context_mode.server_eintrag = lambda *_a, **_k: {}
    wache.sitzung_filter.aufseher_skill_auswahl = lambda *_a, **_k: ([], {})
    wache.sitzung_filter.aufseher_mcp_auswahl = lambda *_a, **_k: ({}, False)
    wache.auf_speicher_warten = lambda *_a, **_k: 0
    wache._nachstarter_starten = lambda *_a, **_k: None
    wache.vertrauen.still_sicherstellen = lambda *_a, **_k: None
    wache.terminal_maus.maus_ruhig = lambda *_a, **_k: None

    import contextlib
    wache.handoff_blick.laeuft = lambda *_a, **_k: contextlib.nullcontext()
    wache.briefkasten_wecker.laeuft = lambda *_a, **_k: contextlib.nullcontext()

    def attrappe_fahre(**_kw):
        gestartet.write_text("1", encoding="utf-8")
        bereit.write_text(str(os.getpid()), encoding="utf-8")
        frist = time.monotonic() + 15.0
        while not stopp.exists() and time.monotonic() < frist:
            time.sleep(0.05)
        return 0

    wache.waechter_lauf.fahre = attrappe_fahre
    sys.argv = ["wache.py", os.environ["TEST_SPEC"]]
    sys.exit(wache.main())
    """
)


def _basis_env(tmp_path: Path, repo: Path, spec: int, name: str) -> tuple[dict[str, str], Path, Path, Path]:
    bereit = tmp_path / f"{name}.bereit"
    stopp = tmp_path / f"{name}.stopp"
    gestartet = tmp_path / f"{name}.gestartet"
    env = {
        **os.environ,
        "TEST_SKILL": str(SKILL),
        "TEST_SPEC": str(spec),
        "TEST_BEREIT": str(bereit),
        "TEST_STOPP": str(stopp),
        "TEST_GESTARTET": str(gestartet),
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_GH_REPO": "test/repo",
        "TO_SPAWN_START_SPERREN": str(tmp_path / "sperren"),
        "TO_SPAWN_TEMP_ORDNER": str(tmp_path / "temp"),
    }
    env.pop("TO_SPAWN_WACHE_ABLOESUNG_START", None)
    return env, bereit, stopp, gestartet


def _warte_auf(pfad: Path, timeout: float = 10.0) -> bool:
    frist = time.monotonic() + timeout
    while time.monotonic() < frist:
        if pfad.exists():
            return True
        time.sleep(0.05)
    return False


def _repo_anlegen(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_zwei_echte_prozesse_selbe_spec_kein_doppelstart(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path)
    env1, bereit1, stopp1, gestartet1 = _basis_env(tmp_path, repo, 1523, "p1")
    p1 = subprocess.Popen(
        [sys.executable, "-c", _KIND_SKRIPT],
        cwd=str(repo),
        env=env1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert _warte_auf(bereit1), f"Erster Aufseher startete nicht: {p1.poll()}"
        assert gestartet1.exists()

        env2, _bereit2, stopp2, gestartet2 = _basis_env(tmp_path, repo, 1523, "p2")
        stopp2.write_text("1", encoding="utf-8")
        p2 = subprocess.run(
            [sys.executable, "-c", _KIND_SKRIPT],
            cwd=str(repo),
            env=env2,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert p2.returncode == 0, p2.stderr
        assert not gestartet2.exists(), "Zweiter Aufseher durfte Claude nicht starten"
        assert "läuft schon" in p2.stderr or "läuft schon" in p2.stdout
    finally:
        stopp1.write_text("1", encoding="utf-8")
        p1.wait(timeout=10)

    # Nach Ende von p1 darf p3 für dieselbe Spec wieder starten.
    env3, _bereit3, stopp3, gestartet3 = _basis_env(tmp_path, repo, 1523, "p3")
    stopp3.write_text("1", encoding="utf-8")
    p3 = subprocess.run(
        [sys.executable, "-c", _KIND_SKRIPT],
        cwd=str(repo),
        env=env3,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )
    assert p3.returncode == 0, p3.stderr
    assert gestartet3.exists(), "Nach Freigabe muss ein neuer Aufseher starten dürfen"


def test_zwei_echte_prozesse_verschiedene_specs_parallel(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path)
    env1, bereit1, stopp1, gestartet1 = _basis_env(tmp_path, repo, 1523, "s1")
    env2, bereit2, stopp2, gestartet2 = _basis_env(tmp_path, repo, 1524, "s2")
    p1 = subprocess.Popen(
        [sys.executable, "-c", _KIND_SKRIPT],
        cwd=str(repo),
        env=env1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    p2 = subprocess.Popen(
        [sys.executable, "-c", _KIND_SKRIPT],
        cwd=str(repo),
        env=env2,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert _warte_auf(bereit1)
        assert _warte_auf(bereit2)
        assert gestartet1.exists() and gestartet2.exists()
    finally:
        stopp1.write_text("1", encoding="utf-8")
        stopp2.write_text("1", encoding="utf-8")
        p1.wait(timeout=10)
        p2.wait(timeout=10)


def test_abloese_ausnahme_erlaubt_genau_eine_abloesung(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path)
    env1, bereit1, stopp1, _gestartet1 = _basis_env(tmp_path, repo, 1523, "alt")
    p1 = subprocess.Popen(
        [sys.executable, "-c", _KIND_SKRIPT],
        cwd=str(repo),
        env=env1,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    p2 = None
    stopp2 = tmp_path / "neu.stopp"
    try:
        assert _warte_auf(bereit1)

        # Kontrollierte Ablösung darf neben dem laufenden Aufseher starten.
        env2, bereit2, stopp2, gestartet2 = _basis_env(tmp_path, repo, 1523, "neu")
        env2["TO_SPAWN_WACHE_ABLOESUNG_START"] = "1"
        p2 = subprocess.Popen(
            [sys.executable, "-c", _KIND_SKRIPT],
            cwd=str(repo),
            env=env2,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        assert _warte_auf(bereit2), "Ablöse-Aufseher durfte nicht starten"
        assert gestartet2.exists()

        # Eine zweite gleichzeitige Ablösung wird abgewiesen.
        env3, _bereit3, stopp3, gestartet3 = _basis_env(tmp_path, repo, 1523, "neu2")
        env3["TO_SPAWN_WACHE_ABLOESUNG_START"] = "1"
        stopp3.write_text("1", encoding="utf-8")
        p3 = subprocess.run(
            [sys.executable, "-c", _KIND_SKRIPT],
            cwd=str(repo),
            env=env3,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert p3.returncode == 0, p3.stderr
        assert not gestartet3.exists()
        assert "Ablösung läuft schon" in p3.stderr or "Ablösung läuft schon" in p3.stdout

        # Alter Aufseher endet -> Ablösung übernimmt die Haupt-Sperre -> normaler Start bleibt gesperrt.
        stopp1.write_text("1", encoding="utf-8")
        p1.wait(timeout=10)
        time.sleep(0.4)

        env4, _bereit4, stopp4, gestartet4 = _basis_env(tmp_path, repo, 1523, "normal")
        stopp4.write_text("1", encoding="utf-8")
        p4 = subprocess.run(
            [sys.executable, "-c", _KIND_SKRIPT],
            cwd=str(repo),
            env=env4,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert p4.returncode == 0, p4.stderr
        assert not gestartet4.exists()
        assert "läuft schon" in p4.stderr or "läuft schon" in p4.stdout
    finally:
        stopp1.write_text("1", encoding="utf-8")
        stopp2.write_text("1", encoding="utf-8")
        p1.wait(timeout=10)
        if p2 is not None:
            p2.wait(timeout=10)
