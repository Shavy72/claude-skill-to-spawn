"""Klasse-2-Antwort vom Handy mit fälschungssicherem Ed25519-Beweis (#312).

Prüft ohne echtes GitHub, ohne echte Konten und plattformgleich (Linux + Windows):
1. RFC-8032-Ed25519-Prüfung in reinem Python (inkl. Testvektoren, Malleability- und
   Kleinstordnungs-Schutz sowie OpenSSH-``ssh-ed25519``-Schlüsselformat).
2. Abnahme 1 (Angreifer-Suite): Eine Session mit allen Rechten des Besitzer-Kontos auf
   derselben Maschine — auch mit umgebauter Umgebung (gelöschtes ``TO_SPAWN_TICKET``,
   gefälschtes ``HOME``/``PATH``, Schein-Terminal, direkter JSON-Schreibzugriff, eigenes
   Schlüsselpaar, Replay, Wahl-/Frage-Manipulation, geänderte Arbeitsbaum-Konfig) — kann
   eine Klasse-2-Frage nicht freigeben.
3. Abnahme 2 (End-to-End): Nutzer signiert die Prüf-Anforderung mit dem nur am Handy
   liegenden privaten Schlüssel; nach Einreichen des Beweises gibt ``briefkasten.lesen`` /
   ``to_spawn.py antwort`` Exit 0 zurück und die wartende Bau-Session läuft weiter.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))


@pytest.fixture(autouse=True)
def _ohne_bau_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    try:
        from to_spawn import klasse2_beweis
    except ImportError:
        return
    monkeypatch.setattr(klasse2_beweis, "_SYSTEM_SCHLUESSEL_DATEI", tmp_path / "_kein_system_schluessel.json")


def _cli(repo: Path, *argv: str, env_zusatz: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env["TO_SPAWN_REPO"] = str(repo)
    if env_zusatz:
        env.update(env_zusatz)
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _schreibe_konfig(repo: Path, schluessel: list[str]) -> Path:
    ordner = repo / ".to-spawn"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / "config.json"
    datei.write_text(
        json.dumps({"klasse_2": {"oeffentliche_schluessel": schluessel}}, indent=2) + "\n",
        encoding="utf-8",
    )
    return datei


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _git_repo_mit_origin(tmp_path: Path, schluessel: list[str]) -> Path:
    """Erstellt ein lokales Repo samt lokalem Bare-Remote ``origin/main`` mit vertrautem Schlüssel."""
    bare = tmp_path / "origin.git"
    bare.mkdir(parents=True, exist_ok=True)
    _git(bare, "init", "--bare", "-q", "-b", "main")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _schreibe_konfig(repo, schluessel)
    _git(repo, "add", ".to-spawn/config.json")
    _git(repo, "commit", "-q", "-m", "chore: konfig")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def _openssh_ed25519_zeile(pub_bytes: bytes, kommentar: str = "handy") -> str:
    typ = b"ssh-ed25519"
    blob = struct.pack(">I", len(typ)) + typ + struct.pack(">I", len(pub_bytes)) + pub_bytes
    return f"ssh-ed25519 {base64.b64encode(blob).decode('ascii')} {kommentar}"


# --- 1. RFC-8032-Ed25519-Prüfung (reines Python, Linux + Windows) ----------------------------


# Offizielle RFC-8032-Testvektoren (Abschnitt 7.1, TEST 1, TEST 2, TEST 3)
RFC8032_VEKTOREN = [
    (
        bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"),
        bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"),
        b"",
        bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    ),
    (
        bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"),
        bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"),
        bytes.fromhex("72"),
        bytes.fromhex(
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    ),
    (
        bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7"),
        bytes.fromhex("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025"),
        bytes.fromhex("af82"),
        bytes.fromhex(
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    ),
]


@pytest.mark.parametrize(("seed", "pub", "msg", "erwartet_sig"), RFC8032_VEKTOREN)
def test_ed25519_rfc8032_vektoren(seed: bytes, pub: bytes, msg: bytes, erwartet_sig: bytes) -> None:
    from to_spawn import klasse2_beweis

    assert klasse2_beweis.ed25519_oeffentlich_aus_seed(seed) == pub
    assert klasse2_beweis.ed25519_signieren(seed, msg) == erwartet_sig
    assert klasse2_beweis.ed25519_pruefen(pub, msg, erwartet_sig) is True
    # Ein gekipptes Bit in Nachricht oder Signatur macht sie ungültig.
    assert klasse2_beweis.ed25519_pruefen(pub, msg + b"\x00", erwartet_sig) is False
    kaputt_sig = bytes([erwartet_sig[0] ^ 0x01]) + erwartet_sig[1:]
    assert klasse2_beweis.ed25519_pruefen(pub, msg, kaputt_sig) is False


def test_ed25519_lehnt_malleability_und_kleinstordnung_ab() -> None:
    from to_spawn import klasse2_beweis

    _seed, pub, msg, sig = RFC8032_VEKTOREN[1]
    assert klasse2_beweis.ed25519_pruefen(pub, msg, sig) is True
    # S + L (>= L) muss abgelehnt werden (RFC 8032 Malleability-Schutz).
    s_int = int.from_bytes(sig[32:], "little") + klasse2_beweis.ED25519_L
    malleable_sig = sig[:32] + s_int.to_bytes(32, "little")
    assert klasse2_beweis.ed25519_pruefen(pub, msg, malleable_sig) is False
    # Identitätspunkt / Kleinstordnungs-Punkt als öffentlicher Schlüssel wird abgelehnt.
    identitaet = b"\x01" + b"\x00" * 31
    assert klasse2_beweis.ed25519_pruefen(identitaet, msg, sig) is False
    # OpenSSH- und ed25519:-Format ergeben denselben 32-Byte-Schlüssel.
    k1 = klasse2_beweis.parse_oeffentlichen_schluessel(klasse2_beweis.formatiere_oeffentlichen_schluessel(pub))
    k2 = klasse2_beweis.parse_oeffentlichen_schluessel(_openssh_ed25519_zeile(pub))
    assert k1.roh == pub and k2.roh == pub and k1.kennung == k2.kennung


# --- 2. Abnahme 1: Session mit allen Rechten des Besitzer-Kontos kann Klasse 2 nicht freigeben ---


def test_abnahme_1_session_mit_besitzerrechten_und_umgebauter_umgebung_gesperrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme 1: Alle lokalen Umgehungswege scheitern, sobald ein Handy-Schlüssel eingerichtet ist."""
    from to_spawn import briefkasten, briefkasten_cli, ja_einrichten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    _ = handy_seed  # privater Schlüssel bleibt am Handy, die Maschine bekommt ihn nie
    _schreibe_konfig(tmp_path, [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)])

    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], ziel="live", art="deploy", hauptbaum=tmp_path)
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER

    # (1) Aufseher-Weg scheitert
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("7", fid, "ja", ["Ticket #7"], "selbst", hauptbaum=tmp_path)

    # (2) Direkter Python-Aufruf von nutzer_antworten / live_fragen_freigeben ohne Beweis scheitert
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("7", fid, "ja", [], "umgangen", hauptbaum=tmp_path)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.live_fragen_freigeben(["7"], 99, "1", hauptbaum=tmp_path)

    # (3) Schein-Terminal + CLI nutzer-antwort ohne Beweis scheitert
    monkeypatch.setenv("TO_SPAWN_LOG_RUECKFALL", str(tmp_path))
    monkeypatch.setattr(briefkasten_cli.konsole, "stdin_ist_terminal", lambda: True)
    args = argparse.Namespace(
        befehl="nutzer-antwort",
        ticket="7",
        frage_id=fid,
        wahl="ja",
        grund="schein-tty",
        beleg=[],
        beweis="",
    )
    assert briefkasten_cli.lauf(args) == 2

    # (4) Umgebaute Umgebung: eigenes HOME mit eingetragener settings.json-Sperre + CLI `ja` scheitert
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    ja_einrichten.sperre_eintragen(fake_home / ".claude", sys.executable)
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    lauf_ja = _cli(
        tmp_path,
        "ja",
        "7",
        env_zusatz={
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        },
    )
    assert lauf_ja.returncode == 2, lauf_ja.stdout + lauf_ja.stderr

    # (5) Direktes Schreiben in .to-spawn/entscheidung-7.json (ohne Beweis) bleibt NUTZER
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][-1]["antwort"] = {
        "ts": "2026-11-28T01:00:00+00:00",
        "wahl": "ja",
        "grund": "direkt",
        "von": "nutzer",
    }
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    assert [o["id"] for o in briefkasten.offene(["7"], hauptbaum=tmp_path)] == [fid]
    assert briefkasten.liste(["7"], hauptbaum=tmp_path) == []

    # (6) Fremdes selbst erzeugtes Schlüsselpaar der Session wird abgelehnt (schreiben + direktes JSON)
    angreifer_seed, _angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=tmp_path)
    gef_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="angreifer-schluessel",
    )
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", gef_token, hauptbaum=tmp_path)
    daten["fragen"][-1]["antwort"] = {
        "ts": "2026-11-28T01:00:00+00:00",
        "wahl": "ja",
        "grund": "angreifer-schluessel",
        "von": "nutzer",
        "beweis": gef_token,
    }
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER

    # (7) Herabstufen von klasse: 2 -> 3 in der JSON-Datei hilft bei Klasse-2-Text nicht
    daten["fragen"][-1]["klasse"] = 3
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_abnahme_1_replay_und_manipulation_abgelehnt(tmp_path: Path) -> None:
    """Ein echter Handy-Beweis gilt nur für genau diese Frage, Ticket, Challenge, Wahl und Grund."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    _schreibe_konfig(tmp_path, [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)])

    q1 = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    q2 = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    q_anderes_ticket = briefkasten.einwerfen("8", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    a1 = briefkasten.beweis_anfrage("7", q1, hauptbaum=tmp_path)
    token_nein = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="7",
        frage_id=q1,
        frage=a1["frage"],
        challenge=a1["challenge"],
        wahl="nein",
        grund="erst morgen",
    )

    # Replay auf anderes Ticket scheitert
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("8", token_nein, hauptbaum=tmp_path)
    assert briefkasten.lesen("8", q_anderes_ticket, hauptbaum=tmp_path) == briefkasten.NUTZER

    # Regulär einreichen auf q1 funktioniert, aber q2 bleibt offen (Ticket bleibt wegen q2 gesperrt)
    briefkasten.beweis_einreichen("7", token_nein, hauptbaum=tmp_path)
    assert [o["id"] for o in briefkasten.offene(["7"], hauptbaum=tmp_path)] == [q2]

    # Replay desselben Tokens direkt auf q2 im JSON scheitert
    datei = tmp_path / ".to-spawn" / "entscheidung-7.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["fragen"][1]["antwort"] = {
        "ts": "2026-11-28T01:05:00+00:00",
        "wahl": "nein",
        "grund": "erst morgen",
        "von": "nutzer",
        "beweis": token_nein,
    }
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", q2, hauptbaum=tmp_path) == briefkasten.NUTZER

    # Manipulation von wahl ("nein" -> "ja") oder Fragetext bei q1 macht den Beweis ungültig
    daten["fragen"][1]["antwort"] = None
    daten["fragen"][0]["antwort"]["wahl"] = "ja"
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", q1, hauptbaum=tmp_path) == briefkasten.NUTZER

    daten["fragen"][0]["antwort"]["wahl"] = "nein"
    daten["fragen"][0]["frage"] = "Live und Kosten sofort freigeben?"
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert briefkasten.lesen("7", q1, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_abnahme_1_arbeitsbaum_konfig_ueberschreibt_origin_main_nicht(tmp_path: Path) -> None:
    """Ändert die Session .to-spawn/config.json im Arbeitsbaum, gilt weiter origin/main."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    repo = _git_repo_mit_origin(tmp_path, [_openssh_ed25519_zeile(handy_pub)])

    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=repo)

    # Angreifer ersetzt im Arbeitsbaum die Schlüssel durch seinen eigenen oder löscht die Liste
    _schreibe_konfig(repo, [klasse2_beweis.formatiere_oeffentlichen_schluessel(angreifer_pub)])
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=repo)
    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="arbeitsbaum-trick",
    )
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", angreifer_token, hauptbaum=repo)
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER

    # Selbst wenn der Angreifer .to-spawn/config.json im Arbeitsbaum löscht, bleibt origin/main aktiv
    (repo / ".to-spawn" / "config.json").unlink()
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("7", fid, "ja", [], "konfig-geloescht", hauptbaum=repo)

    # Echter Handy-Schlüssel aus origin/main funktioniert weiterhin!
    echt_token = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="Handy ok",
    )
    briefkasten.beweis_einreichen("7", echt_token, hauptbaum=repo)
    antwort = briefkasten.lesen("7", fid, hauptbaum=repo)
    assert isinstance(antwort, briefkasten.Antwort) and antwort.wahl == "ja" and antwort.von == "nutzer"


def test_abnahme_1_system_schluesseldatei_hat_vorrang_und_kaputte_konfig_sperrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """System-Schlüsseldatei sticht Repo-Konfig; kaputte Schlüssel-Konfig sperrt fail-closed."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    sys_datei = tmp_path / "etc_to_spawn" / "klasse2_schluessel.json"
    sys_datei.parent.mkdir()
    sys_datei.write_text(
        json.dumps({"oeffentliche_schluessel": [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(klasse2_beweis, "_SYSTEM_SCHLUESSEL_DATEI", sys_datei)

    repo = tmp_path / "repo"
    repo.mkdir()
    # Angreifer legt eine lokale Repo-Konfig mit eigenem Schlüssel an
    _schreibe_konfig(repo, [klasse2_beweis.formatiere_oeffentlichen_schluessel(angreifer_pub)])
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=repo)
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=repo)

    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="repo-trick",
    )
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", angreifer_token, hauptbaum=repo)

    # Kaputte System-Schlüsseldatei sperrt fail-closed
    sys_datei.write_text("{kaputt", encoding="utf-8")
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("7", fid, "ja", [], "kaputte-konfig", hauptbaum=repo)

    # Wieder gültig → echter Handy-Beweis schließt die Frage
    sys_datei.write_text(klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub) + "\n", encoding="utf-8")
    echt_token = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="Handy frei",
    )
    briefkasten.beweis_einreichen("7", echt_token, hauptbaum=repo)
    assert briefkasten.lesen("7", fid, hauptbaum=repo).wahl == "ja"


def test_beweis_antwort_ohne_eingerichteten_schluessel_fail_closed(tmp_path: Path) -> None:
    """Ohne eingerichteten öffentlichen Schlüssel verweigert ``beweis-antwort`` immer."""
    from to_spawn import briefkasten, klasse2_beweis

    seed, _pub = klasse2_beweis.schluesselpaar_erzeugen()
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], ziel="live", art="deploy", hauptbaum=tmp_path)
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=tmp_path)
    token = klasse2_beweis.beweis_erzeugen(
        seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="ohne-schluessel",
    )
    lauf = _cli(tmp_path, "beweis-antwort", "7", "--beweis", token)
    assert lauf.returncode == 2
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    # Auch Leerraum bei ``nutzer-antwort --beweis "   "`` darf ohne Terminal/Sperre nicht durchrutschen.
    leer_home = tmp_path / "leer_home"
    leer_home.mkdir()
    lauf_leer = _cli(
        tmp_path,
        "nutzer-antwort",
        "7",
        "--id",
        fid,
        "--wahl",
        "ja",
        "--grund",
        "whitespace-trick",
        "--beweis",
        "   ",
        env_zusatz={"HOME": str(leer_home), "USERPROFILE": str(leer_home)},
    )
    assert lauf_leer.returncode == 2
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_abnahme_1_verbogener_path_kann_origin_schluessel_nicht_tauschen(tmp_path: Path) -> None:
    """Ein gefälschtes ``git`` vorn im ``PATH`` kann die Schlüssel auf ``origin/main`` nicht ersetzen."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    repo = _git_repo_mit_origin(tmp_path, [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)])
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=repo)
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=repo)

    # Gefälschtes ``git`` im PATH, das bei ``cat-file`` den Angreifer-Schlüssel ausgibt
    fake_bin = tmp_path / "evil_bin"
    fake_bin.mkdir()
    angreifer_json = json.dumps(
        {"klasse_2": {"oeffentliche_schluessel": [klasse2_beweis.formatiere_oeffentlichen_schluessel(angreifer_pub)]}}
    )
    if sys.platform == "win32":
        fake_git = fake_bin / "git.cmd"
        fake_git.write_text(f"@echo off\necho {angreifer_json}\n", encoding="utf-8")
    else:
        fake_git = fake_bin / "git"
        fake_git.write_text(f"#!/bin/sh\nprintf '%s\\n' '{angreifer_json}'\n", encoding="utf-8")
        fake_git.chmod(0o755)

    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="path-spoof",
    )
    evil_env = {"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")}
    lauf = _cli(repo, "beweis-antwort", "7", "--beweis", angreifer_token, env_zusatz=evil_env)
    assert lauf.returncode == 2
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER

    # Echter Handy-Beweis klappt auch mit verbogenem PATH
    echt_token = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="echtes-handy",
    )
    ok = _cli(repo, "beweis-antwort", "7", "--beweis", echt_token, env_zusatz=evil_env)
    assert ok.returncode == 0, ok.stderr
    assert briefkasten.lesen("7", fid, hauptbaum=repo).wahl == "ja"


# --- 3. Abnahme 2: Nutzer antwortet vom Handy, wartende Bau-Session läuft weiter -------------


def test_abnahme_2_handy_antwort_laesst_bau_session_weiterlaufen(tmp_path: Path) -> None:
    """Abnahme 2 (End-to-End über die CLI): Frage → Handy-Signatur → beweis-antwort → Bau-Session Exit 0."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    _schreibe_konfig(tmp_path, [_openssh_ed25519_zeile(handy_pub)])

    # 1. Bau-Session wirft Klasse-2-Frage ein und wartet (Exit 2 = NUTZER)
    einwurf = _cli(
        tmp_path,
        "frage",
        "7",
        "--klasse",
        "2",
        "--frage",
        "Kostenpflichtigen Tarif buchen?",
        "--vorschlag",
        "nein",
        env_zusatz={"TO_SPAWN_TICKET": "7"},
    )
    assert einwurf.returncode == 0, einwurf.stderr
    fid = einwurf.stdout.strip().rsplit(" ", 1)[-1]
    assert _cli(tmp_path, "antwort", "7", "--id", fid, env_zusatz={"TO_SPAWN_TICKET": "7"}).returncode == 2

    # 2. Aufseher holt die Prüf-Anforderung (für Ticket-Meldung ans Handy)
    anfrage_lauf = _cli(tmp_path, "beweis-anfrage", "7", "--id", fid, "--json")
    assert anfrage_lauf.returncode == 0, anfrage_lauf.stderr
    anfrage = json.loads(anfrage_lauf.stdout)
    assert anfrage["ticket"] == "7" and anfrage["id"] == fid and len(anfrage["challenge"]) == 32

    # 3. Nutzer signiert am Handy (privater Schlüssel verlässt das Handy nie)
    token = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket=anfrage["ticket"],
        frage_id=anfrage["id"],
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="Vom Handy freigegeben",
    )
    # Token lässt sich eindeutig aus einer Handy-Nachricht extrahieren
    nachricht = f"Passt, hier die Freigabe vom Handy:\n{token}\nDanke!"
    assert klasse2_beweis.extrahiere_token(nachricht) == token

    # 4. Aufseher / CLI reicht den fälschungssicheren Beweis ohne Terminal ein (Exit 0)
    einreichen = _cli(tmp_path, "beweis-antwort", "7", "--beweis", token, "--beleg", "Handy-Signatur")
    assert einreichen.returncode == 0, einreichen.stderr

    # 5. Wartende Bau-Session fragt erneut nach → Exit 0 mit Wahl und Grund des Nutzers!
    weiter = _cli(tmp_path, "antwort", "7", "--id", fid, env_zusatz={"TO_SPAWN_TICKET": "7"})
    assert weiter.returncode == 0, weiter.stdout + weiter.stderr
    assert "Antwort von nutzer: ja" in weiter.stdout
    assert "Vom Handy freigegeben" in weiter.stdout

    # 6. Entscheidungsliste führt die Handy-Antwort mit Beleg; zweites Einreichen wird abgelehnt
    zeilen = briefkasten.liste(["7"], hauptbaum=tmp_path)
    assert len(zeilen) == 1 and zeilen[0]["wer"] == "nutzer" and "Handy-Signatur" in zeilen[0]["beleg"]
    assert _cli(tmp_path, "beweis-antwort", "7", "--beweis", token).returncode != 0


# --- 4. Doku und Befugnis-Texte --------------------------------------------------------------


def test_doku_und_befugnis_text_nennen_variantenvergleich_und_handy_weg() -> None:
    from to_spawn import befugnis

    doku = SKILL / "docs" / "KLASSE_2_HANDY_BEWEIS_312.md"
    assert doku.is_file(), f"Vergleichsdoku fehlt: {doku}"
    inhalt = doku.read_text(encoding="utf-8")
    for wort in ("Variante (a)", "Variante (b)", "Ed25519", "Einmal-Code", "Abnahme 1"):
        assert wort in inhalt, f"{wort!r} fehlt in {doku.name}"

    text = befugnis.aufseher_text("python X/to_spawn.py")
    assert "beweis-anfrage" in text and "beweis-antwort" in text
    assert "{" not in text and "}" not in text
    assert "uebertragen" not in text and "Antwort <id>:" not in text


# --- 5. Regressionstests aus der Sicherheitsprüfung (Fix-Runde 2, PR #956) -------------------


def _git_ausgabe(repo: Path, *args: str, stdin_text: str | None = None) -> str:
    erg = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=stdin_text,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return erg.stdout.strip()


def test_abnahme_1_git_env_und_replace_objects_hebeln_origin_nicht_aus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GIT_*``-Env-Variablen (z. B. ``GIT_DIR``) und ``git replace`` hebeln ``origin/main`` nicht aus."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    handy_str = klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)
    angreifer_str = klasse2_beweis.formatiere_oeffentlichen_schluessel(angreifer_pub)

    repo = _git_repo_mit_origin(tmp_path / "echt", [handy_str])
    angreifer_repo = _git_repo_mit_origin(tmp_path / "angreifer", [angreifer_str])
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=repo)
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=repo)
    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="git-dir-trick",
    )

    # (1) GIT_DIR + weitere GIT_*-Variablen zeigen auf das Angreifer-Repo
    monkeypatch.setenv("GIT_DIR", str(angreifer_repo / ".git"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(angreifer_repo / ".git"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(angreifer_repo / ".git" / "objects"))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(angreifer_repo / ".git" / "objects"))
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/evil-replace/")

    schluessel = klasse2_beweis.vertraute_schluessel(repo)
    assert [k.roh for k in schluessel] == [handy_pub]
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", angreifer_token, hauptbaum=repo)
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER

    # Auch über die CLI mit gesetztem GIT_DIR scheitert der Angreifer-Beweis
    cli_angriff = _cli(
        repo,
        "beweis-antwort",
        "7",
        "--beweis",
        angreifer_token,
        env_zusatz={"GIT_DIR": str(angreifer_repo / ".git")},
    )
    assert cli_angriff.returncode == 2
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER

    # (2) git replace auf den Konfig-Blob im echten Repo bleibt durch --no-replace-objects wirkungslos
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_COMMON_DIR", raising=False)
    monkeypatch.delenv("GIT_OBJECT_DIRECTORY", raising=False)
    monkeypatch.delenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", raising=False)
    monkeypatch.delenv("GIT_REPLACE_REF_BASE", raising=False)

    orig_blob = _git_ausgabe(repo, "rev-parse", "origin/main:.to-spawn/config.json")
    angreifer_json = json.dumps({"klasse_2": {"oeffentliche_schluessel": [angreifer_str]}}) + "\n"
    ersatz_blob = _git_ausgabe(repo, "hash-object", "-w", "--stdin", stdin_text=angreifer_json)
    _git(repo, "replace", orig_blob, ersatz_blob)

    assert [k.roh for k in klasse2_beweis.vertraute_schluessel(repo)] == [handy_pub]
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", angreifer_token, hauptbaum=repo)

    # Echter Handy-Beweis funktioniert trotz GIT_DIR und git replace weiterhin
    monkeypatch.setenv("GIT_DIR", str(angreifer_repo / ".git"))
    echt_token = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="Handy ok",
    )
    briefkasten.beweis_einreichen("7", echt_token, hauptbaum=repo)
    antwort = briefkasten.lesen("7", fid, hauptbaum=repo)
    assert isinstance(antwort, briefkasten.Antwort) and antwort.wahl == "ja" and antwort.von == "nutzer"


def test_abnahme_1_json_downgrade_auf_klasse_3_und_nutzer_ohne_beweis_abgelehnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bei aktiven Schlüsseln zählt ``von == 'nutzer'`` auch nach JSON-Downgrade auf ``klasse=3`` nicht ohne Beweis."""
    from to_spawn import briefkasten, klasse2_beweis

    handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, _angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    sys_datei = tmp_path / "sys" / "klasse2_schluessel.json"
    sys_datei.parent.mkdir()
    sys_datei.write_text(
        json.dumps({"oeffentliche_schluessel": [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(klasse2_beweis, "_SYSTEM_SCHLUESSEL_DATEI", sys_datei)

    repo = tmp_path / "repo"
    repo.mkdir()

    # (1) Direkter JSON-Downgrade einer Klasse-2-Frage auf klasse=3 + harmloser Text + von="nutzer" ohne Beweis
    fid_k2 = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], ziel="live", art="deploy", hauptbaum=repo)
    datei7 = repo / ".to-spawn" / "entscheidung-7.json"
    daten7 = json.loads(datei7.read_text(encoding="utf-8"))
    daten7["fragen"][-1]["klasse"] = 3
    daten7["fragen"][-1]["frage"] = "Welche Farbe für den Knopf?"
    daten7["fragen"][-1]["vorschlag"] = "blau"
    daten7["fragen"][-1]["ziel"] = ""
    daten7["fragen"][-1]["art"] = ""
    daten7["fragen"][-1]["antwort"] = {
        "ts": "2026-11-28T01:30:00+00:00",
        "wahl": "ja",
        "grund": "json-downgrade",
        "von": "nutzer",
    }
    datei7.write_text(json.dumps(daten7), encoding="utf-8")

    assert briefkasten.lesen("7", fid_k2, hauptbaum=repo) == briefkasten.WARTET
    assert [o["id"] for o in briefkasten.offene(["7"], hauptbaum=repo)] == [fid_k2]
    assert briefkasten.liste(["7"], hauptbaum=repo) == []

    # Auch mit fremd signiertem Token bleibt eine herabgestufte Nutzer-Antwort ungültig
    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid_k2,
        frage=daten7["fragen"][-1]["frage"],
        challenge=daten7["fragen"][-1]["challenge"],
        wahl="ja",
        grund="json-downgrade",
    )
    daten7["fragen"][-1]["antwort"]["beweis"] = angreifer_token
    datei7.write_text(json.dumps(daten7), encoding="utf-8")
    assert briefkasten.lesen("7", fid_k2, hauptbaum=repo) == briefkasten.WARTET
    assert briefkasten.liste(["7"], hauptbaum=repo) == []

    # (2) Symmetrisch in _schreibe_antwort / nutzer_antworten: auch Klasse-3-Frage verlangt Handy-Beweis
    fid_k3 = briefkasten.einwerfen("8", "Welche Farbe für den Knopf?", "blau", 3, [], hauptbaum=repo)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.nutzer_antworten("8", fid_k3, "grün", [], "ohne beweis", hauptbaum=repo)
    assert briefkasten.lesen("8", fid_k3, hauptbaum=repo) == briefkasten.WARTET

    # Mit echtem Handy-Beweis darf der Nutzer auch Klasse-3 beantworten
    anfrage8 = briefkasten.beweis_anfrage("8", fid_k3, hauptbaum=repo)
    echt_token8 = klasse2_beweis.beweis_erzeugen(
        handy_seed,
        ticket="8",
        frage_id=fid_k3,
        frage=anfrage8["frage"],
        challenge=anfrage8["challenge"],
        wahl="grün",
        grund="Handy ok",
    )
    briefkasten.beweis_einreichen("8", echt_token8, hauptbaum=repo)
    antwort8 = briefkasten.lesen("8", fid_k3, hauptbaum=repo)
    assert isinstance(antwort8, briefkasten.Antwort) and antwort8.wahl == "grün" and antwort8.von == "nutzer"

    # (3) Aufseher darf normale Klasse-3-Fragen weiter ohne Handy-Beweis beantworten
    fid_aufseher = briefkasten.einwerfen("9", "Welcher Abstand?", "8px", 3, [], hauptbaum=repo)
    briefkasten.antworten("9", fid_aufseher, "8px", ["Ticket #9"], "passt", hauptbaum=repo)
    antwort9 = briefkasten.lesen("9", fid_aufseher, hauptbaum=repo)
    assert isinstance(antwort9, briefkasten.Antwort) and antwort9.von == "aufseher"


def test_vertraute_schluessel_ohne_konfig_auf_origin_unter_deutscher_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deutsche Locale darf bei Repos ohne ``.to-spawn/config.json`` auf ``origin/main`` keinen Fehlalarm auslösen."""
    from to_spawn import briefkasten, klasse2_beweis

    bare = tmp_path / "origin.git"
    bare.mkdir(parents=True, exist_ok=True)
    _git(bare, "init", "--bare", "-q", "-b", "main")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("hallo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "chore: init")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")

    monkeypatch.setenv("LANGUAGE", "de")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")

    beobachtet: list[tuple[list[str], dict[str, str] | None]] = []
    echt_run = subprocess.run

    def spion(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        beobachtet.append((list(cmd), kwargs.get("env") if isinstance(kwargs.get("env"), dict) else None))
        return echt_run(cmd, *args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(klasse2_beweis.subprocess, "run", spion)

    assert klasse2_beweis.vertraute_schluessel(repo) == ()
    assert beobachtet, "_git_lauf muss subprocess.run aufgerufen haben"
    for cmd, env in beobachtet:
        assert "--no-replace-objects" in cmd
        assert env is not None
        assert env.get("LC_ALL") == "C"
        assert env.get("LANGUAGE") == "C"
        assert not any(k.upper().startswith("GIT_") for k in env)

    fid = briefkasten.einwerfen("10", "Welcher Titel?", "A", 3, [], hauptbaum=repo)
    assert briefkasten.lesen("10", fid, hauptbaum=repo) == briefkasten.WARTET


def test_fehlendes_git_im_git_repo_wirft_beweis_konfig_fehler_kein_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlt ``git`` in einem Repo mit ``.git``, sperrt ``vertraute_schluessel`` fail-closed statt Arbeitsbaum-Fallback."""
    from to_spawn import briefkasten, klasse2_beweis

    _handy_seed, handy_pub = klasse2_beweis.schluesselpaar_erzeugen()
    angreifer_seed, angreifer_pub = klasse2_beweis.schluesselpaar_erzeugen()
    repo = _git_repo_mit_origin(
        tmp_path / "repo_mit_origin", [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)]
    )
    fid = briefkasten.einwerfen("7", "Live schalten?", "nein", 2, [], hauptbaum=repo)
    anfrage = briefkasten.beweis_anfrage("7", fid, hauptbaum=repo)

    # Angreifer schreibt eigenen Schlüssel in den lokalen Arbeitsbaum
    _schreibe_konfig(repo, [klasse2_beweis.formatiere_oeffentlichen_schluessel(angreifer_pub)])
    angreifer_token = klasse2_beweis.beweis_erzeugen(
        angreifer_seed,
        ticket="7",
        frage_id=fid,
        frage=anfrage["frage"],
        challenge=anfrage["challenge"],
        wahl="ja",
        grund="kein-git-fallback",
    )

    monkeypatch.setattr(klasse2_beweis, "_sicheres_git", lambda: None)

    with pytest.raises(klasse2_beweis.BeweisKonfigFehler):
        klasse2_beweis._origin_hauptref(repo)
    with pytest.raises(klasse2_beweis.BeweisKonfigFehler):
        klasse2_beweis.vertraute_schluessel(repo)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.beweis_einreichen("7", angreifer_token, hauptbaum=repo)
    assert briefkasten.lesen("7", fid, hauptbaum=repo) == briefkasten.NUTZER

    # Gegenprobe: reines Verzeichnis ohne .git liest weiterhin lokal ohne git
    ohne_git = tmp_path / "ohne_git"
    ohne_git.mkdir()
    _schreibe_konfig(ohne_git, [klasse2_beweis.formatiere_oeffentlichen_schluessel(handy_pub)])
    assert [k.roh for k in klasse2_beweis.vertraute_schluessel(ohne_git)] == [handy_pub]
