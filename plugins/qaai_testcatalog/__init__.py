"""Test catalog — a searchable HTML lookup for this repo's pytest suite.

The :mod:`qaai_testcatalog.plugin` pytest plugin adds a ``--test-catalog`` flag.
When set, it introspects the tests pytest actually collects (markers, fixtures,
parametrize params, docstrings, and the optional ``@pytest.mark.catalog`` marker)
and writes a JSON record file plus a single self-contained, dependency-free HTML
page you can open in a browser to search/filter/sort every test.

Because collection is what's hooked, ``pytest --collect-only --test-catalog``
produces the catalog without running any tests (no LLM calls).
"""

from qaai_testcatalog.render import build_catalog_html, write_catalog

__all__ = ["build_catalog_html", "write_catalog"]
