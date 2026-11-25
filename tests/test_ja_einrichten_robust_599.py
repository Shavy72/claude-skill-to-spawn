"""#599: ``ja_einrichten`` bei kaputten Hook-Einträgen + eine Stelle für die Sperre-Erkennung."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

from to_spawn import briefkasten, ja_einrichten


def _schreibe(claude: Path, daten: object) -> Path:
    claude.mkdir(parents=True, exist_ok=True)
    datei = claude / "settings.json"
    datei.write_text(json.dumps(daten), encoding="utf-8")
    return datei


def _main(claude: Path) -> int:
    return ja_einrichten.main(["--claude-dir", str(claude), "--python", sys.executable])


@pytest.mark.parametrize(
    ("daten", "stichwort"),
    [
        ({"hooks": None}, "hooks"),
        ({"hooks": {"PreToolUse": None}}, "PreToolUse"),
        ({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": None}]}}, "hooks"),
    ],
    ids=["hooks-null", "pretooluse-null", "block-hooks-null"],
)
def test_hooks_null_klare_meldung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, daten: object, stichwort: str
) -> None:
    """``null`` statt Objekt/Liste → Exit 1 mit Meldung, die den Schlüssel nennt; Datei bleibt."""
    claude = tmp_path / ".claude"
    datei = _schreibe(claude, daten)
    vorher = datei.read_text(encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="to_spawn.ja_einrichten"):
        assert _main(claude) == 1
    meldung = " ".join(r.getMessage() for r in caplog.records)
    assert "Ja-Sperre nicht eingetragen" in meldung
    assert f"{stichwort} ist null" in meldung, meldung
    assert datei.read_text(encoding="utf-8") == vorher


@pytest.mark.parametrize("befehl", ["", "   ", None], ids=["leer", "leerzeichen", "null"])
def test_leeres_command_klare_meldung(tmp_path: Path, caplog: pytest.LogCaptureFixture, befehl: object) -> None:
    """Eintrag ohne Befehlstext: Warnung statt Traceback, Eintrag bleibt, Sperre wird eingetragen."""
    claude = tmp_path / ".claude"
    kaputt = {"type": "command", "command": befehl}
    datei = _schreibe(claude, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [kaputt]}]}})
    with caplog.at_level(logging.WARNING, logger="to_spawn.ja_einrichten"):
        assert _main(claude) == 0
    warnungen = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ohne Befehl" in w for w in warnungen), warnungen
    blocks = json.loads(datei.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert blocks[0]["hooks"] == [kaputt]
    assert briefkasten.sperre_eingetragen_in({"hooks": {"PreToolUse": blocks}})


def test_beide_aufrufer_nutzen_dieselbe_erkennung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine Stelle: ``ja_einrichten`` und ``briefkasten.sperre_eingetragen_in`` fragen dieselbe Funktion."""
    echt = briefkasten.ist_sperre_befehl
    aufrufe: list[tuple[object, bool]] = []

    def merken(befehl: object, *, nur_aktiv: bool = False) -> bool:
        aufrufe.append((befehl, nur_aktiv))
        return echt(befehl, nur_aktiv=nur_aktiv)

    monkeypatch.setattr(briefkasten, "ist_sperre_befehl", merken)
    claude = tmp_path / ".claude"
    fremd = {"type": "command", "command": "echo fremd"}
    _schreibe(claude, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [fremd]}]}})
    ja_einrichten.sperre_eintragen(claude, sys.executable, zeit="t")
    assert any(not aktiv for _, aktiv in aufrufe), aufrufe  # Einrichten: breite Regel
    aufrufe.clear()
    einstellungen = json.loads((claude / "settings.json").read_text(encoding="utf-8"))
    assert briefkasten.sperre_eingetragen_in(einstellungen) is True
    assert any(aktiv for _, aktiv in aufrufe), aufrufe  # Nachweis: nur aktive Sperre zählt


def test_erkennung_wird_nicht_enger(tmp_path: Path) -> None:
    """Toter Eintrag (Datei weg) wird beim Einrichten weiter erkannt und ersetzt, zählt aber nicht als aktiv."""
    tot = f"{Path(sys.executable).as_posix()} {(tmp_path / 'weg' / 'to_spawn.py').as_posix()} {briefkasten.JA_SPERRE_HOOK}"
    assert briefkasten.ist_sperre_befehl(tot) is True
    assert briefkasten.ist_sperre_befehl(tot, nur_aktiv=True) is False
    claude = tmp_path / ".claude"
    datei = _schreibe(
        claude, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": tot}]}]}}
    )
    assert ja_einrichten.sperre_eintragen(claude, sys.executable, zeit="t") is True
    befehle = [
        h["command"] for b in json.loads(datei.read_text(encoding="utf-8"))["hooks"]["PreToolUse"] for h in b["hooks"]
    ]
    assert tot not in befehle and len(befehle) == 1
