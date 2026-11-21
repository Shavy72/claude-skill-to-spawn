"""#1374 (E17): Neue Arbeitsordner holen die Belegbilder aus ``docs/verify-hard`` nicht auf die Platte.

Weg-Test ohne Attrappen: echtes Git, echte CLI ``to_spawn.py nest sandbox`` (derselbe Weg,
den ``bau.py`` vor jedem Session-Start geht). Geprüft wird, was eine Bau-Session danach
braucht: alte Belegbilder fehlen auf der Platte, Text-Belege (lesen Tests) bleiben,
``git status`` bleibt still, ein neuer Beleg
lässt sich normal ablegen und committen, und der Commit löscht keinen alten Beleg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_nest_210 import _cli, _git, _neues_repo, _umgebung


def _repo_mit_belegen(tmp_path: Path) -> Path:
    quelle = _neues_repo(tmp_path / "quelle")
    (quelle / "docs" / "verify-hard" / "alt").mkdir(parents=True)
    (quelle / "docs" / "verify-hard" / "alt" / "beleg.md").write_text(
        "alt\n", encoding="utf-8"
    )
    (quelle / "docs" / "verify-hard" / "alt" / "bild.webp").write_bytes(b"\x00" * 64)
    (quelle / "docs" / "verify-hard" / "alt" / "prüfung größe.PNG").write_bytes(
        b"\x89" * 64
    )
    for endung in ("jpg", "jpeg", "gif"):
        (quelle / "docs" / "verify-hard" / "alt" / f"weitere.{endung}").write_bytes(
            b"\x02" * 8
        )
    (quelle / "docs" / "verify-hard" / "alt" / "assets").mkdir()
    (quelle / "docs" / "verify-hard" / "alt" / "assets" / "roh.jpg").write_bytes(
        b"\x03" * 8
    )
    (quelle / "docs" / "verify-hard" / "alt" / "probe.mp4").write_bytes(b"\x01" * 64)
    (quelle / "docs" / "anleitung.md").write_text("bleibt\n", encoding="utf-8")
    _git(quelle, "add", "-A")
    _git(quelle, "commit", "-m", "Belege")
    haupt = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(quelle), str(haupt)], check=True)
    _git(haupt, "config", "user.email", "test@example.invalid")
    _git(haupt, "config", "user.name", "Test")
    return haupt


def _arbeitsordner(tmp_path: Path, haupt: Path) -> Path:
    heim = tmp_path / "heim"
    heim.mkdir()
    ergebnis = _cli(
        "sandbox",
        "77",
        "--repo",
        str(haupt),
        cwd=haupt,
        env=_umgebung(heim, BAU_WT_DIR=str(tmp_path / "wt")),
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    return tmp_path / "wt" / "wt-77"


def test_neuer_arbeitsordner_ohne_alte_belegbilder(tmp_path: Path) -> None:
    wt = _arbeitsordner(tmp_path, _repo_mit_belegen(tmp_path))
    assert (wt / "README.md").is_file()
    assert (wt / "docs" / "anleitung.md").is_file(), "übriges docs/ bleibt"
    alt = wt / "docs" / "verify-hard" / "alt"
    assert (alt / "beleg.md").is_file(), "Text-Belege bleiben (Tests lesen sie)"
    assert (alt / "probe.mp4").is_file(), "Videos bleiben (Tests nutzen sie)"
    assert (alt / "assets" / "roh.jpg").is_file(), (
        "assets/-Bilder bleiben (Tests lesen sie)"
    )
    assert not (alt / "bild.webp").exists()
    assert not list(alt.glob("weitere.*")), "jpg/jpeg/gif ebenfalls ausgeblendet"
    assert not (alt / "prüfung größe.PNG").exists(), "Umlaut-Namen, Großschreibung"
    assert _git(wt, "status", "--short", "--", ".", ":!.to-spawn") == "", (
        "fehlende Belege dürfen nicht als gelöscht gelten"
    )


def test_neuer_beleg_wird_committed_ohne_alte_zu_loeschen(tmp_path: Path) -> None:
    wt = _arbeitsordner(tmp_path, _repo_mit_belegen(tmp_path))
    neu = wt / "docs" / "verify-hard" / "77" / "beleg.md"
    neu.parent.mkdir(parents=True)
    neu.write_text("neuer Beleg\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "Beleg (#77)")
    stat = _git(wt, "show", "--stat", "--format=", "HEAD")
    assert "docs/verify-hard/77/beleg.md" in stat
    assert "alt/" not in stat, stat
    alte = _git(
        wt,
        "-c",
        "core.quotePath=false",
        "ls-tree",
        "-r",
        "--name-only",
        "HEAD",
        "docs/verify-hard/alt",
    )
    assert alte.splitlines() == [
        "docs/verify-hard/alt/assets/roh.jpg",
        "docs/verify-hard/alt/beleg.md",
        "docs/verify-hard/alt/bild.webp",
        "docs/verify-hard/alt/probe.mp4",
        "docs/verify-hard/alt/prüfung größe.PNG",
        "docs/verify-hard/alt/weitere.gif",
        "docs/verify-hard/alt/weitere.jpeg",
        "docs/verify-hard/alt/weitere.jpg",
    ]


def test_repo_ohne_belegordner_unveraendert(tmp_path: Path) -> None:
    quelle = _neues_repo(tmp_path / "quelle")
    haupt = tmp_path / "klon"
    subprocess.run(["git", "clone", "-q", str(quelle), str(haupt)], check=True)
    wt = _arbeitsordner(tmp_path, haupt)
    assert (wt / "README.md").is_file()
    assert _git(wt, "status", "--short", "--", ".", ":!.to-spawn") == ""


def test_fehler_beim_fuellen_raeumt_halben_ordner_weg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prüfpanel #1374: scheitert das Füllen, darf kein leerer Ordner stehen bleiben.

    Sonst kehrt der nächste Aufruf bei ``worktree.exists()`` früh zurück und die
    Session arbeitet in einem leeren Ordner, in dem alles als gelöscht gilt.
    """

    from to_spawn import nest

    haupt = _repo_mit_belegen(tmp_path)
    wt = tmp_path / "wt" / "wt-78"

    def _scheitert(*_: object) -> None:
        raise nest.NestFehler("Füllen scheiterte (Probe)")

    monkeypatch.setattr(nest, "_auschecken_ohne_beleg_bilder", _scheitert)
    with pytest.raises(nest.NestFehler):
        nest.worktree_anlegen(wt, haupt, "78")
    assert not wt.exists(), "halb angelegter Ordner muss weg"
    assert "wt-78" not in _git(haupt, "worktree", "list")
    assert _git(haupt, "branch", "--list", "ticket-78") == "", "neuer Zweig muss weg"


def test_vorhandener_ticket_zweig_ohne_alte_belege(tmp_path: Path) -> None:
    """Wiederaufnahme: der Ticket-Zweig existiert schon."""
    haupt = _repo_mit_belegen(tmp_path)
    _git(haupt, "branch", "ticket-77", "origin/master")
    wt = _arbeitsordner(tmp_path, haupt)
    assert _git(wt, "branch", "--show-current") == "ticket-77"
    assert not (wt / "docs" / "verify-hard" / "alt" / "bild.webp").exists()
    assert _git(wt, "status", "--short", "--", ".", ":!.to-spawn") == ""


def test_fehler_beim_fuellen_laesst_vorhandenen_zweig_stehen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gegenprobe zum Aufräumen: ein schon vorhandener Ticket-Zweig trägt Arbeit, nie löschen."""
    from to_spawn import nest

    haupt = _repo_mit_belegen(tmp_path)
    _git(haupt, "branch", "ticket-79", "origin/master")
    wt = tmp_path / "wt" / "wt-79"

    def _scheitert(*_: object) -> None:
        raise nest.NestFehler("Füllen scheiterte (Probe)")

    monkeypatch.setattr(nest, "_auschecken_ohne_beleg_bilder", _scheitert)
    with pytest.raises(nest.NestFehler):
        nest.worktree_anlegen(wt, haupt, "79")
    assert not wt.exists()
    assert _git(haupt, "branch", "--list", "ticket-79") != "", (
        "vorhandener Zweig bleibt"
    )


def test_halber_ordner_geht_ueber_den_hausmeister(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aufseher 20.11.: Lösch-Wissen nur im Hausmeister; seine Log-Zeile belegt den Weg."""
    import json

    from to_spawn import nest

    log_datei = tmp_path / "hausmeister.jsonl"
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log_datei))
    haupt = _repo_mit_belegen(tmp_path)
    wt = tmp_path / "wt" / "wt-81"

    def _scheitert(*_: object) -> None:
        raise nest.NestFehler("Füllen scheiterte (Probe)")

    monkeypatch.setattr(nest, "_auschecken_ohne_beleg_bilder", _scheitert)
    with pytest.raises(nest.NestFehler):
        nest.worktree_anlegen(wt, haupt, "81")
    assert not wt.exists()
    zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines()]
    assert [(z["art"], z["ticket"], z["trocken"]) for z in zeilen] == [
        ("gelöscht", 81, False)
    ]
    assert "halb angelegt" in zeilen[0]["grund"]
    assert _git(haupt, "branch", "--list", "ticket-81") == "", "neuer Zweig muss weg"


def test_halber_ordner_echter_git_fehler_bleibt_mit_fehlerzeile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echtes Git, keine Attrappe: Ordner ist kein Worktree dieses Hauptrepos.

    Der Hausmeister wirft nicht, fasst ihn nicht an (Schutz vor fremder Arbeit), meldet
    ``False`` und schreibt „bleibt“ mit ``fehler``; Ordner und Zweig bleiben stehen.
    """
    import json

    from to_spawn import hausmeister

    log_datei = tmp_path / "hausmeister.jsonl"
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log_datei))
    haupt = _repo_mit_belegen(tmp_path)
    _git(haupt, "branch", "ticket-82", "origin/master")
    fremd = tmp_path / "wt" / "wt-82"
    fremd.mkdir(parents=True)
    (fremd / "arbeit.txt").write_text("nicht anfassen", encoding="utf-8")

    assert hausmeister.halb_angelegt_entfernen(haupt, fremd, None) is False
    assert (fremd / "arbeit.txt").is_file()
    assert _git(haupt, "branch", "--list", "ticket-82") != ""
    zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines()]
    assert [(z["art"], z["fehler"]) for z in zeilen] == [("bleibt", True)], (
        "kein „gelöscht“ für einen Ordner, der stehen blieb"
    )


def test_oserror_beim_fuellen_raeumt_halben_ordner_weg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prüfpanel: auch ein Fehler außer ``NestFehler`` (z. B. OSError) räumt auf."""
    from to_spawn import nest

    haupt = _repo_mit_belegen(tmp_path)
    wt = tmp_path / "wt" / "wt-83"

    def _scheitert(*_: object) -> None:
        raise OSError("Platte voll (Probe)")

    monkeypatch.setattr(nest, "_auschecken_ohne_beleg_bilder", _scheitert)
    with pytest.raises(OSError):
        nest.worktree_anlegen(wt, haupt, "83")
    assert not wt.exists()
    assert "wt-83" not in _git(haupt, "worktree", "list")
