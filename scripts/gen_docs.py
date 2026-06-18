"""House-style HTML documentation generator for QAAI.

Generates the self-contained documentation pages that live under ``docs/`` — the
same look as ``docs/api.html`` and ``docs/design/*.html``. Every page it emits is
a single ``.html`` file with **inline CSS + JS and no external/CDN dependency**, so
it opens offline and is safe to archive as regulatory evidence.

Why this module exists
----------------------
The ``docs/`` pages were originally produced ad hoc (by an AI doc skill) with no
committed generator, and they drifted:

* **S1** — three design pages shipped the sidebar's ``a.active`` CSS *and* a TOC,
  but omitted the IntersectionObserver script, so the active-link highlight
  silently never fired on exactly those pages.
* **S2** — the diagram ``<img>`` tags had no intrinsic ``width``/``height``, so the
  page reflowed (cumulative layout shift) when the PNG loaded.
* **S3** — wide multi-column tables could force horizontal scroll at a ~360px
  viewport because nothing constrained them on small screens.

This generator bakes the fixes into the template so new pages are correct *by
construction*:

* ``render_doc_page`` always emits the scroll-spy script (**S1**).
* ``figure(...)`` requires ``width``/``height`` and the stylesheet sets
  ``figure img{height:auto}`` + ``loading="lazy"`` (**S2**).
* the narrow-screen media query wraps long table cells
  (``overflow-wrap:anywhere``) so tables don't overflow (**S3**).

Public API
----------
* ``render_doc_page(...) -> str`` — a sidebar-TOC content page (api/design style).
* ``render_hub_page(...) -> str`` — a card-grid landing page (``index.html`` style).
* ``figure(src, alt, width, height, caption="") -> str`` — a sized, lazy ``<figure>``.
* ``note(html, warn=False) -> str`` — a ``.note`` / ``.note.warn`` callout.
* ``table(headers, rows) -> str`` — a house-style ``<table>``.
* ``esc(text) -> str`` — HTML-escape a plain-text string.

Run ``python scripts/gen_docs.py`` to write a demonstration page exercising all
three fixes to ``scripts/gen_docs_demo.html``.

Content model
-------------
``title`` / ``toc_title`` / section labels / card headings are **plain text** and
are escaped for you. ``body`` / ``meta`` / ``footer`` / card descriptions / table
cells are **HTML** — build them with the helpers (``figure``/``note``/``table``)
or hand-write house-style markup (``<code>``, ``<span class="src">``,
``<span class="pill post">``, ``<pre class="diagram">`` …). Each ``(anchor_id,
label)`` in ``sections`` must match a ``<h2 id="anchor_id">`` you put in ``body`` —
that pairing drives both the sidebar TOC and the scroll-spy.
"""

from __future__ import annotations

from html import escape as _escape
from typing import Iterable, Sequence

__all__ = [
    "render_doc_page",
    "render_hub_page",
    "figure",
    "note",
    "table",
    "esc",
]


def esc(text: object) -> str:
    """HTML-escape a plain-text string (escapes ``& < > " '``)."""
    return _escape(str(text), quote=True)


# --- canonical house stylesheets (verbatim from docs/, plus the S2/S3 fixes) ----
#
# ``__MAXW__`` is substituted with the page's max content width. The only
# deltas from the shipped docs/ pages are flagged inline: figure img height:auto
# (S2), the small-screen cell wrap (S3), and a :focus-visible affordance.

_CONTENT_CSS = """\
  :root{--bg:#fff;--ink:#1a1a1a;--mute:#5a6472;--line:#e3e6ea;--accent:#2b62c2;
    --code-bg:#f6f8fa;--sidebar:#fafbfc;--ok:#1b7f3b;--warn:#9a6700;--bad:#b22;--maxw:__MAXW__px}
  @media(prefers-color-scheme:dark){:root{--bg:#0f1419;--ink:#e6e8eb;--mute:#9aa4b2;
    --line:#222a33;--accent:#6ea8ff;--code-bg:#161b22;--sidebar:#11161c;--ok:#5bd07e;--warn:#e3b341;--bad:#ff7b72}}
  *{box-sizing:border-box}html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .layout{display:grid;grid-template-columns:266px 1fr;min-height:100vh}
  nav.toc{position:sticky;top:0;align-self:start;height:100vh;overflow:auto;padding:24px 18px;background:var(--sidebar);border-right:1px solid var(--line);font-size:14px}
  nav.toc strong{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 8px}
  nav.toc a{display:block;color:var(--mute);text-decoration:none;padding:3px 0}
  nav.toc a:hover,nav.toc a.active{color:var(--accent)}
  nav.toc a:focus-visible{color:var(--accent);outline:2px solid var(--accent);outline-offset:2px}
  main{max-width:var(--maxw);padding:32px 30px 90px;margin:0 auto;width:100%}
  header.doc{border-bottom:1px solid var(--line);margin-bottom:8px;padding-bottom:12px}
  header.doc .meta{color:var(--mute);font-size:13px}
  h1,h2,h3{line-height:1.25;scroll-margin-top:14px}
  h2{margin-top:40px;border-bottom:1px solid var(--line);padding-bottom:6px}
  h3{margin-top:26px}
  a{color:var(--accent)}
  code{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code-bg);padding:1px 5px;border-radius:4px}
  pre{background:var(--code-bg);border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto}
  pre code{background:none;padding:0}
  pre.diagram{line-height:1.35}
  table{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0}
  th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
  th{background:var(--code-bg)}
  .src{color:var(--mute);font:12px ui-monospace,monospace}
  .pill{display:inline-block;font:12px ui-monospace,monospace;padding:1px 7px;border-radius:999px;border:1px solid var(--line)}
  .get{color:var(--ok)}.post{color:var(--accent)}
  .note{border-left:3px solid var(--accent);background:var(--code-bg);padding:10px 14px;border-radius:0 6px 6px 0;margin:14px 0}
  .warn{border-left-color:var(--warn)}
  figure{margin:18px 0}
  figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:#fff}
  figure figcaption{color:var(--mute);font-size:13px;margin-top:6px}
  footer.doc{margin-top:48px;padding-top:14px;border-top:1px solid var(--line);color:var(--mute);font-size:13px}
  @media(max-width:760px){.layout{grid-template-columns:1fr}nav.toc{position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}th,td{overflow-wrap:anywhere}}
  @media print{nav.toc{display:none}.layout{grid-template-columns:1fr}}"""

_HUB_CSS = """\
  :root{--bg:#fff;--ink:#1a1a1a;--mute:#5a6472;--line:#e3e6ea;--accent:#2b62c2;--code-bg:#f6f8fa;--maxw:__MAXW__px}
  @media(prefers-color-scheme:dark){:root{--bg:#0f1419;--ink:#e6e8eb;--mute:#9aa4b2;--line:#222a33;--accent:#6ea8ff;--code-bg:#161b22}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  main{max-width:var(--maxw);margin:0 auto;padding:48px 24px 80px}
  h1{margin:0 0 4px}
  .meta{color:var(--mute);font-size:13px;margin-bottom:28px}
  a{color:inherit;text-decoration:none}
  .card{display:block;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0;transition:border-color .15s,transform .15s}
  .card:hover{border-color:var(--accent);transform:translateY(-1px)}
  .card:focus-visible{border-color:var(--accent);outline:2px solid var(--accent);outline-offset:2px}
  .card h2{margin:0 0 4px;font-size:17px;color:var(--accent)}
  .card p{margin:0;color:var(--mute);font-size:14px}
  footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--line);color:var(--mute);font-size:13px}
  code{font:13px ui-monospace,Menlo,monospace;background:var(--code-bg);padding:1px 5px;border-radius:4px}"""

# S1: the scroll-spy that highlights the in-view section's TOC link. Emitted on
# every content page so the active-link affordance can never silently go missing.
_SCROLLSPY_JS = """\
  const links=[...document.querySelectorAll('nav.toc a[href^="#"]')];
  const byId=new Map(links.map(a=>[a.getAttribute('href').slice(1),a]));
  const obs=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){
    links.forEach(l=>l.classList.remove('active'));
    const a=byId.get(e.target.id); if(a)a.classList.add('active');}});},
    {rootMargin:'-10% 0px -80% 0px'});
  document.querySelectorAll('main [id]').forEach(s=>obs.observe(s));"""


# --- component helpers ----------------------------------------------------------

def figure(
    src: str,
    alt: str,
    width: int,
    height: int,
    caption: str = "",
    *,
    lazy: bool = True,
) -> str:
    """A ``<figure>`` with a correctly sized, lazy-loaded ``<img>`` (fixes S2).

    ``width``/``height`` are **required** and are the image's intrinsic pixel
    dimensions — they reserve the aspect ratio so the page does not reflow when
    the image loads. ``alt`` is escaped; ``caption`` is HTML (allows ``<code>``).
    """
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError("figure() needs the image's intrinsic width and height in px")
    loading = ' loading="lazy"' if lazy else ""
    cap = f"\n  <figcaption>{caption}</figcaption>" if caption else ""
    return (
        "<figure>\n"
        f'  <img src="{esc(src)}" alt="{esc(alt)}" '
        f'width="{int(width)}" height="{int(height)}"{loading}>'
        f"{cap}\n</figure>"
    )


def note(body_html: str, *, warn: bool = False) -> str:
    """A ``.note`` callout (``.note.warn`` when ``warn=True``). ``body_html`` is HTML."""
    cls = "note warn" if warn else "note"
    return f'<div class="{cls}">{body_html}</div>'


def table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A house-style ``<table>``. Header and cell values are HTML (allow ``<code>``)."""
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>\n{body}\n</tbody></table>"


# --- page builders --------------------------------------------------------------

def render_doc_page(
    *,
    title: str,
    sections: Sequence[tuple[str, str]],
    body: str,
    toc_title: str | None = None,
    meta: str = "",
    more_docs: Sequence[tuple[str, str]] = (),
    footer: str = "",
    lang: str = "en",
    site_name: str = "QAAI",
    maxw: int = 860,
) -> str:
    """Render a sidebar-TOC content page (the ``docs/api.html`` / design layout).

    Parameters
    ----------
    title:      plain text — used in ``<title>`` and the ``<h1>``.
    sections:   ``(anchor_id, label)`` pairs — the in-page TOC. Each ``anchor_id``
                must match a ``<h2 id="anchor_id">`` inside ``body``.
    body:       the main content as HTML (use ``figure``/``note``/``table``).
    toc_title:  bold label atop the sidebar (defaults to ``title``).
    meta:       HTML for the ``.meta`` line under the ``<h1>`` (optional).
    more_docs:  ``(href, label)`` cross-links shown under a "More docs" heading.
    footer:     footer inner HTML (optional; a default is supplied).
    """
    toc_title = toc_title or title
    css = _CONTENT_CSS.replace("__MAXW__", str(int(maxw)))
    toc_links = "\n".join(
        f'  <a href="#{esc(sid)}">{esc(label)}</a>' for sid, label in sections
    )
    more_html = ""
    if more_docs:
        items = "\n".join(
            f'  <a href="{esc(href)}">{esc(label)}</a>' for href, label in more_docs
        )
        more_html = f'\n  <strong style="margin-top:16px">More docs</strong>\n{items}'
    meta_html = f'\n  <div class="meta">{meta}</div>' if meta else ""
    footer_html = footer or f"Generated by gen_docs.py · {esc(site_name)}"
    return f"""<!doctype html>
<html lang="{esc(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(site_name)} · {esc(title)}</title>
<style>
{css}
</style>
</head>
<body>
<div class="layout">
<nav class="toc" aria-label="Table of contents">
  <strong>{esc(toc_title)}</strong>
{toc_links}{more_html}
</nav>
<main>
<header class="doc">
  <h1>{esc(title)}</h1>{meta_html}
</header>

{body}

<footer class="doc">{footer_html}</footer>
</main>
</div>
<script>
{_SCROLLSPY_JS}
</script>
</body>
</html>
"""


def render_hub_page(
    *,
    title: str,
    cards: Sequence[tuple[str, str, str]],
    intro: str = "",
    meta: str = "",
    footer: str = "",
    lang: str = "en",
    site_name: str = "QAAI",
    maxw: int = 760,
) -> str:
    """Render a card-grid landing/hub page (the ``docs/index.html`` layout).

    ``cards`` are ``(href, heading, description)`` — ``heading`` is plain text,
    ``description`` is HTML. ``intro`` / ``meta`` / ``footer`` are HTML.
    """
    css = _HUB_CSS.replace("__MAXW__", str(int(maxw)))
    cards_html = "\n\n".join(
        f'<a class="card" href="{esc(href)}"><h2>{esc(heading)}</h2>\n<p>{desc}</p></a>'
        for href, heading, desc in cards
    )
    meta_html = f'<div class="meta">{meta}</div>\n' if meta else ""
    intro_html = f"{intro}\n\n" if intro else ""
    footer_html = footer or f"Generated by gen_docs.py · {esc(site_name)}"
    return f"""<!doctype html>
<html lang="{esc(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(site_name)} · {esc(title)}</title>
<style>
{css}
</style>
</head>
<body>
<main>
<h1>{esc(title)}</h1>
{meta_html}{intro_html}{cards_html}

<footer>{footer_html}</footer>
</main>
</body>
</html>
"""


# --- demonstration --------------------------------------------------------------

def _demo() -> str:
    """Build a content page that exercises all three baked-in fixes (S1/S2/S3)."""
    # A 1x1 PNG data URI keeps the demo fully self-contained (no network image).
    png_1x1 = (
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAw"
        "CAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    body = "\n\n".join([
        '<h2 id="overview">Overview</h2>',
        "<p>This page was produced by <code>scripts/gen_docs.py</code> to show the "
        "house-style generator. It is a single self-contained file and exercises the "
        "three fixes baked into the template.</p>",
        note(
            "<strong>S1 — scroll-spy.</strong> This page ships the IntersectionObserver "
            "script, so the active sidebar link tracks the section in view as you scroll."
        ),
        '<h2 id="diagram">Diagram (S2)</h2>',
        "<p>The figure carries intrinsic <code>width</code>/<code>height</code> and "
        '<code>loading="lazy"</code>, reserving its aspect ratio so the page does not '
        "reflow when the image loads:</p>",
        figure(png_1x1, "Example diagram", 600, 300, "A sized, lazy-loaded figure (placeholder)."),
        '<h2 id="table">Wide table (S3)</h2>',
        "<p>At a ~360px viewport the cells wrap instead of forcing horizontal scroll:</p>",
        table(
            ["Code", "Dimension", "Verdict", "Source", "Owner", "Notes"],
            [
                ["<strong>M1</strong>", "Functional coverage", "Yes / No",
                 '<span class="src">core.py:1</span>', "reviewer",
                 "positive behaviour covered by &ge;1 test case"],
                ["<strong>M2</strong>", "Negative coverage", "Yes / No / N-A",
                 '<span class="src">core.py:2</span>', "reviewer",
                 "error / invalid-input handling is tested"],
            ],
        ),
    ])
    return render_doc_page(
        title="Generator demo",
        toc_title="Demo",
        meta="Produced by <code>scripts/gen_docs.py</code>",
        sections=[("overview", "Overview"), ("diagram", "Diagram (S2)"), ("table", "Wide table (S3)")],
        more_docs=[("../docs/index.html", "← Docs home")],
        body=body,
        footer="Demonstration page generated by gen_docs.py.",
    )


if __name__ == "__main__":
    from pathlib import Path

    out = Path(__file__).with_name("gen_docs_demo.html")
    out.write_text(_demo(), encoding="utf-8")
    print(f"wrote {out}")
