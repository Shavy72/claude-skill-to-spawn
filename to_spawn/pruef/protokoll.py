"""Prüf-Protokoll: eine JSON-Zeile je Lauf, Format und Ort nur hier (Paket-intern, #99)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.pruef")

#: Ort im geprüften Repo; der Ordner bekommt ein eigenes ``.gitignore``, damit nichts mitcommittet wird.
ORDNER = Path(".to-spawn") / "pruef"
DATEI = "protokoll.jsonl"
#: Liste aller Wackler mit Datum, eine Zeile je Test (3b-4).
WACKLER_DATEI = "wackler.jsonl"
URTEIL = {0: "grün", 1: "rot", 2: "gate-fehler"}


def zeile(
    *,
    basis: str,
    ziel: str,
    exit_code: int,
    stufen: list[dict[str, Any]],
    werkzeug: list[str],
    fehler_kurz: str,
    gesamt_s: float,
    wackler: list[dict[str, str]] | None = None,
    cache_treffer: bool = False,
    auswahl: list[dict[str, Any]] | None = None,
    rote_tests: list[dict[str, str]] | None = None,
    schluessel: str | None = None,
) -> dict[str, Any]:
    """Felder nach PRUEFTOR_VERGLEICH 6.2.

    ``auswahl`` = je Adapter {adapter, n, gesamt, sicher, grund}; ``None`` = keine Test-Stufe lief.

    ``cache_treffer`` = Stand war schon grün geprüft, Stufe 0 (``cache``) hat nichts laufen lassen.

    ``wackler`` = [{test, adapter}]: erst rot, in der Wiederholung grün. Ist sonst alles grün,
    heißt das Urteil ``wackler`` (Exit bleibt 0) — nie still „grün“.

    ``rote_tests`` = [{test, adapter}]: rote Tests dieses Laufs für die „Nach Rot“-Regel (#795).
    """
    wackler = wackler or []
    daten: dict[str, Any] = {
        "zeit": datetime.now(UTC).isoformat(timespec="seconds"),
        "basis": basis,
        "ziel": ziel,
        "urteil": "wackler" if exit_code == 0 and wackler else URTEIL[exit_code],
        "stufen": stufen,
        "auswahl": auswahl,
        "cache_treffer": cache_treffer,
        "fehler_kurz": fehler_kurz,
        "wackler": wackler,
        "rote_tests": list(rote_tests or []),
        "gesamt_s": round(gesamt_s, 2),
        "werkzeug": werkzeug,
    }
    if schluessel is not None:
        daten["schluessel"] = schluessel
    return daten


def letzter_lauf(repo: Path) -> dict[str, Any] | None:
    """Letzte Protokoll-Zeile ohne Cache-Treffer; ``None`` = keine oder unlesbar."""
    try:
        zeilen = (repo / ORDNER / DATEI).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for roh in reversed(zeilen):
        try:
            daten = json.loads(roh)
        except ValueError:
            continue
        if isinstance(daten, dict) and not daten.get("cache_treffer"):
            return daten
    return None


def letztes_urteil(repo: Path) -> str:
    """Urteil der letzten Protokoll-Zeile ohne Cache-Treffer; ``""`` = keine oder unlesbar."""
    daten = letzter_lauf(repo)
    return str(daten.get("urteil", "")) if daten is not None else ""


def letzte_rote(repo: Path, adapter: str | None = None) -> tuple[str, ...]:
    """Gemerkte rote Tests des letzten nicht-gecachten Laufs (#795).

    Liefert ``()``, wenn der letzte Lauf nicht rot war oder keine einzelnen roten Tests protokolliert
    wurden. Akzeptiert sowohl ``{"test": ..., "adapter": ...}`` als auch reine Zeichenketten.
    """
    daten = letzter_lauf(repo)
    if daten is None or str(daten.get("urteil", "")) != "rot":
        return ()
    roh = daten.get("rote_tests")
    if not isinstance(roh, list):
        return ()
    gefunden: list[str] = []
    gesehen: set[str] = set()
    for eintrag in roh:
        if isinstance(eintrag, str):
            name, eintrag_adapter = eintrag.strip(), ""
        elif isinstance(eintrag, dict):
            name = str(eintrag.get("test", "")).strip()
            eintrag_adapter = str(eintrag.get("adapter", "")).strip()
        else:
            continue
        if not name or name in gesehen:
            continue
        if adapter and eintrag_adapter and eintrag_adapter != adapter:
            continue
        gesehen.add(name)
        gefunden.append(name)
    return tuple(gefunden)


def text(daten: dict[str, Any]) -> str:
    return json.dumps(daten, ensure_ascii=False, separators=(",", ":"))


def schreibe(repo: Path, daten: dict[str, Any]) -> None:
    """Hängt die Zeile an; scheitert das Schreiben, bleibt es bei einer Warnung (stdout trägt sie)."""
    ordner = repo / ORDNER
    try:
        ordner.mkdir(parents=True, exist_ok=True)
        ignorieren = ordner / ".gitignore"
        if not ignorieren.exists():
            ignorieren.write_text("*\n", encoding="utf-8")
        with (ordner / DATEI).open("a", encoding="utf-8", newline="\n") as datei:
            datei.write(text(daten) + "\n")
        if daten["wackler"]:
            kopf = {feld: daten[feld] for feld in ("zeit", "basis", "ziel")}
            with (ordner / WACKLER_DATEI).open("a", encoding="utf-8", newline="\n") as datei:
                for eintrag in daten["wackler"]:
                    datei.write(text({**kopf, **eintrag}) + "\n")
    except OSError as fehler:
        log.warning("Prüf-Protokoll nicht geschrieben (%s): %s", ordner / DATEI, fehler)
