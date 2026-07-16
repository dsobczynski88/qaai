"""Pytest plugin that emits a searchable test catalog from live collection.

Registered as a ``pytest11`` entry point (see pyproject.toml), so the flags below
are always available under ``uv run pytest``. The plugin is a no-op unless
``--test-catalog`` is passed.

Data flow: ``pytest_collection_modifyitems`` turns each collected ``item`` into a
JSON-safe record (see :func:`_item_to_record`); ``pytest_collection_finish`` writes
``test_catalog.json`` and renders ``test_catalog.html`` via
:mod:`qaai.testcatalog.render`. Nothing here runs the tests, so pairing the flag
with ``--collect-only`` produces the catalog fast and offline.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

# Marker names we treat as the test "type" (first match wins, in this order).
_TYPE_MARKERS = ("integration", "unit")

# nodeid path segment -> component label. Checked in order; first hit wins.
_COMPONENT_SEGMENTS = (
    ("test_suite_reviewer", "rtm"),
    ("test_case_reviewer", "tc"),
    ("hazard_risk_reviewer", "hazard"),
    ("eval", "eval"),
    ("shared", "shared"),
    ("/api/", "api"),
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("test-catalog", "Searchable HTML test catalog")
    group.addoption(
        "--test-catalog",
        action="store_true",
        default=False,
        help="Emit a searchable HTML catalog of the collected tests. "
        "Pair with --collect-only to generate it without running any tests.",
    )
    group.addoption(
        "--test-catalog-out",
        action="store",
        default="logs/test-catalog",
        metavar="DIR",
        help="Output directory for test_catalog.json / test_catalog.html "
        "(default: logs/test-catalog).",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Register the curation marker so @pytest.mark.catalog(...) never warns.
    config.addinivalue_line(
        "markers",
        "catalog(summary=..., example_input=..., example_output=...): curate this "
        "test's entry in the test catalog. Any field overrides the auto-derived value.",
    )


def _first_line(text: str | None) -> str:
    """First non-empty line of a docstring, whitespace-normalized."""
    if not text:
        return ""
    for line in inspect.cleandoc(text).splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _summary_fallback_from_name(name: str) -> str:
    """Humanize a test function name when no docstring/marker is available."""
    base = name
    if base.startswith("test_"):
        base = base[len("test_"):]
    base = base.split("[", 1)[0]  # drop parametrize id suffix
    return base.replace("_", " ").strip().capitalize()


def _detect_type(item: pytest.Item) -> str:
    for marker in _TYPE_MARKERS:
        if item.get_closest_marker(marker) is not None:
            return marker
    if "/api/" in item.nodeid.replace("\\", "/"):
        return "api"
    return "unlabeled"


def _detect_component(nodeid: str) -> str:
    norm = nodeid.replace("\\", "/")
    for segment, label in _COMPONENT_SEGMENTS:
        if segment in norm:
            return label
    return "other"


def _skip_reason(item: pytest.Item) -> str | None:
    for name in ("skip", "skipif"):
        marker = item.get_closest_marker(name)
        if marker is None:
            continue
        reason = marker.kwargs.get("reason")
        if reason:
            return str(reason)
        if name == "skip":
            return "skipped"
        return "conditionally skipped"
    return None


def _catalog_marker(item: pytest.Item) -> dict[str, Any]:
    """Kwargs from @pytest.mark.catalog(...), or {} if absent."""
    marker = item.get_closest_marker("catalog")
    return dict(marker.kwargs) if marker is not None else {}


def _builtin_fixture_names() -> set[str]:
    # pytest/pluggy-provided fixtures we don't want to surface as "test inputs".
    return {
        "request", "pytestconfig", "cache", "capsys", "capsysbinary", "capfd",
        "capfdbinary", "capteesys", "doctest_namespace", "monkeypatch", "recwarn",
        "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory", "caplog",
        "record_property", "record_testsuite_property", "record_xml_attribute",
        "event_loop", "event_loop_policy", "anyio_backend", "unused_tcp_port",
        "unused_udp_port",
    }


def _resolve_fixtures(
    item: pytest.Item, fixturenames: list[str]
) -> list[dict[str, str]]:
    """For each requested fixture, resolve its defining file + docstring summary.

    Surfaces *where a test pulls its inputs from* — the conftest/module that
    defines each fixture and the first line of that fixture's docstring.
    """
    fm = getattr(item.session, "_fixturemanager", None)
    builtins = _builtin_fixture_names()
    resolved: list[dict[str, str]] = []
    for name in fixturenames:
        if name in builtins or name == "row":
            # 'row' is the dynamic parametrize target, not a real fixture def.
            if name != "row":
                continue
        entry: dict[str, str] = {"name": name, "defined_in": "", "doc": ""}
        defs = None
        if fm is not None:
            try:
                # pytest 9's getfixturedefs takes the requesting Node (older
                # pytest took a nodeid str); try the node first, fall back.
                defs = fm.getfixturedefs(name, item)
            except Exception:
                try:
                    defs = fm.getfixturedefs(name, item.nodeid)
                except Exception:
                    defs = None
        if defs:
            fixturedef = defs[-1]  # nearest override wins
            func = getattr(fixturedef, "func", None)
            if func is not None:
                try:
                    src = inspect.getsourcefile(func) or ""
                except TypeError:
                    src = ""
                entry["defined_in"] = _repo_relative(src)
                entry["doc"] = _first_line(inspect.getdoc(func))
        resolved.append(entry)
    return resolved


def _repo_relative(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        return p.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return p.name


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """Best-effort JSON-safe coercion for parametrize params / marker payloads."""
    if _depth > 6:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    model_dump = getattr(value, "model_dump", None)  # pydantic BaseModel
    if callable(model_dump):
        try:
            return _json_safe(model_dump(), _depth + 1)
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, _depth + 1) for v in value]
    return str(value)


def _example_input(item: pytest.Item, marker: dict[str, Any]) -> Any:
    if "example_input" in marker:
        return _json_safe(marker["example_input"])
    callspec = getattr(item, "callspec", None)
    if callspec is not None and getattr(callspec, "params", None):
        return _json_safe(dict(callspec.params))
    return None


def _example_output(item: pytest.Item, marker: dict[str, Any], summary: str) -> Any:
    if "example_output" in marker:
        return _json_safe(marker["example_output"])
    return None


def _item_to_record(item: pytest.Item) -> dict[str, Any]:
    marker = _catalog_marker(item)
    module_doc = _first_line(getattr(getattr(item, "module", None), "__doc__", None))
    func = getattr(item, "function", None)
    func_doc = _first_line(inspect.getdoc(func)) if func is not None else ""

    summary = (
        marker.get("summary")
        or func_doc
        or module_doc
        or _summary_fallback_from_name(item.name)
    )

    fixturenames = list(getattr(item, "fixturenames", []) or [])
    file_path, line_no, _ = item.location  # (relpath, 0-based line, testname)
    callspec = getattr(item, "callspec", None)

    return {
        "nodeid": item.nodeid,
        "name": item.name,
        "base_name": item.originalname if hasattr(item, "originalname") else item.name,
        "file": Path(file_path).as_posix(),
        "line": (line_no + 1) if isinstance(line_no, int) else None,
        "type": _detect_type(item),
        "component": _detect_component(item.nodeid),
        "summary": summary,
        "module_doc": module_doc,
        "func_doc": func_doc,
        "param_id": getattr(callspec, "id", None) if callspec is not None else None,
        "fixtures": _resolve_fixtures(item, fixturenames),
        "skip_reason": _skip_reason(item),
        "curated": bool(marker),
        "example_input": _example_input(item, marker),
        "example_output": _example_output(item, marker, summary),
    }


def pytest_collection_finish(session: pytest.Session) -> None:
    config = session.config
    if not config.getoption("--test-catalog"):
        return

    # session.items is the FINAL, post-deselection list, so the catalog honours
    # -m / -k selectors and any path scoping exactly as the run would.
    records: list[dict[str, Any]] = []
    for item in session.items:
        try:
            records.append(_item_to_record(item))
        except Exception as exc:  # never break collection because of the catalog
            records.append({
                "nodeid": getattr(item, "nodeid", "<unknown>"),
                "name": getattr(item, "name", "<unknown>"),
                "type": "unlabeled",
                "component": "other",
                "summary": f"(catalog error: {exc})",
                "fixtures": [],
                "example_input": None,
                "example_output": None,
            })

    from qaai.testcatalog.render import write_catalog

    out_dir = Path(config.getoption("--test-catalog-out"))
    source_label = " ".join(str(a) for a in config.invocation_params.args) or "pytest"
    json_path, html_path = write_catalog(records, out_dir, source_label=source_label)

    reporter = config.pluginmanager.get_plugin("terminalreporter")
    msg = f"\ntest catalog: {len(records)} tests -> {html_path}  (data: {json_path})"
    if reporter is not None:
        reporter.write_line(msg)
    else:
        print(msg)
