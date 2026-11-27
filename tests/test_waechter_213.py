"""Tests für den Wächter-Ausbau (duoplus-management#213): capo, Melder, Ausweich-Modell.

Echt laufen: Git (bare ``origin`` + Klon im Temp-Ordner), die CLI ``skripte/capo.py``
und ``skripte/wache.py``, Bau-Log-Dateien, Zustandsdateien. Gestellt sind nur
externe Dienste: ``gh`` (``tests/hilfen/gh_stub_213.py``, hält Issue-Zustand in
einer Datei), ``ssh`` zum VPS, der Mail-Befehl (schreibt nur in eine Datei) und im
Ausweich-Test das ``claude``-Programm. Der echte GitHub-Weg steht in
``test_waechter_213_weg.py``.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import attrappe, heim_anlegen

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub_213.py"

sys.path.insert(0, str(SKILL))

SPEC = "900"

#: Echte Limit-Zeile aus einem Claude-Transkript (Beleg im Bauplan #213).
LIMIT_ZEILE = {
    "type": "assistant",
    "isApiErrorMessage": True,
    "error": "rate_limit",
    "message": {
        "content": [
            {
                "type": "text",
                "text": "You've hit your session limit · resets 7:40pm (Europe/Berlin)",
            }
        ]
    },
}


# --- Hilfen ------------------------------------------------------------------


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=60,
    )
    return fertig.stdout.strip()


def _ausfuehrbar(pfad: Path, text: str) -> Path:
    pfad.write_text(text, encoding="utf-8")
    pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return pfad


def _iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit(
    repo: Path,
    betreff: str,
    dateien: dict[str, str | None],
    alter: timedelta | None = None,
) -> str:
    """Dateien schreiben (``None`` = löschen), committen, nach origin/master schieben."""
    for name, inhalt in dateien.items():
        pfad = repo / name
        if inhalt is None:
            _git(repo, "rm", "-q", name)
            continue
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", name)
    env = {}
    if alter is not None:
        stempel = (datetime.now(timezone.utc) - alter).strftime("%Y-%m-%dT%H:%M:%S+0000")
        env = {"GIT_AUTHOR_DATE": stempel, "GIT_COMMITTER_DATE": stempel}
    _git(repo, "commit", "-q", "--allow-empty", "-m", betreff, env=env)
    _git(repo, "push", "-q", "origin", "HEAD:master")
    return _git(repo, "rev-parse", "HEAD")


def _gh_zustand(welt: dict[str, Path]) -> dict:
    return json.loads(welt["gh"].read_text(encoding="utf-8"))


def _gh_setzen(welt: dict[str, Path], nummer: str, **felder: object) -> None:
    daten = _gh_zustand(welt)
    daten["issues"].setdefault(nummer, {}).update(felder)
    welt["gh"].write_text(json.dumps(daten), encoding="utf-8")


def _mails(welt: dict[str, Path]) -> list[dict]:
    if not welt["mails"].is_file():
        return []
    return [json.loads(z) for z in welt["mails"].read_text(encoding="utf-8").splitlines() if z]


def _konfig(welt: dict[str, Path], **zusatz: object) -> None:
    daten = {
        "mail": {"ziel": "", "nur_kritisch": True, "befehl": welt["mail_befehl"]},
        **zusatz,
    }
    ordner = welt["repo"] / ".to-spawn"
    ordner.mkdir(exist_ok=True)
    (ordner / "config.json").write_text(json.dumps(daten), encoding="utf-8")


def _capo(welt: dict[str, Path], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "capo.py"),
            SPEC,
            "--gh-repo",
            "test/wegwerf",
            "--wt-basis",
            str(welt["wt"]),
            *args,
        ],
        cwd=str(welt["repo"]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TO_SPAWN_REPO": str(welt["repo"])},
        timeout=60,
        check=False,
    )


def _text(ergebnis: subprocess.CompletedProcess[str]) -> str:
    return ergebnis.stdout + ergebnis.stderr


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Repo mit Bare-Origin, gestelltem gh (901 zu, 902 offen), Mail-Befehl in Datei."""
    fern = tmp_path / "fern.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(fern)],
        check=True,
        capture_output=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(fern))
    _commit(repo, "chore: Anfang", {"README.md": "Wegwerf\n"})

    gh = tmp_path / "gh_zustand.json"
    gh.write_text(
        json.dumps(
            {
                "sub": {SPEC: [901, 902]},
                "issues": {
                    "901": {
                        "state": "closed",
                        "closed_at": _iso(),
                        "updated_at": _iso(),
                    },
                    "902": {"state": "open", "updated_at": _iso()},
                },
                "kommentare": {},
            }
        ),
        encoding="utf-8",
    )
    mails = tmp_path / "mails.jsonl"
    mail_fake = _ausfuehrbar(
        tmp_path / "mail_fake.py",
        "import os, sys\n"
        "daten = sys.stdin.read()\n"
        "open(sys.argv[1], 'a', encoding='utf-8').write(daten.strip() + '\\n')\n"
        "sys.exit(int(os.environ.get('MAIL_FAKE_EXIT', '0')))\n",
    )
    zustand = tmp_path / "waechter"
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("GH_STUB213_ZUSTAND", str(gh))
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(zustand))
    # tmux nie echt (#1406): echtes ``tmux new-window`` startete auf dem Bau-Server eine
    # Session. Ersatz wie Linux ohne Session: list-windows Exit 1, new-window nur Protokoll.
    monkeypatch.setenv(
        "TO_SPAWN_TMUX",
        json.dumps([sys.executable, str(GH_STUB.parent / "tmux_stub_284.py"), str(tmp_path / "tmux_aufrufe.jsonl")]),
    )
    monkeypatch.setenv("FAKE_TMUX_FENSTER", str(tmp_path / "keine_tmux_session.txt"))
    for name in ("MAIL_FAKE_EXIT", "TO_SPAWN_TICKET", "TO_SPAWN_LOG_REPO"):
        monkeypatch.delenv(name, raising=False)
    welt = {
        "repo": repo,
        "gh": gh,
        "mails": mails,
        "wt": wt,
        "zustand": zustand,
        "tmp": tmp_path,
    }
    welt["mail_befehl"] = [sys.executable, str(mail_fake), str(mails)]  # type: ignore[assignment]
    _konfig(welt)
    return welt


def _kommentare(welt: dict[str, Path], nummer: str) -> list[str]:
    return _gh_zustand(welt).get("kommentare", {}).get(nummer, [])


def _beleg(nummer: str = "901") -> dict[str, str]:
    return {f"docs/verify-hard/{nummer}_beleg.md": "Beleg\n"}


# --- Regel 1: Commit ohne Ticket-Nummer --------------------------------------


def test_commit_ohne_nummer_oeffnet_wieder(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Bauteil ohne Nummer", _beleg())
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open"
    kommentar = _kommentare(welt, "901")
    assert len(kommentar) == 1 and kommentar[0].startswith("Aufseher: commit_ohne_nummer")
    assert "commit_ohne_nummer" in ergebnis.stdout


def test_sauberes_ticket_bleibt_zu(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901) [skip ci]", _beleg())
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"
    assert _kommentare(welt, "901") == []


def test_nummer_mitten_im_betreff_zaehlt_nicht(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "docs(#901): Nachtrag", _beleg())
    _capo(welt)
    assert _kommentare(welt, "901")[0].startswith("Aufseher: commit_ohne_nummer")


# --- Regel 2: Beweis fehlt -----------------------------------------------------


def test_beweis_fehlt_und_laengere_zahl_zaehlt_nicht(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901)", {"docs/verify-hard/9010_fremd.md": "x\n"})
    _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open"
    assert any(k.startswith("Aufseher: beweis_fehlt") for k in _kommentare(welt, "901"))


def test_beleg_ordner_aus_konfig(welt: dict[str, Path]) -> None:
    _konfig(welt, regularien={"belege_ordner": "belege"})
    _commit(welt["repo"], "feat: Bauteil (#901)", {"belege/abnahme-901.md": "x\n"})
    _capo(welt)
    assert _kommentare(welt, "901") == []


# --- Regel 3: Test ersetzt -----------------------------------------------------


def test_test_ersetzt_oeffnet_wieder(welt: dict[str, Path]) -> None:
    alt = "def test_alt():\n    assert True\n"
    _commit(welt["repo"], "test: alter Test", {"tests/test_a.py": alt})
    neu = "def test_neu():\n    assert True\n"
    _commit(welt["repo"], "feat: Umbau (#901)", {"tests/test_a.py": neu, **_beleg()})
    _capo(welt)
    kommentare = _kommentare(welt, "901")
    assert len(kommentare) == 1
    assert kommentare[0].startswith("Aufseher: test_ersetzt")
    assert "test_alt" in kommentare[0]


def test_geloeschte_testdatei_oeffnet_wieder(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "test: Datei", {"tests/test_b.py": "def test_b():\n    pass\n"})
    _commit(welt["repo"], "feat: Aufräumen (#901)", {"tests/test_b.py": None, **_beleg()})
    _capo(welt)
    assert any(k.startswith("Aufseher: test_ersetzt") for k in _kommentare(welt, "901"))


def test_verschobener_test_ist_kein_ersatz(welt: dict[str, Path]) -> None:
    alt = "def test_alt():\n    assert True\n"
    _commit(welt["repo"], "test: alter Test", {"tests/test_a.py": alt})
    neu = "import os\n\n\ndef test_alt():\n    assert os\n"
    _commit(welt["repo"], "feat: Umbau (#901)", {"tests/test_a.py": neu, **_beleg()})
    _capo(welt)
    assert _kommentare(welt, "901") == []


def test_capo_regeln_direkt_js_testfall() -> None:
    from to_spawn import capo

    diff = '-  it("zeigt den Knopf", () => {\n+  it("zeigt den Knopf blau", () => {\n'
    assert capo.entfernte_tests(diff) == ["zeigt den Knopf"]
    diff_ok = '-  test("bleibt", () => {\n+  test("bleibt", async () => {\n'
    assert capo.entfernte_tests(diff_ok) == []


# --- Idempotenz + Probelauf ----------------------------------------------------


def test_je_schliess_ereignis_nur_einmal(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", _beleg())
    geschlossen = _iso()
    _gh_setzen(welt, "901", closed_at=geschlossen)
    _capo(welt)
    # Jemand schließt wieder mit demselben Zeitstempel (Zustand wie vorher) → kein zweites Mal.
    _gh_setzen(welt, "901", state="closed", closed_at=geschlossen)
    _capo(welt)
    assert len(_kommentare(welt, "901")) == 1
    # Neues Schließ-Ereignis → wird wieder geprüft.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(seconds=-5)))
    _capo(welt)
    assert len(_kommentare(welt, "901")) == 2


def test_dry_run_oeffnet_nichts_und_mailt_nichts(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _gh_setzen(welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)))
    ergebnis = _capo(welt, "--dry-run")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "commit_ohne_nummer" in ergebnis.stdout
    assert "session_verwaist" in ergebnis.stdout
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"
    assert _kommentare(welt, "901") == [] and _kommentare(welt, "902") == []
    assert _mails(welt) == []
    assert not welt["zustand"].exists() or not any(welt["zustand"].iterdir())


# --- Regel 4: Session verwaist -------------------------------------------------


def test_verwaiste_session_kommentar_und_mail_einmal_am_tag(
    welt: dict[str, Path],
) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901)", _beleg())
    _commit(welt["repo"], "feat: Anfang (#902)", {"a.txt": "a\n"}, alter=timedelta(hours=5))
    _gh_setzen(welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)))
    _capo(welt)
    _capo(welt)
    assert _gh_zustand(welt)["issues"]["902"]["state"] == "open"
    kommentare = _kommentare(welt, "902")
    assert len(kommentare) == 1 and kommentare[0].startswith("Aufseher: session_verwaist")
    mails = _mails(welt)
    assert [m["art"] for m in mails] == ["session_tot"]
    assert "902" in mails[0]["betreff"]


def test_frische_worktree_aenderung_ist_kein_verwaist(welt: dict[str, Path]) -> None:
    _gh_setzen(welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)))
    wt = welt["wt"] / "wt-902"
    subprocess.run(["git", "clone", "-q", str(welt["tmp"] / "fern.git"), str(wt)], check=True)
    (wt / "neu.txt").write_text("frisch\n", encoding="utf-8")
    _capo(welt)
    assert _kommentare(welt, "902") == []
    assert _mails(welt) == []


def test_ohne_assignee_kein_verwaist(welt: dict[str, Path]) -> None:
    _gh_setzen(welt, "902", updated_at=_iso(timedelta(hours=9)))
    _capo(welt)
    assert _kommentare(welt, "902") == []


# --- Regel 5: VPS ungleich origin ----------------------------------------------


def _ssh_fake(welt: dict[str, Path], sha: str) -> dict[str, str]:
    binaer = welt["tmp"] / "bin"
    binaer.mkdir(exist_ok=True)
    attrappe(binaer, "ssh", f"print({sha!r})\n")
    return {"PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}"}


def test_vps_hinter_origin_oeffnet_wieder(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    vorher = _git(welt["repo"], "rev-parse", "HEAD")
    _konfig(
        welt,
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/", "Dockerfile"],
            # nie das echte Staging-Log des Bau-Servers lesen
            "staging_log": str(welt["tmp"] / "kein_staging.jsonl"),
        },
    )
    _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    for name, wert in _ssh_fake(welt, vorher).items():
        monkeypatch.setenv(name, wert)
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_vps_auf_stand_bleibt_zu(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _konfig(welt, vps={"ssh": "vps-test", "pfad": "/opt/x", "deploy_pfade": ["web/"]})
    kopf = _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    for name, wert in _ssh_fake(welt, kopf).items():
        monkeypatch.setenv(name, wert)
    _capo(welt)
    assert _kommentare(welt, "901") == []


def _vps_staging(
    welt: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    staging_sha: str | None,
    zettel: bool = False,
) -> None:
    """Commit #901 fehlt live; Staging-Log (``None`` = Datei fehlt) + optional Zettel."""
    vorher = _git(welt["repo"], "rev-parse", "HEAD")
    log = welt["tmp"] / "deploys.jsonl"
    freigabe = welt["tmp"] / "live_freigabe"
    freigabe.mkdir()
    if zettel:
        (freigabe / "901").write_text("ok\n", encoding="utf-8")
    _konfig(
        welt,
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/"],
            "staging_log": str(log),
            "freigabe_ordner": str(freigabe),
        },
    )
    kopf = _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    if staging_sha is not None:
        zeilen = [
            {"sha": staging_sha or kopf, "ergebnis": "gruen"},
            {"sha": "0" * 40, "ergebnis": "rot"},
        ]
        log.write_text("\n".join(json.dumps(z) for z in zeilen) + "\n", encoding="utf-8")
    for name, wert in _ssh_fake(welt, vorher).items():
        monkeypatch.setenv(name, wert)


def test_vps_fehlt_aber_auf_staging_ohne_zettel_bleibt_zu(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _vps_staging(welt, monkeypatch, staging_sha="")  # "" = Ticket-Commit selbst
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"


def test_vps_fehlt_auf_staging_mit_zettel_oeffnet_wieder(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _vps_staging(welt, monkeypatch, staging_sha="", zettel=True)
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_vps_fehlt_staging_sha_noch_nicht_geholt_bleibt_zu(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#362 27.09.: Staging-Stand schon grün, Wächter-Repo hatte ihn noch nicht geholt."""
    _vps_staging(welt, monkeypatch, staging_sha="ab" * 20)  # lokal unbekannter Commit
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"


def test_vps_fehlt_ohne_staging_log_oeffnet_wieder(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _vps_staging(welt, monkeypatch, staging_sha=None)
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_staging_fertig_ohne_freigabe_fehlt_live_bleibt_zu_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#275: zu + ``staging:fertig`` + keine Live-Freigabe + Commit fehlt live → kein Wiederöffnen."""
    _vps_staging(welt, monkeypatch, staging_sha=None)
    _gh_setzen(welt, "901", labels=["staging:fertig"])
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"


def test_staging_fertig_mit_freigabe_fehlt_live_oeffnet_wieder_275(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#275: echter Verstoß — Live-Freigabe da, Commit fehlt live → weiter gemeldet."""
    _vps_staging(welt, monkeypatch, staging_sha=None, zettel=True)
    _gh_setzen(welt, "901", labels=["staging:fertig"])
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_ohne_vps_konfig_keine_vps_regel(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)


# --- B: Bau-Log-Delta ------------------------------------------------------------


def _log_zeile(typ: str, **felder: object) -> str:
    zeile = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    zeile.update({"typ": typ, "ticket": "902", **felder})
    return json.dumps(zeile, ensure_ascii=False) + "\n"


def _log_datei(welt: dict[str, Path]) -> Path:
    return welt["repo"] / "docs" / "agents" / "bau_log" / "902.jsonl"


def _log_anhaengen(welt: dict[str, Path], text: str, betreff: str) -> None:
    datei = _log_datei(welt)
    alt = datei.read_text(encoding="utf-8") if datei.is_file() else ""
    _commit(welt["repo"], betreff, {"docs/agents/bau_log/902.jsonl": alt + text})


def test_delta_zeigt_nur_neue_zeilen(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901)", _beleg())
    _log_anhaengen(
        welt,
        _log_zeile("entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da"),
        "docs: Log (#902)",
    )
    erste = _capo(welt).stdout
    assert re.search(r"#902 \d\d:\d\d entscheidung: Welche Tabelle · SQLite · schon da", erste), erste
    zweite = _capo(welt).stdout
    assert "Welche Tabelle" not in zweite
    _log_anhaengen(
        welt,
        _log_zeile("entscheidung", frage="Farbe", wahl="blau", grund="Doktrin"),
        "docs: Log 2 (#902)",
    )
    dritte = _capo(welt).stdout
    assert "Farbe · blau · Doktrin" in dritte and "Welche Tabelle" not in dritte


def test_gate_rot_und_blockiert_melden_einmal(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901)", _beleg())
    _log_anhaengen(
        welt,
        _log_zeile("deploy_phase", lauf="1", phase="ende", ergebnis="rot", grund="pytest rot")
        + _log_zeile("blockiert", grund="Handy aus"),
        "docs: Log (#902)",
    )
    ausgabe = _capo(welt).stdout
    _capo(welt)
    arten = sorted(m["art"] for m in _mails(welt))
    assert arten == ["gate_rot", "live_beweis_blockiert"], ausgabe
    assert any("Handy aus" in m["text"] for m in _mails(welt))


# --- C: Entscheidungs-Übersicht + Spec-Ende ------------------------------------


def test_uebersicht_schreibt_tabelle(welt: dict[str, Path]) -> None:
    _log_anhaengen(
        welt,
        _log_zeile("entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da"),
        "docs: Log (#902)",
    )
    ergebnis = _capo(welt, "--uebersicht")
    datei = welt["repo"] / "docs" / "agents" / f"entscheidungen_{SPEC}.md"
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert str(datei) in ergebnis.stdout
    text = datei.read_text(encoding="utf-8")
    # Spaltenformat seit #364: Ticket · Was · Warum · Quelle (eine Liste aus briefkasten.liste).
    assert "| Ticket | Was | Warum | Quelle |" in text
    assert re.search(r"\| #902 \| Welche Tabelle → SQLite \| schon da \| Bau-Log #902 [^|]+ \|", text)


@pytest.mark.xfail(strict=True, reason="überholt durch #588: keine capo-Kurzmail mehr (E16)")
def test_spec_fertig_meldet_und_schreibt_uebersicht(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    ergebnis = _capo(welt)
    _capo(welt)
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)
    assert [m["art"] for m in _mails(welt)] == ["spec_fertig"]
    assert (welt["repo"] / "docs" / "agents" / f"entscheidungen_{SPEC}.md").is_file()


# --- D: Melder -------------------------------------------------------------------


def test_melder_politik(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import melder

    repo = welt["repo"]
    assert melder.melden(repo, "gate_rot", "Gate rot #5", "pytest rot", "k1") is True
    assert melder.melden(repo, "gate_rot", "Gate rot #5", "pytest rot", "k1") is False
    assert melder.melden(repo, "info", "nur Info", "egal", "k2") is False
    assert melder.melden(repo, "spec_fertig", "Spec fertig", "alles zu", "k3") is True
    mails = _mails(welt)
    assert [m["art"] for m in mails] == ["gate_rot", "spec_fertig"]
    assert mails[0] == {
        "art": "gate_rot",
        "betreff": "Gate rot #5",
        "text": "pytest rot",
        "an": "",
    }

    _konfig(welt, mail={"nur_kritisch": False, "befehl": welt["mail_befehl"], "ziel": ""})
    assert melder.melden(repo, "info", "nur Info", "egal", "k4") is True

    monkeypatch.setenv("MAIL_FAKE_EXIT", "1")
    assert melder.melden(repo, "session_tot", "tot", "x", "k5") is False
    monkeypatch.setenv("MAIL_FAKE_EXIT", "0")
    assert melder.melden(repo, "session_tot", "tot", "x", "k5") is True  # Fehlschlag merkt nichts


def test_melder_ohne_befehl_warnt(welt: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    from to_spawn import melder

    _konfig(welt, mail={"befehl": [], "ziel": "", "nur_kritisch": True})
    with caplog.at_level("WARNING"):
        assert melder.melden(welt["repo"], "gate_rot", "x", "y", "k") is False
    assert "mail.befehl" in caplog.text


# --- F: Limit-Erkennung + wache.py ------------------------------------------------


def test_limit_erkennung_gegen_echte_zeile() -> None:
    from to_spawn import waechter_lauf

    assert waechter_lauf.ist_limit_zeile(LIMIT_ZEILE) is True
    fable = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {"content": [{"type": "text", "text": "You've reached your Fable limit"}]},
    }
    assert waechter_lauf.ist_limit_zeile(fable) is True
    normal = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Tick 3 ok"}]},
    }
    assert waechter_lauf.ist_limit_zeile(normal) is False
    zitat = {
        "type": "user",
        "message": {"content": "You've hit your session limit, sagt das Log"},
    }
    assert waechter_lauf.ist_limit_zeile(zitat) is False


def _wache(
    repo: Path, *args: str, env: dict[str, str] | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "wache.py"), SPEC, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Startklar-Prüfung (#450) ist hier nicht Prüfgegenstand (eigene Tests in
        # test_startklar_450.py) — sonst fehlt in der Testwelt aufraeumen.mjs.
        env={**os.environ, "TO_SPAWN_REPO": str(repo), "TO_SPAWN_STARTKLAR": "aus", **(env or {})},
        timeout=timeout,
        check=False,
    )


def test_wache_dry_run_zeigt_remote_control_und_ausweich(welt: dict[str, Path]) -> None:
    ergebnis = _wache(welt["repo"], "--dry-run")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "--remote-control" in ergebnis.stdout and f"Aufseher #{SPEC}" in ergebnis.stdout
    assert "--fallback-model claude-sonnet-5" in ergebnis.stdout
    prompt = _wache(welt["repo"], "--print-prompt").stdout
    assert f"scripts/capo.py {SPEC}" in prompt
    assert "spec_stand.py" not in prompt


def test_wache_ohne_remote_control_per_konfig(welt: dict[str, Path]) -> None:
    _konfig(welt, waechter={"remote_control": False})
    ergebnis = _wache(welt["repo"], "--dry-run")
    assert "--remote-control" not in ergebnis.stdout, _text(ergebnis)


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, re, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_PROTOKOLL"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\n")
if "--resume" in args:
    sys.exit(0)
sid = args[args.index("--session-id") + 1]
ordner = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
ordner.mkdir(parents=True, exist_ok=True)
datei = ordner / f"{sid}.jsonl"
with datei.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "message": {"content": "los"}}) + "\n")
    fh.flush()
    time.sleep(0.5)
    fh.write(os.environ["FAKE_LIMIT"] + "\n")
time.sleep(float(os.environ.get("FAKE_SCHLAF", "60")))
"""


def _wache_welt(welt: dict[str, Path]) -> dict[str, str]:
    binaer = welt["tmp"] / "claude_bin"
    binaer.mkdir(exist_ok=True)
    _ausfuehrbar(binaer / "claude", FAKE_CLAUDE)
    heim = heim_anlegen(welt["tmp"] / "heim")
    return {
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(heim),
        # Windows: ``Path.home()`` liest USERPROFILE, nicht HOME — sonst lesen wache.py und das
        # Fake-claude das echte Heim (smart-zone.json, Transkripte) statt der Testwelt (#68).
        "USERPROFILE": str(heim),
        "FAKE_PROTOKOLL": str(welt["tmp"] / "claude_aufrufe.jsonl"),
        "FAKE_LIMIT": json.dumps(LIMIT_ZEILE, ensure_ascii=False),
        "TO_SPAWN_AUFSICHT_TAKT": "0.2",
        # Start-Tür (#1406): unter Test startet nur, was in diesem Ordner liegt.
        "TO_SPAWN_TEST_BIN": str(binaer),
    }


def _aufrufe(welt: dict[str, Path]) -> list[list[str]]:
    datei = welt["tmp"] / "claude_aufrufe.jsonl"
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


def test_wache_loest_claude_als_attrappe_auf_1406(welt: dict[str, Path]) -> None:
    """#1406: Derselbe Weg wie ``test_wache_wechselt_bei_limit_auf_ausweich_modell`` muss die
    Attrappe ``claude`` (ohne Endung) finden. Vorher fand Windows' ``shutil.which`` die echte
    ``claude.EXE`` und der Test startete eine echte Aufseher-Session. Trockenlauf: startet nichts."""
    ergebnis = _wache(welt["repo"], "--dry-run", env=_wache_welt(welt))
    assert ergebnis.returncode == 0, _text(ergebnis)
    programm = ergebnis.stdout.strip().splitlines()[-1].split(" --model", 1)[0]
    assert Path(programm).parent == welt["tmp"] / "claude_bin", programm


def test_wache_wechselt_bei_limit_auf_ausweich_modell(welt: dict[str, Path]) -> None:
    beginn = time.monotonic()
    ergebnis = _wache(welt["repo"], env=_wache_welt(welt))
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert time.monotonic() - beginn < 40, "Wächter wurde nicht beendet"
    aufrufe = _aufrufe(welt)
    assert len(aufrufe) == 2, aufrufe
    erster, zweiter = aufrufe
    sid = erster[erster.index("--session-id") + 1]
    assert zweiter[zweiter.index("--resume") + 1] == sid
    assert zweiter[zweiter.index("--model") + 1] == "claude-sonnet-5"
    assert "--remote-control" in zweiter
    from to_spawn import bau_log

    # Laufzeit-Zeilen landen seit dem #204-Fix in der Laufdatei — lese() vereint beide.
    zeilen = bau_log.lese(welt["repo"], SPEC)
    wechsel = [z for z in zeilen if z["typ"] == "waechter_modell"]
    assert len(wechsel) == 1
    # „von“ = Startmodell des Wächters: SSOT ``modelle.waechter`` der Repo-Konfig, bei Opus
    # mit ``[1m]`` ergänzt (wache.volles_fenster, 2026-09-28) — kein fest verdrahtetes Literal.
    from to_spawn import config

    start_modell = erster[erster.index("--model") + 1]
    assert start_modell.startswith(str(config.lade(welt["repo"])["modelle"]["waechter"]))
    assert wechsel[0]["von"] == start_modell and wechsel[0]["nach"] == "claude-sonnet-5"
    assert [m["art"] for m in _mails(welt)] == ["waechter_ausweich"]


@pytest.mark.skip(
    reason="#254 hat dieses Verhalten abgelöst: nennt die Limit-Zeile eine Reset-Uhrzeit "
    "(LIMIT_ZEILE tut das: „resets 7:40pm“), pausiert der Wächter bis dahin und fährt "
    "selbst weiter. Nachfolger: test_waechter_254_weg.py::"
    "test_wache_pausiert_bis_zum_reset_und_faehrt_selbst_weiter. Der alte Weg — Mail "
    "session_tot, kein Neustart — gilt weiter ohne Uhrzeit und wird dort von "
    "test_wache_ohne_uhrzeit_meldet_session_tot_und_wartet_nicht geprüft. Kein "
    "xfail(strict), weil das Ergebnis sonst von der Tageszeit abhinge: liegt 19:40 mehr "
    "als MAX_WARTE_S weg, läuft der alte Weg und der Test wäre grün."
)
def test_wache_auf_ausweich_meldet_nur_session_tot(welt: dict[str, Path]) -> None:
    env = {**_wache_welt(welt), "FAKE_SCHLAF": "3"}
    ergebnis = _wache(welt["repo"], "--model", "claude-opus-5", env=env)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_aufrufe(welt)) == 1
    assert [m["art"] for m in _mails(welt)] == ["session_tot"]


def test_bau_log_kennt_neue_typen() -> None:
    from to_spawn import bau_log

    assert "blockiert" in bau_log.TYPEN and "waechter_modell" in bau_log.TYPEN


def test_eintrag_schreibt_blockiert_und_entscheidung(tmp_path: Path) -> None:
    """Sessions schreiben ``blockiert`` und Entscheidungen als Frage · Wahl · Grund per CLI."""
    ziel = tmp_path / "log_repo"
    ziel.mkdir()
    for args in (
        ["--typ", "blockiert", "--grund", "Handy aus"],
        [
            "--typ",
            "entscheidung",
            "--frage",
            "Welche Tabelle",
            "--wahl",
            "SQLite",
            "--grund",
            "schon da",
        ],
    ):
        fertig = subprocess.run(
            [
                sys.executable,
                str(SKILL / "to_spawn.py"),
                "eintrag",
                "--repo",
                str(ziel),
                "--ticket",
                "902",
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert fertig.returncode == 0, fertig.stdout + fertig.stderr
    datei = ziel / "docs" / "agents" / "bau_log" / "902.jsonl"
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    assert zeilen[0]["typ"] == "blockiert" and zeilen[0]["grund"] == "Handy aus"
    assert (zeilen[1]["frage"], zeilen[1]["wahl"], zeilen[1]["grund"]) == (
        "Welche Tabelle",
        "SQLite",
        "schon da",
    )


def test_eintrag_blockiert_mit_frage_wahl_grund(tmp_path: Path) -> None:
    """Start-Text-Befehl: ``eintrag --typ blockiert --frage … --wahl … --grund …`` (#278)."""
    ziel = tmp_path / "log_repo"
    ziel.mkdir()
    fertig = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "eintrag",
            "--repo",
            str(ziel),
            "--ticket",
            "903",
            "--typ",
            "blockiert",
            "--frage",
            "Darf ich löschen",
            "--wahl",
            "warte",
            "--grund",
            "Klasse 3",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert fertig.returncode == 0, fertig.stdout + fertig.stderr
    datei = ziel / "docs" / "agents" / "bau_log" / "903.jsonl"
    zeile = json.loads(datei.read_text(encoding="utf-8").splitlines()[0])
    assert (zeile["typ"], zeile["ticket"]) == ("blockiert", "903")
    assert (zeile["frage"], zeile["wahl"], zeile["grund"]) == ("Darf ich löschen", "warte", "Klasse 3")


def test_beleg_ordner_mit_nummer_zaehlt(welt: dict[str, Path]) -> None:
    """Echte Belegseiten liegen als Ordner ``docs/verify-hard/<datum>_<N>_<name>/`` (#203, #206)."""
    _commit(
        welt["repo"],
        "feat: Bauteil (#901)",
        {"docs/verify-hard/2026-09-18_901_bauteil/BEWEIS.md": "Beleg\n"},
    )
    _capo(welt)
    assert _kommentare(welt, "901") == []


def test_datum_im_belegpfad_ist_keine_nummer() -> None:
    from to_spawn import capo

    assert capo.beleg_passt("2026-09-18_203_staffel/BEWEIS.md", 203) is True
    assert capo.beleg_passt("2026-09-18_andere/BEWEIS.md", 18) is False
    assert capo.beleg_passt("abnahme_18.md", 18) is True


def test_staging_kopf_liest_per_ssh_wenn_lokal_fehlt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PC-Aufseher: Log nur auf dem Bau-Server → per vps.staging_ssh holen (#1332)."""
    from to_spawn import capo as capo_mod

    zeile = json.dumps({"sha": "abc1234", "ergebnis": "gruen"})
    aufrufe: list[list[str]] = []

    def falsch_ssh(befehl: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append(befehl)
        return subprocess.CompletedProcess(befehl, 0, stdout=zeile + "\n", stderr="")

    monkeypatch.setattr(capo_mod.subprocess, "run", falsch_ssh)
    vps = {"staging_log": str(tmp_path / "fehlt.jsonl"), "staging_ssh": "bau-server"}
    assert capo_mod.staging_kopf(vps) == "abc1234"
    assert aufrufe and Path(aufrufe[0][0]).stem.lower() == "ssh" and "bau-server" in aufrufe[0]
    assert capo_mod.staging_kopf({"staging_log": str(tmp_path / "fehlt.jsonl")}) is None


def _ssh_zaehler(
    monkeypatch: pytest.MonkeyPatch, antwort: Callable[[list[str]], subprocess.CompletedProcess[str]]
) -> list[list[str]]:
    """ssh-Aufrufe aus capo abfangen (externer Dienst); git läuft echt weiter."""
    from to_spawn import capo as capo_mod

    echt = capo_mod.subprocess.run
    aufrufe: list[list[str]] = []

    def run(befehl: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if Path(str(befehl[0])).stem.lower() != "ssh":
            return echt(befehl, **kw)  # type: ignore[call-overload]
        aufrufe.append(befehl)
        return antwort(befehl)

    monkeypatch.setattr(capo_mod.subprocess, "run", run)
    return aufrufe


def test_staging_kopf_einmal_je_takt_bei_mehreren_commits(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1407: drei Ticket-Commits, alle auf Staging → genau ein ssh je Takt, nicht drei."""
    from to_spawn import capo as capo_mod

    repo = welt["repo"]
    live = _git(repo, "rev-parse", "HEAD")
    for i in range(3):
        staging = _commit(repo, f"feat: Teil {i} (#901)", {f"web/teil{i}.py": f"x = {i}\n"})
    zeile = json.dumps({"sha": staging, "ergebnis": "gruen"})
    aufrufe = _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 0, stdout=zeile + "\n", stderr=""))
    eigene = capo_mod.ticket_commits(capo_mod.alle_commits(repo, "HEAD"), 901)
    assert len(eigene) == 3
    vps = {
        "ssh": "vps-test",
        "deploy_pfade": ["web/"],
        "staging_log": str(welt["tmp"] / "fehlt.jsonl"),
        "staging_ssh": "bau-server",
        "freigabe_ordner": str(welt["tmp"] / "keine_freigabe"),
    }
    assert capo_mod.regel_vps(repo, 901, eigene, vps, live, []) is None
    assert len(aufrufe) == 1, aufrufe


def test_staging_log_mit_tilde_wird_auf_server_aufgeloest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#1407: ``~/…`` muss der Server-Shell offen bleiben; der Rest bleibt gequotet."""
    from to_spawn import capo as capo_mod

    zeile = json.dumps({"sha": "abc1234", "ergebnis": "gruen"})
    aufrufe = _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 0, stdout=zeile + "\n", stderr=""))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    vps = {"staging_log": "~/staging/deploys; rm -rf x.jsonl", "staging_ssh": "bau-server"}
    assert capo_mod.staging_kopf(vps) == "abc1234"
    befehl = aufrufe[0][-1]
    assert befehl == "tail -n 200 ~/'staging/deploys; rm -rf x.jsonl'", befehl


def test_staging_abruf_fehlgeschlagen_nicht_wiederholt_im_takt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#1407: ein Takt, mehrere Tickets; ein gescheiterter Abruf wird nicht wiederholt."""
    from to_spawn import capo as capo_mod

    aufrufe = _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 255, stdout="", stderr="weg"))
    stand = capo_mod.StagingStand({"staging_log": str(tmp_path / "fehlt.jsonl"), "staging_ssh": "bau-server"})
    assert stand.kopf() is None
    assert stand.kopf() is None
    assert len(aufrufe) == 1


def test_staging_kopf_exit_ungleich_null_ist_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#1407: ssh-Exit ≠ 0 → kein Staging-Stand, Warnung im Log."""
    from to_spawn import capo as capo_mod

    _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 255, stdout="", stderr="weg"))
    vps = {"staging_log": str(tmp_path / "fehlt.jsonl"), "staging_ssh": "bau-server"}
    with caplog.at_level("WARNING"):
        assert capo_mod.staging_kopf(vps) is None
    assert "Exit 255" in caplog.text


def test_staging_kopf_zeitueberschreitung_ist_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#1407: ssh hängt bis zur Zeitgrenze → kein Staging-Stand, Warnung im Log, kein Absturz."""
    from to_spawn import capo as capo_mod

    def haengt(befehl: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(befehl, 30)

    _ssh_zaehler(monkeypatch, haengt)
    vps = {"staging_log": str(tmp_path / "fehlt.jsonl"), "staging_ssh": "bau-server"}
    with caplog.at_level("WARNING"):
        assert capo_mod.staging_kopf(vps) is None
    assert "nicht lesbar" in caplog.text


def test_staging_unlesbar_oeffnet_nicht_wieder(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#1407: ssh-Aussetzer beim Staging-Log zählt nie als „fehlt auf Staging“ → kein Verstoß, eine Log-Zeile."""
    from to_spawn import capo as capo_mod

    repo = welt["repo"]
    live = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat: Seite (#901)", {"web/seite.py": "x = 1\n"})
    aufrufe = _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 255, stdout="", stderr="weg"))
    eigene = capo_mod.ticket_commits(capo_mod.alle_commits(repo, "HEAD"), 901)
    vps = {
        "ssh": "vps-test",
        "deploy_pfade": ["web/"],
        "staging_log": str(welt["tmp"] / "fehlt.jsonl"),
        "staging_ssh": "bau-server",
        "freigabe_ordner": str(welt["tmp"] / "keine_freigabe"),
    }
    stand = capo_mod.StagingStand(vps)
    with caplog.at_level("WARNING"):
        assert capo_mod.regel_vps(repo, 901, eigene, vps, live, [], stand) is None
        assert capo_mod.regel_vps(repo, 901, eigene, vps, live, [], stand) is None
    assert len(aufrufe) == 1
    assert caplog.text.count("Staging-Prüfung in diesem Takt ausgesetzt") == 1, caplog.text


def _ssh_protokoll_fake(
    welt: dict[str, Path], vps_sha: str, staging_sha: str, staging_exit: int = 0
) -> tuple[dict[str, str], Path]:
    """ssh-Attrappe als Prozess (capo läuft als Unterprozess): VPS-HEAD bzw. Staging-Log, jeder Aufruf protokolliert.

    ``staging_exit`` ≠ 0: ``tail`` scheitert mit diesem Exit (ssh-Aussetzer zum Bau-Server).
    """
    binaer = welt["tmp"] / "bin"
    binaer.mkdir(exist_ok=True)
    protokoll = welt["tmp"] / "ssh_aufrufe.txt"
    # Direkt als Attrappe, ohne ``os.execv``-Umweg: unter Windows reicht execv Argumente
    # ungequotet weiter, der ssh-Befehl ``tail -n 200 …`` zerfiele in Einzelwörter.
    attrappe(
        binaer,
        "ssh",
        "import json, sys\n"
        f"open({str(protokoll)!r}, 'a', encoding='utf-8').write(' '.join(sys.argv[1:]) + chr(10))\n"
        "befehl = sys.argv[-1]\n"
        "if befehl.startswith('tail'):\n"
        f"    if {staging_exit}:\n"
        f"        sys.exit({staging_exit})\n"
        f"    print(json.dumps({{'sha': {staging_sha!r}, 'ergebnis': 'gruen'}}))\n"
        "else:\n"
        f"    print({vps_sha!r})\n",
    )
    return {"PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}"}, protokoll


def test_tick_teilt_staging_stand_ueber_mehrere_tickets(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """#1407: zwei geschlossene Nur-Staging-Tickets in einem Takt → Staging-Log genau einmal per ssh."""
    live = _git(welt["repo"], "rev-parse", "HEAD")
    daten = _gh_zustand(welt)
    daten["sub"][SPEC] = [901, 902, 903]
    daten["issues"]["903"] = dict(daten["issues"]["901"])
    welt["gh"].write_text(json.dumps(daten), encoding="utf-8")
    _konfig(
        welt,
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/"],
            "staging_log": str(welt["tmp"] / "fehlt.jsonl"),
            "staging_ssh": "bau-server",
            "freigabe_ordner": str(welt["tmp"] / "keine_freigabe"),
        },
    )
    _commit(welt["repo"], "feat: Seite (#901)", {"web/a.py": "x = 1\n", **_beleg("901")})
    staging = _commit(welt["repo"], "feat: Seite (#903)", {"web/b.py": "x = 2\n", **_beleg("903")})
    env, protokoll = _ssh_protokoll_fake(welt, live, staging)
    for name, wert in env.items():
        monkeypatch.setenv(name, wert)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    zeilen = protokoll.read_text(encoding="utf-8").splitlines()
    assert sum("tail -n 200" in z for z in zeilen) == 1, zeilen
    assert _kommentare(welt, "901") == [] and _kommentare(welt, "903") == [], _text(ergebnis)


def _staging_ssh_welt(welt: dict[str, Path]) -> tuple[str, str]:
    """Commits #901/#902 fehlen auf dem VPS, Staging nur per ssh lesbar; gibt (VPS-Kopf, Ticket-Kopf)."""
    live = _git(welt["repo"], "rev-parse", "HEAD")
    _konfig(
        welt,
        waechter={"karenz_minuten": 0},
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/"],
            "staging_log": str(welt["tmp"] / "fehlt.jsonl"),
            "staging_ssh": "bau-server",
            "freigabe_ordner": str(welt["tmp"] / "keine_freigabe"),
        },
    )
    _commit(welt["repo"], "feat: A (#901)", {"web/a.py": "x = 1\n", **_beleg("901")})
    kopf = _commit(welt["repo"], "feat: B (#902)", {"web/b.py": "x = 2\n", **_beleg("902")})
    return live, kopf


def _capo_zustand(welt: dict[str, Path]) -> dict:
    dateien = list(welt["zustand"].glob(f"*_{SPEC}.json"))
    return json.loads(dateien[0].read_text(encoding="utf-8")) if dateien else {}


def test_staging_unlesbar_haelt_spec_abschluss_zurueck(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """#1407: alle Tickets zu, Commit nicht live, Staging-Log per ssh Exit 255 → keine SPEC FERTIG, kein spec_fertig_seit."""
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    live, kopf = _staging_ssh_welt(welt)
    env, _ = _ssh_protokoll_fake(welt, live, kopf, staging_exit=255)
    for name, wert in env.items():
        monkeypatch.setenv(name, wert)
    ergebnis = _capo(welt)
    assert "SPEC FERTIG" not in ergebnis.stdout, _text(ergebnis)
    assert "Staging nicht lesbar" in ergebnis.stdout, _text(ergebnis)
    assert not _capo_zustand(welt).get("spec_fertig_seit"), _capo_zustand(welt)
    assert _kommentare(welt, "901") == [] and _kommentare(welt, "902") == [], _text(ergebnis)


def test_staging_dauerhaft_unlesbar_meldet_kopfzeile_bis_lesbar(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1407: ab STAGING_UNLESBAR_LAUT Takten in Folge eigene FEHLER-Kopfzeile; ein lesbarer Takt nimmt sie weg."""
    from to_spawn import capo as capo_mod

    _gh_setzen(welt, "902", state="open")  # Spec bleibt offen: kein Abschluss im lesbaren Takt
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    live, kopf = _staging_ssh_welt(welt)
    env, _ = _ssh_protokoll_fake(welt, live, kopf, staging_exit=255)
    for name, wert in env.items():
        monkeypatch.setenv(name, wert)
    laut = capo_mod.STAGING_UNLESBAR_LAUT
    ausgaben = [_capo(welt).stdout for _ in range(laut)]
    assert all("Staging seit" not in a for a in ausgaben[:-1]), ausgaben
    assert f"Staging seit {laut} Takten nicht lesbar" in ausgaben[-1], ausgaben[-1]
    _ssh_protokoll_fake(welt, live, kopf)  # Bau-Server wieder erreichbar
    ergebnis = _capo(welt)
    assert "Staging seit" not in ergebnis.stdout, _text(ergebnis)
    assert "Staging nicht lesbar" not in ergebnis.stdout, _text(ergebnis)
    assert not _capo_zustand(welt).get("staging_unlesbar_takte"), _capo_zustand(welt)


def test_staging_log_fehlt_auf_server_ist_lesbar_und_meldet_verstoss(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1407: Verbindung steht, ``tail`` endet mit Exit 1 („No such file“) → nicht unlesbar, Verstoß wie vor #1407."""
    from to_spawn import capo as capo_mod

    repo = welt["repo"]
    live = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feat: Seite (#901)", {"web/seite.py": "x = 1\n"})
    _ssh_zaehler(
        monkeypatch,
        lambda b: subprocess.CompletedProcess(
            b, 1, stdout="", stderr="tail: cannot open 'x': No such file or directory"
        ),
    )
    eigene = capo_mod.ticket_commits(capo_mod.alle_commits(repo, "HEAD"), 901)
    vps = {
        "ssh": "vps-test",
        "deploy_pfade": ["web/"],
        "staging_log": str(welt["tmp"] / "fehlt.jsonl"),
        "staging_ssh": "bau-server",
        "freigabe_ordner": str(welt["tmp"] / "keine_freigabe"),
    }
    stand = capo_mod.StagingStand(vps)
    verstoss = capo_mod.regel_vps(repo, 901, eigene, vps, live, [], stand)
    assert not stand.unlesbar
    assert verstoss is not None and verstoss.regel == "vps_ungleich_origin", verstoss


def test_staging_log_fehlt_auf_server_zaehlt_nicht_als_unlesbar(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1407: ``tail`` Exit 1 im echten Takt → keine „Staging nicht lesbar“-Zeile, Zähler bleibt 0."""
    _gh_setzen(welt, "902", state="open")
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    live, kopf = _staging_ssh_welt(welt)
    env, _ = _ssh_protokoll_fake(welt, live, kopf, staging_exit=1)
    for name, wert in env.items():
        monkeypatch.setenv(name, wert)
    ergebnis = _capo(welt)
    assert "Staging nicht lesbar" not in ergebnis.stdout, _text(ergebnis)
    assert not _capo_zustand(welt).get("staging_unlesbar_takte"), _capo_zustand(welt)


def test_vps_kopf_pfad_mit_tilde_bleibt_fuer_server_offen(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1407: ``vps.pfad`` mit ``~/`` darf nicht ganz gequotet werden, der Rest schon."""
    from to_spawn import capo as capo_mod

    sha = "a" * 40
    aufrufe = _ssh_zaehler(monkeypatch, lambda b: subprocess.CompletedProcess(b, 0, stdout=sha + "\n", stderr=""))
    assert capo_mod.vps_kopf({"ssh": "vps-test", "pfad": "~/app; rm -rf x"}) == sha
    assert aufrufe[0][-1] == "cd ~/'app; rm -rf x' && git rev-parse HEAD", aufrufe[0][-1]


# --- #1207: Deckung wie der Deploy-Dienst (scripts/schlange_deckung.py) --------

#: Echte SSOT aus dem Haupt-Repo (scripts/schlange_deckung.py + deploy_ziel.py + schlange_spur.py), keine
#: Nachbildung. Gelesen beim Import, weil conftest ``TO_SPAWN_*`` je Test löscht.
_HAUPT_REPO_TEST = os.environ.get("TO_SPAWN_HAUPT_REPO_TEST") or ""
_SCRIPTS = Path(_HAUPT_REPO_TEST).expanduser() / "scripts" if _HAUPT_REPO_TEST else None
#: Dateien, die capos Deckungs-Befehl im Repo braucht (relativ zu scripts/).
_DECKUNG_DATEIEN = ("schlange_deckung.py", "schlange_spur.py", "staging/deploy_ziel.py")


def test_1207_haupt_repo_variable_zeigt_auf_deploy_ziel() -> None:
    """Gesetzte Variable ohne Skript ist ein Fehler, kein stiller Skip der #1207-Tests."""
    if _SCRIPTS is None:
        pytest.skip("TO_SPAWN_HAUPT_REPO_TEST nicht gesetzt — #1207-Weg-Tests brauchen das Haupt-Repo")
    fehlt = [d for d in _DECKUNG_DATEIEN if not (_SCRIPTS / d).is_file()]
    assert not fehlt, f"TO_SPAWN_HAUPT_REPO_TEST gesetzt, aber unter {_SCRIPTS} fehlt: {fehlt}"


def _vps_deckung(
    welt: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    zettel: list[str],
    manifest: dict | None = None,
    davor: tuple[str, dict[str, str]] = (
        "feat: Fremd (#902)",
        {"web/fremd.py": "y = 1\n"},
    ),
) -> str:
    """#901 mit Zettel-Lage ``zettel``; im Stand davor Commit ``davor``, alles auf Staging, live alt.

    Zettel-Namen mit ``{davor}`` werden durch den SHA des Davor-Commits ersetzt; Rückgabe = dieser SHA.
    """
    if _SCRIPTS is None:
        pytest.skip("TO_SPAWN_HAUPT_REPO_TEST nicht gesetzt — #1207-Weg-Tests brauchen das Haupt-Repo")
    dateien = {f"scripts/{d}": (_SCRIPTS / d).read_text(encoding="utf-8") for d in _DECKUNG_DATEIEN}
    if manifest is not None:
        dateien["docs/agents/manifests/spec-900.json"] = json.dumps(manifest)
    vorher = _commit(welt["repo"], "chore: Deploy-Ziel (#1)", dateien)
    log = welt["tmp"] / "deploys.jsonl"
    freigabe = welt["tmp"] / "live_freigabe"
    freigabe.mkdir()
    _konfig(
        welt,
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/"],
            "staging_log": str(log),
            "freigabe_ordner": str(freigabe),
        },
    )
    zwischen = _commit(welt["repo"], *davor)
    for name in zettel:
        (freigabe / name.format(davor=zwischen)).write_text("ok\n", encoding="utf-8")
    kopf = _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    log.write_text(json.dumps({"sha": kopf, "ergebnis": "gruen"}) + "\n", encoding="utf-8")
    for name, wert in _ssh_fake(welt, vorher).items():
        monkeypatch.setenv(name, wert)
    return zwischen


def test_1207_ticket_zettel_fremdes_ticket_ohne_zettel_auf_staging_bleibt_zu(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1191: Zettel nur für das eigene Ticket, #902 im Stand ohne Zettel → Dienst bleibt auf Staging."""
    _vps_deckung(welt, monkeypatch, zettel=["901"])
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "closed"


def test_1207_alle_tickets_gedeckt_aber_nicht_live_oeffnet_wieder(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _vps_deckung(welt, monkeypatch, zettel=["901", "902"])
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_1207_spec_zettel_deckt_alle_tickets_oeffnet_wieder(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec-Zettel 900 deckt #901 und #902 über das Manifest (deploy_ziel.eltern_specs)."""
    _vps_deckung(welt, monkeypatch, zettel=["900"], manifest={"spec": "900", "tickets": {"901": {}, "902": {}}})
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_1207_docs_commit_ohne_nummer_live_gescheitert_meldet(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review-Fall: Doku-Commit ohne Nummer blockiert die Schlange nie → Stand geht live; liegt er
    nicht auf dem VPS, ist das Live-Deploy gescheitert und capo muss melden."""
    _vps_deckung(
        welt,
        monkeypatch,
        zettel=["901"],
        davor=("docs: Notiz", {"docs/notiz.md": "n\n"}),
    )
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def test_1207_sha_zettel_sieben_zeichen_deckt_code_commit_ohne_nummer(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHA-Zettel mit 7 Zeichen deckt den Code-Commit ohne Nummer wie in der Schlange → live erwartet."""
    _vps_deckung(
        welt,
        monkeypatch,
        zettel=["901", "{davor:.7}"],
        davor=("fix: schnell", {"web/fremd.py": "y = 1\n"}),
    )
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def _live_gedeckt_welt(tmp_path: Path, zettel: bool, skript: str | None) -> tuple[Path, dict]:
    repo = tmp_path / "repo"
    (repo / "scripts" / "staging").mkdir(parents=True)
    # Alter Haupt-Repo-Stand: deploy_ziel.py ist da, der Deckungs-Befehl (noch) nicht.
    (repo / "scripts" / "staging" / "deploy_ziel.py").write_text("print('{}')\n", encoding="utf-8")
    if skript is not None:
        (repo / "scripts" / "schlange_deckung.py").write_text(skript, encoding="utf-8")
    # #1504: capo liest Skript und Manifeste aus origin/master — Stand also pushen.
    fern = tmp_path / "fern.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "master", str(fern)],
        check=True,
        capture_output=True,
    )
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(fern))
    _git(repo, "add", "-A")
    _commit(repo, "chore: Stand (#1)", {})
    ordner = tmp_path / "zettel"
    ordner.mkdir()
    if zettel:
        (ordner / "901").write_text("ok\n", encoding="utf-8")
    return repo, {"freigabe_ordner": str(ordner)}


@pytest.mark.parametrize("zettel", [True, False])
def test_1207_rueckfall_skript_fehlt_nur_eigener_zettel(tmp_path: Path, zettel: bool) -> None:
    from to_spawn import capo as capo_mod

    repo, vps = _live_gedeckt_welt(tmp_path, zettel, None)
    assert capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is zettel


@pytest.mark.parametrize("zettel", [True, False])
def test_1207_rueckfall_alter_stand_exit_2_nur_eigener_zettel(tmp_path: Path, zettel: bool) -> None:
    """Deckungs-Befehl antwortet mit Exit 2 (falscher Aufruf, argparse): Antwort zählt nicht."""
    from to_spawn import capo as capo_mod

    alt = "import sys\nprint('{\"gedeckt\": true}')\nsys.exit(2)\n"
    repo, vps = _live_gedeckt_welt(tmp_path, zettel, alt)
    assert (
        capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is False
    )  # Fix-Runde 2 (M1): kein Rückfall auf den eigenen Zettel


@pytest.mark.parametrize("zettel", [True, False])
def test_1207_rueckfall_zeitlimit_nur_eigener_zettel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zettel: bool
) -> None:
    from to_spawn import capo as capo_mod

    repo, vps = _live_gedeckt_welt(tmp_path, zettel, "print('{\"gedeckt\": true}')\n")

    def haengt(befehl: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(befehl, 60)

    monkeypatch.setattr(capo_mod.subprocess, "run", haengt)
    assert (
        capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is False
    )  # Fix-Runde 2 (M1): kein Rückfall auf den eigenen Zettel


# --- #1207 Fix-Runde 2: Bereich bis zur master-Spitze (H1), Fehler → nicht gedeckt (M1) ---------


def test_1207_sha_zettel_auf_der_spitze_deckt_frueheren_ticket_commit(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H1: Zettel auf der master-Spitze (späterer Schritt) deckt #901 wie in der Schlange → live erwartet."""
    _vps_deckung(welt, monkeypatch, zettel=[], davor=("docs: Notiz", {"docs/notiz.md": "n\n"}))
    spitze = _commit(welt["repo"], "feat: Später (#903)", {"web/spaeter.py": "z = 1\n"})
    (welt["tmp"] / "live_freigabe" / spitze[:7]).write_text("ok\n", encoding="utf-8")
    _capo(welt)
    assert any(k.startswith("Aufseher: vps_ungleich_origin") for k in _kommentare(welt, "901"))


def _antwort_skript(code: int, ausgabe: str) -> str:
    return f"import sys\nprint({ausgabe!r})\nsys.exit({code})\n"


@pytest.mark.parametrize(
    "skript",
    [
        _antwort_skript(2, '{"gedeckt": true}'),  # Exit 2 (falscher Aufruf)
        _antwort_skript(3, '{"gedeckt": true}'),  # Exit ≠ 0/1
        _antwort_skript(0, "kaputt"),  # Nicht-JSON
        _antwort_skript(0, "[true]"),  # kein dict
        _antwort_skript(1, '{"gedeckt": true}'),  # Widerspruch Exit ↔ gedeckt
    ],
    ids=["exit2", "exit3", "kein_json", "kein_dict", "widerspruch"],
)
def test_1207_fehler_nicht_gedeckt_trotz_eigenem_zettel_mit_warnung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, skript: str
) -> None:
    """M1: Befehl da, Antwort unbrauchbar → nicht gedeckt + Warnzeile, nie Rückfall auf den eigenen Zettel."""
    from to_spawn import capo as capo_mod

    repo, vps = _live_gedeckt_welt(tmp_path, True, skript)
    with caplog.at_level("WARNING"):
        assert capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is False
    assert "nicht prüfbar" in caplog.text


def test_1207_zeitlimit_nicht_gedeckt_trotz_eigenem_zettel_mit_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from to_spawn import capo as capo_mod

    repo, vps = _live_gedeckt_welt(tmp_path, True, "print('{\"gedeckt\": true}')\n")

    def haengt(befehl: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(befehl, 60)

    monkeypatch.setattr(capo_mod.subprocess, "run", haengt)
    with caplog.at_level("WARNING"):
        assert capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is False
    assert "nicht prüfbar" in caplog.text


def test_1207_befehl_bekommt_bis_spitze(tmp_path: Path) -> None:
    """Bereichs-Wahl liegt im Befehl: capo übergibt nur ``--bis-spitze``, rechnet keinen Bereich."""
    from to_spawn import capo as capo_mod

    skript = (
        "import json, sys\n"
        "ok = '--bis-spitze' in sys.argv\n"
        "print(json.dumps({'gedeckt': ok, 'offen': []}))\n"
        "sys.exit(0 if ok else 1)\n"
    )
    repo, vps = _live_gedeckt_welt(tmp_path, False, skript)
    assert capo_mod.live_gedeckt(repo, vps, 901, "a" * 40, "b" * 40) is True
