"""Sammel-Einstellungen für einen pytest-Lauf ohne Pfad aus der Repo-Wurzel."""

# #214 Teil A: Hook 1:1, Tests ortsgebunden; Teil B hebt das auf.
# ``review-hook/tests`` suchen ``review/config.standard.json`` relativ zum Installations-Ort
# (``~/.claude/hooks/review``) und laufen nur dort. Geprüft: tests/test_review_hook_214a_fix2.py.
# #426: ``probe/`` enthält Vorlagen (Daten) für das angelegte Lauf-Repo, keine Skill-Tests.
# Geprüft: tests/test_probe_nicht_gesammelt_426.py.
collect_ignore = ["review-hook", "probe"]
