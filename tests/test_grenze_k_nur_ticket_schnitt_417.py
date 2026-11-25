"""#417: ``staffel.grenze_k`` ist nur der Ticket-Schnitt, die Ablösung hängt an der Smart-Zone.

Regel des Eigentümers: Grenz-Zahlen für laufende Sessions stehen nur in
``~/.claude/smart-zone.json``; ``staffel.grenze_k`` in ``.to-spawn/config.json`` ist allein
die Planungs-Grenze je Ticket. Diese Tests brechen den Build, sobald jemand eine zweite
Grenz-Quelle baut (neuer Leser von ``grenze_k``) oder die Anleitung den klaren Satz verliert.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import repo_dateien

WURZEL = Path(__file__).resolve().parent.parent

#: Die einzigen Code-Stellen, die ``grenze_k`` lesen dürfen (Grep-Beleg #417), je mit Grund.
#: Alle drei nutzen das Feld als Ticket-Schnitt, keine als Ablöse-Grenze.
ERLAUBTE_LESER = {
    # Vorgabe ``DEFAULTS["staffel"]["grenze_k"]`` samt Beschreibung des Felds.
    "to_spawn/config.py",
    # ``pruefe_eintrag``: Planungs-Grenze je Ticket im Manifest (``anhaengen``/``manifest``).
    "to_spawn/manifest.py",
    # ``lernstoff``: Faustregeln für /to-tickets (Schätzung gegen Ticket-Schnitt).
    "to_spawn/bau_log.py",
}

#: Jede Erwähnung des Felds als Wort: Schlüssel (``.get("grenze_k")``, ``["grenze_k"]``),
#: Attribut/jq (``konfig.staffel.grenze_k``, ``.staffel.grenze_k``), Zuweisung oder
#: Argument (``grenze_k=``), Shell-Variable (``$grenze_k``).
FELD = re.compile(r"\bgrenze_k\b")

#: Doku-Text in Docstrings (``staffel.grenze_k``) und reine Kommentarzeilen sind kein Leser.
DOKU_SPANNE = re.compile(r"``[^`\n]*``")
KOMMENTAR = re.compile(r"^\s*(#|//|::|REM\b)")

ENDUNGEN = {".py", ".mjs", ".js", ".cjs", ".ts", ".sh", ".ps1", ".psm1", ".bat", ".cmd"}

#: Tests, Doku und Review-Ablage sind kein Laufzeit-Code; Caches ohnehin nicht.
AUSGENOMMEN = {
    "tests",
    "docs",
    ".review",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}


def _code_dateien() -> list[Path]:
    """Alle Code-Dateien des Repos: Wurzel (``to_spawn.py``) und jeder Code-Ordner."""
    dateien = []
    for datei in repo_dateien(WURZEL):
        if datei.suffix not in ENDUNGEN:
            continue
        if AUSGENOMMEN & set(datei.relative_to(WURZEL).parts):
            continue
        dateien.append(datei)
    return dateien


def _liest_grenze_k(text: str) -> bool:
    for zeile in text.splitlines():
        if KOMMENTAR.match(zeile):
            continue
        if FELD.search(DOKU_SPANNE.sub("", zeile)):
            return True
    return False


def _leser() -> set[str]:
    gefunden: set[str] = set()
    for datei in _code_dateien():
        text = datei.read_text(encoding="utf-8", errors="replace")
        if _liest_grenze_k(text):
            gefunden.add(datei.relative_to(WURZEL).as_posix())
    return gefunden


def test_scan_deckt_alle_code_ordner_ab() -> None:
    # ``kette/`` trägt heute nur Skill-Texte (.md); kommt dort Code hinzu, scannt rglob ihn mit.
    erfasst = {d.relative_to(WURZEL).parts[0] for d in _code_dateien()}
    for pflicht in (
        "to_spawn.py",
        "to_spawn",
        "skripte",
        "repo-scripts",
        "review-hook",
        "review",
        "nest",
        "leitstand",
        "messung",
    ):
        assert pflicht in erfasst, f"Scan übersieht {pflicht}"
    assert "tests" not in erfasst


def test_muster_findet_jeden_zugriff() -> None:
    for zugriff in (
        'k.get("grenze_k")',
        "k['grenze_k']",
        "konfig.staffel.grenze_k",
        "jq -r .staffel.grenze_k",
        "lernstoff(repo, grenze_k=200)",
        'echo "$grenze_k"',
    ):
        assert _liest_grenze_k(zugriff), zugriff
    assert not _liest_grenze_k("    ``staffel.grenze_k`` ist nur der Ticket-Schnitt")
    assert not _liest_grenze_k("# grenze_k nur Ticket-Schnitt")


def test_grenze_k_nur_in_erlaubten_modulen_gelesen() -> None:
    fremde = _leser() - ERLAUBTE_LESER
    assert not fremde, (
        f"grenze_k wird in {sorted(fremde)} gelesen. grenze_k ist nur der Ticket-Schnitt; "
        "die Ablösung einer laufenden Session hängt allein an ~/.claude/smart-zone.json (#417)."
    )


def test_stop_hook_kennt_grenze_k_nicht() -> None:
    hook = (WURZEL / "skripte" / "hooks" / "staffel_stop.py").read_text(encoding="utf-8")
    assert "grenze_k" not in hook


def test_skill_md_sagt_klar_was_grenze_k_ist() -> None:
    text = (WURZEL / "SKILL.md").read_text(encoding="utf-8")
    assert "`staffel.grenze_k` ist nur der Ticket-Schnitt" in text
    assert "~/.claude/smart-zone.json" in text
    assert "Staffel (200k)" not in text, "Anleitung legt eine feste Ablöse-Zahl im Skill nahe"


def test_konfig_vorgabe_erklaert_grenze_k() -> None:
    text = (WURZEL / "to_spawn" / "config.py").read_text(encoding="utf-8")
    zeilen = text.splitlines()
    nr = next(i for i, z in enumerate(zeilen) if '"grenze_k"' in z)
    kommentar = " ".join(zeilen[max(nr - 4, 0) : nr])
    assert "Ticket-Schnitt" in kommentar and "smart-zone.json" in kommentar
