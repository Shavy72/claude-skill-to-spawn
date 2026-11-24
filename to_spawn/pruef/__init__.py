"""Prüf-Werkzeugkasten — die eine Tür ``pruefen(repo, basis, ziel) -> Urteil`` (L3, #99).

Der Aufrufer sagt nur „von wo bis wo“ und liest ``Urteil.exit`` (0 grün · 1 rot · 2 Gate-Fehler,
also Werkzeug kaputt, nicht der Code) plus ``Urteil.zeile["fehler_kurz"]``. Sprache, Test-Werkzeug,
Stufen und Protokoll-Format bleiben im Paket. Jeder Lauf hängt ``Urteil.zeile`` an
``<repo>/.to-spawn/pruef/protokoll.jsonl`` an. Ohne ``.to-spawn/config.json`` lauffähig.

``art`` (#425) sagt, welche Prüfung dran ist — die Regel dafür lebt nur in ``to_spawn.pruefplan``,
der Aufrufer reicht deren Antwort durch. ``schnell``: eingerichteter Befehl ``pruefung.schnell``,
sonst Sofort-Stufen + betroffene Tests, nie der volle Lauf. ``komplett``: ``pruefung.komplett``,
sonst voller Lauf. Ohne ``art`` läuft die Leiter wie vor #425 (Auswahl, sonst voll). Mit ``art``
steht sie in der Protokoll-Zeile (``art``) und die Dauer im Log.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn import config, pruefplan
from to_spawn.pruef import (
    adapter_node,
    adapter_py,
    cache,
    leiter,
    protokoll,
    regeln,
    sofort,
)

_ADAPTER: tuple[leiter.Adapter, ...] = (adapter_py.ADAPTER, adapter_node.ADAPTER)
log = logging.getLogger("to_spawn.pruef")

#: Prüf-Arten, die ``pruefen`` ausführt (``keine`` aus ``pruefplan.welche`` heißt: nicht aufrufen).
_ARTEN = ("schnell", "komplett")
_DAUER_NAME = {"schnell": "schnellen", "komplett": "Komplett-"}


@dataclass(frozen=True)
class Urteil:
    exit: int
    zeile: dict[str, Any]


def _sha(repo: Path, ref: str) -> str | None:
    try:
        erg = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return erg.stdout.strip() if erg.returncode == 0 else None


_ungespeichert = sofort.ungespeichert  # eine Stelle für „schmutzig“ (#218), Name für Alt-Tests


def _schreibe(repo: Path, zeile: dict[str, Any], art: str | None) -> None:
    """Protokoll-Zeile anhängen; mit ``art`` samt Feld ``art`` und Messwert „Dauer“ im Log."""
    if art is not None:
        zeile["art"] = art
        log.info("Dauer der %s Prüfung: %.2f s (Urteil %s)", _DAUER_NAME[art], zeile["gesamt_s"], zeile["urteil"])
    protokoll.schreibe(repo, zeile)


def _gate_fehler(repo: Path, basis: str, ziel: str, grund: str, start: float, art: str | None = None) -> Urteil:
    zeile = protokoll.zeile(
        basis=basis,
        ziel=ziel,
        exit_code=leiter.GATE_FEHLER,
        stufen=[],
        werkzeug=[],
        fehler_kurz=grund,
        gesamt_s=time.monotonic() - start,
    )
    _schreibe(repo, zeile, art)
    return Urteil(leiter.GATE_FEHLER, zeile)


def hat_werkzeug(repo: Path) -> bool:
    """Erkennt die Prüf-Tür in ``repo`` eine Sprache (Python/Node), die sie prüfen kann?

    ``False`` heißt: es gibt nichts zu testen (z. B. reines Doku-Repo). ``pruefen`` meldet das
    als Gate-Fehler; wer einen solchen Stand trotzdem durchlassen darf, fragt vorher hier.
    """
    return any(adapter.erkennt(repo) for adapter in _ADAPTER)


def pruefen(repo: Path, basis: str, ziel: str, art: pruefplan.Art | None = None) -> Urteil:
    """Prüft den Stand ``ziel`` gegen ``basis`` und protokolliert das Urteil.

    ``art`` = Antwort von ``pruefplan.welche`` (``schnell``/``komplett``); ``None`` = Leiter wie
    vor #425. ``keine`` ist kein Prüf-Auftrag (``ValueError``) — der Aufrufer ruft dann nicht.
    """
    if art is not None and art not in _ARTEN:
        raise ValueError(f"pruefen: art={art!r} ist keine Prüfung (erlaubt: {', '.join(_ARTEN)})")
    start = time.monotonic()
    basis_sha, ziel_sha, kopf = _sha(repo, basis), _sha(repo, ziel), _sha(repo, "HEAD")
    if basis_sha is None or ziel_sha is None:
        fehlt = basis if basis_sha is None else ziel
        return _gate_fehler(repo, basis, ziel, f"Stand „{fehlt}“ in {repo} nicht gefunden (git)", start, art)
    # ponytail: geprüft wird der Arbeitsbaum, darum muss ziel ausgecheckt sein;
    # Upgrade: eigener Worktree je ziel, wenn die Schlange fremde Stände prüft (3b-6).
    if ziel_sha != kopf:
        return _gate_fehler(
            repo,
            basis_sha,
            ziel_sha,
            f"ziel {ziel} ist nicht ausgecheckt (HEAD {kopf})",
            start,
            art,
        )
    offen = _ungespeichert(repo)
    if offen:
        return _gate_fehler(
            repo,
            basis_sha,
            ziel_sha,
            f"Arbeitsbaum nicht gespeichert (erst committen): {', '.join(offen[:5])}",
            start,
            art,
        )
    schluessel = cache.schluessel(repo, basis_sha, ziel_sha)
    if schluessel is not None and art is not None:
        schluessel = f"{schluessel}-{art}"  # grün „schnell“ ersetzt nie „komplett“
    if cache.treffer(schluessel):
        zeile = protokoll.zeile(
            basis=basis_sha,
            ziel=ziel_sha,
            exit_code=leiter.GRUEN,
            stufen=[
                {
                    "name": "cache",
                    "adapter": "",
                    "lief": True,
                    "grund": "Stand schon grün geprüft (Stufe 0)",
                    "dauer_s": round(time.monotonic() - start, 2),
                    "befehl": "",
                }
            ],
            werkzeug=[],
            fehler_kurz="",
            gesamt_s=time.monotonic() - start,
            cache_treffer=True,
        )
        _schreibe(repo, zeile, art)
        return Urteil(leiter.GRUEN, zeile)
    eigen = pruefplan.befehl(config.lade(repo), art) if art is not None else None
    if eigen:
        liste = [leiter.eigener_befehl(repo, eigen, art)]
    else:
        liste = leiter.stufen(repo, _ADAPTER, basis_sha, art)
    if not liste:
        return _gate_fehler(
            repo,
            basis_sha,
            ziel_sha,
            "keine Sprache erkannt (weder Python noch Node)",
            start,
            art,
        )
    exit_code, fehler_kurz = leiter.ergebnis(liste)
    zeile = protokoll.zeile(
        basis=basis_sha,
        ziel=ziel_sha,
        exit_code=exit_code,
        stufen=[
            {
                "name": stufe.name,
                "adapter": stufe.adapter,
                "lief": stufe.lief,
                "grund": stufe.grund,
                "dauer_s": stufe.dauer_s,
                "befehl": stufe.befehl,
                "wiederholung": stufe.wiederholung,
                "wiederholung_s": stufe.wiederholung_s,
                "voll_nochmal_s": stufe.voll_nochmal_s,
            }
            for stufe in liste
        ],
        werkzeug=[stufe.werkzeug for stufe in liste if stufe.werkzeug] + [regeln.stand()],
        fehler_kurz=fehler_kurz,
        gesamt_s=time.monotonic() - start,
        auswahl=[stufe.auswahl for stufe in liste if stufe.auswahl] or None,
        wackler=[{"test": test, "adapter": stufe.adapter} for stufe in liste for test in stufe.wackler],
    )
    _schreibe(repo, zeile, art)
    cache.merke(schluessel, zeile)
    return Urteil(exit_code, zeile)
