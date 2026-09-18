"""Kompakter Stand aller Tickets einer Spec — für den Bau-Wächter.

Liest NUR GitHub + origin/master + Worktree-Ordner. Spricht keine Session an,
schreibt nichts. Eine Zeile je Ticket, damit ein Wächter-Tick ~1k Token kostet.

    python scripts/spec_stand.py 132
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import config  # noqa: E402

REPO = "Shavy72/duoplus-management"
log = logging.getLogger("spec_stand")
#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO_ORDNER = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel()


def sh(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"{args[:3]} → {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def gh_json(pfad: str) -> object:
    return json.loads(sh("gh", "api", pfad))


def alter(iso: str | None) -> str:
    if not iso:
        return "—"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    m = int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", type=int)
    ap.add_argument("--repo-dir", default=str(REPO_ORDNER))
    ap.add_argument("--wt-basis", default="C:/dev")
    a = ap.parse_args()
    repo = Path(a.repo_dir)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    sh("git", "fetch", "-q", "origin", cwd=repo)
    vps = ""
    try:
        vps = sh("ssh", "clawy-vps", "cd /opt/duoplus-management && git rev-parse --short HEAD")
    except RuntimeError as e:
        log.warning("VPS-HEAD nicht lesbar: %s", e)
    origin = sh("git", "rev-parse", "--short", "origin/master", cwd=repo)

    kinder = gh_json(f"repos/{REPO}/issues/{a.spec}/sub_issues?per_page=100")
    assert isinstance(kinder, list)
    zeilen = []
    offen_gesamt = 0
    for k in sorted(kinder, key=lambda x: x["number"]):
        n = k["number"]
        state = "zu " if k["state"] == "closed" else "OFF"
        if k["state"] != "closed":
            offen_gesamt += 1
        wer = ",".join(u["login"] for u in k.get("assignees") or []) or "-"
        deps = gh_json(f"repos/{REPO}/issues/{n}/dependencies/blocked_by")
        assert isinstance(deps, list)
        blocker_offen = [d["number"] for d in deps if d["state"] != "closed"]
        commit = sh("git", "log", "origin/master", "--oneline", "-1", "--fixed-strings", "--grep", f"(#{n})", cwd=repo)
        commit = commit[:7] if commit else "-"
        kommentare = gh_json(f"repos/{REPO}/issues/{n}/comments?per_page=1&direction=desc")
        assert isinstance(kommentare, list)
        letzter = kommentare[0] if kommentare else None
        kom = f"{alter(letzter['created_at'])} „{letzter['body'].strip().splitlines()[0][:70]}“" if letzter else "—"
        wt = Path(a.wt_basis) / f"wt-{n}"
        wt_info = "-"
        if wt.is_dir():
            try:
                dirty = sh("git", "status", "--short", cwd=wt).splitlines()
                wt_info = f"wt:{len(dirty)}dirty"
            except RuntimeError:
                wt_info = "wt:?"
        wartet = f"wartet {','.join(f'#{b}' for b in blocker_offen)}" if blocker_offen else "frei"
        zeilen.append(f"#{n} {state} {wer:<10} {wartet:<16} commit:{commit} {wt_info:<10} {kom}")

    print(
        f"Spec #{a.spec} · origin/master {origin} · VPS {vps or '?'} · offen {offen_gesamt}/{len(kinder)} · {datetime.now():%d.%m. %H:%M}"
    )
    print("\n".join(zeilen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
