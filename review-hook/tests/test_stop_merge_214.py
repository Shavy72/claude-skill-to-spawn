# ruff: noqa: F811  (Fixtures aus test_hooks importiert)
"""#214 Teil B: Stop-Hook nach Merge von main, -hand.json-Belege und aktiver Helfer im Klon.

Echte temporäre Git-Repos, echte Hook-Aufrufe (merker.py, commit.py, stop.py), Windows-tauglich.
Prüft:
- Merge-Umfang (Variante A, ``stop.merge_umfang``): 30 fremde Dateien + 1 Konflikt, Merge ohne
  Konflikt, Konflikt + eigene Änderung danach, offener Merge, eigene Änderung vor dem Merge,
  Fremdzweig-Merge (nicht main) und Merge in main selbst bleiben voll im Umfang.
- Hand-Belege ``*-hand.json``: gültig für geprüften Stand, ungültig bei älterem SHA, späterer
  Änderung oder fehlender Datei; roter Hand-Beleg blockt im Modus scharf.
- Laufender Helfer im Klon: kein Block, solange Hintergrund-Helfer laut Transkript noch läuft;
  sobald er zurück ist, mahnt der Hook wieder.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

from test_hooks import Umgebung, git, u  # noqa: F401
from test_stop_runde5 import _beleg_echt


def _basis_repo(tmp_path: Path, hauptzweig: str = "main", name: str = "repo") -> Path:
    pfad = tmp_path / name
    pfad.mkdir()
    git(pfad, "init", "-q")
    git(pfad, "config", "user.name", "Test")
    git(pfad, "config", "user.email", "test@example.invalid")
    git(pfad, "config", "core.autocrlf", "false")
    git(pfad, "checkout", "-q", "-b", hauptzweig)
    (pfad / "konflikt.py").write_text("import os\n\ndef wert():\n    return 1\n", encoding="utf-8")
    (pfad / "rechnen.py").write_text("def eins():\n    return 1\n", encoding="utf-8")
    git(pfad, "add", "-A")
    git(pfad, "commit", "-q", "-m", "start")
    return pfad


def _main_erweitern(repo: Path, *, anzahl: int = 30, mit_konflikt: bool = True, hauptzweig: str = "main") -> str:
    """Auf ``hauptzweig`` 30 fremde Python-Dateien (und optional Konflikt-Änderung) committen."""
    git(repo, "checkout", "-q", hauptzweig)
    for i in range(1, anzahl + 1):
        (repo / f"fremd_{i:02d}.py").write_text(
            f"def f_{i:02d}():\n    return {i}\n",
            encoding="utf-8",
        )
    if mit_konflikt:
        (repo / "konflikt.py").write_text(
            "import os\nimport sys\n\ndef wert():\n    return 2\n",
            encoding="utf-8",
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main: 30 fremde Dateien")
    return git(repo, "rev-parse", "HEAD").strip()


def _zweig_anlegen(repo: Path, zweig: str = "ticket/214-bau", *, mit_konflikt: bool = True) -> str:
    """Arbeitszweig vor dem main-Fortschritt anlegen (optional mit konkurrierender konflikt.py)."""
    git(repo, "checkout", "-q", "-b", zweig)
    if mit_konflikt:
        (repo / "konflikt.py").write_text(
            "import os\nimport json\n\ndef wert():\n    return 10\n",
            encoding="utf-8",
        )
        git(repo, "add", "konflikt.py")
        git(repo, "commit", "-q", "-m", "zweig: konflikt.py geändert")
    return git(repo, "rev-parse", "HEAD").strip()


def _merge_mit_konflikt_starten(repo: Path, ziel: str = "main") -> None:
    """``git merge <ziel>`` mit erwartetem Konflikt ausführen (ohne subprocess.run im Test)."""
    import _hookbasis

    ergebnis = _hookbasis.befehl(["git", "-C", str(repo), "merge", "--no-edit", ziel])
    assert ergebnis.returncode != 0, f"Konflikt erwartet, aber Merge war sauber: {ergebnis.stdout}"


def _konflikt_loesen(u: Umgebung, repo: Path, sid: str = "s1") -> None:
    (repo / "konflikt.py").write_text(
        "import os\nimport sys\nimport json\n\ndef wert():\n    return 12\n",
        encoding="utf-8",
    )
    u.merke(repo, "konflikt.py", sid=sid)


def _merge_committen(u: Umgebung, repo: Path) -> None:
    git(repo, "add", "konflikt.py")
    u.commit(repo, "git commit --no-edit")
    git(repo, "commit", "-q", "--no-edit")


def _files_aus_grund(reason: str) -> list[str]:
    assert "--files " in reason, reason
    return reason.split("--files ", 1)[1].split()


# ------------------------------------------------------------------ Merge-Umfang (5+ Fälle)


def test_merge_30_fremde_dateien_und_ein_konflikt_nur_konflikt_im_umfang(u: Umgebung, tmp_path: Path) -> None:
    """Abnahme 1: Zweig + main mit 30 fremden Dateien + ein Konflikt → Umfang = genau die Konflikt-Datei."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=True)
    main_sha = _main_erweitern(repo, anzahl=30, mit_konflikt=True)
    git(repo, "checkout", "-q", "ticket/214-bau")

    _merge_mit_konflikt_starten(repo)
    _konflikt_loesen(u, repo)
    _merge_committen(u, repo)

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert _files_aus_grund(aus["reason"]) == ["konflikt.py"], aus["reason"]
    assert "Stufe klein" in aus["reason"], aus["reason"]
    assert f"--base {main_sha}" in aus["reason"], aus["reason"]
    assert "fremd_01.py" not in aus["reason"]


def test_merge_ohne_konflikt_verlangt_keinen_beleg(u: Umgebung, tmp_path: Path) -> None:
    """Abnahme 2: Merge ohne Konflikt und ohne eigene Änderung → kein neuer Beleg verlangt."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=False)
    _main_erweitern(repo, anzahl=30, mit_konflikt=False)
    git(repo, "checkout", "-q", "ticket/214-bau")

    # Eigener älterer Commit vor Sitzungsstart, damit git merge einen echten Merge-Commit baut
    (repo / "alt.py").write_text("ALT = 1\n", encoding="utf-8")
    git(repo, "add", "alt.py")
    git(repo, "commit", "-q", "-m", "vor Sitzung")

    # Simuliert: commit.py merkt bei --no-commit + git commit alle 30 fremden Dateien
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "main")
    u.commit(repo, "git commit --no-edit")
    git(repo, "commit", "-q", "--no-edit")

    assert u.stop(repo) is None


def test_merge_ohne_konflikt_nach_belegter_aenderung_braucht_keinen_neuen_beleg(
    u: Umgebung, tmp_path: Path
) -> None:
    """Abnahme 2b: Eigene Änderung war schon belegt, danach sauberer Merge von main → kein neuer Beleg."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=False)
    _main_erweitern(repo, anzahl=30, mit_konflikt=False)
    git(repo, "checkout", "-q", "ticket/214-bau")

    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    _beleg_echt(u, repo)
    git(repo, "commit", "-q", "-am", "bau")
    assert u.stop(repo) is None

    git(repo, "merge", "-q", "--no-commit", "--no-ff", "main")
    u.commit(repo, "git commit --no-edit")
    git(repo, "commit", "-q", "--no-edit")

    assert u.stop(repo) is None


def test_merge_konflikt_und_eigene_aenderung_danach(u: Umgebung, tmp_path: Path) -> None:
    """Abnahme 3: Merge + eigene Änderung an Datei X danach → Umfang = Konflikt-Dateien + X."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=True)
    _main_erweitern(repo, anzahl=30, mit_konflikt=True)
    git(repo, "checkout", "-q", "ticket/214-bau")

    _merge_mit_konflikt_starten(repo)
    _konflikt_loesen(u, repo)
    _merge_committen(u, repo)

    (repo / "zusatz.py").write_text("def neu():\n    return 42\n", encoding="utf-8")
    u.merke(repo, "zusatz.py")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert set(_files_aus_grund(aus["reason"])) == {"konflikt.py", "zusatz.py"}, aus["reason"]
    assert "fremd_01.py" not in aus["reason"]


def test_offener_merge_nur_konflikt_im_umfang(u: Umgebung, tmp_path: Path) -> None:
    """Abnahme 4 (Erkundung): offener Merge (MERGE_HEAD, noch nicht committet) → nur Konflikt-Datei."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=True)
    _main_erweitern(repo, anzahl=30, mit_konflikt=True)
    git(repo, "checkout", "-q", "ticket/214-bau")

    # Sitzung lief bereits vor dem Merge (mtime der 30 Merge-Dateien liegt nach sitzungsstart)
    u.merke(repo, "konflikt.py")
    time.sleep(0.05)

    _merge_mit_konflikt_starten(repo)
    _konflikt_loesen(u, repo)
    # Bewusst noch NICHT committet: MERGE_HEAD liegt vor, 30 fremde Dateien sind im Arbeitsbaum neu

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert _files_aus_grund(aus["reason"]) == ["konflikt.py"], aus["reason"]
    assert "fremd_01.py" not in aus["reason"]


def test_offener_merge_ohne_konflikt_still(u: Umgebung, tmp_path: Path) -> None:
    """Offener sauberer Merge (--no-commit, kein Konflikt, keine eigene Änderung) → still."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=False)
    _main_erweitern(repo, anzahl=30, mit_konflikt=False)
    git(repo, "checkout", "-q", "ticket/214-bau")

    u.merke(repo, "rechnen.py")
    time.sleep(0.05)
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "main")

    assert u.stop(repo) is None


def test_merge_von_master_funktioniert_wie_main(u: Umgebung, tmp_path: Path) -> None:
    """Hauptzweig darf auch ``master`` heißen."""
    repo = _basis_repo(tmp_path, hauptzweig="master")
    _zweig_anlegen(repo, mit_konflikt=True)
    _main_erweitern(repo, anzahl=30, mit_konflikt=True, hauptzweig="master")
    git(repo, "checkout", "-q", "ticket/214-bau")

    _merge_mit_konflikt_starten(repo, ziel="master")
    _konflikt_loesen(u, repo)
    _merge_committen(u, repo)

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert _files_aus_grund(aus["reason"]) == ["konflikt.py"], aus["reason"]
    assert "fremd_01.py" not in aus["reason"]


def test_eigene_aenderung_vor_merge_bleibt_im_umfang(u: Umgebung, tmp_path: Path) -> None:
    """Abnahme 5 (Erkundung): eigene unbewiesene Änderung vor dem Merge bleibt im Umfang."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, mit_konflikt=True)
    _main_erweitern(repo, anzahl=30, mit_konflikt=True)
    git(repo, "checkout", "-q", "ticket/214-bau")

    (repo / "eigen_vor.py").write_text("def vor():\n    return 7\n", encoding="utf-8")
    u.merke(repo, "eigen_vor.py")
    git(repo, "add", "eigen_vor.py")
    git(repo, "commit", "-q", "-m", "eigene Arbeit vor Merge")

    _merge_mit_konflikt_starten(repo)
    _konflikt_loesen(u, repo)
    _merge_committen(u, repo)

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert set(_files_aus_grund(aus["reason"])) == {"eigen_vor.py", "konflikt.py"}, aus["reason"]
    assert "fremd_01.py" not in aus["reason"]


def test_merge_eines_fremden_feature_zweigs_bleibt_voll_im_umfang(u: Umgebung, tmp_path: Path) -> None:
    """Sicherheit: Merge eines fremden Feature-Zweigs (nicht auf main) verengt den Umfang NICHT."""
    repo = _basis_repo(tmp_path)
    _zweig_anlegen(repo, zweig="ticket/214-bau", mit_konflikt=False)
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feature/fremd")
    (repo / "fremd_feature.py").write_text("def x():\n    return 9\n", encoding="utf-8")
    git(repo, "add", "fremd_feature.py")
    git(repo, "commit", "-q", "-m", "fremdes feature")

    git(repo, "checkout", "-q", "ticket/214-bau")
    (repo / "alt.py").write_text("ALT = 1\n", encoding="utf-8")
    git(repo, "add", "alt.py")
    git(repo, "commit", "-q", "-m", "vor Sitzung")

    git(repo, "merge", "-q", "--no-commit", "--no-ff", "feature/fremd")
    u.commit(repo, "git commit --no-edit")
    git(repo, "commit", "-q", "--no-edit")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "fremd_feature.py" in _files_aus_grund(aus["reason"]), aus["reason"]


def test_merge_auf_main_selbst_verengt_nicht(u: Umgebung, tmp_path: Path) -> None:
    """Sicherheit: Merge eines Feature-Zweigs IN main (wenn HEAD auf main steht) bleibt voll im Umfang."""
    repo = _basis_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature/neu")
    (repo / "feature_neu.py").write_text("def f():\n    return 5\n", encoding="utf-8")
    git(repo, "add", "feature_neu.py")
    git(repo, "commit", "-q", "-m", "feature")

    git(repo, "checkout", "-q", "main")
    (repo / "main_alt.py").write_text("M = 1\n", encoding="utf-8")
    git(repo, "add", "main_alt.py")
    git(repo, "commit", "-q", "-m", "main vor Sitzung")

    git(repo, "merge", "-q", "--no-commit", "--no-ff", "feature/neu")
    u.commit(repo, "git commit --no-edit")
    git(repo, "commit", "-q", "--no-edit")

    aus = u.stop(repo)

    assert aus is not None and aus["decision"] == "block"
    assert "feature_neu.py" in _files_aus_grund(aus["reason"]), aus["reason"]


# ------------------------------------------------------------------ -hand.json Belege


@pytest.mark.parametrize("dateiname", ["beleg-{sha}-hand.json", "beleg-{sha}-sitzung-hand.json"])
def test_hand_beleg_wird_fuer_geprueften_stand_anerkannt(u: Umgebung, tmp_path: Path, dateiname: str) -> None:
    """Zusatz 3: Belege mit Endung ``-hand.json`` gelten für den geprüften Stand."""
    repo = _basis_repo(tmp_path)
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    git(repo, "commit", "-q", "-am", "bau")
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()

    beleg = repo / ".review" / dateiname.format(sha=sha)
    beleg.parent.mkdir(exist_ok=True)
    beleg.write_text(
        json.dumps({"sha": sha, "status": "gruen", "dateien": ["rechnen.py"], "befunde": []}),
        encoding="utf-8",
    )

    assert u.stop(repo) is None


def test_hand_beleg_fuer_alten_commit_gilt_nicht(u: Umgebung, tmp_path: Path) -> None:
    repo = _basis_repo(tmp_path)
    alt_sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    git(repo, "commit", "-q", "-am", "bau")

    beleg = repo / ".review" / f"beleg-{alt_sha}-hand.json"
    beleg.parent.mkdir(exist_ok=True)
    beleg.write_text(
        json.dumps({"sha": alt_sha, "status": "gruen", "dateien": ["rechnen.py"], "befunde": []}),
        encoding="utf-8",
    )

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"


def test_hand_beleg_ungueltig_nach_weiterer_aenderung_im_arbeitsbaum(u: Umgebung, tmp_path: Path) -> None:
    repo = _basis_repo(tmp_path)
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    git(repo, "commit", "-q", "-am", "bau")
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()

    beleg = repo / ".review" / f"beleg-{sha}-hand.json"
    beleg.parent.mkdir(exist_ok=True)
    beleg.write_text(
        json.dumps({"sha": sha, "status": "gruen", "dateien": ["rechnen.py"], "befunde": []}),
        encoding="utf-8",
    )
    # Beleg älter stempeln, dann Datei im Arbeitsbaum ändern
    alt_zeit = time.time() - 10
    os.utime(beleg, (alt_zeit, alt_zeit))
    (repo / "rechnen.py").write_text("def eins():\n    return 99\n", encoding="utf-8")

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"


def test_hand_beleg_ohne_neue_datei_in_dateiliste_gilt_nicht(u: Umgebung, tmp_path: Path) -> None:
    """Deckt ``dateien`` im Hand-Beleg nicht alle geänderten Dateien ab, verlangt der Hook weiter Review."""
    repo = _basis_repo(tmp_path)
    (repo / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    (repo / "extra.py").write_text("def extra():\n    return 3\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    u.merke(repo, "extra.py")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "bau")
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()

    beleg = repo / ".review" / f"beleg-{sha}-hand.json"
    beleg.parent.mkdir(exist_ok=True)
    beleg.write_text(
        json.dumps({"sha": sha, "status": "gruen", "dateien": ["rechnen.py"], "befunde": []}),
        encoding="utf-8",
    )

    aus = u.stop(repo)
    assert aus is not None and aus["decision"] == "block"


def test_hand_beleg_rot_blockt_im_modus_scharf(u: Umgebung, tmp_path: Path) -> None:
    u.modus("scharf", max_fix_runden=2)
    repo = _basis_repo(tmp_path)
    (repo / "rechnen.py").write_text("def eins():\n    return 1\n\n\ndef zwei():\n    return 2\n", encoding="utf-8")
    u.merke(repo, "rechnen.py")
    git(repo, "commit", "-q", "-am", "bau")
    sha = git(repo, "rev-parse", "--short=7", "HEAD").strip()

    beleg = repo / ".review" / f"beleg-{sha}-hand.json"
    beleg.parent.mkdir(exist_ok=True)
    beleg.write_text(
        json.dumps({"sha": sha, "status": "rot", "dateien": ["rechnen.py"], "befunde": []}),
        encoding="utf-8",
    )

    aus = u.stop(repo, aktiv=True)
    assert aus is not None and aus["decision"] == "block"
    assert "Beleg rot" in aus["reason"]


# ------------------------------------------------------------------ Helfer baut noch im Klon


def _transkript_schreiben(pfad: Path, zeilen: list[dict]) -> None:
    pfad.write_text("\n".join(json.dumps(z) for z in zeilen) + "\n", encoding="utf-8")


def _agent_start_zeilen(tool_use_id: str, agent_id: str, prompt: str) -> list[dict]:
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "Agent",
                        "input": {"prompt": prompt, "run_in_background": True},
                    }
                ]
            },
        },
        {
            "type": "user",
            "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": agent_id},
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"Async agent launched successfully. agentId: {agent_id}",
                    }
                ]
            },
        },
    ]


def _agent_ende_zeile(agent_id: str) -> dict:
    return {
        "type": "user",
        "message": {
            "content": (
                f"<task-notification>\n<task-id>{agent_id}</task-id>\n"
                "<status>completed</status>\n<summary>fertig</summary>\n</task-notification>"
            )
        },
    }


def test_helfer_baut_noch_im_klon_kein_block_erst_nach_rueckkehr(u: Umgebung, tmp_path: Path) -> None:
    """Zusatz 4: kein Mahnen für einen Klon, in dem ein Hintergrund-Helfer noch baut;
    erst nach dessen Rückkehr mahnt der Hook."""
    klon = _basis_repo(tmp_path, name="wt-214")
    (klon / "rechnen.py").write_text("def eins():\n    return 2\n", encoding="utf-8")
    u.merke(klon, "rechnen.py")

    transkript = tmp_path / "sitzung.jsonl"
    zeilen = _agent_start_zeilen("toolu_1", "agent_bau_1", f"Baue im Klon {klon.resolve().as_posix()} das Ticket.")
    _transkript_schreiben(transkript, zeilen)

    # Helfer baut noch → Hook bleibt still
    assert u.stop(klon, transcript=transkript) is None

    # Helfer ist zurück → Hook mahnt jetzt
    _transkript_schreiben(transkript, [*zeilen, _agent_ende_zeile("agent_bau_1")])
    aus = u.stop(klon, transcript=transkript)
    assert aus is not None and aus["decision"] == "block"
    assert "rechnen.py" in aus["reason"]


def test_helfer_baut_ueber_agent_id_im_merker_ohne_pfad_im_prompt(u: Umgebung, tmp_path: Path) -> None:
    """Auch wenn der Prompt den Worktree-Pfad nicht nennt (z. B. isolation=worktree),
    erkennt der Hook den aktiven Helfer über agent_id aus merker.py."""
    klon = _basis_repo(tmp_path, name="wt-iso")
    (klon / "rechnen.py").write_text("def eins():\n    return 3\n", encoding="utf-8")
    u.merke(klon, "rechnen.py", agent_id="agent_iso_9")

    transkript = tmp_path / "sitzung.jsonl"
    zeilen = _agent_start_zeilen("toolu_2", "agent_iso_9", "Baue das Ticket im isolierten Worktree.")
    _transkript_schreiben(transkript, zeilen)

    assert u.stop(klon, transcript=transkript) is None

    _transkript_schreiben(transkript, [*zeilen, _agent_ende_zeile("agent_iso_9")])
    aus = u.stop(klon, transcript=transkript)
    assert aus is not None and aus["decision"] == "block"
