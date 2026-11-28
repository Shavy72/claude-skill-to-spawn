"""Fälschungssicherer Klasse-2-Beweis vom Handy per Ed25519 (RFC 8032, #312).

Gewählte Variante (a) aus dem Variantenvergleich (``docs/KLASSE_2_HANDY_BEWEIS_312.md``):
Der private Ed25519-Schlüssel liegt ausschließlich auf dem Handy des Nutzers — nie auf
der Bau-Maschine, nie im Repo. Die Maschine kennt nur den öffentlichen Schlüssel und prüft
Signaturen vollständig in reinem Python (nur Standardbibliothek, kein Subprozess, kein
``PATH``-Zugriff, identisch unter Linux und Windows).

Vertrauensanker (fail-closed, keine Umgebungsvariablen für Schlüssel):

1. System-Schlüsseldatei (Linux: ``/etc/to-spawn/klasse2_schluessel.json``, Windows:
   ``ProgramData\\to-spawn\\klasse2_schluessel.json`` über OS-API statt Env-Variable) —
   wenn vorhanden, allein maßgeblich (kann vom Besitzer-Konto nur gelesen, nicht geändert
   werden).
2. Geschützter Fern-Hauptzweig ``origin/<hauptzweig>:.to-spawn/config.json``
   (``klasse_2.oeffentliche_schluessel``), gelesen über sicheres System-``git`` —
   Änderungen an ``.to-spawn/config.json`` im Arbeitsbaum bleiben wirkungslos.
3. Lokale ``.to-spawn/config.json`` nur, wenn kein ``origin/<hauptzweig>`` existiert
   (z. B. lokales Test-Verzeichnis ohne Remote).

Ist eine Schlüssel-Quelle vorhanden, aber unlesbar oder kaputt, meldet dieses Modul
:class:`BeweisKonfigFehler` — Aufrufer sperren daraufhin fail-closed.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from to_spawn import prozessbaum

log = logging.getLogger("to_spawn.klasse2_beweis")

# --- RFC-8032-Ed25519-Konstanten ------------------------------------------------------------

ED25519_P = 2**255 - 19
ED25519_L = 2**252 + 27742317777372353535851937790883648493
_ED25519_D = (-121665 * pow(121666, ED25519_P - 2, ED25519_P)) % ED25519_P
_ED25519_I = pow(2, (ED25519_P - 1) // 4, ED25519_P)
_ED25519_IDENTITAET = (0, 1, 1, 0)

#: Domänen-Trenner vor der signierten Payload (verhindert Protokoll-Verwechslungen).
DOMAIN_V1 = b"to-spawn:klasse2:v1\n"
#: Sichtbarer Präfix des einzeiligen, Copy-Paste-sicheren Beweis-Tokens.
TOKEN_PRAEFIX = "TO-SPAWN-K2-V1"
#: Mindestlänge einer gültigen Frage-Challenge (Hex-Zeichen = 64+ Bit, Standard 32 Hex = 128 Bit).
MIN_CHALLENGE_LAENGE = 16
#: Erkennung eines eingebetteten Beweis-Tokens in Nachrichten/Texten.
_TOKEN_MUSTER = re.compile(r"(?<![A-Za-z0-9_-])TO-SPAWN-K2-V1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])")
_B64URL_ZEICHEN = re.compile(r"^[A-Za-z0-9_-]+$")
#: Test-Naht für die System-Schlüsseldatei (nur per monkeypatch in Tests, NIE über Env-Variablen).
_SYSTEM_SCHLUESSEL_DATEI: Path | None = None


class BeweisFehler(Exception):
    """Beweis-Token, Signatur oder Bindung an die Klasse-2-Frage ist ungültig."""


class BeweisKonfigFehler(Exception):
    """Schlüssel-Konfiguration ist vorhanden, aber unlesbar oder kaputt (fail-closed)."""


@dataclass(frozen=True)
class OeffentlicherSchluessel:
    """Geprüfter 32-Byte-Ed25519-Schlüssel samt kurzem Fingerabdruck."""

    roh: bytes
    kennung: str
    quelle: str = ""


@dataclass(frozen=True)
class GepruefterBeweis:
    """Kryptographisch geprüfte Nutzer-Antwort aus einem :data:`TOKEN_PRAEFIX`-Token."""

    ticket: str
    frage_id: str
    frage_sha256: str
    challenge: str
    wahl: str
    grund: str
    ts: str
    schluessel_kennung: str
    token: str


# --- Reine Python-Ed25519-Arithmetik (RFC 8032) ---------------------------------------------


def _punkt_add(p1: tuple[int, int, int, int], p2: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Vollständige Additionsformel in erweiterten Koordinaten (RFC 8032 §5.1.4)."""
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % ED25519_P
    b = ((y1 + x1) * (y2 + x2)) % ED25519_P
    c = (2 * t1 * t2 * _ED25519_D) % ED25519_P
    d = (2 * z1 * z2) % ED25519_P
    e = (b - a) % ED25519_P
    f = (d - c) % ED25519_P
    g = (d + c) % ED25519_P
    h = (b + a) % ED25519_P
    return ((e * f) % ED25519_P, (g * h) % ED25519_P, (f * g) % ED25519_P, (e * h) % ED25519_P)


def _punkt_mul(skalar: int, punkt: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Skalarmultiplikation ``skalar * punkt`` auf der Edwards-Kurve."""
    erg = _ED25519_IDENTITAET
    addend = punkt
    while skalar > 0:
        if skalar & 1:
            erg = _punkt_add(erg, addend)
        addend = _punkt_add(addend, addend)
        skalar >>= 1
    return erg


def _punkt_gleich(p1: tuple[int, int, int, int], p2: tuple[int, int, int, int]) -> bool:
    """Projektiver Vergleich zweier Kurvenpunkte ohne modulare Inversion."""
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % ED25519_P == 0 and (y1 * z2 - y2 * z1) % ED25519_P == 0


def _punkt_kodieren(punkt: tuple[int, int, int, int]) -> bytes:
    x, y, z, _ = punkt
    zinv = pow(z, ED25519_P - 2, ED25519_P)
    ax = (x * zinv) % ED25519_P
    ay = (y * zinv) % ED25519_P
    return (ay | ((ax & 1) << 255)).to_bytes(32, "little")


def _x_aus_y(y: int, vorzeichen: int) -> int | None:
    if y >= ED25519_P:
        return None
    x2 = ((y * y - 1) * pow(_ED25519_D * y * y + 1, ED25519_P - 2, ED25519_P)) % ED25519_P
    if x2 == 0:
        return None if vorzeichen else 0
    x = pow(x2, (ED25519_P + 3) // 8, ED25519_P)
    if (x * x - x2) % ED25519_P != 0:
        x = (x * _ED25519_I) % ED25519_P
    if (x * x - x2) % ED25519_P != 0:
        return None
    if (x & 1) != vorzeichen:
        x = ED25519_P - x
    return x


def _punkt_dekodieren(roh: bytes) -> tuple[int, int, int, int] | None:
    """Strenge Punkt-Dekodierung nach RFC 8032 §5.1.3 (lehnt nicht-kanonische Punkte ab)."""
    if len(roh) != 32:
        return None
    y = int.from_bytes(roh, "little") & ((1 << 255) - 1)
    vorzeichen = roh[31] >> 7
    x = _x_aus_y(y, vorzeichen)
    if x is None:
        return None
    return (x, y, 1, (x * y) % ED25519_P)


def _ist_kleinstordnung(punkt: tuple[int, int, int, int]) -> bool:
    """True für Punkte der Ordnung 1, 2, 4 oder 8 (müssen als öffentliche Schlüssel abgelehnt werden)."""
    return _punkt_gleich(_punkt_mul(8, punkt), _ED25519_IDENTITAET)


def _basispunkt() -> tuple[int, int, int, int]:
    by = (4 * pow(5, ED25519_P - 2, ED25519_P)) % ED25519_P
    bx = _x_aus_y(by, 0)
    assert bx is not None
    return (bx, by, 1, (bx * by) % ED25519_P)


_ED25519_BASIS = _basispunkt()


def _skalar_aus_seed(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519-Seed muss genau 32 Bytes lang sein.")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_oeffentlich_aus_seed(seed: bytes) -> bytes:
    """Leitet den 32-Byte-öffentlichen Ed25519-Schlüssel aus einem 32-Byte-Seed ab."""
    a, _ = _skalar_aus_seed(seed)
    return _punkt_kodieren(_punkt_mul(a, _ED25519_BASIS))


def ed25519_signieren(seed: bytes, nachricht: bytes) -> bytes:
    """Erzeugt eine deterministische RFC-8032-Ed25519-Signatur (64 Bytes).

    Der private ``seed`` gehört im Betrieb ausschließlich auf das Handy des Nutzers
    (oder in automatisierte Tests), niemals auf die Bau-Maschine.
    """
    a, praefix = _skalar_aus_seed(seed)
    pub = _punkt_kodieren(_punkt_mul(a, _ED25519_BASIS))
    r = int.from_bytes(hashlib.sha512(praefix + nachricht).digest(), "little") % ED25519_L
    r_bytes = _punkt_kodieren(_punkt_mul(r, _ED25519_BASIS))
    k = int.from_bytes(hashlib.sha512(r_bytes + pub + nachricht).digest(), "little") % ED25519_L
    s = (r + k * a) % ED25519_L
    return r_bytes + s.to_bytes(32, "little")


def ed25519_pruefen(oeffentlich: bytes, nachricht: bytes, signatur: bytes) -> bool:
    """Strenge Ed25519-Prüfung (RFC 8032): lehnt Malleability (``S >= L``), nicht-kanonische
    Punkte und Kleinstordnungs-Schlüssel ab."""
    if not isinstance(oeffentlich, bytes) or not isinstance(nachricht, bytes) or not isinstance(signatur, bytes):
        return False
    if len(oeffentlich) != 32 or len(signatur) != 64:
        return False
    punkt_a = _punkt_dekodieren(oeffentlich)
    if punkt_a is None or _ist_kleinstordnung(punkt_a):
        return False
    punkt_r = _punkt_dekodieren(signatur[:32])
    if punkt_r is None:
        return False
    s = int.from_bytes(signatur[32:], "little")
    if s >= ED25519_L:
        return False
    k = int.from_bytes(hashlib.sha512(signatur[:32] + oeffentlich + nachricht).digest(), "little") % ED25519_L
    links = _punkt_mul(s, _ED25519_BASIS)
    rechts = _punkt_add(punkt_r, _punkt_mul(k, punkt_a))
    return _punkt_gleich(links, rechts)


def schluesselpaar_erzeugen() -> tuple[bytes, bytes]:
    """Erzeugt ein zufälliges Ed25519-Schlüsselpaar ``(seed_32, oeffentlich_32)``."""
    seed = secrets.token_bytes(32)
    return seed, ed25519_oeffentlich_aus_seed(seed)


# --- Schlüssel-Formate (ed25519:<b64url> und OpenSSH ssh-ed25519) ----------------------------


def _b64url_kodieren(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).decode("ascii").rstrip("=")


def _b64url_dekodieren(text: str) -> bytes:
    if not isinstance(text, str) or not text or not _B64URL_ZEICHEN.fullmatch(text):
        raise BeweisFehler("Ungültige Base64url-Kodierung.")
    pad = "=" * (-len(text) % 4)
    try:
        roh = base64.urlsafe_b64decode(text + pad)
    except ValueError as fehler:
        raise BeweisFehler(f"Ungültige Base64url-Daten: {fehler}") from fehler
    # Kanonische Kodierung prüfen (keine überzähligen Bits im letzten Zeichen).
    if _b64url_kodieren(roh) != text:
        raise BeweisFehler("Nicht-kanonische Base64url-Kodierung.")
    return roh


def schluessel_kennung(pub_bytes: bytes) -> str:
    """Kurzer stabiler Fingerabdruck (16 Hex-Zeichen SHA-256) eines öffentlichen Schlüssels."""
    return hashlib.sha256(pub_bytes).hexdigest()[:16]


def formatiere_oeffentlichen_schluessel(pub_bytes: bytes) -> str:
    """Kanonische kompakte Textform ``ed25519:<base64url>`` für Konfiguration und Doku."""
    if len(pub_bytes) != 32:
        raise ValueError("Öffentlicher Ed25519-Schlüssel muss 32 Bytes lang sein.")
    return f"ed25519:{_b64url_kodieren(pub_bytes)}"


def _parse_openssh_ed25519(b64_teil: str) -> bytes:
    try:
        blob = base64.b64decode(b64_teil, validate=True)
    except ValueError as fehler:
        raise BeweisFehler(f"Ungültige OpenSSH-Base64-Daten: {fehler}") from fehler
    if len(blob) < 8:
        raise BeweisFehler("OpenSSH-Schlüssel zu kurz.")
    typ_len = struct.unpack(">I", blob[:4])[0]
    pos = 4 + typ_len
    if pos + 4 > len(blob) or blob[4:pos] != b"ssh-ed25519":
        raise BeweisFehler("OpenSSH-Schlüssel ist kein ssh-ed25519.")
    key_len = struct.unpack(">I", blob[pos : pos + 4])[0]
    pos += 4
    if key_len != 32 or pos + 32 != len(blob):
        raise BeweisFehler("OpenSSH-Ed25519-Schlüssel hat falsche Länge.")
    return blob[pos : pos + 32]


def parse_oeffentlichen_schluessel(text: str, *, quelle: str = "") -> OeffentlicherSchluessel:
    """Parst ``ed25519:<base64url>`` oder ``ssh-ed25519 <base64> [Kommentar]`` streng."""
    if not isinstance(text, str) or not text.strip():
        raise BeweisFehler("Leerer öffentlicher Schlüssel.")
    sauber = text.strip()
    if sauber.startswith("ed25519:"):
        roh = _b64url_dekodieren(sauber.removeprefix("ed25519:").strip())
    elif sauber.startswith("ssh-ed25519 "):
        teile = sauber.split()
        if len(teile) < 2:
            raise BeweisFehler("Unvollständige ssh-ed25519-Zeile.")
        roh = _parse_openssh_ed25519(teile[1])
    else:
        raise BeweisFehler("Unbekanntes Schlüsselformat (erwartet ed25519:… oder ssh-ed25519 …).")
    punkt = _punkt_dekodieren(roh)
    if punkt is None or _ist_kleinstordnung(punkt):
        raise BeweisFehler("Ungültiger oder schwacher Ed25519-Punkt.")
    return OeffentlicherSchluessel(roh=roh, kennung=schluessel_kennung(roh), quelle=quelle)


# --- Token-Erzeugung, -Extraktion und -Prüfung ----------------------------------------------


def frage_digest(frage: str) -> str:
    """SHA-256-Hex-Digest des exakten Fragetexts (verhindert nachträglichen Fragen-Tausch)."""
    return hashlib.sha256(str(frage).encode("utf-8")).hexdigest()


def beweis_erzeugen(
    seed: bytes,
    *,
    ticket: str | int,
    frage_id: str,
    frage: str,
    challenge: str,
    wahl: str,
    grund: str,
    ts: str = "",
) -> str:
    """Erzeugt ein signiertes ``TO-SPAWN-K2-V1``-Token (für das Handy bzw. Tests)."""
    ticket_s = str(ticket).strip()
    fid_s = str(frage_id).strip()
    challenge_s = str(challenge).strip()
    wahl_s = str(wahl)
    grund_s = str(grund)
    if not ticket_s.isdigit():
        raise ValueError(f"Ungültige Ticket-Nummer: {ticket!r}")
    if not fid_s:
        raise ValueError("Frage-ID darf nicht leer sein.")
    if len(challenge_s) < MIN_CHALLENGE_LAENGE:
        raise ValueError("Challenge ist zu kurz.")
    if not wahl_s.strip():
        raise ValueError("Wahl darf nicht leer sein.")
    pub = ed25519_oeffentlich_aus_seed(seed)
    payload = {
        "challenge": challenge_s,
        "frage_sha256": frage_digest(frage),
        "grund": grund_s,
        "id": fid_s,
        "key_id": schluessel_kennung(pub),
        "ticket": ticket_s,
        "ts": ts or datetime.now(UTC).isoformat(timespec="seconds"),
        "v": 1,
        "wahl": wahl_s,
    }
    payload_b64 = _b64url_kodieren(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signatur = ed25519_signieren(seed, DOMAIN_V1 + payload_b64.encode("ascii"))
    return f"{TOKEN_PRAEFIX}.{payload_b64}.{_b64url_kodieren(signatur)}"


def extrahiere_token(text: str) -> str | None:
    """Extrahiert ein eindeutiges ``TO-SPAWN-K2-V1``-Token aus freiem Text; bei Mehrdeutigkeit Fehler."""
    if not isinstance(text, str) or not text:
        return None
    treffer = list(dict.fromkeys(_TOKEN_MUSTER.findall(text)))
    if not treffer:
        return None
    if len(treffer) > 1:
        raise BeweisFehler("Mehrere verschiedene Klasse-2-Beweis-Tokens im Text gefunden.")
    return treffer[0]


def _text_gleich(a: str, b: str) -> bool:
    """Konstantzeit-Vergleich für beliebige UTF-8-Texte (auch mit Umlauten)."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def beweis_pruefen(
    token: str,
    schluessel: Sequence[OeffentlicherSchluessel],
    *,
    ticket: str | int,
    frage_id: str | None = None,
    frage: str | None = None,
    challenge: str | None = None,
    wahl: str | None = None,
    grund: str | None = None,
) -> GepruefterBeweis:
    """Prüft Signatur und alle Bindungen eines ``TO-SPAWN-K2-V1``-Tokens fail-closed."""
    if not schluessel:
        raise BeweisFehler("Kein vertrauenswürdiger öffentlicher Schlüssel eingerichtet.")
    if not isinstance(token, str) or not token.strip():
        raise BeweisFehler("Beweis-Token fehlt.")
    sauber = token.strip()
    teile = sauber.split(".")
    if len(teile) != 3 or teile[0] != TOKEN_PRAEFIX:
        raise BeweisFehler("Ungültiges Beweis-Format (erwartet TO-SPAWN-K2-V1.<payload>.<sig>).")
    payload_b64, sig_b64 = teile[1], teile[2]
    payload_bytes = _b64url_dekodieren(payload_b64)
    sig_bytes = _b64url_dekodieren(sig_b64)
    if len(sig_bytes) != 64:
        raise BeweisFehler("Ed25519-Signatur hat falsche Länge.")
    signierte_daten = DOMAIN_V1 + payload_b64.encode("ascii")

    # Signatur vor dem JSON-Parsen gegen die vertrauenswürdigen öffentlichen Schlüssel prüfen.
    treffer_schluessel = [k for k in schluessel if ed25519_pruefen(k.roh, signierte_daten, sig_bytes)]
    if not treffer_schluessel:
        raise BeweisFehler("Ed25519-Signatur passt zu keinem vertrauenswürdigen Handy-Schlüssel.")
    geprueft_mit = treffer_schluessel[0]

    try:
        daten = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as fehler:
        raise BeweisFehler(f"Ungültige Beweis-Nutzlast: {fehler}") from fehler
    if not isinstance(daten, dict):
        raise BeweisFehler("Beweis-Nutzlast ist kein JSON-Objekt.")
    if type(daten.get("v")) is not int or daten.get("v") != 1:
        raise BeweisFehler("Nicht unterstützte Beweis-Version.")

    erforderlich = ("ticket", "id", "frage_sha256", "challenge", "wahl", "grund", "ts", "key_id")
    if any(not isinstance(daten.get(feld), str) for feld in erforderlich):
        raise BeweisFehler("Pflichtfeld in der Beweis-Nutzlast fehlt oder hat falschen Typ.")

    if not _text_gleich(daten["key_id"], geprueft_mit.kennung):
        raise BeweisFehler("Schlüssel-Kennung im Beweis stimmt nicht mit dem Prüfschlüssel überein.")
    if not daten["wahl"].strip():
        raise BeweisFehler("Leere Wahl im Beweis.")
    if len(daten["challenge"]) < MIN_CHALLENGE_LAENGE:
        raise BeweisFehler("Challenge im Beweis ist zu kurz.")

    erwartet_ticket = str(ticket).strip()
    if not _text_gleich(daten["ticket"], erwartet_ticket):
        raise BeweisFehler("Beweis gilt für ein anderes Ticket.")
    if frage_id is not None and not _text_gleich(daten["id"], str(frage_id)):
        raise BeweisFehler("Beweis gilt für eine andere Frage-ID.")
    if challenge is not None and (
        len(str(challenge)) < MIN_CHALLENGE_LAENGE or not _text_gleich(daten["challenge"], str(challenge))
    ):
        raise BeweisFehler("Challenge stimmt nicht überein (möglicher Replay).")
    if frage is not None and not _text_gleich(daten["frage_sha256"], frage_digest(frage)):
        raise BeweisFehler("Fragetext stimmt nicht mit dem signierten Digest überein.")
    if wahl is not None and not _text_gleich(daten["wahl"], str(wahl)):
        raise BeweisFehler("Wahl stimmt nicht mit dem signierten Beweis überein.")
    if grund is not None and not _text_gleich(daten["grund"], str(grund)):
        raise BeweisFehler("Grund stimmt nicht mit dem signierten Beweis überein.")

    return GepruefterBeweis(
        ticket=daten["ticket"],
        frage_id=daten["id"],
        frage_sha256=daten["frage_sha256"],
        challenge=daten["challenge"],
        wahl=daten["wahl"],
        grund=daten["grund"],
        ts=daten["ts"],
        schluessel_kennung=geprueft_mit.kennung,
        token=sauber,
    )


# --- Vertrauenswürdige öffentliche Schlüssel laden (fail-closed) ----------------------------


def _windows_programdata() -> Path:
    """Liest ProgramData über die Windows-Shell-API statt über ``os.environ`` (nicht manipulierbar)."""
    try:
        # FOLDERID_ProgramData = {62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}
        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = _GUID(0x62AB5D82, 0xFDC1, 0x4DC3, (ctypes.c_ubyte * 8)(0xA9, 0xDD, 0x07, 0x0D, 0x1D, 0x49, 0x5D, 0x97))
        out = ctypes.c_wchar_p()
        rc = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))  # type: ignore[attr-defined]
        if rc == 0 and out.value:
            pfad = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
            return pfad
    except (OSError, AttributeError, ValueError):
        pass
    return Path("C:/ProgramData")


def system_schluessel_pfad() -> Path:
    """Fester Systempfad für nur-lesbare Handy-Schlüssel (nie aus Env-Variablen)."""
    if _SYSTEM_SCHLUESSEL_DATEI is not None:
        return _SYSTEM_SCHLUESSEL_DATEI
    if sys.platform == "win32":
        return _windows_programdata() / "to-spawn" / "klasse2_schluessel.json"
    return Path("/etc/to-spawn/klasse2_schluessel.json")


def _sicheres_git() -> str | None:
    """Bevorzugt feste System-Pfade für ``git``, damit ein verbogener ``PATH`` wirkungslos bleibt."""
    kandidaten = (
        (
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
        )
        if sys.platform == "win32"
        else ("/usr/bin/git", "/usr/local/bin/git", "/bin/git")
    )
    for pfad in kandidaten:
        if os.path.isfile(pfad):
            return pfad
    return shutil.which("git")


def _git_env() -> dict[str, str]:
    """Bereinigte Umgebung für vertrauenswürdige ``git``-Aufrufe (ohne ``GIT_*``, feste C-Locale)."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["LC_ALL"] = "C"
    env["LANGUAGE"] = "C"
    env["LANG"] = "C"
    return env


def _git_lauf(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git_bin = _sicheres_git()
    if not git_bin:
        return None
    try:
        return subprocess.run(
            [git_bin, "--no-replace-objects", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
            timeout=10,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        raise BeweisKonfigFehler(f"git-Aufruf für Schlüssel-Prüfung fehlgeschlagen: {fehler}") from fehler


def _parse_schluessel_liste(werte: Any, *, quelle: str) -> tuple[OeffentlicherSchluessel, ...]:
    if not isinstance(werte, list):
        raise BeweisKonfigFehler(f"{quelle}: oeffentliche_schluessel muss eine Liste sein.")
    ergebnis: list[OeffentlicherSchluessel] = []
    for eintrag in werte:
        if not isinstance(eintrag, str):
            raise BeweisKonfigFehler(f"{quelle}: jeder Schlüssel muss ein String sein.")
        try:
            ergebnis.append(parse_oeffentlichen_schluessel(eintrag, quelle=quelle))
        except BeweisFehler as fehler:
            raise BeweisKonfigFehler(f"{quelle}: ungültiger Schlüssel ({fehler}).") from fehler
    return tuple(ergebnis)


def _lade_system_schluessel(pfad: Path) -> tuple[OeffentlicherSchluessel, ...]:
    """Liest System-Schlüsseldatei (JSON-Objekt, JSON-Liste oder eine Schlüsselzeile je Zeile)."""
    try:
        roh = pfad.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as fehler:
        raise BeweisKonfigFehler(f"System-Schlüsseldatei {pfad} nicht lesbar: {fehler}") from fehler
    quelle = str(pfad)
    gestrippt = roh.strip()
    if not gestrippt:
        raise BeweisKonfigFehler(f"System-Schlüsseldatei {pfad} ist leer.")
    if gestrippt.startswith(("{", "[")):
        try:
            daten = json.loads(gestrippt)
        except ValueError as fehler:
            raise BeweisKonfigFehler(f"System-Schlüsseldatei {pfad} ist ungültiges JSON: {fehler}") from fehler
        if isinstance(daten, list):
            schluessel = _parse_schluessel_liste(daten, quelle=quelle)
        elif isinstance(daten, dict):
            schluessel = _parse_schluessel_liste(daten.get("oeffentliche_schluessel"), quelle=quelle)
        else:
            raise BeweisKonfigFehler(f"System-Schlüsseldatei {pfad} hat unerwartete Form.")
    else:
        zeilen = [z.strip() for z in gestrippt.splitlines() if z.strip() and not z.lstrip().startswith("#")]
        schluessel = _parse_schluessel_liste(zeilen, quelle=quelle)
    if not schluessel:
        raise BeweisKonfigFehler(f"System-Schlüsseldatei {pfad} enthält keine Schlüssel.")
    return schluessel


def _parse_konfig_schluessel(text: str, *, quelle: str) -> tuple[OeffentlicherSchluessel, ...]:
    try:
        daten = json.loads(text)
    except ValueError as fehler:
        raise BeweisKonfigFehler(f"{quelle}: ungültiges JSON ({fehler}).") from fehler
    if not isinstance(daten, dict):
        raise BeweisKonfigFehler(f"{quelle}: Wurzel muss ein JSON-Objekt sein.")
    gruppe = daten.get("klasse_2")
    if gruppe is None:
        return ()
    if not isinstance(gruppe, dict):
        raise BeweisKonfigFehler(f"{quelle}: klasse_2 muss ein Objekt sein.")
    roh_liste = gruppe.get("oeffentliche_schluessel")
    if roh_liste is None:
        return ()
    return _parse_schluessel_liste(roh_liste, quelle=quelle)


def _origin_hauptref(repo: Path) -> str | None:
    """Ermittelt ``origin/<hauptzweig>`` des Repos oder ``None``, wenn kein Remote-Hauptzweig existiert.

    Existiert ``.git`` im Repo, ist aber kein sicheres ``git`` auffindbar, wird fail-closed
    :class:`BeweisKonfigFehler` geworfen (kein stiller Rückfall auf den Arbeitsbaum).
    """
    git_eintrag = repo / ".git"
    if not (git_eintrag.exists() or git_eintrag.is_symlink()):
        return None
    if not _sicheres_git():
        raise BeweisKonfigFehler("git fehlt: origin-Hauptzweig für vertrauenswürdige Schlüssel nicht prüfbar.")
    sym = _git_lauf(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if sym is None:
        raise BeweisKonfigFehler("git fehlt: origin-Hauptzweig für vertrauenswürdige Schlüssel nicht prüfbar.")
    if sym.returncode == 0 and sym.stdout.strip().startswith("origin/"):
        ref = sym.stdout.strip()
        pruef = _git_lauf(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/{ref}")
        if pruef is None:
            raise BeweisKonfigFehler("git fehlt: origin-Hauptzweig für vertrauenswürdige Schlüssel nicht prüfbar.")
        if pruef.returncode == 0:
            return ref
    for zweig in ("main", "master"):
        pruef = _git_lauf(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{zweig}")
        if pruef is None:
            raise BeweisKonfigFehler("git fehlt: origin-Hauptzweig für vertrauenswürdige Schlüssel nicht prüfbar.")
        if pruef.returncode == 0:
            return f"origin/{zweig}"
    return None


def vertraute_schluessel(hauptbaum: Path) -> tuple[OeffentlicherSchluessel, ...]:
    """Liefert die maßgeblichen vertrauenswürdigen Ed25519-Schlüssel (leer = Beweispflicht aus).

    Reihenfolge (streng, keine Env-Variablen):
    1. System-Schlüsseldatei (:func:`system_schluessel_pfad`), sofern vorhanden.
    2. ``origin/<hauptzweig>:.to-spawn/config.json``, sofern das Repo einen Fern-Hauptzweig hat.
    3. Lokale ``<hauptbaum>/.to-spawn/config.json`` nur ohne Fern-Hauptzweig.

    Fehler in einer vorhandenen Schlüssel-Quelle werfen :class:`BeweisKonfigFehler` (fail-closed).
    """
    sys_pfad = system_schluessel_pfad()
    if sys_pfad.exists() or sys_pfad.is_symlink():
        return _lade_system_schluessel(sys_pfad)

    origin_ref = _origin_hauptref(hauptbaum)
    if origin_ref is not None:
        konfig_pfad = ".to-spawn/config.json"
        liste = _git_lauf(hauptbaum, "ls-tree", "-z", "--name-only", origin_ref, "--", konfig_pfad)
        if liste is None:
            raise BeweisKonfigFehler("git fehlt: vertrauenswürdige Schlüssel auf origin nicht prüfbar.")
        if liste.returncode != 0:
            raise BeweisKonfigFehler(f"git ls-tree für {origin_ref} fehlgeschlagen: {liste.stderr.strip()}")
        if konfig_pfad not in [e for e in liste.stdout.split("\0") if e]:
            return ()
        obj = f"{origin_ref}:{konfig_pfad}"
        erg = _git_lauf(hauptbaum, "cat-file", "-p", obj)
        if erg is None:
            raise BeweisKonfigFehler("git fehlt: vertrauenswürdige Schlüssel auf origin nicht prüfbar.")
        if erg.returncode == 0:
            return _parse_konfig_schluessel(erg.stdout, quelle=obj)
        raise BeweisKonfigFehler(f"git cat-file für {obj} fehlgeschlagen: {erg.stderr.strip()}")

    lokal = hauptbaum / ".to-spawn" / "config.json"
    if not lokal.exists():
        return ()
    try:
        text = lokal.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as fehler:
        raise BeweisKonfigFehler(f"{lokal} nicht lesbar: {fehler}") from fehler
    return _parse_konfig_schluessel(text, quelle=str(lokal))
