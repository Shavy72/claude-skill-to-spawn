"""Absichtlich roter Test des Bau-Werkzeugs (Probe-Prüfpunkt 7, #426).

Liegt unter ``.to-spawn/`` und gehört dem Bau-Werkzeug, nicht der App. Die App-Prüfung
(``pruefung.schnell``/``pruefung.komplett`` und das Prüftor des Skills) darf ihn nie einsammeln:
Ist das Probe-Ticket trotz dieses roten Tests grün, ist die Trennung belegt.
Nicht reparieren und nicht löschen.
"""

import unittest


class WerkzeugRot(unittest.TestCase):
    def test_absichtlich_rot(self) -> None:
        self.fail("absichtlich rot: Werkzeug-Test, darf die Probe-App nicht aufhalten")
