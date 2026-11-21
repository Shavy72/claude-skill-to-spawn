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


def test_pruef_lauf_hat_tmpdir_auf_konfig_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ziel = tmp_path / "platte" / "tmp-bau"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ram"))
    code = (
        "import os, tempfile; print(os.environ['TMPDIR']); print(tempfile.gettempdir())"
    )
    lauf = ausfuehren.lauf([sys.executable, "-c", code], tmp_path)
    assert lauf.code == 0, lauf.ausgabe
    assert lauf.ausgabe.split() == [str(ziel), str(ziel)]
    assert ziel.is_dir()


def test_temp_ordner_aus_maschinen_liste_und_rueckfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    maschine = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    assert temp_ordner.pfad() == tmp_path / "tmp-bau"  # Rückfall ohne Datei
    maschine.write_text(json.dumps({"temp_ordner": "~/platte/temp"}), encoding="utf-8")
    assert temp_ordner.umgebung() == {
        k: str(tmp_path / "platte" / "temp") for k in ("TMPDIR", "TEMP", "TMP")
    }


def test_bau_session_bekommt_temp_umgebung() -> None:
    # bau.py setzt die Umgebung der Claude-Session an einer Stelle; ganzer Start ist hier zu teuer.
    text = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in text


def _rest(ordner: Path, nr: int | None = None, *, alt: bool = True) -> Path:
    ordner.mkdir(parents=True)
    (ordner / "datei.txt").write_text("x", encoding="utf-8")
    if nr is not None:
        _markieren(ordner, nr)
    if alt:
        _alt_baum(ordner)
    return ordner


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_hausmeister_raeumt_temp_reste_geschlossener_tickets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fix-Runde 3: nur markierte Ordner unter der Temp-Wurzel zählen, Namen nie.
    platte = _wurzel(tmp_path, monkeypatch, {1335, 1499, 136, 1500, 1600, 1601})
    weg = [
        _rest(platte / "t1335", 1335),
        _rest(platte / "mut1499", 1499),
        _rest(platte / "irgendwas", 136),
    ]
    bleibt = [
        _rest(platte / "t1400", 1400),  # Ticket offen
        _rest(platte / "t1500", 1500, alt=False),  # frisch
        _rest(platte / "t1601"),  # kein Marker, Ticket zu
        _rest(platte / "t1600", 1600),  # Prozess darin
    ]
    (platte / "t1700").symlink_to(platte / "t1600")  # Symlink nie

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
    assert (platte / "t1700").is_symlink()
    log = Path(os.environ["TO_SPAWN_HAUSMEISTER_LOG"]).read_text(encoding="utf-8")
    assert {str(p) for p in weg} <= {json.loads(z)["ordner"] for z in log.splitlines()}


def test_schutz_hook_blockt_hausmeister_weg_nicht(tmp_path: Path) -> None:
    hook = Path.home() / ".claude" / "hooks" / "block-dangerous.sh"
    bash = shutil.which("bash")
    if not hook.is_file() or bash is None:
        pytest.skip("Schutz-Hook block-dangerous.sh nicht installiert")
    befehl = (
        "python3 ~/.claude/skills/to-spawn/to_spawn.py aufraeumen --alles --loeschen"
    )
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
    for ordner in (
        "to_spawn",
        "skripte",
        "repo-scripts",
        "leitstand",
        "aliase",
        "nest",
        "kette",
    ):
        for datei in sorted((SKILL / ordner).rglob("*")):
            if datei.suffix not in {".py", ".sh", ".ps1"} or not datei.is_file():
                continue
            name = datei.relative_to(SKILL).as_posix()
            if name not in TMP_AUSNAHMEN and "/tmp" in datei.read_text(
                encoding="utf-8", errors="replace"
            ):
                funde.append(name)
    assert funde == [], (
        f"/tmp fest verdrahtet (temp_ordner.pfad() nutzen oder Ausnahme begründen): {funde}"
    )


# --- Fix-Runde 1 (Prüfer-Urteil PR 148) ----------------------------------------------


def _alt_machen(ordner: Path) -> Path:
    # Fix-Runde 2: Frische zählt jetzt im ganzen Baum, darum altert die Vorbereitung ihn ganz.
    return _alt_baum(ordner)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-Sockets + /proc (Linux)")
def test_tmux_socket_ordner_und_sockets_im_baum_bleiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    altbestand = _wurzel(tmp_path, monkeypatch, {1000, 1001})
    socks = []
    for ordner in (altbestand / "tmux-1000", altbestand / "t1001"):
        ordner.mkdir()
        if ordner.name == "t1001":
            _markieren(ordner, 1001)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(ordner / "default"))
        s.listen(1)
        socks.append(s)
        _alt_machen(ordner)
    try:
        ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
        assert altbestand / "tmux-1000" not in ergebnis, "tmux-1000 trägt keinen Marker"
    finally:
        for s in socks:
            s.close()
    assert (altbestand / "tmux-1000" / "default").exists()
    assert (altbestand / "t1001" / "default").exists()
    assert "Socket" in ergebnis[altbestand / "t1001"]


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_rest_mit_ungepushter_arbeit_bleibt_mit_grund(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altbestand = _wurzel(tmp_path, monkeypatch, set(range(1000, 1100)))
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
    _git(
        ohne_remote,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "lokal",
    )
    neu = klon("t1003")
    (neu / "neu.txt").write_text("neu", encoding="utf-8")
    ungepusht = klon("t1004")
    _git(
        ungepusht,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "lokal",
    )
    tief = altbestand / "t1005"
    tief.mkdir()
    _git(tmp_path, "clone", "-q", str(quelle), str(tief / "innen"))
    (tief / "innen" / ".gitignore").write_text("anders\n", encoding="utf-8")
    sauber = klon("t1006")
    (sauber / "lauf.log").write_text("ignoriert", encoding="utf-8")
    pytest_rest = altbestand / "t1007"
    _git(
        tmp_path, "init", "-q", str(pytest_rest / "pytest-of-bau" / "pytest-1" / "repo")
    )
    bleibt = [ohne_remote, neu, ungepusht, tief]
    weg = [sauber, pytest_rest]
    for p in bleibt + weg:
        _alt_machen(_markieren(p, int(p.name[1:])))

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert [p for p in bleibt if not p.exists()] == []
    assert [p for p in weg if p.exists()] == []
    for p in bleibt:
        assert "ungesichert" in ergebnis[p], ergebnis[p]


def test_nur_der_marker_macht_einen_rest_nie_der_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altbestand = _wurzel(tmp_path, monkeypatch, set())
    namen = ["t1335", "mut1499", "tmp_136", "beweis_1400", "tmux-1000", "pt-fix12"]
    for name in namen:
        (altbestand / name).mkdir()
    (altbestand / "x500").mkdir()
    _markieren(altbestand / "x500", 500)
    assert [p.name for p, _ in temp_ordner.aufraeumen(loeschen=False)] == ["x500"]


def test_fremder_besitzer_ist_kein_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "getuid"):
        pytest.skip("kein UID-Modell")
    altbestand = _wurzel(tmp_path, monkeypatch, {1335})
    (altbestand / "t1335").mkdir()
    _alt_baum(_markieren(altbestand / "t1335", 1335))
    echt = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: echt + 1)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert (altbestand / "t1335").exists()
    assert "fremder Besitzer" in ergebnis[altbestand / "t1335"]


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
    maschine.write_text(
        json.dumps({"temp_ordner": "~/" + "lang" * 10}), encoding="utf-8"
    )
    with caplog.at_level("WARNING", logger="to_spawn.temp_ordner"):
        temp_ordner.pfad()
    assert "länger als" in caplog.text
    # Die Vorgabe auf dem Bau-Server bleibt kurz: tmux-Sockets unter pytest-tmp_path brauchen < 108 Zeichen.
    auf_dem_server = "/home/bau/" + config.MASCHINE_DEFAULTS[
        "temp_ordner"
    ].removeprefix("~/")
    assert len(auf_dem_server) <= temp_ordner.MAX_LAENGE <= 24


def test_temp_wurzel_ueber_symlink_aufs_home_abgelehnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heim = tmp_path / "h"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    (tmp_path / "link").symlink_to(heim)
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(tmp_path / "link"))
    assert temp_ordner.pfad() == heim / "tmp-bau"


def test_sandbox_darf_in_den_temp_ordner_schreiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import nest

    ziel = tmp_path / "platte"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    einst = nest.sandbox_einstellungen(
        tmp_path / "wt", tmp_path / "haupt", {}, home=tmp_path / "h"
    )
    assert str(ziel) in json.dumps(einst)


def test_aufseher_und_bau_temp_auf_der_platte() -> None:
    wache = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in wache
    bau = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    vor_dem_start = bau.split("# Temp-Dateien", 1)[1].split("settings_path", 1)[0]
    assert "temp_ordner." in vor_dem_start, (
        "Arbeitsordner der Bau-Session liegt vor TMPDIR auf der RAM-Platte"
    )


# --- Fix-Runde 2 (Prüfer-Urteil PR 148, dirigent_136_fix1) ---------------------------


def _alt_baum(ordner: Path) -> Path:
    """Ganzen Baum altern (``.git`` selbst ja, sein Inneres nicht; Symlinks nicht verfolgen)."""
    for wurzel, ordner_namen, datei_namen in os.walk(ordner):
        for name in [*ordner_namen, *datei_namen]:
            os.utime(Path(wurzel) / name, (ALT, ALT), follow_symlinks=False)
        ordner_namen[:] = [n for n in ordner_namen if n != ".git"]
    os.utime(ordner, (ALT, ALT))
    return ordner


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_frische_datei_tief_im_baum_haelt_den_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altbestand = _wurzel(tmp_path, monkeypatch, {1016, 1017})
    tief = altbestand / "tmp-1016"
    (tief / "a" / "b").mkdir(parents=True)
    _alt_baum(_markieren(tief, 1016))
    (tief / "a" / "b" / "neu.txt").write_text("frische Arbeit", encoding="utf-8")
    os.utime(
        tief / "a" / "b", (ALT, ALT)
    )  # nur die Datei ist frisch, Ordner darüber alt
    kontrolle = altbestand / "tmp-1017"
    (kontrolle / "a" / "b").mkdir(parents=True)
    (kontrolle / "a" / "b" / "alt.txt").write_text("alt", encoding="utf-8")
    _alt_baum(_markieren(kontrolle, 1017))

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert (tief / "a" / "b" / "neu.txt").exists(), ergebnis[tief]
    assert "frisch" in ergebnis[tief]
    assert not kontrolle.exists(), ergebnis[kontrolle]


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_reflog_stash_und_abgehaengter_head_halten_den_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altbestand = _wurzel(tmp_path, monkeypatch, set(range(1010, 1020)))
    quelle = tmp_path / "quelle.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(quelle))
    saat = tmp_path / "saat"
    _git(tmp_path, "init", "-q", "-b", "main", str(saat))
    (saat / "a.txt").write_text("a", encoding="utf-8")
    _git(saat, "add", ".")
    _git(saat, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "saat")
    _git(saat, "push", "-q", str(quelle), "HEAD:refs/heads/main")
    wer = ("-c", "user.name=t", "-c", "user.email=t@t")

    def klon(name: str) -> Path:
        ziel = altbestand / name
        _git(tmp_path, "clone", "-q", str(quelle), str(ziel))
        return ziel

    reflog = klon("t-1014")
    _git(reflog, "checkout", "-q", "--detach")
    _git(reflog, *wer, "commit", "-q", "--allow-empty", "-m", "verlassen")
    _git(reflog, "checkout", "-q", "main")
    abgehaengt = klon("t-1013")
    _git(abgehaengt, "checkout", "-q", "--detach")
    _git(abgehaengt, *wer, "commit", "-q", "--allow-empty", "-m", "abgehängt")
    stash = klon("t-1015")
    (stash / "a.txt").write_text("geändert", encoding="utf-8")
    _git(stash, *wer, "stash", "-q")
    sauber = klon("t-1019")
    bleibt = [reflog, abgehaengt, stash]
    for p in [*bleibt, sauber]:
        _alt_baum(_markieren(p, int(p.name.removeprefix("t-"))))

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert [p for p in bleibt if not p.exists()] == []
    for p in bleibt:
        assert "ungesichert" in ergebnis[p], ergebnis[p]
    assert not sauber.exists(), ergebnis[sauber]


# --- Fix-Runde 3 (Prüfer-Urteil PR 148, dirigent_136_fix2): nur Markiertes wird geräumt ----


def _markieren(ordner: Path, nr: int, repo: str | None = None) -> Path:
    """Marker wie :func:`temp_ordner.anlegen` ihn schreibt (für Ordner aus git clone/mkdir)."""
    inhalt = {
        "repo": hausmeister._ticket_repo() if repo is None else repo,
        "ticket": nr,
        "angelegt": ALT,
        "von": "test",
    }
    (ordner / ".to-spawn-temp.json").write_text(json.dumps(inhalt), encoding="utf-8")
    return ordner


def _wurzel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zu: set[int]) -> Path:
    wurzel = tmp_path / "w"  # kurz: Unix-Sockets darunter brauchen < 108 Zeichen
    wurzel.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(wurzel))
    monkeypatch.setattr(
        hausmeister,
        "_ticket_daten",
        lambda nr, slug=None: {"state": "CLOSED" if nr in zu else "OPEN"},
    )
    return wurzel


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_ordner_ohne_marker_bleibt_auch_mit_ticket_nummer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {414, 21, 1121})
    namen = ["beweis_414", "tmp_21-11", "tmp-1121"]
    for name in namen:
        (wurzel / name).mkdir()
        (wurzel / name / "bild.png").write_bytes(b"\x89PNG")
        _alt_baum(wurzel / name)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    for name in namen:
        assert (wurzel / name / "bild.png").exists(), (name, ergebnis)


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks + /proc (Linux)")
def test_symlink_ordner_oder_marker_bleibt_ziel_heil(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1070, 1071, 1072})
    aussen = tmp_path / "heim" / "t1070"
    aussen.mkdir(parents=True)
    (aussen / "arbeit.txt").write_text("echt", encoding="utf-8")
    _alt_baum(_markieren(aussen, 1070))
    (wurzel / "t1070").symlink_to(aussen)
    echter_marker = _markieren(tmp_path / "heim", 1072) / ".to-spawn-temp.json"
    mit_link = wurzel / "t1072"
    mit_link.mkdir()
    (mit_link / ".to-spawn-temp.json").symlink_to(echter_marker)
    _alt_baum(mit_link)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert (aussen / "arbeit.txt").exists()
    assert (wurzel / "t1070").is_symlink()
    assert mit_link.exists(), ergebnis
    assert echter_marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_marker_mit_fremdem_repo_oder_kaputt_bleibt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1073, 1074})
    fremd = wurzel / "t1073"
    fremd.mkdir()
    _alt_baum(_markieren(fremd, 1073, repo="fremd/anderes-repo"))
    kaputt = wurzel / "t1074"
    kaputt.mkdir()
    (kaputt / ".to-spawn-temp.json").write_text("{kein json", encoding="utf-8")
    _alt_baum(kaputt)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert fremd.exists() and "fremdes Repo" in ergebnis[fremd], ergebnis
    assert kaputt.exists() and "Marker ungültig" in ergebnis[kaputt], ergebnis


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_markierter_alter_sauberer_ordner_wird_geraeumt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1075})
    rest = wurzel / "t1075"
    rest.mkdir()
    (rest / "lauf.txt").write_text("alt", encoding="utf-8")
    _alt_baum(_markieren(rest, 1075))
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert not rest.exists(), ergebnis


def test_anlegen_schreibt_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    ordner = temp_ordner.anlegen(1076, von="test")
    assert ordner.parent == wurzel and ordner.name.startswith("t1076-")
    marker = json.loads((ordner / temp_ordner.MARKER).read_text(encoding="utf-8"))
    assert marker["ticket"] == 1076 and marker["von"] == "test"
    assert marker["repo"] == hausmeister._ticket_repo()
    assert isinstance(marker["angelegt"], float)
    assert dict(temp_ordner.aufraeumen(loeschen=False))[ordner].startswith("bleibt:")
    with pytest.raises(ValueError):
        temp_ordner.anlegen(0)
