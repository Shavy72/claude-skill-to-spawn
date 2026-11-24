"""#455: Komplett-Prüfung — rot je Kopf nur einmal, Zeitgrenze, Takt nicht blockieren.

Echte Abläufe ohne Attrappen: tmp-Git-Repo als Klon, echter kleiner Prüf-Befehl (Zähldatei aus
``test_komplett_je_spec_425``), echte bash mit Unterprozessen für die Zeitgrenze und ein echter
Hintergrund-Prozess ``to_spawn.py komplett`` für den Takt.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from test_komplett_je_spec_425 import SPEC, _befehl, _commit, _git, _laeufe, _rot

from to_spawn import bash_wahl, config, komplett_lauf, prozessbaum
from to_spawn.pruef import leiter, protokoll
from to_spawn.pruefplan import Moment


def _klon(tmp_path: Path, pruefung: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Klon mit eingecheckter Konfig (der Hintergrund-Lauf liest sie selbst aus der Datei)."""
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    eigen = {
        "staging": {"url": "http://127.0.0.1:8001/"},
        "pruefung": {"schnell": "true", **pruefung},
    }
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(eigen), encoding="utf-8")
    (repo / ".gitignore").write_text(
        "\n".join(config.GITIGNORE_BLOCK) + "\n", encoding="utf-8"
    )
    _git(repo, "add", ".gitignore", ".to-spawn/config.json")
    _commit(repo, "a.txt")
    return repo, config.lade(repo)


def _warte_merker(klon: Path, kopf: str, frist_s: float = 60.0) -> komplett_lauf.Merker:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        merker = komplett_lauf.lies_merker(klon, SPEC)
        if merker is not None and merker.kopf == kopf:
            return merker
        time.sleep(0.2)
    raise AssertionError(f"kein Merker für {kopf[:7]} nach {frist_s} s")


def _protokoll(klon: Path) -> list[dict[str, Any]]:
    datei = klon / protokoll.ORDNER / protokoll.DATEI
    if not datei.exists():
        return []
    return [
        json.loads(z)
        for z in datei.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


# --- rot je Kopf nur einmal ---------------------------------------------------------------------


def test_takt_rot_je_kopf_nur_einmal_neu_bei_neuem_kopf_oder_von_hand(
    tmp_path: Path,
) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    _rot(zaehl)
    erst = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert (erst.gelaufen, erst.ok) == (True, False), erst
    zweit = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert (zweit.gelaufen, zweit.ok) == (False, False), zweit
    assert "schon rot" in zweit.grund and "Handlung" in zweit.grund, zweit.grund
    assert _laeufe(zaehl) == 1
    # Von Hand (Tür, ohne takt) läuft rot neu.
    hand = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (hand.gelaufen, hand.ok) == (True, False)
    assert _laeufe(zaehl) == 2
    # Neuer Kopf läuft im Takt einmal neu.
    zaehl.with_name("exit.txt").unlink()
    _commit(klon, "b.txt")
    neu = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert (neu.gelaufen, neu.ok) == (True, True), neu
    assert _laeufe(zaehl) == 3


# --- Takt nicht blockieren ----------------------------------------------------------------------


def test_takt_blockiert_nicht_und_startet_nie_doppelt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zaehl = tmp_path / "zaehl.txt"
    langsam = f"sleep 4; {_befehl(zaehl)}"
    klon, konfig = _klon(tmp_path, {"komplett": langsam})
    monkeypatch.setattr(komplett_lauf, "TAKT_WARTEN_S", 0.5)
    kopf = _git(klon, "rev-parse", "HEAD")
    start = time.monotonic()
    erst = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert time.monotonic() - start < 3.5, "Takt wartet auf die ganze Prüfung"
    assert erst.laeuft and erst.ok and not erst.gelaufen, erst
    zweit = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert zweit.laeuft, zweit
    merker = _warte_merker(klon, kopf)
    assert merker.gruen
    nach = komplett_lauf.komplett_einmal(
        klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True
    )
    assert (nach.gelaufen, nach.ok, nach.laeuft) == (False, True, False), nach
    assert _laeufe(zaehl) == 1


# --- Zeitgrenze ---------------------------------------------------------------------------------


def test_zeitgrenze_beendet_ganze_prozessgruppe(tmp_path: Path) -> None:
    pid_datei = tmp_path / "enkel.pid"
    befehl = f"sleep 60 & echo $! > {shlex.quote(str(pid_datei))}; wait"
    start = time.monotonic()
    stufe = leiter.eigener_befehl(tmp_path, befehl, "komplett", zeitgrenze_s=1.0)
    assert time.monotonic() - start < 15, "Ausgabe blieb offen — Enkel hält die Pipe"
    assert stufe.ergebnis == leiter.ROT, stufe
    assert "Zeitgrenze" in stufe.grund and "Zeitgrenze" in stufe.ausgabe, stufe
    enkel = int(pid_datei.read_text(encoding="utf-8").strip())
    ende = time.monotonic() + 5
    while prozessbaum.lebt(enkel) and time.monotonic() < ende:
        time.sleep(0.1)
    assert not prozessbaum.lebt(enkel) or "Z" in _zustand(enkel), (
        "Enkel lebt nach Zeitgrenze weiter"
    )


def _zustand(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return ""


def test_zeitgrenze_aus_konfig_ist_rot_mit_grund_und_gemerkt(tmp_path: Path) -> None:
    klon, konfig = _klon(
        tmp_path, {"komplett": "sleep 60", "komplett_zeitgrenze_min": 0.02}
    )
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (True, False), erg
    assert "Zeitgrenze" in erg.grund, erg.grund
    merker = komplett_lauf.lies_merker(klon, SPEC)
    assert (
        merker is not None
        and merker.exit == leiter.ROT
        and merker.grund == "Zeitgrenze"
    )


def test_zeitgrenze_vorgabe_und_kaputter_wert() -> None:
    assert config.komplett_zeitgrenze_s({}) == config.KOMPLETT_ZEITGRENZE_MIN * 60
    assert config.komplett_zeitgrenze_s(
        {"pruefung": {"komplett_zeitgrenze_min": "viel"}}
    ) == (config.KOMPLETT_ZEITGRENZE_MIN * 60)
    assert config.komplett_zeitgrenze_s(
        {"pruefung": {"komplett_zeitgrenze_min": -1}}
    ) == (config.KOMPLETT_ZEITGRENZE_MIN * 60)
    assert (
        config.komplett_zeitgrenze_s({"pruefung": {"komplett_zeitgrenze_min": 5}})
        == 300
    )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Prozessgruppen-Abbruch unter Windows nur dort prüfbar",
)
def test_zeitgrenze_windows_beendet_baum(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bash_wahl.lauf(
            ["-c", "sleep 60 & wait"],
            cwd=tmp_path,
            zeitgrenze_s=1.0,
            stdout=subprocess.PIPE,
            text=True,
        )
    assert time.monotonic() - start < 15


# --- Stand: schmutziger Baum, hinter origin ----------------------------------------------------


def test_ungespeicherte_aenderung_kein_lauf_kein_merker(tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    (klon / "a.txt").write_text("geändert\n", encoding="utf-8")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "ungespeichert" in erg.grund and "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_klon_hinter_origin_kein_lauf(tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    nackt = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(nackt))
    zweig = _git(klon, "rev-parse", "--abbrev-ref", "HEAD")
    _git(klon, "remote", "add", "origin", str(nackt))
    _git(klon, "push", "-q", "-u", "origin", zweig)
    anderer = tmp_path / "anderer"
    _git(tmp_path, "clone", "-q", str(nackt), str(anderer))
    _commit(anderer, "neu.txt")
    _git(anderer, "push", "-q", "origin", zweig)
    _git(klon, "fetch", "-q", "origin")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "origin" in erg.grund and "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 0


# --- Protokoll ----------------------------------------------------------------------------------


def test_lauf_steht_im_pruef_protokoll(tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    kopf = _git(klon, "rev-parse", "HEAD")
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    zeilen = [z for z in _protokoll(klon) if z.get("art") == "komplett"]
    assert len(zeilen) == 1, _protokoll(klon)
    assert zeilen[0]["ziel"] == kopf and zeilen[0]["urteil"] == "grün"
    assert zeilen[0]["stufen"][0]["name"] == "komplett"
