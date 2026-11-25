"""#274 P1 Fix-Runde: Repo-Zusatz sicher lesen, Platzhalter-Reihenfolge, Bau-Log, keine Kopie.

Beobachtet (Prüfer zu PR 317):
- ``install.sh``/``install.ps1``/Einrichter legten die Regel-Kopie ``_default.json`` weiter an —
  genau die Datei, vor der ``bau.py`` bei jedem Start warnt.
- ``start_text.zusatz_datei`` durfte aus dem Repo herausführen; nicht-UTF-8 brach mit Exit 1 ab.
- Die Warnungen standen nur im Fenster (übermalt), nicht im Bau-Log.
- Ein ``{KONTEXT}`` im Zusatz setzte das Kontext-Paket ein zweites Mal ein.
- Der Weg-Test #212 legte seinen Wegwerf-Text über die Repo-Kopie an, die nicht mehr gilt.

Weg-Test: ``bau.py <N> --print-prompt`` als echter Prozess gegen ein Temp-Repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
BAU = SKILL / "skripte" / "bau.py"
ZUSATZ_REL = "docs/agents/bau_zusatz.md"
KOPF = "## Regeln dieses Repos"


def _repo(tmp_path: Path, *, zusatz_datei: str | None = ZUSATZ_REL, alte_kopie: bool = False) -> Path:
    repo = tmp_path / "repo"
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/test-org/test-repo.git"],
        check=True,
    )
    (manifeste / "spec-9.json").write_text(
        json.dumps({"spec": 9, "tickets": {"7": {"title": "Titel", "files": ["datei_a.py"]}}}),
        encoding="utf-8",
    )
    if alte_kopie:
        (manifeste / "_default.json").write_text(
            json.dumps({"prompt_template": "ALTE KOPIE #{N}", "core_skills": []}), encoding="utf-8"
        )
    if zusatz_datei is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(
            json.dumps({"start_text": {"zusatz_datei": zusatz_datei}}), encoding="utf-8"
        )
    return repo


def _lauf(repo: Path, tmp_path: Path, *, bau: Path = BAU, dry_run: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TO_SPAWN_REPO": str(repo), "TMPDIR": str(tmp_path)}
    env.pop("BAU_AUFTRAG", None)
    return subprocess.run(
        [sys.executable, str(bau), "7", *(["--dry-run"] if dry_run else []), "--print-prompt"],
        capture_output=True,
        text=True,
        encoding="utf-8",  # bau.py schreibt UTF-8; unter Windows wäre sonst cp1252 die Vorgabe
        env=env,
        cwd=repo,
        timeout=120,
        check=False,
    )


# --- Punkt 3: Pfad aus dem Repo heraus → Warnung, kein Zusatz ------------------------------


def test_zusatz_ausserhalb_des_repos_wird_nicht_gelesen(tmp_path: Path) -> None:
    draussen = tmp_path / "draussen.md"
    draussen.write_text("GEHEIM VON DRAUSSEN\n", encoding="utf-8")
    for nr, rel in enumerate(("../draussen.md", str(draussen), "docs/link.md")):
        fall = tmp_path / f"fall{nr}"
        fall.mkdir()
        repo = _repo(fall)
        (fall / "draussen.md").write_text("GEHEIM VON DRAUSSEN\n", encoding="utf-8")
        (repo / "docs" / "link.md").symlink_to(draussen)
        cfg = repo / ".to-spawn" / "config.json"
        cfg.write_text(json.dumps({"start_text": {"zusatz_datei": rel}}), encoding="utf-8")
        lauf = _lauf(repo, fall)
        assert lauf.returncode == 0, (rel, lauf.stderr)
        assert "GEHEIM VON DRAUSSEN" not in lauf.stdout, rel
        assert KOPF not in lauf.stdout, rel
        assert "außerhalb des Repos" in lauf.stderr, (rel, lauf.stderr)


# --- Punkt 4: nicht UTF-8 → Warnung, kein Abbruch ------------------------------------------


def test_zusatz_nicht_utf8_warnt_und_startet(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / ZUSATZ_REL).write_bytes(b"Regel \xff\xfe kaputt\n")
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "to_spawn.py blocker" in lauf.stdout
    assert KOPF not in lauf.stdout
    assert "bau_zusatz.md" in lauf.stderr and "nicht lesbar" in lauf.stderr


# --- Punkt 5: Warnungen zusätzlich ins Bau-Log ---------------------------------------------


def test_start_warnungen_stehen_im_bau_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path, zusatz_datei="../draussen.md", alte_kopie=True)
    lauf = _lauf(repo, tmp_path, dry_run=False)
    assert lauf.returncode == 0, lauf.stderr
    datei = repo / ".to-spawn" / "bau_log" / "7.jsonl"
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]
    warnungen = [z for z in zeilen if z["typ"] == "start_warnung"]
    texte = " | ".join(z["text"] for z in warnungen)
    assert "wird ignoriert" in texte, texte
    assert "außerhalb des Repos" in texte, texte


def test_probelauf_schreibt_kein_bau_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path, zusatz_datei="../draussen.md", alte_kopie=True)
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "wird ignoriert" in lauf.stderr
    assert not (repo / ".to-spawn" / "bau_log").exists()


# --- Punkt 6: Reihenfolge der Ersetzung -----------------------------------------------------


def test_zusatz_nur_feste_platzhalter_kontext_nicht_doppelt(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    json_text = '{"a": {"b": 1}}'
    (repo / ZUSATZ_REL).write_text(
        f"Ticket {{N}} in {{REPO}}, Ordner {{WT}}.\nKontext hier: {{KONTEXT}} / {{TITLE}}\nJSON {json_text}\n",
        encoding="utf-8",
    )
    lauf = _lauf(repo, tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert "Ticket 7 in test-org/test-repo, Ordner " in lauf.stdout
    assert "{WT}" not in lauf.stdout
    assert "Kontext hier: {KONTEXT} / {TITLE}" in lauf.stdout
    assert lauf.stdout.count("- datei_a.py") == 1, lauf.stdout
    assert f"JSON {json_text}" in lauf.stdout


# --- Punkt 1: kein Schreiber der Regel-Kopie mehr ------------------------------------------


def test_installer_legen_keine_regel_kopie_an() -> None:
    sh = (SKILL / "install.sh").read_text(encoding="utf-8")
    ps1 = (SKILL / "install.ps1").read_text(encoding="utf-8")
    assert '"$MANIFESTE/_default.json"' not in sh
    assert 'Join-Path $man "_default.json"' not in ps1
    assert 'Copy-Item (Join-Path $hier "repo-scripts\\_default.json")' not in ps1


# --- Punkt 2: Weg-Test-Wegwerf-Text über eine Skill-Kopie ----------------------------------


WEGWERF = "/loop Wegwerf-Weg-Test Ticket #{N} (Spec #{S}). Baue NICHTS, lies keine Dateien."


def test_skill_kopie_mit_eigener_vorlage_ersetzt_den_start_text(tmp_path: Path) -> None:
    """Der Weg #212 nutzt: Skill-Stand kopieren, dort ``repo-scripts/_default.json`` ersetzen."""
    kopie = tmp_path / "skill"
    shutil.copytree(SKILL, kopie, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", "docs", ".venv"))
    (kopie / "repo-scripts" / "_default.json").write_text(
        json.dumps({"core_skills": ["loop"], "core_mcp": [], "prompt_template": WEGWERF}), encoding="utf-8"
    )
    repo = _repo(tmp_path, zusatz_datei=None)
    lauf = _lauf(repo, tmp_path, bau=kopie / "skripte" / "bau.py")
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip() == WEGWERF.replace("{N}", "7").replace("{S}", "9")


def test_weg_umzug_212_nutzt_skill_kopie_statt_repo_kopie() -> None:
    text = (SKILL / "tests" / "weg" / "weg_umzug_212.sh").read_text(encoding="utf-8")
    assert "manifests/_default.json" not in text
    assert '"$SKILL/repo-scripts/_default.json"' in text
