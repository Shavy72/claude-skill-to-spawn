"""Hausmeister #1368: räumt fertige Ticket-Ordner auf (Vertrag: docs/verify-hard/1368/API.md).

Weg-Tests mit ECHTEN Git-Ordnern (Bare-Remote, Hauptklon, ``git worktree add``,
eigene Test-Git-Konfig mit ``insteadOf``) und echten Prozessen. Gestellt ist nur
``gh`` (externer Dienst) — es antwortet je Ticket mit state/closedAt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, hausmeister

ORIGIN = "https://github.com/Shavy72/duoplus-management"
STUNDE = 3600.0

GH_ATTRAPPE = """
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["issue", "view"]:
    daten = json.load(open(os.environ["GH_TICKETS"]))
    e = daten.get(a[2])
    if e is None:
        sys.exit(1)
    print(json.dumps(e))
    sys.exit(0)
sys.exit(1)
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Welt:
    tmp: Path
    heim: Path
    bare: Path
    haupt: Path
    log: Path
    tickets: Path
    jetzt: float

    def ticket(self, nr: int, state: str, vor_stunden: float | None = None) -> None:
        daten = json.loads(self.tickets.read_text(encoding="utf-8"))
        eintrag: dict = {"state": state}
        if vor_stunden is not None:
            eintrag["closedAt"] = iso(self.jetzt - vor_stunden * STUNDE)
        daten[str(nr)] = eintrag
        self.tickets.write_text(json.dumps(daten), encoding="utf-8")

    def worktree(self, nr: int, schmutzig: bool = False) -> Path:
        wt = self.heim / "wt" / f"wt-{nr}"
        zweig = f"ticket/{nr}-test"
        git(self.haupt, "worktree", "add", "-q", "-b", zweig, str(wt))
        (wt / "arbeit.txt").write_text("fertig\n", encoding="utf-8")
        git(wt, "add", "arbeit.txt")
        git(wt, "commit", "-q", "-m", "arbeit")
        git(wt, "push", "-q", "-u", "origin", zweig)
        if schmutzig:
            (wt / "geaendert.txt").write_text("ungesichert\n", encoding="utf-8")
        return wt

    def klon(self, name: str) -> Path:
        ziel = self.heim / name
        git(self.tmp, "clone", "-q", ORIGIN, str(ziel))
        return ziel

    def zeilen(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [
            json.loads(z)
            for z in self.log.read_text(encoding="utf-8").splitlines()
            if z.strip()
        ]

    def im_bare(self, *args: str) -> str:
        return git(self.bare, *args)

    def aufraeumen(self, ordner: Path, **kw):
        return hausmeister.aufraeumen(ordner, jetzt=self.jetzt, **kw)


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Welt:
    heim = tmp_path / "heim"
    (heim / "wt").mkdir(parents=True)
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(bare)], check=True
    )
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        f"[user]\n\tname = Test\n\temail = t@example.org\n[init]\n\tdefaultBranch = master\n"
        f'[url "{bare.as_posix()}"]\n\tinsteadOf = {ORIGIN}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    saat = tmp_path / "saat"
    subprocess.run(["git", "init", "-q", str(saat)], check=True)
    (saat / "README.md").write_text("start\n", encoding="utf-8")
    git(saat, "add", "README.md")
    git(saat, "commit", "-q", "-m", "start")
    git(saat, "push", "-q", str(bare), "HEAD:refs/heads/master")
    haupt = tmp_path / "haupt" / "duoplus-management"
    haupt.parent.mkdir()
    git(tmp_path, "clone", "-q", ORIGIN, str(haupt))

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    attrappe(bin_, "gh", GH_ATTRAPPE)
    tickets = tmp_path / "tickets.json"
    tickets.write_text("{}", encoding="utf-8")
    log = tmp_path / "hausmeister.jsonl"
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TICKETS", str(tickets))
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_WURZELN",
        os.pathsep.join([f"{heim}/wt/*", f"{heim}/wt-*", f"{heim}/duoplus-*"]),
    )
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log))
    return Welt(tmp_path, heim, bare, haupt, log, tickets, time.time())


# 1
def test_sauber_ticket_zu_ueber_24h_wird_geloescht(welt: Welt) -> None:
    wt = welt.worktree(11)
    welt.ticket(11, "CLOSED", 48)
    r = welt.aufraeumen(wt)
    assert (r.art, r.trocken, r.ticket) == ("gelöscht", True, 11)
    assert wt.is_dir()


def test_sauber_loeschen_entfernt_ordner_und_worktree(welt: Welt) -> None:
    wt = welt.worktree(11)
    welt.ticket(11, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht" and not r.trocken
    assert not wt.exists()
    assert str(wt) not in git(welt.haupt, "worktree", "list")


# 2
def test_geaendert_wird_gesichert_und_geloescht_trocken(welt: Welt) -> None:
    wt = welt.worktree(12, schmutzig=True)
    welt.ticket(12, "CLOSED", 48)
    r = welt.aufraeumen(wt)
    assert r.art == "gesichert und gelöscht" and r.trocken
    assert wt.is_dir()
    assert "sicherung/12" not in welt.im_bare("branch", "--list")


def test_geaendert_loeschen_sichert_inhalt_im_remote(welt: Welt) -> None:
    wt = welt.worktree(12, schmutzig=True)
    welt.ticket(12, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not wt.exists()
    assert welt.im_bare("show", "sicherung/12:geaendert.txt") == "ungesichert\n"


# 3
def test_ticket_offen_bleibt(welt: Welt) -> None:
    wt = welt.worktree(13)
    welt.ticket(13, "OPEN")
    assert welt.aufraeumen(wt).art == "bleibt"
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "offen" in r.grund
    assert wt.is_dir()


# 4
def test_prozess_im_ordner_bleibt(welt: Welt) -> None:
    wt = welt.worktree(14)
    welt.ticket(14, "CLOSED", 48)
    (wt / "unter").mkdir()
    proc = subprocess.Popen(["sleep", "60"], cwd=wt / "unter")
    try:
        assert welt.aufraeumen(wt).art == "bleibt"
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


# 5
def test_ticket_unter_24h_zu_bleibt(welt: Welt) -> None:
    wt = welt.worktree(15)
    welt.ticket(15, "CLOSED", 2)
    assert welt.aufraeumen(wt).art == "bleibt"
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt"
    assert wt.is_dir()


# 6 (B16)
def _klon_mit_nebenzweig_commit(welt: Welt, spec: int) -> Path:
    klon = welt.klon(f"duoplus-{spec}")
    git(klon, "checkout", "-q", "-b", f"ticket/{spec}-aktiv")
    git(klon, "push", "-q", "-u", "origin", f"ticket/{spec}-aktiv")
    git(klon, "checkout", "-q", "-b", f"ticket/{spec}-probelauf")
    (klon / "probe.txt").write_text("nur lokal\n", encoding="utf-8")
    git(klon, "add", "probe.txt")
    git(klon, "commit", "-q", "-m", "probelauf nur lokal")
    git(klon, "checkout", "-q", f"ticket/{spec}-aktiv")
    return klon


def test_b16_nebenzweig_commit_nie_ohne_sicherung_geloescht(welt: Welt) -> None:
    spec = 1368
    klon = _klon_mit_nebenzweig_commit(welt, spec)
    nebenzweig_commit = git(klon, "rev-parse", f"ticket/{spec}-probelauf").strip()
    welt.ticket(spec, "CLOSED", 48)
    assert welt.aufraeumen(klon).art == "gesichert und gelöscht"
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not klon.exists()
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", nebenzweig_commit, f"sicherung/{spec}"],
        cwd=str(welt.bare),
        check=True,
    )


def test_b16_alte_luecke_sichern_liefert_bei_sauberem_baum_none(welt: Welt) -> None:
    """Dokumentiert das alte Verhalten: ``aufpasser.sichern`` ohne Zusatz-Parameter
    sieht nur den Arbeitsbaum, nicht lokale Nebenzweige."""
    klon = _klon_mit_nebenzweig_commit(welt, 1368)
    assert aufpasser.sichern(klon, 1368) is None


# 7
def test_env_dateien_landen_nie_in_der_sicherung(welt: Welt) -> None:
    wt = welt.worktree(17)
    welt.ticket(17, "CLOSED", 48)
    (wt / "web").mkdir()
    (wt / "sub").mkdir()
    (wt / ".env").write_text("A=1\n", encoding="utf-8")
    (wt / "web" / ".env.local").write_text("B=2\n", encoding="utf-8")
    (wt / "sub" / "x.env").write_text("C=3\n", encoding="utf-8")
    (wt / "normal.txt").write_text("ok\n", encoding="utf-8")
    # S1: Geheimnisse dürfen nur weg, weil das Hauptrepo dieselbe Datei hat.
    for rel in (".env", "web/.env.local", "sub/x.env"):
        ziel = welt.haupt / rel
        ziel.parent.mkdir(exist_ok=True)
        ziel.write_bytes((wt / rel).read_bytes())
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht"
    baum = welt.im_bare("ls-tree", "-r", "--name-only", "sicherung/17").splitlines()
    assert "normal.txt" in baum
    assert not [p for p in baum if p.endswith(".env") or ".env." in p]


# 8
def test_ohne_ticketnummer_und_fremdes_repo_gehen_an_david(welt: Welt) -> None:
    spiel = welt.heim / "wt" / "spielwiese"
    git(welt.haupt, "worktree", "add", "-q", "-b", "spielwiese", str(spiel))
    fremd = welt.heim / "wt" / "wt-77"
    subprocess.run(["git", "init", "-q", str(fremd)], check=True)
    git(fremd, "remote", "add", "origin", "https://github.com/fremd/x")
    welt.ticket(77, "CLOSED", 48)
    for ordner in (spiel, fremd):
        for loeschen in (False, True):
            r = welt.aufraeumen(ordner, loeschen=loeschen)
            assert r.art == "bleibt" and r.fuer_david and r.grund
            assert ordner.is_dir()
    assert (spiel / "README.md").is_file()
    liste = hausmeister.liste_fuer_david(welt.log)
    assert spiel in liste and fremd in liste
    assert len(liste) == len(set(liste)) == 2


# 9
def test_loeschen_nur_im_hausmeister() -> None:
    muster = ('worktree", "remove"', "worktree remove", "rmtree(")
    # ponytail: probesitz.py räumt nur eigene Probe-Sitze weg (kein Ticket-Arbeitsordner).
    ausnahmen = {"to_spawn/probesitz.py", "to_spawn/hausmeister.py"}
    dateien = sorted((SKILL / "to_spawn").glob("*.py")) + [SKILL / "to_spawn.py"]
    treffer = []
    for datei in dateien:
        rel = datei.relative_to(SKILL).as_posix()
        text = datei.read_text(encoding="utf-8")
        if rel not in ausnahmen and any(m in text for m in muster):
            treffer.append(rel)
    assert not treffer, f"Löschen außerhalb des Hausmeisters: {treffer}"
    assert any(
        m in (SKILL / "to_spawn" / "hausmeister.py").read_text(encoding="utf-8")
        for m in muster
    )


def test_zweig_loeschen_nur_im_hausmeister() -> None:
    """#1439: keine Lösch-Refspec, kein ``push --delete``, kein ``branch -D`` außer im Hausmeister."""
    import re

    muster = [
        re.compile(r"""(?:^|[\s"'`(\[,]):refs/heads/""", re.M),
        re.compile(r"""push\b[^\n]{0,60}?(?:--delete|[\s"']-d\b|[\s"']-d["'])"""),
        re.compile(r"""branch\b[^\n]{0,60}?(?:--delete|[\s"']-D\b|[\s"']-D["'])"""),
    ]
    dateien = sorted((SKILL / "to_spawn").glob("*.py")) + [SKILL / "to_spawn.py"]
    treffer = []
    for datei in dateien:
        rel = datei.relative_to(SKILL).as_posix()
        # ponytail: probesitz.py löscht nur seinen eigenen probesitz-<N>-Wegwerfzweig.
        if rel in ("to_spawn/hausmeister.py", "to_spawn/probesitz.py"):
            continue
        text = datei.read_text(encoding="utf-8")
        if any(m.search(text) for m in muster):
            treffer.append(rel)
    assert not treffer, f"Zweig-Löschen außerhalb des Hausmeisters: {treffer}"


# 10
def test_je_aufruf_genau_eine_json_zeile(welt: Welt) -> None:
    wt = welt.worktree(20)
    welt.ticket(20, "OPEN")
    welt.aufraeumen(wt)
    zeilen = welt.zeilen()
    assert len(zeilen) == 1
    assert {
        "zeit",
        "ordner",
        "art",
        "grund",
        "ticket",
        "trocken",
        "fuer_david",
    } <= zeilen[0].keys()
    assert zeilen[0]["ordner"] == str(wt) and zeilen[0]["art"] == "bleibt"
    assert zeilen[0]["ticket"] == 20 and zeilen[0]["trocken"] is True
    welt.aufraeumen(wt, loeschen=True)
    assert len(welt.zeilen()) == 2
    assert welt.zeilen()[1]["trocken"] is False


# --- Fixrunde 1 (Prüfpanel-Befunde) ---------------------------------------------


def _commit(ordner: Path, datei: str, inhalt: str, *, push: bool = False) -> str:
    pfad = ordner / datei
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    git(ordner, "add", "-f", datei)
    git(ordner, "commit", "-q", "-m", f"{datei} dazu")
    if push:
        git(ordner, "push", "-q")
    return git(ordner, "rev-parse", "HEAD").strip()


def _ist_vorfahr(welt: Welt, sha: str, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, ref],
            cwd=str(welt.bare),
            check=False,
        ).returncode
        == 0
    )


# M1
def test_m1_symlink_im_bereich_nach_aussen_bleibt(welt: Welt) -> None:
    aussen = welt.tmp / "aussen" / "wt-30"
    git(welt.haupt, "worktree", "add", "-q", "-b", "ticket/30-x", str(aussen))
    link = welt.heim / "wt" / "wt-30"
    link.symlink_to(aussen)
    welt.ticket(30, "CLOSED", 48)
    r = welt.aufraeumen(link, loeschen=True)
    assert r.art == "bleibt" and r.grund
    assert aussen.is_dir() and link.is_symlink()


# M2 / S4
def test_m2_veralteter_remote_zweig_wird_gesichert(welt: Welt) -> None:
    wt = welt.worktree(31)
    commit = git(wt, "rev-parse", "HEAD").strip()
    welt.im_bare("branch", "-D", "ticket/31-test")  # Tracking-Ref im Ordner veraltet
    welt.ticket(31, "CLOSED", 48)
    assert welt.aufraeumen(wt).art == "gesichert und gelöscht"
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not wt.exists()
    assert _ist_vorfahr(welt, commit, "sicherung/31")


def test_m2_fetch_scheitert_bleibt(welt: Welt) -> None:
    wt = welt.worktree(32)
    git(wt, "config", "remote.origin.uploadpack", "/bin/false")
    welt.ticket(32, "CLOSED", 48)
    for loeschen in (False, True):
        r = welt.aufraeumen(wt, loeschen=loeschen)
        assert r.art == "bleibt" and r.fehler and "fetch" in r.grund
    assert wt.is_dir()


# S1
def test_s1_nur_env_geaendert_bleibt_fuer_david(welt: Welt) -> None:
    wt = welt.worktree(33)
    (wt / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    welt.ticket(33, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and ".env" in r.grund
    assert (wt / ".env").read_text(encoding="utf-8") == "GEHEIM=1\n"
    assert "sicherung/33" not in welt.im_bare("branch", "--list")


def test_s1_env_gleich_im_hauptrepo_darf_weg(welt: Welt) -> None:
    wt = welt.worktree(34)
    for ordner in (wt, welt.haupt):
        (ordner / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    welt.ticket(34, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht" and r.sicherung is None
    assert not wt.exists()
    assert (welt.haupt / ".env").is_file()


# S2
def test_s2_ignorierte_datei_bleibt(welt: Welt) -> None:
    wt = welt.worktree(35)
    _commit(wt, ".gitignore", "daten/\n__pycache__/\n", push=True)
    (wt / "daten").mkdir()
    (wt / "daten" / "wichtig.db").write_text("x", encoding="utf-8")
    welt.ticket(35, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "daten/wichtig.db" in r.grund
    assert (wt / "daten" / "wichtig.db").is_file()


def test_s2_nur_erzeugnisse_ignoriert_wird_geloescht(welt: Welt) -> None:
    wt = welt.worktree(36)
    _commit(wt, ".gitignore", "__pycache__/\n*.log\nnode_modules/\n", push=True)
    for datei in ("__pycache__/a.pyc", "lauf.log", "node_modules/p/index.js"):
        (wt / datei).parent.mkdir(parents=True, exist_ok=True)
        (wt / datei).write_text("x", encoding="utf-8")
    welt.ticket(36, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


def test_s2_ignorierte_env_im_klon_bleibt_fuer_david(welt: Welt) -> None:
    klon = welt.klon("duoplus-37")
    (klon / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
    (klon / ".env").write_text("GEHEIM=1\n", encoding="utf-8")
    welt.ticket(37, "CLOSED", 48)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david
    assert (klon / ".env").is_file()


def test_s2_show_untracked_no_falle(welt: Welt) -> None:
    klon = welt.klon("duoplus-38")
    git(klon, "config", "status.showUntrackedFiles", "no")
    (klon / "neu.txt").write_text("ungesichert\n", encoding="utf-8")
    welt.ticket(38, "CLOSED", 48)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert welt.im_bare("show", "sicherung/38:neu.txt") == "ungesichert\n"


# S3
def test_s3_stash_im_klon_bleibt(welt: Welt) -> None:
    klon = welt.klon("duoplus-39")
    (klon / "README.md").write_text("geändert\n", encoding="utf-8")
    git(klon, "stash", "-q")
    welt.ticket(39, "CLOSED", 48)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and "Stash" in r.grund
    assert klon.is_dir()


def test_s3_nur_getaggter_commit_im_klon_wird_gesichert(welt: Welt) -> None:
    klon = welt.klon("duoplus-40")
    git(klon, "checkout", "-q", "-b", "weg")
    commit = _commit(klon, "nur-tag.txt", "x\n")
    git(klon, "tag", "-a", "-m", "t", "nur-lokal")
    git(klon, "checkout", "-q", "master")
    git(klon, "branch", "-q", "-D", "weg")
    welt.ticket(40, "CLOSED", 48)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert _ist_vorfahr(welt, commit, "sicherung/40")


# S5
def test_s5_eingebettetes_repo_bleibt(welt: Welt) -> None:
    wt = welt.worktree(41)
    innen = wt / "innen"
    subprocess.run(["git", "init", "-q", str(innen)], check=True)
    _commit(innen, "lokal.txt", "nur hier\n")
    welt.ticket(41, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "innen" in r.grund
    assert (innen / "lokal.txt").is_file()


# S6
def test_s6_offene_datei_im_ordner_bleibt(welt: Welt) -> None:
    wt = welt.worktree(42)
    welt.ticket(42, "CLOSED", 48)
    with (wt / "arbeit.txt").open("rb") as f:
        proc = subprocess.Popen(["sleep", "60"], stdin=f, cwd=welt.tmp)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


def test_s6_cwd_unlesbar_bleibt(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = welt.worktree(43)
    welt.ticket(43, "CLOSED", 48)
    proc = subprocess.Popen(["sleep", "60"], cwd=welt.tmp)
    echt = os.readlink

    def readlink(pfad):  # /proc-Lesefehler (kein Rechte-Fehler) nachstellen
        if str(pfad) == f"/proc/{proc.pid}/cwd":
            raise OSError(5, "Eingabe-/Ausgabefehler")
        return echt(pfad)

    monkeypatch.setattr(hausmeister.os, "readlink", readlink)
    try:
        r = welt.aufraeumen(wt, loeschen=True)
        assert r.art == "bleibt" and str(proc.pid) in r.grund
        assert wt.is_dir()
    finally:
        proc.kill()
        proc.wait()


# S7
def test_s7_sichern_liefert_none_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = welt.worktree(44, schmutzig=True)
    welt.ticket(44, "CLOSED", 48)
    monkeypatch.setattr(aufpasser, "sichern", lambda *a, **kw: None)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt"
    assert (wt / "geaendert.txt").is_file()


def test_s7_neue_datei_nach_sicherung_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = welt.worktree(45, schmutzig=True)
    welt.ticket(45, "CLOSED", 48)
    echt = aufpasser.sichern

    def sichern_dann_neue_datei(*a, **kw):
        sha = echt(*a, **kw)
        (wt / "spaet.txt").write_text("nach der Sicherung\n", encoding="utf-8")
        return sha

    monkeypatch.setattr(aufpasser, "sichern", sichern_dann_neue_datei)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt"
    assert (wt / "spaet.txt").is_file()


# S10
def test_s10_env_in_ungepushtem_commit_bleibt(welt: Welt) -> None:
    wt = welt.worktree(46)
    _commit(wt, "web/.env.local", "GEHEIM=1\n")
    welt.ticket(46, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and ".env" in r.grund
    assert wt.is_dir()
    assert "sicherung/46" not in welt.im_bare("branch", "--list")


# T1
def test_t1_sicherung_scheitert_bleibt(welt: Welt) -> None:
    wt = welt.worktree(47, schmutzig=True)
    attrappe(welt.bare / "hooks", "pre-receive", "raise SystemExit(1)\n")
    welt.ticket(47, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler and "Sicherung" in r.grund
    assert (wt / "geaendert.txt").is_file()


# T2
def test_t2_gh_fehler_bleibt(welt: Welt) -> None:
    wt = welt.worktree(48)  # Attrappe kennt Ticket 48 nicht ⇒ gh scheitert
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler and "nicht abfragbar" in r.grund
    assert wt.is_dir()


def test_t2_closed_ohne_closedat_bleibt(welt: Welt) -> None:
    wt = welt.worktree(49)
    welt.ticket(49, "CLOSED")
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "Schließzeit" in r.grund
    assert wt.is_dir()


def test_t2_name_und_zweig_widersprechen_sich(welt: Welt) -> None:
    wt = welt.heim / "wt" / "wt-50"
    git(welt.haupt, "worktree", "add", "-q", "-b", "ticket/51-x", str(wt))
    welt.ticket(50, "CLOSED", 48)
    welt.ticket(51, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "widersprechen" in r.grund
    assert wt.is_dir()


# S8
def test_s8_cli_exit_nur_bei_fehler_ungleich_null(welt: Welt) -> None:
    offen = welt.worktree(52)
    welt.ticket(52, "OPEN")
    unbekannt = welt.worktree(53)

    def cli(ordner: Path) -> int:
        return subprocess.run(
            [sys.executable, str(SKILL / "to_spawn.py"), "aufraeumen", str(ordner)],
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    assert cli(offen) == 0
    assert cli(unbekannt) != 0


# S9
def test_s9_log_nicht_schreibbar_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = welt.worktree(54)
    welt.ticket(54, "CLOSED", 48)
    (welt.tmp / "keinordner").write_text("", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(welt.tmp / "keinordner" / "x"))
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fehler and "Log" in r.grund
    assert wt.is_dir()


# C3
def test_c3_klon_mit_verknuepftem_worktree_bleibt(welt: Welt) -> None:
    klon = welt.klon("duoplus-1368")
    git(klon, "worktree", "add", "-q", "-b", "ticket/1368-x", str(welt.tmp / "wt-x"))
    welt.ticket(1368, "CLOSED", 48)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and "Worktree" in r.grund
    assert klon.is_dir()


# C4
def test_c4_sauberer_baum_ungepushter_head_wird_gesichert(welt: Welt) -> None:
    wt = welt.worktree(55)
    commit = _commit(wt, "lokal.txt", "nur lokal\n")
    welt.ticket(55, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht" and r.sicherung
    assert not wt.exists()
    assert _ist_vorfahr(welt, commit, "sicherung/55")


@pytest.mark.parametrize("datei", ["mein_zugang.txt", "zugang/a.txt", "x.pem", "x.key"])
def test_c4_geheimnis_muster_bleibt_und_nie_in_sicherung(
    welt: Welt, datei: str
) -> None:
    wt = welt.worktree(56)
    (wt / datei).parent.mkdir(exist_ok=True)
    (wt / datei).write_text("geheim\n", encoding="utf-8")
    (wt / "normal.txt").write_text("ok\n", encoding="utf-8")
    welt.ticket(56, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david
    assert (wt / datei).is_file()
    assert "sicherung/56" not in welt.im_bare("branch", "--list")
