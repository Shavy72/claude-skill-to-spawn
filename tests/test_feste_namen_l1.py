"""L1 Etappe 1 P6b: Prüf-Test „keine festen Namen“ im Skill-Code (USB-Stick-Ziel).

Der Skill soll in jedem App-Repo laufen. Namen und Pfade des ersten App-Repos (DuoPlus)
leben nur in ``to_spawn/vorgaben.py`` (über ``config.py`` erreichbar). Dieser Test
durchsucht den Skill-Code nach festen Verdrahtungen und bricht den Build, sobald eine neue
auftaucht. Muster- und Ausnahmeliste stehen nur hier. Eine Ausnahme gilt für Datei +
Muster und erlaubt höchstens so viele Zeilen wie angegeben (keine Zeilennummern — Edits
verschieben nichts). Eine Ausnahme ohne Treffer ist veraltet und muss raus.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

#: Was als feste Verdrahtung gilt (Name → Muster, je Zeile gezählt).
MUSTER: dict[str, re.Pattern[str]] = {
    "duoplus": re.compile(r"duoplus", re.IGNORECASE),
    "github-konto": re.compile(r"Shavy72/", re.IGNORECASE),
    "manifest-ordner": re.compile(r"docs[\\/]+agents[\\/]+manifests"),
    "bau-log-ordner": re.compile(r"docs[\\/]+agents[\\/]+bau_log"),
    "repo-skript": re.compile(r"scripts[\\/]+(?:bau|wache)\.py"),
    "heim-pfad": re.compile(r"/home/[a-z_]|/root/|\b[A-Z]:[\\/]+Users[\\/]", re.IGNORECASE),
}

#: Ordner (Skill-Wurzel-relativ) und Dateitypen, die zum Skill-Code zählen. ``tests/`` nicht.
#: ``messung/`` wird nicht installiert (NICHT_INSTALLIERT in test_installer_vollstaendig.py), aber gescannt.
ORDNER = ("to_spawn", "skripte", "scripts", "repo-scripts", "leitstand", "aliase", "nest", "messung")
ENDUNGEN = {".py", ".sh", ".ps1", ".json", ".html", ""}

#: Die eine Vorgabe-Stelle: hier lebt das DuoPlus-Wissen gewollt.
VORGABE_DATEIEN = {"to_spawn/vorgaben.py"}

_TEXT_BESTANDSTEST = "Wortlaut von einem Bestandstest festgenagelt"
_WEITERLEITUNG = "Weiterleitung im Repo (scripts/<skript>) mit Skill-Rückfall daneben"
_HERKUNFT = "Herkunfts-Verweis auf das Ursprungs-Ticket (Doku)"
_DOKU = "Docstring/Kommentar, Beispiel der Vorgabe (Doku)"
_SKILL_HEIMAT = "Heimat des Skills selbst (Installations-Hinweis), kein App-Name"

#: (Datei, Muster) → (höchstens so viele Zeilen, Grund).
AUSNAHMEN: dict[tuple[str, str], tuple[int, str]] = {
    # Bestandstests nageln den Wortlaut fest (Tests nur addieren, nie ersetzen), ps1-Rückfälle
    ("to_spawn/umzug.py", "duoplus"): (1, _DOKU),
    ("skripte/wache.py", "repo-skript"): (2, "Aufruf-Docstring der Weiterleitung"),
    ("repo-scripts/_default.json", "bau-log-ordner"): (1, _TEXT_BESTANDSTEST + " (test_bau_log_204.py)"),
    ("spawn_local.ps1", "manifest-ordner"): (2, "Rückfall vor config.pfad (test_starter_shell_l1.py) + Kopf-Kommentar"),
    ("spawn_local.ps1", "repo-skript"): (1, _WEITERLEITUNG + " (test_starter_shell_l1.py)"),
    ("install.ps1", "repo-skript"): (10, _WEITERLEITUNG + " (test_starter_shell_l1.py)"),
    ("install.ps1", "manifest-ordner"): (1, "Rückfall vor config.pfad (test_starter_shell_l1.py)"),
    ("install.ps1", "github-konto"): (1, _SKILL_HEIMAT),
    ("nest/nest_server.sh", "heim-pfad"): (2, "Root-Schlüssel des frischen Servers (/root/.ssh), Systempfad, kein Nutzername"),
    # Weiterleitungs-Rückfall: Repo-Skript, sonst Skill-Skript
    ("to_spawn/aufpasser.py", "repo-skript"): (4, _WEITERLEITUNG + " (VORLAGE_*_REPO, Hilfe-Text)"),
    ("to_spawn/aufpasser.py", "duoplus"): (1, _DOKU),
    ("to_spawn/capo.py", "repo-skript"): (1, _WEITERLEITUNG + " (Docstring)"),
    ("repo-scripts/bau.py", "repo-skript"): (1, "Aufruf-Docstring der Weiterleitung selbst"),
    ("repo-scripts/wache.py", "repo-skript"): (1, "Aufruf-Docstring der Weiterleitung selbst"),
    ("repo-scripts/_to_spawn_weiterleitung.py", "repo-skript"): (1, "Aufruf-Docstring der Weiterleitung selbst"),
    ("repo-scripts/_to_spawn_weiterleitung.py", "github-konto"): (1, _SKILL_HEIMAT),
    ("repo-scripts/spawn_srv.sh", "github-konto"): (1, _SKILL_HEIMAT),
    ("skripte/spawn_srv.sh", "github-konto"): (1, _SKILL_HEIMAT),
    # Erkennung, kein Pfad
    ("to_spawn/inventur.py", "duoplus"): (1, "PROJEKT_WÖRTER: Wortliste zur Erkennung von Projekt-Wissen"),
    # Herkunft und Doku in Docstrings/Kommentaren
    ("to_spawn/capo.py", "duoplus"): (1, _HERKUNFT),
    ("to_spawn/context_mode.py", "duoplus"): (1, _HERKUNFT),
    ("to_spawn/stand.py", "duoplus"): (1, _HERKUNFT),
    ("to_spawn/deploy_status.py", "duoplus"): (1, _HERKUNFT),
    ("to_spawn/config.py", "duoplus"): (1, "Verweis auf die Vorgabe-Stelle vorgaben.py"),
    ("to_spawn/einrichten.py", "duoplus"): (1, _DOKU),
    ("to_spawn/gh.py", "duoplus"): (1, _DOKU),
    ("to_spawn/hausmeister.py", "duoplus"): (1, _DOKU),
    ("to_spawn/handoff_blick.py", "manifest-ordner"): (1, _DOKU),
    ("to_spawn/ticketstand.py", "manifest-ordner"): (1, _DOKU),
    ("to_spawn/bau_loop.py", "repo-skript"): (1, _DOKU),
    ("to_spawn/bau_log.py", "bau-log-ordner"): (2, _DOKU),
    ("to_spawn/waechter_lauf.py", "bau-log-ordner"): (1, _DOKU),
    ("skripte/bau.py", "repo-skript"): (1, "Aufruf-Docstring der Weiterleitung"),
    ("skripte/bau.py", "manifest-ordner"): (2, _DOKU),
    ("skripte/belege_uebersicht.py", "manifest-ordner"): (1, _DOKU),
    ("skripte/test_uebersicht.py", "manifest-ordner"): (1, _DOKU),
    ("skripte/sessions_stand.py", "manifest-ordner"): (1, _DOKU),
    ("skripte/rueckblick.py", "manifest-ordner"): (1, _DOKU),
    ("skripte/rueckblick.py", "bau-log-ordner"): (1, _DOKU),
    ("skripte/rueckblick.py", "duoplus"): (3, _DOKU + " (Klon-Namen des Bau-Servers)"),
    ("skripte/hooks/staffel_stop.py", "repo-skript"): (1, _DOKU),
    # Mess-Skripte des Prüftor-Vergleichs (#81): Name nur in Docstring und Aufruf-Beispiel
    ("messung/vorher_zeiten.py", "duoplus"): (1, _DOKU),
    ("messung/rueckblick_auswahl_duoplus.py", "duoplus"): (2, _DOKU + " (Aufruf-Beispiel mit Dateinamen)"),
}


def _skill_dateien() -> list[Path]:
    dateien = [p for p in SKILL.iterdir() if p.is_file() and p.suffix in {".py", ".sh", ".ps1"}]
    for name in ORDNER:
        ordner = SKILL / name
        if ordner.is_dir():
            dateien += [
                p for p in ordner.rglob("*") if p.is_file() and p.suffix in ENDUNGEN and "__pycache__" not in p.parts
            ]
    return sorted(dateien)


def _zeilen_treffer(rel: str, text: str, funde: dict[tuple[str, str], list[str]]) -> None:
    for nr, zeile in enumerate(text.splitlines(), 1):
        for name, muster in MUSTER.items():
            if muster.search(zeile):
                funde.setdefault((rel, name), []).append(f"{rel}:{nr}: {zeile.strip()[:120]}")


def _treffer() -> dict[tuple[str, str], list[str]]:
    """(Datei, Muster) → Fundstellen ``datei:zeile: text``; Vorgabe-Dateien zählen nicht."""
    funde: dict[tuple[str, str], list[str]] = {}
    for datei in _skill_dateien():
        rel = datei.relative_to(SKILL).as_posix()
        if rel not in VORGABE_DATEIEN:
            _zeilen_treffer(rel, datei.read_text(encoding="utf-8", errors="replace"), funde)
    return funde


def _verstoesse(funde: dict[tuple[str, str], list[str]]) -> list[str]:
    verstoesse: list[str] = []
    for schluessel, stellen in sorted(funde.items()):
        erlaubt = AUSNAHMEN.get(schluessel, (0, ""))[0]
        if len(stellen) > erlaubt:
            verstoesse.append(f"[{schluessel[1]}] {len(stellen)} > {erlaubt} erlaubt:\n  " + "\n  ".join(stellen))
    return verstoesse


def test_keine_festen_namen_im_skill_code() -> None:
    verstoesse = _verstoesse(_treffer())
    assert not verstoesse, (
        "Fester Name/Pfad im Skill-Code — Wert in to_spawn/vorgaben.py bzw. Konfig-Schlüssel "
        "lesen, oder begründete Ausnahme in AUSNAHMEN eintragen:\n" + "\n".join(verstoesse)
    )


def test_ausnahmen_nicht_veraltet() -> None:
    funde = _treffer()
    veraltet = [f"{d} [{m}]" for (d, m) in AUSNAHMEN if (d, m) not in funde]
    assert not veraltet, "Ausnahme ohne Treffer — aus AUSNAHMEN streichen: " + ", ".join(veraltet)
    assert all(grund.strip() for _, grund in AUSNAHMEN.values())
    assert set(m for _, m in AUSNAHMEN) <= set(MUSTER)


def test_pruefung_wird_rot_bei_neuem_festen_namen() -> None:
    """Gegenprobe ohne Repo-Eingriff: Probe-Zeilen in einer Datei mit und ohne Ausnahme."""
    stellen = {
        "duoplus": 'REPO = "Shavy72/duoplus-management"',
        "manifest-ordner": 'ORT = "docs/agents/manifests"',
        "heim-pfad": 'LOG = "/home/bau/x.log"',
        "repo-skript": 'BEFEHL = "python scripts/bau.py 5"',
    }
    for rel in ("to_spawn/neu.py", "to_spawn/aufpasser.py"):
        funde: dict[tuple[str, str], list[str]] = {}
        _zeilen_treffer(rel, "\n".join([*stellen.values()] * 5), funde)
        assert {m for _, m in funde} >= set(stellen)
        assert len(_verstoesse(funde)) >= len(stellen), rel


#: Ordner ohne Skill-Code: Tests, Doku, Review-Belege (Prüf-Ergebnisse, keine Logik).
NICHT_GESCANNT = frozenset({"tests", "docs", ".review"})


def test_jeder_code_ordner_wird_gescannt() -> None:
    """P7b (#79): ein neuer Code-Ordner fällt nicht still aus dem Scan (Dirigent P6b, :39)."""
    erfasst = subprocess.run(
        ["git", "-C", str(SKILL), "ls-files"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()
    ordner = {teil.split("/", 1)[0] for teil in erfasst if "/" in teil and Path(teil).suffix in ENDUNGEN - {""}}
    assert sorted(ordner - set(ORDNER) - NICHT_GESCANNT) == []
