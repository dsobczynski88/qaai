"""Shared assembly for the single-file viewer templates.

Every reviewer's viewer is the common layout + base CSS + shared JS, followed by that
reviewer's own ``style.css`` and ``script.js``. Only the asset subdirectory and the
in-page header differ, so the assembly lives here once and each ``template*.py`` module
supplies those two values.
"""

from pathlib import Path
from typing import Sequence

_VIEWER_DIR = Path(__file__).parent
_COMMON = _VIEWER_DIR / "common"

#: Common JS bundled ahead of a reviewer viewer's own script.js. ``dom.js`` holds the
#: schema-agnostic primitives (escaping, modal); ``shared.js`` holds the reviewer
#: feedback pane, which the dataset-studio editor replaces wholesale.
REVIEWER_JS = ("dom.js", "shared.js")


def load_template(
    subdir: str,
    header_title: str,
    *,
    layout: str = "layout.html",
    common_js: Sequence[str] = REVIEWER_JS,
) -> str:
    """Assemble the single-file template for the viewer whose assets live in ``subdir``.

    Returns the template with ``{{CSS}}``/``{{JS}}``/``{{HEADER_TITLE}}`` already
    substituted. The per-run placeholders — ``{{TITLE}}``, ``{{SOURCE}}``,
    ``{{RUN_KEY}}``, ``{{DATA}}``, ``{{LOG}}``, ``{{REVIEW_TYPE}}`` — are left for
    :func:`qaai.viewer.generator._render` to fill.

    ``layout`` and ``common_js`` exist for the dataset-studio editor, whose right pane
    is an edit form rather than the reviewer rating/notes pane, so it needs a different
    shell and a different JS bundle. The defaults reproduce the reviewer viewers
    byte-for-byte.
    """
    here = _VIEWER_DIR / subdir
    css = (_COMMON / "base.css").read_text(encoding="utf-8") + (here / "style.css").read_text(encoding="utf-8")
    js = "\n".join((_COMMON / name).read_text(encoding="utf-8") for name in common_js)
    js += "\n" + (here / "script.js").read_text(encoding="utf-8")
    return (
        (_COMMON / layout).read_text(encoding="utf-8")
        .replace("{{CSS}}", css)
        .replace("{{JS}}", js)
        .replace("{{HEADER_TITLE}}", header_title)
    )
