"""Halt einer Spec (#1444): Datei mit Grund und maschinell prüfbarer Aufhebe-Bedingung.

Ein Halt hält die ganze Spec eines Klons an: der Aufseher-Takt läuft nicht weiter,
der Aufpasser startet keine Fenster. Er liegt als ``<klon>/.to-spawn/halt.json``
und trägt immer eine Bedingung, unter der er sich selbst aufhebt — ein Halt ohne
Ablauf ist nicht anlegbar (19.11.: #1307 stand 10 h, weil niemand den Halt aufhob).

Schnittstelle (Format und Prüfung leben nur hier):

- :func:`anlegen` — Grund + :class:`Bedingung` Pflicht, sonst ``ValueError``.
- :func:`anlegen` lehnt Bedingungen ab, deren Ticket/Commit es nicht gibt; ist
  GitHub/origin nicht erreichbar, legt es trotzdem an (die Frist unten fängt Tippfehler).
- :func:`pruefen` — aktiver :class:`Halt` oder ``None``; ist die Bedingung erfüllt,
  hebt es den Halt selbst auf und schreibt das ins Halt-Log. Im Zweifel hält der Halt:
  gh/git nicht erreichbar ⇒ Halt bleibt ohne Frist, eine Warnung je Halt (#1465). Nur
  ein Halt ohne gültige Bedingung oder mit Ticket/Commit, das es nachweislich nicht gibt,
  hebt sich nach ``ALT_FRIST`` am Stück „nicht prüfbar“ mit Warnung auf; die Datei
  wird dabei nur umbenannt (``halt.json.aufgehoben-<zeit>``), nie gelöscht. Scheitert
  das Entfernen, gilt der Halt weiter. ``aufheben=False`` (Trockenlauf) ändert keine
  Datei; ``wer`` steht im Log. Wirft nie: unerwarteter Fehler ⇒ ``log.exception``
  (einmal je Halt), Halt bleibt (bis zur Frist; dann Umbenennen wie oben).
- :func:`aufheben` — von Hand (``wer`` steht im Log).

Bedingungen: ``ticket_zu`` (Issue N geschlossen), ``commit`` (Commit X auf dem
Hauptzweig von origin), ``bis`` (Zeitpunkt erreicht). Eine Datei ohne gültige
Bedingung (alter Halt von Hand, unbekannte Art, kaputtes JSON, unlesbar) gilt als
„nicht prüfbar“: Frist 24 h ab der ersten Beobachtung durch eine Prüfung, nie ab ``seit``.

Befehle: ``python -m to_spawn.halt anlegen --grund … (--ticket-zu N | --commit SHA
| --bis ISO)`` · ``pruefen`` · ``aufheben --wer …`` (je ``--repo-dir``, Vorgabe cwd).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import gh

log = logging.getLogger("to_spawn.halt")

DATEI = Path(".to-spawn") / "halt.json"
LOG_DATEI = Path(".to-spawn") / "halt_log.jsonl"
#: Erster Fehlschlag der Prüfung (eigene Datei: ein Takt schreibt nie ``halt.json``
#: zurück, das hebt keinen gerade von Hand aufgehobenen Halt wieder auf).
MERKER_DATEI = Path(".to-spawn") / "halt_nicht_pruefbar.json"
ARTEN = ("ticket_zu", "commit", "bis")
#: Ablauf eines Halts ohne gültige Bedingung (Altbestand, kaputt/unlesbar) und
#: Höchstdauer am Stück „nicht prüfbar“.
ALT_FRIST = timedelta(hours=24)
#: Merker-Schlüssel, wenn weder ``seit`` noch Änderungsdatum lesbar sind (auch Fehler-Zweig
#: von ``pruefen``); ``anlegen`` löscht jeden alten Merker, damit keiner einen neuen Halt trifft.
SEIT_UNBEKANNT = "unbekannt"
GIT_TIMEOUT_S = 60
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _jetzt() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _zeit(text: str) -> datetime:
    """ISO-Zeitpunkt; ohne Zeitzone = Ortszeit."""
    wert = datetime.fromisoformat(text)
    return wert if wert.tzinfo else wert.astimezone()


@dataclass(frozen=True)
class Bedingung:
    """Aufhebe-Bedingung; ungültige Werte scheitern schon beim Bauen."""

    art: str
    wert: str

    def __post_init__(self) -> None:
        if self.art == "ticket_zu":
            if not self.wert.isdigit() or int(self.wert) <= 0:
                raise ValueError(f"ticket_zu braucht eine Ticketnummer, nicht {self.wert!r}")
        elif self.art == "commit":
            if not _SHA.match(self.wert):
                raise ValueError(f"commit braucht einen SHA (7–40 Hex-Zeichen), nicht {self.wert!r}")
        elif self.art == "bis":
            _zeit(self.wert)  # ValueError bei unlesbarem Zeitpunkt
        else:
            raise ValueError(f"unbekannte Bedingung {self.art!r} — erlaubt: {', '.join(ARTEN)}")

    def text(self) -> str:
        if self.art == "ticket_zu":
            return f"bis Ticket #{self.wert} geschlossen ist"
        if self.art == "commit":
            return f"bis Commit {self.wert[:7]} auf dem Hauptzweig ist"
        return f"bis {self.wert}"


@dataclass(frozen=True)
class Halt:
    grund: str
    seit: str
    wer: str
    bedingung: Bedingung | None  # None = Datei ohne gültige Bedingung ⇒ nicht prüfbar

    def text(self) -> str:
        return f"Halt seit {self.seit} ({self.wer}): {self.grund} — {_bedingung_text(self.bedingung)}"


def _bedingung_text(b: Bedingung | None) -> str:
    return b.text() if b is not None else f"ohne gültige Bedingung (fällt {ALT_FRIST} nach erster Prüfung)"


def _schreibe_log(klon: Path, ereignis: str, halt: Halt, **felder: Any) -> None:
    zeile = {
        "ts": _jetzt().isoformat(timespec="seconds"),
        "ereignis": ereignis,
        **asdict(halt),
        **felder,
    }
    datei = klon / LOG_DATEI
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def anlegen(klon: Path, grund: str, bedingung: Bedingung, wer: str = "") -> Halt:
    """Halt anlegen. Ohne Grund/Bedingung ⇒ ``ValueError``; liegt schon einer ⇒ ``FileExistsError``."""
    if not isinstance(bedingung, Bedingung):
        raise ValueError("Halt ohne Aufhebe-Bedingung ist nicht anlegbar")
    if not grund.strip():
        raise ValueError("Halt ohne Grund ist nicht anlegbar")
    datei = klon / DATEI
    if datei.exists():
        raise FileExistsError(f"{datei} liegt schon — erst aufheben")
    bekannt = _bedingung_bekannt(klon, bedingung)
    if bekannt is False:
        was = "Ticket #" if bedingung.art == "ticket_zu" else "Commit "
        raise ValueError(f"{was}{bedingung.wert} gibt es nicht — Halt nicht angelegt")
    if bekannt is None:
        log.warning(
            "%s nicht überprüfbar (GitHub/origin nicht erreichbar) — Halt trotzdem angelegt",
            bedingung.text(),
        )
    halt = Halt(
        grund=grund.strip(),
        seit=_jetzt().isoformat(timespec="seconds"),
        wer=wer or os.environ.get("USER", "?"),
        bedingung=bedingung,
    )
    datei.parent.mkdir(parents=True, exist_ok=True)
    _merker_setzen(klon, halt, None)  # Rest-Merker eines früheren Halts zählt nie mit
    zwischen = datei.with_suffix(".json.tmp")
    zwischen.write_text(json.dumps(asdict(halt), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(zwischen, datei)
    _schreibe_log(klon, "angelegt", halt)
    log.info("%s", halt.text())
    return halt


def _bedingung_bekannt(klon: Path, b: Bedingung) -> bool | None:
    """Gibt es Ticket/Commit? ``None`` = nicht entscheidbar (Netz) — nie fälschlich ``False``."""
    if b.art == "ticket_zu":
        return gh.ticket_existiert(b.wert, cwd=klon)
    if b.art == "commit":
        try:
            abruf = subprocess.run(
                ["git", "fetch", "-q", "origin"],
                cwd=klon,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_S,
            ).returncode
            da = subprocess.run(
                ["git", "cat-file", "-e", f"{b.wert}^{{commit}}"],
                cwd=klon,
                capture_output=True,
                check=False,
                timeout=GIT_TIMEOUT_S,
            ).returncode
        except (OSError, subprocess.TimeoutExpired) as fehler:
            log.warning("git für Halt-Bedingung gescheitert: %s", fehler)
            return None
        if da == 0:
            return True
        return False if abruf == 0 else None  # fetch gescheitert ⇒ vielleicht nur nicht geholt
    return True


def _lies(klon: Path) -> Halt | None:
    datei = klon / DATEI
    try:
        roh: str | None = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as fehler:  # Rechte, Ordner, Kodierung
        roh, lesefehler = None, fehler
    try:
        if roh is None:
            raise ValueError(f"nicht lesbar: {lesefehler}")
        daten = json.loads(roh)
        b = daten["bedingung"]
        return Halt(
            grund=str(daten["grund"]),
            seit=str(daten["seit"]),
            wer=str(daten.get("wer") or "?"),
            bedingung=Bedingung(str(b["art"]), str(b["wert"])),
        )
    except (ValueError, KeyError, TypeError) as fehler:
        # Altbestand/kaputt: gilt als nicht prüfbar ⇒ Frist ab erster Beobachtung (pruefen).
        # ``seit`` ist nur der stabile Merker-Schlüssel, nie der Fristbeginn.
        daten: Any = {}
        try:
            daten = json.loads(roh or "")
            seit = _zeit(str(daten["seit"])).isoformat(timespec="seconds")  # kein dict ⇒ TypeError
        except (ValueError, KeyError, TypeError):
            daten = daten if isinstance(daten, dict) else {}
            try:
                mtime = datetime.fromtimestamp(datei.stat().st_mtime, timezone.utc).astimezone()
                seit = mtime.isoformat(timespec="seconds")
            except OSError:
                seit = SEIT_UNBEKANNT
        halt = Halt(
            grund=str(daten.get("grund") or "(ohne Grund)"),
            seit=seit,
            wer=str(daten.get("wer") or "?"),
            bedingung=None,
        )
        log.warning("%s nicht lesbar oder ohne gültige Bedingung (%s) — %s", datei, fehler, halt.text())
        return halt


def _commit_auf_hauptzweig(klon: Path, sha: str) -> bool | None:
    zweig = gh.hauptzweig(klon)
    try:
        subprocess.run(
            ["git", "fetch", "-q", "origin", zweig],
            cwd=klon,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
        )
        code = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, f"origin/{zweig}"],
            cwd=klon,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("git für Halt-Bedingung gescheitert: %s", fehler)
        return None
    return {0: True, 1: False}.get(code)  # sonst unbekannter Commit/Fehler


def _erfuellt(klon: Path, b: Bedingung | None) -> bool | None:
    """``True``/``False``; ``None`` = nicht prüfbar (auch: keine gültige Bedingung)."""
    if b is None:
        return None
    if b.art == "bis":
        return _jetzt() >= _zeit(b.wert)
    if b.art == "ticket_zu":
        offen = gh.ticket_offen(b.wert, cwd=klon)
        return None if offen is None else not offen
    return _commit_auf_hauptzweig(klon, b.wert)


def _merker_stand(klon: Path, halt: Halt) -> tuple[datetime, bool] | None:
    """Merker für genau diesen Halt (``seit`` als Schlüssel): erster Fehlschlag der Prüfung
    und ob „nicht entfernbar“ schon gemeldet ist; fehlt/kaputt/fremder Halt ⇒ ``None``."""
    try:
        stand = json.loads((klon / MERKER_DATEI).read_text(encoding="utf-8"))
        if stand["halt_seit"] == halt.seit:
            return _zeit(str(stand["seit"])), bool(stand.get("nicht_entfernbar"))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        pass  # ⇒ ab jetzt zählen
    return None


def _merker_seit(klon: Path, halt: Halt) -> datetime | None:
    """Erster gemerkter Fehlschlag der Prüfung für genau diesen Halt."""
    stand = _merker_stand(klon, halt)
    return stand[0] if stand else None


def _merker_setzen(klon: Path, halt: Halt, seit: datetime | None, *, nicht_entfernbar: bool = False) -> None:
    """Merker schreiben (``seit``) bzw. löschen (``None``); Dateifehler nur als Warnung."""
    datei = klon / MERKER_DATEI
    try:
        if seit is None:
            datei.unlink(missing_ok=True)
        else:
            stand: dict[str, Any] = {"halt_seit": halt.seit, "seit": seit.isoformat(timespec="seconds")}
            if nicht_entfernbar:
                stand["nicht_entfernbar"] = True
            datei.write_text(json.dumps(stand), encoding="utf-8")
    except OSError as fehler:
        log.warning("Merker %s nicht schreibbar: %s", datei, fehler)


def _entfernen(klon: Path, halt: Halt, wer: str, anlass: str, *, sichern: bool = False, warnen: bool = True) -> bool:
    """Halt-Datei + Merker weg, Log-Zeile; ``True`` = Datei ist weg. Wirft nie.

    ``sichern`` (Fristablauf ohne prüfbare Bedingung): Datei wird umbenannt statt gelöscht,
    Rohinhalt (gekürzt) bzw. Lesefehler steht in der Log-Zeile — sichern → prüfen → löschen.
    Dateifehler ⇒ ``False`` (Halt gilt weiter), Warnung nur bei ``warnen``; war ein zweiter
    Prüfer schneller (Datei schon weg), zählt das als erledigt.
    """
    try:
        felder: dict[str, Any] = {}
        datei = klon / DATEI
        if sichern and datei.exists():
            try:
                felder["roh"] = datei.read_text(encoding="utf-8", errors="replace")[:500]
            except OSError as fehler:
                felder["roh_fehler"] = str(fehler)
            # Mikrosekunden: zwei Fristabläufe in derselben Sekunde überschreiben sich nicht.
            ziel = datei.with_name(f"{datei.name}.aufgehoben-{_jetzt().strftime('%Y%m%dT%H%M%S%f')}")
            datei.rename(ziel)
            felder["gesichert_als"] = ziel.name
        else:
            datei.unlink(missing_ok=True)
        (klon / MERKER_DATEI).unlink(missing_ok=True)
        _schreibe_log(klon, "aufgehoben", halt, wer_hebt=wer, anlass=anlass, **felder)
    except OSError as fehler:
        if not (klon / DATEI).exists():
            return True
        if warnen:
            log.warning("Halt-Datei nicht entfernbar (%s) — Halt bleibt", fehler)
        return False
    return True


def pruefen(klon: Path, *, aufheben: bool = True, wer: str = "befehl") -> Halt | None:
    """Aktiver Halt oder ``None``; erfüllte Bedingung hebt ihn hier selbst auf (``wer`` ins Log).

    ``aufheben=False`` (Trockenlauf jedes Aufrufers): gleiches Ergebnis, aber keine
    Datei wird geändert — die einzige Stelle, die darüber entscheidet, ist ``_pruefe``.
    Ist die Bedingung nicht prüfbar, bleibt der Halt. gh/git nicht erreichbar bei gültiger
    Bedingung ⇒ ohne Frist („nicht prüfbar“ ist nicht „Grund weg“, #1465). Keine gültige
    Bedingung, Ticket/Commit gibt es nicht oder Programmfehler ⇒ 24 h nach der ersten
    Beobachtung hebt er sich auf — kein Stillstand ohne Grund (20.11.: #1307 stand ~10 h).
    Wirft nie: unerwarteter Fehler ⇒ Traceback einmal je Halt, der Halt bleibt —
    Aufrufer (Takt, Aufpasser) brauchen kein eigenes ``try``.
    """
    halt: Halt | None = None
    try:
        halt = _lies(klon)
        return None if halt is None else _pruefe(klon, halt, aufheben=aufheben, wer=wer)
    except Exception:  # Zusage „wirft nie“ — Fehler darf keinen Takt/Aufpasser-Lauf abbrechen
        halt = halt or Halt("(Halt-Prüfung gescheitert)", SEIT_UNBEKANNT, "?", None)
        if _merker_seit(klon, halt) is None:  # Merker = schon gemeldet ⇒ Traceback einmal je Halt
            log.exception("Halt-Prüfung in %s gescheitert — gilt als nicht prüfbar", klon)
        else:
            log.debug("Halt-Prüfung in %s weiter gescheitert", klon, exc_info=True)
    # Dauerhafter Programmfehler = nicht prüfbar ⇒ dieselbe 24-h-Frist ab erster Beobachtung.
    try:
        return _pruefe(klon, halt, aufheben=aufheben, wer=wer, pruefbar=False)
    except Exception:  # auch die Frist scheitert ⇒ im Zweifel hält der Halt
        log.exception("Frist für gescheiterte Halt-Prüfung in %s nicht auswertbar — Halt bleibt", klon)
        return halt


def _pruefe(klon: Path, halt: Halt, *, aufheben: bool, wer: str, pruefbar: bool = True) -> Halt | None:
    """``pruefbar=False``: Bedingung nicht abfragen (Fehler-Zweig), nur die Frist zählt."""
    erfuellt = _erfuellt(klon, halt.bedingung) if pruefbar else None
    merker: datetime | None = None  # Soll-Stand des Merkers „nicht prüfbar seit“
    if erfuellt is None:
        erster = _merker_seit(klon, halt)
        merker = erster or _jetzt()
        # Frist nur, wenn der Grund nachweislich weg/ungültig ist; Werkzeug-Ausfall hält (#1465).
        bleibt = _jetzt() - merker < ALT_FRIST or (
            pruefbar and halt.bedingung is not None and _bedingung_bekannt(klon, halt.bedingung) is not False
        )
        anlass = f"nicht prüfbar seit {merker.isoformat(timespec='seconds')} (> {ALT_FRIST})"
        if bleibt and erster is None:  # Warnung nur einmal je Halt (Merker = schon gemeldet)
            log.warning("Halt-Bedingung nicht prüfbar (%s) — Halt bleibt.", _bedingung_text(halt.bedingung))
    else:
        bleibt = not erfuellt
        anlass = f"Bedingung erfüllt: {_bedingung_text(halt.bedingung)}"
    # Einzige Schreib-Entscheidung: Trockenlauf meldet nur.
    if not aufheben:
        if not bleibt:
            log.info("Halt würde aufheben (Trockenlauf) — %s: %s", anlass, halt.grund)
    elif bleibt:
        _merker_setzen(klon, halt, merker)
    else:
        stand = _merker_stand(klon, halt)
        gemeldet = bool(stand and stand[1])
        if _entfernen(klon, halt, wer, anlass, sichern=erfuellt is None, warnen=not gemeldet):
            log.log(logging.INFO if erfuellt else logging.WARNING, "Halt aufgehoben (%s): %s", anlass, halt.grund)
        else:  # Datei liegt noch ⇒ Halt gilt weiter; Merker hält „schon gemeldet“ fest
            bleibt = True
            _merker_setzen(klon, halt, merker or (stand[0] if stand else _jetzt()), nicht_entfernbar=True)
    return halt if bleibt else None


def aufheben(klon: Path, wer: str) -> bool:
    """Halt von Hand aufheben; ``False``, wenn keiner lag."""
    halt = _lies(klon)
    if halt is None:
        return False
    _entfernen(klon, halt, wer, "von Hand")
    log.info("Halt von Hand aufgehoben (%s): %s", wer, halt.grund)
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m to_spawn.halt", description="Halt einer Spec")
    p.add_argument("--repo-dir", default="", help="Klon der Spec (Vorgabe: aktueller Ordner)")
    unter = p.add_subparsers(dest="befehl", required=True)
    p_an = unter.add_parser("anlegen", help="Halt mit Grund und Aufhebe-Bedingung anlegen")
    p_an.add_argument("--grund", required=True)
    p_an.add_argument("--wer", default="")
    art = p_an.add_mutually_exclusive_group(required=True)
    art.add_argument("--ticket-zu", help="hebt sich auf, wenn dieses Ticket geschlossen ist")
    art.add_argument("--commit", help="hebt sich auf, wenn der Commit auf dem Hauptzweig ist")
    art.add_argument("--bis", help="hebt sich zum Zeitpunkt auf (ISO, z. B. 2026-11-21T08:00)")
    unter.add_parser("pruefen", help="Bedingung prüfen, ggf. aufheben, Stand zeigen")
    p_auf = unter.add_parser("aufheben", help="Halt von Hand aufheben")
    p_auf.add_argument("--wer", required=True)
    args = p.parse_args(argv)
    klon = Path(args.repo_dir or os.getcwd())
    try:
        if args.befehl == "anlegen":
            if args.ticket_zu:
                b = Bedingung("ticket_zu", args.ticket_zu.lstrip("#"))
            elif args.commit:
                b = Bedingung("commit", args.commit.lower())
            else:
                b = Bedingung("bis", _zeit(args.bis).isoformat(timespec="seconds"))
            print(anlegen(klon, args.grund, b, args.wer).text())
        elif args.befehl == "pruefen":
            halt = pruefen(klon, wer="befehl")
            print(halt.text() if halt else "Kein Halt.")
        else:
            print("Halt aufgehoben." if aufheben(klon, args.wer) else "Kein Halt.")
    except (ValueError, FileExistsError) as fehler:
        print(f"FEHLER: {fehler}")
        return 2
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
