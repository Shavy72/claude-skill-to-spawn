"""L1 Etappe 1 P6b: Prüf-Test „keine festen Namen“ im Skill-Code (USB-Stick-Ziel).

Der Skill soll in jedem App-Repo laufen. Namen und Pfade des ersten App-Repos (DuoPlus)
leben nur in ``to_spawn/vorgaben.py`` (über ``config.py`` erreichbar). Dieser Test
durchsucht den Skill-Code nach festen Verdrahtungen und bricht den Build, sobald eine neue
auftaucht. Muster- und Ausnahmeliste stehen nur hier. Eine Ausnahme gilt für Datei +
Muster und nennt je erlaubter Zeile ein Textstück rund um den Fund (keine Zeilennummern —
Edits verschieben nichts). Jede Fundzeile muss ein eigenes Textstück enthalten; ein Tausch
des festen Namens bei gleicher Zahl fällt so auf (E2-7, #91). Eine Ausnahme oder ein
Textstück ohne Treffer ist veraltet und muss raus.
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
ORDNER = ("to_spawn", "skripte", "scripts", "repo-scripts", "leitstand", "aliase", "nest", "messung", "review-hook", "review", "probe")
ENDUNGEN = {".py", ".sh", ".ps1", ".json", ".html", ""}

#: Die eine Vorgabe-Stelle: hier lebt das DuoPlus-Wissen gewollt.
VORGABE_DATEIEN = {"to_spawn/vorgaben.py"}

_TEXT_BESTANDSTEST = "Wortlaut von einem Bestandstest festgenagelt"
_WEITERLEITUNG = "Weiterleitung im Repo (scripts/<skript>) mit Skill-Rückfall daneben"
_HERKUNFT = "Herkunfts-Verweis auf das Ursprungs-Ticket (Doku)"
_DOKU = "Docstring/Kommentar, Beispiel der Vorgabe (Doku)"
_SKILL_HEIMAT = "Heimat des Skills selbst (Installations-Hinweis), kein App-Name"

#: (Datei, Muster) → (erlaubte Textstücke, je Fundzeile eins, Grund).
AUSNAHMEN: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {
    # Bestandstests nageln den Wortlaut fest (Tests nur addieren, nie ersetzen), ps1-Rückfälle
    ("to_spawn/umzug.py", "duoplus"): (("ng ``scripts/<skript>`` (DuoPlus), läuft der Befehl unver",), _DOKU),
    # Start-Marker byte-gleich mit dem App-Skript, sonst ValueError dort (Review PR #110, Befund 2)
    ("to_spawn/globale_regeln.py", "duoplus"): (
        ("start (Quelle duoplus-management/docs/agents/global/{name}.md) -->",),
        "Marker-Zeile gleich mit App-Skript install_global_claude_blocks.py",
    ),
    ("skripte/wache.py", "repo-skript"): (
        (
            "Aufruf: ``python scripts/wache.py <S> [--model <m>] [--tak",
            "enden Aufseher: ``python scripts/wache.py <S> --abloesen <handoff>",
        ),
        "Aufruf-Docstring der Weiterleitung",
    ),
    ("repo-scripts/_default.json", "bau-log-ordner"): (
        (
            "m Worktree {WT}), danach docs/agents/bau_log/{N}.jsonl per Pathspec m",
        ),
        _TEXT_BESTANDSTEST + " (test_bau_log_204.py)",
    ),
    ("spawn_local.ps1", "manifest-ordner"): (
        (
            "# Liest docs/agents/manifests/spec-<S>.json (Ticket-Nu",
            "ifest = Join-Path $repo \"docs/agents/manifests/spec-$Spec.json\"",
        ),
        "Rückfall vor config.pfad (test_starter_shell_l1.py) + Kopf-Kommentar",
    ),
    ("spawn_local.ps1", "repo-skript"): (
        (
            "if (-not (Test-Path \"./scripts/bau.py\") -and -not (Test-Path \"",
        ),
        _WEITERLEITUNG + " (test_starter_shell_l1.py)",
    ),
    ("install.ps1", "repo-skript"): (
        (
            "äuft in jedem Repo mit ./scripts/bau.py ---",
            "if (-not (Test-Path \"./scripts/bau.py\") -and (Test-Path \"$env:",
            "if (-not (Test-Path \"./scripts/bau.py\")) { Write-Error \"bau: k",
            "python \"./scripts/bau.py\" @args",
            "if (-not (Test-Path \"./scripts/wache.py\") -and (Test-Path \"$env:",
            "if (-not (Test-Path \"./scripts/wache.py\")) { Write-Error \"wache:",
            "python \"./scripts/wache.py\" @args",
            "if (-not (Test-Path \"./scripts/wache.py\") -and (Test-Path \"$env:",
            "if (-not (Test-Path \"./scripts/wache.py\")) { Write-Error \"aufseh",
            "python \"./scripts/wache.py\" @args",
        ),
        _WEITERLEITUNG + " (test_starter_shell_l1.py)",
    ),
    ("install.ps1", "manifest-ordner"): (
        (
            "th (Resolve-Path $Repo) \"docs\\agents\\manifests\"",
        ),
        "Rückfall vor config.pfad (test_starter_shell_l1.py)",
    ),
    ("install.ps1", "github-konto"): (("--- to-spawn (github.com/Shavy72/claude-skill-to-spawn): l",), _SKILL_HEIMAT),
    ("nest/nest_server.sh", "heim-pfad"): (
        (
            "if [ -f /root/.ssh/authorized_keys ] &&",
            "o \"$NUTZER\" -g \"$NUTZER\" /root/.ssh/authorized_keys \"$NU",
        ),
        "Root-Schlüssel des frischen Servers (/root/.ssh), Systempfad, kein Nutzername",
    ),
    # Weiterleitungs-Rückfall: Repo-Skript, sonst Skill-Skript
    ("to_spawn/aufpasser.py", "repo-skript"): (
        (
            "EPO = \"cd {repo} && {py} scripts/bau.py {n}{resume}\"",
            "EPO = \"cd {repo} && {py} scripts/wache.py {s}{resume}\"",
            "} {n} {resume} (Vorgabe: scripts/bau.py bzw. Skill)\",",
            "} {s} {resume} (Vorgabe: scripts/wache.py bzw. Skill)\",",
        ),
        _WEITERLEITUNG + " (VORLAGE_*_REPO, Hilfe-Text)",
    ),
    ("to_spawn/aufpasser.py", "duoplus"): (("Beispiele unten sind die DuoPlus-Vorgabe. Erster Treffer",), _DOKU),
    ("to_spawn/capo.py", "repo-skript"): (
        (
            "ein Start OHNE Vorspann ruft (lokal, Windows-Tab): ``scripts/bau.py``",
        ),
        _WEITERLEITUNG + " (Docstring)",
    ),
    ("repo-scripts/bau.py", "repo-skript"): (
        (
            "nen wie bisher: ``python scripts/bau.py --help``.",
        ),
        "Aufruf-Docstring der Weiterleitung selbst",
    ),
    ("repo-scripts/wache.py", "repo-skript"): (
        (
            "nen wie bisher: ``python scripts/wache.py --help``.",
        ),
        "Aufruf-Docstring der Weiterleitung selbst",
    ),
    ("repo-scripts/_to_spawn_weiterleitung.py", "repo-skript"): (
        (
            "* Als Programm (``python scripts/bau.py 205``): Umgebung setzen,",
        ),
        "Aufruf-Docstring der Weiterleitung selbst",
    ),
    ("repo-scripts/_to_spawn_weiterleitung.py", "github-konto"): (
        (
            "QUELLE = \"github.com/Shavy72/claude-skill-to-spawn\"",
        ),
        _SKILL_HEIMAT,
    ),
    ("repo-scripts/spawn_srv.sh", "github-konto"): (
        (
            "installieren (github.com/Shavy72/claude-skill-to-spawn, in",
        ),
        _SKILL_HEIMAT,
    ),
    ("skripte/spawn_srv.sh", "github-konto"): (
        (
            "installieren (github.com/Shavy72/claude-skill-to-spawn), d",
        ),
        _SKILL_HEIMAT,
    ),
    # Erkennung, kein Pfad
    ("to_spawn/inventur.py", "duoplus"): (
        (
            "TER: tuple[str, ...] = (\"duoplus\", \"postproxy\", \"n8n\", \"a",
        ),
        "PROJEKT_WÖRTER: Wortliste zur Erkennung von Projekt-Wissen",
    ),
    # Herkunft und Doku in Docstrings/Kommentaren
    ("to_spawn/capo.py", "duoplus"): (("rüfer des Bau-Aufsehers (duoplus-management#213).",), _HERKUNFT),
    ("to_spawn/context_mode.py", "duoplus"): (("jeder to-spawn-Session (duoplus-management#237).",), _HERKUNFT),
    ("to_spawn/stand.py", "duoplus"): (("em Server-Start sichern (duoplus-management#325).",), _HERKUNFT),
    ("to_spawn/deploy_status.py", "duoplus"): (("ins Bau-Log übernehmen (duoplus-management#207).",), _HERKUNFT),
    ("to_spawn/config.py", "duoplus"): (
        (
            "DuoPlus-Werte stehen nur in ``to",
        ),
        "Verweis auf die Vorgabe-Stelle vorgaben.py",
    ),
    ("to_spawn/einrichten.py", "duoplus"): (("fig ist gültige Vorgabe (DuoPlus, altes Verhalten).",), _DOKU),
    ("to_spawn/gh.py", "duoplus"): (("s heißen heute meist so; DuoPlus hat kein ``origin/main``",), _DOKU),
    ("to_spawn/hausmeister.py", "duoplus"): (("n Eintrags ist (Vorgabe: DuoPlus, Skill-Ordner fragen dor",), _DOKU),
    ("to_spawn/handoff_blick.py", "manifest-ordner"): (
        (
            "\"\"\"Ticket-Nummern aus ``docs/agents/manifests/spec-<S>.json``; Namen o",
        ),
        _DOKU,
    ),
    ("to_spawn/ticketstand.py", "manifest-ordner"): (
        (
            "+ Kurztitel: Manifest ``docs/agents/manifests/spec-<S>.json`` (:mod:`m",
        ),
        _DOKU,
    ),
    ("to_spawn/bau_loop.py", "repo-skript"): (
        (
            "ife gehört dem Skill — ``scripts/bau.py`` im Projekt-Repo bleibt",
        ),
        _DOKU,
    ),
    ("to_spawn/bau_log.py", "bau-log-ordner"): (
        (
            "- Versionierte Datei ``docs/agents/bau_log/<N>.jsonl``: schreibt nu",
            "``eintrag``, schreibt ``docs/agents/bau_log/``) weicht nie aus: eine",
        ),
        _DOKU,
    ),
    ("to_spawn/waechter_lauf.py", "bau-log-ordner"): (("oniert=True)  # schreibt docs/agents/bau_log/ (#257)",), _DOKU),
    ("skripte/bau.py", "repo-skript"): (
        (
            "Aufruf: ``python scripts/bau.py <N> [--dry-run] [--model",
        ),
        "Aufruf-Docstring der Weiterleitung",
    ),
    ("skripte/bau.py", "manifest-ordner"): (
        ("Ticket-Manifest unter ``docs/agents/manifests/*.json`` (SSOT-Schema si",),
        _DOKU,
    ),
    ("skripte/belege_uebersicht.py", "manifest-ordner"): (
        (
            "Je Ticket aus ``docs/agents/manifests/spec-<S>.json``: Titel,",
        ),
        _DOKU,
    ),
    ("skripte/test_uebersicht.py", "manifest-ordner"): (
        (
            "Liest die Tickets aus ``docs/agents/manifests/spec-<S>.json`` und dere",
        ),
        _DOKU,
    ),
    ("skripte/sessions_stand.py", "manifest-ordner"): (
        (
            "Spec-Nummer (Manifest ``docs/agents/manifests/spec-<S>.json``); ohne A",
        ),
        _DOKU,
    ),
    ("skripte/rueckblick.py", "manifest-ordner"): (
        (
            "n`` liest das Manifest ``docs/agents/manifests/spec-<S>.json``, vereini",
        ),
        _DOKU,
    ),
    ("skripte/rueckblick.py", "bau-log-ordner"): (("- Repo: ``<repo>/docs/agents/bau_log/<N>.jsonl``",), _DOKU),
    ("skripte/rueckblick.py", "duoplus"): (
        (
            "``origin``, z. B. ``duoplus-551``), je samt Worktree",
            ": der Bau-Server klont ``duoplus-management`` als",
            "``duoplus-551``, der Präfix ist al",
        ),
        _DOKU + " (Klon-Namen des Bau-Servers)",
    ),
    ("skripte/hooks/staffel_stop.py", "repo-skript"): (
        (
            "t nur in Sessions, die ``scripts/bau.py`` gestartet hat — der La",
        ),
        _DOKU,
    ),
    # Mess-Skripte des Prüftor-Vergleichs (#81): Name nur in Docstring und Aufruf-Beispiel
    ("messung/vorher_zeiten.py", "duoplus"): (("\"\"\"Vorher-Zeiten der DuoPlus-Schlange.",), _DOKU),
    ("messung/rueckblick_auswahl_duoplus.py", "duoplus"): (
        (
            "\"\"Rückblick Test-Auswahl DuoPlus (Etappe 3a): heutige gat",
            "thon3 rueckblick_auswahl_duoplus.py --schlange <schlangen",
        ),
        _DOKU + " (Aufruf-Beispiel mit Dateinamen)",
    ),
    ("review-hook/tests/test_stop_runde6.py", "duoplus"): (
        ("Pfade landeten im Session-Repo duoplus-management statt in",),
        _HERKUNFT + " (Hook-Test, 1:1 aus dem Server-Stand übernommen, #214)",
    ),
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
                funde.setdefault((rel, name), []).append(f"{rel}:{nr}: {zeile.strip()}")


def _treffer() -> dict[tuple[str, str], list[str]]:
    """(Datei, Muster) → Fundstellen ``datei:zeile: text``; Vorgabe-Dateien zählen nicht."""
    funde: dict[tuple[str, str], list[str]] = {}
    for datei in _skill_dateien():
        rel = datei.relative_to(SKILL).as_posix()
        if rel not in VORGABE_DATEIEN:
            _zeilen_treffer(rel, datei.read_text(encoding="utf-8", errors="replace"), funde)
    return funde


def _abgleich(stellen: list[str], erlaubt: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Ordnet jeder Fundstelle ein eigenes erlaubtes Textstück zu, das in ihrer Zeile steht.

    Ergebnis: (Fundstellen ohne Textstück, Textstücke ohne Fundstelle).
    """
    frei = list(erlaubt)
    fremd: list[str] = []
    for stelle in stellen:
        zeile = stelle.split(": ", 1)[1]
        stueck = next((s for s in frei if s in zeile), None)
        if stueck is None:
            fremd.append(stelle)
        else:
            frei.remove(stueck)
    return fremd, frei


def _verstoesse(funde: dict[tuple[str, str], list[str]]) -> list[str]:
    verstoesse: list[str] = []
    for schluessel, stellen in sorted(funde.items()):
        fremd, _ = _abgleich(stellen, AUSNAHMEN.get(schluessel, ((), ""))[0])
        if fremd:
            verstoesse.append(
                f"[{schluessel[1]}] {len(fremd)} Zeile(n) ohne erlaubtes Textstück:\n  "
                + "\n  ".join(s[:160] for s in fremd)
            )
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
    ungenutzt = [
        f"{d} [{m}]: {s!r}" for (d, m), (erlaubt, _) in AUSNAHMEN.items() for s in _abgleich(funde[(d, m)], erlaubt)[1]
    ]
    assert not ungenutzt, "Textstück ohne Treffer — aus AUSNAHMEN streichen: " + ", ".join(ungenutzt)
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
