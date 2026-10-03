"""#428 — Die Rolle „Wächter“ heißt überall „Aufseher“ (Beschluss E1).

Geprüft wird:
a) ``nest/nest_server.sh`` legt in der ``.bashrc`` eine Shell-Funktion ``aufseher`` an,
   die dasselbe Skript startet wie ``wache`` (``wache.py``); ``wache`` bleibt als Alias.
b) ``install.ps1`` registriert ``aufseher`` im PowerShell-Profil neben ``wache``
   (gleiches Ziel ``./scripts/wache.py``), auch für Profile, die ``wache`` schon haben.
c) Grep-Wächter: keine aktive Datei des Skills nennt die Rolle noch „Wächter“/„Wache“/„watcher“.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
NEST = SKILL / "nest" / "nest_server.sh"
INSTALL_PS1 = SKILL / "install.ps1"

#: Historische oder generierte Bereiche — dort bleibt der alte Name stehen (Belege, Pläne).
AUSGENOMMENE_ORDNER: tuple[str, ...] = (
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "tests",  # Testdaten, Fixtures, alte Rot-/Grün-Ausgaben
    "docs/verify-hard",  # Belegseiten früherer Tickets (historisch)
)
#: Pläne früherer Tickets sind historisch und bleiben wörtlich.
AUSGENOMMENE_DATEIMUSTER: tuple[str, ...] = ("docs/PLAN_*.md",)

#: Zusammensetzungen mit „Wache“, die NICHT die Rolle meinen, sondern einen Schutz-Mechanismus
#: (Deploy-/Gate-Sperre im Aufpasser, Blocker-Warteschleife in bau.py, Umzug-Kindprozess).
ERLAUBTE_WACHE_VORSILBEN: tuple[str, ...] = ("Deploy-", "Gate-", "Blocker-", "Umzug-")

# Identifier bleiben bewusst (sonst Änderungs-Lawine): Dateinamen ``wache.py``,
# ``waechter_lauf.py``, ``waechter_takt.py``, Konfig-Schlüssel ``waechter``, Fensternamen
# ``wache <S>``, Sessions-Datei ``wache-<S>.json``, Schalter ``-OhneWache``/``--ohne-wache``,
# Umgebungsvariable ``TO_SPAWN_WACHE_SPEC``. Die Muster unten treffen nur das großgeschriebene
# deutsche Rollenwort bzw. das englische „watcher“ — Identifier fallen von selbst heraus.
#: Zeilen mit dieser Marke dürfen den alten Namen tragen: Rückwärtskompatibilität (alte
#: Issue-Kommentare „Wächter: …“ müssen weiter als eigene Kommentare erkannt werden).
RUECKWAERTS_MARKE = "#428-alt"

ROLLEN_MUSTER = re.compile(r"Wächter\w*|\bWachen?\b|\b[Ww]atchers?\b")

TEXT_ENDUNGEN = {
    ".py",
    ".sh",
    ".ps1",
    ".md",
    ".html",
    ".json",
    ".txt",
    ".toml",
    ".cfg",
    "",
}


def _aktive_dateien() -> list[Path]:
    dateien: list[Path] = []
    for pfad in SKILL.rglob("*"):
        if not pfad.is_file() or pfad.suffix not in TEXT_ENDUNGEN:
            continue
        rel = pfad.relative_to(SKILL).as_posix()
        if any(rel == o or rel.startswith(o + "/") for o in AUSGENOMMENE_ORDNER):
            continue
        if any(Path(rel).match(m) for m in AUSGENOMMENE_DATEIMUSTER):
            continue
        dateien.append(pfad)
    return dateien


def _rollen_treffer(text: str) -> list[str]:
    treffer: list[str] = []
    for m in ROLLEN_MUSTER.finditer(text):
        wort = m.group(0)
        if wort.startswith("Wache"):
            davor = text[max(0, m.start() - 12) : m.start()]
            if any(davor.endswith(v) for v in ERLAUBTE_WACHE_VORSILBEN):
                continue
        treffer.append(wort)
    return treffer


def test_grep_waechter_keine_alte_rollenbezeichnung_in_aktiven_dateien() -> None:
    funde: list[str] = []
    for pfad in _aktive_dateien():
        try:
            zeilen = pfad.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for nr, zeile in enumerate(zeilen, 1):
            if RUECKWAERTS_MARKE in zeile:
                continue
            for wort in _rollen_treffer(zeile):
                funde.append(f"{pfad.relative_to(SKILL)}:{nr}: {wort}")
    assert not funde, "Alte Rollenbezeichnung gefunden:\n" + "\n".join(funde[:60])


def test_rollen_muster_trifft_rolle_aber_nicht_identifier() -> None:
    assert _rollen_treffer("der Wächter prüft") == ["Wächter"]
    assert _rollen_treffer("des Bau-Wächters") == ["Wächters"]
    assert _rollen_treffer("die Nachfolge-Wache startet") == ["Wache"]
    assert _rollen_treffer("The watcher tab") == ["watcher"]
    assert _rollen_treffer("Deploy-Wache: pgrep") == []
    assert (
        _rollen_treffer("waechter_lauf.py wache.py -OhneWache TO_SPAWN_WACHE_SPEC")
        == []
    )


def test_skill_md_eine_aufsicht_je_spec() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "Je Spec gibt es genau eine Aufsicht: den Aufseher." in text
    assert "Aufpasser, Leitstand und Takt-Läufe sind seine Werkzeuge" in text
    assert "Aufseher-Loop" not in text


# --------------------------------------------------------------------- a) nest_server.sh


def _bashrc_funktionen() -> str:
    """Funktionsblock der .bashrc aus nest_server.sh (von ``_bau_py()`` bis ``sessions()``)."""
    text = NEST.read_text(encoding="utf-8")
    m = re.search(
        r"^_bau_py\(\) \{.*?^sessions\(\) \{[^\n]*\n", text, re.DOTALL | re.MULTILINE
    )
    assert m, "Funktionsblock _bau_py … sessions() fehlt in nest_server.sh"
    return m.group(0)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_nest_bashrc_aufseher_startet_wache_py(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "wache.py").write_text(
        "import sys\nprint('WACHE_PY', *sys.argv[1:])\n", encoding="utf-8"
    )
    block = tmp_path / "funktionen.sh"
    block.write_text(_bashrc_funktionen(), encoding="utf-8")
    skript = f'export REPO="{repo}"; source "{block}"; type aufseher >/dev/null && aufseher 42 --dry-run; wache 7'
    lauf = subprocess.run(
        ["bash", "-c", skript], capture_output=True, text=True, check=False, timeout=30
    )
    assert lauf.returncode == 0, lauf.stderr
    assert "WACHE_PY 42 --dry-run" in lauf.stdout
    assert "WACHE_PY 7" in lauf.stdout  # wache bleibt als Alias


def test_nest_bestehende_bashrc_bekommt_aufseher_nachgetragen() -> None:
    """Server mit älterem Nest-Kopf (ohne aufseher) bekommen die Funktion idempotent nachgetragen."""
    text = NEST.read_text(encoding="utf-8")
    assert re.search(r"grep -q[^\n]*aufseher\(\)", text), (
        "Nachtrag für bestehende .bashrc fehlt"
    )
    assert text.count('aufseher() { _bau_py wache.py "$@"; }') >= 2


# --------------------------------------------------------------------- b) install.ps1


def test_install_ps1_registriert_aufseher_neben_wache() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    m = re.search(r"function aufseher \{(.*?)\n\}", text, re.DOTALL)
    assert m, "function aufseher fehlt in install.ps1"
    assert 'python "./scripts/wache.py" @args' in m.group(1)
    assert re.search(r"function wache \{", text), "wache muss als Alias bleiben"
    # eigener Block, damit Profile mit bau/wache/sessions aufseher trotzdem bekommen
    assert '$profil -match "function aufseher\\b"' in text


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh fehlt")
def test_install_ps1_aufseher_funktion_laeuft_echt(tmp_path: Path) -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    m = re.search(r"(function aufseher \{.*?\n\})", text, re.DOTALL)
    assert m
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "wache.py").write_text(
        "import sys\nprint('WACHE_PY', *sys.argv[1:])\n", encoding="utf-8"
    )
    python = shutil.which("python3") or "python3"
    funktion = m.group(1).replace(
        'python "./scripts/wache.py"', f'& "{python}" "./scripts/wache.py"'
    )
    lauf = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", f"{funktion}\naufseher 9"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "WACHE_PY 9" in lauf.stdout, lauf.stdout + lauf.stderr


# --------------------------------------------------------------------- Rückwärtskompatibilität


def test_capo_alte_waechter_marke_bleibt_eigener_kommentar() -> None:
    """Kommentare von vor #428 („Wächter: …“) dürfen nie als Davids Antwort gelten."""
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    from to_spawn import capo

    seit = datetime(2026, 10, 1, tzinfo=timezone.utc)
    kommentare = [
        {
            "user": {"login": "x"},
            "body": "Wächter: alt",
            "created_at": "2026-10-01T10:00:00Z",
        },
        {
            "user": {"login": "x"},
            "body": "Aufseher: neu",
            "created_at": "2026-10-01T11:00:00Z",
        },
    ]
    assert capo.WAECHTER_KOPF == "Aufseher:"
    assert capo.davids_antwort(kommentare, seit) == ""
    kommentare.append(
        {
            "user": {"login": "david"},
            "body": "passt",
            "created_at": "2026-10-01T12:00:00Z",
        }
    )
    assert capo.davids_antwort(kommentare, seit) == "david"
