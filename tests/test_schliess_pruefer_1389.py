"""Weg-Tests für #864 (aus App #1389) — Schließ-Prüfer-Modul ``to_spawn/schliess_pruefer.py``.

Prüft die schmale Tür ``schliess_pruefer.pruefe(stand)`` gegen echte Git-Repos (kein Git-Mock):
- Ticketnummer im Commit (``commit_ohne_nummer``)
- Belegseite auf dem Hauptzweig vorhanden (``beweis_fehlt``) und in fester Form (``beleg_form``)
- Kein Test ersetzt (``test_ersetzt``)
- Commit grün auf Hauptzweig (``nicht_gruen_auf_hauptzweig``)
- Bewusst KEINE Regel „Server nicht aktuell“ (``vps_ungleich_origin``)
- ``to_spawn/capo.py`` (``reopen_funde``) ruft ausschließlich diese Tür (keine Regel-Kopie)
- Weg-Test mit echtem Git-Repo über ``tests/hilfen/git_bash.py``
"""

from __future__ import annotations

import inspect
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from hilfen.git_bash import git_bash

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

TICKET = 1389


# --- Hilfen (echtes Git, echte PNG-Bilder) ------------------------------------


def _git(repo: Path, *args: str) -> str:
    umgebung = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test-Bauer",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test-Bauer",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=umgebung,
        timeout=60,
    )
    return fertig.stdout.strip()


def _png_bytes(breite: int, hoehe: int) -> bytes:
    """Kleinstes gültiges PNG mit echter IHDR-Breite und -Höhe (ohne Fremdbibliothek)."""

    def _chunk(typ: bytes, daten: bytes) -> bytes:
        crc = zlib.crc32(typ + daten) & 0xFFFFFFFF
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    roh = b"".join(b"\x00" + b"\x00\x00\x00" * breite for _ in range(hoehe))
    idat = zlib.compress(roh)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _welt(tmp_path: Path) -> tuple[Path, Path]:
    """Echtes Git-Repo mit Bare-Origin und Ausgangs-Commit auf ``origin/master``."""
    fern = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(fern)],
        check=True,
        capture_output=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "remote", "add", "origin", str(fern))
    (repo / "README.md").write_text("# Test-Repo\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_basis.py").write_text("def test_basis():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: Anfang")
    _git(repo, "push", "-q", "origin", "HEAD:master")
    return repo, fern


def _commit(
    repo: Path,
    betreff: str,
    dateien: dict[str, str | bytes | None],
    *,
    push: bool = True,
    zweig: str = "master",
) -> str:
    for rel, inhalt in dateien.items():
        ziel = repo / rel
        if inhalt is None:
            _git(repo, "rm", "-q", rel)
            continue
        ziel.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(inhalt, bytes):
            ziel.write_bytes(inhalt)
        else:
            ziel.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "--allow-empty", "-m", betreff)
    if push:
        _git(repo, "push", "-q", "origin", f"HEAD:{zweig}")
    return _git(repo, "rev-parse", "HEAD")


def _gueltige_belegseite(
    ticket: int = TICKET,
    *,
    commit_stand: str = "abcdef1234567890",
    staging_adresse: str = "https://staging.example.test/start",
    rolle: str = "Admin",
    datum: str = "2026-11-27 10:15",
    desktop_bild: str = "desktop.png",
    handy_bild: str = "handy.png",
    klickweg_text: str | None = None,
) -> str:
    klick = (
        klickweg_text
        if klickweg_text is not None
        else (
            "| Schritt | Ergebnis | Bild |\n"
            "| --- | --- | --- |\n"
            f"| Startseite Desktop | sichtbar, 0 offen | ![Desktop]({desktop_bild}) |\n"
            f"| Startseite Handy 390x844 | lesbar, keine Punkte offen | ![Handy]({handy_bild}) |\n"
        )
    )
    return (
        f"# Beleg Ticket #{ticket}\n\n"
        f"- **Ticket:** #{ticket}\n"
        f"- **Commit-Stand:** `{commit_stand}`\n"
        f"- **Staging-Adresse:** {staging_adresse}\n"
        f"- **Rolle:** {rolle}\n"
        f"- **Datum:** {datum}\n\n"
        "## Staging-Klickweg\n\n"
        f"{klick}\n"
    )


def _sauberer_stand(repo: Path, ticket: int = TICKET, **beleg_kw: object) -> str:
    """Legt einen gültigen Ticket-Commit mit Belegseite + Desktop-/Handy-PNG auf ``origin/master`` an."""
    ordner = f"docs/verify-hard/{ticket}"
    inhalt = _gueltige_belegseite(ticket, **beleg_kw)  # type: ignore[arg-type]
    return _commit(
        repo,
        f"feat: Bauteil umgesetzt (#{ticket})",
        {
            "app/modul.py": "def ok() -> bool:\n    return True\n",
            f"{ordner}/{ticket}_beleg.md": inhalt,
            f"{ordner}/desktop.png": _png_bytes(1280, 800),
            f"{ordner}/handy.png": _png_bytes(390, 844),
        },
    )


# --- 1. Grüner Standardfall über die schmale Tür pruefe(stand) ----------------


def test_sauberer_stand_besteht_alle_regeln(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _sauberer_stand(repo)
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(
            repo=repo,
            ticket=TICKET,
            ref="origin/master",
            gruen_auf_hauptzweig=sha,
        )
    )
    assert ergebnis.ok is True, ergebnis.gruende
    assert bool(ergebnis) is True
    assert ergebnis.gruende == []


# --- 2. Regel: Ticketnummer im Commit -----------------------------------------


def test_commit_ohne_nummer_wird_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    ordner = f"docs/verify-hard/{TICKET}"
    sha = _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "app/modul.py": "x = 1\n",
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(),
            f"{ordner}/desktop.png": _png_bytes(1280, 800),
            f"{ordner}/handy.png": _png_bytes(390, 844),
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    assert [g.regel for g in ergebnis.gruende] == ["commit_ohne_nummer"]


# --- 3. Regel: Belegseite auf Hauptzweig und in fester Form -------------------


def test_belegseite_fehlt_auf_hauptzweig_wird_abgelehnt(tmp_path: Path) -> None:
    """Belegseite liegt nur uncommittet im Arbeitsbaum oder auf Ticket-Zweig, nicht auf origin/master."""
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _commit(repo, f"feat: Bauteil ohne Beleg (#{TICKET})", {"app/modul.py": "x = 1\n"})
    # Uncommittet im Arbeitsbaum darf beim Schließen nicht zählen.
    lokal = repo / "docs" / "verify-hard" / str(TICKET)
    lokal.mkdir(parents=True)
    (lokal / f"{TICKET}_beleg.md").write_text(_gueltige_belegseite(), encoding="utf-8")
    (lokal / "desktop.png").write_bytes(_png_bytes(1280, 800))
    (lokal / "handy.png").write_bytes(_png_bytes(390, 844))

    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    assert any(g.regel == "beweis_fehlt" for g in ergebnis.gruende), ergebnis.gruende


def test_belegseite_nur_auf_ticketzweig_nicht_auf_master_abgelehnt(tmp_path: Path) -> None:
    """Liegt die Belegseite nur auf dem Ticket-Zweig und nicht auf haupt_ref (origin/master) → Ablehnung."""
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    master_sha = _commit(repo, f"feat: Code auf master (#{TICKET})", {"app/modul.py": "x = 1\n"})
    _git(repo, "checkout", "-q", "-b", f"ticket/{TICKET}-beleg")
    ordner = f"docs/verify-hard/{TICKET}"
    _commit(
        repo,
        f"docs: Beleg nur auf Zweig (#{TICKET})",
        {
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(),
            f"{ordner}/desktop.png": _png_bytes(1280, 800),
            f"{ordner}/handy.png": _png_bytes(390, 844),
        },
        push=False,
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(
            repo=repo,
            ticket=TICKET,
            ref="HEAD",
            haupt_ref="origin/master",
            gruen_auf_hauptzweig=master_sha,
        )
    )
    assert ergebnis.ok is False
    assert any(g.regel == "beweis_fehlt" for g in ergebnis.gruende), ergebnis.gruende


def test_leere_oder_folgt_belegseite_wird_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _commit(
        repo,
        f"feat: Bauteil (#{TICKET})",
        {
            "app/modul.py": "x = 1\n",
            f"docs/verify-hard/{TICKET}_beleg.md": "folgt\n",
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    form_gruende = [g for g in ergebnis.gruende if g.regel == "beleg_form"]
    assert len(form_gruende) == 1, ergebnis.gruende
    meldung = form_gruende[0].text + " " + " ".join(form_gruende[0].details)
    for pflicht in ("Ticket", "Commit-Stand", "Staging-Adresse", "Rolle", "Datum", "Staging-Klickweg"):
        assert pflicht in meldung, f"{pflicht} fehlt in: {meldung}"


@pytest.mark.parametrize(
    ("feld_kw", "erwartet"),
    [
        ({"staging_adresse": "http://localhost:8000/start"}, "Staging-Adresse"),
        ({"staging_adresse": "http://127.0.0.1:5000"}, "Staging-Adresse"),
        ({"commit_stand": "folgt"}, "Commit-Stand"),
        ({"rolle": "TODO"}, "Rolle"),
        ({"datum": "gestern"}, "Datum"),
        ({"klickweg_text": "Klickweg folgt nach Staging-Deploy.\n"}, "Platzhalter"),
        ({"klickweg_text": "- [ ] Desktop prüfen\n![Desktop](desktop.png)\n![Handy](handy.png)\n"}, "Platzhalter"),
    ],
)
def test_beleg_form_fehlerhafte_felder_und_platzhalter_abgelehnt(
    tmp_path: Path, feld_kw: dict[str, str], erwartet: str
) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _sauberer_stand(repo, **feld_kw)
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    form_gruende = [g for g in ergebnis.gruende if g.regel == "beleg_form"]
    assert form_gruende, ergebnis.gruende
    gesamt = form_gruende[0].text + " " + " ".join(form_gruende[0].details)
    assert erwartet in gesamt, gesamt


def test_beleg_ohne_handy_oder_desktop_bild_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    ordner = f"docs/verify-hard/{TICKET}"
    sha = _commit(
        repo,
        f"feat: nur Desktop-Bild (#{TICKET})",
        {
            "app/modul.py": "x = 1\n",
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(
                klickweg_text="| Schritt | Ergebnis | Bild |\n| Start | ok | ![Desktop](desktop.png) |\n"
            ),
            f"{ordner}/desktop.png": _png_bytes(1280, 800),
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    form_gruende = [g for g in ergebnis.gruende if g.regel == "beleg_form"]
    assert form_gruende, ergebnis.gruende
    assert "Handy" in (form_gruende[0].text + " ".join(form_gruende[0].details))


def test_beleg_mit_fehlendem_oder_leerem_bild_auf_master_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    ordner = f"docs/verify-hard/{TICKET}"
    sha = _commit(
        repo,
        f"feat: Bild fehlt bzw leer (#{TICKET})",
        {
            "app/modul.py": "x = 1\n",
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(),
            f"{ordner}/desktop.png": b"",
            # handy.png gar nicht eingecheckt
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    form_gruende = [g for g in ergebnis.gruende if g.regel == "beleg_form"]
    assert form_gruende, ergebnis.gruende
    gesamt = form_gruende[0].text + " " + " ".join(form_gruende[0].details)
    assert "desktop.png" in gesamt and "handy.png" in gesamt, gesamt


def test_beleg_bilder_ueber_png_masse_erkannt_ohne_schluesselwort_im_namen(tmp_path: Path) -> None:
    """PNG mit 1280x800 (Desktop) und 390x844 (Handy) zählt auch ohne 'desktop'/'handy' im Dateinamen."""
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    ordner = f"docs/verify-hard/{TICKET}"
    sha = _commit(
        repo,
        f"feat: Bilder nach Maßen (#{TICKET})",
        {
            "app/modul.py": "x = 1\n",
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(
                klickweg_text="Ansicht A: ![Ansicht A](bild_a.png)\nAnsicht B: ![Ansicht B](bild_b.png)\n"
            ),
            f"{ordner}/bild_a.png": _png_bytes(1280, 800),
            f"{ordner}/bild_b.png": _png_bytes(390, 844),
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is True, ergebnis.gruende


def test_reines_doku_ticket_braucht_keine_belegseite(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _commit(repo, f"docs: Anleitung (#{TICKET})", {"docs/anleitung.md": "# Anleitung\n"})
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is True, ergebnis.gruende


# --- 4. Regel: Kein Test ersetzt ----------------------------------------------


def test_entfernter_test_wird_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    ordner = f"docs/verify-hard/{TICKET}"
    sha = _commit(
        repo,
        f"feat: Test ersetzt (#{TICKET})",
        {
            "tests/test_basis.py": "def test_anders():\n    assert True\n",
            f"{ordner}/{TICKET}_beleg.md": _gueltige_belegseite(),
            f"{ordner}/desktop.png": _png_bytes(1280, 800),
            f"{ordner}/handy.png": _png_bytes(390, 844),
        },
    )
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is False
    assert [g.regel for g in ergebnis.gruende] == ["test_ersetzt"]
    assert "test_basis" in ergebnis.gruende[0].text


# --- 5. Regel: Commit grün auf Hauptzweig -------------------------------------


def test_nicht_gruen_auf_hauptzweig_wird_abgelehnt(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    vorher = _git(repo, "rev-parse", "HEAD")
    _sauberer_stand(repo)

    # Ohne Grün-Nachweis → abgelehnt
    ohne = schliess_pruefer.pruefe(schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master"))
    assert ohne.ok is False
    assert [g.regel for g in ohne.gruende] == ["nicht_gruen_auf_hauptzweig"]

    # Älterer grüner Stand vor dem Ticket-Commit → abgelehnt
    veraltet = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=vorher)
    )
    assert veraltet.ok is False
    assert [g.regel for g in veraltet.gruende] == ["nicht_gruen_auf_hauptzweig"]


def test_gruen_nachweis_aus_ticket_kommentar(tmp_path: Path) -> None:
    from to_spawn import schliess_pruefer

    repo, _ = _welt(tmp_path)
    sha = _sauberer_stand(repo)

    # Nur Kurz-SHA (7 Zeichen) genügt nicht (E18)
    kurz = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(
            repo=repo,
            ticket=TICKET,
            ref="origin/master",
            kommentare=[f"Deploy-Schlange: grün auf master ({sha[:7]})"],
        )
    )
    assert kurz.ok is False
    assert [g.regel for g in kurz.gruende] == ["nicht_gruen_auf_hauptzweig"]

    # Voller 40-Zeichen-SHA im Kommentar → grün
    voll = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(
            repo=repo,
            ticket=TICKET,
            ref="origin/master",
            kommentare=[f"Deploy-Schlange: grün auf master (Commit {sha})"],
        )
    )
    assert voll.ok is True, voll.gruende


# --- 6. Bewusst KEINE Regel „Server nicht aktuell“ ----------------------------


def test_server_nicht_aktuell_erzeugt_keine_ablehnung(tmp_path: Path) -> None:
    """E21: „Server nicht aktuell“ (vps_ungleich_origin) ist bewusst keine Regel im Schließ-Prüfer."""
    from to_spawn import schliess_pruefer

    assert "vps_ungleich_origin" not in schliess_pruefer.SCHLIESS_REGELN

    repo, _ = _welt(tmp_path)
    konfig = repo / ".to-spawn" / "config.json"
    konfig.parent.mkdir(parents=True, exist_ok=True)
    konfig.write_text(
        json.dumps({"vps": {"ssh": "server-test", "pfad": "/opt/app", "deploy_pfade": ["app/"]}}),
        encoding="utf-8",
    )
    sha = _sauberer_stand(repo)
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(repo=repo, ticket=TICKET, ref="origin/master", gruen_auf_hauptzweig=sha)
    )
    assert ergebnis.ok is True, ergebnis.gruende
    assert not any("vps" in g.regel for g in ergebnis.gruende)


# --- 7. Eine Quelle der Wahrheit: capo ruft schliess_pruefer.pruefe -----------


def test_capo_reopen_funde_ruft_schliess_pruefer_ohne_regelkopie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import capo, schliess_pruefer

    quelle_reopen = inspect.getsource(capo.reopen_funde)
    assert "schliess_pruefer.pruefe(" in quelle_reopen, quelle_reopen
    for alt in ("regel_commit(", "regel_beweis(", "regel_tests("):
        assert alt not in quelle_reopen, f"capo.reopen_funde kopiert/ruft noch {alt}"

    # Keine kopierte Regel-Implementierung in to_spawn/capo.py
    capo_text = (SKILL / "to_spawn" / "capo.py").read_text(encoding="utf-8")
    for verboten in ("def _test_verluste(", "def _kein_netto_verlust(", "def _test_abschnitte(", "_PY_TEST = "):
        assert verboten not in capo_text, f"Regel-Kopie noch in capo.py: {verboten}"

    # Laufzeit-Beweis: reopen_funde reicht tatsächlich an schliess_pruefer.pruefe durch
    aufrufe: list[schliess_pruefer.Stand] = []

    def spion(stand: schliess_pruefer.Stand) -> schliess_pruefer.PruefErgebnis:
        aufrufe.append(stand)
        return schliess_pruefer.PruefErgebnis(
            ok=False,
            gruende=[schliess_pruefer.Grund(regel="commit_ohne_nummer", text="Spion-Grund")],
        )

    monkeypatch.setattr(schliess_pruefer, "pruefe", spion)
    funde = capo.reopen_funde(tmp_path, "origin/master", TICKET, [], "docs/verify-hard", {}, None, [])
    assert len(aufrufe) == 1
    assert aufrufe[0].ticket == TICKET
    assert len(funde) == 1 and funde[0].regel == "commit_ohne_nummer" and funde[0].text == "Spion-Grund"


# --- 8. Weg-Test mit echtem Git-Repo über git_bash-Hilfe ----------------------


def test_weg_mit_echtem_git_repo_ueber_git_bash(tmp_path: Path) -> None:
    """Echter Ende-zu-Ende-Lauf über bash (tests/hilfen/git_bash.py) und Python-Modul."""
    bash = git_bash()
    if bash is None:
        pytest.skip("keine bash verfügbar")

    repo, _ = _welt(tmp_path)
    sha = _sauberer_stand(repo)
    skript = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from to_spawn import schliess_pruefer\n"
        "stand = schliess_pruefer.Stand(\n"
        "    repo=Path(sys.argv[1]),\n"
        "    ticket=int(sys.argv[2]),\n"
        "    ref='origin/master',\n"
        "    gruen_auf_hauptzweig=sys.argv[3],\n"
        ")\n"
        "erg = schliess_pruefer.pruefe(stand)\n"
        "print(json.dumps({'ok': erg.ok, 'regeln': [g.regel for g in erg.gruende]}))\n"
    )
    umgebung = {**os.environ, "PYTHONPATH": str(SKILL), "PYTHONIOENCODING": "utf-8"}
    lauf = subprocess.run(
        [
            bash,
            "-c",
            'git -C "$1" rev-parse --verify origin/master >/dev/null && "$2" -c "$3" "$1" "$4" "$5"',
            "_",
            str(repo),
            sys.executable,
            skript,
            str(TICKET),
            sha,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=umgebung,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout.strip())
    assert daten == {"ok": True, "regeln": []}
