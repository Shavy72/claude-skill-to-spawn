"""Ticket #313: Leitstand — gemeinsame Sperren und Zustand.

Weg-Tests mit ECHTEN Prozessen (eigenes Python-Modul ``to_spawn.leitstand``
als Unterprozess), echten Datei-Sperren und echtem Dateisystem. Keine
Attrappen. Jeder Test setzt ``TO_SPAWN_LEITSTAND_ORDNER`` auf ``tmp_path``,
damit Läufe sich nie in die Quere kommen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

PY = sys.executable
WARTE_ZEITLIMIT_S = 20.0


def _umgebung(ordner: Path) -> dict:
    env = dict(os.environ)
    env["TO_SPAWN_LEITSTAND_ORDNER"] = str(ordner)
    return env


def _halte_prozess(
    ordner: Path, name: str, halter: str, bis_datei: Path
) -> subprocess.Popen:
    """Startet einen Unterprozess, der ``leitstand halte`` ausführt."""
    return subprocess.Popen(
        [
            PY,
            "-m",
            "to_spawn.leitstand",
            "halte",
            name,
            halter,
            "--bis",
            str(bis_datei),
        ],
        cwd=str(SKILL),
        env=_umgebung(ordner),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _warte_bis(
    bedingung, zeitlimit_s: float = WARTE_ZEITLIMIT_S, takt_s: float = 0.05
) -> bool:
    """Pollt ``bedingung`` bis sie wahr ist oder das Zeitlimit reißt."""
    ende = time.monotonic() + zeitlimit_s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(takt_s)
    return bedingung()


def _stand_cli(ordner: Path, name: str | None = None) -> list[dict]:
    argv = [PY, str(SKILL / "to_spawn.py"), "leitstand", "stand", "--json"]
    if name is not None:
        argv += ["--name", name]
    ausgabe = subprocess.run(
        argv,
        cwd=str(SKILL),
        env=_umgebung(ordner),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert ausgabe.returncode == 0, ausgabe.stderr
    return json.loads(ausgabe.stdout)


def _stand_von(ordner: Path, name: str) -> dict | None:
    for eintrag in _stand_cli(ordner, name):
        if eintrag["name"] == name:
            return eintrag
    return None


def _beende(*prozesse: subprocess.Popen) -> None:
    for p in prozesse:
        if p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
            p.wait(timeout=10)


def test_zweiter_prozess_wartet_und_bekommt_sperre(tmp_path: Path) -> None:
    a_los = tmp_path / "a_los"
    b_los = tmp_path / "b_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    b = None
    try:
        assert _warte_bis(lambda: Path(f"{a_los}.hat").exists()), (
            "A hat die Sperre nicht bekommen"
        )
        b = _halte_prozess(tmp_path, "gate", "B", b_los)

        time.sleep(1.0)
        assert not Path(f"{b_los}.hat").exists(), (
            "B hätte nicht sofort die Sperre bekommen dürfen"
        )

        eintrag = _warte_bis_stand(tmp_path, "gate")
        assert eintrag["halter"]["halter"] == "A"
        assert any(w["halter"] == "B" for w in eintrag["wartende"])

        a_los.touch()
        assert _warte_bis(lambda: Path(f"{b_los}.hat").exists()), (
            "B hat die Sperre nicht bekommen"
        )
        eintrag = _stand_von(tmp_path, "gate")
        assert eintrag["halter"]["halter"] == "B"

        b_los.touch()
        assert a.wait(timeout=20) == 0
        assert b.wait(timeout=20) == 0
        eintrag = _stand_von(tmp_path, "gate")
        assert eintrag["halter"] is None
    finally:
        _beende(a, *([b] if b else []))


def _warte_bis_stand(ordner: Path, name: str) -> dict:
    ergebnis: dict = {}

    def bedingung() -> bool:
        eintrag = _stand_von(ordner, name)
        if eintrag and eintrag.get("halter"):
            ergebnis["eintrag"] = eintrag
            return True
        return False

    _warte_bis(bedingung)
    assert "eintrag" in ergebnis, "Sperre wurde nie gehalten angezeigt"
    return ergebnis["eintrag"]


def test_gekillter_halter_gibt_sperre_frei(tmp_path: Path) -> None:
    a_los = tmp_path / "a_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    try:
        assert _warte_bis(lambda: Path(f"{a_los}.hat").exists())
        a.kill()
        a.wait(timeout=20)

        assert _warte_bis(
            lambda: (_stand_von(tmp_path, "gate") or {}).get("halter") is None
        ), "veraltete Halter-Datei zählt fälschlich als gehalten"

        from to_spawn import leitstand

        os.environ["TO_SPAWN_LEITSTAND_ORDNER"] = str(tmp_path)
        try:
            schein = leitstand.versuche("gate", "C")
            assert schein is not None
            schein.freigeben()
        finally:
            del os.environ["TO_SPAWN_LEITSTAND_ORDNER"]
    finally:
        _beende(a)


def test_gekillter_wartender_verschwindet_aus_reihe(tmp_path: Path) -> None:
    a_los = tmp_path / "a_los"
    b_los = tmp_path / "b_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    # B erst starten, wenn A hält — sonst kann B unter Last zuerst in der Reihe stehen.
    assert _warte_bis(lambda: Path(f"{a_los}.hat").exists())
    b = _halte_prozess(tmp_path, "gate", "B", b_los)
    try:
        assert _warte_bis(
            lambda: any(
                w["halter"] == "B"
                for w in (_stand_von(tmp_path, "gate") or {}).get("wartende", [])
            )
        )
        b.kill()
        b.wait(timeout=20)

        assert _warte_bis(
            lambda: (_stand_von(tmp_path, "gate") or {}).get("wartende") == []
        )
    finally:
        a_los.touch()
        _beende(a, b)


def test_reihe_fifo(tmp_path: Path) -> None:
    a_los = tmp_path / "a_los"
    b_los = tmp_path / "b_los"
    c_los = tmp_path / "c_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    b = None
    c = None
    try:
        assert _warte_bis(lambda: Path(f"{a_los}.hat").exists())
        b = _halte_prozess(tmp_path, "gate", "B", b_los)
        assert _warte_bis(
            lambda: any(
                w["halter"] == "B"
                for w in (_stand_von(tmp_path, "gate") or {}).get("wartende", [])
            )
        )
        c = _halte_prozess(tmp_path, "gate", "C", c_los)
        assert _warte_bis(
            lambda: any(
                w["halter"] == "C"
                for w in (_stand_von(tmp_path, "gate") or {}).get("wartende", [])
            )
        )

        a_los.touch()
        assert _warte_bis(lambda: Path(f"{b_los}.hat").exists()), (
            "B hätte als Erstes drankommen müssen"
        )
        time.sleep(0.5)
        assert not Path(f"{c_los}.hat").exists(), (
            "C hätte nicht vor B die Sperre bekommen dürfen"
        )
    finally:
        b_los.touch()
        c_los.touch()
        _beende(a, *([b] if b else []), *([c] if c else []))


def test_cli_stand_text(tmp_path: Path) -> None:
    a_los = tmp_path / "a_los"
    b_los = tmp_path / "b_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    b = None
    try:
        assert _warte_bis(lambda: Path(f"{a_los}.hat").exists())
        b = _halte_prozess(tmp_path, "gate", "B", b_los)
        assert _warte_bis(
            lambda: any(
                w["halter"] == "B"
                for w in (_stand_von(tmp_path, "gate") or {}).get("wartende", [])
            )
        )

        ausgabe = subprocess.run(
            [PY, str(SKILL / "to_spawn.py"), "leitstand", "stand"],
            cwd=str(SKILL),
            env=_umgebung(tmp_path),
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert ausgabe.returncode == 0
        assert "gate: gehalten von A" in ausgabe.stdout
        assert "wartet: B" in ausgabe.stdout

        ausgabe_frei = subprocess.run(
            [
                PY,
                str(SKILL / "to_spawn.py"),
                "leitstand",
                "stand",
                "--name",
                "leere-sperre",
            ],
            cwd=str(SKILL),
            env=_umgebung(tmp_path),
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert ausgabe_frei.returncode == 0
        assert "frei" in ausgabe_frei.stdout
    finally:
        a_los.touch()
        b_los.touch()
        _beende(a, *([b] if b else []))


def test_zustand_paralleles_schreiben(tmp_path: Path) -> None:
    skript = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(SKILL)!r})\n"
        "from to_spawn import leitstand\n"
        "for _ in range(25):\n"
        "    def _plus_eins(z):\n"
        "        z['zaehler'] = z.get('zaehler', 0) + 1\n"
        "        return z\n"
        "    leitstand.aendere_zustand(_plus_eins)\n"
        "leitstand.neue_entscheidung(1, 'entscheidung-' + os.environ['PROZESS_ID'])\n"
    )
    prozesse = []
    try:
        for i in range(8):
            env = _umgebung(tmp_path)
            env["PROZESS_ID"] = str(i)
            prozesse.append(
                subprocess.Popen([PY, "-c", skript], cwd=str(SKILL), env=env)
            )
        for p in prozesse:
            assert p.wait(timeout=30) == 0

        from to_spawn import leitstand

        os.environ["TO_SPAWN_LEITSTAND_ORDNER"] = str(tmp_path)
        try:
            zustand = leitstand.lese_zustand()
        finally:
            del os.environ["TO_SPAWN_LEITSTAND_ORDNER"]

        assert zustand["zaehler"] == 200
        assert len(zustand["entscheidungen"]) == 8
        zustand_datei = tmp_path / "zustand.json"
        json.loads(zustand_datei.read_text(encoding="utf-8"))
    finally:
        _beende(*prozesse)


def test_zustand_bereiche(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))

    from to_spawn import leitstand

    leitstand.setze_notiz("bau1", "laeuft")
    assert leitstand.notiz("bau1") == "laeuft"
    assert leitstand.notiz("unbekannt") is None

    leitstand.neuer_reopen(313, "grund-x")
    zustand = leitstand.lese_zustand()
    assert zustand["reopens"][-1]["ticket"] == 313
    assert zustand["reopens"][-1]["grund"] == "grund-x"

    leitstand.neue_entscheidung(313, "text-y")
    zustand = leitstand.lese_zustand()
    assert zustand["entscheidungen"][-1]["text"] == "text-y"

    assert leitstand.leiter_stufe(313) == 0
    leitstand.setze_leiter_stufe(313, 2)
    assert leitstand.leiter_stufe(313) == 2


def test_zustand_kaputt_nicht_ueberschreiben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))
    zustand_datei = tmp_path / "zustand.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    zustand_datei.write_text("kein-gueltiges-json{{{", encoding="utf-8")

    from to_spawn import leitstand

    with pytest.raises(leitstand.ZustandKaputt):
        leitstand.lese_zustand()

    with pytest.raises(leitstand.ZustandKaputt):
        leitstand.aendere_zustand(lambda z: z)

    assert zustand_datei.read_text(encoding="utf-8") == "kein-gueltiges-json{{{"

    ausgabe = subprocess.run(
        [PY, str(SKILL / "to_spawn.py"), "leitstand", "zustand"],
        cwd=str(SKILL),
        env=_umgebung(tmp_path),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert ausgabe.returncode == 1


def test_sperr_name_pruefung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))

    from to_spawn import leitstand

    with pytest.raises(ValueError):
        leitstand.versuche("../x", "A")


def test_warte_s_zeitlimit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))
    a_los = tmp_path / "a_los"
    a = _halte_prozess(tmp_path, "gate", "A", a_los)
    try:
        assert _warte_bis(lambda: Path(f"{a_los}.hat").exists())

        from to_spawn import leitstand

        with pytest.raises(leitstand.SperreBelegt):
            with leitstand.sperre("gate", "T", warte_s=0.5):
                pass

        eintrag = _stand_von(tmp_path, "gate")
        assert not any(w["halter"] == "T" for w in (eintrag or {}).get("wartende", []))
    finally:
        a_los.touch()
        _beende(a)


# --- Fixrunde: Wartezettel-Anlegen gegen fremde Probe (Bau-Server-Flake) ------

_PROBIERER = """
import sys, time
sys.path.insert(0, {skill!r})
from to_spawn import leitstand
ende = time.monotonic() + {dauer}
while time.monotonic() < ende:
    leitstand.stand("gate")
"""


def test_wartezettel_anlegen_waehrend_fremder_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zwei Prozesse proben die Reihe pausenlos (wie ``leitstand stand`` oder
    andere Wartende). Wer gerade seinen Wartezettel anlegt, darf dabei nicht
    abstürzen, nur weil die Probe den Zettel für einen Augenblick sperrt."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))
    from to_spawn import leitstand

    probierer = [
        subprocess.Popen(
            [PY, "-c", _PROBIERER.format(skill=str(SKILL), dauer=60)],
            env=_umgebung(tmp_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(2)
    ]
    try:
        time.sleep(1.0)  # Probierer laufen an
        for _ in range(400):
            with leitstand.sperre("gate", "T", warte_s=10):
                pass
    finally:
        _beende(*probierer)


def test_interne_sperrnamen_abgelehnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Namen mit „_“ vorn gehören dem Leitstand selbst (``_zustand``) — von außen verboten."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))
    from to_spawn import leitstand

    with pytest.raises(ValueError):
        leitstand.versuche("_zustand", "A")
