"""Single source of truth for synod model branding.

Each AI provider has a brand color used to visually distinguish its output
in HUDs (rich terminal) and markdown summaries (Phase 4 collapsible).
This module centralizes the mapping so HUD and markdown layers stay in
sync.

Hex sources (verified 2026-05):
- claude — claude.ai logo SVG fill
- gemini — Google Design Library, dominant of brand gradient
- openai — openai.com / platform.openai.com signature teal-green
"""

from __future__ import annotations


# Glyph used as the brand color marker in markdown surfaces.
# Wrapped in <span style="color:{hex}"> so the box itself renders in the
# model's brand color. A small sized square keeps the marker compact and
# distinct from emoji prefixes (which are larger and less precise).
GLYPH: str = "◾"  # BLACK MEDIUM SMALL SQUARE (U+25FE)


BRANDING: dict[str, dict[str, str]] = {
    "claude": {
        "label": "Claude",
        "hex": "#D97757",     # warm coral
        "rich": "orange3",    # Rich named-color when truecolor unavailable
    },
    "gemini": {
        "label": "Gemini",
        "hex": "#4285F4",     # Google Blue
        "rich": "blue",
    },
    "openai": {
        "label": "OpenAI",
        "hex": "#10A37F",     # signature teal-green
        "rich": "green",
    },
}


def get(model: str) -> dict[str, str]:
    """Return the branding dict for a model key. Case-insensitive.

    Raises KeyError for unknown models — callers pass one of the three
    keys in BRANDING; an unknown key signals a real bug, not a rendering
    edge case.
    """
    return BRANDING[model.lower()]


def markdown_marker(model: str) -> str:
    """Return the HTML-spanned colored glyph that marks output from `model`
    in markdown surfaces.

    Example: ``markdown_marker("claude") == '<span style="color:#D97757">◾</span>'``.
    """
    hex_code = get(model)["hex"]
    return f'<span style="color:{hex_code}">{GLYPH}</span>'
