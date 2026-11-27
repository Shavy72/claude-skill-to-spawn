"""#924: Abnahme-Kommentar „passt“ reicht als Live-Freigabe (kein zweites Live-Ja).

Prüft:
1. Ohne Staging: „passt — abgenommen.“ am Abnahme-Ticket startet Komplett-Prüfung (genau einmal)
   und danach den Live-Schritt ohne Briefkasten-Frage; zweiter Takt startet nicht erneut.
2. Mit Staging: erst ALLE_AUF_STAGING im Takt (ohne Live), nach „passt“ direkt Live ohne
   zweiten Komplett-Lauf und ohne Briefkasten-Frage.
3. Abgrenzung: Kritik-Text („Sonst passt alles. Bitte nachbessern…“), „passt nicht“,
   Aufseher-Annahme nach Frist, offenes Bau-Ticket (Teilabnahme) und Probelauf (``--dry-run``)
   starten Live nie.
4. Hook-Sperre „Live-Ja nur vom Nutzer“ bleibt unangetastet.
5. Aufseher-Prompts, Befugnis-Text und Wecker-Text nennen „passt“ als Live-Freigabe ohne
   zweites Live-Ja / Briefkasten-Frage.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213.
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from test_komplett_abnahme_853 import _abnahme_hinzufuegen, _bau_tickets_zu
from test_komplett_je_spec_425 import _befehl, _laeufe
from test_komplett_live_tuer_456 import _live_befehl
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _gh_zustand,
    _iso,
    _konfig,
    _text,
    welt,
)

from to_spawn import anleitung, befugnis, briefkasten, briefkasten_wecker, capo, hooks, waechter_takt

SKILL = Path(__file__).resolve().parents[1]
GH = "test/wegwerf"
STAGING = {"url": "http://127.0.0.1:8001/"}


def _lade(name: str, datei: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def _einrichten(welt: dict[str, Path], *, staging: bool) -> tuple[Path, Path]:
    zaehl, live_zaehl = welt["tmp"] / "zaehl.txt", welt["tmp"] / "live.txt"
    exclude = welt["repo"] / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    alt = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if ".to-spawn/*" not in alt:
        exclude.write_text(alt + "\n.to-spawn/*\n!.to-spawn/config.json\n", encoding="utf-8")
    _konfig(
        welt,
        staging=STAGING if staging else {"ohne": True},
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        live={"befehl": _live_befehl(live_zaehl), "url": "http://127.0.0.1:18513/"},
    )
    return zaehl, live_zaehl


def _kommentar(
    welt: dict[str, Path],
    ticket: str,
    body: str,
    *,
    rolle: str = "OWNER",
    login: str = "nutzer",
) -> None:
    daten = _gh_zustand(welt)
    liste = daten.setdefault("kommentare", {}).setdefault(ticket, [])
    liste.append(
        {
            "id": len(liste) + 1,
            "autor": login,
            "body": body,
            "created_at": _iso(),
            "author_association": rolle,
        }
    )
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "passt",
        "Passt.",
        "passt!",
        "passt — abgenommen.",
        "passt - abgenommen",
        "Alles passt, bitte live schalten.",
        "> Aufseher: Teilabnahme bereit\n\npasst",
    ],
)
def test_ist_abnahme_passt_erkennt_freigabe(text: str) -> None:
    assert capo.ist_abnahme_passt(text) is True, text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "passt nicht",
        "passt noch nicht",
        "passt, aber der Button fehlt",
        (
            "Punkt Mahngebühr anders: Kulanz nur 2 Tage statt 3 und Deckel 7,50 € statt 10,00 €. "
            "Außerdem soll in der Leihliste bei jeder überfälligen Zeile ‚seit N Tagen überfällig‘ stehen. "
            "Sonst passt alles. Bitte nachbessern und die Rundschau neu machen."
        ),
        "Der Button fehlt noch.",
        "Bitte noch ändern.",
    ],
)
def test_ist_abnahme_passt_lehnt_kritik_und_verneinung_ab(text: str) -> None:
    assert capo.ist_abnahme_passt(text) is False, text


def test_ohne_staging_passt_startet_komplett_und_live_ohne_briefkasten(welt: dict[str, Path]) -> None:
    """Ohne Staging: passt am Abnahme-Ticket → Komplett-Prüfung (1×) → Live (1×), 0 Briefkasten-Fragen."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)

    # Vor „passt“: weder Komplett-Prüfung noch Live.
    vorher = _capo(welt)
    assert vorher.returncode == 0, _text(vorher)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0

    # Nutzer kommentiert „passt — abgenommen.“ am Abnahme-Ticket #903.
    _kommentar(welt, "903", "Aufseher: Rundschau und Abnahme-Paket bereit.")
    _kommentar(welt, "903", "passt — abgenommen.")

    erster = _capo(welt)
    assert erster.returncode == 0, _text(erster)
    assert _laeufe(zaehl) == 1, _text(erster)
    assert _laeufe(live_zaehl) == 1, _text(erster)
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH)
    assert briefkasten.offene(["901", "902", "903"], hauptbaum=welt["repo"]) == []

    # Zweiter Takt startet weder Komplett-Prüfung noch Live erneut.
    zweiter = _capo(welt)
    assert zweiter.returncode == 0, _text(zweiter)
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 1


def test_mit_staging_passt_startet_live_ohne_zweiten_komplett_lauf(welt: dict[str, Path]) -> None:
    """Mit Staging: Takt prüft ALLE_AUF_STAGING (ohne Live); nach „passt“ startet Live direkt."""
    zaehl, live_zaehl = _einrichten(welt, staging=True)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)

    vorher = _capo(welt)
    assert vorher.returncode == 0, _text(vorher)
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 0

    _kommentar(welt, "903", "passt — abgenommen.")
    nach_passt = _capo(welt)
    assert nach_passt.returncode == 0, _text(nach_passt)
    assert _laeufe(zaehl) == 1, _text(nach_passt)
    assert _laeufe(live_zaehl) == 1, _text(nach_passt)
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH)
    assert briefkasten.offene(["901", "902", "903"], hauptbaum=welt["repo"]) == []


def test_kritik_mit_sonst_passt_alles_startet_kein_live(welt: dict[str, Path]) -> None:
    """Kritik-Kommentar aus Probe 7 („Sonst passt alles. Bitte nachbessern…“) löst kein Live aus."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    _kommentar(
        welt,
        "903",
        "Punkt Mahngebühr anders: Kulanz nur 2 Tage statt 3. Sonst passt alles. Bitte nachbessern.",
    )

    erg = _capo(welt)
    assert erg.returncode == 0, _text(erg)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0
    assert not capo.live_gelaufen(welt["repo"], int(SPEC), GH)


def test_aufseher_annahme_startet_kein_live(welt: dict[str, Path]) -> None:
    """Automatische Aufseher-Annahme mit Wort „passt“ startet nie Live — nur der Mensch gibt frei."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    _kommentar(
        welt,
        "903",
        "Aufseher: Annahme nach 60 min nach Doktrin — der Mensch kann kippen. "
        "Die Annahme nach der Frist deckt nur die Entscheidungs-Liste (Abnahme „passt“).",
    )

    erg = _capo(welt)
    assert erg.returncode == 0, _text(erg)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0
    assert not capo.live_gelaufen(welt["repo"], int(SPEC), GH)


def test_teilabnahme_passt_bei_offenem_bau_ticket_startet_kein_live(welt: dict[str, Path]) -> None:
    """Teilabnahme „passt“ solange noch Bau-Tickets offen sind, startet nie Live."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _kommentar(welt, "903", "passt")

    erg = _capo(welt)
    assert erg.returncode == 0, _text(erg)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0


def test_dry_run_startet_nach_passt_kein_live(welt: dict[str, Path]) -> None:
    """Probelauf meldet nur ``[Probe]`` und startet weder Komplett-Prüfung noch Live."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    _kommentar(welt, "903", "passt — abgenommen.")

    erg = _capo(welt, "--dry-run")
    assert erg.returncode == 0, _text(erg)
    assert "[Probe]" in erg.stdout and "Live-Schritt" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0


def test_hook_ja_sperre_bleibt_unangetastet() -> None:
    """Abgrenzung #924: die Hook-Sperre „Live-Ja nur vom Nutzer“ bleibt unverändert scharf."""
    assert hooks.ja_aufrufe("~/.claude/bin/ja 11") is True
    assert hooks.ja_aufrufe("python to_spawn.py nutzer-antwort 11 --id abc --wahl ja --grund ok") is True


def test_prompts_und_wecker_nennen_passt_als_live_freigabe_ohne_zweites_ja() -> None:
    """Alle Aufseher-Quellen und der Wecker stellen klar: „passt“ = Live-Freigabe, keine Briefkasten-Frage."""
    wache = _lade("wache_924", SKILL / "skripte" / "wache.py")
    for quelle in (anleitung.ABSCHLUSS_ANWEISUNG, waechter_takt.PROMPT, wache.PROMPT):
        assert "Abnahme-Kommentar „passt“" in quelle
        assert "kein zweites Live-Ja" in quelle
        assert "keine Briefkasten-Frage" in quelle
    assert "keine Briefkasten-Frage" in befugnis.aufseher_text("python X/to_spawn.py")
    assert "Live-Freigabe" in briefkasten_wecker.KOMMENTAR_TEXT
    assert "ohne Briefkasten-Frage" in briefkasten_wecker.KOMMENTAR_TEXT


@pytest.mark.parametrize(
    "eigener_text",
    [
        "Aufseher: Abnahme erhalten. Komplett-Prüfung vor Live grün.",
        "Bau-Session: Abnahme notiert.",
        "Aufpasser: Status geprüft.",
    ],
)
def test_abnahme_passt_bleibt_nach_eigenem_kommentar_sichtbar_935(eigener_text: str) -> None:
    """#935: Eigene Kommentare (Aufseher-Quittung, Session, Aufpasser) nach „passt“ verdecken die Freigabe nicht."""
    passt = {
        "id": 1,
        "autor": "nutzer",
        "body": "passt — abgenommen.",
        "created_at": "2026-11-27T04:24:20Z",
    }
    quittung = {
        "id": 2,
        "autor": "nutzer",
        "body": eigener_text,
        "created_at": "2026-11-27T04:26:04Z",
    }
    assert capo.abnahme_passt_kommentar([passt, quittung]) == passt


def test_abnahme_kritik_nach_passt_bleibt_stopp_935() -> None:
    """#935 Gegenfall: folgt nach „passt“ (auch nach Aufseher-Quittung) eine Mensch-Kritik, gibt es kein Live."""
    passt = {
        "id": 1,
        "autor": "nutzer",
        "body": "passt — abgenommen.",
        "created_at": "2026-11-27T04:24:20Z",
    }
    quittung = {
        "id": 2,
        "autor": "nutzer",
        "body": "Aufseher: Abnahme erhalten.",
        "created_at": "2026-11-27T04:26:04Z",
    }
    kritik = {
        "id": 3,
        "autor": "nutzer",
        "body": "Stopp, bitte noch ändern.",
        "created_at": "2026-11-27T04:30:00Z",
    }
    assert capo.abnahme_passt_kommentar([passt, kritik]) is None
    assert capo.abnahme_passt_kommentar([passt, quittung, kritik]) is None


def test_aufseher_quittung_nach_passt_startet_trotzdem_live_935(welt: dict[str, Path]) -> None:
    """#935 End-to-End im Takt: Mensch „passt“ → Aufseher-Quittung → Takt startet Live."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)

    _kommentar(welt, "903", "passt — abgenommen.")
    _kommentar(welt, "903", "Aufseher: Abnahme erhalten. Komplett-Prüfung vor Live grün.")

    erg = _capo(welt)
    assert erg.returncode == 0, _text(erg)
    assert _laeufe(zaehl) == 1, _text(erg)
    assert _laeufe(live_zaehl) == 1, _text(erg)
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH)


def _leitstand_vorbereiten(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Ruft ``skripte/leitstand.py`` (``vorbereiten``) ohne capo-Takt gegen den Fake-Stand aus ``welt``."""
    ls = _lade("leitstand_924", SKILL / "skripte" / "leitstand.py")
    daten = _gh_zustand(welt)
    issues_gql: dict[str, dict[str, object]] = {
        SPEC: {"state": "OPEN", "labels": {"nodes": []}, "comments": {"nodes": []}}
    }
    titel: dict[str, str] = {}
    for nr_int in daten.get("sub", {}).get(SPEC, []):
        nr = str(nr_int)
        item = daten.get("issues", {}).get(nr) or {}
        titel[nr] = str(item.get("title") or f"Ticket {nr}")
        labels = [{"name": lbl["name"] if isinstance(lbl, dict) else str(lbl)} for lbl in item.get("labels") or []]
        issues_gql[nr] = {
            "state": str(item.get("state") or "open").upper(),
            "stateReason": item.get("state_reason"),
            "closedAt": item.get("closed_at"),
            "labels": {"nodes": labels},
            "timelineItems": {"nodes": []},
            "comments": {"nodes": []},
        }
    monkeypatch.setenv("TO_SPAWN_GH_REPO", GH)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    monkeypatch.setattr(ls, "lade_sessions_stand", lambda _repo: object())
    monkeypatch.setattr(ls, "prozess_stand", lambda _ss, _spec: (titel, {nr: "aus" for nr in titel}))
    monkeypatch.setattr(ls, "gh_issues", lambda _repo, _nrs: issues_gql)
    monkeypatch.setattr(ls, "repo_slug", lambda _repo: GH)
    return ls.vorbereiten(ls.Ablage(welt["repo"], SPEC))


def test_ohne_capo_leitstand_passt_beantwortet_live_frage_und_startet_live(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#924 Rest (-ohne, nur leitstand.py): passt beantwortet offene Live-Frage (ja) und startet Komplett + Live."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen(
        "903",
        "Darf Spec #900 live gehen?",
        "ja",
        2,
        [],
        ziel="live",
        art="deploy",
        hauptbaum=welt["repo"],
    )

    # Vor „passt“: Live-Frage bleibt offen, weder Komplett-Prüfung noch Live starten.
    _leitstand_vorbereiten(welt, monkeypatch)
    assert briefkasten.lesen("903", fid, hauptbaum=welt["repo"]) == briefkasten.NUTZER
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0

    # Nutzer kommentiert „passt — abgenommen.“ (+ Aufseher-Quittung danach wie in #935).
    _kommentar(welt, "903", "passt — abgenommen.")
    _kommentar(welt, "903", "Aufseher: Abnahme erhalten.")

    _leitstand_vorbereiten(welt, monkeypatch)
    antwort = briefkasten.lesen("903", fid, hauptbaum=welt["repo"])
    assert isinstance(antwort, briefkasten.Antwort)
    assert antwort.wahl == "ja"
    assert antwort.von == briefkasten.NUTZER_VON
    assert briefkasten.offene(["903"], hauptbaum=welt["repo"]) == []
    assert _laeufe(zaehl) == 1
    assert _laeufe(live_zaehl) == 1
    assert capo.live_gelaufen(welt["repo"], int(SPEC), GH)

    # Zweiter Leitstand-Lauf startet weder Komplett-Prüfung noch Live erneut.
    _leitstand_vorbereiten(welt, monkeypatch)
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 1


def test_capo_takt_passt_beantwortet_offene_live_frage_im_briefkasten(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#924 (capo-Weg): auch im capo-Takt wird eine offene Briefkasten-Live-Frage nach „passt“ mit „ja“ beantwortet."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen(
        "903",
        "Darf Spec #900 live ausgerollt werden?",
        "ja",
        2,
        [],
        ziel="live",
        art="deploy",
        hauptbaum=welt["repo"],
    )
    _kommentar(welt, "903", "passt — abgenommen.")

    erg = _capo(welt)
    assert erg.returncode == 0, _text(erg)
    antwort = briefkasten.lesen("903", fid, hauptbaum=welt["repo"])
    assert isinstance(antwort, briefkasten.Antwort)
    assert antwort.wahl == "ja"
    assert antwort.von == briefkasten.NUTZER_VON
    assert briefkasten.offene(["903"], hauptbaum=welt["repo"]) == []
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 1


def test_nicht_live_klasse_2_frage_bleibt_trotz_passt_offen(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#924 Abgrenzung: Nicht-Live-Klasse-2-Fragen (Kosten/Konto/Löschen) bleiben trotz „passt“ offen."""
    _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    _bau_tickets_zu(welt)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid_kosten = briefkasten.einwerfen(
        "903",
        "Kostenpflichtiges Abo buchen?",
        "nein",
        2,
        [],
        ziel="konto",
        art="kosten",
        hauptbaum=welt["repo"],
    )
    fid_loeschen = briefkasten.einwerfen(
        "903",
        "Live-Datenbank löschen?",
        "nein",
        2,
        [],
        ziel="live",
        art="loeschen",
        hauptbaum=welt["repo"],
    )
    _kommentar(welt, "903", "passt — abgenommen.")

    _leitstand_vorbereiten(welt, monkeypatch)
    assert briefkasten.lesen("903", fid_kosten, hauptbaum=welt["repo"]) == briefkasten.NUTZER
    assert briefkasten.lesen("903", fid_loeschen, hauptbaum=welt["repo"]) == briefkasten.NUTZER


def test_ohne_capo_teilabnahme_und_kritik_beantworten_live_frage_nicht(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#924 Gegenfall (ohne capo): offenes Bau-Ticket oder Kritik lassen Live-Frage offen und starten kein Live."""
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _abnahme_hinzufuegen(welt)
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    fid = briefkasten.einwerfen(
        "903",
        "Darf Spec #900 live gehen?",
        "ja",
        2,
        [],
        ziel="live",
        art="deploy",
        hauptbaum=welt["repo"],
    )
    _kommentar(welt, "903", "passt")

    # Bau-Tickets noch offen (Teilabnahme): nichts beantworten, kein Live.
    _leitstand_vorbereiten(welt, monkeypatch)
    assert briefkasten.lesen("903", fid, hauptbaum=welt["repo"]) == briefkasten.NUTZER
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0

    # Bau-Tickets zu, aber jüngster Mensch-Kommentar ist Kritik: bleibt offen, kein Live.
    _bau_tickets_zu(welt)
    _kommentar(welt, "903", "passt nicht, bitte noch ändern.")
    _leitstand_vorbereiten(welt, monkeypatch)
    assert briefkasten.lesen("903", fid, hauptbaum=welt["repo"]) == briefkasten.NUTZER
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0
