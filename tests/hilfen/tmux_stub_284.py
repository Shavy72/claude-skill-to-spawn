"""Ersatz für ``tmux`` in den Folge-Runden-Tests (duoplus-management#284).

tmux ist ein externes Programm; der Ersatz zeichnet nur auf, was capo aufruft —
so prüft der Test den echten Befehl mit allen Argumenten nach.

Aufruf: ``python tmux_stub_284.py <protokoll> <tmux-argumente…>``

* ``list-windows`` → Fensternamen aus der Datei ``FAKE_TMUX_FENSTER`` (eine je
  Zeile); fehlt die Datei, gibt es die Session nicht → Exit 1 wie bei tmux.
* ``new-window`` / ``new-session`` → Aufruf als JSON-Zeile ins Protokoll, Exit 0.

Jeder Aufruf landet als JSON-Zeile im Protokoll, auch ``list-windows``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    protokoll = Path(sys.argv[1])
    args = sys.argv[2:]
    with protokoll.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(args, ensure_ascii=False) + "\n")
    if args[:1] == ["list-windows"]:
        datei = Path(os.environ["FAKE_TMUX_FENSTER"])
        if not datei.is_file():
            print("can't find session", file=sys.stderr)
            return 1
        print(datei.read_text(encoding="utf-8").strip())
        return 0
    if args[:1] in (["new-window"], ["new-session"]):
        return 0
    print(f"tmux-Ersatz #284: unbekannter Aufruf {args}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
