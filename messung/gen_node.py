"""Erzeugt das Node-Probe-Repo <ziel>/node/ (Etappe 3a). Einmalig ausführen.

Aufruf: python3 gen_node.py --ziel <probe-ordner>
"""

import argparse
import json
import textwrap
from pathlib import Path

_ap = argparse.ArgumentParser(description="Erzeugt das Node-Probe-Repo <ziel>/node/.")
_ap.add_argument("--ziel", type=Path, required=True, help="Probe-Ordner; darin entsteht node/")
R = _ap.parse_args().ziel / "node"
F: dict[str, str] = {}

F["package.json"] = (
    json.dumps(
        {
            "name": "probe-node",
            "private": True,
            "type": "module",
            "scripts": {"test": "vitest run"},
            "dependencies": {"greetlib": "file:./deps/greetlib-1"},
        },
        indent=2,
    )
    + "\n"
)
F[".gitignore"] = "node_modules/\n"
F["docs/README.md"] = "# Probe Node\n"
for v, txt in (("1", "Hello"), ("2", "Hi")):
    F[f"deps/greetlib-{v}/package.json"] = (
        json.dumps({"name": "greetlib", "version": f"{v}.0.0", "type": "module", "main": "index.js"}) + "\n"
    )
    F[f"deps/greetlib-{v}/index.js"] = f'export const greet = (n) => "{txt}, " + n;\n'
F["vitest.config.js"] = textwrap.dedent("""\
    import { defineConfig } from "vitest/config";
    export default defineConfig({ test: { setupFiles: ["./test/setup.js"] } });
    """)
F["test/setup.js"] = "globalThis.SHOP = 'Probe-Shop';\n"
F["src/math.js"] = textwrap.dedent("""\
    export const add = (a, b) => a + b;
    export const round2 = (x) => Math.round(x * 100) / 100;
    export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
    """)
F["src/format.js"] = textwrap.dedent("""\
    export const euro = (x) => x.toFixed(2) + " €";
    export const slug = (s) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    """)
F["src/data/prices.json"] = '{"A1": 10, "B2": 25.5, "C3": 4.5}\n'
F["src/data/stock.json"] = '{"A1": 3, "B2": 0, "C3": 12}\n'
F["src/cart.js"] = textwrap.dedent("""\
    import prices from "./data/prices.json" with { type: "json" };
    import { add, round2 } from "./math.js";
    export const total = (skus) => round2(skus.map((s) => prices[s]).reduce(add, 0));
    export const priceOf = (s) => prices[s];
    """)
F["src/stock.js"] = textwrap.dedent("""\
    import { readFileSync } from "node:fs";
    const PATH = new URL("./data/stock.json", import.meta.url);
    export const stockOf = (s) => JSON.parse(readFileSync(PATH, "utf8"))[s];
    export const inStock = (s) => stockOf(s) > 0;
    """)
F["src/plugins/index.js"] = textwrap.dedent("""\
    export async function load(name) {
      return import(`./${name}.js`);
    }
    export async function run(name, s) {
      return (await load(name)).apply(s);
    }
    """)
F["src/plugins/upper.js"] = "export const apply = (s) => s.toUpperCase();\n"
F["src/plugins/reverse.js"] = 'export const apply = (s) => s.split("").reverse().join("");\n'
F["src/greet.js"] = textwrap.dedent("""\
    import { greet } from "greetlib";
    export const welcome = (n) => greet(n) + " bei " + globalThis.SHOP;
    """)
F["src/index.js"] = textwrap.dedent("""\
    export { total } from "./cart.js";
    export { slug } from "./format.js";
    """)

T: dict[str, str] = {}
T["test/math.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { add, round2, clamp } from "../src/math.js";
    test.each([[1, 2, 3], [0, 0, 0], [-1, 1, 0], [2.5, 2.5, 5], [10, -3, 7]])("add %d %d", (a, b, e) => expect(add(a, b)).toBe(e));
    test.each([[1.005, 1], [2.345, 2.35], [3, 3], [0.1 + 0.2, 0.3], [9.999, 10]])("round2 %d", (x, e) => expect(round2(x)).toBe(e));
    test.each([[5, 0, 10, 5], [-1, 0, 10, 0], [11, 0, 10, 10], [0, 0, 0, 0]])("clamp %d", (x, l, h, e) => expect(clamp(x, l, h)).toBe(e));
    """)
T["test/format.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { euro, slug } from "../src/format.js";
    test.each([[1, "1.00 €"], [2.5, "2.50 €"], [0, "0.00 €"], [10.126, "10.13 €"]])("euro %d", (x, e) => expect(euro(x)).toBe(e));
    test.each([["Hallo Welt", "hallo-welt"], [" A ", "a"], ["x_y", "x-y"], ["-q-", "q"], ["ABC 1", "abc-1"], ["a..b", "a-b"]])("slug %s", (s, e) => expect(slug(s)).toBe(e));
    """)
T["test/cart.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { total, priceOf } from "../src/cart.js";
    test.each([[["A1"], 10], [["A1", "B2"], 35.5], [["C3", "C3"], 9], [[], 0], [["A1", "B2", "C3"], 40]])("total %j", (s, e) => expect(total(s)).toBe(e));
    test.each([["A1", 10], ["B2", 25.5], ["C3", 4.5]])("priceOf %s", (s, e) => expect(priceOf(s)).toBe(e));
    """)
T["test/stock.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { stockOf, inStock } from "../src/stock.js";
    test.each([["A1", 3], ["B2", 0], ["C3", 12]])("stockOf %s", (s, e) => expect(stockOf(s)).toBe(e));
    test.each([["A1", true], ["B2", false], ["C3", true]])("inStock %s", (s, e) => expect(inStock(s)).toBe(e));
    """)
T["test/plugins.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { run, load } from "../src/plugins/index.js";
    test.each([["abc", "ABC"], ["Hi", "HI"], ["", ""]])("upper %s", async (s, e) => expect(await run("upper", s)).toBe(e));
    test.each([["abc", "cba"], ["ab", "ba"], ["", ""]])("reverse %s", async (s, e) => expect(await run("reverse", s)).toBe(e));
    test("unknown", async () => { await expect(load("nope")).rejects.toThrow(); });
    """)
T["test/greet.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { welcome } from "../src/greet.js";
    test.each([["Anna"], ["Bob"], ["Cem"]])("welcome %s", (n) => expect(welcome(n)).toBe("Hello, " + n + " bei Probe-Shop"));
    """)
T["test/index.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    import { total, slug } from "../src/index.js";
    test("total reexport", () => expect(total(["A1"])).toBe(10));
    test("slug reexport", () => expect(slug("A B")).toBe("a-b"));
    test("total two", () => expect(total(["A1", "A1"])).toBe(20));
    """)
T["test/setup.test.js"] = textwrap.dedent("""\
    import { test, expect } from "vitest";
    test("shop global", () => expect(globalThis.SHOP).toBe("Probe-Shop"));
    test("shop type", () => expect(typeof globalThis.SHOP).toBe("string"));
    """)
F.update(T)
for rel, txt in F.items():
    p = R / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt)
print(len(F), "Dateien")
