"""Belegseiten eines Tickets lesen: die eine Stelle, die weiß, was ein Beleg aussagt (#282 P1).

Belege liegen unter dem Beleg-Ordner des Repos (Vorgabe ``docs/verify-hard/``) als Datei
``<N>…`` oder Ordner ``[<datum>_]<N>…/`` mit ``.md``/``.txt``. :func:`auswerten` liefert je
Ticket, was darin steht:

- **Rot-Beweis** — eine Datei, deren Name ``rot`` enthält (gewolltes Scheitern vor dem Fix);
  sie zählt nie als Testlauf.
- **Testlauf** — der neueste Lauf (Entstehungszeit der Datei, :func:`zeitpunkt`) aus den
  übrigen Dateien; bei gleicher Zeit der größte. Fallback: saubere Markdown-Zeilen bzw.
  ``X/Y``-Zählung. Ist der neueste Lauf grün, aber ein älterer, größerer Lauf rot, deckt der
  Teil-Lauf den roten nicht ab: Status ``offen``, der rote Lauf steht in ``verdeckt`` — außer
  ein späterer grüner Lauf mit mindestens so vielen Tests hat ihn schon abgelöst. Im Zweifel
  offen: wurde ein Test bewusst entfernt (alt 37+3, neu 39 grün), bleibt das Ticket ``offen``.
- **Klickweg** — eine Markdown-Zeile mit „Klickweg“/„Live-Klick“/„Live-Beweis“, die nicht
  verneint ist („kein Klickweg“, „entfällt“ zählen nicht).
- **Status** ``gruen``/``rot``/``offen`` aus Testlauf und ``ABNAHME:``-Zeile.

Nutzer: ``skripte/test_uebersicht.py`` (Anzeige) und :mod:`to_spawn.wiederanlauf` (was fehlt
beim erneuten Start). Neue Erkennungsregeln gehören nur hierher.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("belege")

MAX_BYTES = 2_000_000
_ROT_DATEI = re.compile(r"(?:^|[_\-/.])rot(?:[_\-/.]|$)", re.I)
_PASSED = re.compile(r"\b(\d+) passed\b")
_FAILED = re.compile(r"\b(\d+) (?:failed|errors?)\b")
_BEFEHL = re.compile(r"(?<![\w./])(?:python3? -m pytest|pytest|node --test|npm (?:run )?test) [^\n`|]*")
_ABNAHME = re.compile(r"ABNAHME[^:\n]*:\s*\**\s*([^\n*|]+)")
# Abnahme-Stichwörter: Wortanfang fest, Endung frei (grün/grüne/grünen, belegt/belegte), aber
# "unbelegt" trifft nicht. ROT nicht vor Bindestrich ("Rot-Beweis" ist Beleg, kein Urteil).
# OFFEN gilt als schlecht, außer verneint ("0 offen", "nichts mehr offen", "keine Punkte offen":
# Verneinung direkt vor OFFEN, höchstens 1 Wort, keine Satzzeichen) - die Verneinung wird vor der Suche entfernt.
_ABNAHME_GUT = re.compile(r"\b(?:ABGENOMMEN|BESTANDEN|GRÜN|BELEGT|FREIGEGEBEN)(?:e|en|er|es|em)?\b", re.I)
_ABNAHME_SCHLECHT = re.compile(r"\b(?:ABGELEHNT|ROT\b(?!-)|NICHT|FEHLT(?:EN|T)?)\b|\bOFFEN\b", re.I)
_OFFEN_VERNEINT = re.compile(r"\b(?:0|null|nichts(?: mehr)?|keine(?: \w+)?|nix)\s+OFFEN\b", re.I)
_XY = re.compile(r"\b(\d+)\s*/\s*(\d+)\s+(?:Tests?|grün|bestanden|belegt|Kriterien|Abnahme)", re.I)
_KLICKWEG = re.compile(r"klick.?weg|live-klick|live-beweis", re.I)
_VERNEINT = re.compile(r"\b(?:kein|keine|keiner|nicht|entfällt|n/a)\b", re.I)
#: Ausdrücklich „Klickweg entfällt / nicht nötig“ (reines Backend) — eigenes Merkmal, kein Klickweg-Beleg.
#: Die Aussage muss direkt am Wort „Klickweg“ hängen (nicht irgendwo in der Zeile) und darf nicht
#: verneint sein: „Klickweg nicht entfällt“, „Klickweg entfällt nicht“ zählen nicht (#282 P3).
#: Zwischen Wort und Aussage erlaubt: Gedankenstrich, Tabellen-Trenner, ein Klammerzusatz
#: („Klickweg (Nutzer-Ablauf) entfällt“, „| Klickweg | entfällt |“). „Live-Beweis“ nur mit
#: „entfällt“ („Live-Beweis nicht nötig für Rot-Test“ meint nicht den Klickweg, #282 P2).
_ZWISCHEN = r"(?:[\s:*_|—–-]|\([^)\n]*\))*"
_ENTFAELLT = re.compile(
    rf"klick.?weg{_ZWISCHEN}(?:entfällt(?!\s+nicht)|nicht\s+(?:nötig|erforderlich)|n/a\b)"
    rf"|live-beweis{_ZWISCHEN}entfällt(?!\s+nicht)"
    r"|\bkein\w*\s+klick.?weg\s+(?:nötig|erforderlich)",
    re.I,
)
#: Im Zweifel nicht entfällt (#282 P2): hinter der Aussage zählt nur eine erlaubte Fortsetzung,
#: alles andere heißt „zählt nicht“ — lieber bleibt eine Session offen, als dass sie zu früh endet.
#: Erlaubt (bis zum nächsten Tabellen-Trenner „|“): Zeilenende, ggf. mit Punkt; ein Klammerzusatz;
#: „:“ / „—“ / „-“ / „,“ mit kurzem Grund („, keine Oberfläche“, „: nur Doku“). Fragezeichen,
#: schließende Anführungszeichen („Behauptung „Klickweg entfällt“ ist falsch“), Durchgestrichenes
#: („~~…~~“) oder Text ohne Trennzeichen („entfällt nur auf Staging“) zählen nicht.
_FORTSETZUNG = re.compile(
    r"[*_]*[ \t]*(?:\([^()\n]*\))?[ \t]*(?:[:—–,-][ \t]*[^\n]{1,80}?)?[ \t]*\.?[ \t]*"
)
#: Klammerzusatz und Grund dürfen nichts verneinen oder einfordern; „kein UI / keine Oberfläche“
#: ist ein Grund, keine Verneinung. „nur“ allein ist erlaubt („nur Doku“).
_DAGEGEN = re.compile(
    r"\b(?:nicht|nie|nein|kein\w*(?!\s+(?:Oberfläche|UI|Frontend)\b)|nötig|erforderlich|pflicht\w*"
    r"|trotzdem|aber|doch|jedoch|falsch|erst)\b|klick.?weg",
    re.I,
)


def _entfaellt(zeile: str) -> bool:
    """Sagt die Zeile ausdrücklich und ohne Einschränkung, dass der Klickweg entfällt?"""
    treffer = _ENTFAELLT.search(zeile)
    if treffer is None:
        return False
    rest = zeile[treffer.end() :].split("|", 1)[0].rstrip("\r\n")
    return _FORTSETZUNG.fullmatch(rest) is not None and not _DAGEGEN.search(rest)


@dataclass
class Lauf:
    """Ein Testlauf: Zählung + Beleg (Befehl, Ausgabezeile, Datei)."""

    passed: int
    failed: int
    zeile: str
    befehl: str
    datei: str
    #: Wann der Beleg entstand (Unix-Zeit, :func:`zeitpunkt`); 0 = unbekannt.
    zeit: float = 0.0


@dataclass
class Belege:
    """Was die Belegseiten eines Tickets aussagen (Felder s. Modul-Doku)."""

    status: str = "offen"  # "gruen" | "rot" | "offen"
    lauf: Lauf | None = None
    abnahme: str = ""
    klickweg: bool = False
    #: Beleg sagt ausdrücklich, dass der Klickweg entfällt; ``klickweg`` bleibt dann ``False``
    #: (Anzeige „kein Klickweg“ unverändert), der Wiederanlauf zählt es als erfüllt (#282).
    klickweg_entfaellt: bool = False
    rot_beweis: bool = False
    #: Älterer, größerer roter Lauf, den der neueste grüne Lauf nicht abdeckt (Status ``offen``).
    verdeckt: Lauf | None = None
    #: Belegdatei, aus der die ``ABNAHME:``-Zeile stammt.
    abnahme_datei: str = ""
    dateien: list[str] = field(default_factory=list)

    @property
    def hinweis(self) -> str:
        """Was im Fall ``offen`` wegen ``verdeckt`` fehlt, in Alltagsworten; sonst leer.

        Die eine Textquelle für Übersicht und Wiederanlauf.
        """
        if self.status != "offen" or self.verdeckt is None:
            return ""
        v = self.verdeckt
        return f"grüner Testlauf mit mindestens {v.passed + v.failed} Tests (so viele wie der rote Lauf {v.datei})"


def _lies(datei: Path) -> str:
    try:
        if datei.stat().st_size > MAX_BYTES:
            return ""
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Belegdatei nicht lesbar %s: %s", datei, fehler)
        return ""


def finde_dateien(verify_hard: Path, nummer: str) -> list[Path]:
    """Alle .md/.txt-Belegdateien eines Tickets (Datei ``<N>…`` oder Ordner ``[<datum>_]<N>…``)."""
    muster = re.compile(rf"^(?:\d{{4}}-\d{{2}}-\d{{2}}_)?{re.escape(nummer)}(?=$|[_.\-])")
    treffer: list[Path] = []
    if not verify_hard.is_dir():
        return treffer
    for eintrag in sorted(verify_hard.iterdir()):
        if not muster.match(eintrag.name):
            continue
        kandidaten = sorted(eintrag.rglob("*")) if eintrag.is_dir() else [eintrag]
        treffer += [k for k in kandidaten if k.is_file() and k.suffix.lower() in (".md", ".txt")]
    return treffer


def zeitpunkt(datei: Path) -> float:
    """Wann eine Belegdatei entstand: letzte Commit-Zeit, unversioniert die Änderungszeit.

    Die Änderungszeit allein taugt nicht: ein Checkout setzt sie für alle Dateien gleich.
    """
    # Bekannte Grenze: Eine schon committete Belegdatei, die danach lokal überschrieben und
    # noch nicht neu committet wurde, zählt mit ihrer alten Commit-Zeit, nicht mit „jetzt“.
    try:
        aus = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", datei.name],
            cwd=datei.parent,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if aus.returncode == 0 and aus.stdout.strip():
            return float(aus.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError) as fehler:
        log.debug("Commit-Zeit nicht lesbar %s: %s", datei, fehler)
    try:
        return datei.stat().st_mtime
    except OSError:
        return 0.0


def lauf_aus_text(text: str, datei: str, nur_saubere_zeilen: bool = False) -> Lauf | None:
    """Letzte Zusammenfassungs-Zeile (``N passed``/``N failed``) eines Textes als Lauf.

    Stehen in einer Zeile mehrere ``N passed`` („54 passed vor Fix, danach 71 passed“), zählt
    die letzte.

    ``nur_saubere_zeilen`` (Markdown): nur Zeilen ohne ``failed``/``rot``/``→`` — dort stehen
    oft Vorher-Nachher-Übergänge, die nichts über den Endstand sagen; dann zählt der größte Lauf.
    """
    befehl_m = _BEFEHL.search(text)
    befehl = befehl_m.group(0).strip()[:200] if befehl_m else ""
    beste: Lauf | None = None
    for zeile in text.splitlines():
        p = _PASSED.findall(zeile)
        f = _FAILED.search(zeile)
        if not p and not f:
            continue
        if nur_saubere_zeilen and (f or "→" in zeile or re.search(r"\brot\b", zeile, re.I)):
            continue
        lauf = Lauf(
            passed=int(p[-1]) if p else 0,
            failed=sum(int(x) for x in _FAILED.findall(zeile)),
            zeile=zeile.strip(" =|\t")[:200],
            befehl=befehl,
            datei=datei,
        )
        if not nur_saubere_zeilen or beste is None or lauf.passed >= beste.passed:
            beste = lauf
    return beste


def auswerten(verify_hard: Path, nummer: str) -> Belege:
    """Belege eines Tickets aus seinen Belegdateien; ohne Dateien alles leer, Status ``offen``."""
    belege = Belege()
    laeufe: list[Lauf] = []
    for datei in finde_dateien(verify_hard, nummer):
        rel = datei.relative_to(verify_hard).as_posix()
        belege.dateien.append(rel)
        text = _lies(datei)
        if _ROT_DATEI.search(rel):
            belege.rot_beweis = True
            continue
        if datei.suffix.lower() == ".txt":
            lauf = lauf_aus_text(text, rel)
        else:
            lauf = None
            if not belege.abnahme and (m := _ABNAHME.search(text)):
                belege.abnahme = m.group(1).strip()
                belege.abnahme_datei = rel
            for zeile in text.splitlines():
                if not _KLICKWEG.search(zeile):
                    continue
                if not _VERNEINT.search(zeile):
                    belege.klickweg = True
                    break
                if _entfaellt(zeile):
                    belege.klickweg_entfaellt = True
        if lauf:
            lauf.zeit = zeitpunkt(datei)
            laeufe.append(lauf)
    if not laeufe:  # Fallback: Zählung aus den Markdown-Belegseiten
        for rel in belege.dateien:
            if rel.lower().endswith(".md") and not _ROT_DATEI.search(rel):
                text = _lies(verify_hard / rel)
                lauf = lauf_aus_text(text, rel, nur_saubere_zeilen=True)
                if not lauf and (xy := _XY.search(text)):
                    x, y = int(xy.group(1)), int(xy.group(2))
                    if 0 < y and x <= y:
                        lauf = Lauf(x, y - x, xy.group(0), "", rel)
                if lauf:
                    lauf.zeit = zeitpunkt(verify_hard / rel)
                    laeufe.append(lauf)
    if laeufe:  # Stand zum Zeitpunkt des Aufrufs: der neueste Lauf zählt, nicht der größte (#366)
        belege.lauf = max(laeufe, key=lambda l: (l.zeit, l.passed + l.failed, l.datei))
        if belege.lauf.failed == 0:  # im Zweifel offen statt grün (#366 W1)
            groesse = belege.lauf.passed
            # Ein roter Lauf zählt nur, solange ihn kein späterer, mindestens gleich großer grüner Lauf ablöst (#366 P1).
            rote = [
                l
                for l in laeufe
                if l.failed > 0
                and l.passed + l.failed > groesse
                and not any(g.failed == 0 and g.zeit > l.zeit and g.passed >= l.passed + l.failed for g in laeufe)
            ]
            belege.verdeckt = max(rote, key=lambda l: (l.passed + l.failed, l.zeit), default=None)
    abnahme_schlecht = bool(belege.abnahme) and bool(_ABNAHME_SCHLECHT.search(_OFFEN_VERNEINT.sub("", belege.abnahme)))
    abnahme_gut = bool(belege.abnahme) and bool(_ABNAHME_GUT.search(belege.abnahme))
    if (belege.lauf and belege.lauf.failed > 0) or abnahme_schlecht:
        belege.status = "rot"
    elif belege.verdeckt:
        belege.status = "offen"
    elif belege.lauf or abnahme_gut:
        belege.status = "gruen"
    return belege
