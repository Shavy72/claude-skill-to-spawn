"""#396: ``review-dirigent/SKILL.md`` liegt versioniert im Skill-Repo, wird mit installiert
und hält sein Pflicht-Gerüst im Gleichschritt mit den Feldern von ``review_route.py``.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from test_review_quelle_329 import (
    NAMENS_PRUEFUNG,
    OEFFENTLICH,
    PROJEKTWOERTER,
    _verboten,
    name_muster,
    nur_mit_namensliste,
)

SKILL = Path(__file__).resolve().parents[1]
DIRIGENT_DATEI = SKILL / "review-dirigent" / "SKILL.md"
REVIEW = SKILL / "review"
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh") or shutil.which("powershell")

#: Felder im Route-JSON (oberste Ebene), die der Dirigent selbst auswertet (nicht im Prüfer-Prompt).
DIRIGENT_NUR_ROUTE = frozenset(
    {
        "version",
        "repo",
        "modus",
        "sha",
        "fingerprint",
        "datei_hashes",
        "inhalt_hashes",
        "diff_zeilen",
        "signale",
        "pruefer",
        "projektregeln",
        "projekt_woerter",
        "builtin",
        "richter",
        "beleg",
        "beleg_gueltig",
        "fix_runde",
        "diff_modus",
    }
)
#: Felder je Prüfer-Eintrag, die der Dirigent für den Agent-Aufruf nutzt (nicht im Prompt-Text).
DIRIGENT_NUR_PRUEFER = frozenset({"typ", "modell", "linsen"})


def _lade_gemeinsam(name: str) -> ModuleType:
    if str(REVIEW) not in sys.path:
        sys.path.insert(0, str(REVIEW))
    spec = importlib.util.spec_from_file_location(name, REVIEW / "_gemeinsam.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def _pflicht_geruest(text: str) -> str:
    treffer = re.search(
        r"Pflicht-Gerüst jedes Prüfer-Prompts:\s*```[^\n]*\n(.*?)```",
        text,
        re.DOTALL,
    )
    assert treffer, "Pflicht-Gerüst-Block in review-dirigent/SKILL.md nicht gefunden"
    return treffer.group(1)


def _fehlende_geruest_felder(route: dict[str, Any], geruest: str) -> list[str]:
    """Prüft alle Felder aus Route + ``pruefer[]``, die im Prüfer-Prompt vorkommen müssen."""
    fehlt: list[str] = []
    for feld in sorted(set(route) - DIRIGENT_NUR_ROUTE):
        if not re.search(rf"\b{re.escape(feld)}\b", geruest):
            fehlt.append(f"route.{feld}")
    for eintrag in route.get("pruefer", []):
        for feld in sorted(set(eintrag) - DIRIGENT_NUR_PRUEFER):
            if not re.search(rf"\b{re.escape(feld)}\b", geruest):
                eintrag_name = f"pruefer[].{feld}"
                if eintrag_name not in fehlt:
                    fehlt.append(eintrag_name)
    return fehlt


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(ordner), *args],
        check=True,
        capture_output=True,
    )


def _route_mit_regeln_und_groesse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Berechnet eine echte Route, in der ``datei_groesse`` und ``pruefer[].regeln`` befüllt sind."""
    cfg_dir = tmp_path / "review_cfg"
    cfg_dir.mkdir()
    shutil.copy2(REVIEW / "config.standard.json", cfg_dir / "config.standard.json")
    (cfg_dir / "regeln.md").write_text(
        "# Projektregeln\n\n## basis\n\nKunstregel Basis.\n\n## python\n\nKunstregel Python.\n",
        encoding="utf-8",
    )

    repo = tmp_path / "beispiel-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    datei = repo / "modul.py"
    datei.write_text("x = 0\n", encoding="utf-8")
    _git(repo, "add", "modul.py")
    _git(repo, "commit", "-q", "-m", "start")
    datei.write_text("".join(f"x_{i} = {i}\n" for i in range(1005)), encoding="utf-8")

    gemeinsam = _lade_gemeinsam("_gemeinsam_dirigent_396")
    monkeypatch.setattr(gemeinsam, "HIER", cfg_dir)
    monkeypatch.setattr(gemeinsam, "STANDARD_CONFIG", cfg_dir / "config.standard.json")
    monkeypatch.delenv("REVIEW_CONFIG", raising=False)
    return gemeinsam.berechne_route(str(repo), "manuell")


def test_dirigent_anleitung_enthaelt_regeln_ergaenzungen() -> None:
    assert DIRIGENT_DATEI.is_file(), f"fehlt im Repo: {DIRIGENT_DATEI}"
    text = DIRIGENT_DATEI.read_text(encoding="utf-8")

    # 1. Ausnahme im Absatz „Auftrag: nur der PFAD …“ für pruefer[].regeln
    auftrag_absatz = next((z for z in text.splitlines() if z.startswith("Auftrag: nur der PFAD")), "")
    assert auftrag_absatz, "Absatz 'Auftrag: nur der PFAD …' fehlt"
    assert "pruefer[].regeln" in auftrag_absatz and "Ausnahme" in auftrag_absatz

    # 2. Pflicht-Gerüst: Projektregeln-Zeile direkt unter „Auftrag: lies und befolge …“
    geruest_zeilen = _pflicht_geruest(text).splitlines()
    auftrag_idx = next((i for i, z in enumerate(geruest_zeilen) if z.startswith("Auftrag: lies und befolge")), None)
    assert auftrag_idx is not None, "Auftrag-Zeile im Pflicht-Gerüst fehlt"
    assert auftrag_idx + 1 < len(geruest_zeilen)
    regeln_zeile = geruest_zeilen[auftrag_idx + 1]
    assert regeln_zeile.startswith("Projektregeln (gelten zusätzlich):")
    assert "regeln[]" in regeln_zeile


def test_pflicht_geruest_deckt_alle_route_felder_ab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = _route_mit_regeln_und_groesse(tmp_path, monkeypatch)
    assert route["datei_groesse"], "Test-Setup: datei_groesse muss befüllt sein"
    assert route["pruefer"] and route["pruefer"][0]["regeln"], "Test-Setup: pruefer[].regeln muss befüllt sein"

    geruest = _pflicht_geruest(DIRIGENT_DATEI.read_text(encoding="utf-8"))
    # Pflicht-Felder werden tatsächlich geprüft (nicht in der Ausschlussliste).
    assert "datei_groesse" not in DIRIGENT_NUR_ROUTE
    assert "regeln" not in DIRIGENT_NUR_PRUEFER
    assert _fehlende_geruest_felder(route, geruest) == []


def test_pflicht_geruest_pruefung_bricht_bei_fehlendem_feld() -> None:
    """Gegenprobe: neues Route-Feld oder fehlendes ``regeln``/``datei_groesse`` lässt die Prüfung brechen."""
    route = {
        "moment": "manuell",
        "stufe": "mittel",
        "dateien": ["a.py"],
        "datei_groesse": [{"datei": "a.py", "vorher": 10, "nachher": 1005, "art": "ueber_1000"}],
        "neues_route_feld": "wert",
        "pruefer": [
            {
                "name": "general-purpose",
                "typ": "agent",
                "modell": "sonnet",
                "dateien": ["a.py"],
                "auftraege": ["basis"],
                "regeln": ["Regel 1"],
                "neues_pruefer_feld": ["x"],
            }
        ],
    }
    geruest = _pflicht_geruest(DIRIGENT_DATEI.read_text(encoding="utf-8"))
    assert _fehlende_geruest_felder(route, geruest) == [
        "route.neues_route_feld",
        "pruefer[].neues_pruefer_feld",
    ]

    # Entfernt man regeln / datei_groesse aus dem Gerüst, werden beide sofort gemeldet.
    gekuerzt = re.sub(r"regeln|datei_groesse", "entfernt", geruest)
    route_ohne_neu = {k: v for k, v in route.items() if k != "neues_route_feld"}
    route_ohne_neu["pruefer"] = [{k: v for k, v in route["pruefer"][0].items() if k != "neues_pruefer_feld"}]
    assert _fehlende_geruest_felder(route_ohne_neu, gekuerzt) == [
        "route.datei_groesse",
        "pruefer[].regeln",
    ]


def test_dirigent_oeffentlich_tauglich() -> None:
    """Kein Projektname, kein Geheimnis, kein privater Pfad, keine fremde Mail/IP/Domain."""
    rel = DIRIGENT_DATEI.relative_to(SKILL).as_posix()
    funde = [
        f"{rel}:{nr} ({art})"
        for nr, zeile in enumerate(DIRIGENT_DATEI.read_text(encoding="utf-8").splitlines(), 1)
        for art, muster in OEFFENTLICH.items()
        if _verboten(rel, art, muster, zeile)
    ]
    assert funde == [], f"nicht öffentlich-tauglich: {funde}"


@nur_mit_namensliste
def test_dirigent_ohne_private_namen() -> None:
    rel = DIRIGENT_DATEI.relative_to(SKILL).as_posix()
    funde = [
        f"{rel}:{nr} ({art})"
        for nr, zeile in enumerate(DIRIGENT_DATEI.read_text(encoding="utf-8").splitlines(), 1)
        for art, muster in NAMENS_PRUEFUNG.items()
        if _verboten(rel, art, muster, zeile)
    ]
    assert funde == [], f"Name aus der privaten Liste in review-dirigent/SKILL.md: {funde}"


@pytest.mark.skipif(not PROJEKTWOERTER, reason="keine privaten Projektwörter in ~/.claude/review/")
def test_dirigent_ohne_projektwoerter() -> None:
    rel = DIRIGENT_DATEI.relative_to(SKILL).as_posix()
    muster = name_muster(PROJEKTWOERTER)
    funde = [
        f"{rel}:{nr}"
        for nr, zeile in enumerate(DIRIGENT_DATEI.read_text(encoding="utf-8").splitlines(), 1)
        if muster.search(zeile)
    ]
    assert funde == [], f"Projektwort in review-dirigent/SKILL.md: {funde}"


def _installer_env(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "XDG_CONFIG_HOME": str(heim / ".config"),
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_PY": sys.executable,
        }
    )
    return env


@pytest.mark.parametrize(
    "art",
    [
        pytest.param("sh", marks=pytest.mark.skipif(not BASH, reason="bash fehlt")),
        pytest.param("ps1", marks=pytest.mark.skipif(not PWSH, reason="pwsh fehlt")),
    ],
)
def test_installer_spielt_review_dirigent_ein(art: str, tmp_path: Path) -> None:
    skills = tmp_path / ".claude" / "skills"
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    alt_ordner = skills / "review-dirigent"
    alt_ordner.mkdir(parents=True)
    (alt_ordner / "SKILL.md").write_text("# alter Dirigent\n", encoding="utf-8")

    if art == "sh":
        befehl = [BASH or "bash", str(SKILL / "install.sh"), "--skills", str(skills), "--claude-md", str(claude_md)]
    else:
        befehl = [
            PWSH or "pwsh",
            "-NoProfile",
            "-File",
            str(SKILL / "install.ps1"),
            "-Skills",
            str(skills),
            "-ClaudeMd",
            str(claude_md),
        ]
    lauf = subprocess.run(
        befehl,
        env=_installer_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe

    soll = DIRIGENT_DATEI.read_bytes().replace(b"\r", b"")
    ziel = skills / "review-dirigent" / "SKILL.md"
    assert ziel.is_file()
    assert ziel.read_bytes().replace(b"\r", b"") == soll
    assert (skills / "to-spawn" / "review-dirigent" / "SKILL.md").read_bytes().replace(b"\r", b"") == soll

    sicherungen = list((skills / "_alt").glob("review-dirigent-*/SKILL.md"))
    assert len(sicherungen) == 1
    assert sicherungen[0].read_text(encoding="utf-8") == "# alter Dirigent\n"


def test_install_ps1_spielt_review_dirigent_ein() -> None:
    """Struktur-Prüfung (läuft auch ohne pwsh unter Linux): ``install.ps1`` installiert ``review-dirigent``."""
    text = (SKILL / "install.ps1").read_text(encoding="utf-8-sig")
    assert '"review-dirigent"' in text
    assert 'Sichere-UndKopiere "review-dirigent"' in text


def test_gitattributes_haelt_review_dirigent_auf_lf() -> None:
    zeilen = {
        z.strip()
        for z in (SKILL / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if z.strip() and not z.lstrip().startswith("#")
    }
    assert "review-dirigent/**/*.md text eol=lf" in zeilen
