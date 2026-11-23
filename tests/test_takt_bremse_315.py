"""Skill-Issue #315: Bremse für offene Briefkasten-Fragen im Aufseher-Takt + Exit-Codes von ``antwort``.

Weg-Tests in der #293-Welt (echter ``to_spawn.py takt``-Unterprozess, echter Leitstand, echtes
Git-Repo); GitHub = Aufzeichnungs-Skript mit der echten Kommentar-Form (``kommentar_315.json``),
Claude = Aufzeichnungs-Skript.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from test_waechter_takt_316 import CLAUDE_AUFZ, PY, _aufrufe, _merker, _takt, welt  # noqa: E402,F401


def _kommentare(welt: dict[str, Any], ticket: str) -> list[str]:
    datei = Path(welt["env"]["GH_AUFZEICHNUNG_KOMMENTARE"])
    if not datei.is_file():
        return []
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]
    return [str(z["body"]) for z in zeilen if z["issue"] == ticket]


def _zeige_aufrufe(welt: dict[str, Any], fid: str) -> int:
    return len([a for a in _aufrufe(welt) if f"Briefkasten-Frage {fid}" in str(a)])


def _merker_zuruecksetzen(welt: dict[str, Any], schluessel: str, minuten: int) -> None:
    """``zuletzt`` des Zeige-Zählers in die Vergangenheit legen (wie nach ``minuten`` ohne Takt)."""
    datei = Path(welt["env"]["TO_SPAWN_LEITSTAND_ORDNER"]) / "zustand.json"
    zustand = json.loads(datei.read_text(encoding="utf-8"))
    alt = datetime.now().astimezone() - timedelta(minutes=minuten)
    zustand["takt"]["293"]["gezeigt"][schluessel]["zuletzt"] = alt.isoformat(timespec="seconds")
    datei.write_text(json.dumps(zustand), encoding="utf-8")


def test_offene_klasse_3_frage_gebremst_und_einmal_gemeldet(welt: dict[str, Any], monkeypatch: Any) -> None:
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Ordner neu anlegen?", "neu", 3, [], hauptbaum=welt["repo"])
    for _ in range(6):
        lauf = _takt(welt, "--claude", str(CLAUDE_AUFZ))
        assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _zeige_aufrufe(welt, fid) == 3, "nach dem dritten Zeigen keine Session je Takt mehr"
    meldungen = [k for k in _kommentare(welt, "297") if fid in k]
    assert len(meldungen) == 1, meldungen
    assert "braucht den Nutzer" in meldungen[0] and "nutzer-antwort 297" in meldungen[0]
    _merker_zuruecksetzen(welt, f"297:{fid}", 31)
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert _zeige_aufrufe(welt, fid) == 4, "nach 30 min kommt die Frage wieder"
    assert len([k for k in _kommentare(welt, "297") if fid in k]) == 1, "Meldung an den Nutzer nur einmal"


def test_klasse_2_kommt_wieder_bis_kommentar_belegt(welt: dict[str, Any], monkeypatch: Any) -> None:
    from to_spawn import briefkasten

    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen("297", "Live schalten?", "nein", 2, [], hauptbaum=welt["repo"])
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    # Takt-Session endete ohne Kommentar an den Nutzer → die Frage kommt wieder.
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert _zeige_aufrufe(welt, fid) == 2
    # Kommentar des Aufsehers wie im Befugnis-Text (über dieselbe gh-Tür wie die Session).
    text = f"Aufseher: braucht den Nutzer: Live schalten? — bitte: `to_spawn.py nutzer-antwort 297 --id {fid} …`"
    gh = subprocess.run(
        [
            PY,
            welt["env"]["TO_SPAWN_GH_STUB"],
            "issue",
            "comment",
            "297",
            "--body",
            text,
        ],
        env=welt["env"],
        check=False,
    )
    assert gh.returncode == 0
    assert _takt(welt, "--claude", str(CLAUDE_AUFZ)).returncode == 0
    assert _zeige_aufrufe(welt, fid) == 2, "belegter Kommentar: Klasse 2 nicht mehr zeigen"
    assert f"297:{fid}" in (_merker(welt) or {}).get("belegt", [])


def test_unbekannte_klasse_kommt_nicht_jeden_takt_wieder(monkeypatch: Any, tmp_path: Path) -> None:
    from to_spawn import briefkasten, waechter_takt

    frage = {
        "ticket": "7",
        "id": "ab12",
        "klasse": "9",
        "frage": "Was?",
        "vorschlag": "x",
        "ts": "",
    }
    monkeypatch.setattr(briefkasten, "offene", lambda *_a, **_k: [frage])
    ((text, _feld, schluessel), kommt_wieder) = waechter_takt._briefkasten_offen(tmp_path, [7])[0]
    assert schluessel == "7:ab12" and kommt_wieder is False
    assert "du entscheidest" not in text and "wartet auf den Nutzer" in text


def _antwort(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, str(SKILL / "to_spawn.py"), "antwort", *args],
        cwd=str(SKILL),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_antwort_aufruf_fehler_eigener_code() -> None:
    from to_spawn import briefkasten_cli

    assert briefkasten_cli.AUFRUF_FEHLER not in (0, 2, 3, 4)
    for args in (
        (),
        ("7", "--id"),
        ("7", "--id", "ab", "--quatsch"),
        ("sieben", "--id", "ab"),
        ("7",),
    ):
        lauf = _antwort(*args)
        assert lauf.returncode == briefkasten_cli.AUFRUF_FEHLER, (
            args,
            lauf.returncode,
            lauf.stderr,
        )


def test_kopf_text_nennt_alle_codes_von_antwort() -> None:
    from to_spawn import briefkasten_cli

    kopf = briefkasten_cli.__doc__ or ""
    abschnitt = re.search(r"``antwort`` (.*?)\s·\s``antworten``", kopf, re.DOTALL)
    assert abschnitt, kopf
    codes = set(re.findall(r"\b(\d) = ", abschnitt.group(1)))
    assert codes == {"0", "1", "2", "3", "4", str(briefkasten_cli.AUFRUF_FEHLER)}, codes


def test_klasse_2_weitergabe_eine_quelle() -> None:
    from to_spawn import befugnis

    quelle = (SKILL / "to_spawn" / "befugnis.py").read_text(encoding="utf-8")
    assert quelle.count(befugnis.KLASSE_2_WEITERGABE) == 1, "Weitergabe-Wendung als Literal neben der Konstante"
    assert quelle.count("nie selbst freigeben") == 1
    assert befugnis.aufseher_text("x").count(befugnis.KLASSE_2_WEITERGABE) == 3
