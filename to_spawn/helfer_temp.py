"""Temp-Ordner für Helfer der Leit-Session (#551): ein fester Ort, ein Aufräum-Weg.

Warum: Helfer (Bauer, Prüfer) legten Temp-Ordner frei im Home an und räumten per
Shell-Löschbefehl mit ``*`` auf. Die Sicherheits-Rückfrage dazu hielt die Leit-Session an.
Darum gilt: anlegen und aufräumen nur über diesen Befehl, Python löscht selbst.

Schnittstelle (CLI ``to_spawn.py helfer-temp …``):

- ``anlegen <name>`` — legt :func:`ort` ``/<name>`` an und gibt den Pfad aus (für ``TMPDIR=``).
- ``aufraeumen <name>`` — löscht genau diesen Ordner.
- ``aufraeumen --aelter-als-tage N`` — löscht Unterordner, die seit N Tagen unverändert sind.

Der feste Ort ist ``<Temp-Ordner des Bau-Systems>/helfer`` (:mod:`to_spawn.temp_ordner`).
Löschen und Pfad-Prüfung (Name, ``..``, Symlink) liegen allein beim Hausmeister
(:func:`to_spawn.hausmeister.helfer_ordner_loeschen`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

from . import hausmeister, temp_ordner

log = logging.getLogger("to_spawn.helfer_temp")

BEFEHLE = ("helfer-temp",)


def ort() -> Path:
    """Der feste Ort aller Helfer-Temp-Ordner (nicht angelegt; das macht :func:`anlegen`)."""
    return temp_ordner.pfad() / "helfer"


#: Zeichen, die ein Helfer-Name nie enthält: Pfad-Trenner, Home-Kürzel, Platzhalter.
_VERBOTEN = frozenset("/\\~*?[]")


def _name(name: str) -> str:
    """Zulässiger Ordnername oder ``ValueError`` (leer, ``.``/``..``, Pfad-Teile, Platzhalter)."""
    if not name.strip() or name != name.strip() or name in (".", "..") or _VERBOTEN & set(name):
        raise ValueError(f"Helfer-Temp: Name {name!r} abgelehnt (nur ein schlichter Ordnername)")
    return name


def anlegen(name: str) -> Path:
    """``ort()/name`` anlegen (idempotent) und zurückgeben; ``ValueError`` bei unzulässigem Namen."""
    ordner = ort() / _name(name)
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def aufraeumen(name: str) -> bool:
    """Genau ``ort()/name`` löschen; ``False``, wenn er fehlt. ``ValueError``: nichts gelöscht."""
    return hausmeister.helfer_ordner_loeschen(ort(), _name(name))


def aufraeumen_aelter_als(tage: float, *, jetzt: float | None = None) -> list[Path]:
    """Unterordner von :func:`ort` löschen, die seit ``tage`` Tagen unverändert sind.

    Symlinks und Dateien bleiben stehen. Gibt die gelöschten Ordner zurück.
    """
    basis = ort()
    if not basis.is_dir():
        return []
    grenze = (time.time() if jetzt is None else jetzt) - tage * 86400
    weg: list[Path] = []
    for eintrag in sorted(basis.iterdir()):
        if eintrag.is_symlink() or not eintrag.is_dir() or eintrag.stat().st_mtime > grenze:
            continue
        try:
            if hausmeister.helfer_ordner_loeschen(basis, _name(eintrag.name)):
                weg.append(eintrag)
        except ValueError as fehler:
            log.warning("%s", fehler)
    return weg


def richte_parser_ein(unter: Any) -> None:
    """Unterbefehl ``helfer-temp`` an ``unter`` (``add_subparsers``-Objekt) hängen."""
    p = unter.add_parser("helfer-temp", help="Temp-Ordner für Helfer anlegen/aufräumen (#551)")
    tuer = p.add_subparsers(dest="helfer_befehl", required=True)
    p_an = tuer.add_parser("anlegen", help="Ordner anlegen, Pfad ausgeben (für TMPDIR=)")
    p_an.add_argument("name")
    p_auf = tuer.add_parser("aufraeumen", help="genau einen Ordner oder alte Reste löschen")
    wahl = p_auf.add_mutually_exclusive_group(required=True)
    wahl.add_argument("name", nargs="?")
    wahl.add_argument("--aelter-als-tage", type=float, metavar="N")


def lauf(args: argparse.Namespace) -> int:
    """``helfer-temp`` ausführen; Exit 1 bei abgelehntem Namen oder Dateifehler."""
    try:
        if args.helfer_befehl == "anlegen":
            print(anlegen(args.name))
        elif args.aelter_als_tage is not None:
            for ordner in aufraeumen_aelter_als(args.aelter_als_tage):
                print(f"gelöscht: {ordner}")
        else:
            print(f"{'gelöscht' if aufraeumen(args.name) else 'fehlte'}: {ort() / args.name}")
    except (ValueError, OSError) as fehler:
        print(fehler, file=sys.stderr)
        return 1
    return 0
