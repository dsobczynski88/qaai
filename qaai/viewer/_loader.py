"""Shared assembly for the single-file viewer templates.

Every reviewer's viewer is the common layout + base CSS + shared JS, followed by that
reviewer's own ``style.css`` and ``script.js``. Only the asset subdirectory and the
in-page header differ, so the assembly lives here once and each ``template*.py`` module
supplies those two values.
"""

from pathlib import Path

_VIEWER_DIR = Path(__file__).parent
_COMMON = _VIEWER_DIR / "common"


def load_template(subdir: str, header_title: str) -> str:
    """Assemble the viewer template for the reviewer whose assets live in ``subdir``.

    Returns the template with ``{{CSS}}``/``{{JS}}``/``{{HEADER_TITLE}}`` already
    substituted. The per-run placeholders — ``{{TITLE}}``, ``{{SOURCE}}``,
    ``{{RUN_KEY}}``, ``{{DATA}}``, ``{{LOG}}``, ``{{REVIEW_TYPE}}`` — are left for
    :func:`qaai.viewer.generator._render` to fill.
    """
    here = _VIEWER_DIR / subdir
    css = (_COMMON / "base.css").read_text(encoding="utf-8") + (here / "style.css").read_text(encoding="utf-8")
    js = (_COMMON / "shared.js").read_text(encoding="utf-8") + "\n" + (here / "script.js").read_text(encoding="utf-8")
    return (
        (_COMMON / "layout.html").read_text(encoding="utf-8")
        .replace("{{CSS}}", css)
        .replace("{{JS}}", js)
        .replace("{{HEADER_TITLE}}", header_title)
    )
