"""Tests für #448 — der Aufseher nimmt eigene Fehl-Reopens zurück.

Anlass: #415 wurde dreimal wieder geöffnet, weil ``regel_tests`` eine reine
Umbenennung (nur die ``def``-Zeile, Vermerk ``test-umbenannt:`` im Commit-Text)
als „Test entfernt statt ergänzt“ meldete.

Echt laufen: Git-Repo mit Bare-Origin (Fixture ``welt`` aus #213), ``capo.tick``,
die Zustandsdatei, ``befund``, ``bau_log`` und ``aufseher_stand``. Gestellt sind
nur GitHub (``gh.lauf`` schreibt mit, ``capo.kinder`` liefert die Sub-Issues).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_aufseher_stand_430 import SPEC as STAND_SPEC
from test_aufseher_stand_430 import FakeWelt, ordner, repo  # noqa: F401  (Fixtures)
from test_waechter_213 import _commit, _iso, welt  # noqa: F401  (welt = Fixture)

from to_spawn import aufseher_stand, bau_log, capo, gh

SPEC = 900
GH_REPO = "test/wegwerf"
N = 415
ALT = "test_cli_laeuft_in_eigener_sitzung_strg_c_trifft_sie_nicht"
NEU = "test_cli_laeuft_in_eigener_sitzung_und_prozessgruppe"


# --- Hilfen ------------------------------------------------------------------


class FakeGh:
    """``gh.lauf``-Ersatz: schreibt jeden Aufruf mit; ``fehler`` = Unterbefehle mit Exit 1."""

    def __init__(self) -> None:
        self.aufrufe: list[list[str]] = []
        self.fehler: set[str] = set()
        self.schliess_zeiten: list[str] = []  # Timeline: created_at der closed-Ereignisse

    def lauf(self, args: list[str], cwd: Path | None = None) -> tuple[int, str]:
        self.aufrufe.append(list(args))
        if args[:1] == ["issue"] and len(args) > 1 and args[1] in self.fehler:
            return 1, ""
        if args[:1] == ["api"] and any("/timeline" in a for a in args):
            if "timeline" in self.fehler:
                return 1, ""
            return 0, "\n".join(self.schliess_zeiten)
        return 0, ""

    def schliessen(self) -> list[list[str]]:
        return [a for a in self.aufrufe if a[:2] == ["issue", "close"]]


@pytest.fixture()
def fake_gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(gh, "lauf", fake.lauf)
    return fake


def _issue(**felder: Any) -> dict[str, Any]:
    return {
        "number": N,
        "state": "open",
        "closed_at": None,
        "labels": [],
        "assignees": [],
        **felder,
    }


def _kinder(monkeypatch: pytest.MonkeyPatch, issue: dict[str, Any]) -> None:
    monkeypatch.setattr(capo, "kinder", lambda gh_repo, spec: [dict(issue)])


def _zustand(welt: dict[str, Path]) -> Path:  # noqa: F811
    return capo._zustand_datei(welt["repo"], GH_REPO, SPEC)


def _erledigt_setzen(welt: dict[str, Path], *schluessel: str) -> None:  # noqa: F811
    datei = _zustand(welt)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps({"erledigt": list(schluessel)}), encoding="utf-8")


def _erledigt(welt: dict[str, Path]) -> list[str]:  # noqa: F811
    return list(
        json.loads(_zustand(welt).read_text(encoding="utf-8")).get("erledigt") or []
    )


def _tick(welt: dict[str, Path]) -> capo.TickErgebnis:  # noqa: F811
    return capo.tick(welt["repo"], SPEC, GH_REPO, {}, wt_basis=str(welt["wt"]))


def _beleg() -> dict[str, str]:
    return {f"docs/verify-hard/{N}_beleg.md": "Beleg\n"}


def _alter_test(welt: dict[str, Path]) -> None:  # noqa: F811
    inhalt = f"def {ALT}(app, pfoertner, protokoll):\n    assert app\n"
    _commit(
        welt["repo"],
        "test: alter Test",
        {"tests/test_pfoertner_415.py": inhalt},
        alter=timedelta(hours=2),
    )


def _umbenennung_415(welt: dict[str, Path]) -> str:  # noqa: F811
    """Commit wie 88359d4: nur die def-Zeile umbenannt, Vermerk im Commit-Text."""
    _alter_test(welt)
    inhalt = f"def {NEU}(app, pfoertner, protokoll):\n    assert app\n"
    text = (
        f"fix(pfoertner): Stopp beendet sture CLI (#{N}) [skip ci]\n\n"
        f"test-umbenannt: tests/test_pfoertner_415.py::{ALT} -> tests/test_pfoertner_415.py::{NEU}\n"
    )
    return _commit(
        welt["repo"],
        text,
        {"tests/test_pfoertner_415.py": inhalt, **_beleg()},
        alter=timedelta(hours=1),
    )


def _eigene(welt: dict[str, Path]) -> list[capo.Commit]:  # noqa: F811
    commits = capo.alle_commits(welt["repo"], "origin/master")
    return capo.ticket_commits(commits, N)


# --- Wurzel: Umbenennung ist kein Verlust --------------------------------------


def test_umbenennung_mit_vermerk_ist_kein_test_ersetzt(welt: dict[str, Path]) -> None:  # noqa: F811
    _umbenennung_415(welt)
    capo._git(welt["repo"], "fetch", "-q", "origin")
    assert capo.regel_tests(welt["repo"], N, _eigene(welt)) is None


def test_vermerk_formen_tolerant() -> None:
    diff = "+def test_neu(a):\n+def test_zwei():\n"
    assert capo.umbenannte_tests(
        diff, "x\n\ntest-umbenannt: test_alt → test_neu\n"
    ) == {"test_alt"}
    text = "Test-Umbenannt: tests/t.py::test_alt -> tests/t.py::test_neu, test_eins => test_zwei"
    assert capo.umbenannte_tests(diff, text) == {"test_alt", "test_eins"}
    # Neuer Name fehlt in den +Zeilen → kein Freibrief.
    assert capo.umbenannte_tests(diff, "test-umbenannt: test_alt -> test_weg") == set()
    assert capo.umbenannte_tests(diff, "kein Vermerk test_alt -> test_neu") == set()


# --- Rücknahme im Tick -----------------------------------------------------------


def test_rot_beweis_415_fehl_reopen_wird_zurueckgenommen(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())

    erg = _tick(welt)

    text = "\n".join(erg.zeilen)
    assert len(fake_gh.schliessen()) == 1, text
    aufruf = fake_gh.schliessen()[0]
    assert aufruf[2] == str(N) and "--repo" in aufruf and GH_REPO in aufruf
    kommentar = aufruf[aufruf.index("--comment") + 1]
    assert kommentar.startswith("Aufseher: Fehl-Reopen zurückgenommen")
    assert f"#{N} Fehl-Reopen zurückgenommen" in erg.zeilen, text
    assert f"ruecknahme|{N}|{geschlossen}" in _erledigt(welt)
    zeilen = bau_log.lese(welt["repo"], N)
    assert any(z.get("typ") == "ruecknahme" and z.get("text") for z in zeilen)


def test_echter_roter_befund_bleibt_offen(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alter_test(welt)
    _commit(
        welt["repo"],
        f"feat: Aufräumen (#{N})",
        {"tests/test_pfoertner_415.py": "import os\n", **_beleg()},
        alter=timedelta(hours=1),
    )
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())

    erg = _tick(welt)

    assert capo.regel_tests(welt["repo"], N, _eigene(welt)) is not None
    assert fake_gh.schliessen() == []
    assert not any("zurückgenommen" in z for z in erg.zeilen)
    assert f"ruecknahme|{N}|{geschlossen}" not in _erledigt(welt)


def test_neuer_commit_nach_schliessen_keine_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _commit(welt["repo"], f"fix: Nacharbeit (#{N})", {"web/x.py": "x\n"})
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())

    _tick(welt)

    assert fake_gh.schliessen() == []


def test_doppelschutz_zweiter_tick_nimmt_nicht_nochmal_zurueck(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())

    _tick(welt)
    # Jemand öffnet wieder (Issue noch offen) — der zweite Tick schließt nicht erneut.
    _tick(welt)

    assert len(fake_gh.schliessen()) == 1


def test_juengster_schliess_stempel_zaehlt(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    alt = _iso(timedelta(minutes=50))
    neu = _iso(timedelta(minutes=10))
    _erledigt_setzen(
        welt,
        f"{N}|test_ersetzt|{alt}",
        f"{N}|test_ersetzt|{neu}",
        f"ruecknahme|{N}|{alt}",
    )
    _kinder(monkeypatch, _issue())

    _tick(welt)

    assert len(fake_gh.schliessen()) == 1
    assert f"ruecknahme|{N}|{neu}" in _erledigt(welt)


def test_fehlerpfad_gh_close_scheitert(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())
    fake_gh.fehler.add("close")

    erg = _tick(welt)

    assert any(
        z.startswith(f"#{N} FEHLER") and "zurücknehmen" in z for z in erg.zeilen
    ), erg.zeilen
    assert erg.exit_code == 1
    assert f"ruecknahme|{N}|{geschlossen}" not in _erledigt(welt)
    assert not bau_log.lese(welt["repo"], N)


def test_probelauf_schliesst_nichts(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())

    erg = capo.tick(
        welt["repo"], SPEC, GH_REPO, {}, wt_basis=str(welt["wt"]), dry_run=True
    )

    assert fake_gh.schliessen() == []
    assert any("würde Fehl-Reopen zurücknehmen" in z for z in erg.zeilen), erg.zeilen


# --- Stand -------------------------------------------------------------------------


def test_stand_zeigt_ruecknahme(repo: Path, ordner: Path) -> None:  # noqa: F811
    w = FakeWelt()
    w.ticket(N, zu=True)
    bau_log.schreibe(
        repo, N, "ruecknahme", text="Fehl-Reopen zurückgenommen: test_ersetzt"
    )

    ausgabe = aufseher_stand.stand(
        STAND_SPEC, w.quellen(repo), ordner=ordner, alle=True
    )

    zeile = next(z for z in ausgabe.splitlines() if z.startswith(f"#{N} "))
    assert "Phase Rücknahme" in zeile, ausgabe
    assert "Fehl-Reopen zurückgenommen" in zeile, ausgabe


# --- Fixrunde 1 (#448) ---------------------------------------------------------


def test_sperren_im_kandidaten_label_ok_und_checkpoint() -> None:
    erledigt = {f"{N}|test_ersetzt|{_iso(timedelta(minutes=30))}"}
    frei = capo.fehl_reopen_kandidat(N, _issue(), [], erledigt, checkpoint="checkpoint:human")
    assert frei is not None
    for label in (capo.OK_LABEL, "checkpoint:human"):
        issue = _issue(labels=[{"name": label}])
        assert (
            capo.fehl_reopen_kandidat(N, issue, [], erledigt, checkpoint="checkpoint:human")
            is None
        ), label


def test_sperre_vps_fehlt_keine_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())
    monkeypatch.setattr(capo, "vps_kopf", lambda vps: None)

    capo.tick(
        welt["repo"], SPEC, GH_REPO, {"vps": {"ssh": "nirgends"}}, wt_basis=str(welt["wt"])
    )

    assert fake_gh.schliessen() == []


def test_mensch_schliesst_und_oeffnet_wieder_keine_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """capo öffnet bei T1, Mensch schließt bei T2 (grün), Mensch öffnet bewusst wieder."""
    _umbenennung_415(welt)
    t1 = _iso(timedelta(minutes=40))
    t2 = _iso(timedelta(minutes=20))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{t1}")
    _kinder(monkeypatch, _issue(state="closed", closed_at=t2, state_reason="completed"))
    _tick(welt)
    assert fake_gh.schliessen() == []

    _kinder(monkeypatch, _issue())
    erg = _tick(welt)

    assert fake_gh.schliessen() == [], erg.zeilen
    assert not any("zurückgenommen" in z for z in erg.zeilen), erg.zeilen


def test_folge_runde_laeuft_ruecknahme_wartet(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    geschlossen = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{geschlossen}")
    _kinder(monkeypatch, _issue())
    monkeypatch.setattr(capo, "tmux_fenster", lambda spec: [f"wache {spec}", f"bau {N}"])

    erg = _tick(welt)

    assert fake_gh.schliessen() == []
    assert any(
        z.startswith(f"#{N} Rücknahme wartet: Folge-Runde läuft") for z in erg.zeilen
    ), erg.zeilen
    assert f"ruecknahme|{N}|{geschlossen}" not in _erledigt(welt)


def test_zweite_ruecknahme_nur_gemeldet(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    erstes = _iso(timedelta(minutes=30))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{erstes}")
    _kinder(monkeypatch, _issue())
    _tick(welt)
    assert len(fake_gh.schliessen()) == 1
    assert f"ruecknahme|{N}" in _erledigt(welt)

    # capo öffnet später erneut fehl (neuer Schließ-Stempel) — keine zweite Rücknahme.
    zweites = _iso(timedelta(minutes=5))
    _erledigt_setzen(welt, *_erledigt(welt), f"{N}|test_ersetzt|{zweites}")
    erg = _tick(welt)

    assert len(fake_gh.schliessen()) == 1, erg.zeilen
    assert any(
        f"#{N} Fehl-Reopen erneut — nicht zurückgenommen, Mensch prüfen" in z
        for z in erg.zeilen
    ), erg.zeilen


def test_vermerk_neuer_test_in_anderer_datei_zaehlt_nicht() -> None:
    diff = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +0,0 @@\n-def test_alt(a):\n"
        "diff --git a/tests/test_b.py b/tests/test_b.py\n"
        "--- a/tests/test_b.py\n+++ b/tests/test_b.py\n"
        "@@ -0,0 +1 @@\n+def test_neu(a):\n"
    )
    assert capo.umbenannte_tests(diff, "test-umbenannt: test_alt -> test_neu") == set()
    gleiche = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
        "@@ -1 +1 @@\n-def test_alt(a):\n+def test_neu(a):\n"
    )
    assert capo.umbenannte_tests(gleiche, "test-umbenannt: test_alt -> test_neu") == {
        "test_alt"
    }


def test_reopen_regeln_stimmen_mit_reopen_funde_ueberein() -> None:
    """REOPEN_REGELN ist kein zweiter Pflegeort: Abgleich mit den Regeln aus reopen_funde."""
    import inspect
    import re

    quelle = inspect.getsource(capo.reopen_funde)
    pruefer = set(re.findall(r"\b(regel_\w+)\(", quelle))
    assert pruefer, quelle
    namen: set[str] = set()
    for name in pruefer:
        namen |= set(
            re.findall(r'Verstoss\(\s*ticket,\s*"(\w+)"', inspect.getsource(getattr(capo, name)))
        )
    assert namen == set(capo.REOPEN_REGELN)


# --- Fixrunde 2: Mensch schließt + öffnet zwischen zwei Ticks --------------------


def test_mensch_schliesst_und_oeffnet_zwischen_ticks_keine_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """capo öffnet bei T1; Mensch schließt bei T2 und öffnet wieder, bevor ein Tick es sieht."""
    _umbenennung_415(welt)
    t1 = _iso(timedelta(minutes=40))
    t2 = _iso(timedelta(minutes=20))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{t1}")
    fake_gh.schliess_zeiten = [t1, t2]
    _kinder(monkeypatch, _issue())

    erg = _tick(welt)

    assert fake_gh.schliessen() == [], erg.zeilen
    assert not any("zurückgenommen" in z for z in erg.zeilen), erg.zeilen


def test_timeline_nur_capos_schliessen_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    t1 = _iso(timedelta(minutes=40))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{t1}")
    fake_gh.schliess_zeiten = [_iso(timedelta(hours=5)), t1]
    _kinder(monkeypatch, _issue())

    erg = _tick(welt)

    assert len(fake_gh.schliessen()) == 1, erg.zeilen


def test_timeline_nicht_lesbar_keine_ruecknahme(
    welt: dict[str, Path],  # noqa: F811
    fake_gh: FakeGh,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _umbenennung_415(welt)
    t1 = _iso(timedelta(minutes=40))
    _erledigt_setzen(welt, f"{N}|test_ersetzt|{t1}")
    fake_gh.fehler.add("timeline")
    _kinder(monkeypatch, _issue())

    erg = _tick(welt)

    assert fake_gh.schliessen() == [], erg.zeilen
    assert f"#{N} Rücknahme ausgesetzt: Issue-Verlauf nicht lesbar (gh)" in erg.zeilen, erg.zeilen
