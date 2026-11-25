"""#757 (Linux-Teil / Kern): Nachstellung und Absicherung der Probe-1330-Beobachtungen.

Prüft alle fünf Linux-/Kern-Punkte aus #757 (aufgeteilt aus #291):
1. Force-Push auf ``wip/``-/``ticket/``-Zweig: ``erlaubt.py`` sperrt ihn als Klasse 2,
   während der Projekt-``pre-push``-Hook bewusst nur den Hauptzweig schützt.
2. Aufseher-Kommentar „Mensch nötig — Review angefordert, aber kein Beleg geschrieben“:
   auf ``main`` durch #530 gelöst (Wartezeit + Beleg-Prüfung in ``mensch_noetig.review_stand``).
3. Stop-Hook „Review fällig – Mensch nötig“: bewusster Endlos-Schutz nach 1 Block im
   Hinweis-Modus (``review/VERTRAG.md`` §8), schreibt aber in ``mensch_noetig.jsonl``.
4. ``crontab <langer Pfad>`` (Linux): Vixie-cron schneidet Pfade ab 99 Zeichen ab;
   ``aufpasser._crontab`` weist Dateipfad-Argumente ab und verlangt ``"-"`` (stdin).
5. „Ist der Fix drin?“: Commit-Abstammung scheitert nach Neu-Aufsetzen (Rebase/Cherry-Pick);
   maßgeblich ist der Datei-Inhalt (dokumentiert in ``SKILL.md``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, erlaubt, mensch_noetig

HILFEN_SPERRE = Path(__file__).parent / "hilfen" / "push_sperre_1442"


def _git(ordner: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ordner), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30,
        check=False,
    )


def _bash_eintrag(befehl: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": befehl}}],
        },
    }


# --- Punkt 1: Force-Push auf Ticket-/WIP-Zweig --------------------------------


@pytest.mark.parametrize(
    "befehl",
    [
        "git push --force origin wip/1482-probe",
        "git push -f origin wip/1482-probe",
        "git push --force-with-lease origin ticket/1482-probe",
        "git push origin +wip/1482-probe",
    ],
)
def test_erlaubt_sperrt_force_push_auch_auf_eigene_zweige(befehl: str) -> None:
    """Verlauf-Prüfung (``to_spawn/erlaubt.py``) lässt keinen Force-Push auf ``wip/`` oder ``ticket/`` durch."""
    grund = erlaubt.unbekannt(_bash_eintrag(befehl))
    assert grund is not None, f"Force-Push wurde als harmlos durchgelassen: {befehl}"


def test_erlaubt_laesst_normalen_wip_push_durch() -> None:
    assert erlaubt.unbekannt(_bash_eintrag("git push origin wip/1482-probe")) is None


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-Teil (#757): Bash-Pre-Push-Hook")
def test_pre_push_master_sperre_laesst_wip_force_push_durch(tmp_path: Path) -> None:
    """Nachstellung Probe 1330: ``pre-push-master-sperre.sh`` sperrt nur ``master``, nicht ``wip/* --force``."""
    quelle = tmp_path / "quelle"
    quelle.mkdir()
    assert _git(quelle, "init", "-q", "-b", "master").returncode == 0
    assert _git(quelle, "config", "user.email", "test@example.invalid").returncode == 0
    assert _git(quelle, "config", "user.name", "Test").returncode == 0
    (quelle / "README.md").write_text("basis\n", encoding="utf-8")
    assert _git(quelle, "add", "README.md").returncode == 0
    assert _git(quelle, "commit", "-q", "-m", "Basis").returncode == 0

    origin = tmp_path / "origin.git"
    assert _git(tmp_path, "clone", "-q", "--bare", str(quelle), str(origin)).returncode == 0
    klon = tmp_path / "klon"
    assert _git(tmp_path, "clone", "-q", str(origin), str(klon)).returncode == 0
    assert _git(klon, "config", "user.email", "test@example.invalid").returncode == 0
    assert _git(klon, "config", "user.name", "Test").returncode == 0

    hook = klon / ".git" / "hooks" / "pre-push"
    shutil.copy(HILFEN_SPERRE / "pre-push-master-sperre.sh", hook)
    hook.chmod(0o755)

    assert _git(klon, "checkout", "-q", "-b", "wip/1482-probe").returncode == 0
    (klon / "a.txt").write_text("stand-1\n", encoding="utf-8")
    assert _git(klon, "add", "a.txt").returncode == 0
    assert _git(klon, "commit", "-q", "-m", "Erster Stand").returncode == 0
    assert _git(klon, "push", "-q", "origin", "wip/1482-probe").returncode == 0

    (klon / "a.txt").write_text("stand-2\n", encoding="utf-8")
    assert _git(klon, "add", "a.txt").returncode == 0
    assert _git(klon, "commit", "-q", "--amend", "-m", "Geänderter Stand").returncode == 0

    env = {k: v for k, v in os.environ.items() if k not in ("MASTER_PUSH_ERLAUBT", "DEPLOY_SCHLANGE_MERGE")}
    auf_master = _git(klon, "push", "origin", "HEAD:refs/heads/master", env=env)
    assert auf_master.returncode != 0, "master-Sperre hat nicht gegriffen"

    force_wip = _git(klon, "push", "--force", "origin", "wip/1482-probe", env=env)
    assert force_wip.returncode == 0, f"Erwartet: master-Sperre prüft wip/* nicht ({force_wip.stderr})"


# --- Punkt 2: Aufseher-Kommentar „Mensch nötig“ während Review läuft ----------


def test_mensch_noetig_review_offen_wartet_waehrend_review_laeuft(tmp_path: Path) -> None:
    """Nachstellung Punkt 2: #530 verhindert voreiligen „Mensch nötig“-Ruf bei laufendem Review."""
    jetzt = datetime.now().astimezone()
    fp = "abcdef1234567890"
    frisch = {
        "zeit": (jetzt - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": str(tmp_path),
        "fingerprint": fp,
        "grund": "review_offen",
    }
    assert mensch_noetig.review_stand(frisch, jetzt) == "laeuft"

    beleg_dir = tmp_path / ".review"
    beleg_dir.mkdir()
    (beleg_dir / f"beleg-1234567-sitzung-{fp[:12]}.json").write_text("{}", encoding="utf-8")
    assert mensch_noetig.review_stand(frisch, jetzt) == "belegt"


# --- Punkt 3: Stop-Hook „Review fällig – Mensch nötig“ ------------------------


def test_stop_hook_hinweis_grenze_laesst_durch_und_schreibt_mensch_noetig(tmp_path: Path) -> None:
    """Nachstellung Punkt 3: 1. Stop blockt, 2. Stop lässt durch (Endlos-Schutz) und schreibt ``mensch_noetig.jsonl``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.invalid").returncode == 0
    assert _git(repo, "config", "user.name", "Test").returncode == 0
    (repo / "rechnen.py").write_text("def plus(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    assert _git(repo, "add", "rechnen.py").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "Basis").returncode == 0

    (repo / "rechnen.py").write_text("def plus(a: int, b: int) -> int:\n    return a + b + 1\n", encoding="utf-8")

    sitzungen = tmp_path / "sitzungen"
    sitzungen.mkdir()
    fremdtemp = tmp_path / "fremdtemp"
    fremdtemp.mkdir()
    log_pfad = tmp_path / "mensch_noetig.jsonl"
    basis_cfg = json.loads((SKILL / "review" / "config.standard.json").read_text(encoding="utf-8"))
    config_pfad = tmp_path / "review_config.json"
    config_pfad.write_text(json.dumps({**basis_cfg, "modus": "hinweis", "max_fix_runden": 2}), encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "REVIEW_SITZUNGEN": str(sitzungen),
            "REVIEW_MENSCH_NOETIG": str(log_pfad),
            "REVIEW_CONFIG": str(config_pfad),
            "REVIEW_LOG": str(tmp_path / "ausloeser.jsonl"),
            "REVIEW_HOOK_LOG": str(tmp_path / "hook-fehler.log"),
            "GIT_CEILING_DIRECTORIES": str(tmp_path),
            "TEMP": str(fremdtemp),
            "TMP": str(fremdtemp),
            "TMPDIR": str(fremdtemp),
            "TO_SPAWN_SPEC": "757",
            "TO_SPAWN_TICKET": "1482",
        }
    )
    env.pop("REVIEW_AUS", None)

    merker_ein = json.dumps(
        {
            "session_id": "probe-757",
            "cwd": str(repo),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "rechnen.py")},
        }
    )
    merker = subprocess.run(
        [sys.executable, str(SKILL / "review-hook" / "merker.py")],
        input=merker_ein,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert merker.returncode == 0, merker.stderr

    stop_ein = json.dumps({"session_id": "probe-757", "cwd": str(repo), "stop_hook_active": False})
    erster = subprocess.run(
        [sys.executable, str(SKILL / "review-hook" / "stop.py")],
        input=stop_ein,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert erster.returncode == 0, erster.stderr
    erg1 = json.loads(erster.stdout)
    assert erg1["decision"] == "block"

    zweiter = subprocess.run(
        [sys.executable, str(SKILL / "review-hook" / "stop.py")],
        input=stop_ein,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert zweiter.returncode == 0, zweiter.stderr
    erg2 = json.loads(zweiter.stdout)
    assert "decision" not in erg2
    assert "Review fällig – Mensch nötig" in erg2["systemMessage"]

    zeilen = log_pfad.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1
    eintrag = json.loads(zeilen[0])
    assert eintrag["grund"] == "review_offen"
    assert eintrag["spec"] == "757"
    assert eintrag["ticket"] == "1482"


# --- Punkt 4: crontab <langer Pfad> (Linux) -----------------------------------


def test_crontab_weist_dateipfad_ab_und_erlaubt_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """``aufpasser._crontab`` verbietet Dateipfad-Argumente (Vixie-cron 99-Zeichen-Falle) und erlaubt ``-l``/``-``."""
    aufrufe: list[tuple[list[str], str | None]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        aufrufe.append((cmd, kwargs.get("input")))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(aufpasser.subprocess, "run", _fake_run)

    langer_pfad = "/tmp/" + "a" * 110 + ".cron"
    with pytest.raises(ValueError, match="99"):
        aufpasser._crontab(langer_pfad)
    with pytest.raises(ValueError, match="99"):
        aufpasser._crontab("-u", "bau", langer_pfad)
    assert aufrufe == [], "Dateipfad-Aufruf darf crontab nicht starten"

    assert aufpasser._crontab("-l").returncode == 0
    assert aufpasser._crontab("-", eingabe="* * * * * true\n").returncode == 0
    assert aufpasser._crontab("-u", "bau", "-l").returncode == 0
    assert [cmd for cmd, _ in aufrufe] == [
        ["crontab", "-l"],
        ["crontab", "-"],
        ["crontab", "-u", "bau", "-l"],
    ]


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("crontab") is None,
    reason="Linux-Nachstellung braucht echtes crontab",
)
def test_linux_crontab_schneidet_langen_pfad_ab() -> None:
    """Live-Nachstellung auf Linux: ``crontab <pfad mit >100 Zeichen>`` schneidet den Pfad still ab."""
    langer_pfad = "/tmp/probe757_nichtda_" + "a" * 100 + ".cron"
    assert not Path(langer_pfad).exists()
    erg = subprocess.run(
        ["crontab", langer_pfad],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    assert erg.returncode != 0
    # Vixie-cron (Debian/Ubuntu) begrenzt MAX_FNAME auf 100 Bytes (99 Zeichen + NUL).
    if "No such file" in erg.stderr or "Datei oder Verzeichnis" in erg.stderr:
        assert langer_pfad not in erg.stderr, "Pfad wurde unerwartet vollständig gemeldet"
        assert langer_pfad[:99] in erg.stderr


# --- Punkt 5: Commit-Abstammung vs. Datei-Inhalt ------------------------------


def test_commit_abstammung_falsch_negativ_nach_rebase_inhalt_richtig(tmp_path: Path) -> None:
    """Nachstellung Punkt 5: Nach Cherry-Pick/Rebase versagt ``merge-base --is-ancestor``, der Datei-Inhalt trifft."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.invalid").returncode == 0
    assert _git(repo, "config", "user.name", "Test").returncode == 0
    (repo / "modul.py").write_text("WERT = 1\n", encoding="utf-8")
    assert _git(repo, "add", "modul.py").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "Basis").returncode == 0

    assert _git(repo, "checkout", "-q", "-b", "ticket/757-fix").returncode == 0
    (repo / "modul.py").write_text("WERT = 2\n", encoding="utf-8")
    assert _git(repo, "add", "modul.py").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "Fix (#757)").returncode == 0
    alt_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    assert _git(repo, "checkout", "-q", "main").returncode == 0
    (repo / "andere.txt").write_text("vorheriges Ticket\n", encoding="utf-8")
    assert _git(repo, "add", "andere.txt").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "Anderes Ticket").returncode == 0
    # Schlange setzt den Commit neu auf → neue Commit-SHA auf main.
    assert _git(repo, "cherry-pick", alt_sha).returncode == 0
    neu_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert neu_sha != alt_sha

    abstammung = _git(repo, "merge-base", "--is-ancestor", alt_sha, "main")
    assert abstammung.returncode == 1, "Alte SHA darf nach Neu-Aufsetzen kein Vorfahr von main sein"

    inhalt = _git(repo, "show", "main:modul.py")
    assert inhalt.returncode == 0 and "WERT = 2" in inhalt.stdout


def test_skill_md_dokumentiert_probe_1330_lehren() -> None:
    """``SKILL.md`` hält die Lehren aus #757 fest (Datei-Inhalt, Linux-crontab, Force-Push)."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Datei-Inhalt" in text and "Commit-Abstammung" in text
    assert "crontab -" in text and "99 Zeichen" in text
    assert "Force-Push" in text and "pre-push-master-sperre.sh" in text
