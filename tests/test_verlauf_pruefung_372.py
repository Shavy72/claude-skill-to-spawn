"""Frage-Klasse aus dem Transkript statt aus dem Fenster-Text (Skill-Issue #372, PR 374 Runde 7).

Regel: Eine Fenster-Frage ist Klasse 2, wenn sie selbst ein Live-Wort trägt oder ein
Assistent-Eintrag der Session seit der zuletzt weitergegebenen Stelle eines trägt. Wer ins
Fenster getippt hat, spielt keine Rolle. Die Fälle stammen aus den Nachstellungen der
Review-Runden 4–6 (``probe_374_live_luecke``, ``probe_374_runde5``, ``probe_374_runde6``).

Gestellt sind nur tmux (Fenster-Text) und die Prozess-Suche (``aufpasser.session_finden``);
das Transkript ist eine echte JSONL unter ``$HOME/.claude/projects`` im tmp-Ordner, Pfad und
Lesen laufen echt; Leitstand und Briefkasten ebenso.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import (
    aufpasser,
    aufseher_stand,
    befugnis,
    briefkasten,
    leiter,
    leitstand,
    ticket_zustand,
    waechter_lauf,
)

SID = "11111111-2222-3333-4444-555555555555"
CWD = Path("/home/bau/wt/probe-9372")
SPEC = 9399
TICKET = 9372
ZIEL = "=spec-9399:4"
E = "─" * 40
FUSS = f"\n\n{E}\n❯ \n{E}\n  Kontext 80k"
LIVE_SATZ = "Ich deploye den Stand jetzt live auf die Hauptapp (safe_deploy_vps.sh)."
LIVE = f"● {LIVE_SATZ}\n\n"
STUPS_AUFPASSER = aufpasser.ANSTUPS_TEXT.format(min=12)
STUPS_AUFSEHER = leiter.MINDSET_TEXT.format(min=12)
HARMLOS = "Soll ich mit Ticket 13 weitermachen?"


# --- Transkript-Attrappe -------------------------------------------------------------------

_zaehler = iter(range(10**6))


def _a(*bloecke: dict[str, Any] | str, uuid: str | None = None, **extra: Any) -> dict[str, Any]:
    """Assistent-Eintrag; ein ``str``-Block ist ein Text-Block."""
    inhalt = [{"type": "text", "text": b} if isinstance(b, str) else b for b in bloecke]
    return {
        "type": "assistant",
        "uuid": uuid or f"a{next(_zaehler)}",
        "sessionId": SID,
        "message": {"role": "assistant", "content": inhalt},
        **extra,
    }


def _u(text: str, uuid: str | None = None) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid or f"u{next(_zaehler)}",
        "sessionId": SID,
        "message": {"role": "user", "content": text},
    }


def _bash(befehl: str) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "Bash",
        "input": {"command": befehl},
    }


#: Echte Klasse-2-Werkzeug-Handlung für Infrastruktur-Tests unter Variante E (#389).
_LIVE_AKTION = _bash("bash scripts/safe_deploy_vps.sh --skip-ci")


def _ergebnis(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": f"r{next(_zaehler)}",
        "sessionId": SID,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": text}],
        },
    }


class Welt:
    """Fenster ``fenster`` (sichtbar), Verlauf ``verlauf`` (``-S``), Session mit Transkript."""

    def __init__(self, heim: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.heim = heim
        self.fenster = ""
        self.verlauf: str | None = None
        self.session: aufpasser.SessionInfo | None = aufpasser.SessionInfo(SID, CWD, "idle", 4242)
        self.aufrufe: list[list[str]] = []
        monkeypatch.setenv("HOME", str(heim))
        monkeypatch.setattr(aufpasser, "session_finden", self._session_finden)

    def _session_finden(self, pane_pid: int, window_id: str, pane_id: str, heim: Path | None = None) -> Any:
        assert (pane_pid, window_id, pane_id) == (4242, "@7", "%9")
        return self.session

    def tmux(self, args: list[str]) -> str | None:
        self.aufrufe.append(args)
        if args[0] == "display-message":
            return "4242 @7 %9\n"
        if args[0] == "capture-pane":
            return (self.verlauf if self.verlauf is not None else self.fenster) if "-S" in args else self.fenster
        return None

    @property
    def quellen(self) -> aufseher_stand.Quellen:
        return aufseher_stand.Quellen(repo=self.heim, kinder=lambda n: [], tmux=self.tmux)

    def datei(self, sid: str = SID) -> Path:
        ordner = waechter_lauf.transkript_ordner(CWD, self.heim)  # Slug wie Claude Code, auch unter Windows
        ordner.mkdir(parents=True, exist_ok=True)
        return ordner / f"{sid}.jsonl"

    def schreibe(self, eintraege: list[dict[str, Any]], ende: str = "") -> None:
        zeilen = [json.dumps(e, ensure_ascii=False) for e in eintraege]
        self.datei().write_text("\n".join(zeilen) + "\n" + ende, encoding="utf-8")

    def anhaengen(self, eintraege: list[dict[str, Any]]) -> None:
        with self.datei().open("a", encoding="utf-8") as f:
            for e in eintraege:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def blick(self, stelle: str | None = None) -> tuple[str, str]:
        return aufseher_stand.fenster_blick(ZIEL, self.quellen, stelle)


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Welt:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)
    return Welt(tmp_path, monkeypatch)


def _k2(frage: str) -> bool:
    assert frage, "Frage erwartet"
    return befugnis.ist_klasse_2(frage)


# --- Nachstellungen: (Fenster, Transkript, uuid der Stelle oder None, Klasse 2?) ----------------

_TABELLE = "\n".join(f"  | Zeile {i} | ok |" for i in range(60))
_R6H = "\n".join(("❯ mach weiter\n\n" + LIVE + _TABELLE + "\n\n● Passt das so?" + FUSS).splitlines()[-50:])
_F3_BERICHT = "\n".join(f"Bericht Zeile {i} ohne Befund." for i in range(12))
_DIALOG_D = (
    "> los\n\n● Bash(bash scripts/safe_deploy_vps.sh --skip-ci)\n\n"
    " Bash command\n   bash scripts/safe_deploy_vps.sh --skip-ci\n Do you want to proceed?\n"
    " ❯ 1. Yes\n   2. No\n\n Esc to cancel"
)
_PASTE = "Bitte nimm diesen Plan:\n" + "\n".join(f"Schritt {i}: aufräumen" for i in range(12))

FAELLE: dict[str, tuple[str, list[dict[str, Any]], str | None, bool]] = {
    # Runde 4 (probe_374_live_luecke): E: reiner Assistent-Text ist keine Handlung (#389).
    "A text+werkzeug+frage": (
        "> mach weiter mit Ticket 12\n\n● Ich deploye den Stand gleich live auf die Hauptapp.\n\n"
        "● Bash(git status --short)\n  ⎿  (No content)\n\n● Passt das so?" + FUSS,
        [
            _u("mach weiter mit Ticket 12"),
            _a("Ich deploye den Stand gleich live auf die Hauptapp."),
            _a(_bash("git status --short")),
            _ergebnis("(No content)"),
            _a("Passt das so?"),
        ],
        None,
        False,  # E: Text ist keine Handlung (#389)
    ),
    "B werkzeug-live+text-frage": (
        "> los\n\n● Bash(bash scripts/safe_deploy_vps.sh --skip-ci)\n  ⎿  Deploy ok\n\n"
        "● Fertig. Soll ich weitermachen?" + FUSS,
        [
            _u("los"),
            _a(_bash("bash scripts/safe_deploy_vps.sh --skip-ci")),
            _ergebnis("Deploy ok"),
            _a("Fertig. Soll ich weitermachen?"),
        ],
        None,
        True,
    ),
    "C rueckfrage harmloser befehl nach live-text": (
        (
            "> los\n\n● Ich schalte jetzt live, erst noch ein Check.\n\n● Bash(git log -1)\n\n"
            "╭────────╮\n│ Bash command\n│   git log -1\n│ Do you want to proceed?\n"
            "│ ❯ 1. Yes\n│   2. No\n╰────────╯\n Esc to cancel"
        ),
        [
            _u("los"),
            _a("Ich schalte jetzt live, erst noch ein Check."),
            _a(_bash("git log -1")),
        ],
        None,
        False,  # E: Text ist keine Handlung (#389)
    ),
    "D rueckfrage live-befehl": (
        _DIALOG_D,
        [_u("los"), _a(_bash("bash scripts/safe_deploy_vps.sh --skip-ci"))],
        None,
        True,
    ),
    # Offener Punkt des Entwurfs: steht der Befehl beim offenen Dialog noch nicht im Transkript,
    # trägt ihn die Frage selbst (Dialog-Zeilen).
    "D dialog, tool_use noch nicht im transkript": (_DIALOG_D, [_u("los")], None, True),
    "F3 live weit oben, ein block": (
        "> los\n\n● Ich würde jetzt live deployen.\n" + _F3_BERICHT + "\n\nPasst das so?" + FUSS,
        [
            _u("los"),
            _a(f"Ich würde jetzt live deployen.\n{_F3_BERICHT}\n\nPasst das so?"),
        ],
        None,
        False,  # E: Text ist keine Handlung (#389)
    ),
    # Runde-3-Fehlalarm bleibt zu: die Stelle steht hinter der Live-Handlung.
    "F2 stelle hinter live-text": (
        "> deploy live\n\n● Live-Deploy ist durch.\n\n> jetzt Ticket 12\n\n● Tests grün.\n\n" + HARMLOS + FUSS,
        [
            _u("deploy live"),
            _a(_LIVE_AKTION, "Live-Deploy ist durch.", uuid="s1"),
            _u("jetzt Ticket 12"),
            _a(f"Tests grün.\n\n{HARMLOS}"),
        ],
        "s1",
        False,
    ),
    "F2b dito mit ❯-eingabezeile": (
        "❯ deploy live\n\n● Live-Deploy ist durch.\n\n❯ jetzt Ticket 12\n\n● Tests grün.\n\n" + HARMLOS + FUSS,
        [
            _u("deploy live"),
            _a(_LIVE_AKTION, "Live-Deploy ist durch.", uuid="s1"),
            _u("jetzt Ticket 12"),
            _a(f"Tests grün.\n\n{HARMLOS}"),
        ],
        "s1",
        False,
    ),
    # Bewusster Einmal-Fehlalarm: ohne gemerkte Stelle zählt das ganze Transkript.
    "F2 ohne stelle (einmal-fehlalarm)": (
        "> deploy live\n\n● Live-Deploy ist durch.\n\n> jetzt Ticket 12\n\n● Tests grün.\n\n" + HARMLOS + FUSS,
        [
            _u("deploy live"),
            _a(_LIVE_AKTION, "Live-Deploy ist durch."),
            _u("jetzt Ticket 12"),
            _a(f"Tests grün.\n\n{HARMLOS}"),
        ],
        None,
        True,
    ),
    # Runde 5 (probe_374_runde5): System- und Entscheider-Eingaben zwischen Live-Handlung und Frage.
    "2a1 live-text, anstups aufpasser, frage": (
        "❯ mach weiter\n\n" + LIVE + f"❯ {STUPS_AUFPASSER}\n\n● Passt das so?" + FUSS,
        [_u("mach weiter"), _a(_LIVE_AKTION), _u(STUPS_AUFPASSER), _a("Passt das so?")],
        None,
        True,
    ),
    "2a2 live-text, mindset-stoß aufseher, frage": (
        "❯ mach weiter\n\n" + LIVE + f"❯ {STUPS_AUFSEHER}\n\n● Soll ich weitermachen?" + FUSS,
        [
            _u("mach weiter"),
            _a(_LIVE_AKTION),
            _u(STUPS_AUFSEHER),
            _a("Soll ich weitermachen?"),
        ],
        None,
        True,
    ),
    "2a3 live-text, entscheider-antwort, frage": (
        "❯ mach weiter\n\n" + LIVE + "● Soll ich vorher noch die Tests laufen lassen?\n\n"
        "❯ Ja, erst Tests, dann weiter.\n\n● Tests grün. Passt das so?" + FUSS,
        [
            _u("mach weiter"),
            _a(_LIVE_AKTION),
            _a("Soll ich vorher noch die Tests laufen lassen?"),
            _u("Ja, erst Tests, dann weiter."),
            _a("Tests grün. Passt das so?"),
        ],
        None,
        True,
    ),
    "2b auswahl ohne nummern ganz links": (
        "❯ los\n\n" + LIVE + "● Wie weiter?\n❯ Weiter\n  Abbrechen\n\nWelche Option?" + FUSS,
        [_u("los"), _a(_LIVE_AKTION), _a("Wie weiter?\n\nWelche Option?")],
        None,
        True,
    ),
    "2c1 slash-befehl dann live": (
        "❯ /loop 3m weiter\n\n" + LIVE + "● Passt das so?" + FUSS,
        [_u("/loop 3m weiter"), _a(_LIVE_AKTION), _a("Passt das so?")],
        None,
        True,
    ),
    "2c2 mehrzeilige eingabe dann live": (
        "❯ erste Zeile\n  zweite Zeile\n\n" + LIVE + "● Passt das so?" + FUSS,
        [_u("erste Zeile\nzweite Zeile"), _a(_LIVE_AKTION), _a("Passt das so?")],
        None,
        True,
    ),
    "3 live vor menschen-eingabe, stelle hinter weitergegebener frage": (
        LIVE + "● Passt das so?\n\n❯ ja, aber nur Staging\n\n● Erledigt. " + HARMLOS + FUSS,
        [
            _a(_LIVE_AKTION),
            _a("Passt das so?", uuid="s1"),
            _u("ja, aber nur Staging"),
            _a(f"Erledigt. {HARMLOS}"),
        ],
        "s1",
        False,
    ),
    # Runde 6 (probe_374_runde6).
    "R6a1 live, freier aufseher-text, harmlose frage": (
        "❯ mach weiter\n\n" + LIVE + "❯ Weiter mit Ticket 13, Tests zuerst.\n\n● Passt das so?" + FUSS,
        [
            _u("mach weiter"),
            _a(_LIVE_AKTION),
            _u("Weiter mit Ticket 13, Tests zuerst."),
            _a("Passt das so?"),
        ],
        None,
        True,
    ),
    "R6a2 start-prompt, dann live, frage": (
        "❯ Übernimm Ticket 12 laut Handoff docs/handoffs/HANDOFF_x.md\n\n" + LIVE + "● Passt das so?" + FUSS,
        [
            _u("Übernimm Ticket 12 laut Handoff docs/handoffs/HANDOFF_x.md"),
            _a(_LIVE_AKTION),
            _a("Passt das so?"),
        ],
        None,
        True,
    ),
    "R6b1 echo, eingerückte live-zeile ohne leerzeile, frage": (
        "❯ mach weiter\n\n"
        f"❯ {STUPS_AUFSEHER[:70]}\n  {STUPS_AUFSEHER[70:140]}\n  ⎿  Ich deploye jetzt live auf die Hauptapp.\n\n"
        "● Passt das so?" + FUSS,
        [
            _u("mach weiter"),
            _u(STUPS_AUFSEHER),
            _a(_LIVE_AKTION),
            _a("Passt das so?"),
        ],
        None,
        True,
    ),
    "R6b2 echo, eingerückte frage direkt darunter": (
        "❯ los\n\n" + LIVE + f"❯ {STUPS_AUFSEHER[:70]}\n  Soll ich live deployen?" + FUSS,
        [_u("los"), _a(_LIVE_AKTION), _u(STUPS_AUFSEHER), _a("Soll ich live deployen?")],
        None,
        True,
    ),
    "R6c1 mensch tippt präfix eines system-texts, live davor": (
        LIVE + "❯ Aufseher: Du stehst\n\n● Ok. " + HARMLOS + FUSS,
        [_a(_LIVE_AKTION), _u("Aufseher: Du stehst"), _a(f"Ok. {HARMLOS}")],
        None,
        True,
    ),
    "R6g1 [pasted text], live davor, stelle hinter live-text": (
        LIVE + "❯ [Pasted text #1 +12 lines]\n\n● Erledigt. " + HARMLOS + FUSS,
        [_a(_LIVE_AKTION, uuid="s1"), _u(_PASTE), _a(f"Erledigt. {HARMLOS}")],
        "s1",
        False,
    ),
    "R6g2 live-wort nur in umbrochener eingabe": (
        "❯ bitte erst alles auf Staging prüfen und auf keinen Fall\n  live deployen, danke\n\n"
        "● Erledigt. " + HARMLOS + FUSS,
        [
            _u("bitte erst alles auf Staging prüfen und auf keinen Fall live deployen, danke"),
            _a(f"Erledigt. {HARMLOS}"),
        ],
        None,
        False,
    ),
    "R6g3 /export, live davor, stelle hinter live-text": (
        LIVE + "❯ /export\n\n● Exportiert. " + HARMLOS + FUSS,
        [_a(_LIVE_AKTION, uuid="s1"), _u("/export"), _a(f"Exportiert. {HARMLOS}")],
        "s1",
        False,
    ),
    "R6h live über den sichtbaren bereich hinaus gescrollt": (
        _R6H,
        [_u("mach weiter"), _a(_LIVE_AKTION), _a(_TABELLE + "\n\nPasst das so?")],
        None,
        True,
    ),
    # Nicht durchsucht: tool_result, thinking; Neben-Agenten werden mitgelesen.
    "live nur im tool_result": (
        "> los\n\n● Bash(cat log.txt)\n  ⎿  Deploy live ok\n\n● Fertig. Soll ich weitermachen?" + FUSS,
        [
            _u("los"),
            _a(_bash("cat log.txt")),
            _ergebnis("Deploy live ok"),
            _a("Fertig. Soll ich weitermachen?"),
        ],
        None,
        False,
    ),
    "live nur im thinking": (
        "> los\n\n● Fertig. Soll ich weitermachen?" + FUSS,
        [
            _u("los"),
            _a(
                {"type": "thinking", "thinking": "Ich könnte live deployen."},
                "Fertig. Soll ich weitermachen?",
            ),
        ],
        None,
        False,
    ),
    "live nur im isSidechain-eintrag": (
        "> los\n\n● Fertig. Soll ich weitermachen?" + FUSS,
        [
            _u("los"),
            _a(_LIVE_AKTION, isSidechain=True),
            _a("Fertig. Soll ich weitermachen?"),
        ],
        None,
        True,  # Runde 8: Unter-Agenten werden mitgelesen (vorher False, Durchrutscher T9c)
    ),
}


@pytest.mark.parametrize("name", list(FAELLE))
def test_nachstellung(welt: Welt, name: str) -> None:
    fenster, eintraege, stelle, soll_k2 = FAELLE[name]
    welt.fenster = fenster
    welt.schreibe(eintraege)
    frage, bis = welt.blick(f"{SID}:{stelle}" if stelle else None)
    assert _k2(frage) is soll_k2, frage
    eigene = [e for e in eintraege if e["type"] == "assistant" and not e.get("isSidechain")]
    assert bis == f"{SID}:{eigene[-1]['uuid'] if eigene else ''}"


def test_r6b2_frage_direkt_unter_echo_ist_nicht_leer(welt: Welt) -> None:
    fenster, eintraege, _, _ = FAELLE["R6b2 echo, eingerückte frage direkt darunter"]
    welt.fenster = fenster
    welt.schreibe(eintraege)
    assert welt.blick()[0].endswith("Soll ich live deployen?")


def test_grund_steht_vor_der_frage_und_ueberlebt_das_kuerzen(welt: Welt) -> None:
    welt.fenster = "❯ mach weiter\n\n" + LIVE + "● Passt das so?" + FUSS
    welt.schreibe([_u("mach weiter"), _a(_LIVE_AKTION), _a("x " * 900 + "Passt das so?")])
    frage, _ = welt.blick()
    kurz = aufseher_stand.frage_kurz(frage)
    assert befugnis.ist_klasse_2(kurz) and kurz.endswith("Passt das so?")
    assert "safe_deploy_vps.sh" in frage


def test_stelle_fremder_session_prueft_ab_anfang(welt: Welt) -> None:
    welt.fenster = "❯ los\n\n" + LIVE + "● Passt das so?" + FUSS
    welt.schreibe([_u("los"), _a(_LIVE_AKTION, uuid="s1"), _a("Passt das so?")])
    assert not _k2(welt.blick(f"{SID}:s1")[0])  # gleiche Session: Stelle gilt
    assert _k2(welt.blick("alte-session-id:s1")[0])  # Respawn/Compact: ab Dateianfang
    assert _k2(welt.blick(f"{SID}:unbekannt")[0])  # uuid nicht zu finden: ab Dateianfang


# --- Rückfall ----------------------------------------------------------------------------


def test_rueckfall_ohne_transkript_prueft_ganzen_verlauf(welt: Welt, caplog: pytest.LogCaptureFixture) -> None:
    welt.session = None
    welt.fenster = "● Tests grün.\n\n● " + HARMLOS + FUSS
    welt.verlauf = LIVE + "\n".join(f"Zeile {i} ok" for i in range(1500)) + "\n" + welt.fenster
    with caplog.at_level(logging.WARNING):
        frage, bis = welt.blick(f"{SID}:s1")
    assert _k2(frage) and bis == "fenster"
    assert any("-S" in a and "-J" in a and "-2000" in a for a in welt.aufrufe)
    assert any("Transkript" in r.getMessage() for r in caplog.records)


def test_rueckfall_datei_fehlt(welt: Welt) -> None:
    welt.fenster = "● " + HARMLOS + FUSS
    welt.verlauf = LIVE + welt.fenster
    assert _k2(welt.blick()[0])


def test_rueckfall_verlauf_ohne_live_wort_bleibt_klasse_3(welt: Welt) -> None:
    welt.session = None
    welt.fenster = "● Tests grün.\n\n● " + HARMLOS + FUSS
    assert not _k2(welt.blick()[0])


def test_rueckfall_auch_verlauf_unlesbar_geht_zum_menschen(welt: Welt) -> None:
    welt.session = None
    welt.fenster = "● " + HARMLOS + FUSS
    original = welt.tmux
    welt.tmux = lambda args: None if "-S" in args else original(args)  # type: ignore[method-assign]
    assert _k2(welt.blick()[0])


def test_halb_geschriebene_letzte_zeile_crasht_nicht_und_geht_zum_menschen(
    welt: Welt,
) -> None:
    welt.fenster = "❯ los\n\n" + LIVE + "● Passt das so?" + FUSS
    halb = json.dumps(_a(LIVE_SATZ), ensure_ascii=False)[:60]
    welt.schreibe([_u("los"), _a("Ich prüfe kurz.")], ende=halb)
    frage, bis = welt.blick()
    assert _k2(frage) and bis == "fenster"


def test_kaputte_zeile_mitten_drin_wird_uebersprungen(welt: Welt) -> None:
    welt.fenster = "❯ los\n\n" + LIVE + "● Passt das so?" + FUSS
    welt.schreibe([_u("los")])
    with welt.datei().open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    welt.anhaengen([_a(LIVE_SATZ), _a("Passt das so?", uuid="z")])
    frage, bis = welt.blick()
    assert _k2(frage) and bis == f"{SID}:z"


# --- Leiter: die Stelle rückt nur nach geglückter Weitergabe weiter ----------------------------


class Umwelt:
    """Leiter-Umwelt; die Frage kommt echt über :func:`leiter.fenster_frage` aus der Welt."""

    def __init__(self, welt: Welt) -> None:
        self.welt = welt
        self.uhr = time.time()
        self.einwurf_kaputt = False

    def jetzt(self) -> float:
        return self.uhr

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        frage, bis = leiter.fenster_frage(ticket, ZIEL, self.welt.quellen)
        stufe: ticket_zustand.Stufe = "offen"
        return leiter.Lage(
            stufe=stufe,
            fenster=aufseher_stand.STILL,
            still_min=25,
            kontext_k=None,
            prompt_datei=False,
            ziel=ZIEL,
            frage=frage,
            frage_bis=bis,
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.welt.heim

    def werkzeug(self) -> Any:
        return self

    def tippen(self, ziel: str, text: str) -> None:
        raise AssertionError("bei einer Frage wird nie getippt")

    def frage_einwerfen(self, spec: int, ticket: int, frage: str) -> str:
        if self.einwurf_kaputt:
            raise OSError("Briefkasten nicht beschreibbar")
        return leiter.frage_einwerfen(self.welt.heim, spec, ticket, frage)


def _grenze() -> str | None:
    return leitstand.notiz(f"leiter-grenze-{TICKET}")


def test_weitergabe_rueckt_stelle_weiter_spaetere_harmlose_frage_ist_klasse_3(
    welt: Welt,
) -> None:
    welt.fenster = "❯ mach weiter\n\n" + LIVE + "● Passt das so?" + FUSS
    welt.schreibe([_u("mach weiter"), _a(_LIVE_AKTION), _a("Passt das so?", uuid="f1")])
    u = Umwelt(welt)
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert "braucht den Menschen" in erg.zeile
    assert _grenze() == f"{SID}:f1"
    # Mensch antwortet, die Session arbeitet weiter und stellt eine harmlose Frage.
    welt.anhaengen([_u("ja, aber nur Staging"), _a(f"Erledigt. {HARMLOS}", uuid="f2")])
    welt.fenster = "❯ ja, aber nur Staging\n\n● Erledigt. " + HARMLOS + FUSS
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert "Entscheider" in erg.zeile, erg.zeile
    assert _grenze() == f"{SID}:f2"
    klassen = sorted(f["klasse"] for f in briefkasten.offene([TICKET], hauptbaum=welt.heim))
    assert klassen == ["2", "3"]


def test_einwerfen_scheitert_stelle_bleibt_zweiter_takt_sieht_live_noch(
    welt: Welt,
) -> None:
    welt.fenster = "❯ mach weiter\n\n" + LIVE + "● Passt das so?" + FUSS
    welt.schreibe([_u("mach weiter"), _a(_LIVE_AKTION), _a("Passt das so?", uuid="f1")])
    u = Umwelt(welt)
    u.einwurf_kaputt = True
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert erg.exit == leiter.EXIT_FEHLER
    assert _grenze() is None
    u.einwurf_kaputt = False
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert "braucht den Menschen" in erg.zeile
    assert [f["klasse"] for f in briefkasten.offene([TICKET], hauptbaum=welt.heim)] == ["2"]


# --- Runde 8: Ablösung, Unter-Agenten, kaputte Zeile, Reichweite und Wortgrenzen --------------

ALT = "99999999-2222-3333-4444-555555555555"
FRAGE_FENSTER = "❯ los\n\n● Passt das so?" + FUSS


def _zeit(sekunde: int) -> str:
    return f"2026-11-23T10:00:{sekunde:02d}.000Z"


def _unter_agent(welt: Welt, eintraege: list[dict[str, Any]], sid: str = SID) -> None:
    ordner = welt.datei(sid).parent / sid / "subagents"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "agent-a1.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eintraege),
        encoding="utf-8",
    )


def _harmlose_neue_session(welt: Welt) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("Handoff lesen und weiter"), _a("Passt das so?", uuid="n1")])


def test_r8_abloesung_liest_rest_der_alten_datei_ab_der_stelle(welt: Welt) -> None:
    welt.datei(ALT).write_text(
        "".join(
            json.dumps(e, ensure_ascii=False) + "\n" for e in [_u("los"), _a("Frage?", uuid="alt1"), _a(_LIVE_AKTION)]
        ),
        encoding="utf-8",
    )
    _harmlose_neue_session(welt)
    frage, bis = welt.blick(f"{ALT}:alt1")
    assert _k2(frage) and bis == f"{SID}:n1"


def test_r8_abloesung_live_vor_der_alten_stelle_bleibt_klasse_3(welt: Welt) -> None:
    welt.datei(ALT).write_text(
        "".join(
            json.dumps(e, ensure_ascii=False) + "\n"
            for e in [_a(_LIVE_AKTION), _a("Frage?", uuid="alt1"), _a("Tests grün.")]
        ),
        encoding="utf-8",
    )
    _harmlose_neue_session(welt)
    assert not _k2(welt.blick(f"{ALT}:alt1")[0])


def test_r8_abloesung_alte_datei_fehlt_geht_zum_menschen(welt: Welt) -> None:
    _harmlose_neue_session(welt)
    assert _k2(welt.blick(f"{ALT}:alt1")[0])


def test_r8_unter_agent_nach_der_stelle_zaehlt(welt: Welt) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), _a("Frage?", uuid="s1", timestamp=_zeit(10)), _a("Passt das so?")])
    _unter_agent(
        welt,
        [_a(_bash("ssh clawy-vps 'docker restart app'"), isSidechain=True, timestamp=_zeit(20))],
    )
    assert _k2(welt.blick(f"{SID}:s1")[0])


def test_r8_unter_agent_vor_der_stelle_zaehlt_nicht(welt: Welt) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), _a("Frage?", uuid="s1", timestamp=_zeit(30)), _a("Passt das so?")])
    _unter_agent(
        welt,
        [_a(_bash("ssh clawy-vps 'docker restart app'"), isSidechain=True, timestamp=_zeit(20))],
    )
    assert not _k2(welt.blick(f"{SID}:s1")[0])


def test_r8_unter_agent_der_alten_session_zaehlt_nach_abloesung(welt: Welt) -> None:
    welt.datei(ALT).write_text(
        json.dumps(_a("Frage?", uuid="alt1", timestamp=_zeit(10))) + "\n",
        encoding="utf-8",
    )
    _unter_agent(welt, [_a(_LIVE_AKTION, isSidechain=True, timestamp=_zeit(20))], sid=ALT)
    _harmlose_neue_session(welt)
    assert _k2(welt.blick(f"{ALT}:alt1")[0])


def test_r8_kaputte_zeile_mitten_nach_der_stelle_geht_zum_menschen(welt: Welt) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), _a("Frage?", uuid="s1")])
    with welt.datei().open("a", encoding="utf-8") as f:
        f.write(json.dumps(_a(LIVE_SATZ), ensure_ascii=False)[:-25] + "\n")
    welt.anhaengen([_a("Ich teste."), _a("Passt das so?", uuid="z")])
    frage, bis = welt.blick(f"{SID}:s1")
    assert _k2(frage) and bis == f"{SID}:z"


def test_r8_kaputte_zeile_vor_der_stelle_bleibt_klasse_3(welt: Welt) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los")])
    with welt.datei().open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    welt.anhaengen([_a("Frage?", uuid="s1"), _a("Passt das so?")])
    assert not _k2(welt.blick(f"{SID}:s1")[0])


@pytest.mark.parametrize(
    "eintrag",
    [
        _a("Ich deliver die Änderung jetzt."),
        _a("Fehler reproduziert, Rot-Beweis steht."),
        _a(
            {
                "type": "tool_use",
                "name": "Edit",
                "input": {
                    "file_path": "/wt/scripts/safe_deploy_vps.sh",
                    "old_string": "live",
                    "new_string": "vps live",
                },
            }
        ),
        _a(
            {
                "type": "tool_use",
                "name": "Write",
                "input": {"file_path": "/wt/deploy_vps.py", "content": "LIVE = True"},
            }
        ),
    ],
    ids=["deliver", "reproduziert", "edit-inhalt", "write-inhalt"],
)
def test_r8_kein_fehlalarm_durch_wortteil_oder_datei_inhalt(welt: Welt, eintrag: dict[str, Any]) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), eintrag, _a("Passt das so?")])
    assert not _k2(welt.blick()[0])


@pytest.mark.parametrize(
    "eintrag",
    [
        _a(_bash("bash scripts/safe_deploy_vps.sh --skip-ci")),
        _a(_LIVE_AKTION, isSidechain=True),
    ],
    # Variante E (#389): Werkzeug-Handlungen (auch im Unter-Agenten) zählen; reiner Text ist keine Handlung.
    ids=["bash-befehl", "sidechain"],
)
def test_r8_live_wort_am_wortanfang_und_im_befehl_zaehlt(welt: Welt, eintrag: dict[str, Any]) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), eintrag, _a("Passt das so?")])
    assert _k2(welt.blick()[0])


def test_r8_ganzwort_suche_ist_eigene_funktion_andere_nutzer_bleiben_streng() -> None:
    # Frage und andere Nutzer (Briefkasten, Leiter, Rückfall) bleiben bei der strengen breiten Liste.
    assert hasattr(befugnis, "ist_klasse_2_am_wortanfang")
    assert befugnis.ist_klasse_2("goLive") and befugnis.ist_klasse_2("Kundenkonto")
    assert befugnis.ist_klasse_2("deliver")


@pytest.mark.parametrize(
    "eintrag",
    [
        _a("Die Produktion läuft wieder."),
        _a("Das Passwort liegt in Bitwarden."),
        _a("Jetzt der Live-Deploy."),
    ],
    ids=["wortanfang-stamm", "umlaut-satz", "bindestrich"],
)
def test_r9_zustand_mit_klasse_2_wort_ist_klasse_2(welt: Welt, eintrag: dict[str, Any]) -> None:
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), eintrag, _a("Passt das so?")])
    # E (#389): reiner Assistent-Text ist keine Handlung → Klasse 3.
    assert not _k2(welt.blick()[0])


@pytest.mark.parametrize(
    "eintrag, soll_2",
    [
        (_a(_bash("gh pr merge 374")), True),
        (_a(_bash("rm -rf /opt/app")), True),
        (_a(_bash('bash -c "ssh clawy-vps uptime"')), True),
        (_a("Das goLive mache ich jetzt"), False),  # E: Text ist keine Handlung
        (_a("Ich lege ein Kundenkonto an"), False),  # E: Text ist keine Handlung
        (
            _a(
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "/opt/app/.env", "content": "A=1"},
                }
            ),
            True,
        ),
        (_a({"type": "tool_use", "name": "mcp__x__purchase_phone", "input": {}}), True),
        (
            _a(
                {
                    "type": "tool_use",
                    "name": "Task",
                    "input": {"description": "x", "prompt": "Deploy auf vps prüfen"},
                }
            ),
            False,  # E: Task-Prompt ist Text; Unter-Agent-Handlungen prüft dessen Transkript
        ),
    ],
    ids=["merge", "rm", "bash-c-ssh", "golive", "kundenkonto", "env", "mcp", "task"],
)
def test_r9_handlung_im_verlauf_ist_klasse_2(welt: Welt, eintrag: dict[str, Any], soll_2: bool) -> None:
    """Unbekannte Werkzeug-Handlung → Klasse 2 (mit HANDLUNG_KOPF); Text/Task-Prompt → Klasse 3 (#389)."""
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), eintrag, _a("Passt das so?")])
    assert _k2(welt.blick()[0]) is soll_2


@pytest.mark.parametrize(
    "eintrag",
    [
        _a(_bash('git commit -m "fix live"')),
        _a(_bash("git commit -F - <<'EOF'\nfix: vps\nEOF")),
        _a(_bash("pytest tests/test_deploy_x.py")),
        _a(_bash("git push -f origin HEAD:refs/heads/wip/12-x")),
        _a(_bash("bash scripts/staging/staging_deploy.sh")),
        _a(
            {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/wt/live/x.py"},
            }
        ),
    ],
    ids=["commit", "heredoc", "pytest", "wip-force", "staging", "read-pfad"],
)
def test_r9_ohne_handlung_klasse_2_nur_mit_klasse_2_wort(
    welt: Welt, eintrag: dict[str, Any], request: pytest.FixtureRequest
) -> None:
    """Variante E (#389): erlaubte Werkzeuge/Befehle → 3; fremdes Skript / Push mit -f und : → 2."""
    welt.fenster = FRAGE_FENSTER
    welt.schreibe([_u("los"), eintrag, _a("Passt das so?")])
    soll_2 = request.node.callspec.id in {"wip-force", "staging"}
    assert _k2(welt.blick()[0]) is soll_2


@pytest.mark.parametrize(
    "wort",
    [
        "goLive",
        "neuerVps",
        "deployProd",
        "Kundenkonto",
        "Bankkonto",
        "Mehrkosten",
        "Monatsrechnung",
    ],
)
def test_r9_frage_mit_wortteil_ist_klasse_2(welt: Welt, wort: str) -> None:
    welt.fenster = f"❯ los\n\n● Soll ich {wort} angehen?" + FUSS
    welt.schreibe([_u("los"), _a(f"Soll ich {wort} angehen?")])
    assert _k2(welt.blick()[0])


def test_r9_rueckfall_prueft_breit_kundenkonto(welt: Welt) -> None:
    welt.session = None
    welt.fenster = "● " + HARMLOS + FUSS
    welt.verlauf = "● Ich prüfe das Kundenkonto.\n\n" + welt.fenster
    assert _k2(welt.blick()[0])


# --- PR 374 Runde 10 -------------------------------------------------------------------------


def test_r10_t7d_ohne_stelle_prueft_die_vorgaengerin_mit(welt: Welt) -> None:
    """T7d: Vorgängerin hat nie gefragt (keine Stelle), Live-Arbeit nur in ihrer Datei."""
    welt.datei(ALT).write_text(json.dumps(_a(_LIVE_AKTION, uuid="alt1")) + "\n", encoding="utf-8")
    _harmlose_neue_session(welt)
    assert _k2(welt.blick(None)[0])


def test_r10_t7d_ohne_stelle_harmlose_vorgaengerin_bleibt_klasse_3(welt: Welt) -> None:
    welt.datei(ALT).write_text(json.dumps(_a("Tests grün.", uuid="alt1")) + "\n", encoding="utf-8")
    _harmlose_neue_session(welt)
    assert not _k2(welt.blick(None)[0])


def test_r10_read_pfad_mit_env_zaehlt(welt: Welt) -> None:
    """Regel „Read-Pfad“: ohne sie rutscht das Lesen der .env durch."""
    welt.fenster = FRAGE_FENSTER
    lesen = {
        "type": "tool_use",
        "id": "t",
        "name": "Read",
        "input": {"file_path": "/opt/app/.env"},
    }
    welt.schreibe([_u("los"), _a(lesen), _a("Passt das so?")])
    assert _k2(welt.blick()[0])
