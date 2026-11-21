"""Schlanker Bau-Ordner über ``scripts/arbeitsordner.py`` des Haupt-Repos (#1505).

Das Wissen, was in einen Arbeitsordner gehört (Sparse-Muster), lebt allein im Haupt-Repo.
Dieses Modul lädt nur dessen ``arbeitsordner_anlegen`` und ruft es auf. Fehlt es, lädt es
nicht oder scheitert es, räumt ``anlegen`` den halben Ordner weg und meldet False; der
Aufrufer legt dann wie früher voll an. Ein kaputter Stand auf origin bricht nie den Bau-Start.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("to_spawn.arbeitsordner")

MODUL_PFAD = "scripts/arbeitsordner.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _laden(hauptrepo: Path, basis: str) -> Callable[..., None] | None:
    """``arbeitsordner_anlegen`` aus ``<basis>:scripts/arbeitsordner.py``, sonst None.

    Quelle ist der frisch geholte ``origin/<hauptzweig>``, nicht der Arbeitsbaum: der geteilte
    Hauptbaum kann veraltet oder halb bearbeitet sein, und die Sparse-Regel muss zum Stand
    passen, auf dem der Ordner beginnt.
    """
    quelle = _git(hauptrepo, "show", f"{basis}:{MODUL_PFAD}")
    if quelle.returncode != 0:
        return None
    with tempfile.TemporaryDirectory(prefix="to-spawn-arbeitsordner-") as ordner:
        datei = Path(ordner) / "arbeitsordner.py"
        datei.write_text(quelle.stdout, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("to_spawn_hauptrepo_arbeitsordner", datei)
        try:
            if spec is None or spec.loader is None:
                raise ImportError(f"{datei} nicht ladbar")
            modul = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(modul)
            anlegen = modul.arbeitsordner_anlegen
        except Exception as fehler:  # noqa: BLE001 — fail-safe: fremder Code bricht nie den Bau-Start
            log.warning("%s aus %s nicht ladbar, volles worktree add: %s", MODUL_PFAD, basis, fehler)
            return None
    return anlegen if callable(anlegen) else None


def anlegen(
    hauptrepo: Path,
    worktree: Path,
    basis: str | None,
    zweig: str,
    *,
    neuer_zweig: bool,
    ticket: str | None,
) -> bool:
    """Legt ``worktree`` schlank an; True = fertig, False = Aufrufer legt voll an.

    ``neuer_zweig``: ``zweig`` entsteht ab ``basis``; sonst wird der vorhandene ausgecheckt.
    Bei False liegt garantiert kein halber Ordner mehr da (sonst hielte der nächste Start ihn
    per ``exists()`` für fertig). Ein schon angelegter Zweig kann bleiben — der Aufrufer
    prüft danach selbst, ob er existiert.
    """
    if basis is None:
        return False
    aufruf = _laden(hauptrepo, basis)
    if aufruf is None:
        return False
    try:
        if neuer_zweig:
            aufruf(hauptrepo, worktree, basis, zweig=zweig, ticket=ticket)
        else:
            aufruf(hauptrepo, worktree, zweig, ticket=ticket)
    except Exception as fehler:  # noqa: BLE001 — jeder Fehler: aufräumen, dann voller Rückfall
        if worktree.exists():
            _git(hauptrepo, "worktree", "remove", "--force", str(worktree))
            shutil.rmtree(worktree, ignore_errors=True)
        _git(hauptrepo, "worktree", "prune")
        rest = getattr(fehler, "stderr", None) or str(fehler) or type(fehler).__name__
        if isinstance(rest, bytes):
            rest = rest.decode("utf-8", "replace")
        log.warning("Schlanker Arbeitsordner scheiterte, volles worktree add: %s", str(rest).strip()[:300])
        return False
    log.info("Arbeitsordner (schlank) angelegt: %s (Zweig %s)", worktree, zweig)
    return True
