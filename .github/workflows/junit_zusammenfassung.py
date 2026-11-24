"""Kurze Markdown-Zusammenfassung eines JUnit-XML für ``GITHUB_STEP_SUMMARY`` (#537)."""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from auslassungen import lesen


def zusammenfassen(
    pfad: Path, laufzeit_s: str | None, titel: str, ausgelassen: list[tuple[str, str]] | None = None
) -> str:
    """Zählt grün/rot/übersprungen/ausgelassen, listet rote und ausgelassene Tests.

    Fehlt das JUnit-XML, sagt die Zeile das (Auslassungen stehen trotzdem darunter).
    """
    ausgelassen = ausgelassen or []
    if not pfad.is_file():
        return f"### {titel}\n\nKein JUnit-XML ({pfad}) — pytest lief nicht bis zum Ende.\n\n" + _aus_liste(ausgelassen)
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
        f"grün {gruen} · rot {len(rot)} · übersprungen {uebersprungen} · ausgelassen {len(ausgelassen)}"
        f" · Laufzeit {zeit}",
        "",
    ]
    if rot:
        zeilen += ["<details><summary>Rote Tests</summary>", ""]
        zeilen += [f"- `{n}`" for n in sorted(rot)]
        zeilen += ["", "</details>", ""]
    return "\n".join(zeilen) + "\n" + _aus_liste(ausgelassen)


def _aus_liste(ausgelassen: list[tuple[str, str]]) -> str:
    if not ausgelassen:
        return ""
    zeilen = [f"<details><summary>Ausgelassen ({len(ausgelassen)})</summary>", ""]
    zeilen += [f"- `{t}` — {g}" for t, g in ausgelassen]
    return "\n".join([*zeilen, "", "</details>", ""]) + "\n"


if __name__ == "__main__":
    titel = f"pytest {os.environ.get('RUNNER_OS', '')}".strip()
    aus = lesen(Path(sys.argv[2])) if len(sys.argv) > 2 else []
    text = zusammenfassen(Path(sys.argv[1]), os.environ.get("LAUFZEIT_S"), titel, aus)
    sys.stdout.buffer.write(text.encode("utf-8"))
