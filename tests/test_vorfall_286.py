"""Lernschleife: jeder erkannte Stillstand landet im Bau-Log und im Fehlerkatalog (#286).

Echt laufen: Git (bare ``origin`` + Klon im Temp-Ordner), die CLI ``skripte/capo.py``
und ``to_spawn.py``, die echten Bau-Log- und Katalog-Dateien. Gestellt sind nur
externe Dienste: ``gh`` (``tests/hilfen/gh_stub_213.py``) und der Mail-Befehl —
dieselbe Welt wie ``test_waechter_213.py``, deren Fixture hier weiterverwendet wird.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from test_waechter_213 import (  # noqa: F401  (welt ist eine Fixture)
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _konfig,
    _text,
    welt,
)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, vorfall  # noqa: E402

KATALOG = Path("docs") / "agents" / "FEHLERKATALOG_spawn.md"

#: Aufbau wie der echte Katalog: Tabelle je Unterabschnitt, Nummer mit Klassen-Buchstabe.
KATALOG_TEXT = """# Fehlerkatalog Spawn-System

## 3. Katalog — Symptom · Ursache · Lösung · Schutz

Legende Schutz: 🟢 automatisch · 🟡 teils · 🔴 nur Prosa-Regel · ⬜ offen.

### 3.1 Externe Grenze (Nutzungs-Limit)
| # | Symptom (was David sieht) | Ursache | Lösung / Rezept | Schutz | Beispiel |
|---|---|---|---|---|---|
| E1 | Alle Fenster stehen | Kontingent erschöpft | Ausweich-Modell | 🟡 | Spec 202 |

### 3.3 Skill-Logik (to-spawn / bau.py / capo / Wächter)
| # | Symptom | Ursache | Lösung | Schutz | Beispiel |
|---|---|---|---|---|---|
| S1 | Fenster steht nach Wiederöffnung | Keine Folge-Runde | bau.py startet neu | 🟢 | #284 |
| S2 | Session doppelt gestartet | Kein Prozess-Check | sessions prüfen | 🟢 | #187 |

### 3.4 Prozess-Regel (Zusammenspiel mehrerer Sessions)
| # | Symptom | Ursache | Lösung | Schutz | Beispiel |
|---|---|---|---|---|---|
| P1 | Fremder Deploy dreht zurück | VPS-HEAD ungeprüft | HEAD vor Bundle prüfen | 🟢 | #189 |

## 4. Wiederholungen derselben Ursache (Muster)
- nichts
"""


def _katalog_legen(repo: Path, text: str = KATALOG_TEXT) -> Path:
    pfad = repo / KATALOG
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(text, encoding="utf-8")
    return pfad


def _tabelle(text: str, kopf: str) -> list[str]:
    """Nur die Tabellenzeilen eines Abschnitts (ohne Kopf, Legende, Leerzeilen)."""
    return [z for z in _abschnitt(text, kopf) if z.lstrip().startswith("|")]


def _abschnitt(text: str, kopf: str) -> list[str]:
    """Zeilen eines ``###``-Abschnitts ohne Kopf und ohne Folge-Abschnitt."""
    zeilen = text.splitlines()
    start = next(i for i, z in enumerate(zeilen) if z.startswith(kopf))
    rest = zeilen[start + 1 :]
    ende = next(
        (i for i, z in enumerate(rest) if z.startswith("### ") or z.startswith("## ")),
        len(rest),
    )
    return rest[:ende]


def _lauf_zeilen(repo: Path, ticket: int | str) -> list[dict]:
    pfad = bau_log.lauf_pfad(repo, ticket)
    if not pfad.is_file():
        return []
    return [
        json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines() if z.strip()
    ]


def _vorfaelle(repo: Path, ticket: int | str) -> list[dict]:
    return [z for z in _lauf_zeilen(repo, ticket) if z.get("typ") == "vorfall"]


# --- Naht 1: Vorfall-Zeile im Bau-Log ------------------------------------------


def test_vorfall_landet_mit_allen_vier_feldern_im_bau_log(tmp_path: Path) -> None:
    vorfall.schreibe(
        tmp_path,
        901,
        klasse="skill",
        symptom="Fenster steht still",
        ursache="Keine Folge-Runde nach Wiederöffnung",
        loesung="capo startet die Runde neu",
    )
    zeilen = _vorfaelle(tmp_path, 901)
    assert len(zeilen) == 1
    z = zeilen[0]
    assert z["klasse"] == "skill"
    assert z["symptom"] == "Fenster steht still"
    assert z["ursache"] == "Keine Folge-Runde nach Wiederöffnung"
    assert z["loesung"] == "capo startet die Runde neu"
    assert z["ticket"] == "901"


def test_vorfall_typ_ist_erlaubt() -> None:
    assert "vorfall" in bau_log.TYPEN


def test_gleicher_vorfall_wird_nicht_doppelt_geschrieben(tmp_path: Path) -> None:
    for _ in range(3):
        vorfall.schreibe(
            tmp_path,
            901,
            klasse="skill",
            symptom="Fenster steht still",
            ursache="Keine Folge-Runde",
            loesung="neu starten",
        )
    assert len(_vorfaelle(tmp_path, 901)) == 1


def test_anderer_vorfall_kommt_dazu(tmp_path: Path) -> None:
    vorfall.schreibe(
        tmp_path, 901, klasse="skill", symptom="A", ursache="B", loesung="C"
    )
    vorfall.schreibe(
        tmp_path, 901, klasse="infra", symptom="D", ursache="E", loesung="F"
    )
    assert len(_vorfaelle(tmp_path, 901)) == 2


def test_unbekannte_klasse_wird_abgelehnt(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        vorfall.schreibe(
            tmp_path, 901, klasse="quatsch", symptom="A", ursache="B", loesung="C"
        )


def test_leeres_symptom_wird_abgelehnt(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        vorfall.schreibe(
            tmp_path, 901, klasse="skill", symptom="  ", ursache="B", loesung="C"
        )


# --- Naht 2: Katalog-Zeile ------------------------------------------------------


def test_katalog_haengt_zeile_an_den_abschnitt_der_klasse(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)
    neu = vorfall.in_katalog(
        pfad,
        [
            vorfall.Vorfall(
                klasse="skill",
                symptom="Gate bricht ohne Grund ab",
                ursache="Temp-Ordner voll",
                loesung="basetemp auf die Platte",
                beispiel="#286",
            )
        ],
    )
    assert neu.nummern == ["S3"] and neu.probleme == []
    text = pfad.read_text(encoding="utf-8")
    skill_zeilen = _tabelle(text, "### 3.3")
    assert skill_zeilen[-1].startswith("| S3 |")
    assert "Gate bricht ohne Grund ab" in skill_zeilen[-1]
    assert "Temp-Ordner voll" in skill_zeilen[-1]
    assert "basetemp auf die Platte" in skill_zeilen[-1]
    assert "#286" in skill_zeilen[-1]
    # Nachbar-Abschnitte bleiben unberührt.
    assert len(_abschnitt(text, "### 3.4")) == len(
        _abschnitt(KATALOG_TEXT, "### 3.4")
    )
    assert text.count("## 4. Wiederholungen") == 1


def test_katalog_zaehlt_je_klasse_weiter(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)
    neu = vorfall.in_katalog(
        pfad,
        [
            vorfall.Vorfall(
                klasse="extern", symptom="X1", ursache="Y1", loesung="Z1"
            ),
            vorfall.Vorfall(
                klasse="prozess", symptom="X2", ursache="Y2", loesung="Z2"
            ),
            vorfall.Vorfall(
                klasse="extern", symptom="X3", ursache="Y3", loesung="Z3"
            ),
        ],
    )
    assert neu.nummern == ["E2", "P2", "E3"]


def test_katalog_ueberspringt_bekannten_vorfall(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)
    doppelt = vorfall.Vorfall(
        klasse="skill",
        symptom="Fenster steht nach Wiederöffnung",
        ursache="Keine Folge-Runde",
        loesung="egal",
    )
    assert vorfall.in_katalog(pfad, [doppelt]).nummern == []
    assert pfad.read_text(encoding="utf-8") == KATALOG_TEXT


def test_katalog_schreibt_denselben_vorfall_nur_einmal(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)
    neuer = vorfall.Vorfall(
        klasse="skill", symptom="Neu", ursache="Grund", loesung="Fix"
    )
    assert vorfall.in_katalog(pfad, [neuer, neuer]).nummern == ["S3"]
    assert pfad.read_text(encoding="utf-8").count("| S3 |") == 1


def test_katalog_entschaerft_pipes_und_umbrueche(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)
    vorfall.in_katalog(
        pfad,
        [
            vorfall.Vorfall(
                klasse="skill",
                symptom="a | b",
                ursache="Zeile eins\nZeile zwei",
                loesung="c",
            )
        ],
    )
    zeile = _tabelle(pfad.read_text(encoding="utf-8"), "### 3.3")[-1]
    assert "\\|" in zeile  # Pipe im Text ist entschärft
    assert len(zeile.replace("\\|", "").split("|")) == 8  # 6 Spalten = 7 echte Trenner
    assert "Zeile eins Zeile zwei" in zeile


def test_katalog_ohne_abschnitt_der_klasse_bleibt_unberuehrt(tmp_path: Path) -> None:
    pfad = _katalog_legen(tmp_path)  # Text hat keinen Abschnitt 3.2
    erg = vorfall.in_katalog(
        pfad,
        [vorfall.Vorfall(klasse="infra", symptom="A", ursache="B", loesung="C")],
    )
    assert erg.nummern == []
    assert erg.probleme and "3.2" in erg.probleme[0]  # Grund wird genannt, nicht verschluckt
    assert pfad.read_text(encoding="utf-8") == KATALOG_TEXT


def test_fehlende_katalogdatei_ist_kein_absturz(tmp_path: Path) -> None:
    erg = vorfall.in_katalog(
        tmp_path / "gibtsnicht.md",
        [vorfall.Vorfall(klasse="skill", symptom="A", ursache="B", loesung="C")],
    )
    assert erg.nummern == [] and erg.ohne_katalog and erg.probleme == []


# --- Naht 3: eintrag-CLI --------------------------------------------------------


def test_eintrag_cli_schreibt_vorfall_in_die_versionierte_datei(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "docs" / "agents" / "bau_log").mkdir(parents=True)
    ergebnis = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "eintrag",
            "--ticket",
            "286",
            "--typ",
            "vorfall",
            "--klasse",
            "skill",
            "--symptom",
            "Fenster steht",
            "--ursache",
            "Keine Folge-Runde",
            "--loesung",
            "capo startet neu",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"TO_SPAWN_REPO": str(repo), "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    zeilen = [
        json.loads(z)
        for z in (repo / "docs" / "agents" / "bau_log" / "286.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if z.strip()
    ]
    assert zeilen[-1]["typ"] == "vorfall"
    assert zeilen[-1]["klasse"] == "skill"
    assert zeilen[-1]["symptom"] == "Fenster steht"


def test_eintrag_cli_verlangt_die_vier_felder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ergebnis = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "eintrag",
            "--ticket",
            "286",
            "--typ",
            "vorfall",
            "--symptom",
            "Fenster steht",
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"TO_SPAWN_REPO": str(repo), "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )
    assert ergebnis.returncode != 0
    assert "klasse" in (ergebnis.stdout + ergebnis.stderr).lower()


# --- Weg-Test: erkannter Stillstand → Bau-Log-Zeile UND Katalog-Zeile -----------


def _welt_sofort(welt: dict[str, Path]) -> None:
    """Karenz und Ausgangsstand abschalten — die prüft ``test_waechter_213_fix.py``."""
    _konfig(welt, waechter={"sofort": True})


def test_weg_verstoss_schreibt_vorfall_und_katalogzeile(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    ergebnis = _capo(welt, "--katalog")
    assert ergebnis.returncode == 0, _text(ergebnis)

    zeilen = _vorfaelle(repo, 901)
    assert len(zeilen) == 1, _lauf_zeilen(repo, 901)
    assert zeilen[0]["klasse"] == "prozess"
    assert "commit_ohne_nummer" in json.dumps(zeilen[0], ensure_ascii=False)

    text = (repo / KATALOG).read_text(encoding="utf-8")
    neue = _tabelle(text, "### 3.4")[-1]
    assert neue.startswith("| P2 |")
    assert "#901" in neue
    assert "Katalog" in ergebnis.stdout


def test_weg_verwaiste_session_schreibt_vorfall(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(repo, "chore: Katalog", {str(KATALOG): KATALOG_TEXT})
    _gh_setzen(
        welt,
        "902",
        updated_at=_iso(timedelta(hours=9)),
        assignees=["Shavy72"],  # gh-Stub macht daraus {"login": …}
    )
    ergebnis = _capo(welt, "--katalog")
    assert ergebnis.returncode == 0, _text(ergebnis)
    zeilen = _vorfaelle(repo, 902)
    assert len(zeilen) == 1
    assert zeilen[0]["klasse"] == "skill"
    assert "session_verwaist" in json.dumps(zeilen[0], ensure_ascii=False)
    assert "| S3 |" in (repo / KATALOG).read_text(encoding="utf-8")


def test_weg_kein_katalog_flag_schreibt_nur_ins_bau_log(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    ergebnis = _capo(welt, "--kein-katalog")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_vorfaelle(repo, 901)) == 1
    assert (repo / KATALOG).read_text(encoding="utf-8") == KATALOG_TEXT


def test_weg_katalog_ist_die_vorgabe(welt: dict[str, Path]) -> None:
    """Ohne jedes Flag lernt capo — sonst hängt die Lernschleife an einem Schalter."""
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "| P2 |" in (repo / KATALOG).read_text(encoding="utf-8")


def test_weg_fehlender_abschnitt_meldet_fehler(welt: dict[str, Path]) -> None:
    """Katalog ohne den Abschnitt der Klasse: FEHLER-Zeile statt stiller Erfolg."""
    repo = welt["repo"]
    _welt_sofort(welt)
    ohne_34 = KATALOG_TEXT.replace(
        "### 3.4 Prozess-Regel (Zusammenspiel mehrerer Sessions)", "### 3.9 Anderes"
    )
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): ohne_34,
        },
    )
    ergebnis = _capo(welt)
    assert "FEHLER: Katalog" in ergebnis.stdout, _text(ergebnis)
    assert ergebnis.returncode == 1
    assert len(_vorfaelle(repo, 901)) == 1  # Bau-Log lernt trotzdem


def test_weg_ohne_katalogdatei_kein_fehler(welt: dict[str, Path]) -> None:
    """Fremd-Repo ohne Fehlerkatalog: Hinweis, kein roter Tick (#257)."""
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(repo, "feat: Bauteil ohne Nummer", {"docs/verify-hard/901_beleg.md": "Beleg\n"})
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "führt keinen Fehlerkatalog" in ergebnis.stdout
    assert len(_vorfaelle(repo, 901)) == 1


def test_katalog_zeile_mit_pipe_wird_beim_zweiten_lauf_erkannt(tmp_path: Path) -> None:
    """Doppelschutz muss auch halten, wenn Symptom oder Ursache ein ``|`` enthält."""
    pfad = _katalog_legen(tmp_path)
    vor = vorfall.Vorfall(
        klasse="skill", symptom="a | b", ursache="c | d", loesung="e"
    )
    assert vorfall.in_katalog(pfad, [vor]).nummern == ["S3"]
    assert vorfall.in_katalog(pfad, [vor]).nummern == []
    assert pfad.read_text(encoding="utf-8").count("| S3 |") == 1


def test_katalog_abschnitt_ohne_tabelle_bleibt_unberuehrt(tmp_path: Path) -> None:
    text = KATALOG_TEXT.replace(
        "| S1 | Fenster steht nach Wiederöffnung | Keine Folge-Runde | bau.py startet neu | 🟢 | #284 |\n"
        "| S2 | Session doppelt gestartet | Kein Prozess-Check | sessions prüfen | 🟢 | #187 |\n",
        "Hier steht nur Fließtext.\n",
    ).replace("| # | Symptom | Ursache | Lösung | Schutz | Beispiel |\n|---|---|---|---|---|---|\n", "", 1)
    pfad = _katalog_legen(tmp_path, text)
    erg = vorfall.in_katalog(
        pfad, [vorfall.Vorfall(klasse="skill", symptom="A", ursache="B", loesung="C")]
    )
    assert erg.nummern == []
    assert erg.probleme and "Tabelle" in erg.probleme[0]
    assert pfad.read_text(encoding="utf-8") == text


def test_katalog_nummer_mit_auszeichnung_wird_mitgezaehlt(tmp_path: Path) -> None:
    """``| **S2** |`` ist dieselbe Nummer — sonst vergibt das Skript sie doppelt."""
    pfad = _katalog_legen(
        tmp_path, KATALOG_TEXT.replace("| S2 | Session", "| **S2** | Session")
    )
    erg = vorfall.in_katalog(
        pfad, [vorfall.Vorfall(klasse="skill", symptom="Neu", ursache="Grund", loesung="Fix")]
    )
    assert erg.nummern == ["S3"]


def test_weg_zweiter_tick_schreibt_keine_zweite_zeile(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    _capo(welt, "--katalog")
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    _capo(welt, "--katalog")
    assert len(_vorfaelle(repo, 901)) == 1
    assert (repo / KATALOG).read_text(encoding="utf-8").count("| P2 |") == 1
    assert "| P3 |" not in (repo / KATALOG).read_text(encoding="utf-8")


def test_weg_probelauf_schreibt_nichts(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil ohne Nummer",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    _capo(welt, "--katalog", "--dry-run")
    assert _vorfaelle(repo, 901) == []
    assert (repo / KATALOG).read_text(encoding="utf-8") == KATALOG_TEXT


def test_weg_sauberes_ticket_hat_keinen_vorfall(welt: dict[str, Path]) -> None:
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(
        repo,
        "feat: Bauteil (#901) [skip ci]",
        {
            "docs/verify-hard/901_beleg.md": "Beleg\n",
            str(KATALOG): KATALOG_TEXT,
        },
    )
    ergebnis = _capo(welt, "--katalog")
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _vorfaelle(repo, 901) == []
    assert (repo / KATALOG).read_text(encoding="utf-8") == KATALOG_TEXT


# --- Naht 4: Aufpasser meldet Stillstand → Vorfall ------------------------------


def _aufpasser(tmp_path: Path, trocken: bool = False):
    """Aufpasser mit echtem Zustandsordner; nur ``gh`` wird abgefangen."""
    from to_spawn import aufpasser as modul

    e = modul.Einstellungen(zustand=tmp_path / "zustand", trocken=trocken)
    wache = modul.Aufpasser(e)
    aufrufe: list[tuple] = []
    wache.sh = lambda *args, **kwargs: aufrufe.append((args, kwargs)) or ""  # type: ignore[method-assign]
    return wache, aufrufe


def test_aufpasser_stillstand_schreibt_vorfall(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wache, aufrufe = _aufpasser(tmp_path)
    wache.melden("900", repo, "spec-900/bau 901", "startet_nicht", "Fenster leer")
    zeilen = _vorfaelle(repo, 901)
    assert len(zeilen) == 1
    assert zeilen[0]["klasse"] == "skill"
    assert zeilen[0]["regel"] == "startet_nicht"
    assert aufrufe, "Kommentar im Spec-Issue fehlt"


def test_aufpasser_normaler_zug_schreibt_keinen_vorfall(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wache, _ = _aufpasser(tmp_path)
    wache.melden("900", repo, "spec-900/bau 901", "gestartet", "läuft")
    assert _vorfaelle(repo, 901) == []


def test_aufpasser_ohne_ticket_schreibt_auf_die_spec(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wache, _ = _aufpasser(tmp_path)
    wache.melden("900", repo, "spec-900/wache 900", "braucht_david", "wartet")
    zeilen = _vorfaelle(repo, 900)
    assert len(zeilen) == 1
    assert zeilen[0]["klasse"] == "mensch"


def test_aufpasser_trockenlauf_schreibt_nichts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wache, _ = _aufpasser(tmp_path, trocken=True)
    wache.melden("900", repo, "spec-900/bau 901", "startet_nicht", "Fenster leer")
    assert _vorfaelle(repo, 901) == []


def test_weg_eigene_vorfall_zeile_tarnt_tote_session_nicht(
    welt: dict[str, Path],
) -> None:
    """Regression: capos eigene Vorfall-Zeile galt als frische Spur der Session.

    Ab dem zweiten Tick meldete der Wächter die tote Session dann nicht mehr —
    gefunden beim Bau von #286 durch ``test_waechter_213_fix.py``.
    """
    repo = welt["repo"]
    _welt_sofort(welt)
    _commit(repo, "chore: Katalog", {str(KATALOG): KATALOG_TEXT})
    _gh_setzen(
        welt,
        "902",
        updated_at=_iso(timedelta(hours=9)),
        assignees=["Shavy72"],
    )
    erster = _capo(welt, "--katalog")
    assert "session_verwaist" in erster.stdout, _text(erster)
    assert len(_vorfaelle(repo, 902)) == 1

    zweiter = _capo(welt, "--katalog")
    assert "session_verwaist" in zweiter.stdout, _text(zweiter)
    assert len(_vorfaelle(repo, 902)) == 1  # kein zweiter Eintrag
    zeile = _vorfaelle(repo, 902)[0]
    assert zeile["quelle"] == "capo"


def test_vorfall_einer_session_zaehlt_als_spur(tmp_path: Path) -> None:
    """Meldet die Session selbst einen Vorfall, ist das ein Lebenszeichen."""
    from to_spawn import vorfall as modul

    eigen = {"typ": "vorfall", "quelle": "session", "klasse": "skill"}
    fremd = {"typ": "vorfall", "quelle": "capo", "klasse": "skill"}
    andere = {"typ": "session_start"}
    assert modul.ohne_waechter_zeilen([eigen, fremd, andere]) == [eigen, andere]


def test_aufpasser_kennt_jeden_stillstand(tmp_path: Path) -> None:
    """Jedes Stillstands-Ereignis des Aufpassers hat Vorfall-Worte (#286).

    ``gestartet``/``fortgesetzt``/``geschlossen`` sind Normalbetrieb und fehlen
    bewusst; alles, was einmal am Tag gemeldet wird, ist ein Stillstand.
    """
    from to_spawn import aufpasser as modul

    fehlend = modul.MELDUNG_EINMAL_PRO_TAG - set(modul.EREIGNIS_VORFALL)
    assert fehlend == set(), fehlend
    assert "angestupst" in modul.EREIGNIS_VORFALL
    assert not {"gestartet", "fortgesetzt", "geschlossen"} & set(modul.EREIGNIS_VORFALL)
