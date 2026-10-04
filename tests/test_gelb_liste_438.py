"""#438 Gelb-Liste: gelber Befund → Folge-Ticket + Manifest, nur rot öffnet wieder.

GitHub ist externer Dienst: ``gh.lauf`` wird hier durch eine Aufzeichnung ersetzt,
alles andere (Manifest-Datei, Feldprüfung, Prompt, Abschnitt im Spec-Stand) läuft echt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import befund, gh, manifest

SPEC = 399
TICKET = 410
GH_REPO = "besitzer/repo"


def _lade_skript(name: str) -> Any:
    os.environ.setdefault("TO_SPAWN_REPO", str(SKILL))
    spec = importlib.util.spec_from_file_location(
        f"{name}_438", SKILL / "skripte" / f"{name}.py"
    )
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    datei = manifest.manifest_pfad(tmp_path, SPEC)
    datei.parent.mkdir(parents=True)
    daten = {
        "spec": SPEC,
        "feature": "aufseher",
        "tickets": {
            str(TICKET): {
                "title": "Bau-Ticket",
                "schaetzung_k": 60,
                "umfang": "baut etwas",
            },
        },
    }
    datei.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def aufrufe(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    liste: list[list[str]] = []

    def falsch_gh(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
        liste.append(list(args))
        if args[:2] == ["issue", "create"]:
            return 0, f"https://github.com/{GH_REPO}/issues/999"
        return 0, ""

    monkeypatch.setattr(gh, "lauf", falsch_gh)
    return liste


def _manifest_text(repo: Path) -> str:
    return manifest.manifest_pfad(repo, SPEC).read_text(encoding="utf-8")


# (a) gelb → Folge-Ticket + Manifest, kein Reopen
def test_gelber_befund_legt_folge_ticket_an_statt_wieder_zu_oeffnen(
    repo: Path, aufrufe: list[list[str]]
) -> None:
    zeilen = befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "Screenshot unscharf", dry_run=False
    ).zeilen

    assert not any(a[:2] == ["issue", "reopen"] for a in aufrufe)
    creates = [a for a in aufrufe if a[:2] == ["issue", "create"]]
    assert len(creates) == 1
    assert "--repo" in creates[0] and GH_REPO in creates[0]
    body = creates[0][creates[0].index("--body") + 1]
    assert f"#{SPEC}" in body and f"#{TICKET}" in body and "Screenshot unscharf" in body

    tickets = json.loads(_manifest_text(repo))["tickets"]
    assert list(tickets) == [str(TICKET), "999"]  # Reihenfolge erhalten, neu hinten
    eintrag = tickets["999"]
    assert eintrag["gelb_von"] == TICKET
    assert eintrag["schaetzung_k"] > 0
    assert eintrag["umfang"] == "Screenshot unscharf"
    assert eintrag["title"]
    assert _manifest_text(repo).endswith("\n")
    assert any("#999" in z and "gelb" in z for z in zeilen)
    assert not any("FEHLER" in z for z in zeilen)


# (b) rot → Reopen mit Kommentar, Manifest unverändert
def test_roter_befund_oeffnet_wieder(repo: Path, aufrufe: list[list[str]]) -> None:
    vorher = _manifest_text(repo)
    zeilen = befund.melde(
        repo, GH_REPO, SPEC, TICKET, "rot", "Live-Beleg fehlt", dry_run=False
    ).zeilen

    reopens = [a for a in aufrufe if a[:2] == ["issue", "reopen"]]
    assert len(reopens) == 1
    assert reopens[0][2] == str(TICKET)
    kommentar = reopens[0][reopens[0].index("--comment") + 1]
    assert kommentar.startswith("Aufseher:") and "Live-Beleg fehlt" in kommentar
    assert not any(a[:2] == ["issue", "create"] for a in aufrufe)
    assert _manifest_text(repo) == vorher
    assert zeilen[-1] == f"#{TICKET} wieder geöffnet"


def test_capo_nutzt_befund_wieder_oeffnen(aufrufe: list[list[str]]) -> None:
    from to_spawn import capo

    funde = [capo.Verstoss(ticket=5, regel="Commit", text="kein Commit auf master")]
    assert capo._wieder_oeffnen(5, GH_REPO, funde, True) == [
        "#5 [Probe] würde wieder öffnen"
    ]
    assert capo._wieder_oeffnen(5, GH_REPO, funde, False) == ["#5 wieder geöffnet"]
    kommentar = aufrufe[-1][aufrufe[-1].index("--comment") + 1]
    assert kommentar == "Aufseher: Commit — kein Commit auf master"


# (c) Prompt: Schritt 4 nutzt befund, kein gh issue reopen
def test_wache_prompt_schritt_4_nutzt_befund() -> None:
    wache = _lade_skript("wache")
    schritt4 = next(z for z in wache.PROMPT.splitlines() if z.startswith("4."))
    assert "to_spawn.py befund" in schritt4
    assert "--stufe gelb" in schritt4 and "--stufe rot" in schritt4
    assert "gh issue reopen" not in wache.PROMPT
    text = wache.PROMPT.format(
        S=SPEC, REPO="r", DATUM="2026-10-04", TAKT=600, SKILL="/skill"
    )
    assert f"/skill/to_spawn.py befund --spec {SPEC}" in text


# (d) Gelbe Folgen mit Status
def test_gelbe_folgen_status(repo: Path, aufrufe: list[list[str]]) -> None:
    for text in ("eins", "zwei", "drei"):
        befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", text, dry_run=False)
    # alle Creates liefern 999 — Manifest-Einträge für die Statusprüfung direkt setzen
    datei = manifest.manifest_pfad(repo, SPEC)
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["tickets"]["1001"] = {**daten["tickets"]["999"], "title": "zwei"}
    daten["tickets"]["1002"] = {**daten["tickets"]["999"], "title": "drei"}
    datei.write_text(
        json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    antworten: dict[int, dict[str, Any] | None] = {
        999: {"state": "closed", "labels": []},
        1001: {"state": "open", "labels": [{"name": "verschoben"}]},
        1002: {"state": "open", "labels": [{"name": "bug"}]},
    }
    folgen = befund.gelbe_folgen(repo, SPEC, abruf=antworten.get)
    status = {f.nummer: f.status for f in folgen}
    assert status == {999: "gebaut", 1001: "verschoben", 1002: "offen"}
    assert all(f.gelb_von == TICKET for f in folgen)
    # Bau-Tickets ohne gelb_von gehören nicht dazu
    assert TICKET not in status


def test_gelbe_folgen_abruf_scheitert_ist_unbekannt(
    repo: Path, aufrufe: list[list[str]]
) -> None:
    befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "eins", dry_run=False)
    folgen = befund.gelbe_folgen(repo, SPEC, abruf=lambda n: None)
    assert [f.status for f in folgen] == ["unbekannt"]


# (e) Spec-Stand-Abschnitt
def test_spec_stand_zeigt_gelbe_folgen(repo: Path, aufrufe: list[list[str]]) -> None:
    befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "Screenshot unscharf", dry_run=False
    )
    spec_stand = _lade_skript("spec_stand")
    zeilen = spec_stand.gelb_zeilen(
        repo, SPEC, abruf=lambda n: {"state": "closed", "labels": []}
    )
    assert zeilen[0] == "Gelbe Folge-Tickets (Gesamtabnahme)"
    assert any("#999" in z and "gebaut" in z and f"#{TICKET}" in z for z in zeilen[1:])


def test_spec_stand_ohne_gelbe_folgen_leer(repo: Path) -> None:
    spec_stand = _lade_skript("spec_stand")
    assert spec_stand.gelb_zeilen(repo, SPEC, abruf=lambda n: None) == []


# (f) Probe schreibt nichts
def test_dry_run_schreibt_nichts(repo: Path, aufrufe: list[list[str]]) -> None:
    vorher = _manifest_text(repo)
    gelb = befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "x", dry_run=True).zeilen
    rot = befund.melde(repo, GH_REPO, SPEC, TICKET, "rot", "x", dry_run=True).zeilen
    assert aufrufe == []
    assert _manifest_text(repo) == vorher
    assert any("[Probe]" in z for z in gelb)
    assert rot == [f"#{TICKET} [Probe] würde wieder öffnen"]


# (g) gh create scheitert → FEHLER, Manifest unverändert
def test_create_scheitert_meldet_fehler(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gh, "lauf", lambda args, cwd=None: (1, ""))
    vorher = _manifest_text(repo)
    zeilen = befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "x", dry_run=False
    ).zeilen
    assert any("FEHLER" in z for z in zeilen)
    assert _manifest_text(repo) == vorher


def test_unbekannte_stufe_wird_abgelehnt(repo: Path, aufrufe: list[list[str]]) -> None:
    with pytest.raises(ValueError):
        befund.melde(repo, GH_REPO, SPEC, TICKET, "orange", "x", dry_run=False)


# (h) Manifest mit gelbem Eintrag besteht Feldprüfung
def test_manifest_mit_gelbem_eintrag_besteht_feldpruefung(
    repo: Path, aufrufe: list[list[str]]
) -> None:
    befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "Screenshot unscharf", dry_run=False
    )
    bericht = manifest.pruefe(repo, SPEC, {}, mit_github=False)
    assert bericht.sauber, bericht.text()
    assert "999" in bericht.tickets


# CLI
def test_cli_befund_ruft_melde(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gesehen: dict[str, Any] = {}

    def falsch_melde(*args: Any, **kwargs: Any) -> befund.Ergebnis:
        gesehen["args"] = args
        gesehen["kwargs"] = kwargs
        return befund.Ergebnis(ok=True, zeilen=["#410 gelb → Folge-Ticket #999"])

    monkeypatch.setattr(befund, "melde", falsch_melde)
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.setattr(gh, "repo_aus_origin", lambda cwd=None, fallback="": GH_REPO)
    spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_438", SKILL / "to_spawn.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    code = cli.main(
        [
            "befund",
            "--spec",
            str(SPEC),
            "--ticket",
            str(TICKET),
            "--stufe",
            "gelb",
            "--text",
            "t",
        ]
    )
    assert code == 0
    assert gesehen["args"][1:6] == (GH_REPO, SPEC, TICKET, "gelb", "t")


# ---------------------------------------------------------------------------
# Fixrunde #438 (Review-Befunde 1–7 + Zusatzfälle)
# ---------------------------------------------------------------------------


def _manifest_daten(repo: Path) -> dict[str, Any]:
    return json.loads(_manifest_text(repo))


def _schreibe_manifest(repo: Path, daten: dict[str, Any]) -> None:
    manifest.manifest_pfad(repo, SPEC).write_text(
        json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# Fix 1: Manifest-Schreiben scheitert nach issue create → klare FEHLER-Zeile, Exit ≠ 0
def test_fix1_manifest_schreiben_scheitert_meldet_angelegtes_ticket(
    repo: Path, aufrufe: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    vorher = _manifest_text(repo)

    def kaputt(*args: Any, **kwargs: Any) -> None:
        raise OSError("Platte voll")

    monkeypatch.setattr(os, "replace", kaputt)
    ergebnis = befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "x", dry_run=False)
    monkeypatch.undo()

    assert ergebnis.ok is False
    assert any(
        "FEHLER: Folge-Ticket #999 angelegt, Manifest nicht geschrieben — von Hand eintragen"
        in z
        for z in ergebnis.zeilen
    )
    assert _manifest_text(repo) == vorher
    ordner = manifest.manifest_pfad(repo, SPEC).parent
    assert sorted(p.name for p in ordner.iterdir()) == [f"spec-{SPEC}.json"]


# Fix 2: Doppel-Schutz — gleicher Befundtext am gleichen Ticket legt kein zweites Issue an
def test_fix2_gleicher_befund_wird_nicht_doppelt_angelegt(
    repo: Path, aufrufe: list[list[str]]
) -> None:
    befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "Screenshot unscharf", dry_run=False
    )
    zweites = befund.melde(
        repo, GH_REPO, SPEC, TICKET, "gelb", "  Screenshot unscharf \n", dry_run=False
    )
    assert len([a for a in aufrufe if a[:2] == ["issue", "create"]]) == 1
    assert zweites.ok is True
    assert zweites.zeilen == [f"#{TICKET} gelb schon gemeldet: Folge-Ticket #999"]


def test_fix2_anderer_text_legt_weiteres_folge_ticket_an(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nummern = iter(["999", "1000"])
    creates: list[list[str]] = []

    def falsch_gh(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
        if args[:2] == ["issue", "create"]:
            creates.append(args)
            return 0, f"https://github.com/{GH_REPO}/issues/{next(nummern)}"
        return 0, ""

    monkeypatch.setattr(gh, "lauf", falsch_gh)
    befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "eins", dry_run=False)
    zweites = befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "zwei", dry_run=False)
    assert len(creates) == 2 and zweites.ok
    tickets = _manifest_daten(repo)["tickets"]
    assert tickets["999"]["umfang"] == "eins" and tickets["1000"]["umfang"] == "zwei"


# Fix 3: kaputtes gelb_von → Warnung + überspringen
def test_fix3_kaputtes_gelb_von_wird_uebersprungen(
    repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    daten = _manifest_daten(repo)
    daten["tickets"]["500"] = {"title": "kaputt", "gelb_von": "abc"}
    daten["tickets"]["501"] = {"title": "gut", "gelb_von": TICKET}
    _schreibe_manifest(repo, daten)
    with caplog.at_level("WARNING", logger="to_spawn.befund"):
        folgen = befund.gelbe_folgen(repo, SPEC, abruf=lambda n: None)
    assert [f.nummer for f in folgen] == [501]
    assert any(
        "500" in r.getMessage() and "gelb_von" in r.getMessage() for r in caplog.records
    )


# Fix 4: Status-Abruf scheitert → Warnung mit Nummer, Status bleibt „unbekannt“
def test_fix4_status_abruf_scheitert_loggt_nummer(
    repo: Path, aufrufe: list[list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "eins", dry_run=False)
    with caplog.at_level("WARNING", logger="to_spawn.befund"):
        folgen = befund.gelbe_folgen(repo, SPEC, abruf=lambda n: None)
    assert [f.status for f in folgen] == ["unbekannt"]
    assert any(
        r.levelname == "WARNING" and "#999" in r.getMessage() for r in caplog.records
    )


# Fix 5: Exit-Code aus dem ok-Flag, nicht aus Textsuche
def _cli(monkeypatch: pytest.MonkeyPatch, repo: Path) -> Any:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.setattr(gh, "repo_aus_origin", lambda cwd=None, fallback="": GH_REPO)
    spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_438_fix", SKILL / "to_spawn.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


_CLI_ARGS = [
    "befund",
    "--spec",
    str(SPEC),
    "--ticket",
    str(TICKET),
    "--stufe",
    "gelb",
    "--text",
    "t",
]


def test_fix5_exit_code_folgt_ok_flag(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = _cli(monkeypatch, repo)
    monkeypatch.setattr(
        befund,
        "melde",
        lambda *a, **k: befund.Ergebnis(
            ok=True, zeilen=["#410 gelb → „FEHLER-Seite unscharf“"]
        ),
    )
    assert cli.main(_CLI_ARGS) == 0
    monkeypatch.setattr(
        befund,
        "melde",
        lambda *a, **k: befund.Ergebnis(ok=False, zeilen=["#410 kaputt"]),
    )
    assert cli.main(_CLI_ARGS) == 1


# Fix 6: kaputtes Manifest → Fehlerzeile im Abschnitt; fehlendes → still leer
def test_fix6_kaputtes_manifest_zeigt_fehlerzeile(repo: Path) -> None:
    manifest.manifest_pfad(repo, SPEC).write_text("{kaputt", encoding="utf-8")
    spec_stand = _lade_skript("spec_stand")
    zeilen = spec_stand.gelb_zeilen(repo, SPEC, abruf=lambda n: None)
    assert zeilen and any("FEHLER" in z for z in zeilen)


def test_fix6_fehlendes_manifest_bleibt_leer(tmp_path: Path) -> None:
    spec_stand = _lade_skript("spec_stand")
    assert spec_stand.gelb_zeilen(tmp_path, SPEC, abruf=lambda n: None) == []


# Fix 7: geschlossen als „not planned“ → verworfen
def test_fix7_not_planned_ist_verworfen(repo: Path, aufrufe: list[list[str]]) -> None:
    befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "eins", dry_run=False)
    folgen = befund.gelbe_folgen(
        repo,
        SPEC,
        abruf=lambda n: {
            "state": "closed",
            "state_reason": "not_planned",
            "labels": [],
        },
    )
    assert [f.status for f in folgen] == ["verworfen"]
    folgen = befund.gelbe_folgen(
        repo,
        SPEC,
        abruf=lambda n: {"state": "closed", "state_reason": "completed", "labels": []},
    )
    assert [f.status for f in folgen] == ["gebaut"]


# Zusatz: create mit Exit 0, aber ohne Issue-URL → FEHLER, kein Manifest-Eintrag
def test_create_exit_0_ohne_url_ist_fehler(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gh, "lauf", lambda args, cwd=None: (0, "irgendwas ohne Link"))
    vorher = _manifest_text(repo)
    ergebnis = befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "x", dry_run=False)
    assert ergebnis.ok is False
    assert any("FEHLER" in z for z in ergebnis.zeilen)
    assert _manifest_text(repo) == vorher


# Zusatz: kaputtes Manifest → kein gh-Aufruf
def test_kaputtes_manifest_kein_gh_aufruf(repo: Path, aufrufe: list[list[str]]) -> None:
    manifest.manifest_pfad(repo, SPEC).write_text("{kaputt", encoding="utf-8")
    ergebnis = befund.melde(repo, GH_REPO, SPEC, TICKET, "gelb", "x", dry_run=False)
    assert aufrufe == []
    assert ergebnis.ok is False


# Fix 8: Prompt Schritt 4 mit je einem Beispiel für rot und gelb
def test_fix8_prompt_schritt_4_hat_beispiele() -> None:
    wache = _lade_skript("wache")
    text = wache.PROMPT.format(
        S=SPEC, REPO="r", DATUM="2026-10-04", TAKT=600, SKILL="/skill"
    )
    schritt4 = next(z for z in text.splitlines() if z.startswith("4."))
    assert "Akzeptanz-Häkchen nicht erfüllt" in schritt4
    assert "Zusatz-Test" in schritt4
