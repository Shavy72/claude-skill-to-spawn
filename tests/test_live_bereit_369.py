"""Prüfung „Bereit für Live?“ vor „bereit zur Abnahme“ (#369).

Echt laufen ``skripte/thermo_lauf.py`` (``live-bereit plan`` / ``live-bereit sammeln``)
und ``skripte/abschluss_paket.py`` (``ablegen`` / ``nachsehen``) gegen ein echtes
temporäres git-Repo (Manifest, Bau-Log, Briefkasten, Belegseiten, Rundschau-Deck).
Gestellt ist nur der externe Dienst ``gh`` über ``TO_SPAWN_GH_STUB`` (Python-Skript,
läuft unter Linux und Windows gleich).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from rundschau_beispiel import rundschau_args

from to_spawn import anleitung, bau_log, briefkasten, waechter_takt

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
SPEC = 369
_LINKS = [
    "--stage",
    "https://stage.example",
    "--rundschau",
    "https://r.example/369",
    "--tests",
    "https://t.example/369",
    "--belege",
    "https://b.example/369",
]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _commit(repo: Path, nachricht: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", nachricht)


def _lauf(
    skript: str, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
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
    """Python-Attrappe für ``gh``: legt Folge-Tickets an, führt Buch und antwortet auf ``gh api``."""
    zustand = tmp_path / "gh_zustand.json"
    zustand.write_text(
        json.dumps(
            {
                "naechste": 910,
                "angelegt": [],
                "sub_issues": [],
                "blocked_by": [],
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
    stub = tmp_path / "gh_stub_369.py"
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
        wert = next((a.split("=", 1)[1] for a in args if a.startswith("sub_issue_id=")), "")
        daten["sub_issues"].append(wert)
        speichern()
        print("{{}}")
        sys.exit(0)
    if ist_post and pfad.endswith("/dependencies/blocked_by"):
        m = re.search(r"/issues/(\\d+)/dependencies/blocked_by$", pfad)
        wartet = m.group(1) if m else ""
        wert = next((a.split("=", 1)[1] for a in args if a.startswith("issue_id=")), "")
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
        if ziel == "903":
            for basis_nr in (901, 902):
                if all(k["number"] != basis_nr for k in kanten):
                    kanten.append({{"number": basis_nr}})
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


def _repo_anlegen(tmp_path: Path, *, mit_beleg_902: bool) -> Path:
    r = tmp_path / "projekt"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "remote", "add", "origin", "https://github.com/org/app.git")
    (r / "app.py").write_text("def start():\n    return True\n", encoding="utf-8")
    _commit(r, "chore: Basis")

    manifest = {
        "spec": SPEC,
        "tickets": {
            "901": {
                "title": "CSV-Export mit Semikolon",
                "schaetzung_k": 30,
                "umfang": "CSV-Export nutzt Semikolon als Trennzeichen",
                "files": ["web/export.py"],
            },
            "902": {
                "title": "Status-Anzeige",
                "schaetzung_k": 30,
                "umfang": "Zeigt aktuellen Status im Kopf",
                "files": ["web/status.py"],
            },
            "903": {
                "title": "Abnahme durch den Nutzer",
                "schaetzung_k": 15,
                "umfang": "Menschliche Abnahme auf Staging",
            },
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    _commit(r, "docs: Manifest")

    (r / "web").mkdir(parents=True)
    (r / "web" / "export.py").write_text("TRENNER = ';'\n", encoding="utf-8")
    (r / "web" / "status.py").write_text("STATUS = 'ok'\n", encoding="utf-8")
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    (vh / "901_gruen.txt").write_text(
        "$ python -m pytest tests/test_export.py -q\n8 passed in 0.4s\n", encoding="utf-8"
    )
    if mit_beleg_902:
        (vh / "902.md").write_text(
            "# Belegseite #902\n**ABNAHME: ABGENOMMEN**\nKlickweg geprüft.\n", encoding="utf-8"
        )
    _commit(r, "feat: Export und Status (#901, #902)")

    bau_log.schreibe(
        r, 901, "entscheidung", frage="Trennzeichen", wahl="Semikolon", grund="Excel-Standard"
    )
    fid = briefkasten.einwerfen("902", "Status-Text?", "ok", 3, [], hauptbaum=r)
    briefkasten.antworten("902", fid, "ok", ["Lastenheft A57"], "kurz", hauptbaum=r)
    return r


def _schreibe_antwort(ordner: Path, daten: dict[str, Any]) -> Path:
    pfad = ordner / "teil-live_bereit.json"
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    return pfad


def test_live_bereit_plan_enthaelt_drei_fragen_entscheidungen_und_nur_lesend(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path, mit_beleg_902=True)
    erg = _lauf("thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD")
    assert erg.returncode == 0, erg.stdout + erg.stderr
    daten = json.loads(erg.stdout)
    assert daten["spec"] == SPEC
    assert daten["teil"] == "live_bereit"
    assert len(daten["teile"]) == 1
    teil = daten["teile"][0]
    assert teil["nr"] == "live_bereit"
    prompt = teil["prompt"]
    assert "nie Dateien ändern" in prompt and "kein Edit/Write" in prompt
    assert f"entscheidungen_{SPEC}.md" in prompt
    assert "Trennzeichen" in prompt and "Semikolon" in prompt
    assert "Status-Text?" in prompt
    assert "Sicherung" in prompt and "Rückweg" in prompt
    assert "Abnahme-Beleg" in prompt
    assert f"git -C {repo.resolve()} diff" in prompt
    assert (Path(daten["befunde_ordner"]) / "lauf.json").is_file()

    # Sperre verhindert doppelten Parallel-Start.
    zweiter = _lauf("thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD")
    assert zweiter.returncode == 4
    assert "läuft schon" in zweiter.stdout

    # Ohne Antwort-JSON meldet sammeln Exit 3 und schreibt keinen Marker.
    fehlend = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), "--ohne-github")
    assert fehlend.returncode == 3
    assert "live_bereit" in fehlend.stdout
    assert not (repo / "docs" / "agents" / f"live_bereit_{SPEC}.md").exists()
    assert (Path(daten["befunde_ordner"]) / "lauf.json").is_file()


def test_fehlender_beleg_meldet_luecke_folge_ticket_und_verweigert_ablegen(tmp_path: Path) -> None:
    """Abnahme-Kriterium #369: Spec mit fehlendem Beleg → Prüfung meldet Lücke, Folge-Ticket, ablegen verweigert."""
    repo = _repo_anlegen(tmp_path, mit_beleg_902=False)
    stub, zustand_pfad = _gh_stub_anlegen(tmp_path)
    env = _env(tmp_path, stub)

    geplant = _lauf(
        "thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env
    )
    assert geplant.returncode == 0, geplant.stdout + geplant.stderr
    ordner = Path(json.loads(geplant.stdout)["befunde_ordner"])
    # Selbst wenn ein unaufmerksamer Helfer „ok: true“ behauptet, erkennt die Prüfung den fehlenden Beleg für #902.
    _schreibe_antwort(
        ordner,
        {
            "teil": "live_bereit",
            "fragen": {
                "entscheidungen": {"ok": True, "beleg": "TRENNER = ';' in web/export.py:1; STATUS = 'ok' in web/status.py:1"},
                "live_daten": {"ok": True, "aendert_live_daten": False, "beleg": "Nur statische Export-/Status-Module, keine Migration."},
                "abnahme_belege": {"ok": True, "beleg": "Beleg 901_gruen.txt gesehen"},
            },
            "luecken": [],
        },
    )

    gesammelt = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert gesammelt.returncode == 2, gesammelt.stdout + gesammelt.stderr
    assert "#902" in gesammelt.stdout
    assert "NICHT BEREIT" in gesammelt.stdout

    marker = repo / "docs" / "agents" / f"live_bereit_{SPEC}.md"
    assert marker.is_file()
    marker_text = marker.read_text(encoding="utf-8")
    assert "Ergebnis: NICHT BEREIT" in marker_text
    assert "#902" in marker_text and "#910" in marker_text

    # Folge-Ticket #910 wurde auf GitHub angelegt, ins Manifest eingetragen und an Spec #369 gehängt.
    gh_daten = json.loads(zustand_pfad.read_text(encoding="utf-8"))
    assert len(gh_daten["angelegt"]) == 1
    assert gh_daten["angelegt"][0]["number"] == 910
    assert "910" in gh_daten["sub_issues"]
    manifest_daten = json.loads(
        (repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").read_text(encoding="utf-8")
    )
    assert "910" in manifest_daten["tickets"]
    assert manifest_daten["tickets"]["910"]["gelb_von"] == 902

    # Gültiges Rundschau-Deck anlegen, aber live_bereit_<S>.md meldet Lücke → ablegen --stand abnahme verweigert!
    deck_args = rundschau_args(repo, SPEC, mit_live_bereit=False)
    verweigert = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--stand",
        "abnahme",
        "--repo",
        str(repo),
        *_LINKS,
        *deck_args,
        env=env,
    )
    assert verweigert.returncode == 4, verweigert.stdout + verweigert.stderr
    assert "Bereit für Live" in verweigert.stdout
    assert "#902" in verweigert.stdout or "NICHT BEREIT" in verweigert.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()


def test_ablegen_ohne_live_bereit_marker_verweigert(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path, mit_beleg_902=False)
    deck_args = rundschau_args(repo, SPEC, mit_live_bereit=False)
    # Sicherstellen, dass kein live_bereit-Marker existiert.
    marker = repo / "docs" / "agents" / f"live_bereit_{SPEC}.md"
    marker.unlink(missing_ok=True)
    verweigert = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--stand",
        "abnahme",
        "--repo",
        str(repo),
        *_LINKS,
        *deck_args,
        env=_env(tmp_path),
    )
    assert verweigert.returncode == 4, verweigert.stdout + verweigert.stderr
    assert "Bereit für Live" in verweigert.stdout
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()


def test_live_bereit_gruen_erlaubt_ablegen_und_eine_zeile_im_abschluss_paket(tmp_path: Path) -> None:
    repo = _repo_anlegen(tmp_path, mit_beleg_902=True)
    env = _env(tmp_path)

    geplant = _lauf(
        "thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env
    )
    assert geplant.returncode == 0, geplant.stdout + geplant.stderr
    ordner = Path(json.loads(geplant.stdout)["befunde_ordner"])
    _schreibe_antwort(
        ordner,
        {
            "teil": "live_bereit",
            "fragen": {
                "entscheidungen": {"ok": True, "beleg": "TRENNER = ';' in web/export.py:1; STATUS = 'ok' in web/status.py:1"},
                "live_daten": {"ok": True, "aendert_live_daten": False, "beleg": "Keine Live-Daten-Änderung im Diff."},
                "abnahme_belege": {"ok": True, "beleg": "#901 (901_gruen.txt, 8 passed), #902 (902.md, ABGENOMMEN)."},
            },
            "luecken": [],
        },
    )

    gesammelt = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), "--ohne-github", env=env)
    assert gesammelt.returncode == 0, gesammelt.stdout + gesammelt.stderr
    assert "BEREIT" in gesammelt.stdout
    marker = repo / "docs" / "agents" / f"live_bereit_{SPEC}.md"
    assert marker.is_file()
    assert "Ergebnis: BEREIT — 3/3 Fragen belegt, 0 Lücken" in marker.read_text(encoding="utf-8")

    # Schon BEREIT → erneutes plan meldet Exit 4.
    nochmal = _lauf("thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env)
    assert nochmal.returncode == 4

    # ablegen --stand abnahme klappt und übernimmt das Ergebnis in einer Zeile.
    deck_args = rundschau_args(repo, SPEC, mit_live_bereit=False)
    abgelegt = _lauf(
        "abschluss_paket.py",
        "ablegen",
        str(SPEC),
        "--stand",
        "abnahme",
        "--repo",
        str(repo),
        *_LINKS,
        *deck_args,
        "--spec-fertig",
        "2026-11-25T10:00:00+01:00",
        env=env,
    )
    assert abgelegt.returncode == 0, abgelegt.stdout + abgelegt.stderr

    probe = _lauf(
        "abschluss_paket.py",
        "nachsehen",
        str(SPEC),
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-25T13:00:00+01:00",
        "--dry-run",
        env=env,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    zeilen = [z for z in probe.stdout.splitlines() if z.startswith("**Bereit für Live?**:")]
    assert len(zeilen) == 1, probe.stdout
    assert "BEREIT — 3/3 Fragen belegt, 0 Lücken" in zeilen[0]
    assert f"docs/agents/live_bereit_{SPEC}.md" in zeilen[0]


def test_live_bereit_luecken_entscheidung_und_live_daten_und_erneut_planbar(tmp_path: Path) -> None:
    """Lücken aus Frage 1 + 2 erzeugen je ein Folge-Ticket (idempotent), nach Behebung erneut planbar."""
    repo = _repo_anlegen(tmp_path, mit_beleg_902=True)
    stub, zustand_pfad = _gh_stub_anlegen(tmp_path)
    env = _env(tmp_path, stub)

    geplant = _lauf(
        "thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env
    )
    assert geplant.returncode == 0, geplant.stdout + geplant.stderr
    ordner = Path(json.loads(geplant.stdout)["befunde_ordner"])
    antwort = {
        "teil": "live_bereit",
        "fragen": {
            "entscheidungen": {"ok": False, "beleg": "Semikolon fehlt in web/export.py"},
            "live_daten": {"ok": False, "aendert_live_daten": True, "beleg": "Migration ohne Sicherung/Rückweg"},
            "abnahme_belege": {"ok": True, "beleg": "Alle Belege grün"},
        },
        "luecken": [
            {
                "frage": "entscheidungen",
                "ticket": 901,
                "titel": "Semikolon nicht umgesetzt",
                "grund": "web/export.py nutzt noch Komma",
            },
            {
                "frage": "live_daten",
                "titel": "Sicherung und Rückweg fehlen",
                "grund": "DB-Migration ohne Backup-Skript",
            },
        ],
    }
    _schreibe_antwort(ordner, antwort)

    erst = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert erst.returncode == 2, erst.stdout + erst.stderr
    gh_daten = json.loads(zustand_pfad.read_text(encoding="utf-8"))
    assert [a["number"] for a in gh_daten["angelegt"]] == [910, 911]

    # Wiederholtes sammeln legt keine doppelten Folge-Tickets an.
    zweit = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert zweit.returncode == 2
    gh_daten_2 = json.loads(zustand_pfad.read_text(encoding="utf-8"))
    assert [a["number"] for a in gh_daten_2["angelegt"]] == [910, 911]

    # Ohne Belege für die offenen Folge-Tickets #910/#911 bleibt es NICHT BEREIT (und legt keine neuen Folge-Tickets an).
    vorab_plan = _lauf(
        "thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env
    )
    assert vorab_plan.returncode == 0, vorab_plan.stdout + vorab_plan.stderr
    _schreibe_antwort(
        ordner,
        {
            "teil": "live_bereit",
            "fragen": {
                "entscheidungen": {"ok": True, "beleg": "behauptet"},
                "live_daten": {"ok": True, "aendert_live_daten": False, "beleg": "behauptet"},
                "abnahme_belege": {"ok": True, "beleg": "behauptet"},
            },
            "luecken": [],
        },
    )
    noch_rot = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert noch_rot.returncode == 2
    assert [a["number"] for a in json.loads(zustand_pfad.read_text(encoding="utf-8"))["angelegt"]] == [910, 911]

    # Nach Behebung und grünen Belegen für die beiden Folge-Tickets darf ein neuer Lauf BEREIT melden.
    vh = repo / "docs" / "verify-hard"
    (vh / "910_gruen.txt").write_text("3 passed in 0.1s\n", encoding="utf-8")
    (vh / "911_gruen.txt").write_text("2 passed in 0.1s\n", encoding="utf-8")
    neu_plan = _lauf(
        "thermo_lauf.py", "live-bereit", "plan", str(SPEC), "--repo", str(repo), "--kopf", "HEAD", env=env
    )
    assert neu_plan.returncode == 0, neu_plan.stdout + neu_plan.stderr
    _schreibe_antwort(
        ordner,
        {
            "teil": "live_bereit",
            "fragen": {
                "entscheidungen": {"ok": True, "beleg": "Semikolon umgesetzt (#910)"},
                "live_daten": {"ok": True, "aendert_live_daten": True, "beleg": "Backup + Rückweg in scripts/backup.sh (#911)"},
                "abnahme_belege": {"ok": True, "beleg": "Alle Tickets #901, #902, #910, #911 grün"},
            },
            "luecken": [],
        },
    )
    gruen = _lauf("thermo_lauf.py", "live-bereit", "sammeln", str(SPEC), "--repo", str(repo), env=env)
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert "Ergebnis: BEREIT" in (repo / "docs" / "agents" / f"live_bereit_{SPEC}.md").read_text(encoding="utf-8")


def test_aufseher_anweisung_ruft_live_bereit_vor_ablegen_auf() -> None:
    modul_spec = importlib.util.spec_from_file_location("wache_369", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    quelle: str = anleitung.ABSCHLUSS_ANWEISUNG
    for name, prompt in (("quelle", quelle), ("wache", wache.PROMPT), ("takt", waechter_takt.PROMPT)):
        pos_lb_plan = prompt.index("thermo_lauf.py live-bereit plan {S}")
        pos_lb_sam = prompt.index("thermo_lauf.py live-bereit sammeln {S}")
        pos_ablegen = prompt.index("abschluss_paket.py ablegen {S} --stand abnahme")
        pos_thermo = prompt.index("thermo_lauf.py plan {S}")
        assert pos_lb_plan < pos_lb_sam < pos_ablegen < pos_thermo, name
        assert "live_bereit_{S}.md" in prompt

    wache.PROMPT.format(S=369, REPO="o/r", DATUM="2026-11-25", TAKT=1800, SKILL="/skill")
    waechter_takt.PROMPT.format(S=369, REPO="o/r", SKILL="/skill", LISTE="- x", NOTIZ="(leer)")


def test_live_bereit_marker_folgt_pfade_agenten(tmp_path: Path) -> None:
    if str(SKRIPTE) not in sys.path:
        sys.path.insert(0, str(SKRIPTE))
    import thermo_lauf

    repo = tmp_path / "repo"
    (repo / ".to-spawn").mkdir(parents=True)
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"pfade": {"agenten": "eigen/agenten"}}), encoding="utf-8"
    )
    assert thermo_lauf.live_bereit_marker_pfad(repo, 7) == repo / "eigen" / "agenten" / "live_bereit_7.md"
