"""Weg-Tests für das tägliche Zweig-Prüf-Modul (#888, aus App #1208).

Prüft mit echtem Git gegen ein lokales Bare-Repo (ohne Git-Attrappe; nur ``gh``
läuft über ``TO_SPAWN_GH_STUB``):
1. Hängende rot/Konflikt-Branches > 2 Tage (Journal oder echter Merge-Konflikt)
2. Geschlossene Issues ohne ``(#N)``-Commit / Inhalts-Deckung auf dem Hauptzweig
   inkl. Inhalts-Probe (Anteil hinzugefügter Zeilen auf dem Hauptzweig)
3. Offene Issues mit altem Branch ohne neuen Push
4. Streng lesender Lauf (kein Zweig wird je gelöscht)
5. CLI-Unterbefehl ``zweige-pruefen`` und optionaler Hausmeister-Tageslauf
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from to_spawn import cli_befehle, cli_parser, hausmeister, prozessbaum, zweig_pruefung

TAG = 24 * 3600
JETZT = 1_750_000_000  # fester vergangener Zeitstempel für deterministische Weg-Tests


def _git(cwd: Path, *args: str, zeit: int | None = None) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test-Bau",
        "GIT_AUTHOR_EMAIL": "test-bau@example.invalid",
        "GIT_COMMITTER_NAME": "Test-Bau",
        "GIT_COMMITTER_EMAIL": "test-bau@example.invalid",
    }
    if zeit is not None:
        iso = f"{zeit} +0000"
        env["GIT_AUTHOR_DATE"] = iso
        env["GIT_COMMITTER_DATE"] = iso
    fertig = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        **prozessbaum.fensterlos(),
    )
    return fertig.stdout.strip()


def _commit(repo: Path, datei: str, inhalt: str, nachricht: str, *, zeit: int) -> str:
    ziel = repo / datei
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    _git(repo, "add", datei)
    _git(repo, "commit", "-q", "-m", nachricht, zeit=zeit)
    return _git(repo, "rev-parse", "HEAD")


def _remote_heads(bare: Path) -> dict[str, str]:
    aus = _git(bare, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads/")
    erg: dict[str, str] = {}
    for zeile in aus.splitlines():
        if zeile.strip():
            name, sha = zeile.split(" ", 1)
            erg[name] = sha
    return erg


class _TestRepo:
    """Lokales Bare-Repo + Arbeitsklon + konfigurierbare ``gh``-Stub-Datenbank."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.basis = tmp_path
        self.bare = tmp_path / "org" / "projekt.git"
        self.bare.parent.mkdir(parents=True, exist_ok=True)
        _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(self.bare))

        self.klon = tmp_path / "klon"
        _git(tmp_path, "clone", "-q", str(self.bare), str(self.klon))
        _git(self.klon, "checkout", "-q", "-B", "main")
        _commit(self.klon, "app.py", "def basis() -> int:\n    return 1\n", "chore: Basis", zeit=JETZT - 10 * TAG)
        _git(self.klon, "push", "-q", "-u", "origin", "main")
        _git(self.bare, "symbolic-ref", "HEAD", "refs/heads/main")

        self.issues_datei = tmp_path / "gh_issues.json"
        self.issues: dict[str, dict[str, Any]] = {}
        self._speichere_issues()

        self.stub = tmp_path / "gh_stub.py"
        self.stub.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"db = json.loads(Path({str(self.issues_datei)!r}).read_text(encoding='utf-8'))\n"
            "args = sys.argv[1:]\n"
            "if len(args) >= 3 and args[0] == 'issue' and args[1] == 'view':\n"
            "    nr = str(args[2])\n"
            "    if nr in db:\n"
            "        print(json.dumps(db[nr]))\n"
            "        sys.exit(0)\n"
            "    sys.exit(1)\n"
            "if len(args) >= 2 and args[0] == 'issue' and args[1] == 'list':\n"
            "    zustand = None\n"
            "    if '--state' in args:\n"
            "        zustand = args[args.index('--state') + 1].upper()\n"
            "    treffer = []\n"
            "    for eintrag in db.values():\n"
            "        if zustand in (None, 'ALL') or eintrag.get('state', '').upper() == zustand:\n"
            "            treffer.append(eintrag)\n"
            "    print(json.dumps(treffer))\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("TO_SPAWN_GH_STUB", str(self.stub))
        monkeypatch.setenv("TO_SPAWN_TEST", "1")

    def _speichere_issues(self) -> None:
        self.issues_datei.write_text(json.dumps(self.issues, ensure_ascii=False), encoding="utf-8")

    def issue(
        self,
        nr: int,
        state: str = "OPEN",
        *,
        state_reason: str = "",
        labels: list[str] | None = None,
        title: str | None = None,
    ) -> None:
        self.issues[str(nr)] = {
            "number": nr,
            "state": state,
            "stateReason": state_reason,
            "labels": [{"name": name} for name in (labels or [])],
            "title": title or f"Ticket {nr}",
        }
        self._speichere_issues()

    def push_zweig(
        self,
        zweig: str,
        datei: str,
        inhalt: str,
        nachricht: str,
        *,
        zeit: int,
        basis: str = "origin/main",
    ) -> str:
        _git(self.klon, "checkout", "-q", "-B", zweig, basis)
        sha = _commit(self.klon, datei, inhalt, nachricht, zeit=zeit)
        _git(self.klon, "push", "-q", "origin", f"HEAD:refs/heads/{zweig}")
        _git(self.klon, "checkout", "-q", "main")
        _git(self.klon, "branch", "-q", "-D", zweig)
        return sha

    def push_main(self, datei: str, inhalt: str, nachricht: str, *, zeit: int) -> str:
        _git(self.klon, "checkout", "-q", "main")
        sha = _commit(self.klon, datei, inhalt, nachricht, zeit=zeit)
        _git(self.klon, "push", "-q", "origin", "main")
        return sha


@pytest.fixture
def testrepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _TestRepo:
    return _TestRepo(tmp_path, monkeypatch)


def test_fall1_haengender_rot_zweig_aus_journal_und_konflikt_zweig(testrepo: _TestRepo) -> None:
    """Fall 1: rote (Journal) oder im Konflikt stehende Branches > 2 Tage werden gemeldet."""
    # Branch 101: > 3 Tage alt, im Schlangen-Journal als "rot" markiert
    testrepo.issue(101, "OPEN")
    testrepo.push_zweig("ticket/101-rot", "rot.py", "x = 1\n", "feat: rot (#101)", zeit=JETZT - 3 * TAG)

    # Branch 102: > 3 Tage alt, echter Merge-Konflikt mit main (ohne Journal-Eintrag)
    basis_sha = _git(testrepo.klon, "rev-parse", "origin/main")
    testrepo.issue(102, "OPEN")
    testrepo.push_zweig(
        "ticket/102-konflikt",
        "app.py",
        "def basis() -> int:\n    return 102\n",
        "feat: konflikt (#102)",
        zeit=JETZT - 3 * TAG,
        basis=basis_sha,
    )
    testrepo.push_main(
        "app.py",
        "def basis() -> int:\n    return 999\n",
        "feat: main aenderung (#999)",
        zeit=JETZT - 2 * TAG,
    )

    # Branch 103: nur 1 Tag alt, im Journal "rot" -> unter 2-Tage-Grenze, kein Befund
    testrepo.issue(103, "OPEN")
    testrepo.push_zweig("ticket/103-frisch", "frisch.py", "y = 1\n", "feat: frisch (#103)", zeit=JETZT - 1 * TAG)

    journal = testrepo.basis / "schlange.jsonl"
    journal.write_text(
        json.dumps({"zweig": "ticket/101-rot", "ticket": 101, "status": "rot"}) + "\n"
        + json.dumps({"zweig": "ticket/103-frisch", "ticket": 103, "status": "rot"}) + "\n",
        encoding="utf-8",
    )
    log_datei = testrepo.basis / "zweig_pruefung.jsonl"
    vorher = _remote_heads(testrepo.bare)

    ergebnis = zweig_pruefung.pruefe_repo(
        testrepo.klon,
        journal_pfad=journal,
        log_datei=log_datei,
        jetzt=JETZT,
        alter_tage=2.0,
    )

    assert not ergebnis.ok
    faelle = {(b.ticket, b.fall) for b in ergebnis.befunde}
    assert (101, "haengend_rot_konflikt") in faelle
    assert (102, "haengend_rot_konflikt") in faelle
    assert all(b.ticket != 103 for b in ergebnis.befunde)

    # Streng lesend: kein Zweig auf origin gelöscht oder verändert
    assert _remote_heads(testrepo.bare) == vorher

    # Ins JSONL-Log geschrieben
    log_zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert len(log_zeilen) == 2
    assert {z["ticket"] for z in log_zeilen} == {101, 102}


def test_fall2_geschlossenes_issue_ohne_code_und_inhalts_probe(testrepo: _TestRepo) -> None:
    """Fall 2: geschlossene Issues ohne (#N)-Commit/Inhalts-Deckung melden; gedeckte nicht."""
    # 201: geschlossen (COMPLETED), Branch hat Code, aber weder (#201)-Commit noch Inhalt auf main -> Fund!
    testrepo.issue(201, "CLOSED", state_reason="COMPLETED")
    testrepo.push_zweig(
        "ticket/201-fehlt",
        "neu_201.py",
        "def eins() -> int:\n    return 201\n\ndef zwei() -> int:\n    return 202\n",
        "feat: 201",
        zeit=JETZT - 1 * TAG,
    )

    # 202: geschlossen (COMPLETED), kein (#202)-Commit auf main, ABER Inhalt liegt auf main -> kein Fund!
    inhalt_202 = "def helfer_202() -> str:\n    wert = 'ok'\n    return wert\n"
    testrepo.issue(202, "CLOSED", state_reason="COMPLETED")
    testrepo.push_zweig("ticket/202-gedeckt", "modul_202.py", inhalt_202, "feat: lokal", zeit=JETZT - 1 * TAG)
    testrepo.push_main("modul_202.py", inhalt_202, "feat: sammel-übernahme ohne nummer", zeit=JETZT - 1 * TAG)

    # 203: geschlossen (COMPLETED), Commit mit (#203) auf main -> kein Fund!
    testrepo.issue(203, "CLOSED", state_reason="COMPLETED")
    testrepo.push_main("modul_203.py", "X = 203\n", "feat: fertig (#203)", zeit=JETZT - 1 * TAG)

    # 204: geschlossen als NOT_PLANNED (verworfen), kein Code -> kein Fund!
    testrepo.issue(204, "CLOSED", state_reason="NOT_PLANNED")

    # 205: geschlossen (COMPLETED), kein Branch und kein (#205)-Commit auf main -> Fund!
    testrepo.issue(205, "CLOSED", state_reason="COMPLETED")

    log_datei = testrepo.basis / "fall2.jsonl"
    ergebnis = zweig_pruefung.pruefe_repo(testrepo.klon, log_datei=log_datei, jetzt=JETZT)

    befunde_map = {b.ticket: b for b in ergebnis.befunde}
    assert 201 in befunde_map
    assert befunde_map[201].fall == "geschlossen_ohne_code"
    assert befunde_map[201].inhalts_deckung == 0.0
    assert 205 in befunde_map
    assert befunde_map[205].fall == "geschlossen_ohne_code"
    assert 202 not in befunde_map
    assert 203 not in befunde_map
    assert 204 not in befunde_map


def test_inhalts_probe_anteilsberechnung_gegen_bare_repo(testrepo: _TestRepo) -> None:
    """Inhalts-Probe berechnet den Anteil hinzugefügter Zeilen auf dem Hauptzweig korrekt."""
    zweig_inhalt = (
        "zeile_a = 1\n"
        "zeile_b = 2\n"
        "zeile_c = 3\n"
        "zeile_d = 4\n"
        "zeile_e = 5\n"
    )
    testrepo.push_zweig("ticket/250-teil", "probe.py", zweig_inhalt, "feat: probe", zeit=JETZT - 1 * TAG)
    # 2 von 5 Zeilen auf main -> 0.4
    testrepo.push_main("probe.py", "zeile_a = 1\nzeile_b = 2\n", "feat: teil", zeit=JETZT - 1 * TAG)
    _git(testrepo.klon, "fetch", "-q", "origin")

    probe_teil = zweig_pruefung.inhalts_probe(testrepo.klon, "origin/main", "origin/ticket/250-teil")
    assert probe_teil.hinzugefuegt == 5
    assert probe_teil.gefunden == 2
    assert probe_teil.anteil == pytest.approx(0.4)

    # 4 von 5 Zeilen auf main -> 0.8
    testrepo.push_main(
        "probe.py",
        "zeile_a = 1\nzeile_b = 2\nzeile_c = 3\nzeile_d = 4\n",
        "feat: fast voll",
        zeit=JETZT - 1 * TAG,
    )
    _git(testrepo.klon, "fetch", "-q", "origin")
    probe_hoch = zweig_pruefung.inhalts_probe(testrepo.klon, "origin/main", "origin/ticket/250-teil")
    assert probe_hoch.hinzugefuegt == 5
    assert probe_hoch.gefunden == 4
    assert probe_hoch.anteil == pytest.approx(0.8)


def test_fall3_offenes_issue_mit_altem_branch_ohne_neuen_push(testrepo: _TestRepo) -> None:
    """Fall 3: offene Issues mit Branch > 2 Tage ohne neuen Push melden; frische/staging:fertig nicht."""
    # 301: offen, Branch 4 Tage alt, sauber mergbar -> Fall 3
    testrepo.issue(301, "OPEN")
    testrepo.push_zweig("ticket/301-alt", "alt_301.py", "A = 1\n", "feat: alt (#301)", zeit=JETZT - 4 * TAG)

    # 302: offen, Branch 1 Tag alt -> kein Fund
    testrepo.issue(302, "OPEN")
    testrepo.push_zweig("ticket/302-neu", "neu_302.py", "B = 2\n", "feat: neu (#302)", zeit=JETZT - 1 * TAG)

    # 303: offen, aber Label staging:fertig (fertig gebaut) -> kein Fund
    testrepo.issue(303, "OPEN", labels=["staging:fertig"])
    testrepo.push_zweig("ticket/303-staging", "st_303.py", "C = 3\n", "feat: st (#303)", zeit=JETZT - 5 * TAG)

    ergebnis = zweig_pruefung.pruefe_repo(testrepo.klon, jetzt=JETZT, alter_tage=2.0, schreibe_log=False)

    assert [(b.ticket, b.fall) for b in ergebnis.befunde] == [(301, "offen_alter_zweig")]
    assert ergebnis.befunde[0].alter_tage == pytest.approx(4.0, abs=0.1)


def test_cli_unterbefehl_zweige_pruefen(testrepo: _TestRepo, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI ``zweige-pruefen`` gibt Exit 0 (sauber) bzw. 1 (Funde) und JSON aus."""
    ap = cli_parser.baue_parser()
    log_datei = testrepo.basis / "cli_log.jsonl"

    # Sauberer Stand -> Exit 0
    args_ok = ap.parse_args(["zweige-pruefen", "--repo", str(testrepo.klon), "--log", str(log_datei), "--json"])
    assert cli_befehle.verteilen(ap, args_ok) == 0
    daten_ok = json.loads(capsys.readouterr().out)
    assert daten_ok["ok"] is True
    assert daten_ok["befunde"] == []

    # Fund erzeugen -> Exit 1 + JSONL-Log
    testrepo.issue(401, "OPEN")
    testrepo.push_zweig("ticket/401-haengt", "h401.py", "H = 1\n", "feat: 401", zeit=JETZT - 5 * TAG)
    args_fund = ap.parse_args(
        [
            "zweige-pruefen",
            "--repo",
            str(testrepo.klon),
            "--log",
            str(log_datei),
            "--alter-tage",
            "2",
            "--json",
        ]
    )
    assert cli_befehle.verteilen(ap, args_fund) == 1
    daten_fund = json.loads(capsys.readouterr().out)
    assert daten_fund["ok"] is False
    assert len(daten_fund["befunde"]) == 1
    assert daten_fund["befunde"][0]["ticket"] == 401
    assert log_datei.is_file()


def test_hausmeister_tageslauf_optional_mit_schalter(
    testrepo: _TestRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hausmeister führt die Zweig-Prüfung nur mit Schalter und höchstens einmal täglich aus."""
    hm_log = testrepo.basis / "state" / "hausmeister.jsonl"
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(hm_log))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(testrepo.basis / "leer"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(testrepo.klon))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN", "0")

    testrepo.issue(501, "OPEN")
    testrepo.push_zweig("ticket/501-hm", "hm501.py", "X = 1\n", "feat: hm", zeit=JETZT - 4 * TAG)
    vorher = _remote_heads(testrepo.bare)

    # Ohne Schalter: kein Zweig-Prüfungs-Log
    monkeypatch.delenv("TO_SPAWN_HAUSMEISTER_ZWEIG_PRUEFUNG", raising=False)
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=float(JETZT))
    pruef_log = hm_log.with_name("zweig_pruefung.jsonl")
    assert not pruef_log.exists()

    # Mit Schalter: läuft einmal täglich, meldet #501, löscht nichts
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_PRUEFUNG", "1")
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=float(JETZT))
    assert pruef_log.is_file()
    zeilen_1 = [json.loads(z) for z in pruef_log.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert [z["ticket"] for z in zeilen_1] == [501]
    assert _remote_heads(testrepo.bare) == vorher

    # Zweiter Lauf am selben Tag: gedrosselt (kein doppelter Eintrag)
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=float(JETZT + 3600))
    zeilen_2 = [json.loads(z) for z in pruef_log.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert len(zeilen_2) == 1
