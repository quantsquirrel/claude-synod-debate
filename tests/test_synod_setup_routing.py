"""Tests for synod-setup.py routing defaults."""

import importlib.util
import os
from pathlib import Path


def load_setup_module():
    module_path = Path(__file__).parent.parent / "tools" / "synod-setup.py"
    spec = importlib.util.spec_from_file_location("synod_setup", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestSynodSetupRouting:
    def test_direct_clis_are_primary_and_bridges_are_fallbacks(self):
        setup = load_setup_module()

        assert setup.PRIMARY_CLI_TOOLS["gemini-3"] == "gemini-3.py"
        assert setup.PRIMARY_CLI_TOOLS["openai-cli"] == "openai-cli.py"
        # The agy/cliproxy bridges expired ~2026-06-30 — installed, not primary.
        assert setup.LEGACY_FALLBACK_CLI_TOOLS["agy-cli"] == "agy-cli"
        assert setup.LEGACY_FALLBACK_CLI_TOOLS["cliproxy-cli"] == "cliproxy-cli.py"
        assert setup.CLI_TOOLS["gemini-3"] == "gemini-3.py"
        assert setup.CLI_TOOLS["openai-cli"] == "openai-cli.py"
        assert setup.CLI_TOOLS["agy-cli"] == "agy-cli"

    def test_setup_requires_direct_gemini_dependency(self):
        setup = load_setup_module()

        # gemini-3.py imports google.genai, so it is required again, not optional.
        assert setup.REQUIRED_PACKAGES["google-genai"] == "google.genai"
        assert setup.REQUIRED_PACKAGES["openai"] == "openai"
        assert setup.REQUIRED_PACKAGES["httpx"] == "httpx"
        assert "google-genai" not in setup.OPTIONAL_FALLBACK_PACKAGES

    def test_default_model_tests_use_direct_api_keys(self):
        setup = load_setup_module()

        assert setup.MODELS_TO_TEST["gemini"]["cli"] == "gemini-3.py"
        assert setup.MODELS_TO_TEST["gemini"]["models"] == ["pro-latest"]
        assert setup.MODELS_TO_TEST["gemini"]["env_key"] == "GEMINI_API_KEY"
        assert setup.MODELS_TO_TEST["gemini"]["env_key_compat"] == "GOOGLE_API_KEY"

        assert setup.MODELS_TO_TEST["openai"]["cli"] == "openai-cli.py"
        assert setup.MODELS_TO_TEST["openai"]["models"] == ["gpt56sol"]
        assert setup.MODELS_TO_TEST["openai"]["env_key"] == "OPENAI_API_KEY"
        # No optional_env_key: a direct lane without a key must fail loudly.
        assert "optional_env_key" not in setup.MODELS_TO_TEST["openai"]

    def test_setup_targets_exercise_direct_pro_latest_and_gpt56sol(self):
        setup = load_setup_module()

        assert ("gemini", "pro-latest") in setup.TEST_TARGETS
        assert ("openai", "gpt56sol") in setup.TEST_TARGETS
        assert ("gemini", "3.1-pro") not in setup.TEST_TARGETS
        assert ("openai", "gpt55fast") not in setup.TEST_TARGETS
        assert ("openai", "o3") not in setup.TEST_TARGETS

    def test_model_matrix_is_authored_against_direct_backend(self):
        import json

        matrix_path = Path(__file__).parent.parent / "config" / "model_matrix.json"
        matrix = json.loads(matrix_path.read_text())

        assert matrix["tiers"]["standard"][0]["cli"] == "gemini-3"
        assert matrix["tiers"]["standard"][0]["model"] == "pro-latest"
        assert matrix["tiers"]["standard"][1]["cli"] == "openai-cli"
        assert matrix["legacy_fallbacks"]["standard"][0] == {
            "provider": "gemini",
            "cli": "gemini-3",
            "model": "pro",
            "thinking": "medium",
            "timeout_sec": 120,
        }
        assert matrix["legacy_fallbacks"]["standard"][1] == {
            "provider": "openai",
            "cli": "openai-cli",
            "model": "o3",
            "reasoning": "medium",
            "timeout_sec": 120,
        }

    def test_key_check_requires_direct_api_keys(self, monkeypatch):
        """Direct lanes have no local session to fall back on — a missing key fails.

        resolve_api_key is stubbed so the result does not depend on the developer's
        own env / ~/.synod/.env / Keychain.
        """
        setup = load_setup_module()
        monkeypatch.setattr(setup, "resolve_api_key", lambda key: None)

        assert setup.check_api_key("gemini") == (False, "GEMINI_API_KEY")
        assert setup.check_api_key("openai") == (False, "OPENAI_API_KEY")

    def test_key_check_accepts_google_api_key_compat_for_gemini(self, monkeypatch):
        """GOOGLE_API_KEY satisfies the Gemini lane and is copied to GEMINI_API_KEY."""
        setup = load_setup_module()
        monkeypatch.setattr(
            setup, "resolve_api_key", lambda key: "test-key" if key == "GOOGLE_API_KEY" else None
        )
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        ok, detail = setup.check_api_key("gemini")
        assert ok is True
        assert "GOOGLE_API_KEY" in detail
        assert os.environ["GEMINI_API_KEY"] == "test-key"

    def test_key_check_prefers_primary_env_key(self, monkeypatch):
        setup = load_setup_module()
        monkeypatch.setattr(
            setup, "resolve_api_key", lambda key: "test-key" if key == "GEMINI_API_KEY" else None
        )

        assert setup.check_api_key("gemini") == (True, "GEMINI_API_KEY")
