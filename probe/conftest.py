"""Skill-pytest sammelt die Probe-Vorlagen nie ein (#426 Teil F).

Die Vorlagen enthalten App-Tests und einen absichtlich roten Werkzeug-Test; sie laufen nur im
angelegten Lauf-Repo. Geprüft: tests/test_probe_vorlagen_426.py.
"""

collect_ignore = ["gemeinsam", "mit-staging", "ohne-staging"]
