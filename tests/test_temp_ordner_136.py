"""Temp-Ordner auf der Platte statt RAM-Platte /tmp (#136).

Prüft:
- Wahl des Ordners (:mod:`to_spawn.temp_ordner`) und ``TMPDIR`` bei Prüf-Tür, Sandbox, ``bau.py``, ``wache.py``
- Anlegen markierter Temp-Ordner (:func:`temp_ordner.anlegen`) mit Bindung an Name, ``st_dev``, ``st_ino``
- Aufräumen im Hausmeister (:func:`temp_ordner.aufraeumen`, aufgerufen aus ``hausmeister.aufraeumen_alles``):
  Ticket zu, alt, kein Prozess, kein Socket, nichts Ungesichertes (gegen erreichbares Remote geprüft),
  Schutz gegen kopierte Ordner, kopierte Marker, Hardlinks, Symlinks und fremde Repos
- Prüf-Test „kein fest verdrahtetes ``/tmp`` im Skill-Code“ (Ausnahmen zeilengenau begründet)
- Schutz-Hooks blocken den Hausmeister-Weg nicht; A24-Übergabe-Regel; Teilbeleg M14 in ``docs/ABNAHME_L1.md``
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from to_spawn import anleitung, befugnis, config, hausmeister, hausmeister_windows, nest, temp_ordner
from to_spawn.pruef import ausfuehren

SKILL = Path(__file__).resolve().parent.parent
ALT = time.time() - 3 * 24 * 3600
TEST_REPO = "Shavy72/claude-skill-to-spawn"


def _maschine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inhalt: dict) -> None:
    datei = tmp_path / "maschine.json"
    datei.write_text(json.dumps(inhalt), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(datei))
    monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER", raising=False)


def test_vorgabe_liegt_im_home_nicht_im_system_temp() -> None:
    vorgabe = Path(config.MASCHINE_DEFAULTS["temp_ordner"]).expanduser()
    assert str(vorgabe).startswith(str(Path.home()))
    assert not str(vorgabe).startswith(tempfile.gettempdir())
    assert not str(vorgabe).startswith("/tmp")
    # kurz, weil Unix-Sockets unter pytest-tmp_path < 108 Zeichen brauchen
    assert len(str(config.MASCHINE_DEFAULTS["temp_ordner"])) <= temp_ordner.MAX_LAENGE


def test_pfad_kommt_aus_der_maschinen_liste_und_wird_angelegt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ziel = tmp_path / "platte" / "tmp-bau"
    _maschine(monkeypatch, tmp_path, {"temp_ordner": str(ziel)})
    assert temp_ordner.pfad() == ziel
    assert ziel.is_dir()


def test_test_tuer_geht_vor_der_maschinen_liste(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _maschine(monkeypatch, tmp_path, {"temp_ordner": str(tmp_path / "liste")})
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "tuer"))
    assert temp_ordner.pfad() == tmp_path / "tuer"


@pytest.mark.parametrize("gefaehrlich", ["~", "/"])
def test_home_oder_wurzel_fallen_auf_die_vorgabe_zurueck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gefaehrlich: str
) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    _maschine(monkeypatch, tmp_path, {"temp_ordner": gefaehrlich})
    vorgabe = Path(str(config.MASCHINE_DEFAULTS["temp_ordner"])).expanduser()
    assert temp_ordner.pfad() == Path(str(vorgabe))
    assert vorgabe.is_dir() and heim in vorgabe.parents


def test_umgebung_setzt_alle_drei_variablen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "t"))
    assert temp_ordner.umgebung() == {k: str(tmp_path / "t") for k in ("TMPDIR", "TEMP", "TMP")}


def test_umgebung_ist_leer_wenn_nicht_anlegbar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(datei / "drunter"))
    assert temp_ordner.umgebung() == {}


def test_pruef_lauf_hat_tmpdir_auf_dem_konfig_ordner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    monkeypatch.setenv("TMPDIR", "/tmp")
    code = "import tempfile; print(tempfile.gettempdir())"
    erg = ausfuehren.lauf([sys.executable, "-c", code], tmp_path)
    assert erg.ausgabe.strip() == str(ziel)


def test_sandbox_der_bau_session_darf_in_den_temp_ordner_schreiben(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    einst = nest.sandbox_einstellungen(tmp_path / "wt", tmp_path / "haupt", {}, home=tmp_path)
    assert str(ziel) in einst["filesystem"]["allowWrite"]


@pytest.mark.parametrize("skript", ["skripte/bau.py", "skripte/wache.py"])
def test_bau_und_aufseher_setzen_tmpdir_aus_der_einen_stelle(skript: str) -> None:
    quelle = (SKILL / skript).read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in quelle
    assert 'LOCALAPPDATA") or tempfile.gettempdir()) / "Temp"' not in quelle


# --- Hausmeister räumt markierte Temp-Ordner (#136) ----------------------------------


def _alt_baum(ordner: Path) -> Path:
    """Setzt die Änderungszeit von Ordner und ganzem Inhalt (bottom-up) weit in die Vergangenheit."""
    eintraege: list[Path] = []
    for wurzel, unter, dateien in os.walk(ordner, topdown=False):
        w = Path(wurzel)
        for name in (*dateien, *unter):
            eintraege.append(w / name)
    eintraege.append(ordner)
    for pfad in eintraege:
        if not pfad.is_symlink():
            os.utime(pfad, (ALT, ALT))
    return ordner


def _markieren(ordner: Path, nr: int, repo: str = TEST_REPO, *, binden: bool = True) -> Path:
    """Schreibt eine Marker-Datei wie :func:`temp_ordner.anlegen` (optional ohne Ordner-Bindung)."""
    inhalt: dict[str, object] = {
        "repo": repo,
        "ticket": nr,
        "angelegt": ALT,
        "von": "test",
    }
    if binden:
        info = os.lstat(ordner)
        inhalt.update({"ordner": ordner.name, "dev": info.st_dev, "ino": info.st_ino})
    (ordner / temp_ordner.MARKER).write_text(json.dumps(inhalt), encoding="utf-8")
    return ordner


def _wurzel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zu: set[int]) -> Path:
    wurzel = tmp_path / "w"  # kurz: Unix-Sockets darunter brauchen < 108 Zeichen
    wurzel.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(wurzel))
    monkeypatch.setattr(
        hausmeister,
        "_ticket_daten",
        lambda nr, ziel=None: {"state": "CLOSED" if nr in zu else "OPEN"},
    )
    return wurzel


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _quelle_anlegen(basis: Path) -> Path:
    basis.mkdir(parents=True, exist_ok=True)
    quelle = basis / "quelle.git"
    _git(basis, "init", "-q", "--bare", str(quelle))
    saat = basis / "saat"
    _git(basis, "init", "-q", str(saat))
    (saat / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(saat, "add", ".")
    _git(saat, "commit", "-qm", "saat")
    _git(saat, "push", "-q", str(quelle), "HEAD:refs/heads/main")
    _git(quelle, "symbolic-ref", "HEAD", "refs/heads/main")
    return quelle


def test_anlegen_schreibt_an_ordner_gebundenen_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    ordner = temp_ordner.anlegen(136, repo=TEST_REPO, von="bau")
    assert ordner.parent == wurzel and ordner.name.startswith("t136-")
    marker = json.loads((ordner / temp_ordner.MARKER).read_text(encoding="utf-8"))
    info = os.lstat(ordner)
    assert marker["ticket"] == 136
    assert marker["repo"] == TEST_REPO
    assert marker["von"] == "bau"
    assert marker["ordner"] == ordner.name
    assert marker["dev"] == info.st_dev
    assert marker["ino"] == info.st_ino
    assert isinstance(marker["angelegt"], float)
    assert dict(temp_ordner.aufraeumen(loeschen=False))[ordner].startswith("bleibt:")
    for schlecht in (0, -5, True):
        with pytest.raises(ValueError):
            temp_ordner.anlegen(schlecht, repo=TEST_REPO)


def test_hausmeister_raeumt_markierte_temp_reste_geschlossener_tickets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platte = _wurzel(tmp_path, monkeypatch, {1335, 1499, 136, 1500, 1600, 1601})

    def rest(name: str, nr: int | None = None, *, alt: bool = True) -> Path:
        ordner = platte / name
        ordner.mkdir()
        (ordner / "datei.txt").write_text("x", encoding="utf-8")
        if nr is not None:
            _markieren(ordner, nr)
        if alt:
            _alt_baum(ordner)
        return ordner

    weg = [
        rest("t1335", 1335),
        rest("mut1499", 1499),
        rest("irgendwas", 136),
    ]
    bleibt = [
        rest("t1400", 1400),  # Ticket offen
        rest("t1500", 1500, alt=False),  # frisch
        rest("t1601"),  # kein Marker, Ticket zu — Namen zählen nie
        rest("t1600", 1600),  # Prozess darin
    ]
    hat_link = True
    try:
        (platte / "t1700").symlink_to(platte / "t1600", target_is_directory=True)
    except OSError:
        hat_link = False

    hausmeister.aufraeumen_alles(loeschen=False)
    assert all(p.exists() for p in weg + bleibt), "Probelauf löscht nie"

    prozess = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=platte / "t1600"
    )
    try:
        hausmeister.aufraeumen_alles(loeschen=True)
    finally:
        prozess.kill()
        prozess.wait()

    assert [p for p in weg if p.exists()] == []
    assert [p for p in bleibt if not p.exists()] == []
    if hat_link:
        assert (platte / "t1700").is_symlink()
    log_pfad = Path(os.environ["TO_SPAWN_HAUSMEISTER_LOG"])
    zeilen = [json.loads(z) for z in log_pfad.read_text(encoding="utf-8").splitlines()]
    assert {str(p) for p in weg} <= {z["ordner"] for z in zeilen if z.get("art") == "gelöscht"}


def test_kopierter_ordner_kopierter_marker_und_hardlink_marker_bleiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {101, 102, 103})
    echt = temp_ordner.anlegen(101, repo=TEST_REPO, von="test")
    (echt / "daten.txt").write_text("echt", encoding="utf-8")

    # 1. Kopierter Ordner (z. B. Sicherung per cp -a / copytree)
    kopie_ordner = wurzel / "sicherung-101"
    shutil.copytree(echt, kopie_ordner)

    # 2. Kopierte Marker-Datei in fremdem Ordner
    fremd_kopie = wurzel / "fremd-mit-kopiertem-marker"
    fremd_kopie.mkdir()
    (fremd_kopie / "wichtig.txt").write_text("behalten", encoding="utf-8")
    shutil.copy2(echt / temp_ordner.MARKER, fremd_kopie / temp_ordner.MARKER)

    # 3. Hardlink-Marker (auch am Original-Ordner erhöht das st_nlink auf 2)
    orig_hl = temp_ordner.anlegen(103, repo=TEST_REPO, von="test")
    anderer = wurzel / "anderer-hardlink"
    anderer.mkdir()
    os.link(orig_hl / temp_ordner.MARKER, anderer / temp_ordner.MARKER)

    for p in (echt, kopie_ordner, fremd_kopie, orig_hl, anderer):
        _alt_baum(p)

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert not echt.exists(), ergebnis[echt]
    assert kopie_ordner.is_dir() and "kopiert oder verschoben" in ergebnis[kopie_ordner], ergebnis[kopie_ordner]
    assert fremd_kopie.is_dir() and "kopiert oder verschoben" in ergebnis[fremd_kopie], ergebnis[fremd_kopie]
    assert orig_hl.is_dir() and "Hardlink" in ergebnis[orig_hl], ergebnis[orig_hl]
    assert anderer.is_dir() and "Hardlink" in ergebnis[anderer], ergebnis[anderer]


def test_symlink_ordner_und_marker_bleiben_mit_grund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1070, 1072})
    aussen = tmp_path / "heim" / "t1070"
    aussen.mkdir(parents=True)
    (aussen / "arbeit.txt").write_text("echt", encoding="utf-8")
    _alt_baum(_markieren(aussen, 1070))

    echter_marker = _markieren(tmp_path / "heim", 1072) / temp_ordner.MARKER
    mit_link = wurzel / "t1072"
    mit_link.mkdir()
    try:
        (wurzel / "t1070").symlink_to(aussen, target_is_directory=True)
        (mit_link / temp_ordner.MARKER).symlink_to(echter_marker)
    except OSError:
        pytest.skip("Symlink nicht anlegbar (Windows ohne Recht)")
    _alt_baum(mit_link)

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert (aussen / "arbeit.txt").is_file()
    assert (wurzel / "t1070").is_symlink()
    assert "Symlink" in ergebnis[wurzel / "t1070"], ergebnis[wurzel / "t1070"]
    assert mit_link.is_dir()
    assert "Symlink" in ergebnis[mit_link], ergebnis[mit_link]
    assert echter_marker.is_file()


def test_marker_ungueltig_zu_gross_ordner_oder_fremdes_repo_bleiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1073, 1074, 1075, 1076, 1077})

    fremd = wurzel / "t1073"
    fremd.mkdir()
    _alt_baum(_markieren(fremd, 1073, repo="fremd/anderes-repo"))

    leer_repo = wurzel / "t1074"
    leer_repo.mkdir()
    _alt_baum(_markieren(leer_repo, 1074, repo=""))

    kaputt = wurzel / "t1075"
    kaputt.mkdir()
    (kaputt / temp_ordner.MARKER).write_text("{kein json", encoding="utf-8")
    _alt_baum(kaputt)

    zu_gross = wurzel / "t1076"
    zu_gross.mkdir()
    (zu_gross / temp_ordner.MARKER).write_text(" " * 5000 + "{}", encoding="utf-8")
    _alt_baum(zu_gross)

    marker_ordner = wurzel / "t1077"
    (marker_ordner / temp_ordner.MARKER).mkdir(parents=True)
    _alt_baum(marker_ordner)

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert fremd.is_dir() and "fremdes Repo" in ergebnis[fremd], ergebnis[fremd]
    assert leer_repo.is_dir() and "fremdes Repo" in ergebnis[leer_repo], ergebnis[leer_repo]
    assert kaputt.is_dir() and "Marker ungültig" in ergebnis[kaputt], ergebnis[kaputt]
    assert zu_gross.is_dir() and "zu groß" in ergebnis[zu_gross], ergebnis[zu_gross]
    assert marker_ordner.is_dir() and "keine eigene Datei" in ergebnis[marker_ordner], ergebnis[marker_ordner]


def test_ungesicherte_git_repos_und_gefaelschter_remote_ref_bleiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set(range(1000, 1100)))
    quelle = _quelle_anlegen(tmp_path)

    def klon(name: str) -> Path:
        ziel = wurzel / name
        _git(tmp_path, "clone", "-q", str(quelle), str(ziel))
        return ziel

    # 1. Geänderte/neue Datei
    neu = klon("t1003")
    (neu / "neu.txt").write_text("neu", encoding="utf-8")

    # 2. Ungepushter Commit
    ungepusht = klon("t1004")
    _git(ungepusht, "commit", "-q", "--allow-empty", "-m", "lokal")

    # 3. Gefälschter lokaler origin-Ref ohne echten Push (#179)
    gefaelscht = klon("t1005")
    _git(gefaelscht, "commit", "-q", "--allow-empty", "-m", "nur-lokal")
    _git(gefaelscht, "update-ref", "refs/remotes/origin/main", "HEAD")

    # 4. Weggefallenes Remote (nicht erreichbar -> fail-closed)
    weg_quelle = _quelle_anlegen(tmp_path / "weg-remote")
    ohne_server = wurzel / "t1006"
    _git(tmp_path, "clone", "-q", str(weg_quelle), str(ohne_server))
    # Windows setzt Schreibschutz auf .git/objects — denselben Handler wie im Hausmeister nutzen.
    if sys.version_info >= (3, 12):
        shutil.rmtree(weg_quelle, onexc=hausmeister_windows.schreibschutz_weg)
    else:
        shutil.rmtree(weg_quelle, onerror=hausmeister_windows.schreibschutz_weg)

    # 5. Stash
    stash = klon("t1007")
    (stash / "s.txt").write_text("stash", encoding="utf-8")
    _git(stash, "add", "s.txt")
    _git(stash, "stash", "-q")

    # 6. Commit nur noch im Reflog
    reflog = klon("t1008")
    _git(reflog, "commit", "-q", "--allow-empty", "-m", "verlassen")
    _git(reflog, "reset", "-q", "--hard", "HEAD~1")

    # 7. Sauberer Klon (auch mit ignorierter .log-Datei) -> darf weg
    sauber = klon("t1009")
    (sauber / "lauf.log").write_text("ignoriert", encoding="utf-8")

    # 8. Wegwerf-Repo in pytest-Ordner (pytest-of-* und markierter pt-*) -> darf weg
    pytest_rest = wurzel / "t1010"
    _git(tmp_path, "init", "-q", str(pytest_rest / "pytest-of-bau" / "pytest-1" / "repo"))
    pt_ordner = pytest_rest / "pt-abc123"
    _git(tmp_path, "init", "-q", str(pt_ordner / "repo"))
    (pt_ordner / ".pt-lauf").write_text("999999\n", encoding="utf-8")

    bleibt = [neu, ungepusht, gefaelscht, ohne_server, stash, reflog]
    weg = [sauber, pytest_rest]
    for p in bleibt + weg:
        _alt_baum(_markieren(p, int(p.name[1:])))

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert [p for p in bleibt if not p.exists()] == []
    assert [p for p in weg if p.exists()] == []
    for p in (neu, ungepusht, gefaelscht, stash, reflog):
        assert "ungesichert" in ergebnis[p], (p.name, ergebnis[p])
    assert "nicht erreichbar" in ergebnis[ohne_server], ergebnis[ohne_server]


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Sockets nur unter POSIX")
def test_socket_im_baum_bleibt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1001})
    ordner = wurzel / "t1001"
    ordner.mkdir()
    _markieren(ordner, 1001)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(ordner / "default"))
    sock.listen(1)
    _alt_baum(ordner)
    try:
        ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    finally:
        sock.close()
    assert (ordner / "default").exists()
    assert "Socket" in ergebnis[ordner], ergebnis[ordner]


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="UID-Prüfung nur unter POSIX")
def test_fremder_besitzer_bleibt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1335})
    ordner = wurzel / "t1335"
    ordner.mkdir()
    _alt_baum(_markieren(ordner, 1335))
    echt = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: echt + 1)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert ordner.is_dir()
    assert "fremder Besitzer" in ergebnis[ordner], ergebnis[ordner]


# --- Prüf-Test: kein fest verdrahtetes /tmp im Skill-Code (#136) ---------------------

#: Sucht ``/tmp`` als eigenen Pfad (nicht ``~/tmp-bau`` oder ``/home/bau/tmp-bau``).
_TMP_MUSTER = re.compile(r"(?<![A-Za-z0-9_~])/tmp(?![A-Za-z0-9_-])")

#: Datei → (erlaubte Textstücke je Fundzeile, Begründung).
TMP_AUSNAHMEN: dict[str, tuple[tuple[str, ...], str]] = {
    "nest/nest_server.sh": (
        (
            "curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource.sh",
            'tmux -S /tmp/tmux-$NUTZER_UID/default list-sessions',
        ),
        "Einrichtung frischer Server (Node-Installer vor Skill-Setup, System-tmux-Socket)",
    ),
    "nest/skill_schutz.py": (
        ("to_spawn.py … > /tmp/log",),
        "Docstring-Beispiel einer Shell-Umleitung",
    ),
    "review/rot_probe.py": (
        ("# Worktrees auf der Platte, nie in /tmp",),
        "Kommentar: Begründung gegen /tmp (#1493)",
    ),
    "to_spawn/aufpasser.py": (
        ("unter ``/tmp/pytest-of-<nutzer>/`` ein Gate",),
        "Kommentar zur Gate-Erkennung (#1411)",
    ),
    "to_spawn/befugnis.py": (
        ("_RM_ERLAUBT = re.compile(r\"(?:/tmp/|/var/tmp/",),
        "Erlaubnis-Muster für rm -r in System-Temp-Ordnern (Verlauf-Prüfung, kein Arbeitsordner)",
    ),
    "to_spawn/hausmeister.py": (
        ("``~/*``, ``/tmp/*`` fallen weg",),
        "Docstring: /tmp/* wird als Hausmeister-Wurzel abgelehnt",
    ),
    "to_spawn/nest.py": (
        (
            'schreiben.append("/tmp")',
            "# #136: TMPDIR der Session liegt auf der Platte, nicht in /tmp",
        ),
        "Sandbox-Schreibrecht für /tmp (System-Werkzeuge) plus Kommentar zu #136",
    ),
    "to_spawn/probesitz.py": (
        ("nicht unter /tmp: /tmp ist in der Sandbox",),
        "Kommentar: warum Probesitz-Ordner unter HOME liegen",
    ),
    "to_spawn/vorgaben.py": (
        ("statt RAM-Platte /tmp (#136)",),
        "Kommentar zur Temp-Ordner-Vorgabe",
    ),
}


def _tmp_treffer(dateien: list[Path], wurzel: Path) -> dict[str, list[str]]:
    funde: dict[str, list[str]] = {}
    for datei in dateien:
        rel = datei.relative_to(wurzel).as_posix()
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _TMP_MUSTER.search(zeile):
                funde.setdefault(rel, []).append(f"{rel}:{nr}: {zeile.strip()}")
    return funde


def _tmp_verstoesse(funde: dict[str, list[str]]) -> list[str]:
    import test_feste_namen_l1 as fn

    verstoesse: list[str] = []
    for rel, stellen in sorted(funde.items()):
        fremd, _ = fn._abgleich(stellen, TMP_AUSNAHMEN.get(rel, ((), ""))[0])
        if fremd:
            verstoesse.append(f"{rel}: {len(fremd)} Zeile(n) mit festem /tmp:\n  " + "\n  ".join(fremd))
    return verstoesse


def test_kein_fest_verdrahtetes_tmp_im_skill_code() -> None:
    import test_feste_namen_l1 as fn

    verstoesse = _tmp_verstoesse(_tmp_treffer(fn._skill_dateien(), fn.SKILL))
    assert not verstoesse, (
        "Fest verdrahtetes /tmp im Skill-Code — temp_ordner.pfad()/umgebung() nutzen oder "
        "begründete Ausnahme in TMP_AUSNAHMEN eintragen:\n" + "\n".join(verstoesse)
    )


def test_tmp_ausnahmen_nicht_veraltet() -> None:
    import test_feste_namen_l1 as fn

    funde = _tmp_treffer(fn._skill_dateien(), fn.SKILL)
    veraltet = [rel for rel in TMP_AUSNAHMEN if rel not in funde]
    assert not veraltet, f"Veraltete /tmp-Ausnahme ohne Treffer: {veraltet}"
    ungenutzt = [
        f"{rel}: {stueck!r}"
        for rel, (erlaubt, _) in TMP_AUSNAHMEN.items()
        for stueck in fn._abgleich(funde[rel], erlaubt)[1]
    ]
    assert not ungenutzt, f"Veraltetes /tmp-Textstück ohne Treffer: {ungenutzt}"
    assert all(grund.strip() for _, grund in TMP_AUSNAHMEN.values())


def test_tmp_pruefung_wird_rot_bei_neuem_tmp(tmp_path: Path) -> None:
    probe = tmp_path / "to_spawn" / "neu.py"
    probe.parent.mkdir(parents=True)
    probe.write_text('ORT = "/tmp/mein-ordner"\n', encoding="utf-8")
    verstoesse = _tmp_verstoesse(_tmp_treffer([probe], tmp_path))
    assert len(verstoesse) == 1 and "to_spawn/neu.py" in verstoesse[0]


# --- L4 (A22–A24) und Abnahme M14 (#136 Nachtrag) -----------------------------------


def test_schutz_hooks_blocken_hausmeister_weg_nicht() -> None:
    befehle = (
        "python3 ~/.claude/skills/to-spawn/to_spawn.py aufraeumen --alles --loeschen",
        "python3 ~/.claude/skills/to-spawn/to_spawn.py helfer-temp aufraeumen --aelter-als-tage 1",
    )
    spec = importlib.util.spec_from_file_location("skill_schutz_136", SKILL / "nest" / "skill_schutz.py")
    assert spec and spec.loader
    skill_schutz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill_schutz)

    for befehl in befehle:
        assert befugnis.handlung_klasse_2(befehle=[befehl]) is None, befehl
        assert (
            skill_schutz.pruefe(
                {"tool_name": "Bash", "tool_input": {"command": befehl}, "cwd": str(Path.home())}
            )
            is False
        ), befehl

    hook = Path.home() / ".claude" / "hooks" / "block-dangerous.sh"
    bash = shutil.which("bash")
    if hook.is_file() and bash is not None:
        for befehl in befehle:
            erg = subprocess.run(
                [bash, str(hook)],
                input=json.dumps({"tool_input": {"command": befehl}}),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert erg.returncode == 0, erg.stdout + erg.stderr


def test_uebergabe_regel_a24_verbietet_aufraeumen_unter_wartet_auf_menschen() -> None:
    text = anleitung.ABLOESE_SOP
    assert "Wartet auf den Menschen" in text
    for wort in ("Aufräumen", "RAM", "Platte", "Neustart"):
        assert wort in text, f"A24-Verbot in ABLOESE_SOP fehlt: {wort}"


def test_abnahme_m14_beleg_vorhanden() -> None:
    zeilen = (SKILL / "docs" / "ABNAHME_L1.md").read_text(encoding="utf-8").splitlines()
    m14 = [z for z in zeilen if z.startswith("| M14 |")]
    assert len(m14) == 1
    assert "test_temp_ordner_136.py" in m14[0]
    assert "| – | offen |" not in m14[0]
