"""Repo-neutrale Minimal-Attrappe für ``scripts/schlange_deckung.py --bis-spitze`` (#872).

Deckt exakt die CLI-Schnittstelle ab, die ``to_spawn/capo.py:live_gedeckt()`` im CI-Gate prüft:
Exit 0 + ``{"gedeckt": true, "offen": []}`` bei voller Deckung, sonst Exit 1 + offene Schlüssel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SKRIPT_ORDNER = str(Path(__file__).resolve().parent)
if _SKRIPT_ORDNER not in sys.path:
    sys.path.insert(0, _SKRIPT_ORDNER)

import schlange_spur
from staging import deploy_ziel


def pruefe_deckung(
    repo: Path,
    kopf: str,
    sha: str,
    *,
    bis_spitze: bool,
    manifeste: Path,
    freigabe_ordner: Path,
) -> tuple[bool, list[str]]:
    """Berechnet, ob alle Commits im Bereich durch Freigabe-Zettel oder Doku-Regel gedeckt sind."""
    ende = schlange_spur.spitze_ref(repo, sha) if bis_spitze else sha
    commits = schlange_spur.commits_im_bereich(repo, kopf, ende)
    if commits is None:
        return False, ["git_fehler"]
    if not commits:
        return True, []

    zettel = schlange_spur.zettel_menge(freigabe_ordner)
    eltern = deploy_ziel.eltern_specs(manifeste)

    # Ein SHA-Zettel auf der Bereichs-Spitze deckt alle früheren Commits mit ab (H1).
    if schlange_spur.sha_passt(commits[-1][0], zettel):
        return True, []

    offen: list[str] = []
    for c_sha, betreff, dateien in commits:
        if schlange_spur.sha_passt(c_sha, zettel):
            continue
        tickets = schlange_spur.ticket_nummern(betreff)
        if tickets:
            for nr in tickets:
                if nr in zettel or bool(eltern.get(nr, set()) & zettel):
                    continue
                if nr not in offen:
                    offen.append(nr)
            continue
        if dateien and all(deploy_ziel.ist_doku_pfad(p) for p in dateien):
            continue
        offen.append(c_sha[:7])
    return len(offen) == 0, offen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repo-neutrale Deckungs-Attrappe für capo.live_gedeckt (#872).")
    parser.add_argument("kopf", help="Aktueller Live-Stand (Commit-SHA)")
    parser.add_argument("sha", help="Ziel-Commit des Tickets")
    parser.add_argument("--bis-spitze", action="store_true", help="Bereich bis origin/master statt nur bis sha prüfen")
    parser.add_argument("--repo", required=True, type=Path, help="Pfad zum Git-Repo")
    parser.add_argument("--manifeste", required=True, type=Path, help="Pfad zum Manifest-Ordner")
    parser.add_argument("--freigabe-ordner", required=True, type=Path, help="Pfad zum Freigabe-Zettel-Ordner")
    args = parser.parse_args(argv)

    gedeckt, offen = pruefe_deckung(
        args.repo,
        args.kopf,
        args.sha,
        bis_spitze=args.bis_spitze,
        manifeste=args.manifeste,
        freigabe_ordner=args.freigabe_ordner,
    )
    print(json.dumps({"gedeckt": gedeckt, "offen": offen}))
    return 0 if gedeckt else 1


if __name__ == "__main__":
    raise SystemExit(main())
