"""#325 Teil 7: Bezeichner und Schalter nennen die Rolle („mensch“) mit Übergangs-Alias.

Prüft neue kanonische Namen und Rückwärtskompatibilität für gespeicherte Stände sowie
bestehende Aufrufer über alle betroffenen Module (capo, aufpasser, hausmeister, Leitstand).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, bau_log, capo, hausmeister, hausmeister_aufgabe, hausmeister_windows  # noqa: E402


def _lade_leitstand() -> ModuleType:
    spec = importlib.util.spec_from_file_location("leitstand_teil7_test", SKILL / "skripte" / "leitstand.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


def test_capo_mensch_antwort_und_alias() -> None:
    seit = datetime(2026, 10, 1, tzinfo=timezone.utc)
    kommentare = [
        {"user": {"login": "mensch"}, "body": "passt", "created_at": "2026-10-01T12:00:00Z"},
    ]
    assert capo.mensch_antwort(kommentare, seit) == "mensch"
    assert capo.davids_antwort is capo.mensch_antwort
    assert capo.davids_antwort(kommentare, seit) == "mensch"


def test_aufpasser_braucht_mensch_und_alias(tmp_path: Path) -> None:
    assert "braucht_mensch" in aufpasser.MELDUNG_EINMAL_PRO_TAG
    assert "braucht_mensch" in aufpasser.EREIGNIS_VORFALL
    assert callable(getattr(aufpasser.Aufpasser, "_braucht_mensch", None))
    assert callable(getattr(aufpasser.Aufpasser, "_braucht_david", None))

    repo = tmp_path / "repo"
    repo.mkdir()
    kommentare: list[list[str]] = []
    e = aufpasser.Einstellungen(zustand=tmp_path / "zustand", trocken=False, heim=tmp_path)
    wache = aufpasser.Aufpasser(e)
    wache.sh = lambda *args, **_kw: (kommentare.append(list(args)), "")[1]  # type: ignore[method-assign]

    # Alt-Eintrag im gespeicherten Stand sperrt doppelte Tagesmeldung auch unter neuem Schlüssel.
    alt_key = f"spec-900/bau 901|braucht_david|{wache.datum}"
    wache.stand.meldungen[alt_key] = wache.jetzt
    wache.melden("900", repo, "spec-900/bau 901", "braucht_mensch", "wartet")
    assert kommentare == []

    # Alt-Ereignis wird auf den neuen Regelnamen normalisiert.
    wache.melden("900", repo, "spec-900/wache 900", "braucht_david", "wartet")
    assert len(kommentare) == 1
    zeilen = [z for z in bau_log.lese(repo, "900") if z.get("typ") == "vorfall"]
    assert len(zeilen) == 1
    assert zeilen[0]["regel"] == "braucht_mensch"

    # Instanz-Override über den Alt-Namen greift auch bei internem Aufruf über _braucht_mensch.
    aufrufe: list[str] = []
    wache._braucht_david = lambda _e, _f, _r, _s, ev, _t: aufrufe.append(ev)  # type: ignore[method-assign]
    f = aufpasser.Fenster("spec-900", "900", "1", "bau 901", str(repo), 0, "@1", "%1")
    wache._braucht_mensch({}, f, repo, "spec-900/bau 901", "braucht_mensch", "text")
    assert aufrufe == ["braucht_mensch"]


def test_hausmeister_fuer_mensch_und_alias(tmp_path: Path) -> None:
    e_neu = hausmeister.Ergebnis(Path("/x/wt-1"), "bleibt", "Geheimnis", 1, fuer_mensch=True)
    e_alt = hausmeister.Ergebnis(Path("/x/wt-1"), "bleibt", "Geheimnis", 1, fuer_david=True)
    assert e_neu == e_alt
    assert e_neu.fuer_mensch is True and e_neu.fuer_david is True
    assert replace(e_neu, grund="neu").fuer_mensch is True
    assert hausmeister_aufgabe._gruppe(e_neu) == "Für den Menschen (selbst prüfen)"

    z_neu = hausmeister.ZweigErgebnis("origin", "ticket/x", "abc", None, "bleibt", "ohne", fuer_mensch=True)
    z_alt = hausmeister.ZweigErgebnis("origin", "ticket/x", "abc", None, "bleibt", "ohne", fuer_david=True)
    assert z_neu == z_alt
    assert z_neu.fuer_mensch is True and z_neu.fuer_david is True

    b_neu = hausmeister._Bleibt("grund", fuer_mensch=True)
    b_alt = hausmeister._Bleibt("grund", fuer_david=True)
    assert b_neu.fuer_mensch is True and b_neu.fuer_david is True
    assert b_alt.fuer_mensch is True and b_alt.fuer_david is True

    bw_neu = hausmeister_windows.Bleibt("grund", fuer_mensch=True)
    bw_alt = hausmeister_windows.Bleibt("grund", fuer_david=True)
    assert bw_neu.fuer_mensch is True and bw_neu.fuer_david is True
    assert bw_alt.fuer_mensch is True and bw_alt.fuer_david is True

    # Log-Lesen versteht sowohl neue als auch gespeicherte alte Zeilen (dedupliziert).
    log_datei = tmp_path / "hausmeister.jsonl"
    log_datei.write_text(
        "\n".join(
            [
                json.dumps({"ordner": "/a/alt", "fuer_david": True}),
                json.dumps({"ordner": "/a/neu", "fuer_mensch": True}),
                json.dumps({"ordner": "/a/alt", "fuer_mensch": True}),
                json.dumps({"ordner": "/a/frei", "fuer_mensch": False, "fuer_david": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert hausmeister.liste_fuer_david is hausmeister.liste_fuer_mensch
    assert hausmeister.liste_fuer_mensch(log_datei) == [Path("/a/alt"), Path("/a/neu")]


def test_leitstand_py_mensch_namen_und_alias() -> None:
    ls = _lade_leitstand()
    ts = datetime(2026, 9, 27, 18, 0).astimezone()
    ts_iso = ts.isoformat()

    aus_log = ls.aus_bau_log("377", [{"typ": "mensch_noetig", "ts": ts_iso}])
    assert len(aus_log) == 1 and aus_log[0].art == "mensch"

    issue = {
        "comments": {
            "nodes": [
                {"id": "c1", "createdAt": ts_iso, "body": "Aufseher: Mensch nötig — bitte prüfen."},
            ]
        }
    }
    kommentare = ls.aus_kommentaren("377", issue, "376")
    assert len(kommentare) == 1 and kommentare[0].art == "mensch"

    assert ls.ohne_doppelte_david is ls.ohne_doppelte_mensch
    assert ls.ohne_doppelte_mensch(aus_log, kommentare) == []
    alt_kommentar = ls.Ereignis("gh|alt", aus_log[0].zeit, "david", "x", "y", 377)
    assert ls.ohne_doppelte_mensch(aus_log, [alt_kommentar]) == []

    leer = ls.leerer_zustand()
    assert ls.ticket_stand(
        "1", "läuft", {}, [{"typ": "session_start", "ts": (ts - timedelta(minutes=5)).isoformat()}], [ts], leer
    ) == (
        "braucht_mensch",
        "Wartet auf den Menschen",
    )


def test_leitstand_pruefen_nutzt_art_mensch() -> None:
    text = (SKILL / "leitstand" / "pruefen.py").read_text(encoding="utf-8")
    assert '"art-mensch"' in text
    assert '"art-david"' not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_leitstand_seite_normalisiert_alt_und_neu(tmp_path: Path) -> None:
    seite = SKILL / "leitstand" / "seite.html"
    text = seite.read_text(encoding="utf-8")
    assert "--mensch:" in text and "--mensch-soft:" in text
    assert 'id="t-mensch"' in text and ".log-e.art-mensch" in text

    js = tmp_path / "probe.js"
    js.write_text(
        r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const start = src.indexOf("  const STUFEN =");
const normEnde = src.indexOf("  /* ---------- Phasen:");
const listeStart = src.indexOf("  function menschListe()");
const listeEnde = src.indexOf("  /* ---------- Naht-Filter");
const logStart = src.indexOf("  /* ---------- Aufseher-Log");
const logEnde = src.indexOf("  function renderMeldung");
const marker = { start, normEnde, listeStart, listeEnde, logStart, logEnde };
for (const k in marker) if (marker[k] < 0) { console.error("Marke fehlt: " + k); process.exit(2); }
const body = `
  const MIN = 60000;
  const zahl = (v, d) => (typeof v === "number" && Number.isFinite(v) ? v : d);
  const ts = (v) => { if (!v) return null; const n = Date.parse(v); return Number.isFinite(n) ? n : null; };
  ${src.slice(start, normEnde)}
  let tickets = [
    normTicket({ nr: 1, zustand: "braucht_mensch" }, "1"),
    normTicket({ nr: 2, zustand: "braucht_david" }, "2"),
  ];
  const now = Date.now();
  const iso = new Date(now - 60000).toISOString();
  let logEintraege = [
    { id: "a", zeit: iso, art: "mensch", ticket: 3, titel: "Neu" },
    { id: "b", zeit: iso, art: "david", ticket: 4, titel: "Alt" },
  ];
  ${src.slice(listeStart, listeEnde)}
  ${src.slice(logStart, logEnde)}
  return {
    zustaende: tickets.map((t) => t.zustand),
    arten: logEintraege.map((e) => logArt(e)),
    menschListe: menschListe(),
    offen: offeneMeldungen(now).map(({ e }) => e.id),
  };
`;
console.log(JSON.stringify(new Function(body)()));
""",
        encoding="utf-8",
    )
    erg = subprocess.run(["node", str(js), str(seite)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert erg.returncode == 0, erg.stderr
    daten = json.loads(erg.stdout.strip())
    assert daten["zustaende"] == ["braucht_mensch", "braucht_mensch"]
    assert daten["arten"] == ["mensch", "mensch"]
    assert daten["menschListe"] == [1, 2, 3, 4]
    assert daten["offen"] == ["a", "b"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_leitstand_seite_norm_ticket_allein_lauffaehig() -> None:
    """``normTicket`` bleibt ohne äußere Zusatz-Hilfe extrahierbar (wie in #279-Tests)."""
    seite = (SKILL / "leitstand" / "seite.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    anfang = seite.index("  function normTicket(")
    norm_ticket = seite[anfang : seite.index("\n  }\n", anfang) + 4]
    js = (
        "const zahl = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);\n"
        "const STUFEN = ['offen', 'fertig_gebaut', 'zu', 'verworfen'];\n"
        + norm_ticket
        + "\nconsole.log(JSON.stringify([\n"
        "  normTicket({ nr: 1, zustand: 'braucht_mensch' }, 1).zustand,\n"
        "  normTicket({ nr: 2, zustand: 'braucht_david' }, 2).zustand,\n"
        "  normTicket({ nr: 3, zustand: 'fertig' }, 3).zustand,\n"
        "  normTicket({ nr: 4, zustand: 'unbekannt' }, 4).zustand,\n"
        "]));\n"
    )
    erg = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True, encoding="utf-8", timeout=30)
    assert json.loads(erg.stdout.strip()) == ["braucht_mensch", "braucht_mensch", "fertig", "wartet"]
