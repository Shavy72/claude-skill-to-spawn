"""#279 Paket 6b: Leitstand-Anzeige liest die Stufe statt ``ende`` und kennt „gebaut um“.

Quelle der Wahrheit bleibt ``to_spawn/ticket_zustand.py``; der Leitstand reicht ``stufe``
und ``gebaut`` an die Seite weiter, die Seite rechnet damit Kopf, Dauer, Plan und Zeitbalken.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
SEITE = SKILL / "leitstand" / "seite.html"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, ticket_zustand  # noqa: E402

LABEL = ticket_zustand.STAGING_FERTIG_LABEL
ohne_node = pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    if str(SKRIPTE) not in sys.path:
        sys.path.insert(0, str(SKRIPTE))
    spec = importlib.util.spec_from_file_location("leitstand_279_p6b", SKRIPTE / "leitstand.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def _gebaut_issue(zeit: str | None) -> dict[str, Any]:
    """GraphQL-Form: offen, Label ``staging:fertig``, Label-Ereignis mit Zeitpunkt."""
    knoten = [{"createdAt": "2026-11-23T08:00:00Z", "label": {"name": "anderes"}}]
    if zeit:
        knoten.append({"createdAt": zeit, "label": {"name": LABEL}})
    return {"state": "OPEN", "labels": {"nodes": [{"name": LABEL}]}, "timelineItems": {"nodes": knoten}}


# --- Punkt 1 + 5: Leitstand liefert ``stufe`` und ``gebaut`` -------------------


def test_berechne_liefert_stufe_je_ticket(ls: ModuleType) -> None:
    issues = {
        "1": {"state": "OPEN", "labels": {"nodes": []}},
        "2": _gebaut_issue("2026-11-23T09:30:00Z"),
        "3": {"state": "CLOSED", "closedAt": "2026-11-23T09:00:00Z"},
        "4": {"state": "CLOSED", "stateReason": "NOT_PLANNED", "closedAt": "2026-11-23T09:00:00Z"},
    }
    titel = {n: "T" for n in issues}
    live, _, _ = ls.berechne("9", titel, {}, issues, {}, ls.leerer_zustand())
    assert {n: live["tickets"][n]["stufe"] for n in issues} == {
        "1": "offen",
        "2": "fertig_gebaut",
        "3": "zu",
        "4": "verworfen",
    }


def test_berechne_gebaut_aus_label_ereignis(ls: ModuleType) -> None:
    live, _, _ = ls.berechne("9", {"2": "T"}, {}, {"2": _gebaut_issue("2026-11-23T09:30:00Z")}, {}, ls.leerer_zustand())
    eintrag = live["tickets"]["2"]
    assert eintrag["gebaut"] == ls.lokal_iso("2026-11-23T09:30:00Z")
    assert "ende" not in eintrag


def test_berechne_gebaut_ersatz_aus_bau_log(ls: ModuleType) -> None:
    """Kein Label-Ereignis lesbar: letzte Aktivität im Bau-Log ist „gebaut um“."""
    zeilen = (
        [
            {"typ": "session_start", "ts": "2026-11-23T08:00:00+00:00"},
            {"typ": "session_ende", "ts": "2026-11-23T08:40:00+00:00"},
        ],
        None,
    )
    live, _, _ = ls.berechne("9", {"2": "T"}, {}, {"2": _gebaut_issue(None)}, {"2": zeilen}, ls.leerer_zustand())
    assert live["tickets"]["2"]["gebaut"] == ls.lokal_iso("2026-11-23T08:40:00+00:00")


def test_berechne_kein_gebaut_bei_offen_oder_zu(ls: ModuleType) -> None:
    issues = {
        "1": {"state": "OPEN", "labels": {"nodes": []}},
        "3": {"state": "CLOSED", "closedAt": "2026-11-23T09:00:00Z"},
    }
    live, _, _ = ls.berechne("9", {"1": "T", "3": "T"}, {}, issues, {}, ls.leerer_zustand())
    assert "gebaut" not in live["tickets"]["1"] and "gebaut" not in live["tickets"]["3"]


def test_graphql_holt_label_ereignisse(ls: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gesehen: list[str] = []

    def lauf(argv: list[str], repo: Path) -> tuple[int, str]:
        gesehen.append(argv[-1])
        return 0, json.dumps({"data": {"repository": {"i5": {"state": "OPEN"}}}})

    monkeypatch.setattr(ls, "repo_slug", lambda repo: "o/n")
    monkeypatch.setattr(ls, "gh_lauf", lauf)
    ls.gh_issues(tmp_path, ["5"])
    assert "LABELED_EVENT" in gesehen[0] and "createdAt" in gesehen[0]


# --- Punkt 7: Lesefehler verliert ``ende`` nicht -------------------------------


def test_berechne_lesefehler_behaelt_ende(ls: ModuleType) -> None:
    zustand = ls.leerer_zustand()
    zustand["ende"] = {"1": "2026-11-23T10:00:00+01:00"}
    live, _, merk = ls.berechne("9", {"1": "T"}, {}, {"1": {}}, {}, zustand)
    assert live["tickets"]["1"]["ende"] == "2026-11-23T10:00:00+01:00"
    assert merk["ende"] == {"1": "2026-11-23T10:00:00+01:00"}
    assert "stufe" not in live["tickets"]["1"]  # unbekannt statt falsch „offen“


# --- Seite (node) --------------------------------------------------------------


def _seite() -> str:
    return SEITE.read_text(encoding="utf-8")


def _js(name: str) -> str:
    seite = _seite()
    anfang = seite.index(f"  function {name}(")
    return seite[anfang : seite.index("\n  }\n", anfang) + 4]


VORSPANN = (
    "const MIN = 60000;\n"
    "const ts = (v) => { if (!v) return null; const n = Date.parse(v); return Number.isFinite(n) ? n : null; };\n"
    "const dauer = (ms) => Math.round(ms / MIN) + ' Min';\n"
    "const esc = (s) => String(s);\n"
)


def _node(js: str) -> list[str]:
    aus = subprocess.run(["node", "-e", VORSPANN + js], capture_output=True, text=True, check=True, encoding="utf-8")
    return aus.stdout.splitlines()


@ohne_node
def test_seite_zu_an_stufe_nicht_an_ende() -> None:
    """„Alles fertig.“ folgt der Stufe — ``ende`` allein reicht nicht, fehlendes ``ende`` stört nicht."""
    js = (
        "let tickets = [];\nconst model = { tiefe: new Map() };\n"
        "const phaseKey = (t) => 1, offeneBlocker = () => [], nrListe = (l) => l.join(', ');\n"
        + _js("istZu")
        + _js("phasenStand")
        + "\nconst t = (nr, stufe, ende) => ({ nr, zustand: 'fertig', blocked_by: [], stufe, ende });\n"
        "tickets = [t(1, 'zu', null), t(2, 'verworfen', null)];\n"
        "console.log(phasenStand({ key: 1 }).ueber);\n"
        "tickets = [t(1, 'zu', null), t(2, 'fertig_gebaut', '2026-11-23T10:00:00')];\n"
        "console.log(phasenStand({ key: 1 }).ueber);\n"
    )
    assert _node(js) == ["Alles fertig.", "Fertig gebaut, wartet auf Live."]


@ohne_node
def test_seite_kopf_trennt_zu_und_fertig_gebaut() -> None:
    js = (
        _js("istZu") + _js("kopfFertigText") + "\nconst t = (zustand, stufe) => ({ zustand, stufe });\n"
        "console.log(kopfFertigText([t('fertig', 'zu'), t('fertig', 'verworfen'), t('fertig', 'fertig_gebaut'), t('läuft', 'offen')]));\n"
        "console.log(kopfFertigText([t('fertig', 'zu'), t('wartet', 'offen')]));\n"
    )
    assert _node(js) == ["2 von 4 Tickets zu · 1 fertig gebaut", "1 von 2 Tickets zu"]


@ohne_node
def test_seite_mobile_liste_zeigt_phase_bei_fertig() -> None:
    """Wie im Gleisplan (``knotenText``): bei „fertig“ Phase ohne „Gerade:“, sonst mit."""
    js = (
        _js("glPhase") + "\nconst p = 'Ticket fertig gebaut, wartet auf Live';\n"
        "console.log(glPhase({ zustand: 'fertig', phase: p }));\n"
        "console.log(glPhase({ zustand: 'läuft', phase: p }));\n"
        "console.log(JSON.stringify(glPhase({ zustand: 'fertig', phase: '' })));\n"
    )
    assert _node(js) == [
        '<span class="gl-phase">Ticket fertig gebaut, wartet auf Live</span>',
        '<span class="gl-phase">Gerade: Ticket fertig gebaut, wartet auf Live</span>',
        '""',
    ]


@ohne_node
def test_seite_fertig_um_nimmt_ende_sonst_gebaut() -> None:
    js = (
        _js("fertigUm")
        + "\nconsole.log(fertigUm({ ende: '2026-11-23T10:00:00Z', gebaut: '2026-11-23T09:00:00Z' }) === Date.parse('2026-11-23T10:00:00Z'));\n"
        "console.log(fertigUm({ ende: null, gebaut: '2026-11-23T09:00:00Z' }) === Date.parse('2026-11-23T09:00:00Z'));\n"
        "console.log(fertigUm({ ende: null, gebaut: null }));\n"
    )
    assert _node(js) == ["true", "true", "null"]


@ohne_node
def test_seite_listenstatus_dauer_bei_fertig_gebaut() -> None:
    js = (
        "const nrListe = (l) => l.join(', '), offeneBlocker = () => [], fortschritt = () => 100, restText = () => '';\n"
        "const ZWORT = {}, anzeigeZustand = (t) => t.zustand;\n"
        + _js("fertigUm")
        + _js("listenStatus")
        + "\nconsole.log(listenStatus({ zustand: 'fertig', start: '2026-11-23T09:00:00Z', ende: null, gebaut: '2026-11-23T09:25:00Z' }, 0));\n"
    )
    assert _node(js) == ["fertig · 25 Min"]


def test_seite_ende_nur_ueber_fertig_um() -> None:
    """Dauer, Plan, Detail, Zeitleiste und Zeitbalken lesen das Ende nur über ``fertigUm`` (#279 P6b)."""
    seite = _seite()
    ausserhalb = seite.replace(_js("fertigUm"), "")
    assert "ts(t.ende)" not in ausserhalb
    assert '"stufe"' in seite.split("const LIVE_FELDER", 1)[1].split("\n", 1)[0]
    assert '"gebaut"' in seite.split("const LIVE_FELDER", 1)[1].split("\n", 1)[0]


# --- Punkt 2: Aufseher-Stand ohne eigene Liste --------------------------------


def test_aufseher_stand_keine_eigene_zu_liste() -> None:
    quelle = (SKILL / "to_spawn" / "aufseher_stand.py").read_text(encoding="utf-8")
    assert "('zu', 'verworfen')" not in quelle and '("zu", "verworfen")' not in quelle


def test_aufseher_kopf_zaehlt_verworfen_als_zu_und_blendet_aus() -> None:
    lagen = [
        aufseher_stand.TicketLage(1, "offen", aufseher_stand.ARBEITET),
        aufseher_stand.TicketLage(2, "verworfen", aufseher_stand.KEIN_FENSTER),
        aufseher_stand.TicketLage(3, "zu", aufseher_stand.KEIN_FENSTER),
        aufseher_stand.TicketLage(4, "fertig_gebaut", aufseher_stand.KEIN_FENSTER),
    ]
    assert "1 offen, 1 fertig gebaut, 2 zu" in aufseher_stand.kopf_zeile(9, lagen, 0.0, False)
    assert [x.endgueltig_zu for x in lagen] == [False, True, True, False]
    assert aufseher_stand.TicketLage(5, None, aufseher_stand.KEIN_FENSTER).endgueltig_zu is False
