"""Prüf-Werkzeugkasten — die eine Tür ``pruefen(repo, basis, ziel) -> Urteil`` (L3, #99).

Der Aufrufer sagt nur „von wo bis wo“ und liest ``Urteil.exit`` (0 grün · 1 rot · 2 Gate-Fehler,
also Werkzeug kaputt, nicht der Code) plus ``Urteil.zeile["fehler_kurz"]``. Sprache, Test-Werkzeug,
Stufen und Protokoll-Format bleiben im Paket. Jeder Lauf hängt ``Urteil.zeile`` an
``<repo>/.to-spawn/pruef/protokoll.jsonl`` an. Ohne ``.to-spawn/config.json`` lauffähig.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from to_spawn.pruef import adapter_node, adapter_py, leiter, protokoll

_ADAPTER: tuple[leiter.Adapter, ...] = (adapter_py.ADAPTER, adapter_node.ADAPTER)


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


def _gate_fehler(repo: Path, basis: str, ziel: str, grund: str, start: float) -> Urteil:
    zeile = protokoll.zeile(
        basis=basis,
        ziel=ziel,
        exit_code=leiter.GATE_FEHLER,
        stufen=[],
        werkzeug=[],
        fehler_kurz=grund,
        gesamt_s=time.monotonic() - start,
    )
    protokoll.schreibe(repo, zeile)
    return Urteil(leiter.GATE_FEHLER, zeile)


def pruefen(repo: Path, basis: str, ziel: str) -> Urteil:
    """Prüft den Stand ``ziel`` gegen ``basis`` und protokolliert das Urteil."""
    start = time.monotonic()
    basis_sha, ziel_sha, kopf = _sha(repo, basis), _sha(repo, ziel), _sha(repo, "HEAD")
    if basis_sha is None or ziel_sha is None:
        fehlt = basis if basis_sha is None else ziel
        return _gate_fehler(
            repo, basis, ziel, f"Stand „{fehlt}“ in {repo} nicht gefunden (git)", start
        )
    # ponytail: geprüft wird der Arbeitsbaum, darum muss ziel ausgecheckt sein;
    # Upgrade: eigener Worktree je ziel, wenn die Schlange fremde Stände prüft (3b-6).
    if ziel_sha != kopf:
        return _gate_fehler(
            repo,
            basis_sha,
            ziel_sha,
            f"ziel {ziel} ist nicht ausgecheckt (HEAD {kopf})",
            start,
        )
    liste = leiter.stufen(repo, _ADAPTER, basis_sha)
    if not liste:
        return _gate_fehler(
            repo,
            basis_sha,
            ziel_sha,
            "keine Sprache erkannt (weder Python noch Node)",
            start,
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
            }
            for stufe in liste
        ],
        werkzeug=[stufe.werkzeug for stufe in liste if stufe.werkzeug],
        fehler_kurz=fehler_kurz,
        gesamt_s=time.monotonic() - start,
    )
    protokoll.schreibe(repo, zeile)
    return Urteil(exit_code, zeile)
