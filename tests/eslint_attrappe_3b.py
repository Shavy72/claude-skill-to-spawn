"""Attrappe für ESLint (Test #100): ``--version`` → v9.0.0, sonst Exit aus ``ESLINT_ATTRAPPE_EXIT``."""

import os
import sys

if "--version" in sys.argv:
    print("v9.0.0")
    sys.exit(0)
code = int(os.environ.get("ESLINT_ATTRAPPE_EXIT", "0"))
if code:
    print(f"{sys.argv[-1]}: Attrappe meldet Fehler (Exit {code})")
sys.exit(code)
