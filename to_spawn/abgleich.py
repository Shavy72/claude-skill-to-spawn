"""Abgleich-Befehl für ``~/.claude`` zwischen zwei Rechnern (#561).

Vergleicht eine feste Liste von Dateien und ``settings.json``-Schlüsseln unter ``~/.claude``
zwischen zwei Verzeichnissen (Quelle → Ziel):

- **Standard ist nur lesen**: zeigt Unterschiede und ändert keine Datei.
- **Liste „gewollt verschieden“** an genau einer Stelle (:data:`GEWOLLT_VERSCHIEDEN`):
  rechnergebundene Pfade, Rechte und Skills-Whitelist werden nicht gemeldet und beim
  Angleichen nicht überschrieben.
- **Angleichen nur auf ausdrücklichen Schalter** (``--angleichen``): sichert jede
  überschriebene Datei (``<datei>.bak-<Stempel>``), prüft ``settings.json`` vor jedem
  Schreibvorgang auf Gültigkeit und schreibt sie atomar über
  :func:`to_spawn.manifest.schreibe_atomar`. Bei ungültiger ``settings.json`` bleibt
  alles unverändert.
- **Keine Geheimnisse in der Ausgabe**: Schlüssel unter ``env`` (und weitere
  geheimnisverdächtige Schlüssel) erscheinen nur als Name, nie mit ihrem Wert.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import manifest
from .pruef import regeln

log = logging.getLogger("to_spawn.abgleich")

BEFEHLE: tuple[str, ...] = ("abgleich",)
SETTINGS_DATEI = "settings.json"

#: Exit-Codes der CLI (wie ``einrichten --pruefen``: 0 = gleich/erledigt, 3 = Abweichung).
EXIT_OK = 0
EXIT_FEHLER = 1
EXIT_AUFRUF = 2
EXIT_ABWEICHUNG = 3

#: Feste Liste der zu vergleichenden relativen Pfade unter ``~/.claude`` (POSIX-Trenner).
#: ``settings.json`` wird strukturell nach Schlüsseln verglichen; übrige Dateien nach Inhalt.
PRUEF_DATEIEN: tuple[str, ...] = (
    SETTINGS_DATEI,
    "hooks/filter-test-output.sh",
    "hooks/smart-zone/meter.mjs",
    "hooks/smart-zone/lib.mjs",
    "hooks/smart-zone/kopien-check.mjs",
    "hooks/smart-zone/kontext-stand.mjs",
)


@dataclass(frozen=True)
class Ausnahme:
    """Ein Eintrag in der Liste „gewollt verschieden“."""

    muster: str
    grund: str = ""


#: Die eine Stelle für „gewollt verschieden“ (#561): Dateien und ``settings.json``-Schlüssel,
#: die zwischen PC und Bau-Server absichtlich abweichen dürfen.
GEWOLLT_VERSCHIEDEN: tuple[Ausnahme, ...] = (
    Ausnahme("settings.local.json", "lokale Überschreibungen je Rechner"),
    Ausnahme("settings.json:permissions", "Rechte bleiben Sache des Nutzers je Rechner"),
    Ausnahme("settings.json:skillsWhitelist", "Skills-Whitelist auf dem Server"),
    Ausnahme("settings.json:skills.whitelist", "Skills-Whitelist auf dem Server"),
    Ausnahme("settings.json:statusLine", "rechnergebundener Statuszeilen-Befehl"),
    Ausnahme("settings.json:apiKeyHelper", "rechnergebundener Schlüssel-Helfer"),
    Ausnahme("settings.json:awsAuthRefresh", "rechnergebundener Anmelde-Befehl"),
    Ausnahme("settings.json:env.HOME", "rechnergebundener Home-Pfad"),
    Ausnahme("settings.json:env.USERPROFILE", "rechnergebundener Windows-Home-Pfad"),
    Ausnahme("settings.json:env.PATH", "rechnergebundener Suchpfad"),
    Ausnahme("settings.json:env.TMPDIR", "rechnergebundener Temp-Pfad"),
    Ausnahme("settings.json:env.TEMP", "rechnergebundener Windows-Temp-Pfad"),
    Ausnahme("settings.json:env.TMP", "rechnergebundener Temp-Pfad"),
    Ausnahme("settings.json:env.CLAUDE_CONFIG_DIR", "rechnergebundener Claude-Ordner"),
    Ausnahme("settings.json:env.*_PATH", "rechnergebundene Pfad-Variable"),
    Ausnahme("settings.json:env.*_DIR", "rechnergebundene Ordner-Variable"),
)

#: Schlüssel-Bruchstücke, deren Werte nie ausgegeben werden (zusätzlich zu allem unter ``env``).
_GEHEIM_WOERTER: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "passwort",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "auth",
    "zugang",
)

_ART_TEXT: dict[str, str] = {
    "fehlt_ziel": "fehlt im Ziel",
    "fehlt_quelle": "fehlt in der Quelle",
    "abweichung": "weicht ab",
}


class AbgleichFehler(ValueError):
    """Unzulässiger Pfad, fehlender Ordner oder ungültige ``settings.json``."""


@dataclass(frozen=True)
class Abweichung:
    """Ein gemeldeter Unterschied zwischen Quelle und Ziel — speichert nie Geheimnis-Werte."""

    pfad: str
    art: str  # "fehlt_ziel" | "fehlt_quelle" | "abweichung"
    schluessel: str | None = None
    ist_geheim: bool = False

    @property
    def kennung(self) -> str:
        return f"{self.pfad}:{self.schluessel}" if self.schluessel else self.pfad

    def zeile(self) -> str:
        beschreibung = _ART_TEXT.get(self.art, self.art)
        zusatz = " (Wert verborgen)" if self.ist_geheim else ""
        return f"- {self.kennung}: {beschreibung}{zusatz}"

    def als_dict(self) -> dict[str, Any]:
        return {
            "kennung": self.kennung,
            "pfad": self.pfad,
            "schluessel": self.schluessel,
            "art": self.art,
            "wert_verborgen": self.ist_geheim,
        }


@dataclass(frozen=True)
class VergleichErgebnis:
    """Ergebnis eines reinen Lese-Vergleichs zwischen Quelle und Ziel."""

    quelle: Path
    ziel: Path
    abweichungen: tuple[Abweichung, ...]
    ausgenommen: tuple[str, ...] = ()

    @property
    def gleich(self) -> bool:
        return len(self.abweichungen) == 0

    def text(self) -> str:
        if self.gleich:
            hinweis = f" ({len(self.ausgenommen)} gewollt verschieden)" if self.ausgenommen else ""
            return f"Abgleich: keine Unterschiede gefunden{hinweis}."
        zeilen = [f"Abgleich: {len(self.abweichungen)} Unterschied(e) gefunden:"]
        zeilen.extend(a.zeile() for a in self.abweichungen)
        if self.ausgenommen:
            zeilen.append(f"Ausgenommen (gewollt verschieden): {len(self.ausgenommen)}")
        return "\n".join(zeilen)

    def als_dict(self) -> dict[str, Any]:
        return {
            "gleich": self.gleich,
            "abweichungen": [a.als_dict() for a in self.abweichungen],
            "ausgenommen": list(self.ausgenommen),
        }


@dataclass(frozen=True)
class AngleichErgebnis:
    """Ergebnis eines expliziten Abgleichs (Quelle → Ziel)."""

    aktualisiert: tuple[str, ...]
    sicherungen: tuple[Path, ...]
    uebersprungen: tuple[Abweichung, ...]
    ausgenommen: tuple[str, ...]

    def text(self) -> str:
        if not self.aktualisiert and not self.uebersprungen:
            return "Angleichen: nichts zu tun — bereits synchron."
        zeilen: list[str] = []
        if self.aktualisiert:
            zeilen.append(f"Angeglichen: {len(self.aktualisiert)} Datei(en) aktualisiert:")
            zeilen.extend(f"- {pfad}" for pfad in self.aktualisiert)
        if self.sicherungen:
            zeilen.append(f"Sicherungen: {len(self.sicherungen)}")
            zeilen.extend(f"- {pfad}" for pfad in self.sicherungen)
        if self.uebersprungen:
            zeilen.append(f"Unverändert im Ziel (fehlt in der Quelle): {len(self.uebersprungen)}")
            zeilen.extend(a.zeile() for a in self.uebersprungen)
        return "\n".join(zeilen)

    def als_dict(self) -> dict[str, Any]:
        return {
            "aktualisiert": list(self.aktualisiert),
            "sicherungen": [str(p) for p in self.sicherungen],
            "uebersprungen": [a.als_dict() for a in self.uebersprungen],
            "ausgenommen": list(self.ausgenommen),
        }


def normalisiere_pfad(pfad: str) -> str:
    """Relative Datei unter ``~/.claude`` auf POSIX-Form bringen; Ausbrüche ablehnen."""
    sauber = regeln.normal(pfad.strip())
    if not sauber or sauber.startswith(("/", "~")):
        raise AbgleichFehler(f"Unzulässiger relativer Pfad: {pfad!r}")
    teile = [t for t in sauber.split("/") if t]
    if not teile or ":" in teile[0] or any(t in (".", "..") for t in teile):
        raise AbgleichFehler(f"Unzulässiger relativer Pfad: {pfad!r}")
    return "/".join(teile)


def ist_geheim_schluessel(schluessel: str) -> bool:
    """``True`` für jeden Schlüssel unter ``env`` oder mit geheimnisverdächtigem Namen."""
    for teil in schluessel.split("."):
        klein = teil.lower()
        if klein == "env" or any(wort in klein for wort in _GEHEIM_WOERTER):
            return True
    return False


def _muster_text(eintrag: str | Ausnahme) -> str:
    roh = eintrag.muster if isinstance(eintrag, Ausnahme) else eintrag
    roh = roh.strip()
    if ":" in roh:
        datei, rest = roh.split(":", 1)
        return f"{regeln.normal(datei)}:{rest}"
    return regeln.normal(roh)


def ist_gewollt_verschieden(kennung: str, ausnahmen: Sequence[str | Ausnahme] = GEWOLLT_VERSCHIEDEN) -> bool:
    """Prüft, ob Datei-Pfad oder ``settings.json:<Schlüssel>`` in „gewollt verschieden“ steht."""
    norm_kennung = _muster_text(kennung)
    for eintrag in ausnahmen:
        muster = _muster_text(eintrag)
        if not muster:
            continue
        if norm_kennung == muster or fnmatch.fnmatchcase(norm_kennung, muster):
            return True
        if ":" in muster and norm_kennung.startswith(muster + "."):
            return True
        if ":" not in muster:
            basis = muster.rstrip("/")
            if norm_kennung.startswith((basis + ":", basis + "/")):
                return True
    return False


def lade_ausnahmen(datei: Path) -> tuple[Ausnahme, ...]:
    """Zusätzliche Ausnahmen aus einer JSON- oder Textdatei (eine Zeile je Muster) laden."""
    if not datei.is_file():
        raise AbgleichFehler(f"Ausnahmen-Datei fehlt: {datei}")
    try:
        inhalt = datei.read_text(encoding="utf-8")
    except OSError as fehler:
        raise AbgleichFehler(f"Ausnahmen-Datei nicht lesbar ({datei}): {fehler}") from fehler
    if datei.suffix.lower() == ".json":
        try:
            roh = json.loads(inhalt)
        except ValueError as fehler:
            raise AbgleichFehler(f"Ausnahmen-Datei ungültiges JSON ({datei}): {fehler}") from fehler
        if isinstance(roh, dict):
            roh = roh.get("gewollt_verschieden")
        if not isinstance(roh, list):
            raise AbgleichFehler(
                f"Ausnahmen-Datei ({datei}) muss eine Liste oder {{'gewollt_verschieden': [...]}} sein."
            )
        ergebnis: list[Ausnahme] = []
        for eintrag in roh:
            if isinstance(eintrag, str) and eintrag.strip():
                ergebnis.append(Ausnahme(eintrag.strip()))
            elif isinstance(eintrag, dict) and isinstance(eintrag.get("muster"), str) and eintrag["muster"].strip():
                ergebnis.append(Ausnahme(eintrag["muster"].strip(), str(eintrag.get("grund") or "")))
            else:
                raise AbgleichFehler(f"Ungültiger Eintrag in Ausnahmen-Datei ({datei}): {eintrag!r}")
        return tuple(ergebnis)
    ergebnis_zeilen: list[Ausnahme] = []
    for zeile in inhalt.splitlines():
        sauber = zeile.split("#", 1)[0].strip()
        if sauber:
            ergebnis_zeilen.append(Ausnahme(sauber))
    return tuple(ergebnis_zeilen)


def _pfad_im_ordner(basis: Path, rel: str) -> Path:
    """Löst ``rel`` innerhalb von ``basis`` auf und verhindert Symlink-Ausbrüche."""
    ziel = basis.joinpath(*rel.split("/"))
    basis_echt = basis.resolve()
    if ziel.is_symlink() or ziel.exists():
        try:
            ziel_echt = ziel.resolve()
        except OSError as fehler:
            raise AbgleichFehler(f"Pfad nicht auflösbar ({ziel}): {fehler}") from fehler
        if not ziel_echt.is_relative_to(basis_echt):
            raise AbgleichFehler(f"Pfad zeigt außerhalb von {basis}: {rel}")
    return ziel


def _validiere_settings_objekt(daten: Any, quelle_name: str) -> dict[str, Any]:
    """Prüft Struktur-Regeln einer ``settings.json`` (Objekt, gültige ``env``/``hooks``/``enabledPlugins``)."""
    if not isinstance(daten, dict):
        raise AbgleichFehler(f"{quelle_name}: kein JSON-Objekt.")
    for feld in ("env", "enabledPlugins"):
        if feld in daten and not isinstance(daten[feld], dict):
            raise AbgleichFehler(f"{quelle_name}: Feld '{feld}' muss ein JSON-Objekt sein.")
    if "hooks" in daten:
        hooks = daten["hooks"]
        if not isinstance(hooks, dict):
            raise AbgleichFehler(f"{quelle_name}: Feld 'hooks' muss ein JSON-Objekt sein.")
        for ereignis, bloecke in hooks.items():
            if not isinstance(bloecke, list):
                raise AbgleichFehler(f"{quelle_name}: Feld 'hooks.{ereignis}' muss eine JSON-Liste sein.")
    return daten


def pruefe_settings_datei(datei: Path) -> dict[str, Any]:
    """Liest ``settings.json`` und prüft UTF-8, JSON-Syntax und Grundstruktur."""
    try:
        roh = datei.read_bytes()
    except OSError as fehler:
        raise AbgleichFehler(f"settings.json nicht lesbar ({datei}): {fehler}") from fehler
    if b"\x00" in roh:
        raise AbgleichFehler(f"settings.json enthält NUL-Bytes ({datei}).")
    try:
        text = roh.decode("utf-8")
        daten = json.loads(text)
    except (UnicodeDecodeError, ValueError) as fehler:
        raise AbgleichFehler(f"settings.json ist kein gültiges JSON ({datei}): {fehler}") from fehler
    return _validiere_settings_objekt(daten, str(datei))


def _vergleiche_objekte(
    links: dict[str, Any],
    rechts: dict[str, Any],
    ausnahmen: Sequence[str | Ausnahme],
    pfad: str = "",
) -> tuple[list[Abweichung], list[str]]:
    """Vergleicht zwei ``settings.json``-Objekte rekursiv auf Schlüssel-Ebene."""
    abweichungen: list[Abweichung] = []
    ausgenommen: list[str] = []
    for schluessel in sorted(set(links) | set(rechts)):
        sub = f"{pfad}.{schluessel}" if pfad else schluessel
        kennung = f"{SETTINGS_DATEI}:{sub}"
        if ist_gewollt_verschieden(kennung, ausnahmen):
            ausgenommen.append(kennung)
            continue
        in_l = schluessel in links
        in_r = schluessel in rechts
        v_l = links.get(schluessel)
        v_r = rechts.get(schluessel)
        if in_l and in_r:
            if isinstance(v_l, dict) and isinstance(v_r, dict):
                if v_l or v_r:
                    unter_abw, unter_aus = _vergleiche_objekte(v_l, v_r, ausnahmen, sub)
                    abweichungen.extend(unter_abw)
                    ausgenommen.extend(unter_aus)
            elif v_l != v_r:
                abweichungen.append(
                    Abweichung(
                        pfad=SETTINGS_DATEI,
                        art="abweichung",
                        schluessel=sub,
                        ist_geheim=ist_geheim_schluessel(sub),
                    )
                )
        elif in_l:
            if isinstance(v_l, dict) and v_l:
                unter_abw, unter_aus = _vergleiche_objekte(v_l, {}, ausnahmen, sub)
                abweichungen.extend(unter_abw)
                ausgenommen.extend(unter_aus)
            else:
                abweichungen.append(
                    Abweichung(
                        pfad=SETTINGS_DATEI,
                        art="fehlt_ziel",
                        schluessel=sub,
                        ist_geheim=ist_geheim_schluessel(sub),
                    )
                )
        else:
            if isinstance(v_r, dict) and v_r:
                unter_abw, unter_aus = _vergleiche_objekte({}, v_r, ausnahmen, sub)
                abweichungen.extend(unter_abw)
                ausgenommen.extend(unter_aus)
            else:
                abweichungen.append(
                    Abweichung(
                        pfad=SETTINGS_DATEI,
                        art="fehlt_quelle",
                        schluessel=sub,
                        ist_geheim=ist_geheim_schluessel(sub),
                    )
                )
    return abweichungen, ausgenommen


def _mische_settings(
    quelle_daten: dict[str, Any],
    ziel_daten: dict[str, Any],
    ausnahmen: Sequence[str | Ausnahme],
    pfad: str = "",
) -> dict[str, Any]:
    """Übernimmt nicht-ausgenommene Schlüssel aus ``quelle_daten`` in ``ziel_daten``."""
    ergebnis = copy.deepcopy(ziel_daten)
    for schluessel, wert in quelle_daten.items():
        sub = f"{pfad}.{schluessel}" if pfad else schluessel
        kennung = f"{SETTINGS_DATEI}:{sub}"
        if ist_gewollt_verschieden(kennung, ausnahmen):
            continue
        if isinstance(wert, dict):
            ziel_unter = ergebnis.get(schluessel)
            if isinstance(ziel_unter, dict):
                ergebnis[schluessel] = _mische_settings(wert, ziel_unter, ausnahmen, sub)
            elif not wert:
                ergebnis[schluessel] = {}
            else:
                gefiltert = _mische_settings(wert, {}, ausnahmen, sub)
                if gefiltert:
                    ergebnis[schluessel] = gefiltert
        else:
            ergebnis[schluessel] = copy.deepcopy(wert)
    return ergebnis


def _pruefe_ordner(ordner: Path, rolle: str) -> None:
    if not ordner.is_dir():
        raise AbgleichFehler(f"{rolle}-Ordner fehlt oder ist kein Verzeichnis: {ordner}")


def vergleichen(
    quelle: Path,
    ziel: Path,
    *,
    dateien: Sequence[str] = PRUEF_DATEIEN,
    gewollt_verschieden: Sequence[str | Ausnahme] = GEWOLLT_VERSCHIEDEN,
) -> VergleichErgebnis:
    """Vergleicht Quelle und Ziel rein lesend und meldet alle nicht ausgenommenen Unterschiede."""
    _pruefe_ordner(quelle, "Quelle")
    _pruefe_ordner(ziel, "Ziel")

    norm_dateien: list[str] = []
    gesehen: set[str] = set()
    for eintrag in dateien:
        rel = normalisiere_pfad(eintrag)
        if rel not in gesehen:
            gesehen.add(rel)
            norm_dateien.append(rel)

    abweichungen: list[Abweichung] = []
    ausgenommen: list[str] = []

    for rel in norm_dateien:
        if ist_gewollt_verschieden(rel, gewollt_verschieden):
            ausgenommen.append(rel)
            continue
        q_pfad = _pfad_im_ordner(quelle, rel)
        z_pfad = _pfad_im_ordner(ziel, rel)
        q_da = q_pfad.is_file()
        z_da = z_pfad.is_file()
        if not q_da and not z_da:
            continue
        if rel == SETTINGS_DATEI:
            q_daten = pruefe_settings_datei(q_pfad) if q_da else {}
            z_daten = pruefe_settings_datei(z_pfad) if z_da else {}
            s_abw, s_aus = _vergleiche_objekte(q_daten, z_daten, gewollt_verschieden)
            abweichungen.extend(s_abw)
            ausgenommen.extend(s_aus)
            continue
        if q_da and not z_da:
            abweichungen.append(Abweichung(pfad=rel, art="fehlt_ziel"))
        elif z_da and not q_da:
            abweichungen.append(Abweichung(pfad=rel, art="fehlt_quelle"))
        else:
            try:
                q_bytes = q_pfad.read_bytes()
                z_bytes = z_pfad.read_bytes()
            except OSError as fehler:
                raise AbgleichFehler(f"Datei nicht lesbar ({rel}): {fehler}") from fehler
            if q_bytes != z_bytes:
                abweichungen.append(Abweichung(pfad=rel, art="abweichung"))

    log.info(
        "Abgleich gelesen: %d Abweichung(en), %d ausgenommen.",
        len(abweichungen),
        len(ausgenommen),
    )
    return VergleichErgebnis(
        quelle=quelle,
        ziel=ziel,
        abweichungen=tuple(abweichungen),
        ausgenommen=tuple(ausgenommen),
    )


def _sicherung_anlegen(datei: Path, stempel: str) -> Path:
    """Legt eine eindeutige Sicherungskopie neben ``datei`` an (``<name>.bak-<stempel>``)."""
    kandidat = datei.with_name(f"{datei.name}.bak-{stempel}")
    zaehler = 1
    while kandidat.exists():
        kandidat = datei.with_name(f"{datei.name}.bak-{stempel}-{zaehler}")
        zaehler += 1
    shutil.copy2(datei, kandidat)
    return kandidat


def _schreibe_bytes_atomar(ziel: Path, inhalt: bytes, vorlage: Path | None = None) -> None:
    """Schreibt Rohdaten über eine Zwischendatei im selben Ordner + ``os.replace``."""
    ziel.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=ziel.parent, prefix=f".{ziel.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as strom:
            strom.write(inhalt)
            strom.flush()
            os.fsync(strom.fileno())
        if vorlage is not None:
            try:
                modus = stat.S_IMODE(vorlage.stat().st_mode)
                os.chmod(tmp, modus)
            except OSError as fehler:
                log.debug("Dateirechte nicht übernommen (%s): %s", ziel, fehler)
        os.replace(tmp, ziel)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def angleichen(
    quelle: Path,
    ziel: Path,
    *,
    dateien: Sequence[str] = PRUEF_DATEIEN,
    gewollt_verschieden: Sequence[str | Ausnahme] = GEWOLLT_VERSCHIEDEN,
    zeit: str | None = None,
) -> AngleichErgebnis:
    """Übernimmt Unterschiede von Quelle nach Ziel: je Datei mit Sicherung, ``settings.json`` atomar.

    Vor jedem Schreibvorgang werden alle Pfade und ``settings.json`` (Quelle, Ziel und
    Mischergebnis) vollständig geprüft — bei einem Fehler bleibt das Ziel komplett unverändert.
    """
    vergleich = vergleichen(quelle, ziel, dateien=dateien, gewollt_verschieden=gewollt_verschieden)
    stempel = zeit or datetime.now().strftime("%Y%m%d-%H%M%S")

    # Vorab-Prüfung und Planung im Speicher: erst nach vollständiger Gültigkeit wird geschrieben.
    settings_abw = [
        a for a in vergleich.abweichungen if a.pfad == SETTINGS_DATEI and a.art in ("fehlt_ziel", "abweichung")
    ]
    gemergte_settings: dict[str, Any] | None = None
    if settings_abw:
        q_settings = _pfad_im_ordner(quelle, SETTINGS_DATEI)
        z_settings = _pfad_im_ordner(ziel, SETTINGS_DATEI)
        q_daten = pruefe_settings_datei(q_settings)
        z_daten = pruefe_settings_datei(z_settings) if z_settings.is_file() else {}
        gemergt = _mische_settings(q_daten, z_daten, gewollt_verschieden)
        _validiere_settings_objekt(gemergt, f"{z_settings} (gemischt)")
        # Gegenprobe durch Serialisieren + erneut Parsen vor dem Schreibvorgang.
        try:
            json.loads(json.dumps(gemergt, ensure_ascii=False))
        except (TypeError, ValueError) as fehler:
            raise AbgleichFehler(f"Gemischte settings.json nicht serialisierbar: {fehler}") from fehler
        gemergte_settings = gemergt

    datei_kopien: list[tuple[str, Path, Path, bytes]] = []
    for abw in vergleich.abweichungen:
        if abw.pfad == SETTINGS_DATEI or abw.art not in ("fehlt_ziel", "abweichung"):
            continue
        q_pfad = _pfad_im_ordner(quelle, abw.pfad)
        z_pfad = _pfad_im_ordner(ziel, abw.pfad)
        try:
            roh = q_pfad.read_bytes()
        except OSError as fehler:
            raise AbgleichFehler(f"Quelldatei nicht lesbar ({abw.pfad}): {fehler}") from fehler
        datei_kopien.append((abw.pfad, q_pfad, z_pfad, roh))

    uebersprungen = tuple(a for a in vergleich.abweichungen if a.art == "fehlt_quelle")
    aktualisiert: list[str] = []
    sicherungen: list[Path] = []

    if gemergte_settings is not None:
        z_settings = _pfad_im_ordner(ziel, SETTINGS_DATEI)
        z_settings.parent.mkdir(parents=True, exist_ok=True)
        if z_settings.is_file():
            sicherungen.append(_sicherung_anlegen(z_settings, stempel))
        manifest.schreibe_atomar(z_settings, gemergte_settings)
        aktualisiert.append(SETTINGS_DATEI)
        log.info("Angeglichen (atomar): %s", z_settings)

    for rel, q_pfad, z_pfad, roh in datei_kopien:
        if z_pfad.is_file():
            sicherungen.append(_sicherung_anlegen(z_pfad, stempel))
        _schreibe_bytes_atomar(z_pfad, roh, vorlage=q_pfad)
        aktualisiert.append(rel)
        log.info("Angeglichen: %s", z_pfad)

    return AngleichErgebnis(
        aktualisiert=tuple(aktualisiert),
        sicherungen=tuple(sicherungen),
        uebersprungen=uebersprungen,
        ausgenommen=vergleich.ausgenommen,
    )


def formatiere_ausnahmen(ausnahmen: Sequence[Ausnahme] = GEWOLLT_VERSCHIEDEN) -> str:
    """Menschenlesbare Übersicht der einen Liste „gewollt verschieden“."""
    zeilen = ["Liste „gewollt verschieden“:"]
    for eintrag in ausnahmen:
        grund = f" — {eintrag.grund}" if eintrag.grund else ""
        zeilen.append(f"- {eintrag.muster}{grund}")
    return "\n".join(zeilen)


def richte_parser_ein(unter: Any) -> None:
    """Unterbefehl ``abgleich`` an ``unter`` (``add_subparsers``-Objekt) hängen."""
    p = unter.add_parser(
        "abgleich",
        help="~/.claude zwischen zwei Verzeichnissen vergleichen (Standard: nur lesen) (#561)",
    )
    p.add_argument("quelle", type=Path, nargs="?", help="Quell-Verzeichnis (z. B. PC-~/.claude)")
    p.add_argument("ziel", type=Path, nargs="?", help="Ziel-Verzeichnis (z. B. Server-~/.claude)")
    p.add_argument(
        "--angleichen",
        action="store_true",
        help="Unterschiede von Quelle nach Ziel übernehmen (je Datei mit Sicherung, settings.json atomar)",
    )
    p.add_argument(
        "--datei",
        action="append",
        default=[],
        metavar="PFAD",
        help="nur diese relative Datei prüfen (mehrfach); Vorgabe: feste Prüfliste",
    )
    p.add_argument(
        "--ausnahme",
        action="append",
        default=[],
        metavar="MUSTER",
        help="zusätzliches Muster für „gewollt verschieden“ (mehrfach)",
    )
    p.add_argument(
        "--ausnahmen-datei",
        type=Path,
        default=None,
        metavar="DATEI",
        help="zusätzliche Ausnahmen aus JSON- oder Textdatei laden",
    )
    p.add_argument(
        "--liste-ausnahmen",
        action="store_true",
        help="feste Liste „gewollt verschieden“ ausgeben und beenden",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Ergebnis als JSON ausgeben (ohne Geheimnisse)",
    )


def lauf(args: argparse.Namespace) -> int:
    """CLI-Einstieg für ``to_spawn.py abgleich``."""
    if args.liste_ausnahmen:
        if args.json:
            daten = [{"muster": a.muster, "grund": a.grund} for a in GEWOLLT_VERSCHIEDEN]
            sys.stdout.write(json.dumps(daten, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(formatiere_ausnahmen() + "\n")
        return EXIT_OK

    if args.quelle is None or args.ziel is None:
        sys.stderr.write("abgleich: Quelle und Ziel angeben (oder --liste-ausnahmen).\n")
        return EXIT_AUFRUF

    try:
        ausnahmen: list[str | Ausnahme] = list(GEWOLLT_VERSCHIEDEN)
        if args.ausnahmen_datei is not None:
            ausnahmen.extend(lade_ausnahmen(args.ausnahmen_datei.expanduser()))
        if args.ausnahme:
            ausnahmen.extend(args.ausnahme)
        dateien: Sequence[str] = tuple(args.datei) if args.datei else PRUEF_DATEIEN
        quelle = args.quelle.expanduser()
        ziel = args.ziel.expanduser()

        if args.angleichen:
            erg_sync = angleichen(quelle, ziel, dateien=dateien, gewollt_verschieden=ausnahmen)
            if args.json:
                sys.stdout.write(json.dumps(erg_sync.als_dict(), ensure_ascii=False, indent=2) + "\n")
            else:
                sys.stdout.write(erg_sync.text() + "\n")
            return EXIT_OK

        erg = vergleichen(quelle, ziel, dateien=dateien, gewollt_verschieden=ausnahmen)
        if args.json:
            sys.stdout.write(json.dumps(erg.als_dict(), ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(erg.text() + "\n")
        return EXIT_OK if erg.gleich else EXIT_ABWEICHUNG
    except (AbgleichFehler, OSError) as fehler:
        log.error("Abgleich fehlgeschlagen: %s", fehler)
        sys.stderr.write(f"FEHLER: {fehler}\n")
        return EXIT_FEHLER
