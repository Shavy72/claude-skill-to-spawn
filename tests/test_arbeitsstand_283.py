"""Start aus veraltetem Arbeitsordner (#283) — mit echtem git, keine Attrappen.

Aufbau je Test: lokales bare-Repo als ``origin``, ein Klon (der Arbeitsordner) und eine
zweite Kopie, die N Commits nachschiebt. Der Klon liegt danach N Commits hinter
``origin/main`` — wie der PC-Hauptordner im Fall (5839 Commits, alte ``_default.json``).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import arbeitsstand, einrichten

SKILL = Path(__file__).resolve().parent.parent
VORLAGE = Path("docs/agents/manifests/_default.json")
N = 3


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(ort: Path, inhalt: str) -> None:
    datei = ort / VORLAGE
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(inhalt, encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", inhalt)


@pytest.fixture
def klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arbeitsordner, der ``N`` Commits hinter ``origin/main`` liegt (sauberer Baum)."""
    monkeypatch.delenv(arbeitsstand.TEST_OK_ENV, raising=False)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    quelle = tmp_path / "quelle"
    _git(tmp_path, "clone", "-q", str(remote), str(quelle))
    _commit(quelle, '{"prompt_template": "alt"}')
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    ort = tmp_path / "app"
    _git(tmp_path, "clone", "-q", str(remote), str(ort))
    for i in range(N):
        _commit(quelle, f'{{"prompt_template": "neu {i} staging:fertig"}}')
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    return ort


def test_sauber_zurueck_wird_nachgezogen(klon: Path) -> None:
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN, stand.text
    assert stand.ok and stand.hinter == N
    assert "staging:fertig" in (klon / VORLAGE).read_text(encoding="utf-8")
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == "0"


def test_zurueck_mit_lokaler_aenderung_bricht_ab_mit_zahl_und_anweisung(
    klon: Path,
) -> None:
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and not stand.ok
    assert f"{N} Commits hinter origin/main" in stand.text
    assert "Tun:" in stand.text and "pull --ff-only" in stand.text
    # Lokale Änderung unberührt, nichts vorgespult.
    assert (klon / VORLAGE).read_text(
        encoding="utf-8"
    ) == '{"prompt_template": "von Hand"}'
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == str(N)


def test_abweichende_historie_bricht_ab(klon: Path) -> None:
    (klon / "eigen.txt").write_text("x", encoding="utf-8")
    _git(klon, "add", ".")
    _git(klon, "commit", "-q", "-m", "eigen")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH
    assert f"{N} Commits hinter" in stand.text and "nicht vorspulbar" in stand.text


def test_aktuell_ist_still_ok(klon: Path) -> None:
    _git(klon, "pull", "-q", "--ff-only")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.AKTUELL and stand.ok and stand.hinter == 0


def test_fetch_fehler_gilt_nie_als_aktuell(klon: Path, tmp_path: Path) -> None:
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and "nicht prüfbar" in stand.text


def test_nur_pruefen_zieht_nicht_nach(klon: Path) -> None:
    stand = arbeitsstand.sicherstellen(klon, nachziehen=False)
    assert stand.zustand == arbeitsstand.VERALTET and not stand.ok
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == str(N)


def test_einrichten_pruefen_meldet_veralteten_arbeitsordner(klon: Path) -> None:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(klon, pruefen=True, ausgabe=ausgabe)
    text = ausgabe.getvalue()
    assert code == einrichten.EXIT_ABWEICHUNG
    zeile = next(z for z in text.splitlines() if z.strip().startswith("arbeitsstand:"))
    assert f"{N} Commits hinter origin/main" in zeile, text


def test_bau_start_bricht_bei_veraltetem_ordner_mit_zeile_ab(klon: Path) -> None:
    """Echter Start-Weg ``skripte/bau.py <N>``: nicht nachziehbar → Exit 2 + Zeile, kein Start."""
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    env = {**os.environ, "TO_SPAWN_REPO": str(klon), "TO_SPAWN_GH_REPO": "test-org/app"}
    fertig = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7"],
        cwd=klon,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    ausgabe = fertig.stdout + fertig.stderr
    assert fertig.returncode == 2, ausgabe
    assert f"{N} Commits hinter origin/main" in ausgabe and "Tun:" in ausgabe


@pytest.mark.parametrize("weg", ["anderer_zweig", "losgeloest"])
def test_nicht_auf_hauptzweig_und_hinter_bricht_ab(klon: Path, weg: str) -> None:
    """Kante (a): anderer Zweig bzw. losgelöster HEAD und hinter → Abbruch mit Zahl, nichts bewegt."""
    if weg == "anderer_zweig":
        _git(klon, "switch", "-q", "-c", "fix/irgendwas")
    else:
        _git(klon, "switch", "-q", "--detach")
    vorher = _git(klon, "rev-parse", "HEAD")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and not stand.ok
    assert f"{N} Commits hinter origin/main" in stand.text and "switch main" in stand.text
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_unversionierte_dateien_hindern_nachziehen_nicht(klon: Path) -> None:
    """Kante (e): unversionierte Datei bleibt liegen, Nachziehen klappt trotzdem."""
    notiz = klon / "notiz-unversioniert.txt"
    notiz.write_text("bleibt", encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN, stand.text
    assert notiz.read_text(encoding="utf-8") == "bleibt"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")


def test_aktueller_klon_ohne_zweig_und_sauberkeits_zwang(klon: Path) -> None:
    """Bau-Server-Fall: hinter == 0 → aktuell, auch auf anderem Zweig mit lokaler Änderung."""
    _git(klon, "pull", "-q", "--ff-only")
    _git(klon, "switch", "-q", "-c", "arbeit")
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.AKTUELL and stand.ok, stand.text


def test_zwei_starts_zugleich_scheitern_nicht_an_git_sperre(klon: Path) -> None:
    """Kante (f): zwei Prozesse rufen die Tür gleichzeitig im selben Ordner — beide ok, einer zieht nach."""
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
        "from to_spawn import arbeitsstand as a;"
        "s = a.sicherstellen(Path(sys.argv[2])); print(s.zustand); print(s.text)"
    )
    env = {k: v for k, v in os.environ.items() if k != arbeitsstand.TEST_OK_ENV}
    starts = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(SKILL), str(klon)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    ergebnisse = [p.communicate(timeout=120) for p in starts]
    zustaende = sorted(aus.splitlines()[0] if aus else f"leer: {err}" for aus, err in ergebnisse)
    assert zustaende == [arbeitsstand.AKTUELL, arbeitsstand.NACHGEZOGEN], ergebnisse
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")
