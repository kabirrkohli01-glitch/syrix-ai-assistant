"""Colour themes for generated documents.

One stylesheet for everything was the whole problem. Every PDF Supercomputer
made looked like the same grey memo: same accent, same spacing, same byline. A
CV, a trading setup and an invoice are not the same document and should not come
out looking like one, so a document is now a template plus an independent colour
theme.

A theme is a small set of CSS custom properties, and every template consumes the
same names — so template and theme are genuinely independent and adding either
does not touch the other.

Print discipline lives here too. These pages are rendered by Chromium's print
engine against a local origin with no network, so: no web fonts, no external
CSS, no images that are not embedded as data URIs. Colours are chosen to survive
being printed on a mono printer, which is why every theme keeps real contrast in
the text rather than relying on the accent to carry meaning.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

THEMES: Dict[str, Dict[str, str]] = {
    "slate": {
        "label": "Slate", "accent": "#334155", "accent_soft": "#e2e8f0",
        "ink": "#0f172a", "muted": "#64748b", "rule": "#cbd5e1",
        "wash": "#f8fafc", "link": "#1d4ed8",
    },
    "navy": {
        "label": "Navy", "accent": "#1e3a8a", "accent_soft": "#dbeafe",
        "ink": "#0c1a3d", "muted": "#5b6b8c", "rule": "#c7d5f0",
        "wash": "#f6f9ff", "link": "#1d4ed8",
    },
    "forest": {
        "label": "Forest", "accent": "#166534", "accent_soft": "#dcfce7",
        "ink": "#0f2417", "muted": "#5c7a68", "rule": "#c2ddcb",
        "wash": "#f6fbf7", "link": "#15803d",
    },
    "plum": {
        "label": "Plum", "accent": "#6b21a8", "accent_soft": "#f3e8ff",
        "ink": "#2a1040", "muted": "#7c6890", "rule": "#ddc9ec",
        "wash": "#fbf7fe", "link": "#7e22ce",
    },
    "ember": {
        "label": "Ember", "accent": "#9a3412", "accent_soft": "#ffedd5",
        "ink": "#3b1a0c", "muted": "#8a6a5c", "rule": "#eccdb8",
        "wash": "#fffaf6", "link": "#c2410c",
    },
    "mono": {
        "label": "Mono", "accent": "#111111", "accent_soft": "#ebebeb",
        "ink": "#111111", "muted": "#666666", "rule": "#cccccc",
        "wash": "#fafafa", "link": "#111111",
    },
}

DEFAULT_THEME = "slate"

# Which theme suits which template when nobody said. A CV wants to look
# considered and not shouty; a trading setup is read on a screen and benefits
# from a strong accent; an invoice should be sober.
_SUGGESTED = {
    "cv": "navy", "cv_modern": "navy", "cv_minimal": "mono",
    "report": "slate", "proposal": "plum", "one_pager": "ember",
    "comparison": "slate", "invoice": "mono", "letter": "mono",
    "trading_setup": "forest",
}


def resolve(name: str = "", *, template: str = "",
            config: Optional[Dict[str, Any]] = None) -> str:
    """A real theme key, from what was asked for, the template, or the default."""
    key = str(name or "").strip().lower().replace(" ", "_")
    if key in THEMES:
        return key
    suggested = _SUGGESTED.get(str(template or "").strip().lower())
    if suggested in THEMES:
        return suggested
    configured = str((config or {}).get("supercomputer_doc_theme", "") or "").strip().lower()
    if configured in THEMES:
        return configured
    return DEFAULT_THEME


def variables(theme: str) -> Dict[str, str]:
    return dict(THEMES.get(theme, THEMES[DEFAULT_THEME]))


def css_root(theme: str) -> str:
    """The `:root { --sc-*: … }` block a template's stylesheet builds on."""
    values = variables(theme)
    lines = [f"  --sc-{key.replace('_', '-')}: {value};"
             for key, value in values.items() if key != "label"]
    return ":root {\n" + "\n".join(lines) + "\n}"


def names() -> List[str]:
    return list(THEMES)


def describe() -> str:
    return ", ".join(f"{k} ({v['label']})" for k, v in THEMES.items())


__all__ = ["THEMES", "DEFAULT_THEME", "resolve", "variables", "css_root",
           "names", "describe"]
