"""#707: Sicherung und Hausmeister nutzen Klon-Ordner aus Maschinen-Liste bzw. ``STAND.json``.

Fehlt der Standard-Ordner ``~/claude-skill-to-spawn``, greift ``klon`` aus ``STAND.json``
über die eine gemeinsame Stelle (:func:`to_spawn.config.repo_klon_pfad` /
:func:`to_spawn.skill_stand.klon_ordner`). Fehlt der Klon-Ordner ganz, schreibt die
Sicherung eine klare Log-Zeile statt eines Fehlers.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from to_spawn import hausmeister, konsole, skill_stand
from to_spawn import uebergabe_sichern as us

SKILL_SLUG = "Shavy72/claude-skill-to-spawn"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
    ).stdout.strip()


def _skill_klon_mit_zweig(basis: Path, name: str, zweig: str) -> tuple[Path, Path]:
    """Legt bare-``origin`` + Klon + Worktree mit einem ungesicherten Commit auf ``zweig`` an."""
    origin = basis / f"{name}-origin.git"
    _git(basis, "init", "-q", "--bare", "-b", "main", str(origin))
    klon = basis / name
    _git(basis, "clone", "-q", str(origin), str(klon))
    for schluessel, wert in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(klon, "config", schluessel, wert)
    _git(klon, "checkout", "-q", "-b", "main")
    _git(klon, "commit", "-q", "--allow-empty", "-m", "basis")
    _git(klon, "push", "-q", "origin", "main")
    wt = basis / f"{name}-{zweig.replace('/', '-')}"
    _git(klon, "worktree", "add", "-q", "-b", zweig, str(wt), "main")
    (wt / "arbeit.txt").write_text(f"{zweig}\n", encoding="utf-8")
    _git(wt, "add", "arbeit.txt")
    _git(wt, "commit", "-q", "-m", f"wip {zweig}")
    return origin, klon


def _entfernte_zweige(origin: Path) -> list[str]:
    roh = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(roh.split())


def _installierter_skill(tmp_path: Path, klon: Path | None) -> Path:
    """Installierte Skill-Kopie (ohne ``.git``) mit optionalem ``STAND.json``-Eintrag ``klon``."""
    installiert = tmp_path / "installiert-skill"
    installiert.mkdir(parents=True, exist_ok=True)
    if klon is not None:
        daten = {
            "commit": "abcdef123456",
            "zeit": "2026-11-25T10:00:00+00:00",
            "quelle": "https://github.com/Shavy72/claude-skill-to-spawn.git",
            "klon": str(klon),
            "dirty": "nein",
        }
        (installiert / skill_stand.STEMPEL).write_text(json.dumps(daten, indent=2) + "\n", encoding="utf-8")
    return installiert


@pytest.fixture
def ohne_standard_klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolierte Umgebung ohne ``~/claude-skill-to-spawn`` und ohne Env-Übersteuerung der Zweig-Repos."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    maschine = tmp_path / "maschine.json"  # existiert bewusst nicht → Vorgaben gelten
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.delenv(us.UMGEBUNG_ZWEIG_REPOS, raising=False)
    monkeypatch.setenv(us.UMGEBUNG_ZIEL, str(tmp_path / "tresor"))
    assert not (heim / "claude-skill-to-spawn").exists()
    return {"tmp": tmp_path, "heim": heim, "maschine": maschine}


def test_sicherung_nutzt_klon_aus_stand_json_wenn_standard_ordner_fehlt(
    ohne_standard_klon: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rot-Test (#707): Standard-Ordner fehlt, ``STAND.json`` nennt ``klon`` → Sicherung nutzt ``klon``."""
    tmp = ohne_standard_klon["tmp"]
    origin, klon = _skill_klon_mit_zweig(tmp, "echter-skill-klon", "l5/stand-707")
    installiert = _installierter_skill(tmp, klon)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)

    erg = us.sichern()

    assert erg.fehler == [], erg.fehler
    assert erg.hochgeladen == ["echter-skill-klon:l5/stand-707"]
    assert _entfernte_zweige(origin) == ["l5/stand-707", "main"]


def test_sicherung_bevorzugt_vorhandenen_ordner_aus_maschinen_liste_vor_stand_json(
    ohne_standard_klon: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maschinen-Liste gewinnt vor ``STAND.json``, wenn der eingetragene Ordner existiert."""
    tmp = ohne_standard_klon["tmp"]
    origin_liste, klon_liste = _skill_klon_mit_zweig(tmp, "liste-klon", "l5/aus-liste")
    origin_stand, klon_stand = _skill_klon_mit_zweig(tmp, "stand-klon", "l5/aus-stand")
    ohne_standard_klon["maschine"].write_text(
        json.dumps({"repos": [{"slug": SKILL_SLUG, "pfad": str(klon_liste)}]}),
        encoding="utf-8",
    )
    installiert = _installierter_skill(tmp, klon_stand)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)

    erg = us.sichern()

    assert erg.fehler == [], erg.fehler
    assert erg.hochgeladen == ["liste-klon:l5/aus-liste"]
    assert _entfernte_zweige(origin_liste) == ["l5/aus-liste", "main"]
    assert _entfernte_zweige(origin_stand) == ["main"]


def test_sicherung_nutzt_stand_json_auch_ohne_skill_eintrag_in_maschinen_liste(
    ohne_standard_klon: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt der Skill-Eintrag in ``maschine.json``, greift ``STAND.json`` (``klon``) für den Zweig-Upload."""
    tmp = ohne_standard_klon["tmp"]
    app = tmp / "andere-app"
    app.mkdir()
    ohne_standard_klon["maschine"].write_text(
        json.dumps({"repos": [{"slug": "test/app", "pfad": str(app)}]}),
        encoding="utf-8",
    )
    origin, klon = _skill_klon_mit_zweig(tmp, "skill-aus-stempel", "l5/ohne-eintrag")
    installiert = _installierter_skill(tmp, klon)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)

    erg = us.sichern()

    assert erg.fehler == [], erg.fehler
    assert erg.hochgeladen == ["skill-aus-stempel:l5/ohne-eintrag"]
    assert _entfernte_zweige(origin) == ["l5/ohne-eintrag", "main"]


def test_sicherung_ohne_klon_ordner_loggt_klar_statt_fehler(
    ohne_standard_klon: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fehlen Standard-Ordner und ``STAND.json``-Klon: klare Log-Zeile, kein Fehler, Exit 0."""
    tmp = ohne_standard_klon["tmp"]
    tot = tmp / "geloeschter-klon"
    installiert = _installierter_skill(tmp, tot)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)

    with caplog.at_level(logging.INFO, logger="to_spawn.uebergabe_sichern"):
        erg = us.sichern()

    assert erg.fehler == [], erg.fehler
    assert erg.hochgeladen == []
    assert "claude-skill-to-spawn" in caplog.text
    assert "fehlt" in caplog.text
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert us.lauf([], trocken=False) == 0


def test_hausmeister_rundgang_und_hauptrepos_nutzen_stand_json_klon(
    ohne_standard_klon: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hausmeister-Rundgang und ``_hauptrepos`` nutzen denselben ``STAND.json``-Klon."""
    tmp = ohne_standard_klon["tmp"]
    origin, klon = _skill_klon_mit_zweig(tmp, "hausmeister-skill", "l5/rundgang-707")
    installiert = _installierter_skill(tmp, klon)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", raising=False)

    assert klon in hausmeister._hauptrepos()

    rund = hausmeister.rundgang(loeschen=True)
    assert rund.gelaufen is True
    assert _entfernte_zweige(origin) == ["l5/rundgang-707", "main"]


def test_sicherung_kopiert_uebergaben_aus_stand_json_klon(
    ohne_standard_klon: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch die Übergabe-Kopie (``_quellen``) findet Dateien im ``STAND.json``-Klon."""
    tmp = ohne_standard_klon["tmp"]
    _origin, klon = _skill_klon_mit_zweig(tmp, "skill-handoff", "l5/handoff-707")
    (klon / ".claude-handoff-707.md").write_text("Übergabe 707\n", encoding="utf-8")
    installiert = _installierter_skill(tmp, klon)
    monkeypatch.setattr(skill_stand, "SKILL", installiert)

    erg = us.sichern()

    assert erg.fehler == [], erg.fehler
    assert erg.ziel is not None
    assert (erg.ziel / "skill-handoff__claude-handoff-707.md").is_file()
