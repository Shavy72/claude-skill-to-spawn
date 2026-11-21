"""Ausführbare Test-Attrappen, die auch unter Windows starten (Skill-Issue #68).

Unter Linux startet ein Python-Skript mit Shebang direkt. Windows startet ohne Umweg nur
``.exe``/``.com``: ein Skript ohne Endung scheitert mit ``WinError 193``, und eine
``.cmd``-Hülle läuft über ``cmd.exe`` — dort verweigern Pythons BatBadBut-Schutz und der
Notnagel in ``to_spawn.starter`` jedes Argument mit Anführungszeichen (zu Recht: echte
Programme wie ``ssh.exe`` oder ``powershell.exe`` sind keine Batch-Dateien).

``windows_starter`` legt deshalb neben das Skript eine kleine ``.exe``: den Starter aus
distlib, den pip für seine Konsolen-Skripte nutzt. Er reicht die Befehlszeile unverändert an
das Test-Python weiter, das das Skript bei jedem Start frisch ausführt — wer das Skript neu
schreibt, ändert die Attrappe sofort. Unter Linux tut die Hilfe nichts.
"""

from __future__ import annotations

import importlib.util
import io
import struct
import sys
import sysconfig
import zipfile
from pathlib import Path

__all__ = ["windows_starter"]


def _starter_vorlage() -> bytes:
    """Rohbytes des distlib-Konsolen-Starters (``t64.exe`` usw.) für dieses Python."""
    bits = "64" if struct.calcsize("P") == 8 else "32"
    arm = "-arm" if sysconfig.get_platform() == "win-arm64" else ""
    name = f"t{bits}{arm}.exe"
    for paket in ("pip._vendor.distlib", "distlib"):
        try:
            spec = importlib.util.find_spec(paket)
        except ImportError:
            continue
        if spec is None or spec.origin is None:
            continue
        datei = Path(spec.origin).parent / name
        if datei.is_file():
            return datei.read_bytes()
    raise FileNotFoundError(f"distlib-Starter {name} fehlt (weder pip noch distlib im Test-Python)")


def windows_starter(skript: Path) -> Path:
    """Startpfad der Python-Attrappe ``skript``: unter Windows ``skript.exe``, sonst ``skript``.

    Windows: legt ``<skript>.exe`` neben ``skript`` an (vorhandene wird ersetzt). Liegt der
    Ordner im ``PATH``, findet ``shutil.which(<name>)`` die ``.exe`` über ``PATHEXT`` vor
    einer gleichnamigen ``.cmd``. ``sys.argv[0]`` ist im Skript dessen eigener Pfad.
    Linux/macOS: nichts zu tun — das Skript startet über seinen Shebang.
    """
    if sys.platform != "win32":
        return skript
    ziel = skript.with_name(skript.name + ".exe")
    hauptmodul = (
        f"import runpy, sys\nsys.argv[0] = {str(skript)!r}\nrunpy.run_path({str(skript)!r}, run_name='__main__')\n"
    )
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as archiv:
        archiv.writestr("__main__.py", hauptmodul)
    shebang = f'#!"{sys.executable}"\n'.encode()
    ziel.write_bytes(_starter_vorlage() + shebang + puffer.getvalue())
    return ziel
