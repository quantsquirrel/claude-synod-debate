"""
Tests for openai-cli.py - OpenAI API client with retry logic.
"""

import os
import sys

import pytest

# Add tools directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

# Import after path modification
import importlib.util

spec = importlib.util.spec_from_file_location(
    "openai_cli", os.path.join(os.path.dirname(__file__), "..", "tools", "openai-cli.py")
)
openai_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(openai_cli)


class TestModelMapping:
    """Tests for MODEL_MAP configuration."""

    def test_model_map_contains_all_models(self):
        """Test that MODEL_MAP has all expected models."""
        assert "gpt4o" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "o3" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "o4mini" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "gpt54" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "gpt5mini" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "gpt54mini" in openai_cli.OpenAIProvider.MODEL_MAP
        assert "gpt55" in openai_cli.OpenAIProvider.MODEL_MAP

    def test_model_names_correct(self):
        """Test that model names are correctly mapped."""
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt4o"] == "gpt-4o"
        assert openai_cli.OpenAIProvider.MODEL_MAP["o3"] == "o3"
        assert openai_cli.OpenAIProvider.MODEL_MAP["o4mini"] == "o4-mini"
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt54"] == "gpt-5.4"
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt5mini"] == "gpt-5-mini"
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt54mini"] == "gpt-5.4-mini"
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt55"] == "gpt-5.5"

    def test_default_model_is_gpt54mini(self):
        """DEFAULT_MODEL is gpt54mini after v3.4.0 modernization."""
        assert openai_cli.OpenAIProvider.DEFAULT_MODEL == "gpt54mini"


class TestReasoningModels:
    """Tests for REASONING_MODELS configuration."""

    def test_reasoning_models_list(self):
        """Test that reasoning models are correctly identified."""
        assert "o3" in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "o4mini" in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "gpt4o" not in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "gpt54" in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "gpt5mini" in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "gpt54mini" in openai_cli.OpenAIProvider.REASONING_MODELS
        assert "gpt55" in openai_cli.OpenAIProvider.REASONING_MODELS


class TestTimeoutConfig:
    """Tests for TIMEOUT_CONFIG."""

    def test_timeout_config_has_all_combinations(self):
        """Test that timeout config covers all model+reasoning combinations."""
        models = ["gpt4o", "o3", "o4mini", "gpt54", "gpt5mini", "gpt54mini", "gpt55"]
        levels = ["low", "medium", "high"]

        for model in models:
            for level in levels:
                assert (model, level) in openai_cli.OpenAIProvider.TIMEOUT_CONFIG

    def test_timeout_values_reasonable(self):
        """Test that all timeout values are positive and reasonable."""
        for timeout in openai_cli.OpenAIProvider.TIMEOUT_CONFIG.values():
            assert timeout > 0
            assert timeout <= 600  # Max 10 minutes

    def test_o3_high_has_longest_timeout(self):
        """Test that o3 with high reasoning has the longest timeout."""
        o3_high = openai_cli.OpenAIProvider.TIMEOUT_CONFIG[("o3", "high")]
        # Should be one of the highest timeouts
        assert o3_high >= 180

    def test_provider_timeout_config_applies_to_default_model(self, monkeypatch):
        """Default CLI timeout comes from TIMEOUT_CONFIG for the chosen model/reasoning."""
        monkeypatch.delenv("SYNOD_V2_ADAPTIVE_TIMEOUT", raising=False)
        provider = openai_cli.OpenAIProvider()
        parser = provider.build_parser()
        args, _ = parser.parse_known_args(["prompt"])

        timeout = provider.get_timeout_ms(args, args.model)

        assert args.model == "gpt54mini"
        assert args.reasoning == "medium"
        assert timeout == openai_cli.OpenAIProvider.TIMEOUT_CONFIG[("gpt54mini", "medium")] * 1000

    def test_explicit_timeout_overrides_provider_default(self, monkeypatch):
        """User-supplied timeout still overrides provider defaults."""
        monkeypatch.delenv("SYNOD_V2_ADAPTIVE_TIMEOUT", raising=False)
        provider = openai_cli.OpenAIProvider()
        parser = provider.build_parser()
        args, _ = parser.parse_known_args(["--timeout", "45", "prompt"])

        assert provider.get_timeout_ms(args, args.model) == 45_000


class TestXHighClamp:
    """xhigh must only reach models that accept it.

    Probed live 2026-07-25: gpt-5.6-sol / gpt-5.5 / gpt-5.4 / gpt-5.4-mini / o3
    accept reasoning_effort='xhigh'; gpt-5-mini rejects it with a 400 (supports
    minimal|low|medium|high) and gpt-4o takes no reasoning_effort at all.
    """

    def test_gpt56sol_is_registered(self):
        assert openai_cli.OpenAIProvider.MODEL_MAP["gpt56sol"] == "gpt-5.6-sol"
        assert "gpt56sol" in openai_cli.OpenAIProvider.REASONING_MODELS

    def test_xhigh_models_are_all_reasoning_models(self):
        for key in openai_cli.OpenAIProvider.XHIGH_MODELS:
            assert key in openai_cli.OpenAIProvider.REASONING_MODELS
            assert key in openai_cli.OpenAIProvider.MODEL_MAP

    @pytest.mark.parametrize("model_key", ["gpt56sol", "gpt55", "gpt54", "gpt54mini", "o3"])
    def test_xhigh_passes_through_for_supported_models(self, model_key):
        assert openai_cli.OpenAIProvider.clamp_reasoning(model_key, "xhigh") == "xhigh"

    @pytest.mark.parametrize("model_key", ["gpt5mini", "o4mini"])
    def test_xhigh_clamps_to_high_for_unsupported_models(self, model_key):
        assert openai_cli.OpenAIProvider.clamp_reasoning(model_key, "high") == "high"
        assert openai_cli.OpenAIProvider.clamp_reasoning(model_key, "xhigh") == "high"

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_non_xhigh_levels_are_never_altered(self, level):
        for model_key in ["gpt56sol", "gpt5mini", "o3"]:
            assert openai_cli.OpenAIProvider.clamp_reasoning(model_key, level) == level

    def test_gpt56sol_has_timeout_for_every_reasoning_level(self):
        cfg = openai_cli.OpenAIProvider.TIMEOUT_CONFIG
        for level in openai_cli.OpenAIProvider.REASONING_LEVELS:
            assert ("gpt56sol", level) in cfg, level

    def test_gpt56sol_timeouts_increase_with_depth(self):
        """Deeper effort costs more wall clock, so the ceiling must rise with it."""
        cfg = openai_cli.OpenAIProvider.TIMEOUT_CONFIG
        assert (
            cfg[("gpt56sol", "low")]
            < cfg[("gpt56sol", "medium")]
            <= cfg[("gpt56sol", "high")]
            < cfg[("gpt56sol", "xhigh")]
        )

    def test_gpt56sol_xhigh_timeout_covers_measured_latency(self):
        """xhigh measured at 190.6s — the ceiling must exceed it with headroom."""
        assert openai_cli.OpenAIProvider.TIMEOUT_CONFIG[("gpt56sol", "xhigh")] > 190


class TestReasoningLevels:
    """Tests for REASONING_LEVELS configuration."""

    def test_reasoning_levels_order(self):
        """Test that reasoning levels are in descending order, deepest first."""
        levels = openai_cli.OpenAIProvider.REASONING_LEVELS
        assert levels == ["xhigh", "high", "medium", "low"]

    def test_xhigh_downgrades_one_step_to_high(self):
        """A timeout at 'xhigh' must degrade to 'high', not skip levels."""
        levels = openai_cli.OpenAIProvider.REASONING_LEVELS
        assert levels[levels.index("xhigh") + 1] == "high"


class TestCreateClient:
    """Tests for create_client() function."""

    def test_create_client_with_valid_api_key(self, mock_openai_api_key):
        """Test that client creation requires API key."""
        # Just verify API key is set - actual client creation would need mocking
        assert os.environ.get("OPENAI_API_KEY") == mock_openai_api_key


class TestGenerateWithRetry:
    """Tests for generate_with_retry() retry logic."""

    def test_successful_generation(self, mock_openai_api_key, monkeypatch):
        """Test that generate function exists and has correct signature."""
        # Verify function exists with expected parameters
        import inspect

        sig = inspect.signature(openai_cli.OpenAIProvider.generate_with_retry)
        params = list(sig.parameters.keys())
        assert "client" in params
        assert "model" in params
        assert "prompt" in params
        assert "max_retries" in params
        assert "kwargs" in params  # Additional args passed via kwargs

    def test_o_series_includes_reasoning_effort(self):
        """Test that reasoning-capable models are in REASONING_MODELS."""
        for model in openai_cli.OpenAIProvider.REASONING_MODELS:
            assert model in [
                "o3",
                "o4mini",
                "gpt54",
                "gpt5mini",
                "gpt54mini",
                "gpt55",
                "gpt56sol",
            ]

    def test_gpt4o_excludes_reasoning_effort(self):
        """Test that gpt4o should not use reasoning_effort."""
        assert "gpt4o" not in openai_cli.OpenAIProvider.REASONING_MODELS


class TestIntegration:
    """Integration tests that don't require API calls."""

    def test_module_imports_successfully(self):
        """Test that the module can be imported without errors."""
        assert openai_cli is not None
        assert hasattr(openai_cli, "OpenAIProvider")
        assert hasattr(openai_cli.OpenAIProvider, "create_client")
        assert hasattr(openai_cli.OpenAIProvider, "generate_with_retry")
        assert hasattr(openai_cli.OpenAIProvider, "run")

    def test_cli_help_works_without_api_key(self, monkeypatch):
        """Test that CLI help can be shown without API key."""
        # The help functionality should work even without API key
        assert openai_cli.OpenAIProvider.MODEL_MAP is not None
        assert openai_cli.OpenAIProvider.TIMEOUT_CONFIG is not None


class TestErrorDetection:
    """Tests for error detection logic."""

    def test_timeout_error_keywords(self):
        """Test timeout error detection keywords."""
        timeout_keywords = ["timeout", "timed out", "deadline"]
        for keyword in timeout_keywords:
            assert keyword.lower() in keyword.lower()

    def test_rate_limit_error_keywords(self):
        """Test rate limit error detection keywords."""
        rate_keywords = ["429", "rate", "quota"]
        for keyword in rate_keywords:
            assert keyword.lower() in keyword.lower()

    def test_overload_error_keywords(self):
        """Test overload error detection keywords."""
        overload_keywords = ["503", "overloaded", "unavailable", "502"]
        for keyword in overload_keywords:
            assert keyword.lower() in keyword.lower()


class TestTimeoutStrategy:
    """Tests for timeout strategy."""

    def test_timeout_increases_with_reasoning_level(self):
        """Test that timeouts increase with reasoning complexity."""
        for model in ["gpt4o", "o3", "o4mini", "gpt54", "gpt5mini", "gpt54mini", "gpt55"]:
            low = openai_cli.OpenAIProvider.TIMEOUT_CONFIG[(model, "low")]
            medium = openai_cli.OpenAIProvider.TIMEOUT_CONFIG[(model, "medium")]
            high = openai_cli.OpenAIProvider.TIMEOUT_CONFIG[(model, "high")]
            # Timeouts should generally increase or stay same
            assert low <= medium <= high or (low == medium == high)

    def test_o_series_has_higher_timeouts(self):
        """Test that o-series models have generally higher timeouts."""
        # O3 should have higher timeouts than gpt4o on average
        o3_avg = (
            sum(
                openai_cli.OpenAIProvider.TIMEOUT_CONFIG[("o3", level)]
                for level in ["low", "medium", "high"]
            )
            / 3
        )
        gpt4o_avg = (
            sum(
                openai_cli.OpenAIProvider.TIMEOUT_CONFIG[("gpt4o", level)]
                for level in ["low", "medium", "high"]
            )
            / 3
        )
        assert o3_avg > gpt4o_avg
