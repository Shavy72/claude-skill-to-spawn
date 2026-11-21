"""Erzeugt das Python-Probe-Repo <ziel>/py/ (Etappe 3a). Einmalig ausführen.

Aufruf: python3 gen_py.py --ziel <probe-ordner>
"""

import argparse
import textwrap
from pathlib import Path

_ap = argparse.ArgumentParser(description="Erzeugt das Python-Probe-Repo <ziel>/py/.")
_ap.add_argument("--ziel", type=Path, required=True, help="Probe-Ordner; darin entsteht py/")
R = _ap.parse_args().ziel / "py"

F: dict[str, str] = {}

F["requirements.txt"] = "pytest\n./deps/greetlib-1\n"
F["pytest.ini"] = "[pytest]\ntestpaths = tests\naddopts = -p no:cacheprovider\n"
F["docs/README.md"] = "# Probe-App\n\nKleine Web-App-artige Probe für die Test-Auswahl.\n"
F[".gitignore"] = ".testmondata*\n__pycache__/\n*.egg-info/\nbuild/\n"
for v, txt in (("1", "Hello"), ("2", "Hi")):
    F[f"deps/greetlib-{v}/pyproject.toml"] = textwrap.dedent(f"""\
        [project]
        name = "greetlib"
        version = "{v}.0"
        [build-system]
        requires = ["setuptools"]
        build-backend = "setuptools.build_meta"
        """)
    F[f"deps/greetlib-{v}/greetlib/__init__.py"] = f'def greet(name: str) -> str:\n    return "{txt}, " + name\n'

F["app/__init__.py"] = (
    'from app.pricing import total_price as total\nfrom app.util.text import slug\n\n__all__ = ["total", "slug"]\n'
)
F["app/util/__init__.py"] = ""
F["app/util/text.py"] = textwrap.dedent("""\
    import re


    def slug(s: str) -> str:
        s = s.strip().lower()
        return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


    def title_case(s: str) -> str:
        return " ".join(w.capitalize() for w in s.split())


    def truncate(s: str, n: int) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"
    """)
F["app/util/dates.py"] = textwrap.dedent("""\
    from datetime import date, timedelta


    def is_weekend(d: date) -> bool:
        return d.weekday() >= 5


    def add_days(d: date, n: int) -> date:
        return d + timedelta(days=n)
    """)
F["app/tax.py"] = textwrap.dedent("""\
    RATES = {"DE": 0.19, "AT": 0.20, "CH": 0.081}


    def vat(amount: float, country: str) -> float:
        return round(amount * RATES[country], 2)
    """)
F["app/pricing.py"] = textwrap.dedent("""\
    from app.tax import vat


    def discount(amount: float, pct: int) -> float:
        return round(amount * (100 - pct) / 100, 2)


    def total_price(prices: list[float], country: str, pct: int = 0) -> float:
        net = discount(sum(prices), pct)
        return round(net + vat(net, country), 2)
    """)
F["app/models.py"] = textwrap.dedent("""\
    from dataclasses import dataclass, field


    @dataclass
    class User:
        name: str
        email: str
        country: str


    @dataclass
    class Order:
        user: User
        skus: list[str] = field(default_factory=list)
        total: float = 0.0
    """)
F["app/validators.py"] = textwrap.dedent("""\
    import re

    from app.tax import RATES


    def valid_email(s: str) -> bool:
        return re.fullmatch(r"[^@\\s]+@[^@\\s]+\\.[a-z]{2,}", s) is not None


    def valid_country(c: str) -> bool:
        return c in RATES
    """)
F["app/data/catalog.json"] = '{"A1": 10.0, "B2": 25.5, "C3": 4.5}\n'
F["app/catalog.py"] = textwrap.dedent("""\
    import json
    from pathlib import Path

    _PATH = Path(__file__).parent / "data" / "catalog.json"


    def load() -> dict[str, float]:
        return json.loads(_PATH.read_text())


    def price_of(sku: str) -> float:
        return load()[sku]
    """)
F["app/templates/welcome.txt"] = "{greeting}! Willkommen bei {shop}, {name}.\n"
F["app/render.py"] = textwrap.dedent("""\
    from pathlib import Path

    import greetlib

    from app import config

    _TPL = Path(__file__).parent / "templates" / "welcome.txt"


    def render_welcome(name: str) -> str:
        return _TPL.read_text().format(
            greeting=greetlib.greet(name).split(",")[0], shop=config.get("shop"), name=name
        ).strip()
    """)
F["app/settings.toml"] = 'shop = "Probe-Shop"\nmax_items = 5\n'
F["app/config.py"] = textwrap.dedent("""\
    import tomllib
    from pathlib import Path

    _PATH = Path(__file__).parent / "settings.toml"


    def get(key: str):
        return tomllib.loads(_PATH.read_text())[key]
    """)
F["app/mailer.py"] = textwrap.dedent("""\
    outbox: list[dict] = []
    SENDER = "shop@example.org"


    def send(to: str, subject: str, body: str) -> dict:
        msg = {"from": SENDER, "to": to, "subject": subject, "body": body}
        outbox.append(msg)
        return msg
    """)
F["app/notify.py"] = textwrap.dedent("""\
    from app import mailer
    from app.render import render_welcome


    def welcome(name: str, email: str) -> dict:
        return mailer.send(email, "Willkommen", render_welcome(name))
    """)
F["app/services/__init__.py"] = ""
F["app/services/orders.py"] = textwrap.dedent("""\
    from app import catalog, config
    from app.models import Order, User
    from app.pricing import total_price


    def place_order(user: User, skus: list[str], pct: int = 0) -> Order:
        if len(skus) > config.get("max_items"):
            raise ValueError("zu viele Artikel")
        prices = [catalog.price_of(s) for s in skus]
        return Order(user=user, skus=skus, total=total_price(prices, user.country, pct))
    """)
F["app/services/users.py"] = textwrap.dedent("""\
    from app import notify
    from app.models import User
    from app.validators import valid_country, valid_email


    def register(name: str, email: str, country: str) -> User:
        if not valid_email(email) or not valid_country(country):
            raise ValueError("ungültig")
        notify.welcome(name, email)
        return User(name, email, country)
    """)
F["app/plugins/__init__.py"] = textwrap.dedent("""\
    import importlib


    def load(name: str):
        return importlib.import_module(f"app.plugins.{name}")


    def export(name: str, rows: list[list[str]]) -> str:
        return load(name).render(rows)
    """)
F["app/plugins/csv_export.py"] = textwrap.dedent("""\
    DELIM = ","


    def render(rows: list[list[str]]) -> str:
        return "\\n".join(DELIM.join(r) for r in rows)
    """)
F["app/plugins/json_export.py"] = textwrap.dedent("""\
    import json

    INDENT = None


    def render(rows: list[list[str]]) -> str:
        return json.dumps(rows, indent=INDENT, sort_keys=True)
    """)
F["app/legacy.py"] = textwrap.dedent("""\
    import warnings


    def old_total(prices: list[float]) -> float:
        warnings.warn("old_total ist veraltet", DeprecationWarning, stacklevel=2)
        return sum(prices)
    """)

# ---------------- Tests ----------------
F["tests/__init__.py"] = ""
F["tests/conftest.py"] = textwrap.dedent("""\
    import pytest

    from app import mailer
    from app.models import User


    @pytest.fixture
    def user() -> User:
        return User("Anna Muster", "anna@example.org", "DE")


    @pytest.fixture(autouse=True)
    def clean_outbox():
        mailer.outbox.clear()
        yield
        mailer.outbox.clear()
    """)
F["tests/test_text.py"] = textwrap.dedent("""\
    import pytest

    from app.util.text import slug, title_case, truncate


    @pytest.mark.parametrize("s,exp", [("Hallo Welt", "hallo-welt"), ("  A  ", "a"), ("x_y", "x-y"),
        ("ABC", "abc"), ("a--b", "a-b"), ("1 2 3", "1-2-3"), ("Ä", ""), ("foo.bar", "foo-bar"),
        ("-x-", "x"), ("Mix 9", "mix-9")])
    def test_slug(s, exp):
        assert slug(s) == exp


    @pytest.mark.parametrize("s,exp", [("a b", "A B"), ("hallo", "Hallo"), ("", ""), ("x  y", "X Y"),
        ("aBC", "Abc")])
    def test_title_case(s, exp):
        assert title_case(s) == exp


    @pytest.mark.parametrize("s,n,exp", [("abc", 5, "abc"), ("abcdef", 4, "abc…"), ("ab", 2, "ab"),
        ("abc", 3, "abc"), ("abcd", 3, "ab…")])
    def test_truncate(s, n, exp):
        assert truncate(s, n) == exp
    """)
F["tests/test_dates.py"] = textwrap.dedent("""\
    from datetime import date

    import pytest

    from app.util.dates import add_days, is_weekend


    @pytest.mark.parametrize("d,exp", [(date(2026, 11, 21), True), (date(2026, 11, 22), True),
        (date(2026, 11, 23), False), (date(2026, 11, 20), False), (date(2026, 11, 18), False)])
    def test_is_weekend(d, exp):
        assert is_weekend(d) is exp


    @pytest.mark.parametrize("n,exp", [(0, date(2026, 1, 1)), (1, date(2026, 1, 2)),
        (31, date(2026, 2, 1)), (-1, date(2025, 12, 31)), (365, date(2027, 1, 1))])
    def test_add_days(n, exp):
        assert add_days(date(2026, 1, 1), n) == exp
    """)
F["tests/test_tax.py"] = textwrap.dedent("""\
    import pytest

    from app.tax import RATES, vat


    @pytest.mark.parametrize("amount,c,exp", [(100, "DE", 19.0), (100, "AT", 20.0), (100, "CH", 8.1),
        (0, "DE", 0.0), (10, "DE", 1.9), (50, "AT", 10.0), (1, "CH", 0.08), (200, "DE", 38.0)])
    def test_vat(amount, c, exp):
        assert vat(amount, c) == exp


    def test_unknown_country():
        with pytest.raises(KeyError):
            vat(1, "XX")


    def test_rates_keys():
        assert set(RATES) == {"DE", "AT", "CH"}
    """)
F["tests/test_pricing.py"] = textwrap.dedent("""\
    import pytest

    from app.pricing import discount, total_price


    @pytest.mark.parametrize("a,p,exp", [(100, 0, 100.0), (100, 10, 90.0), (100, 100, 0.0),
        (50, 50, 25.0), (19.99, 0, 19.99), (10, 25, 7.5)])
    def test_discount(a, p, exp):
        assert discount(a, p) == exp


    @pytest.mark.parametrize("prices,c,p,exp", [([10, 10], "DE", 0, 23.8), ([100], "AT", 0, 120.0),
        ([100], "DE", 10, 107.1), ([], "DE", 0, 0.0), ([1], "CH", 0, 1.08), ([50, 50], "AT", 50, 60.0)])
    def test_total(prices, c, p, exp):
        assert total_price(prices, c, p) == exp
    """)
F["tests/test_validators.py"] = textwrap.dedent("""\
    import pytest

    from app.validators import valid_country, valid_email


    @pytest.mark.parametrize("s,ok", [("a@b.de", True), ("anna@example.org", True), ("a@b", False),
        ("ab.de", False), ("a b@c.de", False), ("@c.de", False), ("a@b.c", False), ("x@y.com", True)])
    def test_email(s, ok):
        assert valid_email(s) is ok


    @pytest.mark.parametrize("c,ok", [("DE", True), ("AT", True), ("FR", False), ("de", False)])
    def test_country(c, ok):
        assert valid_country(c) is ok
    """)
F["tests/test_catalog.py"] = textwrap.dedent("""\
    import pytest

    from app import catalog


    @pytest.mark.parametrize("sku,exp", [("A1", 10.0), ("B2", 25.5), ("C3", 4.5)])
    def test_price(sku, exp):
        assert catalog.price_of(sku) == exp


    def test_unknown_sku():
        with pytest.raises(KeyError):
            catalog.price_of("ZZ")


    def test_count():
        assert len(catalog.load()) == 3


    def test_all_positive():
        assert all(v > 0 for v in catalog.load().values())


    def test_keys_sorted_like_file():
        assert list(catalog.load()) == ["A1", "B2", "C3"]


    def test_types():
        assert all(isinstance(v, float) for v in catalog.load().values())
    """)
F["tests/test_render.py"] = textwrap.dedent("""\
    import pytest

    from app.render import render_welcome


    @pytest.mark.parametrize("name", ["Anna", "Bob", "Cem", "Dana"])
    def test_welcome_contains_name(name):
        assert render_welcome(name).endswith(name + ".")


    def test_welcome_full():
        assert render_welcome("Anna") == "Hello! Willkommen bei Probe-Shop, Anna."
    """)
F["tests/test_config.py"] = textwrap.dedent("""\
    import pytest

    from app import config


    def test_shop():
        assert config.get("shop") == "Probe-Shop"


    def test_max_items():
        assert config.get("max_items") == 5


    def test_missing():
        with pytest.raises(KeyError):
            config.get("nope")


    def test_type_int():
        assert isinstance(config.get("max_items"), int)


    def test_type_str():
        assert isinstance(config.get("shop"), str)
    """)
F["tests/test_notify.py"] = textwrap.dedent("""\
    from unittest import mock

    from app import mailer, notify


    def test_welcome_sends():
        notify.welcome("Anna", "anna@example.org")
        assert len(mailer.outbox) == 1


    def test_welcome_subject():
        assert notify.welcome("Anna", "a@b.de")["subject"] == "Willkommen"


    def test_welcome_sender():
        assert notify.welcome("Anna", "a@b.de")["from"] == "shop@example.org"


    def test_welcome_patched():
        with mock.patch("app.mailer.send") as m:
            notify.welcome("Anna", "a@b.de")
        m.assert_called_once()
        assert m.call_args.args[1] == "Willkommen"


    def test_outbox_empty_at_start():
        assert mailer.outbox == []


    def test_body():
        assert "Anna" in notify.welcome("Anna", "a@b.de")["body"]
    """)
F["tests/test_orders.py"] = textwrap.dedent("""\
    import pytest

    from app.services.orders import place_order


    @pytest.mark.parametrize("skus,exp", [(["A1"], 11.9), (["A1", "A1"], 23.8), (["B2"], 30.34),
        (["C3"], 5.35), (["A1", "B2", "C3"], 47.6), ([], 0.0)])
    def test_total_de(user, skus, exp):
        assert place_order(user, skus).total == exp


    def test_discount(user):
        assert place_order(user, ["A1"], pct=10).total == 10.71


    def test_too_many(user):
        with pytest.raises(ValueError):
            place_order(user, ["A1"] * 6)


    def test_max_ok(user):
        assert len(place_order(user, ["A1"] * 5).skus) == 5


    def test_user_kept(user):
        assert place_order(user, ["A1"]).user.name == "Anna Muster"


    def test_unknown(user):
        with pytest.raises(KeyError):
            place_order(user, ["ZZ"])


    def test_country_de(user):
        assert user.country == "DE"
    """)
F["tests/test_users.py"] = textwrap.dedent("""\
    from unittest import mock

    import pytest

    from app import mailer
    from app.services.users import register


    @pytest.mark.parametrize("email", ["a@b.de", "x@y.com", "anna@example.org"])
    def test_register_ok(email):
        assert register("Anna", email, "DE").email == email


    @pytest.mark.parametrize("email,c", [("ab.de", "DE"), ("a@b.de", "FR"), ("a@b", "AT")])
    def test_register_bad(email, c):
        with pytest.raises(ValueError):
            register("Anna", email, c)


    def test_register_sends_mail():
        register("Anna", "a@b.de", "AT")
        assert mailer.outbox[0]["to"] == "a@b.de"


    def test_register_patched():
        with mock.patch("app.notify.welcome") as m:
            register("Anna", "a@b.de", "AT")
        m.assert_called_once_with("Anna", "a@b.de")
    """)
F["tests/test_plugins.py"] = textwrap.dedent("""\
    from unittest import mock

    import pytest

    from app import plugins

    ROWS = [["a", "b"], ["c", "d"]]


    def test_csv():
        assert plugins.export("csv_export", ROWS) == "a,b\\nc,d"


    def test_csv_patched_delim():
        with mock.patch("app.plugins.csv_export.DELIM", ";"):
            assert plugins.export("csv_export", ROWS) == "a;b\\nc;d"


    def test_json():
        assert plugins.export("json_export", ROWS) == '[["a", "b"], ["c", "d"]]'


    def test_json_dict_sorted():
        assert plugins.load("json_export").render({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'


    def test_json_patched_indent():
        with mock.patch("app.plugins.json_export.INDENT", 1):
            assert "\\n" in plugins.export("json_export", ROWS)


    def test_unknown():
        with pytest.raises(ModuleNotFoundError):
            plugins.load("nope")


    @pytest.mark.parametrize("name", ["csv_export", "json_export"])
    def test_has_render(name):
        assert callable(plugins.load(name).render)
    """)
F["tests/test_init.py"] = textwrap.dedent("""\
    import app


    def test_total_reexport():
        assert app.total([100], "DE") == 119.0


    def test_slug_reexport():
        assert app.slug("A B") == "a-b"


    def test_all():
        assert app.__all__ == ["total", "slug"]


    def test_total_discount():
        assert app.total([100], "AT", 50) == 60.0
    """)
F["tests/test_legacy.py"] = textwrap.dedent("""\
    from app.legacy import old_total


    def test_old_total():
        assert old_total([1, 2]) == 3


    def test_old_total_empty():
        assert old_total([]) == 0
    """)

for rel, txt in F.items():
    p = R / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt)
print(len(F), "Dateien")
