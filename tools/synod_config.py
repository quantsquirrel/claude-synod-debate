"""Synod mode configuration loader.

Loads mode definitions from config/synod-modes.yaml.
Provides typed access to mode parameters.
"""

import os
import yaml
from typing import Any, Optional


_CONFIG_CACHE: Optional[dict] = None


def _find_config_path() -> str:
    """Find synod-modes.yaml relative to this file."""
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(tools_dir)
    return os.path.join(project_root, "config", "synod-modes.yaml")


def load_config(force_reload: bool = False) -> dict:
    """Load and cache the synod modes configuration."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE

    config_path = _find_config_path()
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path, "r") as f:
        _CONFIG_CACHE = yaml.safe_load(f)

    return _CONFIG_CACHE


def get_mode_config(mode: str) -> dict:
    """Get configuration for a specific mode."""
    config = load_config()
    modes = config.get("modes", {})
    if mode not in modes:
        return modes.get("general", {})
    return modes[mode]


def get_model_config(mode: str, provider: str) -> dict:
    """Get model configuration for a mode and provider."""
    mode_config = get_mode_config(mode)
    return mode_config.get("models", {}).get(provider, {})


def get_focus(mode: str, provider: str) -> str:
    """Get focus area for a mode and provider."""
    mode_config = get_mode_config(mode)
    return mode_config.get("focus", {}).get(provider, "")


def get_rounds(mode: str) -> dict:
    """Get round configuration for a mode."""
    mode_config = get_mode_config(mode)
    return mode_config.get("rounds", {"base": 3, "dynamic_range": [2, 4]})


def get_complexity_rounds(score: float) -> int:
    """Get number of rounds based on complexity score."""
    config = load_config()
    complexity = config.get("complexity", {})

    if score < complexity.get("simple", {}).get("max_score", 0.5):
        return complexity.get("simple", {}).get("rounds", 2)
    elif score < complexity.get("medium", {}).get("max_score", 2.0):
        return complexity.get("medium", {}).get("rounds", 3)
    else:
        return complexity.get("complex", {}).get("rounds", 4)


def list_modes() -> list[str]:
    """List all available mode names."""
    config = load_config()
    return list(config.get("modes", {}).keys())
