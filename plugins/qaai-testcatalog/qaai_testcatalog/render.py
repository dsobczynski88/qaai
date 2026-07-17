"""Render collected test records into a single self-contained HTML catalog.

Mirrors ``qaai/viewer/generator.py``: assets are concatenated at import time and
the per-run data is injected with plain ``str.replace()`` placeholders (not Jinja2,
so the template stays browser-openable and never clashes with the page's own JS).
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
from typing import Iterable, Union

PathLike = Union[str, pathlib.Path]

_ASSETS = pathlib.Path(__file__).parent / "assets"

_CSS = (_ASSETS / "catalog.css").read_text(encoding="utf-8")
_JS = (_ASSETS / "catalog.js").read_text(encoding="utf-8")
_LAYOUT = (_ASSETS / "layout.html").read_text(encoding="utf-8")

# Bake CSS/JS into the layout once (build-time placeholders).
_HTML_TEMPLATE = _LAYOUT.replace("{{CSS}}", _CSS).replace("{{JS}}", _JS)


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_catalog_html(
    records: Iterable[dict], source_label: str = "pytest"
) -> str:
    """Return the full HTML string for the given catalog records."""
    data_json = json.dumps(list(records), ensure_ascii=False, default=str)
    data_json = data_json.replace("</", "<\\/")  # JSON-injection safety
    generated = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        _HTML_TEMPLATE
        .replace("{{DATA}}", data_json)
        .replace("{{TITLE}}", _escape_html(f"Test Catalog — {source_label}"))
        .replace("{{SOURCE}}", _escape_html(source_label))
        .replace("{{GENERATED}}", _escape_html(generated))
    )


def write_catalog(
    records: Iterable[dict],
    out_dir: PathLike,
    source_label: str = "pytest",
) -> tuple[pathlib.Path, pathlib.Path]:
    """Write ``test_catalog.json`` + ``test_catalog.html`` into ``out_dir``.

    Returns ``(json_path, html_path)``. Creates ``out_dir`` if needed.
    """
    records = list(records)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "test_catalog.json"
    html_path = out / "test_catalog.html"

    json_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    html_path.write_text(
        build_catalog_html(records, source_label=source_label), encoding="utf-8"
    )
    return json_path, html_path


def write_catalog_from_json(
    json_path: PathLike, output_path: PathLike | None = None
) -> pathlib.Path:
    """Re-render the HTML from a previously written ``test_catalog.json``."""
    src = pathlib.Path(json_path)
    records = json.loads(src.read_text(encoding="utf-8"))
    out = pathlib.Path(output_path) if output_path else src.with_name("test_catalog.html")
    out.write_text(
        build_catalog_html(records, source_label=src.as_posix()), encoding="utf-8"
    )
    return out
