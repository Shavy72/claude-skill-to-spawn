"""Echter ``bau.py``-Prozess für #97: ``main()`` läuft echt, nur die Außengrenzen sind gestellt.

Aufruf: ``python bau_lauf_97.py <beleg> <ticket> [bau-Schalter …]``. Jede gestartete
Claude-Session hängt eine Zeile ``START <pid>`` an ``<beleg>`` und läuft 2 s (damit sich
zwei gleichzeitige Läufe überlappen). Der Ticket-Stand kommt aus der gh-Attrappe
(``TO_SPAWN_GH_STUB``), so wie ``gh issue view --json state`` antwortet.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

SKRIPTE = Path(__file__).resolve().parent.parent.parent / "skripte"


def main() -> int:
    beleg = Path(sys.argv[1])
    spec = importlib.util.spec_from_file_location("bau_lauf_97", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    bau = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bau)

    bau.gh_repo_ermitteln = lambda _repo: "test-org/test-repo"
    bau.config.sicherstellen = lambda _repo: None
    bau.load_default = lambda *_a, **_k: {"prompt_template": "T {ticket}"}
    bau.find_manifest = lambda _t: ({"spec": "9"}, {"title": "Titel"})
    bau.build_prompt = lambda *_a, **_k: "PROMPT"
    bau.known_skill_names = lambda: set()
    bau.mcp_catalog = dict
    bau.context_mode.server_definition = lambda _d: {}
    bau.nest.sandbox_start = lambda *_a, **_k: []
    bau.nest.git_hooks_einrichten = lambda _r: None
    bau.auf_speicher_warten = lambda *_a, **_k: None
    bau.speicher.auf_platte_warten = lambda **_k: None
    bau.vertrauen.still_sicherstellen = lambda *_a, **_k: None
    bau.blocker_schranke = lambda *_a, **_k: None
    bau.session_id_setzen = lambda *_a, **_k: None
    bau.bau_log_umgebung = lambda *_a, **_k: {}
    bau.staffel_uebergabe = lambda _d: None
    bau.umzug_anfragen_aufraeumen = lambda _t: beleg.parent / "anfrage.json"
    bau.terminal_maus.maus_ruhig = lambda: None

    ticket = sys.argv[2]
    halte_datei = os.environ.get("BAU_LAUF_97_HALTE_DATEI")
    schlaf_s = float(os.environ.get("BAU_LAUF_97_SCHLAF_S", "2.0"))

    def starte_session(_cmd: list[str], _umzug: Path) -> tuple[int, None]:
        with beleg.open("a", encoding="utf-8") as datei:
            datei.write(f"START {os.getpid()} {ticket}\n")
        if halte_datei:
            ziel = Path(halte_datei)
            ende = time.monotonic() + 60.0
            while not ziel.exists() and time.monotonic() < ende:
                time.sleep(0.05)
        elif schlaf_s > 0:
            time.sleep(schlaf_s)
        return 0, None

    bau.starte_session = starte_session
    sys.argv = ["bau.py", *sys.argv[2:]]
    return int(bau.main())


if __name__ == "__main__":
    sys.exit(main())
