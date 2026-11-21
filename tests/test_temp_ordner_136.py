"""#136: Temp-Ordner auf der Platte statt RAM-Platte /tmp, Hausmeister räumt Temp-Reste.

Alle Ordner liegen in ``tmp_path`` (nachgebaute Wurzel); echte ``/tmp``/``~/wt`` nie.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import hausmeister, temp_ordner
from to_spawn.pruef import ausfuehren

SKILL = Path(__file__).resolve().parents[1]
ALT = time.time() - 3 * 24 * 3600


def test_pruef_lauf_hat_tmpdir_auf_konfig_ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ziel = tmp_path / "platte" / "tmp-bau"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ram"))
    code = "import os, tempfile; print(os.environ['TMPDIR']); print(tempfile.gettempdir())"
    lauf = ausfuehren.lauf([sys.executable, "-c", code], tmp_path)
    assert lauf.code == 0, lauf.ausgabe
    assert lauf.ausgabe.split() == [str(ziel), str(ziel)]
    assert ziel.is_dir()


def test_temp_ordner_aus_maschinen_liste_und_rueckfall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    maschine = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    assert temp_ordner.pfad() == tmp_path / "tmp-bau"  # Rückfall ohne Datei
    maschine.write_text(json.dumps({"temp_ordner": "~/platte/temp"}), encoding="utf-8")
    assert temp_ordner.umgebung() == {k: str(tmp_path / "platte" / "temp") for k in ("TMPDIR", "TEMP", "TMP")}


def test_bau_session_bekommt_temp_umgebung() -> None:
    # bau.py setzt die Umgebung der Claude-Session an einer Stelle; ganzer Start ist hier zu teuer.
    text = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in text


def _rest(ordner: Path, *, alt: bool = True) -> Path:
    ordner.mkdir(parents=True)
    (ordner / "datei.txt").write_text("x", encoding="utf-8")
    if alt:
        for p in (ordner / "datei.txt", ordner):
            os.utime(p, (ALT, ALT))
    return ordner


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_hausmeister_raeumt_temp_reste_geschlossener_tickets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platte = tmp_path / "tmp-bau"
    altbestand = tmp_path / "ram"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(platte))
    monkeypatch.setenv(
        "TO_SPAWN_TEMP_RESTE", os.pathsep.join([str(altbestand / "*"), str(altbestand / "claude-*" / "*")])
    )
    zu = {1335, 1499, 136, 1500, 1600}
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: {"state": "CLOSED" if nr in zu else "OPEN"})
    weg = [_rest(altbestand / "t1335"), _rest(altbestand / "claude-1000" / "mut1499"), _rest(platte / "tmp_136")]
    os.utime(altbestand / "claude-1000", (ALT, ALT))
    bleibt = [
        altbestand / "claude-1000",  # Such-Eltern, nie selbst ein Rest
        _rest(altbestand / "t1400"),  # Ticket offen
        _rest(altbestand / "t1500", alt=False),  # frisch
        _rest(altbestand / "tmpk3j2x9"),  # kein Ticket-Name
        _rest(altbestand / "t1600"),  # Prozess darin
    ]
    (altbestand / "t1700").symlink_to(altbestand / "t1600")  # Symlink nie

    hausmeister.aufraeumen_alles(loeschen=False)
    assert all(p.exists() for p in weg + bleibt), "Probelauf löscht nie"

    prozess = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=altbestand / "t1600")
    try:
        hausmeister.aufraeumen_alles(loeschen=True)
    finally:
        prozess.kill()
        prozess.wait()
    assert [p for p in weg if p.exists()] == []
    assert [p for p in bleibt if not p.exists()] == []
    assert (altbestand / "t1700").is_symlink()
    log = Path(os.environ["TO_SPAWN_HAUSMEISTER_LOG"]).read_text(encoding="utf-8")
    assert {str(p) for p in weg} <= {json.loads(z)["ordner"] for z in log.splitlines()}


def test_schutz_hook_blockt_hausmeister_weg_nicht(tmp_path: Path) -> None:
    hook = Path.home() / ".claude" / "hooks" / "block-dangerous.sh"
    bash = shutil.which("bash")
    if not hook.is_file() or bash is None:
        pytest.skip("Schutz-Hook block-dangerous.sh nicht installiert")
    befehl = "python3 ~/.claude/skills/to-spawn/to_spawn.py aufraeumen --alles --loeschen"
    erg = subprocess.run(
        [bash, str(hook)],
        input=json.dumps({"tool_input": {"command": befehl}}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert erg.returncode == 0, erg.stdout + erg.stderr


#: Begründete Ausnahmen für ``/tmp`` im Skill-Code: Datei → Grund.
TMP_AUSNAHMEN = {
    "to_spawn/vorgaben.py": "Vorgabe-Muster des Temp-Altbestands (#136), überschreibbar per Maschinen-Liste",
    "to_spawn/nest.py": "Sandbox-Schreibrecht für /tmp (Werkzeuge schreiben dort), kein Arbeitsordner",
    "to_spawn/aufpasser.py": "Kommentar zur Gate-Erkennung (#1411)",
    "to_spawn/probesitz.py": "Kommentar: warum die Ordner nicht unter /tmp liegen",
    "to_spawn/hausmeister.py": "Docstring: /tmp/* fällt als Wurzel weg",
    "nest/skill_schutz.py": "Docstring-Beispiel einer Umleitung",
    "nest/nest_server.sh": "Einrichtung frischer Server (Installer-Download, tmux-Socket des Systems)",
}


def test_kein_fest_verdrahtetes_tmp_im_skill_code() -> None:
    funde: list[str] = []
    for ordner in ("to_spawn", "skripte", "repo-scripts", "leitstand", "aliase", "nest", "kette"):
        for datei in sorted((SKILL / ordner).rglob("*")):
            if datei.suffix not in {".py", ".sh", ".ps1"} or not datei.is_file():
                continue
            name = datei.relative_to(SKILL).as_posix()
            if name not in TMP_AUSNAHMEN and "/tmp" in datei.read_text(encoding="utf-8", errors="replace"):
                funde.append(name)
    assert funde == [], f"/tmp fest verdrahtet (temp_ordner.pfad() nutzen oder Ausnahme begründen): {funde}"


# --- Fix-Runde 1 (Prüfer-Urteil PR 148) ----------------------------------------------


def _alt_machen(ordner: Path) -> Path:
    for p in [*ordner.iterdir(), ordner]:
        os.utime(p, (ALT, ALT), follow_symlinks=False)
    return ordner


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _altbestand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zu: set[int]) -> Path:
    altbestand = tmp_path / "ram"
    altbestand.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "platte"))
    monkeypatch.setenv("TO_SPAWN_TEMP_RESTE", str(altbestand / "*"))
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: {"state": "CLOSED" if nr in zu else "OPEN"})
    return altbestand


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Sockets + /proc (Linux)")
def test_tmux_socket_ordner_und_sockets_im_baum_bleiben(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    altbestand = _altbestand(tmp_path, monkeypatch, {1000, 1001})
    socks = []
    for ordner in (altbestand / "tmux-1000", altbestand / "t1001"):
        ordner.mkdir()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(ordner / "default"))
        s.listen(1)
        socks.append(s)
        _alt_machen(ordner)
    try:
        assert altbestand / "tmux-1000" not in {p for p, _ in temp_ordner.reste()}, "tmux-1000 ist kein Ticket"
        ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    finally:
        for s in socks:
            s.close()
    assert (altbestand / "tmux-1000" / "default").exists()
    assert (altbestand / "t1001" / "default").exists()
    assert "Socket" in ergebnis[altbestand / "t1001"]


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_rest_mit_ungepushter_arbeit_bleibt_mit_grund(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    altbestand = _altbestand(tmp_path, monkeypatch, set(range(1000, 1100)))
    quelle = tmp_path / "quelle.git"
    _git(tmp_path, "init", "-q", "--bare", str(quelle))
    saat = tmp_path / "saat"
    _git(tmp_path, "init", "-q", str(saat))
    (saat / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(saat, "add", ".")
    _git(saat, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "saat")
    _git(saat, "push", "-q", str(quelle), "HEAD:refs/heads/main")
    _git(quelle, "symbolic-ref", "HEAD", "refs/heads/main")

    def klon(name: str) -> Path:
        ziel = altbestand / name
        _git(tmp_path, "clone", "-q", str(quelle), str(ziel))
        return ziel

    ohne_remote = altbestand / "t1002"
    _git(tmp_path, "init", "-q", str(ohne_remote))
    (ohne_remote / "a.txt").write_text("a", encoding="utf-8")
    _git(ohne_remote, "add", ".")
    _git(ohne_remote, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "lokal")
    neu = klon("t1003")
    (neu / "neu.txt").write_text("neu", encoding="utf-8")
    ungepusht = klon("t1004")
    _git(ungepusht, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "lokal")
    tief = altbestand / "t1005"
    tief.mkdir()
    _git(tmp_path, "clone", "-q", str(quelle), str(tief / "innen"))
    (tief / "innen" / ".gitignore").write_text("anders\n", encoding="utf-8")
    sauber = klon("t1006")
    (sauber / "lauf.log").write_text("ignoriert", encoding="utf-8")
    pytest_rest = altbestand / "t1007"
    _git(tmp_path, "init", "-q", str(pytest_rest / "pytest-of-bau" / "pytest-1" / "repo"))
    bleibt = [ohne_remote, neu, ungepusht, tief]
    weg = [sauber, pytest_rest]
    for p in bleibt + weg:
        _alt_machen(p)

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert [p for p in bleibt if not p.exists()] == []
    assert [p for p in weg if p.exists()] == []
    for p in bleibt:
        assert "ungesichert" in ergebnis[p], ergebnis[p]


def test_rest_namen_nur_bekannte_praefixe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    altbestand = _altbestand(tmp_path, monkeypatch, set())
    namen = [
        "t1335",
        "mut1499",
        "tmp_136",
        "beweis_1400",
        "tmux-1000",
        "tmp_2026-11-21",
        "x500",
        "systemd-1234",
        "pt-fix12",
    ]
    for name in namen:
        (altbestand / name).mkdir()
    gefunden = {p.name: nr for p, nr in temp_ordner.reste()}
    assert gefunden == {"t1335": 1335, "mut1499": 1499, "tmp_136": 136, "beweis_1400": 1400, "pt-fix12": 12}


def test_fremder_besitzer_ist_kein_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(os, "getuid"):
        pytest.skip("kein UID-Modell")
    altbestand = _altbestand(tmp_path, monkeypatch, set())
    (altbestand / "t1335").mkdir()
    echt = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: echt + 1)
    assert temp_ordner.reste() == []


def test_temp_ordner_weist_wurzel_und_home_ab_warnt_bei_langem_pfad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from to_spawn import config

    heim = tmp_path / "h"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    maschine = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    vorgabe = heim / "tmp-bau"
    for wert in ("/", "~", str(heim.parent)):
        monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", wert)
        assert temp_ordner.pfad() == vorgabe, wert
        monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER")
        maschine.write_text(json.dumps({"temp_ordner": wert}), encoding="utf-8")
        assert temp_ordner.pfad() == vorgabe, wert
    maschine.write_text(json.dumps({"temp_ordner": "~/" + "lang" * 10}), encoding="utf-8")
    with caplog.at_level("WARNING", logger="to_spawn.temp_ordner"):
        temp_ordner.pfad()
    assert "länger als" in caplog.text
    # Die Vorgabe auf dem Bau-Server bleibt kurz: tmux-Sockets unter pytest-tmp_path brauchen < 108 Zeichen.
    auf_dem_server = "/home/bau/" + config.MASCHINE_DEFAULTS["temp_ordner"].removeprefix("~/")
    assert len(auf_dem_server) <= temp_ordner.MAX_LAENGE <= 24


def test_rest_muster_ohne_ausbruch_wurzel_oder_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heim = tmp_path / "h"
    (heim / "ram").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "platte"))
    gut = str(heim / "ram" / "*")
    monkeypatch.setenv("TO_SPAWN_TEMP_RESTE", os.pathsep.join(["/*", "~/*", str(heim / "ram" / ".." / "*"), gut]))
    assert temp_ordner._muster() == [str(tmp_path / "platte" / "*"), gut]


def test_sandbox_darf_in_den_temp_ordner_schreiben(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import nest

    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    einst = nest.sandbox_einstellungen(tmp_path / "wt", tmp_path / "haupt", {}, home=tmp_path / "h")
    assert str(ziel) in json.dumps(einst)


def test_aufseher_und_bau_temp_auf_der_platte() -> None:
    wache = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in wache
    bau = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    vor_dem_start = bau.split("# Temp-Dateien", 1)[1].split("settings_path", 1)[0]
    assert "temp_ordner." in vor_dem_start, "Arbeitsordner der Bau-Session liegt vor TMPDIR auf der RAM-Platte"
