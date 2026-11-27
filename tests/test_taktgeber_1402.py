"""#906 (App #1402): Zustandsloser Taktgeber Bau-Server.

Weg-Tests ohne Zustandsdatei mit echten aufgezeichneten GitHub-Antworten
(Vorlagen aus ``tests/hilfen/takt_316/``), echtem Git-Repo und echter Start-Stelle
(``to_spawn/startstelle.py``):

- schließt bei vier erfüllten Regeln (Commit-Nummer, Belegseite, kein Test ersetzt,
  „grün auf master/main“ mit vollem 40-Zeichen-Hash) und nennt bei Verletzung den
  Grund aus dem Prüfer;
- schließt ``checkpoint:human``-Tickets nie (auch wenn alle 4 Regeln stimmen);
- setzt bei „fertig auf Staging, nicht live“ das Label ``staging:fertig`` ohne
  Wiedereröffnung; nach Live-Meldung der Schlange → geschlossen, Label entfernt;
- weckt wiedereröffnete Tickets genau einmal über die Start-Stelle; ein zweiter
  gleichzeitiger Lauf weckt nicht doppelt (Sperre je Ticket);
- zwei Läufe hintereinander und zwei gleichzeitige Läufe ergeben denselben
  GitHub-Endstand;
- Grep-/AST-Nachweis: keine eigene Zustandsdatei, keine eigene Fertig-Regel;
- jede Handlung steht über ``bau_log.schreibe()`` im Ereignis-Protokoll;
- idempotente Cron-Einrichtung mit eigener Marke neben dem Aufseher-Takt;
- Aufseher-Anleitung beschränkt das Urteil auf Klickweg, Rolle, Bild und Inhalt;
- ADR-0008-Nachtrag dokumentiert.
"""

from __future__ import annotations

import ast
import concurrent.futures
import json
import os
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
HILFEN = SKILL / "tests" / "hilfen"
FIXTURE = HILFEN / "takt_316"
GH_STUB = HILFEN / "gh_taktgeber_1402.py"
PY = sys.executable

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import anleitung, bau_log, befund, ticket_zustand, waechter_takt


def _git(repo: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return erg.stdout.strip()


def _vorlage_issue(
    nummer: int,
    *,
    state: str = "open",
    labels: list[str] | None = None,
    state_reason: str | None = None,
    closed_at: str | None = None,
) -> dict[str, Any]:
    """Baut einen Issue-Eintrag aus der echten GitHub-Aufzeichnung ``sub_issues_293.json``."""
    vorlagen = json.loads((FIXTURE / "sub_issues_293.json").read_text(encoding="utf-8"))
    basis = dict(vorlagen[0])
    basis["number"] = nummer
    basis["title"] = f"Ticket #{nummer}"
    basis["state"] = state
    basis["state_reason"] = state_reason
    basis["closed_at"] = closed_at
    basis["labels"] = [{"name": name} for name in (labels or [])]
    return basis


def _vorlage_kommentar(body: str, *, created_at: str = "2026-11-27T10:00:00Z", rolle: str = "OWNER") -> dict[str, Any]:
    """Baut einen Kommentar aus der echten GitHub-Aufzeichnung ``kommentar_315.json``."""
    basis = json.loads((FIXTURE / "kommentar_315.json").read_text(encoding="utf-8"))
    return {**basis, "body": body, "created_at": created_at, "author_association": rolle}


def _welt_bauen(
    tmp_path: Path,
    issues: list[dict[str, Any]],
    kommentare: dict[str, list[dict[str, Any]]],
    *,
    konfig: dict[str, Any] | None = None,
    repo_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Echtes Git-Repo mit origin/master + aufgezeichneter GitHub-Zustand in ``tmp_path``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".to-spawn").mkdir()
    standard_konfig: dict[str, Any] = {
        "hauptzweig": "master",
        "live": {"ohne": True},
    }
    if konfig:
        standard_konfig.update(konfig)
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(standard_konfig), encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_basis.py").write_text("def test_bestehend():\n    assert True\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "chore: basis")
    fern = tmp_path / "fern" / "test-org" / "beispiel-app.git"
    _git(repo, "remote", "add", "origin", fern.as_posix())
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")

    zustand_pfad = tmp_path / "gh_zustand.json"
    kommentar_vorlage = json.loads((FIXTURE / "kommentar_315.json").read_text(encoding="utf-8"))
    zustand_pfad.write_text(
        json.dumps(
            {
                "issues": issues,
                "kommentare": kommentare,
                "repo_labels": list(repo_labels or [ticket_zustand.STAGING_FERTIG_LABEL]),
                "kommentar_vorlage": kommentar_vorlage,
                "aufrufe": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_GH_STUB": str(GH_STUB),
            "GH_TAKTGEBER_ZUSTAND": str(zustand_pfad),
            "TO_SPAWN_START_SPERREN": str(tmp_path / "start-sperren"),
            "TO_SPAWN_TEST_FERN_SPERRE_AUS": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return {"repo": repo, "zustand": zustand_pfad, "env": env, "tmp": tmp_path}


def _png_bytes(breite: int, hoehe: int) -> bytes:
    """Kleinstes gültiges PNG mit echter IHDR-Breite und -Höhe für feste Belegform."""

    def _chunk(typ: bytes, daten: bytes) -> bytes:
        crc = zlib.crc32(typ + daten) & 0xFFFFFFFF
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    zeile = b"\x00" + b"\x00\x00\x00" * breite
    idat = zlib.compress(zeile * hoehe)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _commit_mit_beleg(
    repo: Path,
    ticket: int,
    *,
    mit_beleg: bool = True,
    mit_nummer: bool = True,
    test_loeschen: bool = False,
) -> str:
    """Legt einen echten Commit auf ``origin/master`` an und liefert den vollen 40-Zeichen-SHA."""
    datei = repo / f"modul_{ticket}.py"
    datei.write_text(f"WERT_{ticket} = {ticket}\n", encoding="utf-8")
    if mit_beleg:
        stand_sha = _git(repo, "rev-parse", "HEAD")
        beleg_ordner = repo / "docs" / "verify-hard" / str(ticket)
        beleg_ordner.mkdir(parents=True, exist_ok=True)
        (beleg_ordner / "desktop.png").write_bytes(_png_bytes(1280, 800))
        (beleg_ordner / "handy.png").write_bytes(_png_bytes(390, 844))
        (beleg_ordner / f"{ticket}_beweis.md").write_text(
            f"# Beleg #{ticket}\n\n"
            f"- **Ticket:** #{ticket}\n"
            f"- **Commit-Stand:** `{stand_sha}`\n"
            "- **Staging-Adresse:** https://staging.example.test/start\n"
            "- **Rolle:** Admin\n"
            "- **Datum:** 2026-11-27 10:00\n\n"
            "## Tests\ngrün\n\n"
            "## Staging-Klickweg\n"
            "| Schritt | Ergebnis | Bild |\n"
            "| --- | --- | --- |\n"
            "| Startseite Desktop | sichtbar, 0 offen | ![Desktop](desktop.png) |\n"
            "| Startseite Handy 390x844 | lesbar, 0 offen | ![Handy](handy.png) |\n",
            encoding="utf-8",
        )
    if test_loeschen:
        (repo / "tests" / "test_basis.py").write_text("def test_anders():\n    assert True\n", encoding="utf-8")
    _git(repo, "add", ".")
    betreff = f"feat: Umsetzung (#{ticket})" if mit_nummer else "feat: Umsetzung ohne Nummer"
    _git(repo, "commit", "-q", "-m", betreff)
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    return _git(repo, "rev-parse", "HEAD")


def _taktgeber_cli(welt: dict[str, Any], spec: int = 293, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            PY,
            str(SKILL / "to_spawn.py"),
            "taktgeber",
            str(spec),
            "--repo-dir",
            str(welt["repo"]),
            "--gh-repo",
            "test-org/beispiel-app",
            *extra,
        ],
        cwd=str(SKILL),
        env=welt["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _gh_stand(welt: dict[str, Any]) -> dict[str, Any]:
    return json.loads(welt["zustand"].read_text(encoding="utf-8"))


def _issue_aus_stand(stand: dict[str, Any], nummer: int) -> dict[str, Any]:
    return next(i for i in stand["issues"] if int(i["number"]) == nummer)


def _installiere_test_bau(
    repo: Path,
    weck_protokoll: Path,
    *,
    halte_sekunden: float = 0.0,
    stop_datei: Path | None = None,
) -> None:
    """Legt eine ``scripts/bau.py``-Test-Attrappe im Repo an, die durch ``startstelle.betreten`` geht."""
    skript = repo / "scripts" / "bau.py"
    skript.parent.mkdir(parents=True, exist_ok=True)
    stop_pfad = str(stop_datei) if stop_datei is not None else ""
    skript.write_text(
        f"""from __future__ import annotations
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, {str(SKILL)!r})
from to_spawn import startstelle

ticket = sys.argv[1]
entsch = startstelle.betreten(ticket, "test-org/beispiel-app", cwd=Path({str(repo)!r}))
if not entsch.erlaubt:
    sys.exit(entsch.exit_code)
with Path({str(weck_protokoll)!r}).open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"ticket": ticket, "auftrag": os.environ.get("BAU_AUFTRAG", "")}}) + "\\n")
stop_pfad = {stop_pfad!r}
if stop_pfad:
    frist = time.monotonic() + max({halte_sekunden!r}, 20.0)
    while not Path(stop_pfad).exists() and time.monotonic() < frist:
        time.sleep(0.05)
elif {halte_sekunden!r} > 0:
    time.sleep({halte_sekunden!r})
""",
        encoding="utf-8",
    )


# --- AC 1: 4 Regeln erfüllt → schließt; Regel verletzt → bleibt offen + Prüfer-Grund ---


def test_schliesst_bei_vier_erfuellten_regeln_und_meldet_verletzung(tmp_path: Path) -> None:
    welt = _welt_bauen(
        tmp_path,
        issues=[
            _vorlage_issue(501),
            _vorlage_issue(502),
            _vorlage_issue(503),
            _vorlage_issue(504),
        ],
        kommentare={},
    )
    sha_501 = _commit_mit_beleg(welt["repo"], 501, mit_beleg=True, mit_nummer=True)
    sha_502 = _commit_mit_beleg(welt["repo"], 502, mit_beleg=False, mit_nummer=True)
    sha_503 = _commit_mit_beleg(welt["repo"], 503, mit_beleg=True, mit_nummer=True, test_loeschen=True)
    sha_504 = _commit_mit_beleg(welt["repo"], 504, mit_beleg=True, mit_nummer=True)

    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "501": [_vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_501}")],
        "502": [_vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_502}")],
        "503": [_vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_503}")],
        # Kurzer 7-Zeichen-Hash genügt nicht (#1400):
        "504": [_vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_504[:7]}")],
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    erg = _taktgeber_cli(welt)
    assert erg.returncode == 0, erg.stdout + erg.stderr

    nachher = _gh_stand(welt)
    assert _issue_aus_stand(nachher, 501)["state"] == "closed"
    assert _issue_aus_stand(nachher, 502)["state"] == "open"
    assert _issue_aus_stand(nachher, 503)["state"] == "open"
    assert _issue_aus_stand(nachher, 504)["state"] == "open"

    # Prüfer-Gründe stehen in der Ausgabe
    assert "502" in erg.stdout and "Belegseite" in erg.stdout
    assert "503" in erg.stdout and "Test entfernt" in erg.stdout

    # Handlung steht über bau_log.schreibe() im Protokoll
    zeilen_501 = bau_log.lese(welt["repo"], 501)
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "geschlossen" for z in zeilen_501), zeilen_501


# --- AC 2: checkpoint:human bleibt immer offen ---


def test_checkpoint_human_bleibt_immer_offen(tmp_path: Path) -> None:
    welt = _welt_bauen(
        tmp_path,
        issues=[_vorlage_issue(510, labels=["checkpoint:human"])],
        kommentare={},
    )
    sha = _commit_mit_beleg(welt["repo"], 510, mit_beleg=True, mit_nummer=True)
    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "510": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha}"),
            _vorlage_kommentar(f"Deploy-Schlange: live · {sha}", created_at="2026-11-27T10:05:00Z"),
        ]
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    erg = _taktgeber_cli(welt)
    assert erg.returncode == 0, erg.stdout + erg.stderr
    nachher = _gh_stand(welt)
    assert _issue_aus_stand(nachher, 510)["state"] == "open"
    assert not any(a["aktion"] == "close" for a in nachher["aufrufe"])
    assert ticket_zustand.STAGING_FERTIG_LABEL not in ticket_zustand.label_namen(_issue_aus_stand(nachher, 510))


# --- AC 3: Staging fertig, wartet auf Live → Label; nach Live-Meldung → geschlossen + Label entfernt ---


def test_staging_fertig_setzt_label_und_schliesst_erst_nach_live(tmp_path: Path) -> None:
    welt = _welt_bauen(
        tmp_path,
        issues=[_vorlage_issue(520)],
        kommentare={},
        konfig={"hauptzweig": "master", "live": {"ohne": False, "befehl": "python deploy.py live"}},
    )
    sha = _commit_mit_beleg(welt["repo"], 520, mit_beleg=True, mit_nummer=True)
    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "520": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha}"),
            # Verneinung darf NICHT als Live-Signal zählen:
            _vorlage_kommentar("Hinweis: fertig auf Staging, wartet auf Live.", created_at="2026-11-27T10:01:00Z"),
        ]
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    # Lauf 1: nur Staging → Label staging:fertig gesetzt, bleibt offen, kein Reopen
    erg1 = _taktgeber_cli(welt)
    assert erg1.returncode == 0, erg1.stdout + erg1.stderr
    nach1 = _gh_stand(welt)
    issue1 = _issue_aus_stand(nach1, 520)
    assert issue1["state"] == "open"
    assert ticket_zustand.STAGING_FERTIG_LABEL in ticket_zustand.label_namen(issue1)

    zeilen1 = bau_log.lese(welt["repo"], 520)
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "label_gesetzt" for z in zeilen1), zeilen1

    # Jetzt meldet die Deploy-Schlange Live am Ticket
    nach1["kommentare"]["520"].append(
        _vorlage_kommentar(f"Deploy-Schlange: ist live · {sha}", created_at="2026-11-27T10:15:00Z")
    )
    welt["zustand"].write_text(json.dumps(nach1, ensure_ascii=False, indent=2), encoding="utf-8")

    # Lauf 2: Live da → geschlossen + Label entfernt
    erg2 = _taktgeber_cli(welt)
    assert erg2.returncode == 0, erg2.stdout + erg2.stderr
    nach2 = _gh_stand(welt)
    issue2 = _issue_aus_stand(nach2, 520)
    assert issue2["state"] == "closed"
    assert ticket_zustand.STAGING_FERTIG_LABEL not in ticket_zustand.label_namen(issue2)

    zeilen2 = bau_log.lese(welt["repo"], 520)
    assert any(z.get("wer") == "taktgeber" and z.get("aktion") == "geschlossen" for z in zeilen2), zeilen2


# --- AC 4: Wiedereröffnetes Ticket → genau ein Weck-Aufruf über Start-Stelle, kein Doppel-Wecken ---


def test_wiedereroeffnetes_ticket_weckt_einmal_ueber_startstelle_auch_gleichzeitig(tmp_path: Path) -> None:
    welt = _welt_bauen(
        tmp_path,
        issues=[
            _vorlage_issue(
                530,
                state="open",
                state_reason="reopened",
                closed_at="2026-11-27T09:50:00Z",
            )
        ],
        kommentare={},
    )
    sha_alt = _commit_mit_beleg(welt["repo"], 530, mit_beleg=True, mit_nummer=True)
    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "530": [
            # Alter Grün-Kommentar VOR der Wiedereröffnung darf nicht sofort wieder schließen:
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_alt}", created_at="2026-11-27T09:40:00Z"),
            _vorlage_kommentar(
                "Aufseher: Belegseite — Klickweg zeigt falsche Rolle", created_at="2026-11-27T10:00:00Z"
            ),
        ]
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    weck_log = tmp_path / "geweckt.jsonl"
    stop_datei = tmp_path / "stop_bau"
    _installiere_test_bau(welt["repo"], weck_log, halte_sekunden=20.0, stop_datei=stop_datei)
    # Ohne tmux starten, damit direkt scripts/bau.py über startstelle läuft (im Test-Attrappen-Ordner)
    welt["env"]["TO_SPAWN_TMUX"] = str(tmp_path / "kein-tmux-vorhanden")
    welt["env"]["TO_SPAWN_TEST_BIN"] = str(welt["repo"] / "scripts")

    try:
        # Zwei gleichzeitige Taktgeber-Prozesse laufen parallel gegen dasselbe wiedereröffnete Ticket
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_taktgeber_cli, welt)
            f2 = pool.submit(_taktgeber_cli, welt)
            r1, r2 = f1.result(), f2.result()

        assert r1.returncode == 0, r1.stdout + r1.stderr
        assert r2.returncode == 0, r2.stdout + r2.stderr

        # Kurz warten, bis das gestartete bau.py seinen Eintrag geschrieben hat
        for _ in range(80):
            if weck_log.is_file() and weck_log.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)

        # Ticket blieb offen (nicht durch alten Grün-Kommentar geschlossen) und genau 1 Weck-Aufruf!
        nachher = _gh_stand(welt)
        assert _issue_aus_stand(nachher, 530)["state"] == "open"
        weck_eintraege = [json.loads(z) for z in weck_log.read_text(encoding="utf-8").splitlines() if z.strip()]
        assert len(weck_eintraege) == 1, weck_eintraege
        assert weck_eintraege[0]["ticket"] == "530"

        # Auch ein sofortiger Folgelauf während bau.py noch läuft weckt nicht erneut (Start-Sperre aktiv)
        r3 = _taktgeber_cli(welt)
        assert r3.returncode == 0, r3.stdout + r3.stderr
        weck_eintraege_nach3 = [json.loads(z) for z in weck_log.read_text(encoding="utf-8").splitlines() if z.strip()]
        assert len(weck_eintraege_nach3) == 1, weck_eintraege_nach3
    finally:
        stop_datei.write_text("stop\n", encoding="utf-8")

    # Handlung steht über bau_log.schreibe() im Protokoll
    zeilen = bau_log.lese(welt["repo"], 530)
    geweckt_zeilen = [z for z in zeilen if z.get("wer") == "taktgeber" and z.get("aktion") == "geweckt"]
    assert len(geweckt_zeilen) == 1, zeilen


# --- AC 5: Idempotenz (hintereinander + gleichzeitig) & Grep-Beweis ohne Zustandsdatei ---


def test_idempotenz_hintereinander_und_gleichzeitig(tmp_path: Path) -> None:
    welt = _welt_bauen(
        tmp_path,
        issues=[_vorlage_issue(540), _vorlage_issue(541)],
        kommentare={},
        konfig={"hauptzweig": "master", "live": {"ohne": False, "befehl": "python deploy.py live"}},
    )
    sha_540 = _commit_mit_beleg(welt["repo"], 540)
    sha_541 = _commit_mit_beleg(welt["repo"], 541)
    stand = _gh_stand(welt)
    stand["kommentare"] = {
        "540": [
            _vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_540}"),
            _vorlage_kommentar(f"Deploy-Schlange: live · {sha_540}", created_at="2026-11-27T10:05:00Z"),
        ],
        "541": [_vorlage_kommentar(f"Deploy-Schlange: grün auf master · {sha_541}")],
    }
    welt["zustand"].write_text(json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")

    # Zwei gleichzeitige Läufe
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = pool.submit(_taktgeber_cli, welt).result(), pool.submit(_taktgeber_cli, welt).result()
    assert r1.returncode == 0 and r2.returncode == 0
    stand_nach_parallel = _gh_stand(welt)
    assert _issue_aus_stand(stand_nach_parallel, 540)["state"] == "closed"
    assert _issue_aus_stand(stand_nach_parallel, 541)["state"] == "open"
    assert ticket_zustand.STAGING_FERTIG_LABEL in ticket_zustand.label_namen(_issue_aus_stand(stand_nach_parallel, 541))

    aufrufe_nach_parallel = list(stand_nach_parallel["aufrufe"])

    # Noch ein Lauf hintereinander → identischer Endstand, keine weiteren Schreib-Aufrufe
    r3 = _taktgeber_cli(welt)
    assert r3.returncode == 0
    stand_nach_folge = _gh_stand(welt)
    assert stand_nach_folge["issues"] == stand_nach_parallel["issues"]
    assert stand_nach_folge["aufrufe"] == aufrufe_nach_parallel


def test_grep_beweis_keine_zustandsdatei_im_taktgeber() -> None:
    """Der Taktgeber führt keine eigene Zustandsdatei (kein Leitstand, kein Merker, kein JSON-Zustand)."""
    pfad = SKILL / "to_spawn" / "taktgeber.py"
    assert pfad.is_file(), "to_spawn/taktgeber.py fehlt"
    text = pfad.read_text(encoding="utf-8")
    verboten = [
        r"\bleitstand\b",
        r"zustand\.json",
        r"lese_zustand",
        r"aendere_zustand",
        r"schreibe_json",
        r"\.merker\b",
    ]
    funde = [m for m in verboten if re.search(m, text)]
    assert funde == [], f"Taktgeber greift auf Zustandsdatei/Merker zu: {funde}"


# --- AC 6: Keine eigene Fertig-Regel im Taktgeber (Regeln stehen nur im Prüfer) ---


def test_grep_beweis_keine_eigene_fertig_regel_im_taktgeber() -> None:
    """Die vier Fertig-Regeln stehen im Prüfer, nicht als Kopie im Taktgeber."""
    pfad = SKILL / "to_spawn" / "taktgeber.py"
    assert pfad.is_file(), "to_spawn/taktgeber.py fehlt"
    text = pfad.read_text(encoding="utf-8")
    baum = ast.parse(text)
    funktionsnamen = {k.name for k in ast.walk(baum) if isinstance(k, ast.FunctionDef)}
    for verboten in ("regel_commit", "regel_beweis", "regel_tests", "regel_vps", "beleg_passt", "betreff_hat_nummer"):
        assert verboten not in funktionsnamen, f"Taktgeber dupliziert Regel {verboten}"
    # Keine eigenen Belegordner-/Testdiff-Prüfungen
    assert "docs/verify-hard" not in text
    assert "test_ersetzt" not in text
    assert "beweis_fehlt" not in text
    assert "commit_ohne_nummer" not in text


# --- AC 8: Idempotente Cron-Einrichtung neben dem Aufseher-Takt ---


def test_cron_einrichtung_idempotent_und_neben_waechter_takt() -> None:
    from to_spawn import taktgeber

    assert taktgeber.cron_marke(293) != waechter_takt.cron_marke(293)
    bestehend = [
        "0 3 * * * /usr/local/bin/backup",
        f"*/5 * * * * python3 to_spawn.py takt 293  {waechter_takt.cron_marke(293)}",
    ]
    befehl = taktgeber.cron_befehl(293, Path("/srv/mein repo"), Path("/tmp/taktgeber-293.log"))
    assert befehl.startswith(taktgeber.CRON_TAKT + " ")
    assert "taktgeber 293" in befehl

    einmal = taktgeber.cron_zeilen(bestehend, 293, befehl)
    zweimal = taktgeber.cron_zeilen(einmal, 293, befehl)
    assert einmal == zweimal
    assert sum(taktgeber.cron_marke(293) in z for z in zweimal) == 1
    assert sum(waechter_takt.cron_marke(293) in z for z in zweimal) == 1


def test_windows_prozess_optionen_ohne_detached_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import taktgeber

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    optionen = taktgeber.abgeloest_optionen()
    flags = optionen["creationflags"]
    assert flags & 0x08000000
    assert flags & 0x00000200
    assert not (flags & 0x00000008), "DETACHED_PROCESS ist unter Windows verboten"


def test_windows_wecke_ticket_startet_bau_direkt_fensterlos_ohne_wt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unter Windows startet ``wecke_ticket`` fensterlos direkt ``bau.py`` (kein ``wt``-Fenster, Fix-Runde 2)."""
    from to_spawn import starter, taktgeber

    repo = tmp_path / "repo"
    skript = repo / "scripts" / "bau.py"
    skript.parent.mkdir(parents=True)
    skript.write_text("# test-bau\n", encoding="utf-8")

    monkeypatch.setenv("TO_SPAWN_START_SPERREN", str(tmp_path / "sperren"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)

    gestartet_cmds: list[tuple[list[str], dict[str, Any]]] = []

    class _FakeProc:
        def poll(self) -> int:
            return 0

    def _fake_starte(cmd: list[str], **kwargs: Any) -> _FakeProc:
        gestartet_cmds.append((list(cmd), dict(kwargs)))
        return _FakeProc()

    monkeypatch.setattr(starter, "starte", _fake_starte)

    ok, _meldung = taktgeber.wecke_ticket(repo, 293, 530, "test-org/beispiel-app", "Beleg fehlt")
    assert ok is True
    assert len(gestartet_cmds) == 1
    cmd, kwargs = gestartet_cmds[0]
    assert cmd[0] == sys.executable, f"Windows-Wecken muss direkt Python/bau.py starten, nicht {cmd[0]!r}"
    assert Path(cmd[1]) == skript
    assert cmd[2:] == ["530", "--sofort"]
    assert "wt" not in Path(cmd[0]).name.lower()
    flags = int(kwargs.get("creationflags") or 0)
    assert flags & 0x08000000
    assert flags & 0x00000200
    assert not (flags & 0x00000008)


# --- AC 9 & 10: Aufseher-Anleitung (Wache-SOP) & ADR-0008-Nachtrag ---


def test_aufseher_anleitung_beschraenkt_urteil_auf_klickweg_rolle_bild_inhalt() -> None:
    """Aufseher-Anleitung beschränkt das Urteil auf Klickweg, Rolle, Bild und Inhalt."""
    assert hasattr(anleitung, "AUFSEHER_URTEIL")
    urteil = anleitung.AUFSEHER_URTEIL
    for wort in ("Klickweg", "Rolle", "Bild", "Inhalt"):
        assert wort in urteil, f"{wort!r} fehlt in anleitung.AUFSEHER_URTEIL"
    wache_text = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "anleitung.AUFSEHER_URTEIL" in wache_text
    assert urteil in waechter_takt.PROMPT


def test_adr_0008_nachtrag_dokumentiert() -> None:
    """Doku-/ADR-0008-Nachtrag beschreibt Taktgeber ohne Gedächtnis + Aufpasser als Schreiber."""
    nachtrag = SKILL / "docs" / "ADR_0008_taktgeber_nachtrag.md"
    assert nachtrag.is_file(), "docs/ADR_0008_taktgeber_nachtrag.md fehlt"
    inhalt = nachtrag.read_text(encoding="utf-8")
    for begriff in ("ADR 0008", "Taktgeber", "ohne Gedächtnis", "Aufpasser", "Ereignis-Protokoll"):
        assert begriff in inhalt, f"{begriff!r} fehlt im ADR-0008-Nachtrag"
    assert "taktgeber" in (SKILL / "SKILL.md").read_text(encoding="utf-8")


# --- Zusatz: Prüfer-Anbindung bei vorhandenem schliess_pruefer (#864) ---


def test_delegiert_an_schliess_pruefer_wenn_vorhanden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ist ``to_spawn.schliess_pruefer`` installiert, nutzt der Taktgeber direkt dessen ``pruefe(stand)``-Tür."""
    from types import ModuleType

    from to_spawn import taktgeber

    aufrufe: list[dict[str, Any]] = []
    fake_modul = ModuleType("to_spawn.schliess_pruefer")

    def fake_pruefe(stand: dict[str, Any]) -> tuple[bool, list[str]]:
        aufrufe.append(stand)
        return False, ["Belegseite entspricht nicht der festen Form"]

    fake_modul.pruefe = fake_pruefe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "to_spawn.schliess_pruefer", fake_modul)

    erg = taktgeber.pruefe_mit_pruefer(
        tmp_path,
        "origin/master",
        777,
        [],
        {},
        gruen_sha="a" * 40,
    )
    assert erg.ok is False
    assert erg.gruende == ("Belegseite entspricht nicht der festen Form",)
    assert len(aufrufe) == 1 and aufrufe[0]["ticket"] == 777
    assert befund.KOPF == "Aufseher:"
