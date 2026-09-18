"""Wächter-Tick für eine Spec: Stand, Bau-Log-Delta, Regel-Verstöße (#213).

    python scripts/capo.py <S> [--dry-run] [--uebersicht] [--wt-basis P] [--gh-repo owner/name]

Ein Aufruf = ein Tick: Kopfzeile, eine Stand-Zeile je Ticket, nur die neuen
Bau-Log-Zeilen, dann Verstöße und was capo getan hat (Ticket wieder geöffnet,
Kommentar, Mail). Sind alle Tickets zu und ohne Verstoß: „SPEC FERTIG“, Mail
``spec_fertig`` und die Entscheidungs-Übersicht ``docs/agents/entscheidungen_<S>.md``.

``--dry-run`` öffnet nichts, kommentiert nichts, mailt nichts und merkt sich nichts.
``--uebersicht`` schreibt nur die Entscheidungs-Übersicht und gibt ihren Pfad aus.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn`` importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import bau_log, capo, config, gh

log = logging.getLogger("capo")
#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO_ORDNER = (
    Path(os.environ["TO_SPAWN_REPO"]).resolve()
    if os.environ.get("TO_SPAWN_REPO")
    else config.repo_wurzel()
)


def main() -> int:
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    ap.add_argument(
        "--repo-dir", default=str(REPO_ORDNER), help="Git-Repo (Vorgabe: TO_SPAWN_REPO)"
    )
    ap.add_argument(
        "--gh-repo", default="", help="owner/name auf GitHub (Vorgabe: aus origin)"
    )
    ap.add_argument(
        "--wt-basis",
        default=None,
        help="Ordner der Worktrees wt-<N> (Vorgabe: Konfig wt_basis)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="nichts öffnen, nichts mailen, nichts merken",
    )
    ap.add_argument(
        "--uebersicht",
        action="store_true",
        help="nur Entscheidungs-Übersicht schreiben",
    )
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    repo = Path(a.repo_dir).resolve()
    konfig = config.lade(repo)
    gh_repo = a.gh_repo or gh.repo_aus_origin(repo, fallback="")
    if not gh_repo:
        print("FEHLER: GitHub-Repo unbekannt — --gh-repo owner/name angeben.")
        return 2
    wt_basis = (
        a.wt_basis if a.wt_basis is not None else str(konfig.get("wt_basis") or "")
    )

    if a.uebersicht:
        ref = capo.haupt_ref(repo)
        liste = capo.kinder(gh_repo, a.spec)
        if liste is not None:
            tickets = [int(i["number"]) for i in liste]
        else:
            log.warning("Sub-Issues nicht lesbar — nehme alle Bau-Logs des Repos.")
            tickets = [int(t) for t in bau_log.alle_tickets(repo)]
        print(capo.uebersicht(repo, ref, a.spec, tickets))
        return 0

    ergebnis = capo.tick(
        repo, a.spec, gh_repo, konfig, wt_basis=wt_basis, dry_run=a.dry_run
    )
    print("\n".join(ergebnis.zeilen))
    return ergebnis.exit_code


if __name__ == "__main__":
    sys.exit(main())
