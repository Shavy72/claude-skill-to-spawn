"""Review 393, Fix-Runde 1: lokale Review-Config übersteht den Installer; keine Namensliste im Repo.

- Der Installer spielt nur ``config.standard.json`` ein; die lokale ``config.json`` im Ziel fasst er
  nie an. ``lade_config`` ergänzt fehlende Schlüssel aus dem Standard.
- Die Öffentlich-Prüfung trägt keine Namen (auch nicht kodiert oder als Prüfsumme) im Repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from to_spawn import review_hook

SKILL = Path(__file__).resolve().parents[1]


def _config_im_ziel(ziel: Path) -> dict:
    """``lade_config`` so, wie der Hook sie im eingespielten Ordner aufruft (ohne ``REVIEW_CONFIG``)."""
    env = {k: v for k, v in os.environ.items() if k != "REVIEW_CONFIG"}
    raus = subprocess.run(
        [sys.executable, "-c", "import json, _gemeinsam; print(json.dumps(_gemeinsam.lade_config()))"],
        cwd=ziel,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(raus.stdout)


def test_lokale_config_bleibt_scharf_standard_ergaenzt(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    ziel.mkdir()
    lokal = '{"modus": "scharf"}\n'
    (ziel / "config.json").write_text(lokal, encoding="utf-8")

    review_hook.installiere(ziel, review_hook.WERKZEUG_QUELLE, zeit="t")

    assert (ziel / "config.json").read_text(encoding="utf-8") == lokal
    cfg = _config_im_ziel(ziel)
    assert cfg["modus"] == "scharf"
    standard = json.loads((review_hook.WERKZEUG_QUELLE / "config.standard.json").read_text(encoding="utf-8"))
    assert cfg["projekt_woerter"] == standard["projekt_woerter"]


def test_ohne_lokale_config_gilt_standard(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    review_hook.installiere(ziel, review_hook.WERKZEUG_QUELLE, zeit="t")
    assert not (ziel / "config.json").exists()
    standard = json.loads((ziel / "config.standard.json").read_text(encoding="utf-8"))
    assert _config_im_ziel(ziel) == standard


def test_keine_namensliste_im_oeffentlich_test() -> None:
    """Weder Zeichen-Codes noch Kurz-Prüfsummen von Namen im Quelltext der Öffentlich-Prüfung."""
    text = (SKILL / "tests" / "test_review_quelle_329.py").read_text(encoding="utf-8")
    assert not re.search(r"chr\(\s*c\s*\)|\(\s*\d{2,3}\s*,\s*\d{2,3}\s*,\s*\d{2,3}", text)
    assert not re.search(r'"[0-9a-f]{16}"', text)
