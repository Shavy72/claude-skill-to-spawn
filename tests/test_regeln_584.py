"""#584 (E20, E21): Regeltrennung global/Skill/Projekt und Prüf-Skript vor Regeltext.

Prüft:
1. Die E21-Regel („wiederholter Agent-Fehler → Aufbau, Lint-Regel oder Prüf-Skript vor Regeltext“)
   steht im globalen Regel-Block (``kette/global/strategisches-design.md``) und wird über
   ``install.sh`` sauber in eine Wegwerf-``CLAUDE.md`` installiert.
2. Ausgelieferte Regel-Blöcke (``kette/global/*.md``, ``SKILL.md``, ``skripte/wache.py``) enthalten
   keine projektspezifischen Reste fremder Repos (z. B. Flowbase-Pfad, DuoPlus-Server-Repo).
3. Das Prüf-Skript ``to_spawn.regel_pruefung`` (CLI ``to_spawn.py regeln-pruefen`` und
   ``python -m to_spawn.regel_pruefung``) erkennt doppelte ``to-spawn``-Ablauf-Texte außerhalb des
   Skills — sowohl wörtliche Kopien (Shingles aus den kanonischen Skill-Quellen) als auch
   umformulierte Ablauf-Blöcke (Signal-Cluster) — und bleibt bei sauberen Projekt-Regeln sowie
   kurzen Querverweisen grün.
4. Die Bestandsaufnahme in ``docs/REGELTRENNUNG_584.md`` ordnet jede Regel genau einmal einem Ort
   (global/Skill/Projekt) zu, weist die Zählung vorher/nachher ohne Regelverlust aus und benennt
   die Befunde für App #1604.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import anleitung, globale_regeln

STRATEGISCH = SKILL / "kette" / "global" / "strategisches-design.md"
BESTANDSAUFNAHME = SKILL / "docs" / "REGELTRENNUNG_584.md"


def _umgebung(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_PY": sys.executable,
        }
    )
    return env


def test_e21_regel_im_globalen_block_und_installiert_per_install_sh(tmp_path: Path) -> None:
    """E21 steht im globalen Block und landet per ``install.sh`` in der globalen ``CLAUDE.md``."""
    inhalt = STRATEGISCH.read_text(encoding="utf-8")
    klein = inhalt.lower()
    assert "wiederholter agent-fehler" in klein
    assert "aufbau" in klein
    assert "lint-regel" in klein
    assert "prüf-skript" in klein
    assert "vor regeltext" in klein
    assert "E21" in inhalt
    # Bestehende Kernregel „Tiefe Module“ bleibt erhalten (kein Verlust).
    assert "Tiefe Module" in inhalt
    # Repo-spezifischer Pfad eines anderen Projekts ist raus.
    assert "Flowbase" not in inhalt
    assert "tiefe-module.html" not in inhalt

    # Installation gegen Wegwerf-HOME über install.sh.
    (tmp_path / "CLAUDE.md").write_bytes(b"# Eigene Regeln\n")
    lauf = subprocess.run(
        [
            "bash",
            str(SKILL / "install.sh"),
            "--skills",
            str(tmp_path / "skills"),
            "--claude-md",
            str(tmp_path / "CLAUDE.md"),
        ],
        env=_umgebung(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    installiert = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Wiederholter Agent-Fehler" in installiert
    assert "Lint-Regel" in installiert
    assert "Prüf-Skript vor Regeltext" in installiert
    assert globale_regeln.pruefe(tmp_path / "CLAUDE.md")


def test_ausgelieferte_regeln_ohne_fremde_projekt_reste() -> None:
    """E20: Keine repo-spezifischen Reste in den globalen Blöcken, ``SKILL.md`` und ``wache.py``."""
    global_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted((SKILL / "kette" / "global").glob("*.md")))
    for verboten in ("Flowbase", "tiefe-module.html", "duoplus", "safe_deploy_vps"):
        assert verboten.lower() not in global_text.lower(), f"Projekt-Rest {verboten!r} in kette/global/"

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for verboten in (
        "netcup-Bau-Server",
        "~/duoplus-management",
        "docs/BAU_SERVER.md",
        "reference_cmo_browser_beweis_playwright",
        "DuoPlus: aus",
        "safe_deploy_vps.sh",
    ):
        assert verboten not in skill_text, f"Projekt-Rest {verboten!r} in SKILL.md"

    wache_text = (SKILL / "skripte" / "wache.py").read_text(encoding="utf-8")
    assert "safe_deploy_vps.sh" not in wache_text


def test_regel_pruefung_skill_repo_gruen() -> None:
    """Das Skill-Repo selbst hat keine doppelten ``to-spawn``-Ablauf-Texte außerhalb des Skills."""
    from to_spawn import regel_pruefung

    ergebnis = regel_pruefung.pruefe_skill_repo(SKILL)
    assert ergebnis.ok, f"Unerwartete Treffer im Skill-Repo: {ergebnis.funde}"
    assert ergebnis.gepruefte_dateien >= 5


def test_regel_pruefung_rot_beweis_woertliche_kopie_in_repo_claude_md(tmp_path: Path) -> None:
    """Rot-Beweis (#584): Absichtliche Kopie aus ``anleitung``/``SKILL.md`` in ``CLAUDE.md`` → rot."""
    from to_spawn import regel_pruefung

    (tmp_path / "CLAUDE.md").write_text(
        f"# Projektregeln\n\n- Tests mit pytest ausführen.\n\n## Kopierter Ablauf\n{anleitung.MINDSET}\n",
        encoding="utf-8",
    )
    ergebnis = regel_pruefung.pruefe_repo(tmp_path)
    assert not ergebnis.ok
    assert any(f.datei == "CLAUDE.md" and f.art == "kopie" for f in ergebnis.funde), ergebnis.funde


def test_regel_pruefung_rot_beweis_woertliche_kopie_harte_regeln(tmp_path: Path) -> None:
    """Rot-Beweis (#584): Kopie aus ``SKILL.md`` (Harte Regeln) in ``.claude/rules/`` → rot."""
    from to_spawn import regel_pruefung

    regeln_dir = tmp_path / ".claude" / "rules"
    regeln_dir.mkdir(parents=True)
    (regeln_dir / "spawn.md").write_text(
        "# Regeln\n\n"
        "Nie `bau.py` beenden ohne vorher `sessions <S>`. Nur Zustand `wartet` darf gekillt werden. "
        "Ein gekilltes `bau.py` hinterlässt sein Claude-Kind verwaist.\n",
        encoding="utf-8",
    )
    ergebnis = regel_pruefung.pruefe_repo(tmp_path)
    assert not ergebnis.ok
    assert any(f.datei == ".claude/rules/spawn.md" for f in ergebnis.funde), ergebnis.funde


def test_regel_pruefung_rot_beweis_umformulierter_ablauf_cluster(tmp_path: Path) -> None:
    """Rot-Beweis (#584): Auch eine umformulierte Ablaufbeschreibung von ``to-spawn`` fällt auf."""
    from to_spawn import regel_pruefung

    (tmp_path / "AGENTS.md").write_text(
        "# Arbeitsweise\n\n"
        "## Eigenbau-Anleitung für Tickets\n"
        "Wenn eine Session hängt, prüfe zuerst mit `sessions <S>` auf `VERWAIST`. "
        "Greife danach ausschließlich über `to_spawn.py leiter` ein und nutze die "
        "Ablöse-SOP mit `ScheduleWakeup` statt `bau.py` direkt abzubrechen.\n",
        encoding="utf-8",
    )
    ergebnis = regel_pruefung.pruefe_repo(tmp_path)
    assert not ergebnis.ok
    assert any(f.datei == "AGENTS.md" and f.art == "ablauf_cluster" for f in ergebnis.funde), ergebnis.funde


def test_regel_pruefung_saubere_projekt_regeln_mit_querverweis_gruen(tmp_path: Path) -> None:
    """Kein Fehlalarm bei Projekt-Regeln und kurzem Querverweis auf ``/to-spawn``."""
    from to_spawn import regel_pruefung

    (tmp_path / "CLAUDE.md").write_text(
        "# Projektregeln\n\n"
        "- Python mit Type Hints, Tests über `pytest`.\n"
        "- Echte deutsche Umlaute verwenden.\n"
        "- Für Spec-Bauten nutzen wir `/to-spawn <S>` (Ablauf siehe Skill `to-spawn`).\n",
        encoding="utf-8",
    )
    (tmp_path / ".to-spawn").mkdir()
    (tmp_path / ".to-spawn" / "kette.md").write_text(
        "# Projekt-Zusatz\n\n- Rollen für Klickwege: Admin, Sachbearbeiter.\n- Werkzeuge: pytest, playwright.\n",
        encoding="utf-8",
    )
    ergebnis = regel_pruefung.pruefe_repo(tmp_path)
    assert ergebnis.ok, f"Fehlalarm bei sauberen Projekt-Regeln: {ergebnis.funde}"
    assert ergebnis.gepruefte_dateien == 2


def test_regel_pruefung_cli_und_modul_exit_codes(tmp_path: Path) -> None:
    """CLI ``to_spawn.py regeln-pruefen`` und ``python -m to_spawn.regel_pruefung`` liefern 0/1/2."""
    # Sauberes Repo → Exit 0
    (tmp_path / "CLAUDE.md").write_text("# Projekt\n\nNur eigene Fachregeln.\n", encoding="utf-8")
    ok_lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "regeln-pruefen", "--wurzel", str(tmp_path), "--json"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ok_lauf.returncode == 0, ok_lauf.stdout + ok_lauf.stderr
    daten_ok = json.loads(ok_lauf.stdout)
    assert daten_ok["ok"] is True
    assert daten_ok["funde"] == []

    # Absichtliche Kopie → Exit 1 (Rot-Beweis über CLI)
    (tmp_path / "CLAUDE.md").write_text(
        "# Projekt\n\n" + anleitung.ABLOESE_SOP + "\n",
        encoding="utf-8",
    )
    rot_lauf = subprocess.run(
        [sys.executable, "-m", "to_spawn.regel_pruefung", "--wurzel", str(tmp_path), "--json"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rot_lauf.returncode == 1, rot_lauf.stdout + rot_lauf.stderr
    daten_rot = json.loads(rot_lauf.stdout)
    assert daten_rot["ok"] is False
    assert len(daten_rot["funde"]) >= 1
    assert daten_rot["funde"][0]["datei"] == "CLAUDE.md"

    # Nicht existierender Pfad → Exit 2
    fehler_lauf = subprocess.run(
        [sys.executable, "-m", "to_spawn.regel_pruefung", "--wurzel", str(tmp_path / "fehlt")],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert fehler_lauf.returncode == 2


def test_bestandsaufnahme_tabelle_vollstaendig_ohne_verlust() -> None:
    """Bestandsaufnahme (#584): Tabelle Regel → Ort, eindeutige IDs, Zählung vorher/nachher, App #1604."""
    assert BESTANDSAUFNAHME.is_file(), f"{BESTANDSAUFNAHME} fehlt"
    text = BESTANDSAUFNAHME.read_text(encoding="utf-8")

    # Alle Regel-Zeilen aus der Tabelle ziehen: | <ID> | <Quelle> | <Regel> | <Ort> | <Maßnahme> |
    zeilen = re.findall(
        r"^\|\s*([A-Z]{2}-\d{2})\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|",
        text,
        re.MULTILINE,
    )
    assert len(zeilen) >= 40, f"Zu wenige Regeln in der Bestandsaufnahme: {len(zeilen)}"

    ids = [z[0] for z in zeilen]
    assert len(ids) == len(set(ids)), "Doppelte Regel-IDs in der Bestandsaufnahme"

    orte = {z[3].strip().split()[0].lower() for z in zeilen}
    assert {"global", "skill"} <= orte, f"Erwartete Orte fehlen: {orte}"

    # Zählung vorher/nachher + kein Verlust + Querverweis App #1604.
    assert "Vorher" in text and "Nachher" in text
    assert "Ersatzlos gelöscht" in text and "| 0 |" in text
    assert "#1604" in text
    assert "E21" in text


def test_regel_pruefung_windows_pfade_und_einzeldatei(tmp_path: Path) -> None:
    """Plattformneutralität (#584): Backslashes werden normiert, ``--datei`` prüft gezielt."""
    from to_spawn import regel_pruefung

    funde = regel_pruefung.pruefe_text(anleitung.MINDSET, r"docs\agents\regeln.md")
    assert funde and funde[0].datei == "docs/agents/regeln.md"

    einzel = tmp_path / "einzel.md"
    einzel.write_text("# Nur Projekt\n\nKeine Kopie.\n", encoding="utf-8")
    lauf = subprocess.run(
        [sys.executable, "-m", "to_spawn.regel_pruefung", "--datei", str(einzel)],
        cwd=SKILL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "1 Datei(en) geprüft" in lauf.stdout


def test_regel_pruefung_modul_utf8_trotz_cp1252_konsole(tmp_path: Path) -> None:
    """Modul-CLI spricht UTF-8 auch bei cp1252-Pipe wie unter Windows (#584)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONUTF8"] = "0"

    einzel = tmp_path / "einzel.md"
    einzel.write_text("# Nur Projekt\n\nKeine Kopie.\n", encoding="utf-8")
    ok_lauf = subprocess.run(
        [sys.executable, "-m", "to_spawn.regel_pruefung", "--datei", str(einzel)],
        cwd=SKILL,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert ok_lauf.returncode == 0, f"{ok_lauf.stdout!r} {ok_lauf.stderr!r}"
    assert ok_lauf.stdout is not None and "1 Datei(en) geprüft" in ok_lauf.stdout

    (tmp_path / "CLAUDE.md").write_text("# Projekt\n\n" + anleitung.ABLOESE_SOP + "\n", encoding="utf-8")
    rot_lauf = subprocess.run(
        [sys.executable, "-m", "to_spawn.regel_pruefung", "--wurzel", str(tmp_path), "--json"],
        cwd=SKILL,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rot_lauf.returncode == 1, f"{rot_lauf.stdout!r} {rot_lauf.stderr!r}"
    assert rot_lauf.stdout is not None
    daten = json.loads(rot_lauf.stdout)
    assert daten["ok"] is False
    assert daten["funde"][0]["datei"] == "CLAUDE.md"
