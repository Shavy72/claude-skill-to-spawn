"""Nachbesserung nach Abnahme — Folge-Tickets und neue Runde (#370, Lastenheft L9 A50, A54, N23).

Echt laufen ``skripte/abschluss_paket.py`` (``nachbesserung``, ``stand``, ``rundschau-auftrag``,
``selbstpruefung``, ``ablegen``, ``nachsehen``) und ``to_spawn.py takt`` gegen echte temporäre
Git-Repos (Manifest, Bau-Log, Briefkasten, Rundschau-Deck, Selbstprüfung, Ablage-JSON).
Gestellt ist nur der externe Dienst ``gh`` über Python-Skripte (``TO_SPAWN_GH_STUB``) und
Claude als Aufzeichnungs-Skript — läuft unter Linux und Windows gleich.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from rundschau_beispiel import live_bereit_marker, rundschau_args

from to_spawn import anleitung, bau_log, befugnis, briefkasten, capo, waechter_takt

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
HILFEN = SKILL / "tests" / "hilfen"
FIXTURE = HILFEN / "takt_316"
CLAUDE_AUFZ = HILFEN / "claude_aufzeichnung_316.py"
SPEC = 370
_LINKS = [
    "--stage",
    "https://stage.example",
    "--rundschau",
    "https://r.example/370",
    "--tests",
    "https://t.example/370",
    "--belege",
    "https://b.example/370",
]
_FERTIG = "2026-11-25T10:00:00+01:00"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _lauf(skript: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / skript), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        timeout=120,
        check=False,
    )


def _env(tmp_path: Path, gh_stub: Path | None = None) -> dict[str, str]:
    daten = {
        "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
        "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
    }
    if gh_stub is not None:
        daten["TO_SPAWN_GH_STUB"] = str(gh_stub)
    return daten


def _gh_stub_anlegen(tmp_path: Path, slug: str = "org/app") -> tuple[Path, Path]:
    """Python-Attrappe für ``gh``: legt Folge-Tickets an, verwaltet Sub-Issues und Kanten."""
    zustand = tmp_path / "gh_zustand.json"
    zustand.write_text(
        json.dumps(
            {
                "naechste": 910,
                "angelegt": [],
                "sub_issues": [901, 902, 903],
                "blocked_by": [["903", "901"], ["903", "902"]],
                "issues": {
                    str(SPEC): {"id": SPEC, "number": SPEC, "state": "open", "labels": []},
                    "901": {"id": 901, "number": 901, "state": "closed", "labels": []},
                    "902": {"id": 902, "number": 902, "state": "closed", "labels": []},
                    "903": {
                        "id": 903,
                        "number": 903,
                        "state": "open",
                        "labels": [{"name": "checkpoint:human"}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    stub = tmp_path / "gh_stub_370.py"
    stub.write_text(
        f"""import json, re, sys
from pathlib import Path

ZUSTAND = Path({str(zustand)!r})
SLUG = {slug!r}
args = sys.argv[1:]
daten = json.loads(ZUSTAND.read_text(encoding="utf-8"))

def speichern():
    ZUSTAND.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")

if args[:2] == ["issue", "create"]:
    titel = args[args.index("--title") + 1] if "--title" in args else ""
    body = args[args.index("--body") + 1] if "--body" in args else ""
    nr = daten["naechste"]
    daten["naechste"] = nr + 1
    daten["angelegt"].append({{"number": nr, "title": titel, "body": body}})
    daten["issues"][str(nr)] = {{
        "id": nr,
        "number": nr,
        "title": titel,
        "state": "open",
        "labels": [],
    }}
    speichern()
    print(f"https://github.com/{{SLUG}}/issues/{{nr}}")
    sys.exit(0)

if args[:1] == ["api"]:
    ist_post = "-X" in args and "POST" in args
    pfad = next((a for a in args[1:] if a.startswith("repos/")), "")
    if ist_post and pfad.endswith("/sub_issues"):
        wert = int(next((a.split("=", 1)[1] for a in args if a.startswith("sub_issue_id=")), "0"))
        if wert and wert not in daten["sub_issues"]:
            daten["sub_issues"].append(wert)
        speichern()
        print("{{}}")
        sys.exit(0)
    if ist_post and pfad.endswith("/dependencies/blocked_by"):
        m = re.search(r"/issues/(\\d+)/dependencies/blocked_by$", pfad)
        wartet = m.group(1) if m else ""
        wert = next((a.split("=", 1)[1] for a in args if a.startswith("issue_id=")), "")
        if [wartet, wert] not in daten["blocked_by"]:
            daten["blocked_by"].append([wartet, wert])
        speichern()
        print("{{}}")
        sys.exit(0)
    if pfad.endswith("/sub_issues"):
        if "--jq" in args:
            print("\\n".join(str(x) for x in daten["sub_issues"]))
        else:
            print(json.dumps([{{"number": int(x)}} for x in daten["sub_issues"]]))
        sys.exit(0)
    if pfad.endswith("/dependencies/blocked_by"):
        m = re.search(r"/issues/(\\d+)/dependencies/blocked_by$", pfad)
        ziel = m.group(1) if m else ""
        kanten = [{{"number": int(auf)}} for w, auf in daten["blocked_by"] if w == ziel]
        print(json.dumps(kanten))
        sys.exit(0)
    m = re.search(r"/issues/(\\d+)$", pfad)
    if m:
        nr = m.group(1)
        info = dict(daten["issues"].get(nr, {{"id": int(nr), "number": int(nr), "state": "open", "labels": []}}))
        info["repository_url"] = f"https://api.github.com/repos/{{SLUG}}"
        print(json.dumps(info))
        sys.exit(0)

print("{{}}")
sys.exit(0)
""",
        encoding="utf-8",
    )
    return stub, zustand


def _mail_konfig(repo: Path, tmp_path: Path, *, mit_staging: bool = True) -> Path:
    postfach = tmp_path / "post.jsonl"
    mailer = tmp_path / "mailer.py"
    mailer.write_text(
        "import sys\nopen(sys.argv[1], 'a', encoding='utf-8').write(sys.stdin.read() + '\\n')\n",
        encoding="utf-8",
    )
    (repo / ".to-spawn").mkdir(exist_ok=True)
    konfig: dict[str, Any] = {
        "mail": {"ziel": "", "nur_kritisch": True, "befehl": [sys.executable, str(mailer), str(postfach)]}
    }
    if mit_staging:
        konfig["staging"] = {"url": "https://stage.example"}
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    return postfach


def _repo_anlegen(tmp_path: Path, *, mit_staging: bool = True) -> tuple[Path, Path]:
    r = tmp_path / "projekt"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", "https://github.com/org/app.git")
    manifest = {
        "spec": SPEC,
        "tickets": {
            "901": {"title": "Knopf zeigt den Status", "schaetzung_k": 30, "umfang": "Status-Knopf"},
            "902": {"title": "Export als CSV", "schaetzung_k": 30, "umfang": "CSV-Export"},
            "903": {"title": "Abnahme durch den Nutzer", "schaetzung_k": 15, "umfang": "Abnahme"},
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    bau_log.schreibe(r, 901, "entscheidung", frage="Farbe?", wahl="Grün", grund="klar")
    _git(r, "add", "docs")
    _git(r, "commit", "-q", "-m", "Basis Spec 370")
    postfach = _mail_konfig(r, tmp_path, mit_staging=mit_staging)
    return r, postfach


def _selbstpruefung_gruen(repo: Path, spec: int, tickets: list[str], *, runde: int = 1) -> None:
    ordner = repo / ".to-spawn" / "selbstpruefung" / str(spec)
    ordner.mkdir(parents=True, exist_ok=True)
    daten: dict[str, Any] = {
        "spec": spec,
        "stage": "https://stage.example",
        "browser": "kopflos",
        "testdaten_bereinigt": True,
        "folgen": [
            {
                "ticket": t,
                "titel": f"Ticket #{t}",
                "schritte": [
                    {
                        "schritt": f"Funktion #{t} auf Staging klicken",
                        "ergebnis": "passt",
                        "beleg": f"docs/verify-hard/sp_{spec}_{t}.png",
                        "hinweis": "",
                    }
                ],
            }
            for t in tickets
        ],
    }
    if runde > 1:
        daten["runde"] = runde
    (ordner / "ergebnis.json").write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")


def _takt_welt(tmp_path: Path) -> dict[str, Any]:
    """Echtes Repo + eigene Kopie der #293-gh-Aufzeichnung mit allen Bau-Tickets geschlossen."""
    aufz = tmp_path / "aufzeichnung"
    shutil.copytree(FIXTURE, aufz)
    # Bau-Ticket #297 schließen, sodass alle Bau-Tickets (296–300) fertig sind und nur #301 (checkpoint:human) offen bleibt.
    sub_datei = aufz / "sub_issues_293.json"
    issues = json.loads(sub_datei.read_text(encoding="utf-8"))
    for issue in issues:
        if issue["number"] == 297:
            issue["state"] = "closed"
            issue["state_reason"] = "completed"
    sub_datei.write_text(json.dumps(issues), encoding="utf-8")

    repo = tmp_path / "repo"
    ziel = repo / "docs" / "agents" / "bau_log"
    ziel.mkdir(parents=True)
    for datei in (FIXTURE / "bau_log").glob("*.jsonl"):
        shutil.copy(datei, ziel / datei.name)
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"hauptzweig": "master", "wt_basis": str(tmp_path / "wts")}),
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "bau-logs #293")
    _git(repo, "remote", "add", "origin", (tmp_path / "fern" / "o" / "r.git").as_posix())
    _git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")

    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_LEITSTAND_ORDNER": str(tmp_path / "leitstand"),
            "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "waechter"),
            "TO_SPAWN_GH_STUB": str(HILFEN / "gh_aufzeichnung_316.py"),
            "GH_AUFZEICHNUNG_ORDNER": str(aufz),
            "GH_AUFZEICHNUNG_KOMMENTARE": str(tmp_path / "gh_kommentare.jsonl"),
            "CLAUDE_AUFZEICHNUNG": str(tmp_path / "claude_aufruf.jsonl"),
            "CLAUDE_AUFZEICHNUNG_SPEC": "293",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return {"repo": repo, "env": env, "tmp": tmp_path, "aufz": sub_datei}


def _takt(welt: dict[str, Any], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "takt", "293", "--repo-dir", str(welt["repo"]), *extra],
        cwd=str(SKILL),
        env=welt["env"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _abnahme_ablage_schreiben(repo: Path, spec: int, *, runde: int, tickets: list[int]) -> Path:
    pfad = repo / ".to-spawn" / f"abschluss_{spec}_paket.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    daten = {
        "paket": {
            "spec": spec,
            "stage": "https://stage.example",
            "rundschau": "https://r.example",
            "tests": "https://t.example",
            "saetze": ["Neu: Funktion."],
            "stand": "abnahme",
            "runde": runde,
        },
        "neue_form": True,
        "spec_fertig": _FERTIG,
        "runde": runde,
        "tickets": sorted(tickets),
        "folge_tickets": [],
    }
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    return pfad


# --- Abnahme-Kriterium 1: Kommentar im Spec-Issue -> Folge-Tickets je Punkt + Manifest ---


def test_nachbesserung_legt_folge_tickets_je_punkt_an_und_ergaenzt_manifest(tmp_path: Path) -> None:
    """Nachbesserungs-Punkte legen je ein Folge-Ticket an derselben Spec an, ergänzen Manifest und merken sie vor."""
    repo, _ = _repo_anlegen(tmp_path)
    stub, zustand_pfad = _gh_stub_anlegen(tmp_path)
    _abnahme_ablage_schreiben(repo, SPEC, runde=1, tickets=[901, 902, 903])

    lauf = _lauf(
        "abschluss_paket.py",
        "nachbesserung",
        str(SPEC),
        "--repo",
        str(repo),
        "--punkt",
        "Knopf-Farbe auf Staging zu blass",
        "--punkt",
        "CSV-Export bricht bei Umlauten ab",
        env=_env(tmp_path, stub),
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr

    gh_stand = json.loads(zustand_pfad.read_text(encoding="utf-8"))
    assert len(gh_stand["angelegt"]) == 2
    assert {e["number"] for e in gh_stand["angelegt"]} == {910, 911}
    assert 910 in gh_stand["sub_issues"] and 911 in gh_stand["sub_issues"]

    manifest_daten = json.loads(
        (repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").read_text(encoding="utf-8")
    )
    assert "910" in manifest_daten["tickets"] and "911" in manifest_daten["tickets"]
    assert manifest_daten["tickets"]["910"]["gelb_von"] == SPEC
    assert manifest_daten["tickets"]["911"]["gelb_von"] == SPEC

    ablage = json.loads((repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8"))
    assert ablage["folge_tickets"] == [910, 911]

    # Wiederholung mit demselben Punkt ist idempotent (kein doppeltes Ticket).
    nochmal = _lauf(
        "abschluss_paket.py",
        "nachbesserung",
        str(SPEC),
        "--repo",
        str(repo),
        "--punkt",
        "Knopf-Farbe auf Staging zu blass",
        env=_env(tmp_path, stub),
    )
    assert nochmal.returncode == 0, nochmal.stdout + nochmal.stderr
    assert len(json.loads(zustand_pfad.read_text(encoding="utf-8"))["angelegt"]) == 2


def test_takt_meldet_nachbesserung_kommentar_im_spec_issue(tmp_path: Path) -> None:
    """Ein neuer Nutzer-Kommentar im Spec-Issue nach Abnahme Runde 1 wird im Takt gemeldet; eigene Aufseher-Kommentare nicht."""
    welt = _takt_welt(tmp_path)
    _abnahme_ablage_schreiben(welt["repo"], 293, runde=1, tickets=[296, 297, 298, 299, 300, 301])

    kommentar_datei = Path(welt["env"]["GH_AUFZEICHNUNG_KOMMENTARE"])
    vorlage = json.loads((FIXTURE / "kommentar_315.json").read_text(encoding="utf-8"))
    eintraege = [
        {**vorlage, "id": 1001, "issue": "293", "body": "Aufseher: Abschlussbericht Runde 1"},
        {
            **vorlage,
            "id": 1002,
            "issue": "293",
            "body": "Bitte nachbessern: 1) Knopf zu klein 2) Export fehlt Header",
        },
    ]
    kommentar_datei.write_text("".join(json.dumps(e) + "\n" for e in eintraege), encoding="utf-8")

    probe = _takt(welt, "--dry-run")
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "Bitte nachbessern" in probe.stdout
    assert "nachbesserung 293" in probe.stdout
    assert "Aufseher: Abschlussbericht Runde 1" not in probe.stdout


# --- Abnahme-Kriterium 2: Folge-Tickets fertig -> neue Rundschau, neue Selbstprüfung, „Runde 2“ im Titel ---


def test_runde_2_verlangt_neue_rundschau_und_selbstpruefung_und_titel_runde_2(tmp_path: Path) -> None:
    """Nach Folge-Ticket verlangt Runde 2 neue Rundschau + neue Selbstprüfung und trägt „Runde 2“ im Titel ohne zweite Mail."""
    repo, postfach = _repo_anlegen(tmp_path, mit_staging=True)
    env = _env(tmp_path)

    # Runde 1 vollständig ablegen + versenden.
    _selbstpruefung_gruen(repo, SPEC, ["901", "902"], runde=1)
    live_bereit_marker(repo, SPEC)
    r1_deck = rundschau_args(repo, SPEC)
    r1 = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        *r1_deck,
        "--spec-fertig",
        _FERTIG,
        env=env,
    )
    assert r1.returncode == 0, r1.stdout + r1.stderr

    ablage1 = json.loads((repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8"))
    assert ablage1["runde"] == 1
    assert ablage1["paket"]["runde"] == 1
    assert "Runde 2" not in ablage1.get("titel", "")

    # Marker für nachsehen (Runde-1-Mail raus).
    (repo / "docs" / "agents" / f"rueckblick_{SPEC}.md").write_text("Rückblick ok\n", encoding="utf-8")
    (repo / "docs" / "agents" / f"thermo_{SPEC}.md").write_text("Thermo ok\n", encoding="utf-8")
    mail1 = _lauf(
        "abschluss_paket.py",
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-25T10:05:00+01:00",
        env=env,
    )
    assert mail1.returncode == 0, mail1.stdout + mail1.stderr
    mails_nach_r1 = [json.loads(z) for z in postfach.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert len(mails_nach_r1) == 1
    assert mails_nach_r1[0]["betreff"] == f"Spec {SPEC} bereit zur Abnahme — alles zum Durchschauen"

    # Folge-Ticket #910 ins Manifest aufnehmen (simuliert Nachbesserung).
    m_pfad = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    m_daten = json.loads(m_pfad.read_text(encoding="utf-8"))
    m_daten["tickets"]["910"] = {
        "title": "Gelb aus #370: Knopf-Farbe kräftiger",
        "schaetzung_k": 30,
        "umfang": "Knopf-Farbe kräftiger",
        "gelb_von": SPEC,
    }
    m_pfad.write_text(json.dumps(m_daten, indent=2), encoding="utf-8")

    # Mit altem Runde-1-Deck und alter Runde-1-Selbstprüfung verweigert ablegen (Exit 4).
    alt_versuch = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--repo",
        str(repo),
        *_LINKS,
        *r1_deck,
        env=env,
    )
    assert alt_versuch.returncode == 4, alt_versuch.stdout + alt_versuch.stderr
    assert "#910" in alt_versuch.stdout
    assert "Selbstprüfung" in alt_versuch.stdout

    # Neuer Rundschau-Auftrag nennt Runde 2 und #910.
    auftrag = _lauf("abschluss_paket.py", "rundschau-auftrag", str(SPEC), "--repo", str(repo), env=env)
    assert auftrag.returncode == 0, auftrag.stdout + auftrag.stderr
    auftrag_text = (repo / "docs" / "agents" / f"rundschau_auftrag_{SPEC}.md").read_text(encoding="utf-8")
    assert "Runde 2" in auftrag_text and "#910" in auftrag_text

    # Neues Rundschau-Deck (inkl. #910) + neue Selbstprüfung (planen läuft trotz grünem Runde-1-Marker).
    r2_deck = rundschau_args(repo, SPEC)
    sp_plan = _lauf("abschluss_paket.py", "selbstpruefung", "planen", str(SPEC), "--repo", str(repo), env=env)
    assert sp_plan.returncode == 0, sp_plan.stdout + sp_plan.stderr
    _selbstpruefung_gruen(repo, SPEC, ["901", "902", "910"], runde=2)
    sp_sam = _lauf("abschluss_paket.py", "selbstpruefung", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert sp_sam.returncode == 0, sp_sam.stdout + sp_sam.stderr

    # Jetzt klappt ablegen für Runde 2.
    r2 = _lauf("abschluss_paket.py", "ablegen", str(SPEC), "--repo", str(repo), *_LINKS, *r2_deck, env=env)
    assert r2.returncode == 0, r2.stdout + r2.stderr

    ablage2 = json.loads((repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8"))
    assert ablage2["runde"] == 2
    assert ablage2["paket"]["runde"] == 2
    assert ablage2["folge_tickets"] == []
    assert 910 in ablage2["tickets"]
    assert ablage2["titel"] == f"Spec {SPEC} bereit zur Abnahme (Runde 2) — alles zum Durchschauen"

    md_r2 = (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").read_text(encoding="utf-8")
    assert f"# Spec {SPEC} bereit zur Abnahme (Runde 2) — alles zum Durchschauen" in md_r2

    # Probelauf von nachsehen zeigt Runde 2 im Titel; echter Aufruf schickt keine zweite Mail.
    probe = _lauf("abschluss_paket.py", "nachsehen", str(SPEC), "--repo", str(repo), "--dry-run", env=env)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"Spec {SPEC} bereit zur Abnahme (Runde 2) — alles zum Durchschauen" in probe.stdout

    mail2 = _lauf("abschluss_paket.py", "nachsehen", str(SPEC), "--repo", str(repo), env=env)
    assert mail2.returncode == 0, mail2.stdout + mail2.stderr
    mails_nach_r2 = [json.loads(z) for z in postfach.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert len(mails_nach_r2) == 1, "Runde 2 darf keine ungeklärte zweite Mail schicken"

    # stand-Unterbefehl meldet Runde 2 und nicht mehr fällig.
    stand = _lauf("abschluss_paket.py", "stand", str(SPEC), "--repo", str(repo), "--json", env=env)
    assert stand.returncode == 0, stand.stdout + stand.stderr
    stand_daten = json.loads(stand.stdout)
    assert stand_daten["runde"] == 2 and stand_daten["faellig"] is False


# --- Abnahme-Kriterium 4: Abschluss-Zustand „abnahme Runde 1“ + neues Folge-Ticket -> Takt verlangt Runde 2 ---


def test_takt_abnahme_runde_1_plus_folge_ticket_verlangt_runde_2(tmp_path: Path) -> None:
    """Abnahme Runde 1 + neues Folge-Ticket: offen = ruhig, nach Schließen verlangt der Takt Abschluss Runde 2."""
    welt = _takt_welt(tmp_path)
    _abnahme_ablage_schreiben(welt["repo"], 293, runde=1, tickets=[296, 297, 298, 299, 300, 301])

    # Ausgangsstand: Abnahme Runde 1 liegt vor, kein neues Ticket -> nichts zu entscheiden.
    ruhig = _takt(welt, "--dry-run")
    assert ruhig.returncode == 0, ruhig.stdout + ruhig.stderr
    assert "0 zu entscheiden" in ruhig.stdout, ruhig.stdout

    # Neues Folge-Ticket #305 (noch offen) an Spec #293.
    issues = json.loads(welt["aufz"].read_text(encoding="utf-8"))
    folge = {
        "number": 305,
        "title": "Gelb aus #293: Nachbesserung Knopf",
        "state": "open",
        "state_reason": None,
        "labels": [{"name": "ready-for-agent"}],
        "assignees": [],
        "updated_at": "2026-11-25T10:10:00Z",
    }
    issues.append(folge)
    welt["aufz"].write_text(json.dumps(issues), encoding="utf-8")

    # Solange #305 offen ist, verlangt der Takt noch keinen Abschluss Runde 2.
    offen = _takt(welt, "--dry-run")
    assert offen.returncode == 0, offen.stdout + offen.stderr
    assert "Runde 2" not in offen.stdout, offen.stdout

    # Folge-Ticket #305 wird geschlossen -> jetzt verlangt der Takt den Abschluss Runde 2!
    issues[-1]["state"] = "closed"
    issues[-1]["state_reason"] = "completed"
    welt["aufz"].write_text(json.dumps(issues), encoding="utf-8")

    faellig = _takt(welt, "--dry-run")
    assert faellig.returncode == 0, faellig.stdout + faellig.stderr
    assert "Abschluss Runde 2 fällig" in faellig.stdout, faellig.stdout
    assert "#305" in faellig.stdout

    # Echter Takt übergibt Runde 2 an Claude (und rührt Live nicht an).
    echt = _takt(welt, "--claude", str(CLAUDE_AUFZ))
    assert echt.returncode == 0, echt.stdout + echt.stderr
    aufrufe = [
        json.loads(z)
        for z in Path(welt["env"]["CLAUDE_AUFZEICHNUNG"]).read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]
    assert len(aufrufe) == 1
    assert "Abschluss Runde 2 fällig" in aufrufe[0]["stdin"]
    assert not capo.live_gelaufen(welt["repo"], 293, "o/r")

    # Nachdem Runde 2 abgelegt wurde, ist der Takt wieder ruhig.
    _abnahme_ablage_schreiben(welt["repo"], 293, runde=2, tickets=[296, 297, 298, 299, 300, 301, 305])
    danach = _takt(welt, "--dry-run")
    assert danach.returncode == 0, danach.stdout + danach.stderr
    assert "Runde 2" not in danach.stdout and "Runde 3" not in danach.stdout


# --- Abnahme-Kriterium 3: Ohne ausdrückliches Wort des Nutzers geht nichts live ---


def test_ohne_nutzer_wort_geht_nichts_live(tmp_path: Path) -> None:
    """Auch nach Runde 2 bleibt die Live-Grenze hart: kein Live-Merker, Klasse 2 verweigert Aufseher-Freigabe."""
    repo, _ = _repo_anlegen(tmp_path, mit_staging=False)
    env = _env(tmp_path)
    r1_deck = rundschau_args(repo, SPEC)
    assert (
        _lauf("abschluss_paket.py", "ablegen", str(SPEC), "--repo", str(repo), *_LINKS, *r1_deck, env=env).returncode
        == 0
    )
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_live_paket.json").exists()
    assert not (repo / "docs" / "agents" / f"abschluss_{SPEC}_live.md").exists()
    assert not capo.live_gelaufen(repo, SPEC)

    # Bestehende Klasse-2-Grenze: Live bleibt beim Nutzer, Aufseher darf nicht selbst freigeben.
    assert befugnis.einordnen("live", "deploy") == 2
    assert befugnis.ist_klasse_2(f"Spec {SPEC} nach Abnahme Runde 2 live schalten")
    fid = briefkasten.einwerfen("901", f"Spec {SPEC} nach Runde 2 live schalten?", "ja", 2, [], hauptbaum=repo)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("901", fid, "ja", ["Runde 2 grün"], "alles passt", hauptbaum=repo)

    # Prompt-Quelle nennt Nachbesserung + neue Runde + Live-Grenze.
    modul_spec = importlib.util.spec_from_file_location("wache_370", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    for text in (anleitung.ABSCHLUSS_ANWEISUNG, waechter_takt.PROMPT, wache.PROMPT):
        assert "nachbesserung {S}" in text
        assert "Runde 2" in text
        assert "ausdrückliches Wort des Nutzers" in text or "ausdrücklichem Wort des Nutzers" in text
