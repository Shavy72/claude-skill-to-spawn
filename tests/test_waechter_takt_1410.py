"""#1410: „Spec fertig“ und Abschluss-Mail kommen nur aus capo, nie aus der Aufseher-Session.

Zwei Wege daran vorbei werden geprüft:

1. Prompt-Text: der Aufseher-Takt-Prompt überlässt der Session kein eigenes Fertig-Urteil,
   die Abschluss-Kette hängt nur an der capo-Zeile „#S SPEC FERTIG — Abschluss-Kette“.
2. Sperre im Skript: ``abschluss_paket.py ablegen/nachsehen`` verweigert (Exit 4), solange capos
   Zustand die Spec führt, aber nicht fertig meldet (kein ``spec_fertig_seit``, z. B. Staging
   nicht lesbar, #1407). Mit capo-Fertig geht der Aufruf durch.

Echt laufen das CLI und capos Zustandsdatei (echte Dateien im Temp-Ordner); nichts gestellt.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from test_waechter_213 import welt  # noqa: E402,F401  (Fixture)

from to_spawn import capo, waechter_takt  # noqa: E402

SPEC = 1410
FERTIG_ZEILE = f"#{SPEC} SPEC FERTIG — Abschluss-Kette"


def _prompt() -> str:
    return waechter_takt.PROMPT.format(S=SPEC, REPO="x/y", SKILL="/skill", LISTE="-", NOTIZ="-")


def test_prompt_laesst_session_kein_eigenes_fertig_urteil() -> None:
    text = _prompt()
    # Alte Sätze, mit denen die Session selbst „fertig“ entschied.
    for verboten in ("alle Bau-Tickets zu", "Bau fertig, bereit zur Abnahme", "Kette … durch"):
        assert verboten not in text, f"Prompt überlässt der Session das Fertig-Urteil: „{verboten}“"
    assert "nur capo" in text
    # Die Abschluss-Kette hängt an genau der Zeile, die pruefe() bei tick.fertig in die Liste setzt.
    quelle = Path(waechter_takt.__file__).read_text(encoding="utf-8")
    assert 'f"#{spec} SPEC FERTIG — Abschluss-Kette' in quelle
    assert FERTIG_ZEILE in text
    # Exit 4 der Sperre ist im Prompt erklärt (nicht umgehen).
    assert re.search(r"Exit 4[^.]*capo", text)


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
        "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "projekt"
    (r / ".git").mkdir(parents=True)
    return r


def _capo_zustand(repo: Path, tmp_path: Path, zustand: dict[str, object]) -> Path:
    alt = os.environ.get("TO_SPAWN_WAECHTER_ORDNER")
    os.environ["TO_SPAWN_WAECHTER_ORDNER"] = str(tmp_path / "w")
    try:
        datei = capo._zustand_datei(repo, "", SPEC)
    finally:
        if alt is None:
            os.environ.pop("TO_SPAWN_WAECHTER_ORDNER", None)
        else:
            os.environ["TO_SPAWN_WAECHTER_ORDNER"] = alt
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(zustand), encoding="utf-8")
    return datei


def _abschluss(befehl: str, repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "abschluss_paket.py"), befehl, str(SPEC), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(tmp_path),
        timeout=120,
        check=False,
    )


_LINKS = ("--stage", "https://stage.example", "--rundschau", "fehlt", "--tests", "fehlt")


def test_session_ablegen_ohne_capo_fertig_wird_abgelehnt(repo: Path, tmp_path: Path) -> None:
    # capo führt die Spec, sagt aber nicht fertig (Staging nicht lesbar, #1407).
    _capo_zustand(repo, tmp_path, {"erledigt": [], "staging_unlesbar_takte": 2})
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 4, lauf.stdout + lauf.stderr
    assert "capo" in lauf.stderr
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()
    nach = _abschluss("nachsehen", repo, tmp_path)
    assert nach.returncode == 4, nach.stdout + nach.stderr


def test_mit_capo_fertig_geht_ablegen_durch(repo: Path, tmp_path: Path) -> None:
    _capo_zustand(repo, tmp_path, {"erledigt": [], "spec_fertig_seit": "2026-11-21T10:00:00+01:00"})
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").is_file()
    nach = _abschluss("nachsehen", repo, tmp_path, "--dry-run")
    assert nach.returncode != 4, nach.stdout + nach.stderr


# --- Fix-Runde 1 (Review #1410) -------------------------------------------------------------------


def test_tick_sichert_fertig_urteil_vor_abschluss_anstoss(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch  # noqa: F811 (Fixture-Parameter)
) -> None:
    """Befund 1: beim Aufruf von ``abschluss_anstossen`` steht ``spec_fertig_seit`` schon auf Platte.

    Sonst liest der Subprozess ``abschluss_paket.py nachsehen`` im ersten Fertig-Tick noch kein
    Urteil → Exit 4 → FEHLER-Zeile, Mail erst einen Tick später. Echter Tick, echte Zustandsdatei;
    nur der Anstoß ist ein Spion (statt Mail).
    """
    from test_waechter_213 import SPEC as SPEC_213
    from test_waechter_213 import _beleg, _commit, _gh_setzen, _iso

    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    gesehen: list[tuple[str | None, object]] = []

    def spion(repo: Path, spec: int, gh_repo: str, *_: object) -> list[str]:
        gesehen.append((capo.fertig_urteil(repo, spec, gh_repo), capo.fertig_luecke(repo, spec, gh_repo)))
        return []

    monkeypatch.setattr(capo, "abschluss_anstossen", spion)
    erg = capo.tick(welt["repo"], int(SPEC_213), "test/wegwerf", {}, wt_basis=str(welt["wt"]))
    assert any("SPEC FERTIG" in z for z in erg.zeilen), erg.zeilen
    assert len(gesehen) == 1, gesehen
    urteil, luecke = gesehen[0]
    assert urteil, f"spec_fertig_seit beim Anstoß noch nicht auf Platte: {urteil!r}"
    assert luecke is None, luecke


@pytest.mark.parametrize(
    ("inhalt", "grund"),
    [
        (json.dumps({"erledigt": [901], "erster_tick": "2026-09-01T10:00:00+00:00"}), "Feld spec_fertig_seit fehlt"),
        ("{kaputt", "nicht lesbar"),
    ],
    ids=["alt_zustand_ohne_feld", "zustand_kaputt"],
)
@pytest.mark.parametrize("stand", ["abnahme", "live"])
def test_sperre_nennt_datei_grund_und_ausweg(
    repo: Path, tmp_path: Path, inhalt: str, grund: str, stand: str
) -> None:
    """Befund 2: Alt-Zustand/kaputte Datei bleibt gesperrt (sichere Seite), aber mit Datei + Grund + Ausweg."""
    datei = _capo_zustand(repo, tmp_path, {})
    datei.write_text(inhalt, encoding="utf-8")
    for befehl, args in (("ablegen", (*_LINKS, "--stand", stand)), ("nachsehen", ("--stand", stand))):
        lauf = _abschluss(befehl, repo, tmp_path, *args)
        assert lauf.returncode == 4, (befehl, lauf.stdout + lauf.stderr)
        assert str(datei) in lauf.stderr, lauf.stderr
        assert grund in lauf.stderr, lauf.stderr
        assert "capo schreibt das Urteil beim nächsten Durchlauf selbst" in lauf.stderr, lauf.stderr
        assert "bewusst von Hand" in lauf.stderr, lauf.stderr
    assert not list(repo.glob(".to-spawn/abschluss_*_paket.json"))


def test_zustand_unter_anderem_repo_namen_wird_gemeldet(repo: Path, tmp_path: Path) -> None:
    """Befund 3: capo schrieb mit anderem ``--gh-repo`` → Sperre findet die Datei nicht; das steht im Log."""
    ordner = tmp_path / "w"
    ordner.mkdir(parents=True)
    fremd = ordner / f"anderer_name_{SPEC}.json"
    fremd.write_text(json.dumps({"erledigt": []}), encoding="utf-8")
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert fremd.name in lauf.stderr and "Repo-Name weicht ab" in lauf.stderr, lauf.stderr


def _wache_prompt() -> str:
    import importlib.util

    modul_spec = importlib.util.spec_from_file_location("wache_1410", SKILL / "skripte" / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    return str(wache.PROMPT)


def _skill_abschluss_zeile() -> str:
    zeilen = (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()
    treffer = [z for z in zeilen if z.startswith("- Abschluss-Paket (#366")]
    assert len(treffer) == 1, treffer
    return treffer[0]


@pytest.mark.parametrize("quelle", ["takt", "wache", "skill_md"])
def test_kopien_lassen_session_kein_eigenes_fertig_urteil(quelle: str) -> None:
    """Befund 5: alle drei Texte (Takt-Prompt, alter Wache-Prompt, SKILL.md) sagen dasselbe Soll."""
    text = {"takt": _prompt, "wache": _wache_prompt, "skill_md": _skill_abschluss_zeile}[quelle]()
    for verboten in ("alle Bau-Tickets zu", "Bau fertig, bereit zur Abnahme", "Kette … durch", "nicht erst bei"):
        assert verboten not in text, f"{quelle}: überlässt der Session das Fertig-Urteil: „{verboten}“"
    assert "nur capo" in text, quelle
    assert "SPEC FERTIG" in text, quelle
    assert re.search(r"Exit 4[^.]*capo", text), quelle
