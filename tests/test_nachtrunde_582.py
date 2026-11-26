"""#582: Nachtrunden-Vorlage (``workflow_call``), Commit-Vorabprüfung und Ein-Ticket „Nachtrunde rot“ (Linux + Windows).

Prüft plattformübergreifend ohne externe YAML-Abhängigkeit und ohne ``testmon``:
- ``to_spawn.pruef.nachtrunde`` erzeugt die wiederverwendbare ``workflow_call``-Vorlage
  (``pipeline-nachtrunde.yml``) mit dokumentierten Eingaben, ``concurrency``-Gruppe,
  Commit-Vorabprüfung, Stufe-2-Ausführung auf GitHub Actions (nie Bau-Server) und
  automatischem Ticket-Abgleich („Nachtrunde rot“).
- Anschluss-Vorlagen (Eigenanschluss & Übungs-Repo) nutzen ``on: schedule`` mit schiefer
  Minute (Standard ``17 2 * * *`` UTC) + ``workflow_dispatch`` + ``concurrency``.
- Kein Mail- oder Benachrichtigungs-Schritt in Vorlage oder Anschluss (auch per grep).
- Echter Weg-Test mit Git-Repo und zustandsbehaftetem ``TO_SPAWN_GH_STUB`` für alle vier
  Szenarien aus #582:
  (a) ohne neue Commits seit letzter grüner Nachtrunde → schneller Abbruch (< 60 s, grün);
  (b) roter Lauf → genau ein offenes Ticket „Nachtrunde rot“;
  (c) zweiter roter Lauf → Kommentar im selben Ticket, kein zweites Ticket;
  (d) grüner Lauf → Ticket automatisch geschlossen (und weiterer grüner Lauf bleibt ruhig).
- ``probe/anlegen.sh --mit-nachtrunde`` schließt das Übungs-Repo direkt an die Vorlage an.
- ``docs/PIPELINE_VORLAGE.md`` dokumentiert Eingaben, Ablauf und GitHub-Budget-Deckel (30 $).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum
from to_spawn.pruef import leiter, nachtrunde

ANLEGEN = SKILL / "probe" / "anlegen.sh"
DOKU = SKILL / "docs" / "PIPELINE_VORLAGE.md"
WORKFLOW_VORLAGE = SKILL / ".github" / "workflows" / "pipeline-nachtrunde.yml"
WORKFLOW_EIGEN = SKILL / ".github" / "workflows" / "pipeline-nachtrunde-eigen.yml"

#: Erlaubte kostenfreie Standard-GitHub-Runner (keine bezahlten/großen Runner, kein self-hosted).
ERLAUBTE_RUNNER = frozenset({"ubuntu-latest", "windows-latest", "${{ matrix.os }}"})

#: Muster für verbotene Mail-/Benachrichtigungs-Schritte in der Nachtrunden-Vorlage (grep-Prüfung).
BENACHRICHTIGUNG_MUSTER = re.compile(
    r"mail|smtp|slack|discord|teams|telegram|webhook|notify|notification|pushover|benachrichtig",
    re.IGNORECASE,
)


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    return erg.stdout.strip()


def _cli(
    modul: str,
    *args: str,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    umg = {**os.environ, "PYTHONPATH": str(SKILL)}
    if extra_env:
        umg.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", modul, *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


def _probe_git_repo(ordner: Path) -> tuple[Path, str]:
    """Legt ein frisches Git-Repo mit Hauptzweig ``main`` und erstem Commit an."""
    ordner.mkdir(parents=True, exist_ok=True)
    _git(ordner, "init", "-q", "-b", "main")
    _git(ordner, "remote", "add", "origin", "https://github.com/BeispielOrg/uebungs-repo.git")
    (ordner / "README.md").write_text("# Übungs-Repo\n", encoding="utf-8")
    _git(ordner, "add", "README.md")
    _git(ordner, "commit", "-q", "-m", "chore: start")
    return ordner, _git(ordner, "rev-parse", "HEAD")


def _schreibe_gh_stub(ordner: Path, zustand_pfad: Path) -> Path:
    """Erzeugt ein plattformübergreifendes Python-Stub-Skript für ``gh`` (Linux + Windows)."""
    stub = ordner / "gh_stub.py"
    stub.write_text(
        textwrap.dedent(f"""\
            import json
            import pathlib
            import sys

            ZUSTAND = pathlib.Path({str(zustand_pfad)!r})

            def lade():
                return json.loads(ZUSTAND.read_text(encoding="utf-8"))

            def speichere(daten):
                ZUSTAND.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")

            def flag(args, name, vorgabe=""):
                for idx, wert in enumerate(args):
                    if wert == name and idx + 1 < len(args):
                        return args[idx + 1]
                return vorgabe

            def main():
                args = sys.argv[1:]
                daten = lade()
                daten.setdefault("aufrufe", []).append(args)
                speichere(daten)

                if daten.get("fehler_bei") and args[:2] == daten["fehler_bei"]:
                    sys.stderr.write("simulierter gh-Fehler\\n")
                    return 1

                if args[:1] == ["api"]:
                    pfad = args[1] if len(args) > 1 else ""
                    if "/actions/runs/" in pfad and "/workflows/" not in pfad:
                        run_id = pfad.rsplit("/", 1)[-1].split("?", 1)[0]
                        lauf = daten.get("lauf_details", {{}}).get(run_id)
                        if lauf is None:
                            sys.stderr.write("run not found\\n")
                            return 1
                        print(json.dumps(lauf))
                        return 0
                    if "/actions/workflows/" in pfad and "/runs" in pfad:
                        print(json.dumps({{"workflow_runs": daten.get("workflow_runs", [])}}))
                        return 0
                    if "/actions/runs" in pfad:
                        print(json.dumps({{"workflow_runs": daten.get("workflow_runs", [])}}))
                        return 0
                    sys.stderr.write(f"unbekannter api-pfad: {{pfad}}\\n")
                    return 1

                if args[:2] == ["issue", "list"]:
                    zustand = flag(args, "--state", "open")
                    treffer = [
                        {{"number": i["number"], "title": i["title"], "url": i["url"]}}
                        for i in daten.get("issues", [])
                        if zustand == "all" or i.get("state", "open") == zustand
                    ]
                    print(json.dumps(treffer))
                    return 0

                if args[:2] == ["issue", "create"]:
                    titel = flag(args, "--title")
                    koerper = flag(args, "--body")
                    repo = flag(args, "--repo", "BeispielOrg/uebungs-repo")
                    naechste = max((i["number"] for i in daten.get("issues", [])), default=0) + 1
                    url = f"https://github.com/{{repo}}/issues/{{naechste}}"
                    daten.setdefault("issues", []).append({{
                        "number": naechste,
                        "title": titel,
                        "body": koerper,
                        "state": "open",
                        "url": url,
                        "comments": [],
                    }})
                    speichere(daten)
                    print(url)
                    return 0

                if args[:2] == ["issue", "comment"]:
                    nr = int(args[2])
                    koerper = flag(args, "--body")
                    for eintrag in daten.get("issues", []):
                        if eintrag["number"] == nr:
                            eintrag.setdefault("comments", []).append(koerper)
                            speichere(daten)
                            print(f"{{eintrag['url']}}#issuecomment-{{len(eintrag['comments'])}}")
                            return 0
                    sys.stderr.write(f"issue #{{nr}} fehlt\\n")
                    return 1

                if args[:2] == ["issue", "close"]:
                    nr = int(args[2])
                    kommentar = flag(args, "--comment")
                    for eintrag in daten.get("issues", []):
                        if eintrag["number"] == nr:
                            eintrag["state"] = "closed"
                            if kommentar:
                                eintrag.setdefault("comments", []).append(kommentar)
                            speichere(daten)
                            print(eintrag["url"])
                            return 0
                    sys.stderr.write(f"issue #{{nr}} fehlt\\n")
                    return 1

                sys.stderr.write(f"unbekannter gh-aufruf: {{args}}\\n")
                return 2

            raise SystemExit(main())
            """),
        encoding="utf-8",
    )
    return stub


def _freie_ports(anzahl: int) -> list[int]:
    with contextlib.ExitStack() as stapel:
        sockets = [stapel.enter_context(socket.socket()) for _ in range(anzahl)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


# --- Vorlage, Anschluss & Verträge ------------------------------------------------------------


def test_vorlage_nachtrunde_sauber_und_ohne_mail_oder_benachrichtigung() -> None:
    text = nachtrunde.vorlage_nachtrunde_yaml()
    assert nachtrunde.pruefe_nachtrunde_vorlage_text(text) == []

    # Grep-Kriterium aus #582: keine Mail-/Benachrichtigungs-Schritte oder -Verweise in der Vorlage.
    treffer = BENACHRICHTIGUNG_MUSTER.findall(text)
    assert treffer == [], f"Unerwartete Mail-/Benachrichtigungs-Spur in Vorlage: {treffer}"
    assert "self-hosted" not in text.lower()
    assert "contents: write" not in text
    assert "issues: write" in text
    assert "actions: read" in text
    assert "cancel-in-progress: false" in text
    assert "to_spawn.pruef.pipeline_stufe2" in text
    assert "to_spawn.pruef.nachtrunde vorab" in text
    assert "to_spawn.pruef.nachtrunde ticket" in text


def test_vorlage_pruefer_erkennt_verstoesse() -> None:
    sauber = nachtrunde.vorlage_nachtrunde_yaml()

    mit_mail = sauber + "\n      - uses: dawidd6/action-send-mail@v3\n"
    befunde_mail = nachtrunde.pruefe_nachtrunde_vorlage_text(mit_mail)
    assert any("Benachrichtig" in b or "Mail" in b for b in befunde_mail), befunde_mail

    ohne_concurrency = re.sub(r"(?ms)^concurrency:.*?(?=^jobs:)", "", sauber)
    befunde_conc = nachtrunde.pruefe_nachtrunde_vorlage_text(ohne_concurrency)
    assert any("concurrency" in b for b in befunde_conc), befunde_conc

    mit_self_hosted = sauber.replace("runs-on: ubuntu-latest", "runs-on: self-hosted", 1)
    befunde_runner = nachtrunde.pruefe_nachtrunde_vorlage_text(mit_self_hosted)
    assert any("self-hosted" in b or "Bau-Server" in b for b in befunde_runner), befunde_runner

    mit_contents_write = sauber.replace("contents: read", "contents: write")
    befunde_rechte = nachtrunde.pruefe_nachtrunde_vorlage_text(mit_contents_write)
    assert any("contents: write" in b for b in befunde_rechte), befunde_rechte


def test_anschluss_und_uebungsrepo_vorlagen_mit_schiefer_minute_und_concurrency() -> None:
    eigen = nachtrunde.eigenanschluss_nachtrunde_yaml()
    assert nachtrunde.pruefe_anschluss_nachtrunde_text(eigen) == []
    assert BENACHRICHTIGUNG_MUSTER.findall(eigen) == []
    assert f'cron: "{nachtrunde.STANDARD_CRON}"' in eigen
    assert "workflow_dispatch:" in eigen
    assert "cancel-in-progress: false" in eigen

    uebung_mit = nachtrunde.uebungsrepo_anschluss_yaml(mit_staging_app=True)
    assert nachtrunde.pruefe_anschluss_nachtrunde_text(uebung_mit) == []
    assert BENACHRICHTIGUNG_MUSTER.findall(uebung_mit) == []
    assert "mit-staging-app: true" in uebung_mit
    assert 'anforderungen: "requirements-dev.txt"' in uebung_mit
    assert 'skill-repo: "Shavy72/claude-skill-to-spawn"' in uebung_mit

    uebung_ohne = nachtrunde.uebungsrepo_anschluss_yaml(mit_staging_app=False)
    assert nachtrunde.pruefe_anschluss_nachtrunde_text(uebung_ohne) == []
    assert "mit-staging-app: false" in uebung_ohne


def test_pruefe_cron_verlangt_schiefe_minute() -> None:
    nachtrunde.pruefe_cron("17 2 * * *")
    nachtrunde.pruefe_cron("43 3 * * 1-5")
    for schlecht in ("0 2 * * *", "00 2 * * *", "60 2 * * *", "17 2 * *", ""):
        with pytest.raises(ValueError):
            nachtrunde.pruefe_cron(schlecht)


# --- Weg-Test Vorab-Prüfung (a: ohne neue Commits < 60 s; mit neuen Commits) ------------------


def test_vorab_pruefung_ueberspringt_ohne_neue_commits_in_sekunden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    zustand = tmp_path / "gh_state.json"
    zustand.write_text(
        json.dumps(
            {
                "lauf_details": {"501": {"id": 501, "workflow_id": 77}},
                "workflow_runs": [
                    {"id": 501, "head_sha": sha1, "status": "in_progress", "conclusion": None},
                    {"id": 499, "head_sha": sha1, "status": "completed", "conclusion": "success"},
                ],
            }
        ),
        encoding="utf-8",
    )
    stub = _schreibe_gh_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_TEST", "1")

    gh_out = tmp_path / "github_output.txt"
    summary = tmp_path / "summary.md"
    start = time.monotonic()
    erg = nachtrunde.vorab_pruefung(
        repo,
        hauptzweig="main",
        repo_slug="BeispielOrg/uebungs-repo",
        aktueller_lauf_id="501",
        github_output=gh_out,
        summary_pfad=summary,
    )
    dauer = time.monotonic() - start

    assert dauer < 60.0
    assert erg.ok is True
    assert erg.exit_code == leiter.GRUEN
    assert erg.laufen is False
    assert erg.aktueller_sha == sha1
    assert erg.letzter_gruener_sha == sha1
    assert "laufen=false" in gh_out.read_text(encoding="utf-8")
    assert "übersprungen" in summary.read_text(encoding="utf-8")


def test_vorab_pruefung_laeuft_bei_neuem_commit_und_bei_rotem_vorgaenger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    (repo / "README.md").write_text("# Änderung\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "feat: neu")
    sha2 = _git(repo, "rev-parse", "HEAD")
    assert sha2 != sha1

    zustand = tmp_path / "gh_state.json"
    zustand.write_text(
        json.dumps(
            {
                "workflow_runs": [
                    {"id": 502, "head_sha": sha2, "status": "completed", "conclusion": "failure"},
                    {"id": 499, "head_sha": sha1, "status": "completed", "conclusion": "success"},
                ]
            }
        ),
        encoding="utf-8",
    )
    stub = _schreibe_gh_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_TEST", "1")

    gh_out = tmp_path / "github_output.txt"
    erg = nachtrunde.vorab_pruefung(
        repo,
        hauptzweig="main",
        repo_slug="BeispielOrg/uebungs-repo",
        workflow_datei="nachtrunde.yml",
        github_output=gh_out,
    )

    assert erg.ok is True
    assert erg.laufen is True
    assert erg.aktueller_sha == sha2
    assert erg.letzter_gruener_sha == sha1
    assert "laufen=true" in gh_out.read_text(encoding="utf-8")


def test_vorab_pruefung_erste_runde_und_api_fehler_laufen_sicher_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    zustand = tmp_path / "gh_state.json"
    zustand.write_text(json.dumps({"workflow_runs": []}), encoding="utf-8")
    stub = _schreibe_gh_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_TEST", "1")

    erste = nachtrunde.vorab_pruefung(
        repo,
        hauptzweig="main",
        repo_slug="BeispielOrg/uebungs-repo",
        workflow_datei="nachtrunde.yml",
    )
    assert erste.ok is True and erste.laufen is True and erste.letzter_gruener_sha == ""
    assert erste.aktueller_sha == sha1

    zustand.write_text(
        json.dumps(
            {
                "fehler_bei": [
                    "api",
                    "repos/BeispielOrg/uebungs-repo/actions/workflows/nachtrunde.yml/runs?branch=main&status=success&per_page=20",
                ]
            }
        ),
        encoding="utf-8",
    )
    api_ausfall = nachtrunde.vorab_pruefung(
        repo,
        hauptzweig="main",
        repo_slug="BeispielOrg/uebungs-repo",
        workflow_datei="nachtrunde.yml",
    )
    assert api_ausfall.ok is True and api_ausfall.laufen is True


def test_vorab_pruefung_immer_laufen_umgeht_abfrage(tmp_path: Path) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    erg = nachtrunde.vorab_pruefung(
        repo,
        hauptzweig="main",
        repo_slug="BeispielOrg/uebungs-repo",
        immer_laufen=True,
    )
    assert erg.ok is True and erg.laufen is True and erg.aktueller_sha == sha1


# --- Weg-Test Ein-Ticket-Lebenszyklus „Nachtrunde rot“ (b, c, d) ------------------------------


def test_ticket_abgleich_lebenszyklus_erstellen_kommentieren_schliessen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    zustand = tmp_path / "gh_state.json"
    zustand.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 7,
                        "title": "Anderes Ticket",
                        "body": "bleibt unberührt",
                        "state": "open",
                        "url": "https://github.com/BeispielOrg/uebungs-repo/issues/7",
                        "comments": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    stub = _schreibe_gh_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_TEST", "1")

    # (b) Erster roter Lauf → genau ein Ticket „Nachtrunde rot“ erstellen.
    rot1 = nachtrunde.ticket_abgleich(
        repo,
        ergebnis="rot",
        repo_slug="BeispielOrg/uebungs-repo",
        zweig="main",
        sha=sha1,
        lauf_url="https://github.com/BeispielOrg/uebungs-repo/actions/runs/1001",
        details="stufe-2: failure",
    )
    assert rot1.ok is True
    assert rot1.aktion == "erstellt"
    assert rot1.ticket_nummer == 8
    stand = json.loads(zustand.read_text(encoding="utf-8"))
    offene_nr = [i for i in stand["issues"] if i["title"] == nachtrunde.TICKET_TITEL and i["state"] == "open"]
    assert len(offene_nr) == 1
    assert "runs/1001" in offene_nr[0]["body"]
    assert sha1[:12] in offene_nr[0]["body"]

    # (c) Zweiter roter Lauf → Kommentar im selben Ticket, KEIN zweites Ticket.
    rot2 = nachtrunde.ticket_abgleich(
        repo,
        ergebnis="rot",
        repo_slug="BeispielOrg/uebungs-repo",
        zweig="main",
        sha=sha1,
        lauf_url="https://github.com/BeispielOrg/uebungs-repo/actions/runs/1002",
        details="stufe-2: failure",
    )
    assert rot2.ok is True
    assert rot2.aktion == "kommentiert"
    assert rot2.ticket_nummer == 8
    stand = json.loads(zustand.read_text(encoding="utf-8"))
    offene_nr = [i for i in stand["issues"] if i["title"] == nachtrunde.TICKET_TITEL and i["state"] == "open"]
    assert len(offene_nr) == 1
    assert len(offene_nr[0]["comments"]) == 1
    assert "runs/1002" in offene_nr[0]["comments"][0]

    # (d) Grüner Lauf → Ticket automatisch schließen; fremdes Ticket #7 bleibt offen.
    gruen1 = nachtrunde.ticket_abgleich(
        repo,
        ergebnis="gruen",
        repo_slug="BeispielOrg/uebungs-repo",
        zweig="main",
        sha=sha1,
        lauf_url="https://github.com/BeispielOrg/uebungs-repo/actions/runs/1003",
    )
    assert gruen1.ok is True
    assert gruen1.aktion == "geschlossen"
    assert gruen1.ticket_nummer == 8
    assert gruen1.geschlossene_nummern == (8,)
    stand = json.loads(zustand.read_text(encoding="utf-8"))
    assert [i for i in stand["issues"] if i["title"] == nachtrunde.TICKET_TITEL and i["state"] == "open"] == []
    geschlossen = next(i for i in stand["issues"] if i["number"] == 8)
    assert geschlossen["state"] == "closed"
    assert "runs/1003" in geschlossen["comments"][-1]
    assert next(i for i in stand["issues"] if i["number"] == 7)["state"] == "open"

    # Weiterer grüner Lauf ohne offenes Ticket → keine Aktion.
    gruen2 = nachtrunde.ticket_abgleich(
        repo,
        ergebnis="gruen",
        repo_slug="BeispielOrg/uebungs-repo",
        zweig="main",
        sha=sha1,
        lauf_url="https://github.com/BeispielOrg/uebungs-repo/actions/runs/1004",
    )
    assert gruen2.ok is True
    assert gruen2.aktion == "unveraendert"
    assert gruen2.ticket_nummer is None


def test_ticket_abgleich_fail_closed_bei_gh_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    zustand = tmp_path / "gh_state.json"
    zustand.write_text(json.dumps({"fehler_bei": ["issue", "list"]}), encoding="utf-8")
    stub = _schreibe_gh_stub(tmp_path, zustand)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv("TO_SPAWN_TEST", "1")

    erg = nachtrunde.ticket_abgleich(
        repo,
        ergebnis="rot",
        repo_slug="BeispielOrg/uebungs-repo",
        zweig="main",
        sha=sha1,
    )
    assert erg.ok is False
    assert erg.exit_code == leiter.GATE_FEHLER
    assert erg.aktion == "fehler"


# --- CLI & Integration in pipeline_vorlage ----------------------------------------------------


def test_cli_vorlage_und_rahmen_stufe_nachtrunde(tmp_path: Path) -> None:
    ziel = tmp_path / "pipeline-nachtrunde.yml"
    aus = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "vorlage",
        "--stufe",
        "nachtrunde",
        "--ausgabe",
        str(ziel),
        cwd=tmp_path,
    )
    assert aus.returncode == 0, aus.stdout + aus.stderr
    assert ziel.is_file()

    pruef = _cli("to_spawn.pruef.nachtrunde", "vorlage", "--pruefen", str(ziel), cwd=tmp_path)
    assert pruef.returncode == 0, pruef.stdout + pruef.stderr

    anschluss = tmp_path / "nachtrunde.yml"
    aus2 = _cli(
        "to_spawn.pruef.nachtrunde",
        "vorlage",
        "--uebungsrepo",
        "--mit-staging-app",
        "--ausgabe",
        str(anschluss),
        cwd=tmp_path,
    )
    assert aus2.returncode == 0, aus2.stdout + aus2.stderr
    pruef2 = _cli(
        "to_spawn.pruef.nachtrunde",
        "vorlage",
        "--uebungsrepo",
        "--pruefen",
        str(anschluss),
        cwd=tmp_path,
    )
    assert pruef2.returncode == 0, pruef2.stdout + pruef2.stderr


def test_cli_vorab_und_ticket_mit_json_und_cp1252_pipe(tmp_path: Path) -> None:
    repo, sha1 = _probe_git_repo(tmp_path / "repo")
    zustand = tmp_path / "gh_state.json"
    zustand.write_text(
        json.dumps(
            {
                "workflow_runs": [{"id": 10, "head_sha": sha1, "status": "completed", "conclusion": "success"}],
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    stub = _schreibe_gh_stub(tmp_path, zustand)
    env = {"TO_SPAWN_GH_STUB": str(stub), "TO_SPAWN_TEST": "1", "PYTHONIOENCODING": "cp1252"}

    vorab = _cli(
        "to_spawn.pruef.nachtrunde",
        "vorab",
        "--repo",
        str(repo),
        "--repo-slug",
        "BeispielOrg/uebungs-repo",
        "--hauptzweig",
        "main",
        "--workflow-datei",
        "nachtrunde.yml",
        "--json",
        cwd=repo,
        extra_env=env,
    )
    assert vorab.returncode == 0, vorab.stdout + vorab.stderr
    daten = json.loads(vorab.stdout)
    assert daten["laufen"] is False

    ticket = _cli(
        "to_spawn.pruef.nachtrunde",
        "ticket",
        "--repo",
        str(repo),
        "--repo-slug",
        "BeispielOrg/uebungs-repo",
        "--ergebnis",
        "rot",
        "--zweig",
        "main",
        "--sha",
        sha1,
        "--lauf-url",
        "https://github.com/BeispielOrg/uebungs-repo/actions/runs/99",
        "--json",
        cwd=repo,
        extra_env=env,
    )
    assert ticket.returncode == 0, ticket.stdout + ticket.stderr
    tdaten = json.loads(ticket.stdout)
    assert tdaten["aktion"] == "erstellt"
    assert tdaten["ticket_nummer"] == 1


# --- Übungs-Repo-Anschluss über probe/anlegen.sh --mit-nachtrunde -----------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="anlegen.sh braucht bash")
@pytest.mark.parametrize("variante", ("mit-staging", "ohne-staging"))
def test_probe_anlegen_mit_nachtrunde_schliesst_uebungsrepo_an(tmp_path: Path, variante: str) -> None:
    repo = tmp_path / f"uebung-{variante}"
    app_p, stg_p, live_p = _freie_ports(3)
    raus = subprocess.run(
        [
            "bash",
            str(ANLEGEN),
            str(repo),
            variante,
            "--app-port",
            str(app_p),
            "--staging-port",
            str(stg_p),
            "--live-port",
            str(live_p),
            "--mit-nachtrunde",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
        **prozessbaum.fensterlos(),
    )
    assert raus.returncode == 0, raus.stdout + raus.stderr
    wf = repo / ".github" / "workflows" / "nachtrunde.yml"
    assert wf.is_file()
    inhalt = wf.read_text(encoding="utf-8")
    assert nachtrunde.pruefe_anschluss_nachtrunde_text(inhalt) == []
    assert BENACHRICHTIGUNG_MUSTER.findall(inhalt) == []
    erwartet_staging = "true" if variante == "mit-staging" else "false"
    assert f"mit-staging-app: {erwartet_staging}" in inhalt
    assert _git(repo, "status", "--porcelain") == ""
    assert ".github/workflows/nachtrunde.yml" in _git(repo, "ls-files").splitlines()


# --- Doku inkl. Budget-Hinweis (30 $) ---------------------------------------------------------


def test_doku_enthaelt_nachtrunde_und_budget_hinweis_30_dollar() -> None:
    text = DOKU.read_text(encoding="utf-8")
    for begriff in (
        "pipeline-nachtrunde.yml",
        "Nachtrunde rot",
        "concurrency",
        "30 $",
        "Billing",
        "17 2 * * *",
        "to_spawn.pruef.nachtrunde",
        "--mit-nachtrunde",
    ):
        assert begriff in text, f"Pflicht-Begriff {begriff!r} fehlt in docs/PIPELINE_VORLAGE.md"
    for eingabe in nachtrunde.EINGABEN_NACHTRUNDE:
        assert f"`{eingabe.name}`" in text, f"Eingabe {eingabe.name!r} nicht in docs/PIPELINE_VORLAGE.md dokumentiert"


# --- Eingecheckte GitHub-Workflows gegen Vorlage aus to_spawn/pruef/nachtrunde.py -------------


def test_eingecheckte_workflow_vorlage_entspricht_generator_und_vertrag() -> None:
    """Prüft ``.github/workflows/pipeline-nachtrunde.yml`` gegen Generator, Vertrag, Runner und Secrets."""
    assert WORKFLOW_VORLAGE.is_file(), f"Workflow-Vorlage fehlt: {WORKFLOW_VORLAGE}"
    text = WORKFLOW_VORLAGE.read_text(encoding="utf-8")
    assert text == nachtrunde.vorlage_nachtrunde_yaml()
    assert nachtrunde.pruefe_nachtrunde_vorlage_text(text) == []
    assert BENACHRICHTIGUNG_MUSTER.findall(text) == []
    assert "secrets." not in text.lower()
    assert "self-hosted" not in text.lower()

    runner = re.findall(r"(?m)^\s*runs-on:\s*(.+?)\s*$", text)
    assert runner, "mindestens ein runs-on-Eintrag erwartet"
    assert set(runner) <= ERLAUBTE_RUNNER, f"Unerlaubter/bezahlter Runner: {set(runner) - ERLAUBTE_RUNNER}"

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    on_block = daten.get("on") or daten.get(True)
    assert isinstance(on_block, dict) and "workflow_call" in on_block
    eingaben = on_block["workflow_call"]["inputs"]
    for eingabe in nachtrunde.EINGABEN_NACHTRUNDE:
        assert eingabe.name in eingaben
        assert eingaben[eingabe.name]["type"] == eingabe.typ
    assert daten["concurrency"]["cancel-in-progress"] is False
    assert daten["permissions"] == {"contents": "read", "actions": "read", "issues": "write"}
    assert set(daten["jobs"]) == {"vorab", "vorbereiten", "stufe-2", "staging-probe", "ticket-abgleich"}


def test_eingecheckter_eigenanschluss_hat_zeitplan_workflow_dispatch_und_keine_secrets() -> None:
    """Prüft ``.github/workflows/pipeline-nachtrunde-eigen.yml`` gegen Generator, Zeitplan und Vertrag."""
    assert WORKFLOW_EIGEN.is_file(), f"Eigenanschluss-Workflow fehlt: {WORKFLOW_EIGEN}"
    text = WORKFLOW_EIGEN.read_text(encoding="utf-8")
    assert text == nachtrunde.eigenanschluss_nachtrunde_yaml()
    assert nachtrunde.pruefe_anschluss_nachtrunde_text(text) == []
    assert BENACHRICHTIGUNG_MUSTER.findall(text) == []
    assert "secrets." not in text.lower()
    assert "self-hosted" not in text.lower()
    assert f'cron: "{nachtrunde.STANDARD_CRON}"' in text
    assert "workflow_dispatch:" in text
    assert "uses: ./.github/workflows/pipeline-nachtrunde.yml" in text

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    on_block = daten.get("on") or daten.get(True)
    assert isinstance(on_block, dict)
    assert on_block["schedule"] == [{"cron": nachtrunde.STANDARD_CRON}]
    assert "workflow_dispatch" in on_block
    assert daten["concurrency"]["cancel-in-progress"] is False
    job = daten["jobs"]["nachtrunde"]
    assert job["uses"] == "./.github/workflows/pipeline-nachtrunde.yml"
    betriebssysteme = json.loads(job["with"]["betriebssysteme"])
    assert set(betriebssysteme) <= {"ubuntu-latest", "windows-latest"}
