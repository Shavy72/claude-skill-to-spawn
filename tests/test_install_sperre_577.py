"""#577: Installations-Sperre — roter oder unbekannter Skill-Stand wird nie installiert.

Prüft die Tür „neuer Stand → installieren ja/nein“ in ``to_spawn/install_pruefung.py``
gegen echte Aufrufe von ``install.sh`` und ``install.ps1`` in Wegwerf-Ordnern (nie ``~/.claude``):
- GitHub-Actions-Ergebnis des Commits zuerst (Linux = Pflicht, Windows = Anzeige bis der
  Windows-Topf geschlossen ist), sonst Rückfall auf lokale Prüfung.
- Rot oder unbekannt (z. B. laufender CI-Lauf) → alter Stand bleibt unverändert
  (Prüfsumme identisch), Exit ≠ 0, Meldung nennt den Lauf und den Grund.
- Grüner Stand → Installation wie bisher inkl. Sicherung nach ``_alt/``.
- Bewusstes Überstimmen nur per ausdrücklichem Schalter (``--erzwingen`` / ``-Erzwingen``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_install_suite_rot_415 import (
    NUR_BASH,
    PWSH,
    _env_ohne_pytest,
    _lese_jsonl,
    _mini_klon,
    _ordner_sha,
)

SKILL = Path(__file__).resolve().parents[1]


def _gh_ci_stub(
    tmp: Path,
    *,
    check_runs: list[dict[str, Any]] | None,
    zustand_datei: Path,
    api_exit: int = 0,
) -> Path:
    """Attrappe für ``gh``: beantwortet ``commits/<sha>/check-runs`` sowie Issue-Aufrufe."""
    payload = None if check_runs is None else {"total_count": len(check_runs), "check_runs": check_runs}
    stub = tmp / "gh_ci_stub.py"
    stub.write_text(
        f"""import json, sys
from pathlib import Path

datei = Path({str(zustand_datei)!r})
zustand = json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {{"issues": [], "kommentare": [], "aufrufe": []}}
args = sys.argv[1:]
zustand["aufrufe"].append(args)

def _wert(flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args else ""

if args[:1] == ["api"] and len(args) > 1 and "/check-runs" in args[1]:
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    payload = {payload!r}
    if {api_exit} != 0:
        sys.exit({api_exit})
    if payload is not None:
        print(json.dumps(payload))
    sys.exit(0)
if args[:1] == ["api"] and len(args) > 1 and "/labels/" in args[1]:
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)
if args[:2] == ["label", "create"]:
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)
if args[:2] == ["issue", "list"]:
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    print(json.dumps([
        {{"number": i["number"], "title": i["title"], "body": i["body"]}}
        for i in zustand["issues"] if i["state"] == "open"
    ]))
    sys.exit(0)
if args[:2] == ["issue", "create"]:
    nr = len(zustand["issues"]) + 1
    repo = _wert("--repo") or "test-org/skill-test"
    zustand["issues"].append({{
        "number": nr,
        "title": _wert("--title"),
        "body": _wert("--body"),
        "labels": [_wert("--label")] if _wert("--label") else [],
        "state": "open",
    }})
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    print(f"https://github.com/{{repo}}/issues/{{nr}}")
    sys.exit(0)
if args[:2] == ["issue", "comment"]:
    zustand["kommentare"].append({{"number": int(args[2]), "body": _wert("--body")}})
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)

datei.write_text(json.dumps(zustand), encoding="utf-8")
sys.exit(0)
""",
        encoding="utf-8",
    )
    return stub


def _install_sh(
    klon: Path,
    heim: Path,
    *,
    gh_stub: Path | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    skills = heim / ".claude" / "skills"
    claude_md = heim / ".claude" / "CLAUDE.md"
    befehl = ["bash", str(klon / "install.sh"), "--skills", str(skills), "--claude-md", str(claude_md)]
    if extra_args:
        befehl.extend(extra_args)
    return subprocess.run(
        befehl,
        env=_env_ohne_pytest(heim, gh_stub=gh_stub),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _install_ps1(
    klon: Path,
    heim: Path,
    *,
    gh_stub: Path | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    skills = heim / ".claude" / "skills"
    claude_md = heim / ".claude" / "CLAUDE.md"
    befehl = [
        PWSH or "pwsh",
        "-NoProfile",
        "-File",
        str(klon / "install.ps1"),
        "-Skills",
        str(skills),
        "-ClaudeMd",
        str(claude_md),
    ]
    if extra_args:
        befehl.extend(extra_args)
    return subprocess.run(
        befehl,
        env=_env_ohne_pytest(heim, gh_stub=gh_stub),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


@NUR_BASH
def test_roter_ci_lauf_sperrt_installation_und_nennt_den_roten_lauf(tmp_path: Path) -> None:
    """#577 Weg-Test: Linux-Lauf in GitHub Actions rot → alter Stand unverändert, Exit ≠ 0, Meldung nennt Lauf."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter stabiler Stand\n", encoding="utf-8")
    (alt / "STAND.json").write_text('{"commit": "alt111111111"}\n', encoding="utf-8")
    vorher_sha = _ordner_sha(alt)

    # Lokaler Mini-Test wäre grün — erst das rote GitHub-Actions-Ergebnis sperrt den Commit.
    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_ok.py",
        test_inhalt="def test_gruen() -> None:\n    assert True\n",
        marker_text="roter CI-Stand\n",
    )
    lauf_url = "https://github.com/test-org/skill-test/actions/runs/999/job/101"
    gh_stand = tmp_path / "gh.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 101,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": lauf_url,
            },
        ],
    )

    lauf = _install_sh(klon, heim, gh_stub=stub)
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode != 0, f"Roter CI-Stand wurde installiert!\n{ausgabe}"
    assert _ordner_sha(alt) == vorher_sha, "Alter Ordner wurde trotz rotem CI-Lauf verändert"
    assert not (heim / ".claude" / "skills" / "_alt").exists()
    assert "pytest (ubuntu-latest)" in ausgabe, ausgabe
    assert lauf_url in ausgabe, ausgabe
    assert "--erzwingen" in ausgabe, ausgabe

    zeilen = _lese_jsonl(heim / ".claude" / "to-spawn" / "install_fehler.jsonl")
    assert len(zeilen) == 1
    assert zeilen[0]["ergebnis"] == "rot"
    assert zeilen[0]["quelle"] == "github_actions"
    assert zeilen[0]["sha"] == sha
    assert zeilen[0]["lauf_url"] == lauf_url


@NUR_BASH
def test_unbekannter_ci_lauf_in_progress_sperrt_installation_ohne_ticket(tmp_path: Path) -> None:
    """#577 Weg-Test: laufender GitHub-Actions-Lauf (unbekannt) → alter Stand bleibt, Exit ≠ 0, kein rot-Ticket."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")
    vorher_sha = _ordner_sha(alt)

    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_ok.py",
        test_inhalt="def test_gruen() -> None:\n    assert True\n",
    )
    lauf_url = "https://github.com/test-org/skill-test/actions/runs/998/job/102"
    gh_stand = tmp_path / "gh.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 102,
                "name": "pytest (ubuntu-latest)",
                "status": "in_progress",
                "conclusion": None,
                "html_url": lauf_url,
            },
        ],
    )

    lauf = _install_sh(klon, heim, gh_stub=stub)
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode != 0, f"Unbekannter CI-Stand wurde installiert!\n{ausgabe}"
    assert _ordner_sha(alt) == vorher_sha
    assert "pytest (ubuntu-latest)" in ausgabe, ausgabe
    assert lauf_url in ausgabe, ausgabe

    zeilen = _lese_jsonl(heim / ".claude" / "to-spawn" / "install_fehler.jsonl")
    assert len(zeilen) == 1
    assert zeilen[0]["ergebnis"] == "unbekannt"
    assert zeilen[0]["sha"] == sha
    gh_daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    assert gh_daten["issues"] == [], "Laufender CI-Check darf kein install:rot-Ticket anlegen"


@NUR_BASH
def test_gruener_linux_ci_lauf_installiert_mit_sicherung_und_zeigt_rotes_windows_nur_an(
    tmp_path: Path,
) -> None:
    """#577 Weg-Test: Linux in Actions grün, Windows rot (nur Anzeige) → Installation klappt inkl. _alt/."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")

    # Lokaler Test ist absichtlich rot — beweist, dass das grüne Actions-Ergebnis entscheidet.
    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_lokal_rot.py",
        test_inhalt="def test_soll_nicht_laufen() -> None:\n    assert False\n",
        marker_text="gruener CI-Stand\n",
    )
    win_url = "https://github.com/test-org/skill-test/actions/runs/997/job/202"
    gh_stand = tmp_path / "gh.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 201,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/test-org/skill-test/actions/runs/997/job/201",
            },
            {
                "id": 202,
                "name": "pytest (windows-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": win_url,
            },
        ],
    )

    lauf = _install_sh(klon, heim, gh_stub=stub)
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 0, ausgabe
    assert "gruener CI-Stand" in (alt / "SKILL.md").read_text(encoding="utf-8")
    sicherungen = list((heim / ".claude" / "skills" / "_alt").glob("to-spawn-*"))
    assert len(sicherungen) == 1, "Alter Stand muss nach _alt/ gesichert worden sein"
    assert "alter Stand" in (sicherungen[0] / "SKILL.md").read_text(encoding="utf-8")
    assert "pytest (windows-latest)" in ausgabe and win_url in ausgabe, ausgabe
    stempel = json.loads((alt / "STAND.json").read_text(encoding="utf-8"))
    assert stempel["commit"] == sha


@NUR_BASH
def test_ueberstimmen_per_erzwingen_installiert_trotz_rotem_stand(tmp_path: Path) -> None:
    """#577: Bewusstes Überstimmen nur per ausdrücklichem Schalter ``--erzwingen``."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")

    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_kaputt.py",
        test_inhalt="def test_rot() -> None:\n    assert False\n",
        marker_text="erzwungener Stand\n",
    )
    gh_stand = tmp_path / "gh.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 301,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/test-org/skill-test/actions/runs/301",
            },
        ],
    )

    lauf = _install_sh(klon, heim, gh_stub=stub, extra_args=["--erzwingen"])
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 0, ausgabe
    assert "erzwungener Stand" in (alt / "SKILL.md").read_text(encoding="utf-8")
    assert "erzwingen" in ausgabe.lower() or "überstimmt" in ausgabe.lower(), ausgabe
    stempel = json.loads((alt / "STAND.json").read_text(encoding="utf-8"))
    assert stempel["commit"] == sha


@pytest.mark.skipif(not PWSH, reason="pwsh fehlt")
def test_install_ps1_sperrt_roten_ci_lauf_und_erlaubt_erzwingen(tmp_path: Path) -> None:
    """#577: ``install.ps1`` sperrt bei rotem CI-Lauf und unterstützt ``-Erzwingen``."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter ps1 Stand\n", encoding="utf-8")
    vorher_sha = _ordner_sha(alt)

    klon, _sha = _mini_klon(
        tmp_path,
        test_datei="test_ok.py",
        test_inhalt="def test_gruen() -> None:\n    assert True\n",
        marker_text="neuer ps1 Stand\n",
    )
    # install.ps1 startet globale_regeln.py direkt aus dem Zielordner — dort muss mind. eine Regel liegen.
    (klon / "kette" / "global" / "probe.md").write_text("# Probe-Regel\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=klon, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "regel"],
        cwd=klon,
        check=True,
        capture_output=True,
    )
    lauf_url = "https://github.com/test-org/skill-test/actions/runs/401/job/1"
    gh_stand = tmp_path / "gh_ps1.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 401,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": lauf_url,
            },
        ],
    )

    # Ohne -Erzwingen: Abbruch, alter Ordner unverändert.
    rot_lauf = _install_ps1(klon, heim, gh_stub=stub)
    rot_ausgabe = rot_lauf.stdout + rot_lauf.stderr
    assert rot_lauf.returncode != 0, rot_ausgabe
    assert _ordner_sha(alt) == vorher_sha
    assert lauf_url in rot_ausgabe, rot_ausgabe

    # Mit -Erzwingen: bewusst überstimmt.
    ok_lauf = _install_ps1(klon, heim, gh_stub=stub, extra_args=["-Erzwingen"])
    ok_ausgabe = ok_lauf.stdout + ok_lauf.stderr
    assert ok_lauf.returncode == 0, ok_ausgabe
    assert "neuer ps1 Stand" in (alt / "SKILL.md").read_text(encoding="utf-8")


@NUR_BASH
def test_ungespeicherte_aenderungen_vertrauen_nicht_gruenem_ci_und_pruefen_lokal(tmp_path: Path) -> None:
    """#577: Dirty-Klon fällt trotz grünem CI für HEAD auf die lokale Prüfung zurück."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")
    vorher_sha = _ordner_sha(alt)

    klon, _sha = _mini_klon(
        tmp_path,
        test_datei="test_ok.py",
        test_inhalt="def test_gruen() -> None:\n    assert True\n",
    )
    # Ungespeicherte, kaputte Änderung nach dem Commit.
    (klon / "tests" / "test_ok.py").write_text("def test_rot_dirty() -> None:\n    assert False\n", encoding="utf-8")

    gh_stand = tmp_path / "gh_dirty.json"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            {
                "id": 501,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/test-org/skill-test/actions/runs/501",
            },
        ],
    )

    lauf = _install_sh(klon, heim, gh_stub=stub)
    assert lauf.returncode != 0, lauf.stdout + lauf.stderr
    assert _ordner_sha(alt) == vorher_sha


def test_windows_pflicht_sperrt_roten_windows_lauf_und_neuester_rerun_zaehlt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#577: Sobald ``windows_pflicht=True``, sperrt ein roter Windows-Lauf; bei Wiederholung zählt der neueste Lauf."""
    from to_spawn import install_pruefung

    klon, _sha = _mini_klon(tmp_path, test_inhalt="def test_ok() -> None:\n    assert True\n")
    gh_stand = tmp_path / "gh_win.json"
    win_url = "https://github.com/test-org/skill-test/actions/runs/600/job/2"
    stub = _gh_ci_stub(
        tmp_path,
        zustand_datei=gh_stand,
        check_runs=[
            # Älterer roter Linux-Lauf + neuerer grüner Wiederholungs-Lauf → Linux zählt grün.
            {
                "id": 10,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/test-org/skill-test/actions/runs/600/job/1-alt",
            },
            {
                "id": 20,
                "name": "pytest (ubuntu-latest)",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/test-org/skill-test/actions/runs/600/job/1-neu",
            },
            {
                "id": 21,
                "name": "pytest (windows-latest)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": win_url,
            },
        ],
    )
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TO_SPAWN_TESTLAUF", "1")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))

    # Ohne Windows-Pflicht (aktuell, Windows-Topf noch offen): erlaubt + Warnung (Anzeige).
    anzeige = install_pruefung.entscheiden(klon, windows_pflicht=False)
    assert anzeige.erlaubt is True
    assert anzeige.quelle == "github_actions"
    assert win_url in anzeige.warnung

    # Mit Windows-Pflicht (nach Abschluss des Windows-Topfs): gesperrt + nennt Windows-Lauf.
    pflicht = install_pruefung.entscheiden(klon, windows_pflicht=True)
    assert pflicht.erlaubt is False
    assert pflicht.zustand == "rot"
    assert "pytest (windows-latest)" in pflicht.grund
    assert win_url in pflicht.grund


def test_beide_installer_reichen_erzwingen_an_pruefmodul_weiter() -> None:
    """Struktur-Prüfung: ``install.sh`` (--erzwingen) und ``install.ps1`` (-Erzwingen) schalten über dasselbe Modul."""
    sh = (SKILL / "install.sh").read_text(encoding="utf-8")
    ps1 = (SKILL / "install.ps1").read_text(encoding="utf-8-sig")

    assert "--erzwingen" in sh
    assert "-Erzwingen" in ps1 or "$Erzwingen" in ps1
    assert "--erzwingen" in ps1
