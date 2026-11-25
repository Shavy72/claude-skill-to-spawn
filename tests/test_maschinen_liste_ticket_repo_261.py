"""#261: Maschinen-Liste ``ticket_repo`` ausdrücklich setzen, automatisch nachziehen, Aufpasser-Log.

Alles mit echter ``maschine.json`` in ``tmp_path`` (über ``TO_SPAWN_MASCHINE``) und
echtem Git-Repo. Kein Eingriff in echte Nutzerdateien oder Installer-Ausführung.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import aufpasser, einrichten, hausmeister, vorgaben

SKILL = Path(__file__).resolve().parent.parent


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _cli(cwd: Path, *args: str, maschine: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "TO_SPAWN_MASCHINE": str(maschine),
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _bau_server_altliste(maschine: Path, app_pfad: Path, skill_pfad: Path, eigen_pfad: Path) -> None:
    """Genau der veraltete Stand vom Bau-Server (vor #235) + ein eigenes Repo."""
    app_slug = vorgaben.REPOS[0]["slug"]
    skill_slug = vorgaben.REPOS[1]["slug"]
    daten = {
        "repos": [
            {
                "slug": app_slug,
                "pfad": app_pfad.as_posix(),
                "ticket_repo": "",
                "worktree_basis": "",
            },
            {
                "slug": skill_slug,
                "pfad": skill_pfad.as_posix(),
                "ticket_repo": app_slug,
                "worktree_basis": "",
            },
            {
                "slug": "beispiel/eigenes-repo",
                "pfad": eigen_pfad.as_posix(),
                "ticket_repo": "beispiel/eigene-tickets",
                "worktree_basis": "",
            },
        ]
    }
    maschine.write_text(json.dumps(daten, indent=2) + "\n", encoding="utf-8")


def _repos_je_slug(maschine: Path) -> dict[str, dict[str, str]]:
    daten = json.loads(maschine.read_text(encoding="utf-8"))
    return {e["slug"]: e for e in daten["repos"]}


# --- (1) Befehl setzt ticket_repo ausdrücklich, zeigt vorher/nachher, wiederholbar ---


def test_cli_ticket_repo_setzt_vorher_nachher_und_wiederholbar(tmp_path: Path) -> None:
    maschine = tmp_path / "maschine.json"
    app_pfad = tmp_path / "app"
    skill_pfad = tmp_path / "skill"
    eigen_pfad = tmp_path / "eigen"
    _bau_server_altliste(maschine, app_pfad, skill_pfad, eigen_pfad)

    # Läuft auch außerhalb jedes Git-Repos (tmp_path ist kein Repo).
    erst = _cli(
        tmp_path,
        "ticket-repo",
        "beispiel/eigenes-repo",
        "beispiel/neue-tickets",
        maschine=maschine,
    )
    assert erst.returncode == 0, erst.stderr
    assert "beispiel/eigene-tickets" in erst.stdout
    assert "beispiel/neue-tickets" in erst.stdout
    assert _repos_je_slug(maschine)["beispiel/eigenes-repo"]["ticket_repo"] == "beispiel/neue-tickets"

    # Zweiter Lauf mit demselben Wert: wiederholbar, schreibt nicht neu.
    mtime = maschine.stat().st_mtime_ns
    zweit = _cli(
        tmp_path,
        "ticket-repo",
        eigen_pfad.as_posix(),  # auch per Pfad adressierbar
        "beispiel/neue-tickets",
        maschine=maschine,
    )
    assert zweit.returncode == 0, zweit.stderr
    assert "unverändert" in zweit.stdout and "beispiel/neue-tickets" in zweit.stdout
    assert maschine.stat().st_mtime_ns == mtime


def test_cli_ticket_repo_lehnt_ungueltigen_wert_und_unbekannten_eintrag_ab(
    tmp_path: Path,
) -> None:
    maschine = tmp_path / "maschine.json"
    _bau_server_altliste(maschine, tmp_path / "app", tmp_path / "skill", tmp_path / "eigen")
    vorher = maschine.read_text(encoding="utf-8")

    ungueltig = _cli(tmp_path, "ticket-repo", "beispiel/eigenes-repo", "kein-slash", maschine=maschine)
    assert ungueltig.returncode != 0
    assert "ungültig" in ungueltig.stderr
    assert maschine.read_text(encoding="utf-8") == vorher

    fehlt = _cli(tmp_path, "ticket-repo", "unbekannt/repo", "a/b", maschine=maschine)
    assert fehlt.returncode != 0
    assert "Kein Eintrag" in fehlt.stderr
    assert maschine.read_text(encoding="utf-8") == vorher

    # Liste anzeigen (ohne Argumente) liest nur und nennt alle Einträge.
    zeigen = _cli(tmp_path, "ticket-repo", maschine=maschine)
    assert zeigen.returncode == 0, zeigen.stderr
    assert "beispiel/eigenes-repo" in zeigen.stdout and "beispiel/eigene-tickets" in zeigen.stdout

    # Kaputte JSON-Datei wird weder bei --nachziehen noch beim Setzen überschrieben.
    kaputt = tmp_path / "kaputt.json"
    kaputt.write_text("{kaputt", encoding="utf-8")
    nach = _cli(tmp_path, "ticket-repo", "--nachziehen", maschine=kaputt)
    assert nach.returncode != 0 and "unlesbar" in nach.stderr
    assert kaputt.read_text(encoding="utf-8") == "{kaputt"


# --- (2) Einrichter + Install-Weg erkennen und ziehen veraltete Werte nach -----------


def test_cli_nachziehen_behebt_veraltete_liste_wiederholbar(tmp_path: Path) -> None:
    maschine = tmp_path / "maschine.json"
    app_slug = vorgaben.REPOS[0]["slug"]
    skill_slug = vorgaben.REPOS[1]["slug"]
    _bau_server_altliste(maschine, tmp_path / "app", tmp_path / "skill", tmp_path / "eigen")

    erst = _cli(tmp_path, "ticket-repo", "--nachziehen", maschine=maschine)
    assert erst.returncode == 0, erst.stderr
    assert app_slug in erst.stdout and skill_slug in erst.stdout

    danach = _repos_je_slug(maschine)
    assert danach[app_slug]["ticket_repo"] == app_slug
    assert danach[skill_slug]["ticket_repo"] == skill_slug
    # Bewusst gesetzter fremder Wert bleibt unberührt (#542).
    assert danach["beispiel/eigenes-repo"]["ticket_repo"] == "beispiel/eigene-tickets"

    # Wiederholbar: zweiter Lauf ändert die Datei nicht.
    mtime = maschine.stat().st_mtime_ns
    zweit = _cli(tmp_path, "ticket-repo", "--nachziehen", maschine=maschine)
    assert zweit.returncode == 0, zweit.stderr
    assert maschine.stat().st_mtime_ns == mtime


def test_cli_nachziehen_ohne_datei_legt_nichts_an(tmp_path: Path) -> None:
    maschine = tmp_path / "nicht-da" / "maschine.json"
    lauf = _cli(tmp_path, "ticket-repo", "--nachziehen", maschine=maschine)
    assert lauf.returncode == 0, lauf.stderr
    assert not maschine.exists()


def test_einrichter_erkennt_und_zieht_veraltetes_ticket_repo_nach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_slug = vorgaben.REPOS[0]["slug"]
    skill_slug = vorgaben.REPOS[1]["slug"]
    repo = tmp_path / "skill"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", f"https://github.com/{skill_slug}.git")
    (repo / "README.md").write_text("skill\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "start")

    stub = tmp_path / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))

    maschine = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    _bau_server_altliste(maschine, tmp_path / "app", repo, tmp_path / "eigen")
    vorher = maschine.read_text(encoding="utf-8")

    # --pruefen meldet Abweichung und schreibt nichts.
    aus_pruef = io.StringIO()
    code_pruef = einrichten.einrichten(repo, pruefen=True, ausgabe=aus_pruef)
    assert code_pruef == einrichten.EXIT_ABWEICHUNG, aus_pruef.getvalue()
    assert "ticket_repo" in aus_pruef.getvalue()
    assert maschine.read_text(encoding="utf-8") == vorher

    # Normaler Einrichter-Lauf zieht beide veralteten Einträge nach und zeigt vorher/nachher.
    aus_lauf = io.StringIO()
    code_lauf = einrichten.einrichten(repo, ausgabe=aus_lauf)
    assert code_lauf == 0, aus_lauf.getvalue()
    assert skill_slug in aus_lauf.getvalue() and app_slug in aus_lauf.getvalue()

    danach = _repos_je_slug(maschine)
    assert danach[app_slug]["ticket_repo"] == app_slug
    assert danach[skill_slug]["ticket_repo"] == skill_slug
    assert danach["beispiel/eigenes-repo"]["ticket_repo"] == "beispiel/eigene-tickets"

    # Danach meldet --pruefen für maschinen_liste da_aktuell.
    aus_nach = io.StringIO()
    einrichten.einrichten(repo, pruefen=True, ausgabe=aus_nach)
    assert "maschinen_liste: da_aktuell" in aus_nach.getvalue(), aus_nach.getvalue()


def test_installer_rufen_ticket_repo_nachziehen_auf() -> None:
    sh = (SKILL / "install.sh").read_text(encoding="utf-8")
    ps1 = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert "ticket-repo --nachziehen" in sh
    assert "ticket-repo --nachziehen" in ps1


# --- (3) Aufpasser-Log nennt je Takt die Gründe fürs Bleiben gezählt -----------------


def test_aufpasser_log_nennt_bleib_gruende_gezaehlt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ergebnisse = [
        hausmeister.Ergebnis(tmp_path / "wt-1", "gelöscht", "Ticket #1 zu", 1),
        hausmeister.Ergebnis(tmp_path / "wt-12", "bleibt", "Ticket #12 noch offen", 12),
        hausmeister.Ergebnis(tmp_path / "wt-34", "bleibt", "Ticket #34 noch offen", 34),
        hausmeister.Ergebnis(tmp_path / "wt-notiz", "bleibt", "keine Ticket-Nummer", None),
        hausmeister.Ergebnis(
            tmp_path / "wt-99",
            "bleibt",
            "Ticket-Repo nicht bestimmbar (Maschinen-Liste)",
            99,
            fehler=True,
        ),
    ]
    monkeypatch.setattr(hausmeister, "aufraeumen_alles", lambda **_k: ergebnisse)

    zustand = tmp_path / "aufpasser"
    handler = aufpasser._logging_einrichten(zustand, trocken=False)
    try:
        a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=zustand))
        a._hausmeister_lauf()
    finally:
        aufpasser.log.removeHandler(handler)
        handler.close()

    inhalt = (zustand / "aufpasser.log").read_text(encoding="utf-8")
    assert "Hausmeister: 1 gelöscht, 3 bleibt, 1 Fehler" in inhalt, inhalt
    assert "Hausmeister: bleibt 2× Ticket #N noch offen" in inhalt, inhalt
    assert "Hausmeister: bleibt 1× keine Ticket-Nummer" in inhalt, inhalt
    assert "Hausmeister: bleibt 1× Ticket-Repo nicht bestimmbar" in inhalt, inhalt
    assert "wt-99" in inhalt, inhalt
