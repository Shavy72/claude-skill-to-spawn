"""#412: Messbefehl für die Start-Last je Fenstertyp (Bau-Session und Aufseher).

Prüft:
- ``hooks.start_kontext`` misst den Kontext des ersten Haupt-Modellaufrufs (echtes
  Transkript #204 + Sidechain-/Null-Filter).
- ``to_spawn.start_last`` / ``skripte/start_last.py`` / ``to_spawn.py start-last``:
  - Posten-Aufschlüsselung je Fenstertyp (Prompt, aktive/abgeschaltete Skills,
    globale Regeln, Repo-Ladedateien, Kontext-Paket, aktive MCP-Server).
  - Obergrenze im Probe-Repo (Bau ≤ 50k, Aufseher ≤ 80k) mit Exit 0.
  - Überschreitung (z. B. 81,3k Bau / 143,6k Aufseher wie in #412 beobachtet) → Exit 1.
  - Automatische Transkript-Erkennung über ``.to-spawn/sessions/`` und explizite Pfade.
  - Fehlerfälle (fehlende Transkript-Datei / fehlendes Repo) → Exit 2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
CLI = SKILL / "to_spawn.py"
TRANSKRIPT_204 = (
    Path(__file__).resolve().parent / "hilfen" / "transkript_204" / "97a30ed9-6786-4987-b0ba-3197343cfec6.jsonl"
)

sys.path.insert(0, str(SKILL))


def _transkript_schreiben(
    pfad: Path,
    aufrufe: list[tuple[int, int, int]],
    *,
    sidechain_davor: tuple[int, int, int] | None = None,
    null_davor: bool = False,
) -> Path:
    """Schreibt ein minimales Claude-Transkript mit (input, cache_read, cache_creation) je Zug."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    zeilen: list[dict[str, object]] = [{"type": "user", "isSidechain": False, "timestamp": "2026-11-24T00:38:00Z"}]
    if sidechain_davor is not None:
        inp, read, create = sidechain_davor
        zeilen.append(
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "id": "msg_side_0",
                    "usage": {
                        "input_tokens": inp,
                        "cache_read_input_tokens": read,
                        "cache_creation_input_tokens": create,
                        "output_tokens": 20,
                    },
                },
            }
        )
    if null_davor:
        zeilen.append(
            {
                "type": "assistant",
                "isSidechain": False,
                "message": {
                    "id": "msg_null_0",
                    "usage": {
                        "input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 0,
                    },
                },
            }
        )
    for nr, (inp, read, create) in enumerate(aufrufe, start=1):
        zeilen.append(
            {
                "type": "assistant",
                "isSidechain": False,
                "timestamp": f"2026-11-24T00:38:{nr:02d}Z",
                "message": {
                    "id": f"msg_{nr}",
                    "model": "claude-opus-5-5",
                    "usage": {
                        "input_tokens": inp,
                        "cache_read_input_tokens": read,
                        "cache_creation_input_tokens": create,
                        "output_tokens": 100,
                    },
                },
            }
        )
    pfad.write_text(
        "\n".join(json.dumps(z, ensure_ascii=False) for z in zeilen) + "\n",
        encoding="utf-8",
    )
    return pfad


def _probe_repo(tmp_path: Path) -> Path:
    """Kopiert ``probe/gemeinsam`` + ``probe/mit-staging`` ohne Bash (Linux + Windows)."""
    ziel = tmp_path / "probe-repo"
    shutil.copytree(SKILL / "probe" / "gemeinsam", ziel)
    shutil.copytree(SKILL / "probe" / "mit-staging", ziel, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(ziel), check=True)
    manifeste = ziel / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True, exist_ok=True)
    (manifeste / "spec-1.json").write_text(
        json.dumps(
            {
                "spec": 1,
                "tickets": {
                    "2": {
                        "title": "Datenmodell anlegen",
                        "schaetzung_k": 40,
                        "umfang": "Datenmodell der Probe-App.",
                        "files": ["app/server.py:1-30"],
                        "docs": ["README.md"],
                        "skills": [],
                        "mcp": [],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return ziel


def _claude_heim(tmp_path: Path) -> Path:
    """Isoliertes Heimverzeichnis mit globalen Regel-Blöcken aus ``kette/global/`` und Skills."""
    from to_spawn import globale_regeln

    heim = tmp_path / "heim"
    claude_dir = heim / ".claude"
    claude_dir.mkdir(parents=True)
    globale_regeln.schreibe(claude_dir / "CLAUDE.md")
    skills_dir = claude_dir / "skills"
    # Kette-Skills aus dem Repo übernehmen (realistische Frontmatter-Beschreibungen).
    for quelle in sorted((SKILL / "kette" / "skills").glob("*/SKILL.md")):
        ziel = skills_dir / quelle.parent.name / "SKILL.md"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(quelle, ziel)
    # Zusätzlicher fremder Skill: in bau.py per skillOverrides abgeschaltet, in wache.py aktiv.
    fremd = skills_dir / "extra-fremd" / "SKILL.md"
    fremd.parent.mkdir(parents=True, exist_ok=True)
    fremd.write_text(
        "---\nname: extra-fremd\ndescription: " + ("sehr lange Beschreibung " * 40) + "\n---\n# Extra\n",
        encoding="utf-8",
    )
    # Pflicht-Skill mit Beschreibung.
    pflicht = skills_dir / "review-dirigent" / "SKILL.md"
    pflicht.parent.mkdir(parents=True, exist_ok=True)
    pflicht.write_text(
        "---\nname: review-dirigent\ndescription: Steuert die Prüfrunde vor dem Einreichen.\n---\n# Review\n",
        encoding="utf-8",
    )
    return heim


def _start_last_env(heim: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"TO_SPAWN_REPO", "TO_SPAWN_GH_REPO", "CLAUDE_CONFIG_DIR"}}
    env["HOME"] = str(heim)
    env["USERPROFILE"] = str(heim)
    env["TO_SPAWN_GH_REPO"] = "probe/leihliste"
    return env


def _skript_aufruf(args: list[str], *, cwd: Path, heim: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "start_last.py"), *args],
        cwd=str(cwd),
        env=_start_last_env(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _cli_aufruf(args: list[str], *, cwd: Path, heim: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "start-last", *args],
        cwd=str(cwd),
        env=_start_last_env(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


# --- hooks.start_kontext -------------------------------------------------------


def test_hooks_start_kontext_echtes_transkript_204() -> None:
    """Erster Haupt-Aufruf im echten Transkript #204: 2 + 38087 + 32068 = 70157 (Spitze 180199)."""
    from to_spawn import hooks

    zeilen = hooks.lese_transkript(TRANSKRIPT_204)
    assert hooks.start_kontext(zeilen) == 70157
    assert hooks.kontext(zeilen) == {"spitze": 180199, "aufrufe": 61}


def test_hooks_start_kontext_ignoriert_sidechain_und_null_aufrufe(tmp_path: Path) -> None:
    from to_spawn import hooks

    pfad = _transkript_schreiben(
        tmp_path / "t.jsonl",
        [(10, 30_000, 12_000), (15, 50_000, 15_000)],
        sidechain_davor=(5, 90_000, 5_000),
        null_davor=True,
    )
    assert hooks.start_kontext(hooks.lese_transkript(pfad)) == 42_010
    assert hooks.start_kontext([]) is None


# --- Posten-Messung & Unterschiede bau vs. aufseher ----------------------------


def test_posten_messung_trennt_bau_und_aufseher_und_sortiert_groesste(tmp_path: Path) -> None:
    """Bau filtert Skills/MCPs; Aufseher lädt alle Skills/MCPs. Größte Posten stehen oben."""
    from to_spawn import start_last

    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)
    claude_dir = heim / ".claude"
    # Globale Regel + Repo-CLAUDE.md + globaler MCP-Server anlegen.
    (claude_dir / "rules").mkdir(parents=True)
    (claude_dir / "rules" / "grosse-regel.md").write_text("Regeltext " * 500, encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# Projektregeln\n" + ("Hinweis " * 200), encoding="utf-8")
    (heim / ".claude.json").write_text(
        json.dumps({"mcpServers": {"browser-test": {"command": "npx", "args": ["browser"]}}}),
        encoding="utf-8",
    )

    bericht = start_last.messen(
        repo,
        spec=1,
        ticket="2",
        claude_dir=claude_dir,
        home=heim,
        gh_repo="probe/leihliste",
        mit_sessions=False,
    )
    bau = bericht["bau"]
    aufseher = bericht["aufseher"]

    assert bau.typ == "bau"
    assert aufseher.typ == "aufseher"
    # Standard-Bau-Session hat 14 aktive Skills (11 core + 3 zusätzliche Pflicht-Skills).
    assert len(bau.skills_aktiv) == 14
    assert "extra-fremd" not in bau.skills_aktiv
    assert bau.skills_aus >= 1
    # Aufseher hat heute keine skillOverrides → extra-fremd ist aktiv.
    assert "extra-fremd" in aufseher.skills_aktiv
    assert aufseher.skills_aus == 0
    # MCP-Unterschied: Bau sperrt nicht gelistete MCPs per --strict-mcp-config, Aufseher nicht.
    assert "browser-test" not in bau.mcp_aktiv
    assert "browser-test" in aufseher.mcp_aktiv
    # Bau misst das Kontext-Paket (app/server.py:1-30 + README.md), Aufseher nicht.
    assert any(p.gruppe == "kontext_paket" for p in bau.posten)
    assert not any(p.gruppe == "kontext_paket" for p in aufseher.posten)
    # Größte Posten absteigend sortiert.
    bau_tokens = [p.tokens for p in bau.posten]
    assert bau_tokens == sorted(bau_tokens, reverse=True)
    assert any(p.gruppe == "regeln_global" and "grosse-regel.md" in p.name for p in bau.posten)
    assert any(p.gruppe == "regeln_repo" and p.name == "CLAUDE.md" for p in bau.posten)


def test_kontext_paket_liest_nie_ausserhalb_des_repos(tmp_path: Path) -> None:
    """Pfad-Ausbruch (``../geheim.txt``) im Manifest wird ignoriert."""
    from to_spawn import start_last

    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)
    geheim = tmp_path / "geheim.txt"
    geheim.write_text("GEHEIM " * 5000, encoding="utf-8")
    manifest_pfad = repo / "docs" / "agents" / "manifests" / "spec-1.json"
    daten = json.loads(manifest_pfad.read_text(encoding="utf-8"))
    daten["tickets"]["2"]["files"] = ["../geheim.txt:1-100"]
    manifest_pfad.write_text(json.dumps(daten), encoding="utf-8")

    bericht = start_last.messen(
        repo,
        spec=1,
        ticket="2",
        claude_dir=heim / ".claude",
        home=heim,
        gh_repo="probe/leihliste",
        mit_sessions=False,
    )
    assert not any("geheim.txt" in p.name for p in bericht["bau"].posten)


# --- Abnahme: Probe-Repo Obergrenze (Bau ≤ 50k, Aufseher ≤ 80k) ----------------


def test_probe_repo_unter_obergrenze_gruen(tmp_path: Path) -> None:
    """Abnahme #412: Messbefehl gibt je Fenstertyp Start-Kontext aus; Probe-Repo ≤ 50k / ≤ 80k."""
    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)

    lauf = _skript_aufruf(
        ["--repo", str(repo), "--spec", "1", "--ticket", "2", "--pruefen", "--json"],
        cwd=repo,
        heim=heim,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    assert daten["ok"] is True
    assert daten["grenzen_k"] == {"bau": 50.0, "aufseher": 80.0}
    assert daten["fenster"]["bau"]["obergrenze_k"] == 50.0
    assert daten["fenster"]["aufseher"]["obergrenze_k"] == 80.0
    assert daten["fenster"]["bau"]["massgeblich_k"] <= 50.0
    assert daten["fenster"]["aufseher"]["massgeblich_k"] <= 80.0
    assert daten["fenster"]["bau"]["bestanden"] is True
    assert daten["fenster"]["aufseher"]["bestanden"] is True

    # Menschenlesbare Ausgabe nennt beide Fenstertypen, Zielwerte und Status.
    text_lauf = _skript_aufruf(
        ["--repo", str(repo), "--spec", "1", "--ticket", "2", "--pruefen"],
        cwd=repo,
        heim=heim,
    )
    assert text_lauf.returncode == 0, text_lauf.stdout + text_lauf.stderr
    assert "bau" in text_lauf.stdout and "aufseher" in text_lauf.stdout
    assert "50,0k" in text_lauf.stdout and "80,0k" in text_lauf.stdout


# --- Transkript-Messung + Obergrenzen-Prüfung ----------------------------------


def test_obergrenze_ueberschritten_meldet_exit_1(tmp_path: Path) -> None:
    """Beobachtete Werte aus #412 (Bau 81,3k, Aufseher 143,6k) → Exit 1 mit klarer Meldung."""
    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)
    bau_tr = _transkript_schreiben(tmp_path / "bau.jsonl", [(10, 41_300, 40_000)])
    auf_tr = _transkript_schreiben(tmp_path / "aufseher.jsonl", [(10, 83_600, 60_000)])

    lauf = _skript_aufruf(
        [
            "--repo",
            str(repo),
            "--bau-transkript",
            str(bau_tr),
            "--aufseher-transkript",
            str(auf_tr),
            "--pruefen",
            "--json",
        ],
        cwd=repo,
        heim=heim,
    )
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    assert daten["ok"] is False
    assert daten["fenster"]["bau"]["gemessen_k"] == 81.3
    assert daten["fenster"]["bau"]["bestanden"] is False
    assert daten["fenster"]["aufseher"]["gemessen_k"] == 143.6
    assert daten["fenster"]["aufseher"]["bestanden"] is False

    # Textausgabe zeigt Überschreitung deutlich.
    text_lauf = _skript_aufruf(
        [
            "--repo",
            str(repo),
            "--bau-transkript",
            str(bau_tr),
            "--aufseher-transkript",
            str(auf_tr),
            "--pruefen",
        ],
        cwd=repo,
        heim=heim,
    )
    assert text_lauf.returncode == 1
    assert "81,3k" in text_lauf.stdout and "143,6k" in text_lauf.stdout
    assert "UEBER GRENZE" in text_lauf.stdout


def test_transkripte_unter_obergrenze_bestehen(tmp_path: Path) -> None:
    """Bau 42,0k ≤ 50k und Aufseher 68,5k ≤ 80k → Exit 0."""
    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)
    bau_tr = _transkript_schreiben(tmp_path / "bau.jsonl", [(5, 22_000, 20_000), (5, 70_000, 5_000)])
    auf_tr = _transkript_schreiben(tmp_path / "auf.jsonl", [(5, 48_500, 20_000), (5, 110_000, 5_000)])

    lauf = _skript_aufruf(
        [
            "--repo",
            str(repo),
            "--bau-transkript",
            str(bau_tr),
            "--aufseher-transkript",
            str(auf_tr),
            "--pruefen",
            "--json",
        ],
        cwd=repo,
        heim=heim,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    assert daten["ok"] is True
    # Erster Zug zählt (42,0k / 68,5k), nicht die spätere Spitze (75,0k / 115,0k).
    assert daten["fenster"]["bau"]["gemessen_k"] == 42.0
    assert daten["fenster"]["aufseher"]["gemessen_k"] == 68.5


def test_cli_to_spawn_start_last_liest_sessions_dateien_automatisch(tmp_path: Path) -> None:
    """``to_spawn.py start-last`` findet Bau- und Aufseher-Transkripte über ``.to-spawn/sessions/``."""
    from to_spawn import sessions_datei, waechter_lauf

    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)
    wt = tmp_path / "wt-2"
    wt.mkdir()

    bau_sid = "11111111-2222-3333-4444-555555555555"
    wache_sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    sessions_datei.schreiben(repo, "2", bau_sid, wt, 1)
    sessions_datei.schreiben(repo, "wache-1", wache_sid, repo, 1)

    _transkript_schreiben(
        waechter_lauf.transkript_ordner(wt, heim=heim) / f"{bau_sid}.jsonl",
        [(2, 24_000, 15_000)],
    )
    _transkript_schreiben(
        waechter_lauf.transkript_ordner(repo, heim=heim) / f"{wache_sid}.jsonl",
        [(2, 45_000, 20_000)],
    )

    lauf = _cli_aufruf(["--repo", str(repo), "--pruefen", "--json"], cwd=repo, heim=heim)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    daten = json.loads(lauf.stdout)
    assert daten["ok"] is True
    assert daten["fenster"]["bau"]["quelle"] == "transkript"
    assert daten["fenster"]["bau"]["gemessen_k"] == 39.0
    assert daten["fenster"]["aufseher"]["quelle"] == "transkript"
    assert daten["fenster"]["aufseher"]["gemessen_k"] == 65.0


def test_fehlerfaelle_exit_2(tmp_path: Path) -> None:
    """Fehlendes Transkript oder fehlendes Repo → Exit 2 mit Grund auf stderr."""
    repo = _probe_repo(tmp_path)
    heim = _claude_heim(tmp_path)

    fehlt = _skript_aufruf(
        ["--repo", str(repo), "--bau-transkript", str(tmp_path / "gibts-nicht.jsonl")],
        cwd=repo,
        heim=heim,
    )
    assert fehlt.returncode == 2
    assert "Transkript nicht gefunden" in fehlt.stderr

    leer = tmp_path / "leer.jsonl"
    leer.write_text('{"type": "user"}\n', encoding="utf-8")
    ohne_aufruf = _skript_aufruf(
        ["--repo", str(repo), "--aufseher-transkript", str(leer)],
        cwd=repo,
        heim=heim,
    )
    assert ohne_aufruf.returncode == 2
    assert "keinen lesbaren Haupt-Modellaufruf" in ohne_aufruf.stderr

    kein_repo = _skript_aufruf(["--repo", str(tmp_path / "kein-ordner")], cwd=repo, heim=heim)
    assert kein_repo.returncode == 2
    assert "Repo-Ordner existiert nicht" in kein_repo.stderr
