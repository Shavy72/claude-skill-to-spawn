"""Rot-/Grün-Tests für die Schlüssel-/Secret-Sperre im Beleg-Ordner (#863, aus App #503 / #426).

Prüft:
- Zentrale Muster (Live-API-Bearer-Tokens, Secrets, private Schlüssel) in ``to_spawn.pruef.sofort``
  ohne Klartext-Leck im roten Befund und ohne Fehlalarme auf Platzhaltern.
- Beleg-Ordner aus ``regularien.belege_ordner`` (Vorgabe ``docs/verify-hard``), Plattform-neutral
  (POSIX- und Windows-Pfade, Binärdateien).
- Einbindung in ``sofort.stufen`` (auch bei ``pruefen.doku_pfade``) und ``sofort.stufe0`` (CLI/CI).
- Commit-Hook ``review-hook/commit.py`` blockiert Commits mit Schlüsseln im Beleg-Ordner
  (staged, ``-a``, Pathspec ``--``) mit klarem roten Befund ohne Mail-Alarm (E40).
- Gegenprobe über den bestehenden Beleg-Ordner ``docs/verify-hard`` dieses Repos ohne Fehlalarme.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn.pruef import leiter, sofort

REPO_WURZEL = Path(__file__).resolve().parent.parent
COMMIT_HOOK = REPO_WURZEL / "review-hook" / "commit.py"
REVIEW_ORDNER = REPO_WURZEL / "review"

# Synthetische Test-Schlüssel (keine echten Zugangsdaten, aus Teilen zusammengesetzt)
_LIVE_BEARER = "Bearer " + "liveDashApi_" + "9f8e7d6c5b4a3210fedcba9876543210"
_PRIV_KEY_KOPF = "-----BEGIN " + "RSA PRIVATE KEY-----"
_GH_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_API_SECRET_WERT = "sec_" + "prod_9876543210abcdef1234567890"


def _git(repo: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return erg.stdout


def _init_repo(pfad: Path, konfig: dict[str, Any] | None = None) -> Path:
    pfad.mkdir(parents=True, exist_ok=True)
    _git(pfad, "init", "-q")
    _git(pfad, "config", "user.email", "test@example.invalid")
    _git(pfad, "config", "user.name", "Test")
    (pfad / "README.md").write_text("# Test\n", encoding="utf-8")
    if konfig is not None:
        konfig_datei = pfad / ".to-spawn" / "config.json"
        konfig_datei.parent.mkdir(parents=True, exist_ok=True)
        konfig_datei.write_text(json.dumps(konfig), encoding="utf-8")
    _git(pfad, "add", ".")
    _git(pfad, "commit", "-q", "-m", "init")
    _git(pfad, "branch", "-f", "base", "HEAD")
    return pfad


def _rufe_commit_hook(
    tmp_path: Path,
    repo: Path,
    befehl: str,
    *,
    modus: str = "hinweis",
) -> tuple[int, dict[str, Any] | None]:
    basis_cfg = json.loads((REVIEW_ORDNER / "config.standard.json").read_text(encoding="utf-8"))
    cfg_pfad = tmp_path / "review_config.json"
    cfg_pfad.write_text(json.dumps({**basis_cfg, "modus": modus}), encoding="utf-8")
    env = {
        **os.environ,
        "REVIEW_CONFIG": str(cfg_pfad),
        "REVIEW_LOG": str(tmp_path / "ausloeser.jsonl"),
        "REVIEW_SITZUNGEN": str(tmp_path / "sitzungen"),
        "REVIEW_HOOK_LOG": str(tmp_path / "hook-fehler.log"),
        "REVIEW_MENSCH_NOETIG": str(tmp_path / "mensch_noetig.jsonl"),
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }
    env.pop("REVIEW_AUS", None)
    eingabe = {
        "session_id": "s-863",
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": befehl},
    }
    erg = subprocess.run(
        [sys.executable, str(COMMIT_HOOK)],
        input=json.dumps(eingabe).encode("utf-8"),
        capture_output=True,
        env=env,
        check=False,
        timeout=30,
    )
    text = erg.stdout.decode("utf-8").strip()
    return erg.returncode, (json.loads(text) if text else None)


# --- 1. Zentrale Muster & Klartext-Schutz --------------------------------------


def test_zentrale_muster_erkennen_bearer_secrets_und_private_schluessel() -> None:
    assert hasattr(sofort, "BELEG_SCHLUESSEL_MUSTER")
    assert len(sofort.BELEG_SCHLUESSEL_MUSTER) >= 4

    text = "\n".join(
        [
            f"Authorization: {_LIVE_BEARER}",
            _PRIV_KEY_KOPF,
            f"token = {_GH_TOKEN}",
            f"DASHBOARD_API_SECRET={_API_SECRET_WERT}",
        ]
    )
    funde = sofort.pruefe_beleg_text("docs/verify-hard/416/frozen/live.txt", text)
    zeilen = {f.zeile for f in funde}
    assert {1, 2, 3, 4}.issubset(zeilen)


def test_platzhalter_und_geschwaerzte_werte_erzeugen_keinen_fehlalarm() -> None:
    text = (
        "Authorization: Bearer <token>\n"
        "Authorization: Bearer ***\n"
        "Authorization: Bearer REDACTED\n"
        "Authorization: Bearer xxxxxxxxxxxxxxxxxxxxxxxx\n"
        "API_KEY=PLACEHOLDER_VALUE_123456\n"
        "API_SECRET=CHANGEME_IN_BITWARDEN\n"
        "normale Belegzeile ohne Geheimnis\n"
    )
    funde = sofort.pruefe_beleg_text("docs/verify-hard/863/platzhalter.txt", text)
    assert funde == []


def test_bericht_leakt_keinen_klartext_schluessel(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    beleg = repo / "docs" / "verify-hard" / "416" / "frozen" / "api.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text(f"Header: Authorization: {_LIVE_BEARER}\n", encoding="utf-8")

    erg = sofort.pruefe_beleg_dateien(repo, ["docs/verify-hard/416/frozen/api.txt"])
    assert not erg.ok
    assert len(erg.funde) == 1
    bericht = erg.bericht()
    assert "docs/verify-hard/416/frozen/api.txt:1" in bericht
    assert "9f8e7d6c5b4a3210" not in bericht


def test_windows_pfade_und_binaerdateien_werden_sauber_behandelt(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    ordner = repo / "docs" / "verify-hard" / "863"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "bild.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + _LIVE_BEARER.encode())
    (ordner / "log.txt").write_text(f"Authorization: {_LIVE_BEARER}\n", encoding="utf-8")

    erg = sofort.pruefe_beleg_dateien(
        repo,
        [r"docs\verify-hard\863\bild.png", r"docs\verify-hard\863\log.txt"],
    )
    assert not erg.ok
    assert [f.datei for f in erg.funde] == ["docs/verify-hard/863/log.txt"]


# --- 2. Einbindung in sofort.stufen & sofort.stufe0 ----------------------------


def test_sofort_stufen_blockiert_schluessel_trotz_nur_doku(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo", konfig={"pruefen": {"doku_pfade": ["docs/**"]}})
    beleg = repo / "docs" / "verify-hard" / "416" / "frozen" / "probe.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text(f"Authorization: {_LIVE_BEARER}\n", encoding="utf-8")

    liste, halt = sofort.stufen(repo, "base")
    assert halt == sofort.ROT_HALT
    schluessel_stufen = [s for s in liste if s.name == "beleg_schluessel"]
    assert len(schluessel_stufen) == 1
    assert schluessel_stufen[0].ergebnis == leiter.ROT
    assert "docs/verify-hard/416/frozen/probe.txt" in schluessel_stufen[0].ausgabe


def test_sofort_stufe0_meldet_rot_bei_schluessel_und_gruen_ohne(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    beleg = repo / "docs" / "verify-hard" / "863" / "befund.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text(f"{_PRIV_KEY_KOPF}\n", encoding="utf-8")

    erg_rot = sofort.stufe0(repo, "base")
    assert not erg_rot.ok
    assert erg_rot.exit_code == leiter.ROT
    assert any(s.name == "beleg_schluessel" and s.ergebnis == leiter.ROT for s in erg_rot.stufen)

    beleg.write_text("Sauberer Beleg ohne Schlüssel.\n", encoding="utf-8")
    erg_gruen = sofort.stufe0(repo, "base")
    assert erg_gruen.ok
    assert erg_gruen.exit_code == leiter.GRUEN


def test_konfigurierter_belege_ordner_wird_geprueft(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo", konfig={"regularien": {"belege_ordner": "belege/pruef"}})
    beleg = repo / "belege" / "pruef" / "lauf.log"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text(f"DASHBOARD_API_TOKEN={_API_SECRET_WERT}\n", encoding="utf-8")

    erg = sofort.pruefe_beleg_diff(repo, "base")
    assert not erg.ok
    assert erg.funde[0].datei == "belege/pruef/lauf.log"


# --- 3. Commit-Hook review-hook/commit.py --------------------------------------


@pytest.mark.parametrize(
    ("modus", "befehl", "vorbereitung"),
    [
        ("hinweis", 'git commit -m "beleg"', "stage"),
        ("scharf", 'git commit -am "beleg"', "tracked_edit"),
        ("hinweis", 'git commit -m "beleg" -- docs/verify-hard/416/live.txt', "stage"),
    ],
)
def test_commit_hook_blockiert_schluessel_im_beleg_ordner(
    tmp_path: Path,
    modus: str,
    befehl: str,
    vorbereitung: str,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    beleg = repo / "docs" / "verify-hard" / "416" / "live.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    if vorbereitung == "tracked_edit":
        beleg.write_text("sauber\n", encoding="utf-8")
        _git(repo, "add", "docs/verify-hard/416/live.txt")
        _git(repo, "commit", "-q", "-m", "sauberer beleg")
        beleg.write_text(f"Authorization: {_LIVE_BEARER}\n", encoding="utf-8")
    else:
        beleg.write_text(f"Authorization: {_LIVE_BEARER}\n", encoding="utf-8")
        _git(repo, "add", "docs/verify-hard/416/live.txt")

    code, ausgabe = _rufe_commit_hook(tmp_path, repo, befehl, modus=modus)
    assert code == 0
    assert ausgabe is not None
    hook_out = ausgabe["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
    grund = hook_out["permissionDecisionReason"]
    assert "docs/verify-hard/416/live.txt" in grund
    assert "9f8e7d6c5b4a3210" not in grund


def test_commit_hook_laesst_sauberen_beleg_durch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    beleg = repo / "docs" / "verify-hard" / "863" / "gruen.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text("Alles grün, kein Schlüssel enthalten.\n", encoding="utf-8")
    _git(repo, "add", "docs/verify-hard/863/gruen.txt")

    code, ausgabe = _rufe_commit_hook(tmp_path, repo, 'git commit -m "beleg"', modus="hinweis")
    assert code == 0
    if ausgabe is not None:
        assert ausgabe.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_commit_hook_beachtet_konfigurierten_belege_ordner(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo", konfig={"regularien": {"belege_ordner": "belege/custom"}})
    beleg = repo / "belege" / "custom" / "secret.txt"
    beleg.parent.mkdir(parents=True, exist_ok=True)
    beleg.write_text(f"{_PRIV_KEY_KOPF}\n", encoding="utf-8")
    _git(repo, "add", "belege/custom/secret.txt")

    code, ausgabe = _rufe_commit_hook(tmp_path, repo, 'git commit -m "beleg"', modus="hinweis")
    assert code == 0
    assert ausgabe is not None
    assert ausgabe["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "belege/custom/secret.txt" in ausgabe["hookSpecificOutput"]["permissionDecisionReason"]


# --- 4. Gegenprobe über bestehende Beleg-Ordner ohne Fehlalarme ----------------


def test_gegenprobe_bestehende_beleg_ordner_ohne_fehlalarme() -> None:
    erg = sofort.pruefe_beleg_ordner(REPO_WURZEL)
    assert erg.dateien_geprueft > 0
    assert erg.ok, f"Unerwartete Fehlalarme in bestehenden Belegen:\n{erg.bericht()}"
    assert erg.funde == ()
