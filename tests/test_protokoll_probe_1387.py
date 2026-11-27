"""Tests für die Git-Plumbing-Probe auf dem verwaisten Zweig ``protokoll`` (#901 / App #1387)."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

from probe.protokoll_probe import (
    DEFAULT_DATEI,
    DEFAULT_ZWEIG,
    anhaengen_plumbing,
    lese_zweig_inhalt,
)
from to_spawn.prozessbaum import fensterlos


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        **fensterlos(),
    )


def _erstelle_bare_und_klone(tmp_path: Path, anzahl_klone: int = 2) -> tuple[Path, list[Path]]:
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "--bare")

    klone: list[Path] = []
    for idx in range(anzahl_klone):
        klon = tmp_path / f"schreiber_{idx}"
        klon.mkdir()
        _git(klon, "init")
        _git(klon, "remote", "add", "origin", str(bare))
        klone.append(klon)
    return bare, klone


def test_legt_verwaisten_zweig_ohne_checkout_an(tmp_path: Path) -> None:
    """Erster Schreibvorgang legt den verwaisten Zweig ``protokoll`` an, ohne den Arbeitsordner anzufassen."""
    bare, [klon] = _erstelle_bare_und_klone(tmp_path, anzahl_klone=1)

    ergebnis = anhaengen_plumbing(
        repo_pfad=klon,
        remote="origin",
        zweig=DEFAULT_ZWEIG,
        dateiname=DEFAULT_DATEI,
        zeile=json.dumps({"ereignis": "start", "nr": 1}),
    )

    assert ergebnis.erfolg is True
    assert ergebnis.versuche == 1
    assert ergebnis.commit_sha

    # Arbeitsordner bleibt komplett leer (kein Checkout, kein HEAD-Wechsel).
    assert list(klon.iterdir()) == [klon / ".git"]

    # Zweig existiert auf dem Bare-Repo und hat genau einen verwaisten Commit (0 Eltern).
    parents = _git(bare, "rev-list", "--parents", "-n", "1", f"refs/heads/{DEFAULT_ZWEIG}").stdout.strip().split()
    assert len(parents) == 1

    inhalt = lese_zweig_inhalt(bare, DEFAULT_ZWEIG, DEFAULT_DATEI)
    assert inhalt.splitlines() == [json.dumps({"ereignis": "start", "nr": 1})]


def test_zwei_gleichzeitige_schreiber_verlustfrei(tmp_path: Path) -> None:
    """Zwei parallele Schreiber auf dasselbe Bare-Repo verlieren bei Push-Konflikten keine Zeile."""
    bare, [klon_a, klon_b] = _erstelle_bare_und_klone(tmp_path, anzahl_klone=2)

    # Basis-Commit anlegen, damit beide Schreiber auf demselben Stand starten.
    basis = anhaengen_plumbing(
        repo_pfad=klon_a,
        remote="origin",
        zweig=DEFAULT_ZWEIG,
        dateiname=DEFAULT_DATEI,
        zeile=json.dumps({"schreiber": "init", "nr": 0}),
    )
    assert basis.erfolg is True

    zeilen_je_schreiber = 6

    def _schreibe_serie(klon: Path, name: str) -> list[int]:
        versuche_liste: list[int] = []
        for nr in range(1, zeilen_je_schreiber + 1):
            res = anhaengen_plumbing(
                repo_pfad=klon,
                remote="origin",
                zweig=DEFAULT_ZWEIG,
                dateiname=DEFAULT_DATEI,
                zeile=json.dumps({"schreiber": name, "nr": nr}),
                max_versuche=25,
            )
            assert res.erfolg is True
            versuche_liste.append(res.versuche)
        return versuche_liste

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_schreibe_serie, klon_a, "A")
        fut_b = pool.submit(_schreibe_serie, klon_b, "B")
        fut_a.result()
        fut_b.result()

    inhalt = lese_zweig_inhalt(bare, DEFAULT_ZWEIG, DEFAULT_DATEI)
    zeilen = [json.loads(z) for z in inhalt.splitlines() if z.strip()]

    # Alle 1 + 6 + 6 = 13 Zeilen müssen vollständig erhalten sein.
    assert len(zeilen) == 1 + 2 * zeilen_je_schreiber
    assert {(z["schreiber"], z["nr"]) for z in zeilen} == (
        {("init", 0)}
        | {("A", nr) for nr in range(1, zeilen_je_schreiber + 1)}
        | {("B", nr) for nr in range(1, zeilen_je_schreiber + 1)}
    )

    # Arbeitsordner beider Schreiber bleiben unberührt.
    assert list(klon_a.iterdir()) == [klon_a / ".git"]
    assert list(klon_b.iterdir()) == [klon_b / ".git"]
