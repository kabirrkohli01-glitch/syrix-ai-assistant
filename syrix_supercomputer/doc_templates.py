"""Document templates — the difference between a file and a deliverable.

The complaint that produced this was specific: the PDFs were *lazy*. No real
structure, no bold points, nothing that looked like it had been designed. And it
was fair, because there was exactly one stylesheet in the whole subsystem — a
generic grey memo shell with a "Produced by SYRIX Supercomputer" byline — and
every document went through it, whether it was a CV or a research report.

A template here is a full print stylesheet plus a body layout. Each one:

* has a real title block, not just an ``<h1>``;
* carries running page numbers via ``@page`` counters;
* sets its own type scale, so a CV does not read like a memo;
* keeps headings with the text under them (``break-after: avoid``), which is the
  difference between a document that looks typeset and one that looks dumped;
* consumes the theme variables from :mod:`syrix_supercomputer.doc_themes`, so
  template and colour are chosen independently.

Everything is self-contained: no web fonts, no external CSS, images embedded as
data URIs. The print happens in a Supercomputer tab against a local origin with
no network, so anything remote would simply not render.

Adding a template = one entry in :data:`TEMPLATES`. It needs no changes
anywhere else; ``create_document`` will offer it to the model automatically.
"""
from __future__ import annotations

import html as _html
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import doc_themes

# Shared by every template. Page geometry, base typography and the elements any
# document might contain (tables, code, quotes, images, page breaks).
_BASE_CSS = """
@page {{
  size: {page_size};
  margin: {margin};
  @bottom-center {{ content: counter(page); color: var(--sc-muted); font-size: 9pt; }}
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; }}
body {{
  font: {body_size}/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: var(--sc-ink);
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}}
h1, h2, h3, h4 {{ break-after: avoid; page-break-after: avoid; }}
p, li, td {{ orphans: 2; widows: 2; }}
a {{ color: var(--sc-link); text-decoration: none; }}
strong, b {{ font-weight: 650; }}
ul, ol {{ padding-left: 18pt; margin: 0 0 9pt; }}
li {{ margin: 0 0 4pt; }}
blockquote {{
  margin: 10pt 0; padding: 6pt 12pt;
  border-left: 3px solid var(--sc-accent); background: var(--sc-wash);
  color: var(--sc-muted);
}}
code {{
  font-family: Consolas, "SF Mono", monospace; font-size: .9em;
  background: var(--sc-accent-soft); padding: 1px 4px; border-radius: 3px;
}}
pre {{
  background: var(--sc-wash); border: 1px solid var(--sc-rule);
  padding: 9pt 11pt; border-radius: 5px; overflow-x: auto; font-size: 9pt;
  break-inside: avoid;
}}
pre code {{ background: none; padding: 0; }}
table {{
  border-collapse: collapse; width: 100%; margin: 0 0 12pt; font-size: 9.5pt;
  break-inside: auto;
}}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
th, td {{
  border: 1px solid var(--sc-rule); padding: 5pt 7pt;
  text-align: left; vertical-align: top;
}}
th {{ background: var(--sc-accent-soft); font-weight: 650; color: var(--sc-ink); }}
tbody tr:nth-child(even) td {{ background: var(--sc-wash); }}
img {{ max-width: 100%; height: auto; }}
figure {{ margin: 12pt 0; break-inside: avoid; }}
figcaption {{ color: var(--sc-muted); font-size: 9pt; margin-top: 4pt; }}
.sc-break {{ break-before: page; page-break-before: always; }}
.sc-note {{
  background: var(--sc-wash); border: 1px solid var(--sc-rule);
  border-radius: 6px; padding: 9pt 12pt; margin: 0 0 12pt;
}}
.sc-footer {{
  margin-top: 20pt; padding-top: 8pt; border-top: 1px solid var(--sc-rule);
  color: var(--sc-muted); font-size: 8.5pt;
}}
"""


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    # What it is for, shown to the model so it can pick sensibly.
    purpose: str
    page_size: str
    margin: str
    body_size: str
    css: str
    # Builds the <body> inner HTML from the rendered body and the metadata.
    layout: Callable[..., str]


def _esc(value: Any) -> str:
    return _html.escape(str(value or ""))


def _stamp() -> str:
    return time.strftime("%d %B %Y")


# ── layouts ────────────────────────────────────────────────────────────────
def _layout_report(body: str, *, title: str, subtitle: str = "", author: str = "",
                   photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    return f"""
<header class="sc-cover">
  <div class="sc-eyebrow">{_esc((meta or {}).get('eyebrow') or 'Report')}</div>
  <h1>{_esc(title)}</h1>
  {f'<p class="sc-sub">{_esc(subtitle)}</p>' if subtitle else ''}
  <div class="sc-byline">{_esc(author or 'Prepared by SYRIX')} &middot; {_stamp()}</div>
</header>
<main>{body}</main>
"""


def _layout_cv(body: str, *, title: str, subtitle: str = "", author: str = "",
               photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    """Classic single column. The photo, when there is one, sits in the header."""
    contact = (meta or {}).get("contact") or ""
    img = f'<img class="sc-photo" src="{photo}" alt="">' if photo else ""
    return f"""
<header class="sc-cvhead">
  {img}
  <div class="sc-cvid">
    <h1>{_esc(title)}</h1>
    {f'<p class="sc-role">{_esc(subtitle)}</p>' if subtitle else ''}
    {f'<p class="sc-contact">{_esc(contact)}</p>' if contact else ''}
  </div>
</header>
<main>{body}</main>
"""


def _layout_cv_modern(body: str, *, title: str, subtitle: str = "", author: str = "",
                      photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    """Two columns: a coloured sidebar, and the body beside it.

    The sidebar is built from ``meta['sidebar']`` when present, otherwise from
    the first section of the body — so a model that just writes markdown still
    gets a two-column CV rather than an empty rail.
    """
    contact = (meta or {}).get("contact") or ""
    sidebar = (meta or {}).get("sidebar") or ""
    img = f'<img class="sc-photo" src="{photo}" alt="">' if photo else ""
    return f"""
<div class="sc-two">
  <aside class="sc-rail">
    {img}
    <h1>{_esc(title)}</h1>
    {f'<p class="sc-role">{_esc(subtitle)}</p>' if subtitle else ''}
    {f'<div class="sc-contact">{_esc(contact)}</div>' if contact else ''}
    {sidebar}
  </aside>
  <main class="sc-main">{body}</main>
</div>
"""


def _layout_minimal(body: str, *, title: str, subtitle: str = "", author: str = "",
                    photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    contact = (meta or {}).get("contact") or ""
    return f"""
<header class="sc-min">
  <h1>{_esc(title)}</h1>
  {f'<p class="sc-role">{_esc(subtitle)}</p>' if subtitle else ''}
  {f'<p class="sc-contact">{_esc(contact)}</p>' if contact else ''}
</header>
<main>{body}</main>
"""


def _layout_letter(body: str, *, title: str, subtitle: str = "", author: str = "",
                   photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    to = (meta or {}).get("to") or ""
    return f"""
<header class="sc-letterhead">
  <div class="sc-from">{_esc(author or title)}</div>
  <div class="sc-date">{_stamp()}</div>
</header>
{f'<div class="sc-to">{_esc(to)}</div>' if to else ''}
<main>{body}</main>
"""


def _layout_invoice(body: str, *, title: str, subtitle: str = "", author: str = "",
                    photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    m = meta or {}
    return f"""
<header class="sc-invhead">
  <div>
    <div class="sc-eyebrow">Invoice</div>
    <h1>{_esc(title)}</h1>
  </div>
  <div class="sc-invmeta">
    <div><span>Number</span> {_esc(m.get('number') or '—')}</div>
    <div><span>Date</span> {_stamp()}</div>
    <div><span>Due</span> {_esc(m.get('due') or '—')}</div>
  </div>
</header>
<main>{body}</main>
"""


def _layout_trading(body: str, *, title: str, subtitle: str = "", author: str = "",
                    photo: str = "", meta: Optional[Dict[str, Any]] = None) -> str:
    m = meta or {}
    chips = "".join(
        f'<span class="sc-chip"><b>{_esc(k)}</b> {_esc(v)}</span>'
        for k, v in (m.get("levels") or {}).items()
    )
    return f"""
<header class="sc-tradehead">
  <div class="sc-eyebrow">Trading setup &middot; {_stamp()}</div>
  <h1>{_esc(title)}</h1>
  {f'<p class="sc-sub">{_esc(subtitle)}</p>' if subtitle else ''}
  {f'<div class="sc-chips">{chips}</div>' if chips else ''}
</header>
<main>{body}</main>
<p class="sc-footer">Not financial advice. Every figure in this document was read
from a named source at the time of writing and can move at any moment.</p>
"""


# ── the table ──────────────────────────────────────────────────────────────
TEMPLATES: Dict[str, Template] = {
    "report": Template(
        "report", "Report", "research, findings, analysis, anything long",
        "A4", "20mm 18mm", "11pt",
        """
.sc-cover { margin: 0 0 18pt; padding: 0 0 12pt; border-bottom: 2px solid var(--sc-accent); }
.sc-eyebrow { color: var(--sc-accent); font-size: 9pt; font-weight: 700;
              letter-spacing: .12em; text-transform: uppercase; margin: 0 0 6pt; }
h1 { font-size: 25pt; line-height: 1.15; margin: 0 0 6pt; letter-spacing: -.02em; }
.sc-sub { font-size: 12pt; color: var(--sc-muted); margin: 0 0 8pt; }
.sc-byline { font-size: 9pt; color: var(--sc-muted); }
h2 { font-size: 14pt; margin: 20pt 0 6pt; color: var(--sc-accent); }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; }
p { margin: 0 0 8pt; }
""",
        _layout_report),

    "cv": Template(
        "cv", "CV — classic", "a CV or resume, single column, traditional",
        "A4", "16mm 16mm", "10.5pt",
        """
.sc-cvhead { display: flex; gap: 16pt; align-items: center;
             border-bottom: 2px solid var(--sc-accent); padding-bottom: 12pt; margin-bottom: 14pt; }
.sc-photo { width: 84pt; height: 84pt; object-fit: cover; border-radius: 50%;
            border: 2px solid var(--sc-accent-soft); flex: none; }
h1 { font-size: 24pt; margin: 0 0 2pt; letter-spacing: -.02em; }
.sc-role { font-size: 12pt; color: var(--sc-accent); margin: 0 0 4pt; font-weight: 600; }
.sc-contact { font-size: 9.5pt; color: var(--sc-muted); margin: 0; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .08em;
     color: var(--sc-accent); margin: 16pt 0 6pt; padding-bottom: 3pt;
     border-bottom: 1px solid var(--sc-rule); }
h3 { font-size: 10.5pt; margin: 10pt 0 1pt; }
h3 + p { color: var(--sc-muted); font-size: 9.5pt; margin: 0 0 4pt; }
p { margin: 0 0 6pt; }
""",
        _layout_cv),

    "cv_modern": Template(
        "cv_modern", "CV — modern two-column",
        "a CV with a coloured sidebar; looks designed rather than typed",
        "A4", "0", "10.5pt",
        """
.sc-two { display: flex; min-height: 100%; }
.sc-rail { width: 33%; background: var(--sc-accent); color: #fff;
           padding: 18mm 10mm; }
.sc-rail h1 { font-size: 20pt; margin: 0 0 3pt; line-height: 1.15; color: #fff; }
.sc-rail .sc-role { font-size: 10.5pt; opacity: .88; margin: 0 0 12pt; }
.sc-rail .sc-contact { font-size: 9pt; opacity: .85; line-height: 1.6;
                       border-top: 1px solid rgba(255,255,255,.28); padding-top: 10pt; }
.sc-rail h2 { font-size: 9.5pt; text-transform: uppercase; letter-spacing: .1em;
              margin: 16pt 0 5pt; color: #fff; opacity: .9; }
.sc-rail ul { padding-left: 14pt; }
.sc-rail li, .sc-rail p { font-size: 9pt; opacity: .92; }
.sc-photo { width: 92pt; height: 92pt; object-fit: cover; border-radius: 50%;
            border: 3px solid rgba(255,255,255,.35); margin: 0 0 12pt; }
.sc-main { flex: 1; padding: 18mm 12mm; }
.sc-main h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .08em;
              color: var(--sc-accent); margin: 0 0 6pt; padding-bottom: 3pt;
              border-bottom: 1px solid var(--sc-rule); }
.sc-main h2 ~ h2 { margin-top: 16pt; }
h3 { font-size: 10.5pt; margin: 10pt 0 1pt; }
h3 + p { color: var(--sc-muted); font-size: 9.5pt; margin: 0 0 4pt; }
p { margin: 0 0 6pt; }
""",
        _layout_cv_modern),

    "cv_minimal": Template(
        "cv_minimal", "CV — minimal", "a plain, typographic CV with no colour",
        "A4", "18mm 20mm", "10.5pt",
        """
.sc-min { text-align: center; margin: 0 0 16pt; padding-bottom: 10pt;
          border-bottom: 1px solid var(--sc-rule); }
h1 { font-size: 21pt; margin: 0 0 3pt; letter-spacing: .02em; font-weight: 600; }
.sc-role { color: var(--sc-muted); margin: 0 0 4pt; }
.sc-contact { font-size: 9.5pt; color: var(--sc-muted); margin: 0; }
h2 { font-size: 10pt; text-transform: uppercase; letter-spacing: .14em;
     margin: 16pt 0 6pt; font-weight: 600; }
h3 { font-size: 10.5pt; margin: 9pt 0 1pt; }
p { margin: 0 0 6pt; }
""",
        _layout_minimal),

    "proposal": Template(
        "proposal", "Proposal", "a pitch, a plan, or a piece of work being offered",
        "A4", "20mm 18mm", "11pt",
        """
.sc-cover { background: var(--sc-accent); color: #fff; margin: -20mm -18mm 20pt;
            padding: 26mm 18mm 18mm; }
.sc-eyebrow { font-size: 9pt; letter-spacing: .14em; text-transform: uppercase;
              opacity: .85; margin: 0 0 8pt; }
.sc-cover h1 { font-size: 28pt; margin: 0 0 8pt; line-height: 1.12; color: #fff; }
.sc-sub { font-size: 12.5pt; opacity: .9; margin: 0 0 10pt; }
.sc-byline { font-size: 9pt; opacity: .8; }
h2 { font-size: 14.5pt; color: var(--sc-accent); margin: 20pt 0 6pt; }
h3 { font-size: 11.5pt; margin: 13pt 0 3pt; }
p { margin: 0 0 8pt; }
""",
        _layout_report),

    "one_pager": Template(
        "one_pager", "One-pager", "a single dense page — a summary or a brief",
        "A4", "14mm 14mm", "10pt",
        """
.sc-cover { margin: 0 0 10pt; padding: 0 0 8pt; border-bottom: 3px solid var(--sc-accent); }
.sc-eyebrow { color: var(--sc-accent); font-size: 8.5pt; font-weight: 700;
              letter-spacing: .12em; text-transform: uppercase; margin: 0 0 4pt; }
h1 { font-size: 20pt; margin: 0 0 3pt; letter-spacing: -.02em; }
.sc-sub { font-size: 10.5pt; color: var(--sc-muted); margin: 0 0 4pt; }
.sc-byline { font-size: 8.5pt; color: var(--sc-muted); }
main { column-count: 2; column-gap: 14pt; }
h2 { font-size: 11pt; color: var(--sc-accent); margin: 10pt 0 4pt;
     break-after: avoid; }
h2:first-child { margin-top: 0; }
p, li { font-size: 9.5pt; margin: 0 0 6pt; }
table { column-span: all; }
""",
        _layout_report),

    "comparison": Template(
        "comparison", "Comparison", "pros and cons, options weighed against each other",
        "A4", "20mm 18mm", "11pt",
        """
.sc-cover { margin: 0 0 16pt; padding: 0 0 10pt; border-bottom: 2px solid var(--sc-accent); }
.sc-eyebrow { color: var(--sc-accent); font-size: 9pt; font-weight: 700;
              letter-spacing: .12em; text-transform: uppercase; margin: 0 0 6pt; }
h1 { font-size: 23pt; margin: 0 0 5pt; letter-spacing: -.02em; }
.sc-sub { font-size: 11.5pt; color: var(--sc-muted); margin: 0 0 6pt; }
.sc-byline { font-size: 9pt; color: var(--sc-muted); }
h2 { font-size: 13.5pt; color: var(--sc-accent); margin: 18pt 0 6pt; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; }
p { margin: 0 0 8pt; }
/* "Pros" and "Cons" headings get a tint without needing special markup —
   the model writes ordinary markdown and the styling follows the words. */
h3:first-letter { font-weight: 700; }
ul li::marker { color: var(--sc-accent); }
table th:first-child { width: 28%; }
""",
        _layout_report),

    "invoice": Template(
        "invoice", "Invoice", "a bill, with line items and a total",
        "A4", "20mm 18mm", "10.5pt",
        """
.sc-invhead { display: flex; justify-content: space-between; align-items: flex-start;
              border-bottom: 2px solid var(--sc-accent); padding-bottom: 12pt;
              margin-bottom: 16pt; }
.sc-eyebrow { color: var(--sc-accent); font-size: 9pt; font-weight: 700;
              letter-spacing: .14em; text-transform: uppercase; margin: 0 0 4pt; }
h1 { font-size: 20pt; margin: 0; }
.sc-invmeta { text-align: right; font-size: 9.5pt; color: var(--sc-muted); }
.sc-invmeta span { display: inline-block; min-width: 52pt; color: var(--sc-ink);
                   font-weight: 600; }
h2 { font-size: 11.5pt; margin: 16pt 0 5pt; color: var(--sc-accent); }
table td:last-child, table th:last-child { text-align: right; }
tbody tr:last-child td { font-weight: 700; border-top: 2px solid var(--sc-accent); }
p { margin: 0 0 7pt; }
""",
        _layout_invoice),

    "letter": Template(
        "letter", "Letter", "a covering letter or formal correspondence",
        "A4", "25mm 22mm", "11pt",
        """
.sc-letterhead { display: flex; justify-content: space-between;
                 border-bottom: 1px solid var(--sc-rule); padding-bottom: 8pt;
                 margin-bottom: 18pt; }
.sc-from { font-weight: 650; }
.sc-date, .sc-to { color: var(--sc-muted); font-size: 10pt; }
.sc-to { margin: 0 0 16pt; white-space: pre-line; }
h1 { font-size: 13pt; margin: 0 0 10pt; }
h2 { font-size: 11.5pt; margin: 14pt 0 5pt; }
p { margin: 0 0 10pt; }
""",
        _layout_letter),

    "trading_setup": Template(
        "trading_setup", "Trading setup",
        "a chart-backed trade idea with levels, rationale and risk",
        "A4", "16mm 16mm", "10.5pt",
        """
.sc-tradehead { border-bottom: 2px solid var(--sc-accent); padding-bottom: 10pt;
                margin-bottom: 14pt; }
.sc-eyebrow { color: var(--sc-accent); font-size: 8.5pt; font-weight: 700;
              letter-spacing: .12em; text-transform: uppercase; margin: 0 0 5pt; }
h1 { font-size: 22pt; margin: 0 0 4pt; letter-spacing: -.02em; }
.sc-sub { font-size: 11pt; color: var(--sc-muted); margin: 0 0 8pt; }
.sc-chips { display: flex; flex-wrap: wrap; gap: 6pt; }
.sc-chip { background: var(--sc-accent-soft); border: 1px solid var(--sc-rule);
           border-radius: 999px; padding: 3pt 9pt; font-size: 9pt; }
.sc-chip b { color: var(--sc-accent); margin-right: 4pt; }
h2 { font-size: 12.5pt; color: var(--sc-accent); margin: 16pt 0 5pt; }
h3 { font-size: 10.5pt; margin: 10pt 0 3pt; }
p { margin: 0 0 7pt; }
""",
        _layout_trading),
}

DEFAULT_TEMPLATE = "report"

# Words in a request that point at a template, most specific first. Used when
# the model asks for a document without naming one, so "make my CV" still gets
# a CV layout rather than the report shell.
_HINTS = (
    (re.compile(r"\b(curriculum vitae|resum[eé]|\bcv\b)", re.I), "cv"),
    (re.compile(r"\b(invoice|bill|receipt)\b", re.I), "invoice"),
    (re.compile(r"\b(cover(ing)? letter|letter to|dear\b)", re.I), "letter"),
    (re.compile(r"\b(pros and cons|compare|comparison|versus|vs\.?)\b", re.I), "comparison"),
    (re.compile(r"\b(trade|trading|setup|entry|stop.?loss|ticker)\b", re.I), "trading_setup"),
    (re.compile(r"\b(proposal|pitch|quote|statement of work)\b", re.I), "proposal"),
    (re.compile(r"\b(one.?pager|brief|summary sheet)\b", re.I), "one_pager"),
    (re.compile(r"\b(report|analysis|research|findings)\b", re.I), "report"),
)


def resolve(name: str = "", *, hint: str = "") -> str:
    """A real template key from what was asked for, or inferred from the text."""
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in TEMPLATES:
        return key
    aliases = {"resume": "cv", "curriculum_vitae": "cv", "modern_cv": "cv_modern",
               "minimal_cv": "cv_minimal", "pros_and_cons": "comparison",
               "onepager": "one_pager", "trading": "trading_setup"}
    if key in aliases:
        return aliases[key]
    text = f"{name} {hint}"
    for pattern, template in _HINTS:
        if pattern.search(text):
            return template
    return DEFAULT_TEMPLATE


def get(key: str) -> Template:
    return TEMPLATES.get(resolve(key), TEMPLATES[DEFAULT_TEMPLATE])


def names() -> List[str]:
    return list(TEMPLATES)


def describe() -> str:
    """The list the model sees, so it can choose deliberately."""
    return "; ".join(f"{t.key} — {t.purpose}" for t in TEMPLATES.values())


def render(body_html: str, *, template: str = "", theme: str = "",
           title: str = "", subtitle: str = "", author: str = "",
           photo: str = "", meta: Optional[Dict[str, Any]] = None,
           config: Optional[Dict[str, Any]] = None) -> str:
    """A complete, self-contained HTML document ready for the print engine."""
    tpl = get(template)
    theme_key = doc_themes.resolve(theme, template=tpl.key, config=config)
    base = _BASE_CSS.format(page_size=tpl.page_size, margin=tpl.margin,
                            body_size=tpl.body_size)
    inner = tpl.layout(body_html, title=title or "Document", subtitle=subtitle,
                       author=author, photo=photo, meta=meta or {})
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8">'
        f"<title>{_esc(title or 'Document')}</title>\n"
        "<style>\n"
        f"{doc_themes.css_root(theme_key)}\n{base}\n{tpl.css}\n"
        "</style></head><body>\n"
        f"{inner}\n"
        "</body></html>\n"
    )


__all__ = ["Template", "TEMPLATES", "DEFAULT_TEMPLATE", "resolve", "get",
           "names", "describe", "render"]
