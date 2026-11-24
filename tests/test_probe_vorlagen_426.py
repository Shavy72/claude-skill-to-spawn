"""Probe-Vorlagen für den erweiterten Probe-Lauf (#426 Teil F).

Prüft mit echten Prozessen und echten Abrufen (keine Attrappen):
``probe/anlegen.sh`` legt ein Lauf-Repo an, der schnelle Prüf-Befehl ist grün, die App
antwortet, Staging-/Live-Schritt und Aufräumen funktionieren, und der absichtlich rote
Werkzeug-Test wird von der App-Prüfung nicht eingesammelt.
"""

from __future__ import annotations

import json
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ANLEGEN = SKILL / "probe" / "anlegen.sh"
VARIANTEN = ("mit-staging", "ohne-staging")
ROT = ".to-spawn/werkzeug/test_werkzeug_rot.py"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="anlegen.sh braucht bash"
)


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _anlegen(ziel: Path, variante: str) -> dict[str, int]:
    ports = {"app": _freier_port(), "staging": _freier_port(), "live": _freier_port()}
    raus = subprocess.run(
        [
            "bash",
            str(ANLEGEN),
            str(ziel),
            variante,
            "--app-port",
            str(ports["app"]),
            "--staging-port",
            str(ports["staging"]),
            "--live-port",
            str(ports["live"]),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert raus.returncode == 0, raus.stdout + raus.stderr
    return ports


def _konfig(repo: Path) -> dict:
    return json.loads((repo / ".to-spawn" / "config.json").read_text(encoding="utf-8"))


def _befehl(text: str) -> list[str]:
    """Konfig-Befehl ausführbar machen; ``python`` = dieser Interpreter (gleiche Umgebung)."""
    teile = shlex.split(text)
    if teile and teile[0] == "python":
        teile[0] = sys.executable
    return teile


def _lauf(
    repo: Path, text: str, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _befehl(text),
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _abruf(url: str, versuche: int = 1) -> str | None:
    for i in range(versuche):
        try:
            with urllib.request.urlopen(url, timeout=2) as antwort:
                return antwort.read().decode("utf-8")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if i + 1 < versuche:
                time.sleep(0.2)
    return None


@pytest.mark.parametrize("variante", VARIANTEN)
def test_anlegen_legt_git_repo_mit_konfig_an(tmp_path: Path, variante: str) -> None:
    repo = tmp_path / "lauf"
    ports = _anlegen(repo, variante)
    assert (repo / ".git").is_dir()
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip(), "Vorlage braucht einen ersten Commit als Basis"
    sauber = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert sauber.stdout == ""
    verfolgt = subprocess.run(
        ["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split()
    assert ROT in verfolgt and ".to-spawn/config.json" in verfolgt
    for datei in repo.rglob("*"):
        if datei.is_file() and ".git" not in datei.parts:
            assert "@@" not in datei.read_text(encoding="utf-8"), datei
    konfig = _konfig(repo)
    assert konfig["pruefung"]["schnell"] and konfig["pruefung"]["komplett"]
    assert konfig["live"]["url"] == f"http://127.0.0.1:{ports['live']}/"
    if variante == "mit-staging":
        assert konfig["staging"]["url"] == f"http://127.0.0.1:{ports['staging']}/"
        assert konfig["staging"].get("ohne") is not True
    else:
        assert konfig["staging"]["ohne"] is True
        assert not konfig["staging"].get("url")


def test_anlegen_lehnt_unbekannte_variante_und_vollen_ordner_ab(tmp_path: Path) -> None:
    falsch = subprocess.run(
        ["bash", str(ANLEGEN), str(tmp_path / "x"), "mit-allem"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert falsch.returncode != 0 and not (tmp_path / "x").exists()
    voll = tmp_path / "voll"
    voll.mkdir()
    (voll / "datei").write_text("da", encoding="utf-8")
    raus = subprocess.run(
        ["bash", str(ANLEGEN), str(voll), "ohne-staging"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert raus.returncode != 0 and not (voll / ".git").exists()


@pytest.mark.parametrize("variante", VARIANTEN)
def test_schnelle_pruefung_gruen_und_unter_drei_minuten(
    tmp_path: Path, variante: str
) -> None:
    repo = tmp_path / "lauf"
    _anlegen(repo, variante)
    start = time.monotonic()
    raus = _lauf(repo, _konfig(repo)["pruefung"]["schnell"])
    dauer = time.monotonic() - start
    assert raus.returncode == 0, raus.stdout + raus.stderr
    assert dauer < 180
    print(f"schnelle Prüfung {variante}: {dauer:.2f} s")


@pytest.mark.parametrize("variante", VARIANTEN)
def test_komplett_pruefung_gruen(tmp_path: Path, variante: str) -> None:
    repo = tmp_path / "lauf"
    _anlegen(repo, variante)
    raus = _lauf(repo, _konfig(repo)["pruefung"]["komplett"])
    assert raus.returncode == 0, raus.stdout + raus.stderr
    assert "Rauchtest" in raus.stdout


def test_app_antwortet_mit_versions_text(tmp_path: Path) -> None:
    repo = tmp_path / "lauf"
    ports = _anlegen(repo, "ohne-staging")
    prozess = subprocess.Popen(
        [sys.executable, "-m", "app.server", "--port", str(ports["app"])],
        cwd=repo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        text = _abruf(f"http://127.0.0.1:{ports['app']}/", versuche=50)
        assert text is not None and text.startswith("Probe-App ")
        assert "(lokal)" in text
    finally:
        prozess.terminate()
        prozess.wait(timeout=10)


@pytest.mark.parametrize("variante", VARIANTEN)
def test_live_schritt_und_aufraeumen(tmp_path: Path, variante: str) -> None:
    repo = tmp_path / "lauf"
    _anlegen(repo, variante)
    konfig = _konfig(repo)
    url = konfig["live"]["url"]
    try:
        raus = _lauf(repo, konfig["live"]["befehl"], timeout=60)
        assert raus.returncode == 0, raus.stdout + raus.stderr
        assert "Beleg:" in raus.stdout
        text = _abruf(url)
        assert text is not None and "(live)" in text
        assert (repo / "live" / "app").is_dir()
    finally:
        weg = _lauf(repo, konfig["live"]["aufraeumen"], timeout=60)
    assert weg.returncode == 0, weg.stdout + weg.stderr
    assert _abruf(url) is None
    assert not (repo / "live").exists()
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        == ""
    )


def test_staging_schritt_nur_mit_staging(tmp_path: Path) -> None:
    mit = tmp_path / "mit"
    _anlegen(mit, "mit-staging")
    konfig = _konfig(mit)
    try:
        raus = _lauf(mit, konfig["staging_start"], timeout=60)
        assert raus.returncode == 0, raus.stdout + raus.stderr
        text = _abruf(konfig["staging"]["url"])
        assert text is not None and "(staging)" in text
    finally:
        weg = _lauf(mit, "python skripte/ausrollen.py staging --aufraeumen", timeout=60)
    assert weg.returncode == 0
    assert _abruf(konfig["staging"]["url"]) is None

    ohne = tmp_path / "ohne"
    _anlegen(ohne, "ohne-staging")
    abgelehnt = _lauf(ohne, "python skripte/ausrollen.py staging", timeout=60)
    assert abgelehnt.returncode != 0
    assert not (ohne / "staging").exists()


@pytest.mark.parametrize("variante", VARIANTEN)
def test_roter_werkzeug_test_wird_nicht_eingesammelt(
    tmp_path: Path, variante: str
) -> None:
    repo = tmp_path / "lauf"
    _anlegen(repo, variante)
    # Der Werkzeug-Test ist wirklich rot …
    rot = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", ROT],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert rot.returncode == 1, rot.stdout + rot.stderr
    # … aber weder der schnelle Prüf-Befehl noch ein nacktes pytest im App-Repo sammelt ihn ein.
    schnell = _lauf(repo, _konfig(repo)["pruefung"]["schnell"])
    assert schnell.returncode == 0 and "werkzeug" not in schnell.stdout + schnell.stderr
    nackt = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert nackt.returncode == 0, nackt.stdout + nackt.stderr
    assert "werkzeug" not in nackt.stdout


def test_skill_pytest_sammelt_probe_vorlagen_nicht_ein() -> None:
    raus = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "probe",
        ],
        cwd=SKILL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    # Exit 5 = nichts gesammelt: Vorlagen-Tests (auch der rote) bleiben aus dem Skill-Lauf draußen.
    assert raus.returncode == 5, raus.stdout + raus.stderr
