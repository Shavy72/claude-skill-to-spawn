"""Beweislauf für den Bau-Leitstand: Screenshots, Konsolenfehler, Funktionstests."""

import json
import logging
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("pruefen")

ORDNER = Path(__file__).parent
URL = (ORDNER / "bau-leitstand.html").resolve().as_uri()

MESSUNG_JS = """() => {
  const sc = document.getElementById('gleis-scroll');
  const svg = document.getElementById('gleisplan');
  const knoten = [...document.querySelectorAll('.knoten')];
  // Überlappung: Text-Elemente je Knoten gegen Kastenbreite prüfen
  const raus = [];
  knoten.forEach(k => {
    const box = k.querySelector('.koerper').getBBox();
    k.querySelectorAll('text').forEach(t => {
      const b = t.getBBox();
      if (b.x + b.width > box.width - 4 || b.y + b.height > box.height) raus.push(k.dataset.nr + ':' + t.getAttribute('class') + ':' + t.textContent);
    });
    const nr = k.querySelector('.nr').getBBox(), tag = k.querySelector('.ntag').getBBox();
    const zst = k.querySelector('.zst');
    if (zst) { const z = zst.getBBox(); if (z.x < tag.x + tag.width + 4) raus.push(k.dataset.nr + ':zst-ueberlappt'); }
  });
  return {
    scrollW: document.documentElement.scrollWidth, innerW: innerWidth,
    svgW: Number(svg.getAttribute('width')), containerW: sc.clientWidth,
    knoten: knoten.length, zonen: document.querySelectorAll('#gleisplan .zone').length,
    kastenW: knoten.length ? knoten[0].querySelector('.koerper').getAttribute('width') : null,
    kastenH: knoten.length ? knoten[0].querySelector('.koerper').getAttribute('height') : null,
    zstAnz: document.querySelectorAll('.knoten .zst').length,
    raus,
  };
}"""


HANDY_JS = """() => {
  const sichtbar = (e) => { const r = e.getClientRects(); if (!r.length) return false; const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const clipt = (e) => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o !== 'visible') return true; } return false; };
  const alle = [...document.querySelectorAll('#app *')].filter(sichtbar);
  const raus = [], klein = [], abgeschnitten = [];
  alle.forEach((e) => {
    const r = e.getBoundingClientRect();
    if ((r.right > innerWidth + 1 || r.left < -1) && r.width > 0 && !clipt(e) && getComputedStyle(e).position !== 'fixed') raus.push((e.id || e.className || e.tagName) + ':' + Math.round(r.left) + '-' + Math.round(r.right));
    const eigenText = [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (eigenText) {
      const fs = parseFloat(getComputedStyle(e).fontSize);
      if (fs < 12) klein.push((e.className || e.tagName) + ':' + fs + ':' + e.textContent.trim().slice(0, 20));
      const cs = getComputedStyle(e);
      if (cs.textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth + 1) abgeschnitten.push((e.className || e.tagName) + ':' + e.textContent.trim().slice(0, 30));
      if ([...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.includes('…'))) abgeschnitten.push('…:' + e.textContent.trim().slice(0, 30));
    }
  });
  return {
    scrollW: document.documentElement.scrollWidth, bodyScrollW: document.body.scrollWidth, innerW: innerWidth,
    liste_sichtbar: sichtbar(document.getElementById('gleis-liste')), svg_sichtbar: sichtbar(document.getElementById('gleis-scroll')),
    zeilen: document.querySelectorAll('.gl-zeile').length, gruppen: document.querySelectorAll('.gl-gruppe').length,
    raus: raus.slice(0, 12), klein: klein.slice(0, 12), abgeschnitten: abgeschnitten.slice(0, 12),
  };
}"""


def handy_tests(page) -> dict:
    tests: dict = {}
    page.locator(".ph-knopf").first.click()
    page.wait_for_timeout(400)
    tests["naht_filter_an"] = page.locator(".abgedunkelt").count() > 0
    page.locator(".ph-knopf").first.click()
    page.wait_for_timeout(400)
    tests["naht_filter_aus"] = page.locator(".abgedunkelt").count() == 0
    zeile = page.locator('.gl-zeile[data-nr="378"]')
    zeile.scroll_into_view_if_needed()
    zeile.tap()
    page.wait_for_timeout(500)
    tests["tippen_detail_auf"] = page.locator("#detail").is_visible()
    geo = page.evaluate(
        "() => { const d = document.getElementById('detail').getBoundingClientRect(); const z = document.getElementById('d-zu').getBoundingClientRect();"
        " const knoepfe = [...document.querySelectorAll('#detail button, #detail a')].map(b => b.getBoundingClientRect().height);"
        " return { unten: Math.round(d.bottom), breite: Math.round(d.width), zuOben: Math.round(z.top), zuH: Math.round(z.height), zuW: Math.round(z.width), minTap: Math.min(...knoepfe) }; }"
    )
    tests["blatt_unten_volle_breite"] = geo["unten"] == page.viewport_size["height"] and geo["breite"] == page.viewport_size["width"]
    tests["schliessen_erreichbar_44"] = 0 <= geo["zuOben"] < page.viewport_size["height"] and geo["zuH"] >= 44
    tests["tap_ziele_44"] = geo["minTap"] >= 44
    page.screenshot(path=str(ORDNER / "m-390-blatt.png"))
    page.locator("#d-zu").tap()
    page.wait_for_timeout(400)
    tests["schliessen_zu"] = not page.locator("#detail").is_visible()
    zeile.tap()
    page.wait_for_timeout(400)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    tests["esc_zu"] = not page.locator("#detail").is_visible()
    zeile.tap()
    page.wait_for_timeout(400)
    page.mouse.click(195, 30)
    page.wait_for_timeout(400)
    tests["daneben_tippen_zu"] = not page.locator("#detail").is_visible()
    page.locator('[data-logfilter="probleme"]').click()
    page.wait_for_timeout(300)
    arten = page.evaluate(
        "() => [...document.querySelectorAll('#log-liste .log-e')].filter(e => e.offsetParent).map(e => [...e.classList].find(c => c.startsWith('art-')))"
    )
    tests["log_probleme"] = bool(arten) and all(a in ("art-problem", "art-david") for a in arten)
    return {"tests": tests, "blatt": geo}


def handy_lauf(pw, name: str, breite: int, hoehe: int, schema: str, fehler: list[str], funktion: bool) -> dict:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, color_scheme=schema, has_touch=True, device_scale_factor=2)
    page = ctx.new_page()
    page.on("console", lambda m: fehler.append(f"{name} console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: fehler.append(f"{name} pageerror: {e}"))
    page.goto(URL)
    page.wait_for_timeout(5000)
    ergebnis = {"mess": page.evaluate(HANDY_JS)}
    page.screenshot(path=str(ORDNER / f"{name}.png"), full_page=True)
    if funktion:
        ergebnis.update(handy_tests(page))
    browser.close()
    return ergebnis


def lauf(pw, name: str, breite: int, hoehe: int, schema: str, fehler: list[str], funktion: bool) -> dict:
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, color_scheme=schema)
    page = ctx.new_page()
    page.on("console", lambda m: fehler.append(f"{name} console: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: fehler.append(f"{name} pageerror: {e}"))
    page.goto(URL)
    page.wait_for_timeout(5000)
    mess = page.evaluate(MESSUNG_JS)
    page.screenshot(path=str(ORDNER / f"{name}.png"), full_page=True)
    page.locator("#stellpult").screenshot(path=str(ORDNER / f"z-{name}-gleis.png"))
    page.locator(".kopf").screenshot(path=str(ORDNER / f"z-{name}-kopf.png"))
    ergebnis = {"mess": mess}
    if funktion:
        tests = {}
        page.locator(".ph-knopf").first.click()
        page.wait_for_timeout(400)
        n1 = page.locator(".abgedunkelt").count()
        page.locator(".ph-knopf").first.click()
        page.wait_for_timeout(400)
        n2 = page.locator(".abgedunkelt").count()
        tests["naht_filter_an"] = n1 > 0
        tests["naht_filter_aus"] = n2 == 0
        page.locator('.knoten[data-nr="378"]').click()
        page.wait_for_timeout(400)
        tests["detail_auf"] = page.locator("#detail").is_visible()
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        tests["detail_esc_zu"] = not page.locator("#detail").is_visible()
        page.locator('[data-logfilter="probleme"]').click()
        page.wait_for_timeout(300)
        arten = page.evaluate(
            "() => [...document.querySelectorAll('#log-liste .log-e')].filter(e => e.offsetParent).map(e => [...e.classList].find(c => c.startsWith('art-')))"
        )
        tests["log_probleme"] = bool(arten) and all(a in ("art-problem", "art-david") for a in arten)
        ergebnis["tests"] = tests
        ergebnis["log_arten"] = arten
    browser.close()
    return ergebnis


def main() -> int:
    fehler: list[str] = []
    with sync_playwright() as pw:
        res = {
            "s-1408-hell": lauf(pw, "s-1408-hell", 1408, 791, "light", fehler, True),
            "s-1408-dunkel": lauf(pw, "s-1408-dunkel", 1408, 791, "dark", fehler, False),
            "m-390-hell": handy_lauf(pw, "m-390-hell", 390, 844, "light", fehler, True),
            "m-390-dunkel": handy_lauf(pw, "m-390-dunkel", 390, 844, "dark", fehler, False),
            "m-360-hell": handy_lauf(pw, "m-360-hell", 360, 780, "light", fehler, False),
        }
    res["fehler"] = fehler
    log.info(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
