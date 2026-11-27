"""#1456 / #870 (Fehlerkatalog S16/P16): Warteschleifen finden die eigene Session nicht mehr.

``pgrep -f '<muster>'`` sucht in allen Kommandozeilen, auch in der eigenen: steht das
Muster im Sitzungs-Prompt oder in ``bash -c '… while pgrep -f …'``, trifft es den
Wartenden selbst, und die Schleife endet nie. Gewartet wird deshalb nur über
``prozessbaum.laufende`` / ``prozessbaum.warte_auf_lauf`` (CLI ``to_spawn.py
warte-lauf``); die schließen die eigene PID samt Vorfahren aus.

Der Prüf-Check bricht den Build, sobald in Skripten, Prompt-Vorlagen oder Doku des
Repos wieder ein rohes ``pgrep -f`` steht. Die einzige erlaubte Stelle für die
Muster-Suche ist ``to_spawn/prozessbaum.py`` (``laufende``) — sie liest ``/proc``
und braucht gar kein ``pgrep``.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from to_spawn import prozessbaum

REPO = Path(__file__).resolve().parents[1]

#: Ausführbarer oder vorgelesener Text: Shell-/PowerShell-Skripte, Prompt-Vorlagen, Doku.
GEPRUEFTE_ENDUNGEN = {".sh", ".ps1", ".json", ".md", ".txt"}
#: Belege und Lauf-Mitschnitte zitieren alte Befehle, sie werden nicht ausgeführt.
AUSGENOMMEN = ("docs/verify-hard/", "tests/weg/", ".git/")
#: Ausnahme von der Ausnahme: Weg-Test-Skripte laufen echt und zählen mit.
WEG_SKRIPT = re.compile(r"^tests/weg/[^/]+\.(sh|ps1)$")
#: ``pgrep`` mit ``-f`` in beliebiger Kurzform (``-f``, ``-af``, ``-fc`` …).
ROHES_PGREP_F = re.compile(r"\bpgrep\b[^|;&\n]*?\s-[a-zA-Z]*f")

linux = pytest.mark.skipif(sys.platform == "win32", reason="liest /proc")


def _dateien(root: Path) -> list[Path]:
    """Von git verwaltete (und neue, nicht ignorierte) Dateien; ohne git der Baum ohne `.git/`, `.review/`."""
    try:
        ausgabe = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout.decode("utf-8", errors="replace")
        dateien = sorted(root / n for n in ausgabe.split("\0") if n)
        if dateien:
            return dateien
    except (OSError, subprocess.SubprocessError):
        pass
    # git ohne Treffer (z. B. Ordner liegt in äußerem Repo, das ihn ignoriert) = wie git-Fehler
    return sorted(d for d in root.rglob("*") if not {".git", ".review"} & set(d.relative_to(root).parts))


def _fundstellen(root: Path = REPO) -> list[str]:
    raus = []
    for datei in _dateien(root):
        rel = datei.relative_to(root).as_posix()
        if not datei.is_file() or datei.suffix not in GEPRUEFTE_ENDUNGEN:
            continue
        if rel.startswith(AUSGENOMMEN) and not WEG_SKRIPT.match(rel):
            continue
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ROHES_PGREP_F.search(zeile):
                raus.append(f"{rel}:{nr}: {zeile.strip()[:120]}")
    return raus


def test_kein_rohes_pgrep_f_in_skripten_und_vorlagen() -> None:
    assert "to_spawn.py" in {d.name for d in _dateien(REPO)}, (
        "Prüf-Check hat nichts geprüft (to_spawn.py fehlt in der Dateiliste)"
    )
    funde = _fundstellen()
    assert funde == [], (
        "Rohes `pgrep -f` findet die eigene Session (S16/P16) — stattdessen "
        "`python ~/.claude/skills/to-spawn/to_spawn.py warte-lauf '<regex>'`:\n" + "\n".join(funde)
    )


def test_check_erkennt_die_kurzformen() -> None:
    for treffer in ("pgrep -f x", "pgrep -af x", "pgrep -fc x", "pgrep -u bau -f x"):
        assert ROHES_PGREP_F.search(treffer), treffer
    for kein in ("pgrep -x claude", "pgrep -P 12", "pgrep -c -x claude"):
        assert not ROHES_PGREP_F.search(kein), kein


def test_vorlage_nennt_die_warte_hilfe() -> None:
    import json

    vorlage = json.loads((REPO / "repo-scripts/_default.json").read_text(encoding="utf-8"))["prompt_template"]
    assert "to_spawn.py warte-lauf" in vorlage


@linux
def test_rohes_pgrep_f_findet_sich_selbst_schleife_endet_nie() -> None:
    """Rot-Beweis der Falle: das Muster steht nur in der eigenen Kommandozeile."""
    marke = f"lauf-{uuid.uuid4().hex}"
    fertig = subprocess.run(
        ["timeout", "3", "bash", "-c", f"while pgrep -f '{marke}' >/dev/null; do sleep 0.2; done"],
        check=False,
    )
    assert fertig.returncode == 124  # nur der Deckel beendet die Schleife


@linux
def test_hilfe_findet_eigene_kommandozeile_nicht() -> None:
    """Gleiches Muster in der eigenen Kommandozeile und in der des Elternprozesses: kein Treffer."""
    marke = f"lauf-{uuid.uuid4().hex}"
    code = f"from to_spawn import prozessbaum; import sys; sys.exit(len(prozessbaum.laufende({marke!r})))"
    fertig = subprocess.run(
        ["timeout", "10", "bash", "-c", f'{sys.executable} -c "{code}" # {marke}'],
        cwd=REPO,
        check=False,
    )
    assert fertig.returncode == 0


@linux
def test_hilfe_findet_fremden_lauf_und_wartet_bis_er_endet() -> None:
    marke = f"lauf-{uuid.uuid4().hex}"
    lauf = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)", marke])
    try:
        assert lauf.pid in prozessbaum.laufende(marke)
        beginn = time.monotonic()
        assert prozessbaum.warte_auf_lauf(marke, frist_s=30, takt_s=0.2) is True
        assert time.monotonic() - beginn < 10
    finally:
        lauf.kill()
        lauf.wait()
    assert prozessbaum.laufende(marke) == []


@linux
def test_hilfe_meldet_frist_abgelaufen() -> None:
    marke = f"lauf-{uuid.uuid4().hex}"
    lauf = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marke])
    try:
        assert prozessbaum.warte_auf_lauf(marke, frist_s=0.6, takt_s=0.2) is False
    finally:
        lauf.kill()
        lauf.wait()


@linux
def test_cli_warte_lauf_ohne_lauf_exit_0_und_mit_frist_0_exit_1() -> None:
    marke = f"lauf-{uuid.uuid4().hex}"
    cli = [sys.executable, str(REPO / "to_spawn.py"), "warte-lauf"]
    leer = subprocess.run([*cli, marke], capture_output=True, text=True, timeout=30, check=False)
    assert leer.returncode == 0, leer.stderr
    lauf = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marke])
    try:
        voll = subprocess.run([*cli, "--frist", "0", marke], capture_output=True, text=True, timeout=30, check=False)
        assert voll.returncode == 1, voll.stderr
        assert str(lauf.pid) in voll.stdout.split()
    finally:
        lauf.kill()
        lauf.wait()


def test_review_ordner_und_fallback_baum(tmp_path: Path) -> None:
    """Ohne git: Datei-Baum, aber `.review/` (Belege mit Zitaten) bleibt außen; echte Funde bleiben."""
    (tmp_path / ".review").mkdir()
    (tmp_path / ".review" / "beleg.json").write_text('{"cmd": "pgrep -af x"}\n', encoding="utf-8")
    assert _fundstellen(tmp_path) == []
    (tmp_path / "warte.sh").write_text("while pgrep -f x; do sleep 1; done\n", encoding="utf-8")
    assert len(_fundstellen(tmp_path)) == 1


def test_git_repo_prueft_nur_verwaltete_dateien(tmp_path: Path) -> None:
    """Mit git: ignoriertes `.review/` zählt nicht, verwaltete und neue Dateien zählen."""
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True)
    (tmp_path / ".gitignore").write_text(".review/\n", encoding="utf-8")
    (tmp_path / ".review").mkdir()
    (tmp_path / ".review" / "beleg.json").write_text('{"cmd": "pgrep -af x"}\n', encoding="utf-8")
    (tmp_path / "warte.sh").write_text("pgrep -f x\n", encoding="utf-8")
    funde = _fundstellen(tmp_path)
    assert len(funde) == 1 and funde[0].startswith("warte.sh:1")


def test_ignorierender_aeusserer_git_ordner_prueft_trotzdem(tmp_path: Path) -> None:
    """Skill-Ordner liegt in äußerem Repo, das ihn ignoriert: git meldet Exit 0 mit leerer Liste."""
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / ".gitignore").write_text("skill/\n", encoding="utf-8")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "warte.sh").write_text("while pgrep -f x; do sleep 1; done\n", encoding="utf-8")
    assert len(_fundstellen(skill)) == 1
