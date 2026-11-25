"""Probe-Vorlagen für den erweiterten Probe-Lauf (#426 Teil F).

Prüft mit echten Prozessen und echten Abrufen (keine Attrappen):
``probe/anlegen.sh`` legt ein Lauf-Repo an, der schnelle Prüf-Befehl ist grün, die App
antwortet, Staging-/Live-Schritt und Aufräumen funktionieren, und der absichtlich rote
Werkzeug-Test wird von der App-Prüfung nicht eingesammelt.
"""

from __future__ import annotations

import contextlib
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

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="anlegen.sh braucht bash")


def _freie_ports(anzahl: int) -> list[int]:
    """Reserviert ``anzahl`` freie Ports gleichzeitig, damit keiner doppelt vergeben wird."""
    with contextlib.ExitStack() as stapel:
        sockets = [stapel.enter_context(socket.socket()) for _ in range(anzahl)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


def _anlegen(ziel: Path, variante: str) -> dict[str, int]:
    app, staging, live = _freie_ports(3)
    ports = {"app": app, "staging": staging, "live": live}
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


def _lauf(repo: Path, text: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
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
    verfolgt = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True, check=True).stdout.split()
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
def test_schnelle_pruefung_gruen_und_unter_drei_minuten(tmp_path: Path, variante: str) -> None:
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
def test_roter_werkzeug_test_wird_nicht_eingesammelt(tmp_path: Path, variante: str) -> None:
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


# --- Probe-Inhalt „Leihliste“ (#426, Kommentar 24.11.: Leihlisten-Niveau) ---

PROTOKOLL = SKILL / "probe" / "gemeinsam" / "docs" / "GRILL_probe.md"
PROBE_SPEC = SKILL / "docs" / "PROBE_SPEC.md"


def _ticket_schnitt() -> dict[str, set[str]]:
    """Ticket -> Vorgänger aus der Schnitt-Tabelle (E9) des Beschluss-Protokolls."""
    schnitt: dict[str, set[str]] = {}
    for zeile in PROTOKOLL.read_text(encoding="utf-8").splitlines():
        zellen = [z.strip() for z in zeile.strip().strip("|").split("|")]
        if len(zellen) == 4 and zellen[0][:1] == "T" and zellen[0][1:].isdigit():
            vor = zellen[3]
            schnitt[zellen[0]] = set() if vor == "–" else {v.strip() for v in vor.split(",")}
    return schnitt


def _vorfahren(schnitt: dict[str, set[str]], ticket: str) -> set[str]:
    gesehen: set[str] = set()
    offen = list(schnitt[ticket])
    while offen:
        t = offen.pop()
        if t not in gesehen:
            gesehen.add(t)
            offen.extend(schnitt[t])
    return gesehen


def test_beschluss_protokoll_hat_blocker_kette_und_parallele_tickets() -> None:
    schnitt = _ticket_schnitt()
    assert 5 <= len(schnitt) + 1 <= 7, schnitt  # + Abnahme-Ticket
    for ticket, vor in schnitt.items():
        assert vor <= schnitt.keys(), (ticket, vor)
        assert ticket not in _vorfahren(schnitt, ticket), f"Kreis bei {ticket}"

    def tiefe(t: str) -> int:
        return 1 + max((tiefe(v) for v in schnitt[t]), default=0)

    assert max(tiefe(t) for t in schnitt) >= 3, "keine Kette A -> B -> C"
    parallel = [
        (a, b)
        for a in schnitt
        for b in schnitt
        if a < b and a not in _vorfahren(schnitt, b) and b not in _vorfahren(schnitt, a)
    ]
    assert len(parallel) >= 2, parallel


def test_beide_varianten_bekommen_dasselbe_beschluss_protokoll(tmp_path: Path) -> None:
    inhalte = []
    for variante in VARIANTEN:
        repo = tmp_path / variante
        _anlegen(repo, variante)
        heute = time.strftime("%Y-%m-%d")
        datei = repo / "docs" / f"GRILL_{heute}_probe.md"
        assert sorted((repo / "docs").glob("GRILL_*")) == [datei]
        eingecheckt = subprocess.run(
            ["git", "ls-files", "docs"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert eingecheckt == [f"docs/GRILL_{heute}_probe.md"]
        inhalte.append(datei.read_bytes())
    # Windows-Klon (autocrlf): Vorlage hat CRLF, sed in anlegen.sh schreibt LF — Zeilenenden egal (#553).
    vorlage = PROTOKOLL.read_bytes().replace(b"\r\n", b"\n")
    assert inhalte[0] == inhalte[1] and inhalte[0].replace(b"\r\n", b"\n") == vorlage


def test_kritik_text_steht_in_probe_spec_und_nicht_im_lauf_repo() -> None:
    spec = PROBE_SPEC.read_text(encoding="utf-8")
    kritik = spec[spec.index("### 4.1 Kritik-Text") : spec.index("### 4.2 Abnahme-Schritte")]
    # Trifft Rechenlogik (Kulanz/Deckel) und eine sichtbare Stelle (Leihliste).
    for teil in (
        "Punkt Mahngebühr anders",
        "Kulanz nur 2 Tage statt 3",
        "7,50 €",
        "Leihliste",
    ):
        assert teil in kritik, teil
    protokoll = PROTOKOLL.read_text(encoding="utf-8")
    assert "die ersten 3 Tage überfällig sind Kulanz" in protokoll
    assert "höchstens 1000 Cent" in protokoll
    # Die Bau-Sessions dürfen die Kritik nicht vorweg sehen.
    assert "Punkt Mahngebühr anders" not in protokoll and "7,50" not in protokoll


def test_anlegen_reserviert_drei_verschiedene_ports_gleichzeitig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ports bleiben bis zur Vergabe aller drei belegt, damit das OS keinen doppelt ausgibt."""
    belegt: set[int] = set()

    class _WiederverwendenderSocket:
        def __init__(self) -> None:
            self._port: int | None = None

        def __enter__(self) -> _WiederverwendenderSocket:
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def bind(self, adresse: tuple[str, int]) -> None:
            assert adresse == ("127.0.0.1", 0)
            port = 40000
            while port in belegt:
                port += 1
            belegt.add(port)
            self._port = port

        def getsockname(self) -> tuple[str, int]:
            assert self._port is not None
            return ("127.0.0.1", self._port)

        def close(self) -> None:
            if self._port is not None:
                belegt.discard(self._port)
                self._port = None

    monkeypatch.setattr(socket, "socket", _WiederverwendenderSocket)
    ports = _anlegen(tmp_path / "lauf", "mit-staging")
    assert len(set(ports.values())) == 3
    assert not belegt


def test_probe_spec_verankert_messlatte_l10() -> None:
    spec = PROBE_SPEC.read_text(encoding="utf-8")
    auswertung = spec[spec.index("## 6 Auswertung") : spec.index("## 7 Bestehens-Kriterien")]
    for teil in (
        "LASTENHEFT.md",
        "L10",
        "Ticket-Abhängigkeit",
        "null System-Anomalien",
    ):
        assert teil in auswertung, teil
