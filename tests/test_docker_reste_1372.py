"""#1372 (E42): Hausmeister gibt täglich Docker-Reste frei, nie Volumes, nie während Staging baut.

Weg-Test: echte Hausmeister-Tür ``aufraeumen_alles``, echte flock-Sperre wie
``staging_deploy.sh`` sie hält, echte Stand-Datei. Nur Docker selbst (externer Dienst)
ist eine Attrappe: ein Skript, das seine Argumente mitschreibt.
"""

from __future__ import annotations

import fcntl
import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from to_spawn import docker_reste, hausmeister

TAG = 24 * 3600


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    aufrufe = tmp_path / "docker_aufrufe.txt"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{aufrufe}"\n'
        'if [ -n "$STAGING_SPERRE_PRUEFEN" ]; then '
        'flock -n "$TO_SPAWN_STAGING_SPERRE" true && echo frei >> "$STAGING_SPERRE_PRUEFEN" '
        '|| echo gehalten >> "$STAGING_SPERRE_PRUEFEN"; fi\n'
        'if [ -n "$DOCKER_ATTRAPPE_EXIT" ]; then echo kaputt >&2; exit "$DOCKER_ATTRAPPE_EXIT"; fi\n'
        'echo "Total reclaimed space: 1.5GB"\n',
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    log_ordner = tmp_path / "hm"
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_LOG", str(log_ordner / "hausmeister.jsonl")
    )
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_DOCKER", str(docker))
    sperre = tmp_path / "staging" / ".deploy.lock"
    sperre.parent.mkdir()
    sperre.write_text("", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_STAGING_SPERRE", str(sperre))
    yield {
        "aufrufe": aufrufe,
        "sperre": sperre,
        "stand": log_ordner / docker_reste.STAND_DATEI,
    }


def _aufrufe(welt: dict[str, Path]) -> list[str]:
    pfad = welt["aufrufe"]
    return pfad.read_text(encoding="utf-8").splitlines() if pfad.exists() else []


def test_aufpasser_takt_gibt_images_und_build_reste_frei(welt: dict[str, Path]) -> None:
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0)

    assert _aufrufe(welt) == [
        "image prune -a -f --filter until=24h",
        "builder prune -a -f --filter until=24h",
    ]
    zeile = json.loads(welt["stand"].read_text(encoding="utf-8").splitlines()[-1])
    assert zeile["ausgang"] == "freigegeben"
    assert zeile["image prune"] == "1.5GB"


def test_bei_laufendem_staging_neubau_passiert_nichts(welt: dict[str, Path]) -> None:
    with welt["sperre"].open("w") as gehalten:  # so hält staging_deploy.sh die Sperre
        fcntl.flock(gehalten, fcntl.LOCK_EX)
        hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0)
        assert _aufrufe(welt) == []
        assert not welt["stand"].exists()

    # Neubau vorbei → der nächste Takt räumt.
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_900.0)
    assert len(_aufrufe(welt)) == 2


def test_hoechstens_einmal_am_tag(welt: dict[str, Path]) -> None:
    for jetzt in (1_000_000.0, 1_000_000.0 + 900, 1_000_000.0 + TAG - 1):
        hausmeister.aufraeumen_alles(loeschen=True, jetzt=jetzt)
    assert len(_aufrufe(welt)) == 2

    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0 + TAG)
    assert len(_aufrufe(welt)) == 4


def test_keine_sperrdatei_heisst_kein_neubau(welt: dict[str, Path]) -> None:
    welt["sperre"].unlink()
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0)
    assert len(_aufrufe(welt)) == 2
    assert not welt["sperre"].exists(), (
        "Hausmeister legt die Staging-Sperre nie selbst an"
    )


def test_probelauf_und_start_check_fassen_docker_nie_an(welt: dict[str, Path]) -> None:
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=1_000_000.0)
    hausmeister.start_erlaubt(loeschen=True)
    assert _aufrufe(welt) == []


def test_docker_fehler_wird_geloggt_und_nicht_alle_15_min_wiederholt(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCKER_ATTRAPPE_EXIT", "1")
    assert docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_000.0) == "fehler"
    zeile = json.loads(welt["stand"].read_text(encoding="utf-8").splitlines()[-1])
    assert zeile["ausgang"] == "fehler"
    assert "kaputt" in " ".join(zeile["fehler"])
    assert (
        docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_900.0)
        == "nicht_faellig"
    )


def test_fehlendes_docker_wirft_nie(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_DOCKER", str(welt["stand"].parent / "gibt-es-nicht")
    )
    assert docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_000.0) == "fehler"


def test_pruef_befehle_enthalten_nie_volume_oder_system() -> None:
    """Prüf-Test E42: Volumes (werkstatt-live_werkstatt-arbeit) bleiben immer unberührt."""
    for befehl in docker_reste.BEFEHLE:
        teile = [t.lower() for t in befehl]
        assert not any("volume" in t for t in teile), befehl
        assert "system" not in teile, befehl
    assert {b[:2] for b in docker_reste.BEFEHLE} == {
        ("image", "prune"),
        ("builder", "prune"),
    }


def test_staging_sperre_bleibt_waehrend_docker_laeuft_gehalten(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grund-Images (älter als 24 h) dürfen nie mitten in einen Staging-Bau fallen."""
    beleg = welt["stand"].parent.parent / "sperre_beleg.txt"
    monkeypatch.setenv("STAGING_SPERRE_PRUEFEN", str(beleg))
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0)
    assert beleg.read_text(encoding="utf-8").split() == ["gehalten", "gehalten"]
    # danach wieder frei, ein Staging-Deploy kommt durch
    with welt["sperre"].open("r") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_unlesbare_staging_sperre_heisst_im_zweifel_nicht_raeumen(
    welt: dict[str, Path],
) -> None:
    welt["sperre"].unlink()
    welt["sperre"].mkdir()  # IsADirectoryError statt FileNotFoundError
    assert (
        docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_000.0)
        == "staging_baut"
    )
    assert _aufrufe(welt) == []


def test_stand_in_der_zukunft_blockiert_nicht_fuer_immer(welt: dict[str, Path]) -> None:
    welt["stand"].parent.mkdir(parents=True, exist_ok=True)
    welt["stand"].write_text('{"zeit": 9999999999}\n', encoding="utf-8")
    assert (
        docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_000.0) == "freigegeben"
    )


def test_kaputte_stand_datei_heisst_nie_gelaufen(welt: dict[str, Path]) -> None:
    welt["stand"].parent.mkdir(parents=True, exist_ok=True)
    welt["stand"].write_bytes(b"\xff\xfe kaputt\n")
    assert (
        docker_reste.freigeben(welt["stand"].parent, jetzt=1_000_000.0) == "freigegeben"
    )


def test_ohne_hausmeister_sperre_kein_docker(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def kaputt() -> None:
        raise OSError("Sperre nicht nutzbar")

    monkeypatch.setattr(hausmeister, "_sperre_nehmen", kaputt)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=1_000_000.0)
    assert _aufrufe(welt) == []
