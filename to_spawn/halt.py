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
  hebt es den Halt selbst auf und schreibt das ins Halt-Log. Nicht prüfbar
  (gh/git-Fehler) ⇒ Halt bleibt, Warnung im Log, kein Absturz — länger als
  ``ALT_FRIST`` am Stück nicht prüfbar ⇒ Halt hebt sich mit Warnung auf.
  ``aufheben=False`` (Probelauf) ändert keine Datei. Wirft nie.
- :func:`aufheben` — von Hand (``wer`` steht im Log).

Bedingungen: ``ticket_zu`` (Issue N geschlossen), ``commit`` (Commit X auf dem
Hauptzweig von origin), ``bis`` (Zeitpunkt erreicht). Eine Datei ohne gültige
Bedingung (alter Halt von Hand, kaputtes JSON, unlesbar) gilt als ``bis`` Anlage + 24 h.

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
    bedingung: Bedingung

    def text(self) -> str:
        return f"Halt seit {self.seit} ({self.wer}): {self.grund} — {self.bedingung.text()}"


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
        # Altbestand/kaputt: nie ewig stehen, nie still weg — 24 h ab Anlage.
        try:
            daten = json.loads(roh or "")
            seit = _zeit(str(daten["seit"]))  # kein dict ⇒ TypeError ⇒ unten {}
        except (ValueError, KeyError, TypeError):
            daten = {}
            try:
                seit = datetime.fromtimestamp(datei.stat().st_mtime, timezone.utc).astimezone()
            except OSError:
                seit = _jetzt() - ALT_FRIST  # nicht einmal das Alter lesbar ⇒ nie ewig blockieren
        halt = Halt(
            grund=str(daten.get("grund") or "(ohne Grund)"),
            seit=seit.isoformat(timespec="seconds"),
            wer=str(daten.get("wer") or "?"),
            bedingung=Bedingung("bis", (seit + ALT_FRIST).isoformat(timespec="seconds")),
        )
        log.warning(
            "%s nicht lesbar oder ohne gültige Bedingung (%s) — gilt %s",
            datei,
            fehler,
            halt.bedingung.text(),
        )
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


def _erfuellt(klon: Path, b: Bedingung) -> bool | None:
    """``True``/``False``; ``None`` = nicht prüfbar."""
    if b.art == "bis":
        return _jetzt() >= _zeit(b.wert)
    if b.art == "ticket_zu":
        offen = gh.ticket_offen(b.wert, cwd=klon)
        return None if offen is None else not offen
    return _commit_auf_hauptzweig(klon, b.wert)


def _nicht_pruefbar_seit(klon: Path, halt: Halt, merken: bool) -> datetime:
    """Erster Fehlschlag der Prüfung für genau diesen Halt (``seit`` als Schlüssel)."""
    datei = klon / MERKER_DATEI
    try:
        stand = json.loads(datei.read_text(encoding="utf-8"))
        if stand["halt_seit"] == halt.seit:
            return _zeit(str(stand["seit"]))
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError):
        pass  # fehlt/kaputt/fremder Halt ⇒ ab jetzt zählen
    jetzt = _jetzt()
    if merken:
        try:
            datei.write_text(
                json.dumps(
                    {
                        "halt_seit": halt.seit,
                        "seit": jetzt.isoformat(timespec="seconds"),
                    }
                ),
                encoding="utf-8",
            )
        except OSError as fehler:
            log.warning("Merker %s nicht schreibbar: %s", datei, fehler)
    return jetzt


def _entfernen(klon: Path, halt: Halt, wer: str, anlass: str) -> None:
    """Halt-Datei + Merker weg, Log-Zeile; Dateifehler nur als Warnung (wirft nie)."""
    try:
        (klon / DATEI).unlink(missing_ok=True)
        (klon / MERKER_DATEI).unlink(missing_ok=True)
        _schreibe_log(klon, "aufgehoben", halt, wer_hebt=wer, anlass=anlass)
    except OSError as fehler:
        log.warning("Halt-Datei nicht entfernbar (%s) — gilt trotzdem als aufgehoben", fehler)


def pruefen(klon: Path, *, aufheben: bool = True) -> Halt | None:
    """Aktiver Halt oder ``None``; erfüllte Bedingung hebt ihn hier selbst auf.

    ``aufheben=False`` (Probelauf): gleiches Ergebnis, aber keine Datei wird geändert.
    """
    halt = _lies(klon)
    if halt is None:
        return None
    erfuellt = _erfuellt(klon, halt.bedingung)
    if erfuellt is None:
        seit = _nicht_pruefbar_seit(klon, halt, merken=aufheben)
        if _jetzt() - seit < ALT_FRIST:
            log.warning(
                "Halt-Bedingung nicht prüfbar (%s) — Halt bleibt.",
                halt.bedingung.text(),
            )
            return halt
        anlass = f"nicht prüfbar seit {seit.isoformat(timespec='seconds')} (> {ALT_FRIST})"
        log.warning("Halt-Bedingung %s — Halt aufgehoben: %s", anlass, halt.grund)
    elif not erfuellt:
        if aufheben:
            (klon / MERKER_DATEI).unlink(missing_ok=True)
        return halt
    else:
        anlass = "Bedingung erfüllt"
        log.info(
            "Halt aufgehoben (Bedingung erfüllt: %s): %s",
            halt.bedingung.text(),
            halt.grund,
        )
    if aufheben:
        _entfernen(klon, halt, "takt", anlass)
    else:
        log.info("Probelauf: Halt würde aufgehoben (%s)", anlass)
    return None


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
            halt = pruefen(klon)
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
