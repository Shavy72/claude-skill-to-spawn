"""Hausmeister #1439: fertige ``ticket/*``-Zweige räumt der Hausmeister weg (Remote + lokal).

Weg-Tests mit echtem Wegwerf-Git und lokalem Bare-Remote aus ``test_hausmeister_1368``
(Fixture ``welt``). Git ist nie attrappiert; gestellt sind nur gh (Ticket-Stand) und die
Remote-Liste (``TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS``, ``..._HAUPTREPOS``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    ORIGIN,
    SKILL,
    Welt,
    git,
    welt,
)

from to_spawn import hausmeister

TAG = 24 * 3600.0


@pytest.fixture
def zw(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", ORIGIN)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "1")  # #1491: Löschen nur mit Schalter
    return welt


def _heads(w: Welt) -> list[str]:
    return sorted(w.im_bare("for-each-ref", "--format=%(refname:short)").split())


def _tip(w: Welt, zweig: str) -> str:
    return w.im_bare("rev-parse", f"refs/heads/{zweig}").strip()


def _commit(ordner: Path, datei: str, text: str) -> str:
    (ordner / datei).write_text(text, encoding="utf-8")
    git(ordner, "add", datei)
    git(ordner, "commit", "-q", "-m", f"c {datei}")
    return git(ordner, "rev-parse", "HEAD").strip()


def _zeilen_mit(w: Welt, zweig: str) -> list[dict]:
    return [z for z in w.zeilen() if zweig in str(z.get("zweig", ""))]


def test_rot_beweis_uebernommener_zweig_nach_taeglichem_lauf_weg(zw: Welt) -> None:
    """Schlangen-Löschung gescheitert: der übernommene Zweig muss nach dem Tageslauf weg sein."""
    wt = zw.worktree(40)
    git(wt, "push", "-q", "origin", "HEAD:master")  # Schlange hat übernommen
    zw.ticket(40, "CLOSED", vor_stunden=48)
    assert "ticket/40-test" in _heads(zw)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=zw.jetzt)
    assert "ticket/40-test" not in _heads(zw)


def test_rebase_und_gemeldeter_stand_zweig_weg_ohne_sicherung(zw: Welt) -> None:
    """Neu aufgesetzter Zweig, Schlange meldet genau diesen Stand: weg, keine Sicherung."""
    wt = zw.worktree(41)
    zw.ticket(41, "OPEN")
    _commit(zw.haupt, "anderes.txt", "x\n")
    git(zw.haupt, "push", "-q", "origin", "HEAD:master")
    git(wt, "fetch", "-q", "origin")
    git(wt, "rebase", "-q", "origin/master")
    git(wt, "push", "-q", "-f", "origin", "ticket/41-test")
    tip = _tip(zw, "ticket/41-test")
    r = hausmeister.zweig_aufraeumen(
        ORIGIN, "ticket/41-test", uebernommen_tip=tip, loeschen=True
    )
    assert r.art == "gelöscht" and not r.fehler and not r.trocken
    assert "ticket/41-test" not in _heads(zw)
    assert not [h for h in _heads(zw) if h.startswith("sicherung/")]


def test_ticket_zu_commit_nirgends_sonst_wird_erst_gesichert(
    zw: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket zu, Commit nur auf dem Zweig: sicherung/<N> trägt ihn, dann ist der Zweig weg."""
    zw.worktree(42)
    zw.ticket(42, "CLOSED", vor_stunden=48)
    commit = _tip(zw, "ticket/42-test")
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/42-test", loeschen=True)
    assert r.art == "gesichert+gelöscht" and not r.fehler
    assert "ticket/42-test" not in _heads(zw)
    assert "sicherung/42" in _heads(zw)
    assert commit in zw.im_bare("rev-list", "refs/heads/sicherung/42").split()
    # #1371 erkennt die Anlegezeit der Sicherung: nach 91 Tagen geht sie.
    erg = hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=zw.jetzt + 91 * TAG)
    assert [(e.zweig, e.art) for e in erg] == [("sicherung/42", "gelöscht")]


def test_ticket_offen_zweig_bleibt(zw: Welt) -> None:
    """Ticket offen, keine Schlangen-Meldung: der Zweig bleibt."""
    zw.worktree(43)
    zw.ticket(43, "OPEN")
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/43-test", loeschen=True)
    assert r.art == "bleibt" and r.grund
    assert "ticket/43-test" in _heads(zw)


def test_nach_der_pruefung_nachgeschoben_bleibt_dann_naechster_lauf_entscheidet_neu(
    zw: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwischen Prüfung und Löschen nachgeschoben: Lease hält, Log-Zeile, 2. Lauf neu."""
    wt = zw.worktree(44)
    zw.ticket(44, "CLOSED", vor_stunden=48)
    echt = hausmeister.zweig_fertig

    def dazwischen(*a, **kw):
        erg = echt(*a, **kw)
        _commit(wt, "nachgeschoben.txt", "neu\n")
        git(wt, "push", "-q", "origin", "ticket/44-test")
        return erg

    monkeypatch.setattr(hausmeister, "zweig_fertig", dazwischen)
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/44-test", loeschen=True)
    assert r.art == "bleibt"
    assert "ticket/44-test" in _heads(zw)
    assert _zeilen_mit(zw, "ticket/44-test")
    monkeypatch.setattr(hausmeister, "zweig_fertig", echt)
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/44-test", loeschen=True)
    assert r.art == "gesichert+gelöscht"
    assert "ticket/44-test" not in _heads(zw)
    assert "sicherung/44" in _heads(zw)


def test_ohne_ticket_nummer_nie_angefasst_steht_in_liste_fuer_david(zw: Welt) -> None:
    """``ticket/ohne-nummer``: nie angefasst, fuer_david, in der Liste für David."""
    _commit(zw.haupt, "x.txt", "x\n")
    git(zw.haupt, "push", "-q", "origin", "HEAD:refs/heads/ticket/ohne-nummer")
    r = hausmeister.zweig_aufraeumen(ORIGIN, "ticket/ohne-nummer", loeschen=True)
    assert r.art == "bleibt" and r.fuer_david
    assert "ticket/ohne-nummer" in _heads(zw)
    assert any("ohne-nummer" in str(p) for p in hausmeister.liste_fuer_david(zw.log))


def test_remote_nicht_erreichbar_wirft_nie_naechster_lauf_loescht(zw: Welt) -> None:
    """Remote weg: fehler=True + Log-Zeile, kein Wurf; Remote zurück: nächster Lauf löscht."""
    wt = zw.worktree(46)
    git(wt, "push", "-q", "origin", "HEAD:master")
    zw.ticket(46, "CLOSED", vor_stunden=48)
    weg = zw.tmp / "weg.git"
    zw.bare.rename(weg)
    erg = hausmeister.zweige_aufraeumen(loeschen=True, jetzt=zw.jetzt)
    assert erg and all(e.fehler for e in erg)
    assert zw.zeilen()
    weg.rename(zw.bare)
    assert "ticket/46-test" in _heads(zw)
    erg = hausmeister.zweige_aufraeumen(loeschen=True, jetzt=zw.jetzt + 900)
    assert "ticket/46-test" not in _heads(zw)
    assert any(e.zweig == "ticket/46-test" and e.art == "gelöscht" for e in erg)


def _lokale_zweige(w: Welt) -> list[str]:
    return git(w.haupt, "branch", "--format=%(refname:short)").split()


def test_worktree_entfernt_lokaler_zweig_ohne_eigenen_commit_weg(zw: Welt) -> None:
    """``_loeschen`` entfernt mit dem Worktree auch den lokalen Zweig."""
    wt = zw.heim / "wt" / "wt-47"
    git(zw.haupt, "worktree", "add", "-q", "-b", "ticket/47-test", str(wt))
    zw.ticket(47, "CLOSED", vor_stunden=48)
    hausmeister._loeschen(wt, True)
    assert not wt.exists()
    assert "ticket/47-test" not in _lokale_zweige(zw)


def test_worktree_entfernt_lokaler_zweig_mit_eigenem_commit_erst_gesichert(
    zw: Welt,
) -> None:
    """Eigener Commit im lokalen Zweig: erst sicherung/<N>, dann Zweig weg."""
    wt = zw.heim / "wt" / "wt-48"
    git(zw.haupt, "worktree", "add", "-q", "-b", "ticket/48-test", str(wt))
    commit = _commit(wt, "eigen.txt", "nur lokal\n")
    zw.ticket(48, "CLOSED", vor_stunden=48)
    hausmeister._loeschen(wt, True)
    assert "ticket/48-test" not in _lokale_zweige(zw)
    assert "sicherung/48" in _heads(zw)
    assert commit in zw.im_bare("rev-list", "refs/heads/sicherung/48").split()


def _veralteter_ref(w: Welt, name: str) -> None:
    """Remote-Zweig anlegen, im Hauptrepo holen, im Remote wieder löschen: Tracking-Ref veraltet."""
    _commit(w.haupt, f"{name}.txt", "x\n")
    git(w.haupt, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
    git(w.haupt, "fetch", "-q", "origin")
    git(w.bare, "update-ref", "-d", f"refs/heads/{name}")


def _tracking(w: Welt) -> list[str]:
    return git(w.haupt, "for-each-ref", "--format=%(refname:short)", "refs/remotes").split()


def test_taeglicher_lauf_fetch_prune_und_worktree_prune_hoechstens_einmal_am_tag(
    zw: Welt,
) -> None:
    """Zweiter Lauf am selben Tag ``[]`` und ohne Prune; am nächsten Tag wieder."""
    _veralteter_ref(zw, "zombie1")
    kaputt = zw.heim / "wt" / "wt-49"
    git(zw.haupt, "worktree", "add", "-q", "-b", "wip/49", str(kaputt))
    subprocess.run(["rm", "-rf", str(kaputt)], check=True)
    hausmeister.zweige_aufraeumen(loeschen=True, jetzt=zw.jetzt)
    assert "origin/zombie1" not in _tracking(zw)
    assert str(kaputt) not in git(zw.haupt, "worktree", "list")
    _veralteter_ref(zw, "zombie2")
    assert hausmeister.zweige_aufraeumen(loeschen=True, jetzt=zw.jetzt + 3600) == []
    assert "origin/zombie2" in _tracking(zw)
    hausmeister.zweige_aufraeumen(loeschen=True, jetzt=zw.jetzt + 25 * 3600)
    assert "origin/zombie2" not in _tracking(zw)


def test_probelauf_loescht_nichts_und_nennt_gruppen(zw: Welt, tmp_path: Path) -> None:
    """Probelauf: ``ls-remote`` vorher = nachher, Datei mit Gruppen je Zweig."""
    zw.worktree(51)
    zw.ticket(51, "CLOSED", vor_stunden=48)
    zw.worktree(52)
    zw.ticket(52, "OPEN")
    _commit(zw.haupt, "o.txt", "o\n")
    git(zw.haupt, "push", "-q", "origin", "HEAD:refs/heads/ticket/ohne-nummer")
    vorher = git(zw.haupt, "ls-remote", "origin")
    datei = tmp_path / "probe.txt"
    pfad = hausmeister.zweige_probelauf(datei)
    assert git(zw.haupt, "ls-remote", "origin") == vorher
    text = Path(pfad).read_text(encoding="utf-8").lower()
    for z in ("ticket/51-test", "ticket/52-test", "ticket/ohne-nummer"):
        assert z in text
    assert "bleibt" in text and "ohne ticket-nummer" in text and "löschen" in text


def test_cli_aufraeumen_zweig_exit_0_eine_zeile(zw: Welt) -> None:
    """``to_spawn.py aufraeumen --zweig …`` löscht den übernommenen Zweig, Exit 0, 1 Zeile."""
    wt = zw.worktree(53)
    zw.ticket(53, "OPEN")
    tip = _tip(zw, "ticket/53-test")
    p = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "aufraeumen",
            "--zweig",
            "ticket/53-test",
            "--repo",
            ORIGIN,
            "--uebernommen-tip",
            tip,
            "--loeschen",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        cwd=str(SKILL),
    )
    assert p.returncode == 0, p.stderr
    assert len(p.stdout.strip().splitlines()) == 1
    assert "ticket/53-test" not in _heads(zw)
    assert wt.exists()


def test_lokaler_repo_pfad_wie_die_schlange_loescht_ohne_sicherung(zw: Welt) -> None:
    """Schlange ruft mit dem Klon-Pfad (origin → Bare-Remote): Zweig weg, keine sicherung/*."""
    zw.worktree(54)
    zw.ticket(54, "OPEN")
    tip = _tip(zw, "ticket/54-test")
    r = hausmeister.zweig_aufraeumen(zw.haupt, "ticket/54-test", loeschen=True, uebernommen_tip=tip)
    assert r.art == "gelöscht" and not r.fehler
    assert "ticket/54-test" not in _heads(zw)
    assert not [h for h in _heads(zw) if h.startswith("sicherung/")]


def test_cli_mit_lokalem_repo_pfad_loescht_ohne_sicherung(zw: Welt) -> None:
    zw.worktree(55)
    zw.ticket(55, "OPEN")
    tip = _tip(zw, "ticket/55-test")
    p = subprocess.run(
        [
            sys.executable, str(SKILL / "to_spawn.py"), "aufraeumen",
            "--zweig", "ticket/55-test", "--repo", str(zw.haupt),
            "--uebernommen-tip", tip, "--loeschen",
        ],
        capture_output=True, text=True, encoding="utf-8",
        env=os.environ.copy(), cwd=str(SKILL),
    )  # fmt: skip
    assert p.returncode == 0, p.stderr
    assert "ticket/55-test" not in _heads(zw)
    assert not [h for h in _heads(zw) if h.startswith("sicherung/")]


def test_probelauf_gruppe_je_zweig_exakt_und_wip_gezaehlt(zw: Welt, tmp_path: Path) -> None:
    wt = zw.worktree(56)
    git(wt, "push", "-q", "origin", "HEAD:master")  # übernommen → löschen
    zw.ticket(56, "CLOSED", vor_stunden=48)
    wt57 = zw.worktree(57)
    _commit(wt57, "eigen57.txt", "nur hier\n")
    git(wt57, "push", "-q", "origin", "ticket/57-test")
    zw.ticket(57, "CLOSED", vor_stunden=48)  # Commit nur auf Zweig → sichern+löschen
    zw.worktree(58)
    zw.ticket(58, "OPEN")  # bleibt
    _commit(zw.haupt, "o.txt", "o\n")
    git(zw.haupt, "push", "-q", "origin", "HEAD:refs/heads/ticket/ohne-nummer")
    git(zw.haupt, "push", "-q", "origin", "HEAD:refs/heads/wip/1439-x")
    pfad = hausmeister.zweige_probelauf(tmp_path / "probe.txt")
    zeilen = Path(pfad).read_text(encoding="utf-8").splitlines()

    def gruppe(z: str) -> str:
        z_ = [x for x in zeilen if f" {z}: " in x]
        assert len(z_) >= 1, z
        return z_[0].split(f" {z}: ", 1)[1]

    assert gruppe("ticket/56-test").startswith("löschen (")
    assert gruppe("ticket/57-test").startswith("sichern+löschen (")
    assert gruppe("ticket/58-test").startswith("bleibt: ")
    assert gruppe("ticket/ohne-nummer") == "ohne Ticket-Nummer"
    assert not any(" wip/1439-x: " in x for x in zeilen)  # nie als Zweig bewertet
    assert any("wip/*: 1 Zweig(e)" in x and "[remote]" in x for x in zeilen)
    assert "wip/1439-x" in _heads(zw)
