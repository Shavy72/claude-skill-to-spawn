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

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, hausmeister  # noqa: E402

ORIGIN = "https://github.com/Shavy72/duoplus-management"
STUNDE = 3600.0

GH_ATTRAPPE = """#!/bin/sh
exec {py} -c '
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
' "$@"
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
        f'[url "{bare}"]\n\tinsteadOf = {ORIGIN}\n',
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
    gh = bin_ / "gh"
    gh.write_text(GH_ATTRAPPE.format(py=sys.executable), encoding="utf-8")
    gh.chmod(0o755)
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
