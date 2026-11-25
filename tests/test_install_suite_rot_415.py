"""#415: Roter Skill-Stand wird nicht installiert — alter Stand bleibt, Fehler-Log + Ticket, atomarer Tausch.

Prüft alle Akzeptanzkriterien gegen echte Installationen in Wegwerf-Ordnern (nie ``~/.claude``):
- Rot-Beweis zuerst: roter Stand bricht vor dem Austausch mit Exit ≠ 0 ab, alter Stand bleibt
  Byte für Byte gleich (SHA vorher = nachher).
- Grüner Stand tauscht wie bisher und schreibt den installierten SHA ins Fehler-Log.
- Fehler-Log (JSONL im Datenordner ``~/.claude/to-spawn/install_fehler.jsonl``): Zeit, SHA,
  rote Kennungen.
- Genau ein offenes Ticket im Repo (gleiches Rot = Kommentar statt neues Ticket), keine Mail /
  kein Push.
- Atomarer Austausch: Abbruch mitten im Kopieren oder beim Umbenennen hinterlässt nie einen
  halben Stand.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import site
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")
NUR_BASH = pytest.mark.braucht("bash")


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _ordner_sha(ordner: Path) -> str:
    """Kryptografische Prüfsumme über alle Dateipfade und Bytes unter ``ordner``."""
    h = hashlib.sha256()
    for pfad in sorted(p for p in ordner.rglob("*") if p.is_file()):
        rel = pfad.relative_to(ordner).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(pfad.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _mini_klon(
    tmp: Path,
    *,
    test_inhalt: str,
    test_datei: str = "test_probe.py",
    name: str = "klon",
    origin_url: str = "https://github.com/test-org/skill-test.git",
    marker_text: str = "neuer Stand\n",
) -> tuple[Path, str]:
    """Minimaler, voll installierbarer Skill-Klon mit eigener Mini-Suite (schnell, keine Rekursion)."""
    klon = tmp / name
    klon.mkdir(parents=True)

    # Wurzeldateien & Pflichtordner für install.sh / install.ps1
    for datei in ("install.sh", "install.ps1", "to_spawn.py", "SKILL.md", "README.md", "LICENSE", "spawn_local.ps1", "spawn_srv.ps1"):
        quelle = SKILL / datei
        if quelle.is_file():
            shutil.copy2(quelle, klon / datei)
    (klon / "SKILL.md").write_text(f"# Skill\n{marker_text}", encoding="utf-8")
    (klon / "conftest.py").write_text("", encoding="utf-8")

    # Nur die für Installation + Prüfung nötigen Pakete/Ordner (ohne die große Repo-Suite).
    shutil.copytree(
        SKILL / "to_spawn",
        klon / "to_spawn",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (klon / "skripte").mkdir()
    (klon / "skripte" / "spawn_srv.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (klon / "repo-scripts").mkdir()
    (klon / "repo-scripts" / "spawn_srv.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (klon / "nest").mkdir()
    (klon / "nest" / "dummy.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (klon / "aliase" / "to-spawn-local").mkdir(parents=True)
    (klon / "aliase" / "to-spawn-local" / "SKILL.md").write_text("# Alias\n", encoding="utf-8")
    (klon / "kette" / "skills" / "to-spec").mkdir(parents=True)
    (klon / "kette" / "skills" / "to-spec" / "SKILL.md").write_text("# Kette\n", encoding="utf-8")
    (klon / "kette" / "global").mkdir(parents=True)
    (klon / "review-hook").mkdir()
    (klon / "review-hook" / "hook.py").write_text("# hook\n", encoding="utf-8")
    (klon / "review").mkdir()
    (klon / "review" / "config.standard.json").write_text("{}\n", encoding="utf-8")
    (klon / "review-dirigent").mkdir()
    (klon / "review-dirigent" / "SKILL.md").write_text("# Dirigent\n", encoding="utf-8")
    (klon / "docs").mkdir()
    (klon / "leitstand").mkdir()
    (klon / "probe").mkdir()

    # Kleine Test-Suite im Klon — läuft über das echte pytest-Gate.
    (klon / "tests").mkdir()
    (klon / "tests" / test_datei).write_text(test_inhalt, encoding="utf-8")

    _git(klon, "init", "-q", "-b", "main")
    _git(klon, "add", "-A")
    _git(klon, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "stand")
    if origin_url:
        _git(klon, "remote", "add", "origin", origin_url)
    sha = _git(klon, "rev-parse", "HEAD")
    return klon, sha


def _gh_stub(tmp: Path, zustand_datei: Path) -> Path:
    """Attrappe für ``gh``: führt echte Zustandsliste offener Issues + Kommentare in JSON."""
    stub = tmp / "gh_stub.py"
    stub.write_text(
        f"""import json, sys
from pathlib import Path

datei = Path({str(zustand_datei)!r})
zustand = json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {{"issues": [], "kommentare": [], "aufrufe": []}}
args = sys.argv[1:]
zustand["aufrufe"].append(args)

def _wert(flag: str) -> str:
    return args[args.index(flag) + 1] if flag in args else ""

if args[:1] == ["api"] and "/labels/" in (args[1] if len(args) > 1 else ""):
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)
if args[:2] == ["label", "create"]:
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)
if args[:2] == ["issue", "list"]:
    label = _wert("--label")
    state = _wert("--state") or "open"
    treffer = [
        {{"number": i["number"], "title": i["title"], "body": i["body"]}}
        for i in zustand["issues"]
        if (not state or i["state"] == state) and (not label or label in i["labels"])
    ]
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    print(json.dumps(treffer))
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
        "repo": repo,
    }})
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    print(f"https://github.com/{{repo}}/issues/{{nr}}")
    sys.exit(0)
if args[:2] == ["issue", "comment"]:
    nr = int(args[2])
    zustand["kommentare"].append({{"number": nr, "body": _wert("--body"), "repo": _wert("--repo")}})
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    sys.exit(0)

datei.write_text(json.dumps(zustand), encoding="utf-8")
sys.exit(0)
""",
        encoding="utf-8",
    )
    return stub


def _env_ohne_pytest(heim: Path, *, gh_stub: Path | None = None) -> dict[str, str]:
    """Umgebung wie beim echten Nutzerlauf (ohne ``PYTEST_CURRENT_TEST``), isoliert in ``heim``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.pop("PYTEST_CURRENT_TEST", None)
    # Nutzer-Site-Packages (z. B. ~/.local/lib/...) auch mit umgebogenem HOME auffindbar halten.
    user_base = site.getuserbase()
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "XDG_CONFIG_HOME": str(heim / ".config"),
            "XDG_CACHE_HOME": str(heim / ".cache"),
            "LOCALAPPDATA": str(heim / ".local"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUSERBASE": user_base,
            "TO_SPAWN_PY": sys.executable,
            "TO_SPAWN_TESTLAUF": "1",
        }
    )
    if gh_stub is not None:
        env["TO_SPAWN_GH_STUB"] = str(gh_stub)
    return env


def _install_sh(klon: Path, heim: Path, *, gh_stub: Path | None = None) -> subprocess.CompletedProcess[str]:
    skills = heim / ".claude" / "skills"
    claude_md = heim / ".claude" / "CLAUDE.md"
    return subprocess.run(
        ["bash", str(klon / "install.sh"), "--skills", str(skills), "--claude-md", str(claude_md)],
        env=_env_ohne_pytest(heim, gh_stub=gh_stub),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _lese_jsonl(datei: Path) -> list[dict[str, Any]]:
    assert datei.is_file(), f"Fehler-Log fehlt: {datei}"
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


@NUR_BASH
def test_rot_stand_wird_nicht_installiert_alter_stand_bleibt_byte_gleich_und_log_geschrieben(
    tmp_path: Path,
) -> None:
    """#415 Rot-Beweis (E9, E8): Suite rot → Exit ≠ 0, Ziel Byte für Byte gleich, JSONL + Ticket."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter produktiver Stand\n", encoding="utf-8")
    (alt / "STAND.json").write_text('{"commit": "alt111111111"}\n', encoding="utf-8")
    vorher_sha = _ordner_sha(alt)

    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_kaputt.py",
        test_inhalt="def test_rot_beweis() -> None:\n    assert 1 == 2\n",
        marker_text="kaputter neuer Stand\n",
    )
    gh_stand = tmp_path / "gh.json"
    stub = _gh_stub(tmp_path, gh_stand)

    lauf = _install_sh(klon, heim, gh_stub=stub)
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode != 0, f"Roter Stand wurde installiert! Ausgabe:\n{ausgabe}"
    assert _ordner_sha(alt) == vorher_sha, "Installierter Stand wurde trotz roter Suite verändert"
    assert not (heim / ".claude" / "skills" / "_alt").exists(), "Alter Stand wurde trotz roter Suite verschoben"
    assert "rot" in ausgabe.lower() and "unverändert" in ausgabe.lower(), ausgabe

    log_datei = heim / ".claude" / "to-spawn" / "install_fehler.jsonl"
    zeilen = _lese_jsonl(log_datei)
    assert len(zeilen) == 1
    eintrag = zeilen[0]
    assert eintrag["ergebnis"] == "rot"
    assert eintrag["sha"] == sha
    assert eintrag["zeit"]
    assert any("test_kaputt.py::test_rot_beweis" in k for k in eintrag["rote_kennungen"]), eintrag

    gh_daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    assert len(gh_daten["issues"]) == 1, gh_daten
    assert gh_daten["issues"][0]["labels"] == ["install:rot"]
    assert sha in gh_daten["issues"][0]["body"]
    assert "test_kaputt.py::test_rot_beweis" in gh_daten["issues"][0]["body"]


@NUR_BASH
def test_gruener_stand_tauscht_wie_bisher_und_schreibt_sha_ins_log(tmp_path: Path) -> None:
    """#415 (E9, E8): Suite grün → Austausch wie bisher, installierter SHA steht im Fehler-Log."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")

    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_ok.py",
        test_inhalt="def test_gruen() -> None:\n    assert True\n",
        marker_text="gruener neuer Stand\n",
    )
    gh_stand = tmp_path / "gh.json"
    stub = _gh_stub(tmp_path, gh_stand)

    lauf = _install_sh(klon, heim, gh_stub=stub)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "gruener neuer Stand" in (alt / "SKILL.md").read_text(encoding="utf-8")
    stempel = json.loads((alt / "STAND.json").read_text(encoding="utf-8"))
    assert stempel["commit"] == sha

    log_datei = heim / ".claude" / "to-spawn" / "install_fehler.jsonl"
    zeilen = _lese_jsonl(log_datei)
    assert len(zeilen) == 1
    assert zeilen[0]["ergebnis"] == "gruen"
    assert zeilen[0]["sha"] == sha
    assert zeilen[0]["zeit"]
    assert not gh_stand.exists() or json.loads(gh_stand.read_text(encoding="utf-8"))["issues"] == []


@NUR_BASH
def test_wiederholtes_rot_kommentiert_bestehendes_ticket_statt_zweites_anzulegen(
    tmp_path: Path,
) -> None:
    """#415 (E8): Genau ein offenes Ticket im Repo; gleiches oder weiteres Rot kommentiert dort."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# stabil\n", encoding="utf-8")
    vorher = _ordner_sha(alt)

    klon, sha1 = _mini_klon(
        tmp_path,
        test_datei="test_kaputt.py",
        test_inhalt="def test_rot_eins() -> None:\n    assert False\n",
    )
    gh_stand = tmp_path / "gh.json"
    stub = _gh_stub(tmp_path, gh_stand)

    # 1. Rot → genau ein offenes Ticket angelegt.
    assert _install_sh(klon, heim, gh_stub=stub).returncode != 0
    daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    assert len(daten["issues"]) == 1 and len(daten["kommentare"]) == 0

    # 2. Gleiches Rot erneut → Kommentar auf #1, kein zweites Ticket.
    assert _install_sh(klon, heim, gh_stub=stub).returncode != 0
    daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    assert len(daten["issues"]) == 1, "Zweites Ticket trotz offenem install:rot-Ticket angelegt"
    assert len(daten["kommentare"]) == 1
    assert daten["kommentare"][0]["number"] == 1
    assert sha1 in daten["kommentare"][0]["body"]

    # 3. Anderes Rot, Ticket #1 noch offen → weiter genau ein offenes Ticket, zweiter Kommentar.
    (klon / "tests" / "test_kaputt.py").write_text(
        "def test_rot_zwei() -> None:\n    assert 2 == 3\n", encoding="utf-8"
    )
    _git(klon, "add", "-A")
    _git(klon, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "zweiter rot")
    sha2 = _git(klon, "rev-parse", "HEAD")
    assert _install_sh(klon, heim, gh_stub=stub).returncode != 0
    daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    assert len(daten["issues"]) == 1
    assert len(daten["kommentare"]) == 2
    assert sha2 in daten["kommentare"][1]["body"]
    assert "test_kaputt.py::test_rot_zwei" in daten["kommentare"][1]["body"]

    assert _ordner_sha(alt) == vorher
    zeilen = _lese_jsonl(heim / ".claude" / "to-spawn" / "install_fehler.jsonl")
    assert len(zeilen) == 3
    assert [z["ergebnis"] for z in zeilen] == ["rot", "rot", "rot"]


def test_suite_befehl_erhaelt_parallelitaet_wie_suite_sh() -> None:
    """#415 Harte Grenze: Parallelität (-n 4 --dist worksteal) wird nicht gesenkt."""
    from to_spawn import install_pruefung

    befehl = install_pruefung.suite_befehl(sys.executable)
    assert befehl[:5] == [sys.executable, "-m", "pytest", "-q", "-p"]
    assert "no:cacheprovider" in befehl
    assert "-n" in befehl and befehl[befehl.index("-n") + 1] == "4"
    assert "--dist" in befehl and befehl[befehl.index("--dist") + 1] == "worksteal"


def test_kein_mail_oder_push_import_im_install_schutz() -> None:
    """#415 (E8): Keine Mail, kein Telegram oder sonstiger Push im Prüfmodul."""
    pfad = SKILL / "to_spawn" / "install_pruefung.py"
    assert pfad.is_file(), "to_spawn/install_pruefung.py fehlt"
    quelltext = pfad.read_text(encoding="utf-8")
    baum = ast.parse(quelltext)
    importe: set[str] = set()
    docstring_zeilen: set[int] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            importe.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module:
            importe.add(knoten.module)
            importe.update(f"{knoten.module}.{a.name}" for a in knoten.names)
        elif (
            isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.body
            and isinstance(knoten.body[0], ast.Expr)
        ):
            wert = knoten.body[0].value
            if isinstance(wert, ast.Constant) and isinstance(wert.value, str):
                ende = wert.end_lineno or wert.lineno
                docstring_zeilen.update(range(wert.lineno, ende + 1))
    verboten = {"melder", "to_spawn.melder", "smtplib", "email", "urllib", "requests", "http"}
    assert not (importe & verboten), f"Verbotener Benachrichtigungs-Import: {importe & verboten}"
    with pfad.open(encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type == tokenize.COMMENT or tok.start[0] in docstring_zeilen:
                continue
            klein = tok.string.lower()
            for wort in ("telegram", "sendmail", "smtp", "melder"):
                assert wort not in klein, f"Verbotener Push-Aufruf {wort!r} in {pfad.name}:{tok.start[0]}"


def test_atomarer_tausch_abbruch_beim_kopieren_laesst_alten_stand_unberuehrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#415 (E9): Fehler mitten im Kopieren → alter Stand bleibt Byte für Byte gleich, kein Rest-Temp."""
    from to_spawn import install_pruefung

    skills = tmp_path / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")
    (alt / "STAND.json").write_text('{"commit": "alt"}\n', encoding="utf-8")
    vorher = _ordner_sha(alt)

    quelle = tmp_path / "quelle"
    quelle.mkdir()
    (quelle / "a.txt").write_text("neu-a\n", encoding="utf-8")
    (quelle / "b.txt").write_text("neu-b\n", encoding="utf-8")

    echt_copy2 = shutil.copy2
    zaehler = {"n": 0}

    def kaputtes_copy2(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        zaehler["n"] += 1
        if zaehler["n"] == 2:
            raise OSError("Platte voll (simulierter Abbruch mitten im Kopieren)")
        return echt_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copy2", kaputtes_copy2)

    with pytest.raises(OSError, match="Platte voll"):
        install_pruefung.tausche_atomar(
            skills=skills,
            name="to-spawn",
            quellen=[quelle / "a.txt", quelle / "b.txt"],
            zeit="20261125-100000",
        )

    assert _ordner_sha(alt) == vorher
    assert not (skills / "_alt").exists()
    assert [p.name for p in skills.iterdir()] == ["to-spawn"]


def test_atomarer_tausch_abbruch_beim_umbenennen_rollt_alten_stand_zurueck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#415 (E9): Scheitert das Einsetzen nach Wegschieben des alten Stands → Rollback stellt alten Stand wieder her."""
    from to_spawn import install_pruefung

    skills = tmp_path / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter Stand\n", encoding="utf-8")
    vorher = _ordner_sha(alt)

    quelle = tmp_path / "quelle"
    quelle.mkdir()
    (quelle / "SKILL.md").write_text("# neuer Stand\n", encoding="utf-8")

    # Erzwinge den Fallback-Pfad (wie unter Windows/macOS) und lasse den zweiten Rename scheitern.
    monkeypatch.setattr(install_pruefung, "_tausche_linux_atomar", lambda _a, _b: False)
    echt_rename = os.rename
    versuche = {"n": 0}

    def kaputtes_rename(src: Any, dst: Any) -> None:
        versuche["n"] += 1
        if versuche["n"] == 2:
            raise OSError("Ziel gesperrt (simulierter Abbruch beim Tausch)")
        echt_rename(src, dst)

    monkeypatch.setattr(os, "rename", kaputtes_rename)

    with pytest.raises(OSError, match="Ziel gesperrt"):
        install_pruefung.tausche_atomar(
            skills=skills,
            name="to-spawn",
            quellen=[quelle / "SKILL.md"],
            zeit="20261125-100000",
        )

    assert alt.is_dir() and _ordner_sha(alt) == vorher
    # Kein halber Staging-Ordner übrig.
    assert not [p for p in skills.iterdir() if p.name.startswith(".neu-")]


def test_install_sh_und_install_ps1_rufen_pruefung_vor_austausch_und_verbuchen_danach() -> None:
    """Struktur-Prüfung (Linux + Windows): beide Installer fahren Prüfung → atomaren Tausch → Verbuchung."""
    sh = (SKILL / "install.sh").read_text(encoding="utf-8")
    ps1 = (SKILL / "install.ps1").read_text(encoding="utf-8-sig")

    for name, text in (("install.sh", sh), ("install.ps1", ps1)):
        assert "to_spawn.install_pruefung" in text, f"{name}: ruft install_pruefung nicht"
        assert "pruefen" in text and "tauschen" in text and "verbuchen" in text, name
        pos_pruefen = text.index("to_spawn.install_pruefung")
        pos_verbuchen = text.index("verbuchen")
        assert pos_pruefen < pos_verbuchen, name
    assert sh.index("to_spawn.install_pruefung") < sh.index("sichere_und_kopiere to-spawn")
    assert ps1.index("to_spawn.install_pruefung") < ps1.index('Sichere-UndKopiere "to-spawn"')


@pytest.mark.skipif(not PWSH, reason="pwsh fehlt")
def test_install_ps1_rot_stand_bleibt_unveraendert(tmp_path: Path) -> None:
    """#415: ``install.ps1`` bricht bei roter Suite vor jedem Tausch ab und schreibt Fehler-Log."""
    heim = tmp_path / "heim"
    alt = heim / ".claude" / "skills" / "to-spawn"
    alt.mkdir(parents=True)
    (alt / "SKILL.md").write_text("# alter ps1 Stand\n", encoding="utf-8")
    vorher = _ordner_sha(alt)

    klon, sha = _mini_klon(
        tmp_path,
        test_datei="test_ps1_rot.py",
        test_inhalt="def test_ps1_rot() -> None:\n    assert False\n",
    )
    gh_stand = tmp_path / "gh_ps1.json"
    stub = _gh_stub(tmp_path, gh_stand)
    skills = heim / ".claude" / "skills"
    claude_md = heim / ".claude" / "CLAUDE.md"
    lauf = subprocess.run(
        [PWSH or "pwsh", "-NoProfile", "-File", str(klon / "install.ps1"), "-Skills", str(skills), "-ClaudeMd", str(claude_md)],
        env=_env_ohne_pytest(heim, gh_stub=stub),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode != 0, ausgabe
    assert _ordner_sha(alt) == vorher
    assert not (skills / "_alt").exists()
    zeilen = _lese_jsonl(heim / ".claude" / "to-spawn" / "install_fehler.jsonl")
    assert len(zeilen) == 1
    assert zeilen[0]["ergebnis"] == "rot"
    assert zeilen[0]["sha"] == sha
    assert any("test_ps1_rot.py::test_ps1_rot" in k for k in zeilen[0]["rote_kennungen"])


def test_geschlossenes_ticket_laesst_neues_offenes_ticket_zu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#415 (E8): Ist das frühere Ticket geschlossen, legt ein neues Rot wieder genau ein offenes Ticket an."""
    from to_spawn import install_pruefung

    klon, sha = _mini_klon(tmp_path, test_inhalt="def test_ok() -> None:\n    assert True\n")
    gh_stand = tmp_path / "gh_zu.json"
    stub = _gh_stub(tmp_path, gh_stand)
    monkeypatch.setenv("TO_SPAWN_TESTLAUF", "1")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))

    t1 = install_pruefung.ticket_melden(
        klon, sha=sha, exit_code=1, kennungen=["tests/test_a.py::test_x"], zeit="2026-11-25T10:00:00+01:00"
    )
    assert t1 == "#1"
    # Ticket #1 schließen
    daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    daten["issues"][0]["state"] = "closed"
    gh_stand.write_text(json.dumps(daten), encoding="utf-8")

    t2 = install_pruefung.ticket_melden(
        klon, sha=sha, exit_code=1, kennungen=["tests/test_a.py::test_x"], zeit="2026-11-25T11:00:00+01:00"
    )
    assert t2 == "#2"
    daten = json.loads(gh_stand.read_text(encoding="utf-8"))
    offene = [i for i in daten["issues"] if i["state"] == "open"]
    assert len(offene) == 1 and offene[0]["number"] == 2

