"""Single source of truth for synod model branding.

Each AI provider has a brand color used to visually distinguish its output
in HUDs (rich terminal) and markdown summaries (emoji prefix). This module
centralizes the mapping so HUD and markdown layers stay in sync.

Hex sources (verified 2026-05):
- claude — claude.ai logo SVG fill
- gemini — Google Design Library, dominant of brand gradient
- openai — openai.com / platform.openai.com signature teal-green
"""

from __future__ import annotations


BRANDING: dict[str, dict[str, str]] = {
    "claude": {
        "label": "Claude",
        "hex": "#D97757",     # warm coral
        "emoji": "🟠",
        "rich": "orange3",    # Rich named-color when truecolor unavailable
    },
    "gemini": {
        "label": "Gemini",
        "hex": "#4285F4",     # Google Blue
        "emoji": "🔵",
        "rich": "blue",
    },
    "openai": {
        "label": "OpenAI",
        "hex": "#10A37F",     # signature teal-green
        "emoji": "🟢",
        "rich": "green",
    },
}


def get(model: str) -> dict[str, str]:
    """Return the branding dict for a model key. Case-insensitive.

    Raises KeyError for unknown models — callers should pass one of the
    three keys in BRANDING; an unknown key signals a real bug, not a
    rendering edge case.
    """
    return BRANDING[model.lower()]
