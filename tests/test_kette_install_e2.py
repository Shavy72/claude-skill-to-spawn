"""L1 E2-1 (#85): Kette-Regeln und die Skills grill-with-docs/to-spec kommen mit dem Installer.

Ein Repo ohne eigene Kopie bekommt so die volle Kette: ``kette/skills/*`` landen unter
``<skills>/<name>``, die Regel-Blöcke aus ``kette/global/*.md`` als Marker-Blöcke in der
globalen ``CLAUDE.md``. Beide Installer laufen gegen ein Wegwerf-HOME (nie das echte).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_kette_to_tickets_e2 import pruefe_repo_zuordnung_und_projekt_vorrang

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import globale_regeln

MARKER = "<!-- pflicht:beschluss-protokoll -->"
# Pflicht-Bausteine wie im App-Test test_beschluss_protokoll_skills.py (to-spec).
PFLICHT_TO_SPEC = (
    "docs/GRILL_",
    "(E<n>)",
    "Offene Fragen",
    "Gegenprüfung",
    "Quelle: docs/GRILL_",
    "Projekt-Vorrang",
    "Repo-Zuordnung",
)
REGELN = ("beschluss-protokoll", "strategisches-design")
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _umgebung(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "PYTHONIOENCODING": "utf-8",
            # Installer nimmt das Python dieses Laufs, nie ein anderes aus dem PATH.
            "TO_SPAWN_PY": sys.executable,
        }
    )
    return env


def _install_sh(heim: Path) -> subprocess.CompletedProcess[str]:
    befehl = [
        "bash",
        str(SKILL / "install.sh"),
        "--skills",
        str(heim / "skills"),
        "--claude-md",
        str(heim / "CLAUDE.md"),
    ]
    return subprocess.run(
        befehl,
        env=_umgebung(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _pruefe_installiert(heim: Path) -> None:
    to_spec = (heim / "skills" / "to-spec" / "SKILL.md").read_text(encoding="utf-8")
    assert MARKER in to_spec
    for baustein in PFLICHT_TO_SPEC:
        assert baustein in to_spec, f"to-spec: Pflicht-Baustein {baustein!r} fehlt"
    assert to_spec.index(MARKER) < to_spec.index("## Process")
    assert ".to-spawn/kette.md" in to_spec, (
        "to-spec: Verweis auf .to-spawn/kette.md fehlt"
    )
    assert "statt des restlichen Texts" not in to_spec, (
        "to-spec: alter Projekt-Vorrang (ersetzt) noch drin"
    )
    pruefe_repo_zuordnung_und_projekt_vorrang(to_spec, "to-spec")
    to_tickets = (heim / "skills" / "to-tickets" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    pruefe_repo_zuordnung_und_projekt_vorrang(to_tickets, "to-tickets")
    grill = (heim / "skills" / "grill-with-docs" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "LOKALER ZUSATZ" in grill and "Architektur-Runde" in grill
    claude_md = (heim / "CLAUDE.md").read_text(encoding="utf-8")
    for name in REGELN:
        inhalt = (
            (SKILL / "kette" / "global" / f"{name}.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        assert (
            f"<!-- global:{name}:start" in claude_md
            and f"<!-- global:{name}:ende -->" in claude_md
        )
        assert inhalt in claude_md
    assert globale_regeln.pruefe(heim / "CLAUDE.md")


def test_install_sh_legt_kette_an_und_zweiter_lauf_aendert_nichts(
    tmp_path: Path,
) -> None:
    # Bytes statt write_text: das übersetzt unter Windows \n in \r\n, geprüft wird mit LF.
    (tmp_path / "CLAUDE.md").write_bytes(b"# Eigene Regeln\n\nbleibt stehen\n")
    lauf = _install_sh(tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    _pruefe_installiert(tmp_path)
    vorher = (tmp_path / "CLAUDE.md").read_bytes()
    assert vorher.startswith(b"# Eigene Regeln\n\nbleibt stehen\n")
    lauf = _install_sh(tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert (tmp_path / "CLAUDE.md").read_bytes() == vorher
    # Alter Skill-Ordner wird verschoben, nie gelöscht (wie bei to-spawn).
    assert list((tmp_path / "skills" / "_alt").glob("to-spec-*"))


def test_install_ps1_legt_kette_an(tmp_path: Path) -> None:
    assert PWSH, "pwsh/powershell fehlt"
    befehl = [
        PWSH,
        "-NoProfile",
        "-File",
        str(SKILL / "install.ps1"),
        "-Skills",
        str(tmp_path / "skills"),
    ]
    befehl += ["-ClaudeMd", str(tmp_path / "CLAUDE.md")]
    lauf = subprocess.run(
        befehl,
        env=_umgebung(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    _pruefe_installiert(tmp_path)


def test_pruefe_erkennt_veralteten_block_und_schreibe_ersetzt_an_ort_und_stelle(
    tmp_path: Path,
) -> None:
    ziel = tmp_path / "CLAUDE.md"
    assert not globale_regeln.pruefe(ziel)
    globale_regeln.schreibe(ziel)
    assert globale_regeln.pruefe(ziel)
    text = ziel.read_text(encoding="utf-8")
    # Alter Stand aus dem App-Repo: andere Quelle in der Start-Zeile, alter Inhalt.
    alt = text.replace("Tiefe Module", "Flache Module").replace(
        globale_regeln.start_marker("strategisches-design"),
        "<!-- global:strategisches-design:start (Quelle alt/strategisches-design.md) -->",
    )
    ziel.write_text("Kopf\n\n" + alt + "\nFuß\n", encoding="utf-8")
    assert not globale_regeln.pruefe(ziel)
    cli = [
        sys.executable,
        "-m",
        "to_spawn.globale_regeln",
        "--ziel",
        str(ziel),
        "--pruefen",
    ]
    assert (
        subprocess.run(cli, cwd=SKILL, capture_output=True, check=False).returncode == 1
    )
    globale_regeln.schreibe(ziel)
    neu = ziel.read_text(encoding="utf-8")
    assert globale_regeln.pruefe(ziel)
    assert neu.startswith("Kopf\n\n") and neu.endswith("\nFuß\n")
    assert neu.count("<!-- global:strategisches-design:start") == 1
    assert "Flache Module" not in neu
    assert (
        subprocess.run(cli, cwd=SKILL, capture_output=True, check=False).returncode == 0
    )


# --- Fix-Runde 1 (Review PR #110) ---------------------------------------------

# Nachbildung von start_marker() aus duoplus-management scripts/install_global_claude_blocks.py
# (Z. 29). Das App-Skript sucht genau diese Zeile; beide Werkzeuge müssen denselben Block finden.
APP_START = "<!-- global:{name}:start (Quelle duoplus-management/docs/agents/global/{name}.md) -->"
APP_ENDE = "<!-- global:{name}:ende -->"


def _app_datei(ordner: Path = globale_regeln.QUELL_ORDNER) -> str:
    """CLAUDE.md, wie das App-Skript sie schreibt (Blöcke mit Leerzeile getrennt)."""
    bloecke = [
        f"{APP_START.format(name=q.stem)}\n{q.read_text(encoding='utf-8').strip()}\n{APP_ENDE.format(name=q.stem)}\n"
        for q in sorted(ordner.glob("*.md"))
    ]
    return "\n".join(bloecke)


def _installiere(art: str, heim: Path) -> subprocess.CompletedProcess[str]:
    if art == "sh":
        return _install_sh(heim)
    befehl = [PWSH or "pwsh", "-NoProfile", "-File", str(SKILL / "install.ps1")]
    befehl += ["-Skills", str(heim / "skills"), "-ClaudeMd", str(heim / "CLAUDE.md")]
    return subprocess.run(
        befehl,
        env=_umgebung(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


INSTALLER = [
    "sh",
    pytest.param(
        "ps1",
        marks=pytest.mark.skipif(
            not PWSH, reason="pwsh fehlt — install.ps1 am PC prüfen"
        ),
    ),
]


def test_to_spec_liest_projekt_skill_im_uebergang() -> None:
    """Befund 1: bis E2-12 verdeckt der persönliche Skill den Projekt-Skill — Übergangszeile."""
    text = (SKILL / "kette" / "skills" / "to-spec" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    zeile = next(
        (z for z in text.splitlines() if ".claude/skills/to-spec/SKILL.md" in z), ""
    )
    assert zeile, "to-spec: Übergangszeile für .claude/skills/to-spec/SKILL.md fehlt"
    assert "Vorrang" in zeile and ".to-spawn/kette.md" in zeile
    assert text.index(zeile) < text.index("## Process")


def test_marker_vertraeglich_mit_app_skript(tmp_path: Path) -> None:
    """Befund 2: was das eine Werkzeug schreibt, findet das Marker-Muster des anderen."""
    ziel = tmp_path / "CLAUDE.md"
    globale_regeln.schreibe(ziel)
    text = ziel.read_text(encoding="utf-8")
    for name in REGELN:
        assert text.count(APP_START.format(name=name)) == 1
        assert text.count(APP_ENDE.format(name=name)) == 1
    app = tmp_path / "app.md"
    app.write_text(_app_datei(), encoding="utf-8")
    assert globale_regeln.pruefe(app), "App-Stand gilt dem Skill-Modul als veraltet"
    assert not globale_regeln.schreibe(app)


def test_crlf_datei_behaelt_zeilenenden(tmp_path: Path) -> None:
    """Befund 3: fremde CRLF-Datei bleibt CRLF, zweiter Lauf byte-gleich."""
    ziel = tmp_path / "CLAUDE.md"
    ziel.write_bytes(b"# Eigene Regeln\r\n\r\nbleibt stehen\r\n")
    assert globale_regeln.schreibe(ziel)
    roh = ziel.read_bytes()
    assert roh.startswith(b"# Eigene Regeln\r\n\r\nbleibt stehen\r\n")
    assert b"\n" not in roh.replace(b"\r\n", b""), "LF ohne CR in CRLF-Datei"
    assert globale_regeln.pruefe(ziel)
    assert not globale_regeln.schreibe(ziel)
    assert ziel.read_bytes() == roh


@pytest.mark.skipif(os.name == "nt", reason="Symlinks brauchen unter Windows Rechte")
def test_symlink_bleibt_symlink(tmp_path: Path) -> None:
    """Befund 4: CLAUDE.md als Verknüpfung — das Ziel wird beschrieben, der Link bleibt."""
    echt = tmp_path / "echt.md"
    echt.write_text("# Eigene Regeln\n", encoding="utf-8")
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(echt)
    assert globale_regeln.schreibe(link)
    assert link.is_symlink()
    assert globale_regeln.pruefe(echt)


def test_doppelter_block_wird_abgelehnt(tmp_path: Path) -> None:
    """Befund 5: zwei Start-Marker (oder zwei Enden) → ValueError, Datei bleibt unverändert."""
    ziel = tmp_path / "CLAUDE.md"
    globale_regeln.schreibe(ziel)
    einfach = ziel.read_text(encoding="utf-8")
    ziel.write_text(einfach + "\n" + einfach, encoding="utf-8")
    vorher = ziel.read_bytes()
    with pytest.raises(ValueError, match="Marker"):
        globale_regeln.schreibe(ziel)
    with pytest.raises(ValueError, match="Marker"):
        globale_regeln.pruefe(ziel)
    assert ziel.read_bytes() == vorher


@pytest.mark.parametrize("art", INSTALLER)
def test_installer_zweiter_lauf_byte_gleich_fremdes_bleibt(
    art: str, tmp_path: Path
) -> None:
    """Befund 6: beide Installer — fremder Inhalt bleibt, zweiter Lauf byte-gleich."""
    # Bytes statt write_text: das übersetzt unter Windows \n in \r\n, geprüft wird mit LF.
    (tmp_path / "CLAUDE.md").write_bytes(b"# Eigene Regeln\n\nbleibt stehen\n")
    lauf = _installiere(art, tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    _pruefe_installiert(tmp_path)
    vorher = (tmp_path / "CLAUDE.md").read_bytes()
    assert vorher.startswith(b"# Eigene Regeln\n\nbleibt stehen\n")
    lauf = _installiere(art, tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (tmp_path / "CLAUDE.md").read_bytes() == vorher


@pytest.mark.parametrize("art", INSTALLER)
def test_installer_sichert_abweichenden_alten_skill_und_behaelt_agents(
    art: str, tmp_path: Path
) -> None:
    """Befunde 7/8: alter Skill landet unverändert in _alt/, agents/ geht nicht verloren,
    Dateien nur im alten Stand werden gemeldet."""
    alt = tmp_path / "skills" / "to-spec"
    (alt / "agents").mkdir(parents=True)
    (alt / "SKILL.md").write_text("alter eigener Stand\n", encoding="utf-8")
    (alt / "agents" / "openai.yaml").write_text("alt: ja\n", encoding="utf-8")
    (alt / "eigene_notiz.md").write_text("nur hier\n", encoding="utf-8")
    lauf = _installiere(art, tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    gesichert = list((tmp_path / "skills" / "_alt").glob("to-spec-*"))
    assert len(gesichert) == 1
    assert (gesichert[0] / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "alter eigener Stand\n"
    assert (gesichert[0] / "eigene_notiz.md").exists()
    for name in ("to-spec", "grill-with-docs"):
        assert (tmp_path / "skills" / name / "agents" / "openai.yaml").is_file(), name
    assert "eigene_notiz.md" in lauf.stdout, "Datei nur im alten Stand nicht gemeldet"


def test_install_sh_enthaelt_repo_zuordnung_und_projekt_vorrang_579(
    tmp_path: Path,
) -> None:
    """#579 (E15, E29): Nach install.sh enthalten to-spec und to-tickets beide Pflicht-Blöcke."""
    lauf = _install_sh(tmp_path)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    for skill in ("to-spec", "to-tickets"):
        datei = tmp_path / "skills" / skill / "SKILL.md"
        assert datei.is_file(), f"installierter Skill {skill} fehlt"
        pruefe_repo_zuordnung_und_projekt_vorrang(
            datei.read_text(encoding="utf-8"), f"install/{skill}"
        )
