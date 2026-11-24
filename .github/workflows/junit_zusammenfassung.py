"""Kurze Markdown-Zusammenfassung eines JUnit-XML für ``GITHUB_STEP_SUMMARY`` (#537)."""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def zusammenfassen(pfad: Path, laufzeit_s: str | None, titel: str) -> str:
    """Zählt grün/rot/übersprungen und listet rote Tests; fehlt die Datei, sagt die Zeile das."""
    if not pfad.is_file():
        return f"### {titel}\n\nKein JUnit-XML ({pfad}) — pytest lief nicht bis zum Ende.\n"
    wurzel = ET.parse(pfad).getroot()
    rot: list[str] = []
    gruen = uebersprungen = 0
    for fall in wurzel.iter("testcase"):
        name = f"{fall.get('classname', '')}::{fall.get('name', '')}"
        if fall.find("failure") is not None or fall.find("error") is not None:
            rot.append(name)
        elif fall.find("skipped") is not None:
            uebersprungen += 1
        else:
            gruen += 1
    zeit = f"{laufzeit_s} s" if laufzeit_s else "unbekannt"
    zeilen = [
        f"### {titel}",
        "",
        f"grün {gruen} · rot {len(rot)} · übersprungen {uebersprungen} · Laufzeit {zeit}",
        "",
    ]
    if rot:
        zeilen += ["<details><summary>Rote Tests</summary>", ""]
        zeilen += [f"- `{n}`" for n in sorted(rot)]
        zeilen += ["", "</details>", ""]
    return "\n".join(zeilen) + "\n"


if __name__ == "__main__":
    titel = f"pytest {os.environ.get('RUNNER_OS', '')}".strip()
    sys.stdout.buffer.write(zusammenfassen(Path(sys.argv[1]), os.environ.get("LAUFZEIT_S"), titel).encode("utf-8"))
