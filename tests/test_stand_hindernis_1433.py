"""#1433: ``/stand`` zeigt offene Hindernisse der letzten 24 h als eine Zeile.

Wissen über das Log (Format, 24-h-Fenster, „offen“) lebt nur in
``to_spawn/hindernis.py``; ``skills/stand/stand.py`` ruft ``hindernis.kurzzeile()``
und druckt das Ergebnis. Fehlt Log oder Modul, zeigt ``/stand`` nichts und bricht nie ab.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import hindernis

SKILL = Path(__file__).resolve().parents[1]
STAND_PY = SKILL / "skills" / "stand" / "stand.py"
JETZT = datetime(2026, 11, 21, 12, 0).astimezone()


def _schreiben(datei: Path, zeilen: list[object]) -> None:
    datei.write_text(
        "\n".join(z if isinstance(z, str) else json.dumps(z) for z in zeilen) + "\n",
        encoding="utf-8",
    )


def _zeile(stunden_her: float, art: str = "platte_voll", **felder: object) -> dict:
    zeit = (JETZT - timedelta(hours=stunden_her)).isoformat(timespec="seconds")
    return {"zeit": zeit, "art": art, "text": f"{art} vor {stunden_her} h", **felder}


# --- (a) Lese-Funktion in hindernis.py -------------------------------------------


def test_offene_nur_letzte_24h_und_nicht_erledigt(tmp_path: Path) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(
        datei,
        [
            _zeile(30),  # zu alt
            _zeile(5, art="platte_unmessbar", erledigt=True),  # erledigt
            _zeile(3),
            _zeile(1),
        ],
    )
    offen = hindernis.offene(jetzt=JETZT, datei=datei)
    assert [z["text"] for z in offen] == ["platte_voll vor 1 h"]  # je Art die jüngste
    assert offen[0]["anzahl"] == 2


def test_offene_kaputte_zeilen_und_fehlende_datei_leer(tmp_path: Path) -> None:
    assert hindernis.offene(jetzt=JETZT, datei=tmp_path / "fehlt.jsonl") == []
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(
        datei,
        ["{kaputt", "[1, 2]", {"art": "x", "text": "ohne Zeit"}, {"zeit": "Unsinn"}],
    )
    assert hindernis.offene(jetzt=JETZT, datei=datei) == []
    assert hindernis.kurzzeile(jetzt=JETZT, datei=datei) is None


def test_kurzzeile_eine_zeile_fuer_mehrere_arten(tmp_path: Path) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(datei, [_zeile(2), _zeile(1), _zeile(4, art="platte_unmessbar")])
    zeile = hindernis.kurzzeile(jetzt=JETZT, datei=datei)
    assert zeile is not None and "\n" not in zeile
    assert zeile.startswith("Hindernisse (24 h): ")
    assert "platte_voll vor 1 h (2×" in zeile and "platte_unmessbar vor 4 h" in zeile


# --- (b) stand.py druckt genau eine Hindernis-Zeile ------------------------------


@pytest.fixture
def stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Lädt ``skills/stand/stand.py`` in einem leeren DuoPlus-Klon (kein GitHub, kein ssh)."""
    klon = tmp_path / "duoplus-test"
    klon.mkdir()
    subprocess.run(["git", "init", "-q", str(klon)], check=True)
    monkeypatch.chdir(klon)
    monkeypatch.setenv("TO_SPAWN_HOME", str(SKILL))
    spec = importlib.util.spec_from_file_location("stand_1433", STAND_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stand_1433"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "sh", lambda *a, **k: "")
    monkeypatch.setattr(mod, "sessions_lokal", lambda nr: {})
    fertig = mod.Ticket(1, "t", False, None, None, [], [], "", None)
    monkeypatch.setattr(
        mod, "spec_laden", lambda *a, **k: mod.Spec(7, "S", "F", [fertig])
    )
    return mod


def _hindernis_zeilen(text: str) -> list[str]:
    return [z for z in text.splitlines() if z.startswith("Hindernisse")]


def test_stand_zeigt_genau_eine_hindernis_zeile(
    stand: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    jetzt = datetime.now().astimezone()
    _schreiben(
        datei,
        [
            {"zeit": (jetzt - timedelta(hours=h)).isoformat(), "art": a, "text": a}
            for h, a in [
                (2, "platte_voll"),
                (1, "platte_voll"),
                (3, "platte_unmessbar"),
            ]
        ],
    )
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(datei))
    assert stand.kurz_modus(7, "Aufruf") == 0
    aus = capsys.readouterr().out
    assert len(_hindernis_zeilen(aus)) == 1
    assert aus.splitlines()[0].startswith("**Spec #7")  # Satz bleibt Zeile 1


def test_stand_ohne_spec_zeigt_hindernis_zeile(
    stand: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    datei = tmp_path / "hindernisse.jsonl"
    _schreiben(
        datei,
        [{"zeit": datetime.now().astimezone().isoformat(), "art": "a", "text": "a"}],
    )
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(datei))
    monkeypatch.setattr(stand, "spec_erkennen", lambda: (None, ""))
    monkeypatch.setattr(stand, "offene_specs", list)
    monkeypatch.setattr(sys, "argv", ["stand.py", "--ohne-fetch"])
    assert stand.main() == 0
    assert len(_hindernis_zeilen(capsys.readouterr().out)) == 1


def test_stand_leeres_log_oder_fehlendes_modul_keine_zeile(
    stand: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(tmp_path / "fehlt.jsonl"))
    assert stand.kurz_modus(7, "Aufruf") == 0
    assert _hindernis_zeilen(capsys.readouterr().out) == []
    monkeypatch.setenv("TO_SPAWN_HOME", str(tmp_path / "kein-skill"))
    assert stand.kurz_modus(7, "Aufruf") == 0
    assert _hindernis_zeilen(capsys.readouterr().out) == []


def test_install_sh_bringt_stand_mit() -> None:
    text = (SKILL / "install.sh").read_text(encoding="utf-8")
    assert re.search(r"skills/\*/", text), "install.sh verteilt skills/<name>/ nicht"


def test_keine_mail_im_neuen_code() -> None:
    verboten = re.compile(r"to_spawn_mail|telegram|smtplib|sendmail|melder", re.IGNORECASE)
    for datei in (STAND_PY, SKILL / "to_spawn" / "hindernis.py"):
        assert not verboten.search(datei.read_text(encoding="utf-8")), datei
