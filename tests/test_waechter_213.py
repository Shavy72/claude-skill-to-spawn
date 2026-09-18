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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
        stempel = (datetime.now(timezone.utc) - alter).strftime(
            "%Y-%m-%dT%H:%M:%S+0000"
        )
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
    return [
        json.loads(z)
        for z in welt["mails"].read_text(encoding="utf-8").splitlines()
        if z
    ]


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
    assert len(kommentar) == 1 and kommentar[0].startswith(
        "Wächter: commit_ohne_nummer"
    )
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
    assert _kommentare(welt, "901")[0].startswith("Wächter: commit_ohne_nummer")


# --- Regel 2: Beweis fehlt -----------------------------------------------------


def test_beweis_fehlt_und_laengere_zahl_zaehlt_nicht(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"], "feat: Bauteil (#901)", {"docs/verify-hard/9010_fremd.md": "x\n"}
    )
    _capo(welt)
    assert _gh_zustand(welt)["issues"]["901"]["state"] == "open"
    assert any(k.startswith("Wächter: beweis_fehlt") for k in _kommentare(welt, "901"))


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
    assert kommentare[0].startswith("Wächter: test_ersetzt")
    assert "test_alt" in kommentare[0]


def test_geloeschte_testdatei_oeffnet_wieder(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"], "test: Datei", {"tests/test_b.py": "def test_b():\n    pass\n"}
    )
    _commit(
        welt["repo"], "feat: Aufräumen (#901)", {"tests/test_b.py": None, **_beleg()}
    )
    _capo(welt)
    assert any(k.startswith("Wächter: test_ersetzt") for k in _kommentare(welt, "901"))


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
    _commit(
        welt["repo"], "feat: Anfang (#902)", {"a.txt": "a\n"}, alter=timedelta(hours=5)
    )
    _gh_setzen(welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)))
    _capo(welt)
    _capo(welt)
    assert _gh_zustand(welt)["issues"]["902"]["state"] == "open"
    kommentare = _kommentare(welt, "902")
    assert len(kommentare) == 1 and kommentare[0].startswith(
        "Wächter: session_verwaist"
    )
    mails = _mails(welt)
    assert [m["art"] for m in mails] == ["session_tot"]
    assert "902" in mails[0]["betreff"]


def test_frische_worktree_aenderung_ist_kein_verwaist(welt: dict[str, Path]) -> None:
    _gh_setzen(welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)))
    wt = welt["wt"] / "wt-902"
    subprocess.run(
        ["git", "clone", "-q", str(welt["tmp"] / "fern.git"), str(wt)], check=True
    )
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
    _ausfuehrbar(binaer / "ssh", f"#!/bin/sh\necho {sha}\n")
    return {"PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}"}


def test_vps_hinter_origin_oeffnet_wieder(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    vorher = _git(welt["repo"], "rev-parse", "HEAD")
    _konfig(
        welt,
        vps={
            "ssh": "vps-test",
            "pfad": "/opt/x",
            "deploy_pfade": ["web/", "Dockerfile"],
        },
    )
    _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    for name, wert in _ssh_fake(welt, vorher).items():
        monkeypatch.setenv(name, wert)
    _capo(welt)
    assert any(
        k.startswith("Wächter: vps_ungleich_origin") for k in _kommentare(welt, "901")
    )


def test_vps_auf_stand_bleibt_zu(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _konfig(welt, vps={"ssh": "vps-test", "pfad": "/opt/x", "deploy_pfade": ["web/"]})
    kopf = _commit(
        welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()}
    )
    for name, wert in _ssh_fake(welt, kopf).items():
        monkeypatch.setenv(name, wert)
    _capo(welt)
    assert _kommentare(welt, "901") == []


def test_ohne_vps_konfig_keine_vps_regel(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Seite (#901)", {"web/seite.py": "x = 1\n", **_beleg()})
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)


# --- B: Bau-Log-Delta ------------------------------------------------------------


def _log_zeile(typ: str, **felder: object) -> str:
    zeile = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    }
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
        _log_zeile(
            "entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da"
        ),
        "docs: Log (#902)",
    )
    erste = _capo(welt).stdout
    assert re.search(
        r"#902 \d\d:\d\d entscheidung: Welche Tabelle · SQLite · schon da", erste
    ), erste
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
        _log_zeile(
            "deploy_phase", lauf="1", phase="ende", ergebnis="rot", grund="pytest rot"
        )
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
        _log_zeile(
            "entscheidung", frage="Welche Tabelle", wahl="SQLite", grund="schon da"
        ),
        "docs: Log (#902)",
    )
    ergebnis = _capo(welt, "--uebersicht")
    datei = welt["repo"] / "docs" / "agents" / f"entscheidungen_{SPEC}.md"
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert str(datei) in ergebnis.stdout
    text = datei.read_text(encoding="utf-8")
    assert "| Ticket | Zeit | Frage | Wahl | Grund |" in text
    assert re.search(
        r"\| #902 \| [^|]+ \| Welche Tabelle \| SQLite \| schon da \|", text
    )


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

    _konfig(
        welt, mail={"nur_kritisch": False, "befehl": welt["mail_befehl"], "ziel": ""}
    )
    assert melder.melden(repo, "info", "nur Info", "egal", "k4") is True

    monkeypatch.setenv("MAIL_FAKE_EXIT", "1")
    assert melder.melden(repo, "session_tot", "tot", "x", "k5") is False
    monkeypatch.setenv("MAIL_FAKE_EXIT", "0")
    assert (
        melder.melden(repo, "session_tot", "tot", "x", "k5") is True
    )  # Fehlschlag merkt nichts


def test_melder_ohne_befehl_warnt(
    welt: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
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
        "message": {
            "content": [{"type": "text", "text": "You've reached your Fable limit"}]
        },
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
        env={**os.environ, "TO_SPAWN_REPO": str(repo), **(env or {})},
        timeout=timeout,
        check=False,
    )


def test_wache_dry_run_zeigt_remote_control_und_ausweich(welt: dict[str, Path]) -> None:
    ergebnis = _wache(welt["repo"], "--dry-run")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert (
        "--remote-control" in ergebnis.stdout and f"Wächter #{SPEC}" in ergebnis.stdout
    )
    assert "--fallback-model claude-opus-5" in ergebnis.stdout
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
    heim = welt["tmp"] / "heim"
    heim.mkdir(exist_ok=True)
    return {
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(heim),
        "FAKE_PROTOKOLL": str(welt["tmp"] / "claude_aufrufe.jsonl"),
        "FAKE_LIMIT": json.dumps(LIMIT_ZEILE, ensure_ascii=False),
        "TO_SPAWN_AUFSICHT_TAKT": "0.2",
    }


def _aufrufe(welt: dict[str, Path]) -> list[list[str]]:
    datei = welt["tmp"] / "claude_aufrufe.jsonl"
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z]


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
    assert zweiter[zweiter.index("--model") + 1] == "claude-opus-5"
    assert "--remote-control" in zweiter
    from to_spawn import bau_log

    # Laufzeit-Zeilen landen seit dem #204-Fix in der Laufdatei — lese() vereint beide.
    zeilen = bau_log.lese(welt["repo"], SPEC)
    wechsel = [z for z in zeilen if z["typ"] == "waechter_modell"]
    assert len(wechsel) == 1
    assert (
        wechsel[0]["von"] == "claude-fable-5-1"
        and wechsel[0]["nach"] == "claude-opus-5"
    )
    assert [m["art"] for m in _mails(welt)] == ["waechter_ausweich"]


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
