"""Tests for synod_config module."""

import pytest
import os
from tools.synod_config import (
    load_config,
    get_mode_config,
    get_model_config,
    get_focus,
    get_rounds,
    get_complexity_rounds,
    list_modes,
    _CONFIG_CACHE,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the config cache before each test."""
    import tools.synod_config as config_module
    config_module._CONFIG_CACHE = None
    yield
    config_module._CONFIG_CACHE = None


def test_load_config_returns_dict():
    """Test that load_config returns a dictionary."""
    config = load_config()
    assert isinstance(config, dict)
    assert "modes" in config
    assert "complexity" in config
    assert "timeouts" in config


def test_load_config_caches():
    """Test that load_config caches the configuration."""
    config1 = load_config()
    config2 = load_config()
    assert config1 is config2  # Same object reference


def test_load_config_force_reload():
    """Test that force_reload=True reloads the configuration."""
    config1 = load_config()
    config2 = load_config(force_reload=True)
    # They should be different objects but equal content
    assert config1 == config2


def test_get_mode_config_valid_mode():
    """Test get_mode_config for all 5 valid modes."""
    modes = ["review", "design", "debug", "idea", "general"]
    for mode in modes:
        config = get_mode_config(mode)
        assert isinstance(config, dict)
        assert "description" in config
        assert "models" in config
        assert "rounds" in config
        assert "focus" in config


def test_get_mode_config_unknown_falls_back_to_general():
    """Test that unknown mode falls back to general mode."""
    unknown_config = get_mode_config("nonexistent")
    general_config = get_mode_config("general")
    assert unknown_config == general_config


def test_get_model_config_gemini_review():
    """Test get_model_config for gemini in review mode."""
    config = get_model_config("review", "gemini")
    assert config == {"model": "flash", "thinking": "high"}


def test_get_model_config_openai_design():
    """Test get_model_config for openai in design mode."""
    config = get_model_config("design", "openai")
    assert config == {"model": "o3", "reasoning": "high"}


def test_get_focus_returns_string():
    """Test that get_focus returns a string."""
    focus = get_focus("review", "gemini")
    assert isinstance(focus, str)
    assert len(focus) > 0
    assert "Code correctness" in focus


def test_get_focus_unknown_provider_returns_empty():
    """Test that get_focus with unknown provider returns empty string."""
    focus = get_focus("review", "nonexistent_provider")
    assert focus == ""


def test_get_rounds_review():
    """Test get_rounds for review mode."""
    rounds = get_rounds("review")
    assert rounds["base"] == 3
    assert rounds["dynamic_range"] == [2, 4]


def test_get_rounds_design():
    """Test get_rounds for design mode."""
    rounds = get_rounds("design")
    assert rounds["base"] == 4
    assert rounds["dynamic_range"] == [3, 4]


def test_get_complexity_rounds_simple():
    """Test get_complexity_rounds for simple complexity (score=0.3)."""
    rounds = get_complexity_rounds(0.3)
    assert rounds == 2


def test_get_complexity_rounds_medium():
    """Test get_complexity_rounds for medium complexity (score=1.0)."""
    rounds = get_complexity_rounds(1.0)
    assert rounds == 3


def test_get_complexity_rounds_complex():
    """Test get_complexity_rounds for complex complexity (score=3.0)."""
    rounds = get_complexity_rounds(3.0)
    assert rounds == 4


def test_get_complexity_rounds_boundary_simple():
    """Test boundary between simple and medium (score=0.5)."""
    rounds = get_complexity_rounds(0.5)
    assert rounds == 3  # At boundary, should be medium


def test_get_complexity_rounds_boundary_medium():
    """Test boundary between medium and complex (score=2.0)."""
    rounds = get_complexity_rounds(2.0)
    assert rounds == 4  # At boundary, should be complex


def test_list_modes_returns_all_five():
    """Test that list_modes returns all five mode names."""
    modes = list_modes()
    assert isinstance(modes, list)
    assert len(modes) == 5
    expected_modes = {"review", "design", "debug", "idea", "general"}
    assert set(modes) == expected_modes


def test_all_modes_have_required_fields():
    """Test that all modes have required fields."""
    modes = list_modes()
    required_fields = ["description", "models", "rounds", "focus"]

    for mode in modes:
        config = get_mode_config(mode)
        for field in required_fields:
            assert field in config, f"Mode {mode} missing field {field}"


def test_all_modes_have_gemini_and_openai_models():
    """Test that all modes define gemini and openai models."""
    modes = list_modes()

    for mode in modes:
        config = get_mode_config(mode)
        models = config.get("models", {})
        assert "gemini" in models, f"Mode {mode} missing gemini model"
        assert "openai" in models, f"Mode {mode} missing openai model"


def test_all_modes_have_focus_for_three_providers():
    """Test that all modes define focus for gemini, openai, and claude."""
    modes = list_modes()

    for mode in modes:
        config = get_mode_config(mode)
        focus = config.get("focus", {})
        assert "gemini" in focus, f"Mode {mode} missing gemini focus"
        assert "openai" in focus, f"Mode {mode} missing openai focus"
        assert "claude" in focus, f"Mode {mode} missing claude focus"


def test_rounds_have_base_and_dynamic_range():
    """Test that all modes have base and dynamic_range in rounds."""
    modes = list_modes()

    for mode in modes:
        rounds = get_rounds(mode)
        assert "base" in rounds, f"Mode {mode} missing base rounds"
        assert "dynamic_range" in rounds, f"Mode {mode} missing dynamic_range"
        assert isinstance(rounds["base"], int)
        assert isinstance(rounds["dynamic_range"], list)
        assert len(rounds["dynamic_range"]) == 2
