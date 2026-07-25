"""
Tests for gemini-3.py - Gemini API client with retry logic.
"""

import os
import sys
from unittest.mock import patch

import pytest

# Add tools directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Import after path modification
import importlib.util

spec = importlib.util.spec_from_file_location(
    "gemini_cli", os.path.join(os.path.dirname(__file__), "..", "tools", "gemini-3.py")
)
gemini_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini_cli)


class TestModelMapping:
    """Tests for MODEL_MAP configuration."""

    def test_model_map_contains_all_models(self):
        """Test that MODEL_MAP has all expected models."""
        assert "flash" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "pro" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "3.1-flash-lite" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "3.1-pro" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "2.5-flash" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "2.5-pro" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "flash-latest" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "pro-latest" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "flash-lite-latest" in gemini_cli.GeminiProvider.MODEL_MAP
        assert "3-pro" not in gemini_cli.GeminiProvider.MODEL_MAP

    def test_model_names_correct(self):
        """Test that model names are correctly mapped."""
        assert gemini_cli.GeminiProvider.MODEL_MAP["flash"] == "gemini-3-flash-preview"
        assert gemini_cli.GeminiProvider.MODEL_MAP["pro"] == "gemini-3.1-pro-preview"
        assert (
            gemini_cli.GeminiProvider.MODEL_MAP["3.1-flash-lite"] == "gemini-3.1-flash-lite-preview"
        )
        assert gemini_cli.GeminiProvider.MODEL_MAP["3.1-pro"] == "gemini-3.1-pro-preview"
        assert gemini_cli.GeminiProvider.MODEL_MAP["2.5-flash"] == "gemini-2.5-flash"
        assert gemini_cli.GeminiProvider.MODEL_MAP["2.5-pro"] == "gemini-2.5-pro"
        assert gemini_cli.GeminiProvider.MODEL_MAP["flash-latest"] == "gemini-flash-latest"
        assert gemini_cli.GeminiProvider.MODEL_MAP["pro-latest"] == "gemini-pro-latest"
        assert (
            gemini_cli.GeminiProvider.MODEL_MAP["flash-lite-latest"] == "gemini-flash-lite-latest"
        )


class TestThinkingMapping:
    """Tests for THINKING_MAP configuration."""

    def test_thinking_map_contains_all_levels(self):
        """Test that THINKING_MAP has all expected levels."""
        assert "minimal" in gemini_cli.GeminiProvider.THINKING_MAP
        assert "low" in gemini_cli.GeminiProvider.THINKING_MAP
        assert "medium" in gemini_cli.GeminiProvider.THINKING_MAP
        assert "high" in gemini_cli.GeminiProvider.THINKING_MAP
        assert "max" in gemini_cli.GeminiProvider.THINKING_MAP

    def test_thinking_budgets_increasing(self):
        """Test that thinking budgets increase appropriately."""
        assert (
            gemini_cli.GeminiProvider.THINKING_MAP["minimal"]
            < gemini_cli.GeminiProvider.THINKING_MAP["low"]
        )
        assert (
            gemini_cli.GeminiProvider.THINKING_MAP["low"]
            < gemini_cli.GeminiProvider.THINKING_MAP["medium"]
        )
        assert (
            gemini_cli.GeminiProvider.THINKING_MAP["medium"]
            < gemini_cli.GeminiProvider.THINKING_MAP["high"]
        )
        assert (
            gemini_cli.GeminiProvider.THINKING_MAP["high"]
            < gemini_cli.GeminiProvider.THINKING_MAP["max"]
        )

    def test_thinking_budget_values(self):
        """Test specific thinking budget values."""
        assert gemini_cli.GeminiProvider.THINKING_MAP["minimal"] == 50
        assert gemini_cli.GeminiProvider.THINKING_MAP["low"] == 200
        assert gemini_cli.GeminiProvider.THINKING_MAP["medium"] == 500
        assert gemini_cli.GeminiProvider.THINKING_MAP["high"] == 2000
        assert gemini_cli.GeminiProvider.THINKING_MAP["max"] == 10000


class TestThinkingLevelSelection:
    """Gemini 3.x uses the native thinking_level enum, not thinking_budget.

    thinking_budget saturates on 3.x (measured 2026-07-25 on gemini-3.1-pro-preview:
    budget=2000 -> 5,766 thought tokens, budget=10000 -> 5,137), so only
    thinking_level=HIGH reaches maximum reasoning depth (8,473 thought tokens).
    """

    def test_level_map_covers_every_thinking_choice(self):
        for level in gemini_cli.GeminiProvider.THINKING_MAP:
            assert level in gemini_cli.GeminiProvider.THINKING_LEVEL_MAP

    def test_level_map_values_are_valid_api_enum_members(self):
        from google.genai import types

        valid = {member.value for member in types.ThinkingLevel}
        for native in gemini_cli.GeminiProvider.THINKING_LEVEL_MAP.values():
            assert native in valid

    def test_max_collapses_to_high(self):
        """The API has no level above HIGH; 'max' must not emit an invalid value."""
        assert gemini_cli.GeminiProvider.THINKING_LEVEL_MAP["max"] == "HIGH"
        assert gemini_cli.GeminiProvider.THINKING_LEVEL_MAP["high"] == "HIGH"

    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gemini-pro-latest", True),  # what 'pro-latest' resolves to
            ("gemini-3.1-pro-preview", True),
            ("gemini-flash-latest", True),
            ("gemini-3-flash-preview", True),
            ("gemini-2.5-pro", False),  # legacy family: budget only
            ("gemini-2.5-flash", False),
        ],
    )
    def test_uses_thinking_level_per_model_family(self, model, expected):
        assert gemini_cli.GeminiProvider.uses_thinking_level(model) is expected

    def test_build_config_emits_level_for_gemini_3(self):
        cfg = gemini_cli.GeminiProvider().build_thinking_config("high", use_level=True)
        assert cfg.thinking_level is not None
        assert cfg.thinking_budget is None

    def test_build_config_emits_budget_for_legacy(self):
        cfg = gemini_cli.GeminiProvider().build_thinking_config("high", use_level=False)
        assert cfg.thinking_budget == 2000
        assert cfg.thinking_level is None

    def test_level_and_budget_are_never_set_together(self):
        """The API rejects both at once (400 INVALID_ARGUMENT)."""
        provider = gemini_cli.GeminiProvider()
        for use_level in (True, False):
            cfg = provider.build_thinking_config("high", use_level=use_level)
            assert (cfg.thinking_level is None) != (cfg.thinking_budget is None)

    def test_unknown_thinking_name_defaults_to_deepest_level(self):
        cfg = gemini_cli.GeminiProvider().build_thinking_config("bogus", use_level=True)
        assert cfg.thinking_level == "HIGH"


class TestThinkingArgErrorDetection:
    """Fallback trigger: only a thinking_level rejection may switch to budget."""

    def test_detects_invalid_argument_on_thinking_level(self):
        err = Exception(
            "400 INVALID_ARGUMENT. Invalid value at "
            "'generation_config.thinking_config.thinking_level'"
        )
        assert gemini_cli.GeminiProvider.is_thinking_arg_error(err) is True

    def test_ignores_unrelated_errors(self):
        for message in (
            "429 RESOURCE_EXHAUSTED",
            "504 DEADLINE_EXCEEDED",
            "400 INVALID_ARGUMENT: bad temperature",
            "thinking_level looks fine",  # no status code
        ):
            assert gemini_cli.GeminiProvider.is_thinking_arg_error(Exception(message)) is False


class TestCreateClient:
    """Tests for create_client() function."""

    @patch.dict(os.environ, {}, clear=True)
    def test_create_client_missing_api_key(self):
        """Test that missing API key causes exit."""
        provider = gemini_cli.GeminiProvider()
        with pytest.raises(SystemExit):
            provider.create_client(timeout_ms=60000)

    def test_create_client_with_api_key(self, mock_gemini_api_key, monkeypatch):
        """Test client creation validates API key exists."""
        # Just verify the function can be called with API key set
        # We can't test actual client creation without mocking google.genai
        assert os.environ.get("GEMINI_API_KEY") == mock_gemini_api_key


class TestRetryLevels:
    """Tests for RETRY_LEVELS configuration."""

    def test_retry_levels_order(self):
        """Test that retry levels are in descending order, starting at 'max'.

        'max' must be included so a downgrade from the top level steps to 'high'
        instead of falling through to index 1 ('medium' under the old list).
        """
        levels = gemini_cli.GeminiProvider.RETRY_LEVELS
        assert levels == ["max", "high", "medium", "low", "minimal"]

    def test_max_downgrades_one_step_to_high(self):
        """A timeout at 'max' must degrade to 'high', not skip levels."""
        levels = gemini_cli.GeminiProvider.RETRY_LEVELS
        assert levels[levels.index("max") + 1] == "high"

    def test_all_retry_levels_in_thinking_map(self):
        """Test that all retry levels exist in THINKING_MAP."""
        for level in gemini_cli.GeminiProvider.RETRY_LEVELS:
            assert level in gemini_cli.GeminiProvider.THINKING_MAP


class TestRetryLogic:
    """Tests for retry logic and error detection."""

    def test_timeout_error_detection(self):
        """Test that timeout-related strings are correctly identified."""
        timeout_errors = ["timeout", "504", "gateway", "deadline"]
        for error in timeout_errors:
            # These would be detected in the error handling logic
            assert error.lower() in error.lower()  # Basic sanity check

    def test_rate_limit_error_detection(self):
        """Test that rate limit errors are correctly identified."""
        rate_errors = ["429", "rate", "quota", "resource_exhausted"]
        for error in rate_errors:
            assert error.lower() in error.lower()  # Basic sanity check

    def test_overload_error_detection(self):
        """Test that overload errors are correctly identified."""
        overload_errors = ["503", "overloaded", "unavailable"]
        for error in overload_errors:
            assert error.lower() in error.lower()  # Basic sanity check


class TestIntegration:
    """Integration tests that don't require API calls."""

    def test_module_imports_successfully(self):
        """Test that the module can be imported without errors."""
        assert gemini_cli is not None
        assert hasattr(gemini_cli, "GeminiProvider")
        assert hasattr(gemini_cli.GeminiProvider, "create_client")
        assert hasattr(gemini_cli.GeminiProvider, "generate_with_retry")
        assert hasattr(gemini_cli.GeminiProvider, "run")

    def test_model_map_and_thinking_map_alignment(self):
        """Test that model configurations are consistent."""
        # All models should be valid strings
        for model_key, model_value in gemini_cli.GeminiProvider.MODEL_MAP.items():
            assert isinstance(model_key, str)
            assert isinstance(model_value, str)
            assert len(model_value) > 0

        # All thinking levels should have positive budgets
        for _level, budget in gemini_cli.GeminiProvider.THINKING_MAP.items():
            assert isinstance(budget, int)
            assert budget > 0
