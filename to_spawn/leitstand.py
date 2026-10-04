"""Leitstand (#313): gemeinsame Sperren und gemeinsamer Zustand auf dem Bau-Server.

Jede Komponente (Aufseher, Gate, Nest, capo, aufpasser) fragt hier, statt nach
eigenem Stand zu handeln.

* Sperren sind echte Betriebssystem-Sperren (Linux ``fcntl.flock``, Windows
  ``msvcrt.locking``) und werden beim Prozess-Tod vom System frei. Lebend-Prüfung
  nur über die Sperre selbst — nie ``os.kill(pid, 0)`` (killt auf Windows).
* Wer wartet, legt einen Wartezettel in ``<name>.warte/`` und hält darauf selbst
  eine Sperre (Lebendzeichen). Reihenfolge = Name des Zettels (Zeit zuerst), FIFO.
* ``zustand.json`` wird nur unter der internen Sperre ``_zustand`` gelesen und
  atomar (``.neu`` + ``os.replace``) geschrieben.

Unterbefehle von ``to_spawn.py leitstand``: ``stand`` und ``zustand``.
Halter-Einstieg (Tests, Deploy-Dienst): ``python -m to_spawn.leitstand halte <name> <halter> --bis <datei>``.
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger("to_spawn.leitstand")

NAME_MUSTER = re.compile(r"[A-Za-z0-9._-]+")
ZUSTAND_SPERRE = "_zustand"
# Windows sperrt ein Byte weit hinter dem Inhalt, damit der Inhalt lesbar bleibt.
_SPERR_OFFSET = 1 << 30
# Ein frisch angelegter Wartezettel ist kurz leer und noch nicht gesperrt.
_ZETTEL_ANLAUF_S = 5.0
_BELEGT_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    errno.EWOULDBLOCK,
    getattr(errno, "EDEADLK", -1),
    getattr(errno, "EDEADLOCK", -1),
}


class SperreBelegt(TimeoutError):
    """Die Sperre wurde innerhalb von ``warte_s`` nicht frei."""


class ZustandKaputt(RuntimeError):
    """``zustand.json`` ist unlesbar — wird nie still überschrieben."""


# --- Ordner und Namen ---------------------------------------------------------


def leitstand_ordner() -> Path:
    """``$TO_SPAWN_LEITSTAND_ORDNER`` oder ``~/.claude/to-spawn/leitstand``."""
    wert = os.environ.get("TO_SPAWN_LEITSTAND_ORDNER")
    if wert:
        return Path(wert).expanduser()
    return Path.home() / ".claude" / "to-spawn" / "leitstand"


def _sperr_ordner() -> Path:
    ordner = leitstand_ordner() / "sperren"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def _pruefe_name(name: str, *, intern_erlaubt: bool = False) -> str:
    if (
        not isinstance(name, str)
        or not NAME_MUSTER.fullmatch(name)
        or name in {".", ".."}
    ):
        raise ValueError(
            f"ungültiger Sperr-Name: {name!r} (erlaubt: A-Z a-z 0-9 . _ -)"
        )
    if name.startswith("_") and not intern_erlaubt:
        # „_“ vorn gehört dem Leitstand selbst (z. B. ``_zustand``).
        raise ValueError(f"Sperr-Name {name!r} ist intern (beginnt mit „_“)")
    return name


def _jetzt_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- Betriebssystem-Sperre ----------------------------------------------------


def _versuche_os_sperre(fd: int) -> bool:
    """Nimmt die Sperre auf ``fd`` nicht-blockierend; ``False`` wenn belegt."""
    try:
        if sys.platform == "win32":
            os.lseek(fd, _SPERR_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as fehler:
        if fehler.errno in _BELEGT_ERRNOS:
            return False
        raise
    return True


def _loese_os_sperre(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, _SPERR_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _probe_frei(pfad: Path) -> bool | None:
    """``True`` = nehmbar (Inhaber tot/keiner), ``False`` = gehalten, ``None`` = Datei weg."""
    try:
        fd = os.open(pfad, os.O_RDWR)
    except FileNotFoundError:
        return None
    except PermissionError:
        # Nur Windows: Datei wird gerade gelöscht. Linux: fehlende Rechte — nie still „frei“.
        if sys.platform == "win32":
            return None
        raise
    try:
        if _versuche_os_sperre(fd):
            _loese_os_sperre(fd)
            return True
        return False
    finally:
        os.close(fd)


def _mit_wiederholung(aktion: Callable[[], None], was: str, anzahl: int = 40) -> None:
    """Führt ``aktion`` aus; Windows-PermissionError (Leser hat Datei offen) kurz wiederholen."""
    for _ in range(anzahl):
        try:
            aktion()
            return
        except FileNotFoundError:
            log.debug("%s: Datei schon weg", was)
            return
        except PermissionError:
            time.sleep(0.05)
        except OSError as fehler:
            log.warning("%s fehlgeschlagen: %s", was, fehler)
            return
    log.warning("%s fehlgeschlagen: Datei blieb belegt", was)


def _schreibe_atomar(ziel: Path, daten: Any) -> None:
    neu = ziel.with_name(f"{ziel.name}.{os.getpid()}.neu")
    with open(neu, "w", encoding="utf-8") as datei:
        json.dump(daten, datei, ensure_ascii=False, indent=2)
        datei.flush()
        os.fsync(datei.fileno())
    for _ in range(40):
        try:
            os.replace(neu, ziel)
            return
        except PermissionError:
            # Windows: ein Leser hat die Zieldatei gerade offen.
            time.sleep(0.05)
    try:
        os.replace(neu, ziel)
    except OSError:
        _mit_wiederholung(neu.unlink, f"verwaiste Datei {neu.name} löschen")
        raise


def _lies_json_tolerant(pfad: Path) -> dict | None:
    """Liest eine Halter-/Zettel-Datei; halb geschrieben oder belegt → ``None``."""
    try:
        inhalt = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return inhalt if isinstance(inhalt, dict) else None


# --- Sperren ------------------------------------------------------------------


@dataclass
class Halteschein:
    """Beleg für eine gehaltene Sperre; ``freigeben()`` ist idempotent."""

    name: str
    halter: str
    _fd: int | None = field(default=None, repr=False)

    def freigeben(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        halter_datei = _sperr_ordner() / f"{self.name}.halter.json"
        _mit_wiederholung(halter_datei.unlink, f"Halter-Datei {self.name} löschen")
        try:
            _loese_os_sperre(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> Halteschein:
        return self

    def __exit__(self, *_: object) -> None:
        self.freigeben()


@dataclass
class SperrStand:
    name: str
    halter: dict | None
    wartende: list[dict] = field(default_factory=list)


def _nimm(name: str, halter: str) -> Halteschein | None:
    """Nimmt die Sperre nicht-blockierend (ohne Blick auf die Reihe)."""
    ordner = _sperr_ordner()
    fd = os.open(ordner / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if not _versuche_os_sperre(fd):
            os.close(fd)
            return None
        # Rest eines abgestürzten Halters sofort weg, damit ``stand()`` ihn nie zeigt.
        _mit_wiederholung(
            (ordner / f"{name}.halter.json").unlink, f"alte Halter-Datei {name} löschen"
        )
        _schreibe_atomar(
            ordner / f"{name}.halter.json",
            {"halter": halter, "pid": os.getpid(), "seit": _jetzt_iso()},
        )
    except BaseException:
        os.close(fd)
        raise
    return Halteschein(name=name, halter=halter, _fd=fd)


def _lebende_wartende(name: str) -> list[tuple[Path, dict]]:
    """Lebende Wartende in Reihenfolge; tote Zettel werden weggeräumt."""
    reihe = _sperr_ordner() / f"{name}.warte"
    if not reihe.is_dir():
        return []
    lebende: list[tuple[Path, dict]] = []
    for zettel in sorted(reihe.glob("*.json")):
        frei = _probe_frei(zettel)
        if frei is None:
            continue
        inhalt = _lies_json_tolerant(zettel)
        if not frei:
            lebende.append((zettel, inhalt or {"halter": "?"}))
            continue
        if inhalt is None:
            try:
                alter = time.time() - zettel.stat().st_mtime
            except FileNotFoundError:
                continue
            if alter < _ZETTEL_ANLAUF_S:
                continue  # wird gerade angelegt
        _mit_wiederholung(
            zettel.unlink, f"toten Wartezettel {zettel.name} wegräumen", anzahl=3
        )
    return lebende


def _lege_zettel(name: str, halter: str) -> tuple[Path, int]:
    reihe = _sperr_ordner() / f"{name}.warte"
    reihe.mkdir(parents=True, exist_ok=True)
    zettel = reihe / f"{time.time_ns():020d}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    fd = os.open(zettel, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        # Eine fremde Probe (``_probe_frei``) kann den frischen Zettel für einen
        # Augenblick sperren — kurz wiederholen statt abbrechen.
        ende = time.monotonic() + _ZETTEL_ANLAUF_S
        while not _versuche_os_sperre(fd):
            if time.monotonic() >= ende:
                raise RuntimeError(f"eigener Wartezettel {zettel} nicht sperrbar")
            time.sleep(0.005)
        os.lseek(fd, 0, os.SEEK_SET)  # Windows-Sperre hat die Position verschoben
        daten = {"halter": halter, "pid": os.getpid(), "seit": _jetzt_iso()}
        os.write(fd, json.dumps(daten, ensure_ascii=False).encode("utf-8"))
    except BaseException:
        # Kein halber Zettel darf als Lebender vorne in der Reihe stehen bleiben.
        os.close(fd)
        _mit_wiederholung(zettel.unlink, f"Wartezettel {zettel.name} verwerfen")
        raise
    return zettel, fd


def _entferne_zettel(zettel: Path, fd: int) -> None:
    try:
        _loese_os_sperre(fd)
    finally:
        os.close(fd)
    _mit_wiederholung(zettel.unlink, f"eigenen Wartezettel {zettel.name} entfernen")


@contextmanager
def sperre(
    name: str, halter: str, *, warte_s: float | None = None, takt_s: float = 0.1
) -> Iterator[Halteschein]:
    """Blockiert in der Reihe (FIFO), bis die Sperre frei ist; ``warte_s`` → ``SperreBelegt``."""
    _pruefe_name(name)
    ende = None if warte_s is None else time.monotonic() + warte_s
    zettel, zettel_fd = _lege_zettel(name, halter)
    schein: Halteschein | None = None
    try:
        while True:
            lebende = [pfad for pfad, _ in _lebende_wartende(name)]
            if not lebende or lebende[0] == zettel or zettel not in lebende:
                schein = _nimm(name, halter)
                if schein is not None:
                    break
            if ende is not None and time.monotonic() >= ende:
                raise SperreBelegt(f"Sperre {name!r} nach {warte_s} s nicht frei")
            time.sleep(takt_s)
    finally:
        try:
            _entferne_zettel(zettel, zettel_fd)
        except BaseException:
            # Sonst bliebe die schon genommene Sperre bis zum Prozess-Ende belegt.
            if schein is not None:
                schein.freigeben()
            raise
    with schein:
        yield schein


def versuche(name: str, halter: str) -> Halteschein | None:
    """Nicht-blockierend; stehen lebende Wartende an, → ``None``.

    Probt gleichzeitig jemand die Sperre (``stand()``), kann auch eine freie
    Sperre einmal ``None`` liefern — Aufrufer versuchen es im nächsten Takt erneut.
    """
    _pruefe_name(name)
    if _lebende_wartende(name):
        return None
    return _nimm(name, halter)


def stand(name: str | None = None) -> list[SperrStand]:
    """Je Sperre: wirklicher Halter (Probe) und lebende Wartende in Reihenfolge."""
    ordner = _sperr_ordner()
    if name is not None:
        namen = [_pruefe_name(name, intern_erlaubt=True)]
    else:
        namen = sorted(
            {p.name[: -len(".lock")] for p in ordner.glob("*.lock")}
            | {p.name[: -len(".warte")] for p in ordner.glob("*.warte") if p.is_dir()}
        )
        namen = [n for n in namen if not n.startswith("_")]
    ergebnis: list[SperrStand] = []
    for n in namen:
        halter: dict | None = None
        if _probe_frei(ordner / f"{n}.lock") is False:
            halter = _lies_json_tolerant(ordner / f"{n}.halter.json") or {"halter": "?"}
        wartende = [inhalt for _, inhalt in _lebende_wartende(n)]
        ergebnis.append(SperrStand(name=n, halter=halter, wartende=wartende))
    return ergebnis


# --- Zustand ------------------------------------------------------------------


@contextmanager
def _zustand_sperre(warte_s: float = 60.0) -> Iterator[None]:
    """Interne Sperre ``_zustand`` (ohne Reihe, kurz gehalten)."""
    fd = os.open(
        _sperr_ordner() / f"{ZUSTAND_SPERRE}.lock", os.O_RDWR | os.O_CREAT, 0o644
    )
    try:
        ende = time.monotonic() + warte_s
        while not _versuche_os_sperre(fd):
            if time.monotonic() >= ende:
                raise SperreBelegt(
                    f"interne Sperre {ZUSTAND_SPERRE} nach {warte_s} s nicht frei"
                )
            time.sleep(0.005)
        try:
            yield
        finally:
            _loese_os_sperre(fd)
    finally:
        os.close(fd)


def _zustand_datei() -> Path:
    return leitstand_ordner() / "zustand.json"


def _lies_zustand_ungesperrt() -> dict:
    datei = _zustand_datei()
    try:
        text = datei.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as fehler:
        log.error("Zustand %s nicht lesbar: %s", datei, fehler)
        raise ZustandKaputt(f"{datei} nicht lesbar: {fehler}") from fehler
    try:
        zustand = json.loads(text)
    except ValueError as fehler:
        log.error("Zustand %s kaputt (kein JSON): %s", datei, fehler)
        raise ZustandKaputt(f"{datei} ist kein gültiges JSON: {fehler}") from fehler
    if not isinstance(zustand, dict):
        log.error("Zustand %s kaputt: kein Objekt", datei)
        raise ZustandKaputt(f"{datei} enthält kein JSON-Objekt")
    return zustand


def lese_zustand() -> dict:
    """Ganzer Zustand; ``{}`` wenn die Datei fehlt, unlesbar → ``ZustandKaputt``."""
    with _zustand_sperre():
        return _lies_zustand_ungesperrt()


def aendere_zustand(fn: Callable[[dict], dict | None]) -> dict:
    """Liest, ruft ``fn`` (in-place oder neues dict), schreibt atomar, gibt neuen Zustand."""
    with _zustand_sperre():
        zustand = _lies_zustand_ungesperrt()
        neu = fn(zustand)
        if neu is not None:
            zustand = neu
        _schreibe_atomar(_zustand_datei(), zustand)
        return zustand


def setze_notiz(name: str, text: str) -> None:
    aendere_zustand(lambda z: z.setdefault("notizzettel", {}).__setitem__(name, text))


def notiz(name: str) -> str | None:
    return lese_zustand().get("notizzettel", {}).get(name)


def neuer_reopen(ticket: int, grund: str) -> None:
    eintrag = {"ticket": ticket, "grund": grund, "zeit": _jetzt_iso()}
    aendere_zustand(lambda z: z.setdefault("reopens", []).append(eintrag))


def neue_entscheidung(ticket: int, text: str) -> None:
    eintrag = {"ticket": ticket, "text": text, "zeit": _jetzt_iso()}
    aendere_zustand(lambda z: z.setdefault("entscheidungen", []).append(eintrag))


def setze_leiter_stufe(ticket: int, stufe: int, seit: float | None = None) -> None:
    """Merkt Stufe der Eingriffs-Leiter (#432) und Zeitpunkt des letzten Eingriffs."""
    eintrag = {"stufe": int(stufe), "seit": seit}
    aendere_zustand(
        lambda z: z.setdefault("leiter_stufe", {}).__setitem__(str(ticket), eintrag)
    )


def leiter_eintrag(ticket: int) -> tuple[int, float | None]:
    """(Stufe, seit) — liest das alte Format (nackte Zahl) und das neue (dict)."""
    roh = lese_zustand().get("leiter_stufe", {}).get(str(ticket), 0)
    if isinstance(roh, dict):
        seit = roh.get("seit")
        return int(roh.get("stufe") or 0), None if seit is None else float(seit)
    return int(roh), None


def leiter_stufe(ticket: int) -> int:
    return leiter_eintrag(ticket)[0]


# --- CLI ----------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    """Hängt ``leitstand`` mit Unterbefehlen an den Parser von ``to_spawn.py``."""
    p_ls = unter.add_parser(
        "leitstand", help="Leitstand: gemeinsame Sperren und Zustand"
    )
    ls_unter = p_ls.add_subparsers(dest="leitstand_befehl", required=True)

    p_st = ls_unter.add_parser("stand", help="Sperren: Halter und Wartende")
    p_st.add_argument("--name", help="nur diese Sperre")
    p_st.add_argument("--json", action="store_true", help="als JSON ausgeben")

    p_zu = ls_unter.add_parser("zustand", help="gemeinsamen Zustand ausgeben")
    p_zu.add_argument("--json", action="store_true", help="kompakt als JSON ausgeben")


def _zeile(eintrag: dict) -> str:
    return f"{eintrag.get('halter', '?')} (pid {eintrag.get('pid', '?')}, seit {eintrag.get('seit', '?')})"


def lauf(args: argparse.Namespace) -> int:
    """Führt ``to_spawn.py leitstand <unterbefehl>`` aus."""
    befehl = args.leitstand_befehl
    if befehl == "stand":
        try:
            eintraege = stand(args.name)
        except (ValueError, OSError) as fehler:
            print(f"Fehler: {fehler}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps([asdict(e) for e in eintraege], ensure_ascii=False))
            return 0
        for e in eintraege:
            if e.halter is None:
                print(f"{e.name}: frei")
            else:
                print(f"{e.name}: gehalten von {_zeile(e.halter)}")
            for w in e.wartende:
                print(f"  wartet: {_zeile(w)}")
        return 0
    if befehl == "zustand":
        try:
            zustand = lese_zustand()
        except ZustandKaputt as fehler:
            print(f"Zustand kaputt: {fehler}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(zustand, ensure_ascii=False))
        else:
            print(json.dumps(zustand, ensure_ascii=False, indent=2))
        return 0
    print(f"unbekannter Unterbefehl: {befehl}", file=sys.stderr)
    return 2


def _halte(argv: list[str]) -> int:
    """Sperre nehmen, ``<datei>.hat`` schreiben, halten bis ``<datei>`` existiert.

    Stirbt der Elternprozess (Linux: neue Eltern-PID), gibt der Halter ebenfalls frei.
    Genutzt von Tests und vom Deploy-Dienst (#315, Halter ``deploy-schlange``).
    """
    ap = argparse.ArgumentParser(prog="python -m to_spawn.leitstand")
    unter = ap.add_subparsers(dest="befehl", required=True)
    p_ha = unter.add_parser("halte", help="Sperre halten, bis eine Datei existiert")
    p_ha.add_argument("name")
    p_ha.add_argument("halter")
    p_ha.add_argument("--bis", type=Path, required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    eltern = os.getppid()
    with sperre(args.name, args.halter):
        Path(f"{args.bis}.hat").touch()
        while not args.bis.exists() and os.getppid() == eltern:
            time.sleep(0.05)
    return 0


if __name__ == "__main__":
    sys.exit(_halte(sys.argv[1:]))
