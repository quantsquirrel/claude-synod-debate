#!/usr/bin/env python3
"""
Gemini 3 CLI with robust timeout handling, streaming, and adaptive retry.

Usage:
  echo "prompt" | gemini-3 [options]
  gemini-3 "prompt" [options]
  gemini-3 --model flash --thinking high "prompt"

Models: flash (default), pro, 3.1-flash-lite, 3.1-pro, 2.5-flash, 2.5-pro,
        flash-latest, pro-latest, flash-lite-latest
Thinking: minimal, low, medium (default), high, max

On Gemini 3.x models the thinking level is sent as the native `thinking_level`
enum, where `high` is the deepest setting available and `max` collapses to it.
Only the legacy 2.5 family uses the `thinking_budget` token knob.
"""

import argparse
import os
import sys

# Suppress warnings
import warnings

warnings.filterwarnings("ignore")

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai not installed. Run: pip install google-genai", file=sys.stderr)
    sys.exit(1)

# Import base provider
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base_provider import BaseProvider  # noqa: E402


class GeminiProvider(BaseProvider):
    """Gemini 3 CLI provider with thinking budget and adaptive retry."""

    PROVIDER = "gemini"
    API_KEY_ENV = "GEMINI_API_KEY"
    MODEL_MAP = {
        "flash": "gemini-3-flash-preview",
        "pro": "gemini-3.1-pro-preview",
        "3.1-flash-lite": "gemini-3.1-flash-lite-preview",
        "3.1-pro": "gemini-3.1-pro-preview",
        "2.5-flash": "gemini-2.5-flash",
        "2.5-pro": "gemini-2.5-pro",
        # Stable aliases avoid preview-EOL migrations (e.g. 3.0 EOL incident on 2026-03-09)
        "flash-latest": "gemini-flash-latest",
        "pro-latest": "gemini-pro-latest",
        "flash-lite-latest": "gemini-flash-lite-latest",
    }
    DEFAULT_MODEL = "flash"

    # Thinking budget mapping (tokens). Legacy knob — only the 2.5 family still
    # needs it; see THINKING_LEVEL_MAP for why 3.x uses a different control.
    THINKING_MAP = {
        "minimal": 50,
        "low": 200,
        "medium": 500,
        "high": 2000,
        "max": 10000,
    }

    # Gemini 3.x native thinking control. thinking_budget SATURATES on 3.x, so it
    # cannot reach maximum reasoning depth. Measured on gemini-3.1-pro-preview
    # (2026-07-25, hard reasoning prompt), thought tokens / wall clock:
    #   budget=200   →  1,153 / 22.3s     budget=2000  →  5,766 / 55.5s
    #   budget=10000 →  5,137 / 52.6s  (no gain over 2000 — saturated)
    #   level=LOW    →  2,140 / 30.0s     level=HIGH   →  8,473 / 74.8s
    # HIGH is the deepest level the API accepts; thinking_level="max" is rejected
    # (400 INVALID_ARGUMENT), and level+budget are mutually exclusive (400).
    THINKING_LEVEL_MAP = {
        "minimal": "MINIMAL",
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
        "max": "HIGH",  # no native level above HIGH
    }

    # Vendor model ids that accept only the legacy thinking_budget knob. Everything
    # else — 3.x pins and the stable -latest aliases, which resolve to 3.x — takes
    # thinking_level, with a runtime fallback if a model rejects it.
    BUDGET_ONLY_MODEL_PREFIXES = ("gemini-2.5",)

    # Retry levels (progressive downgrade). "max" is included so a downgrade from
    # the top level steps to "high" instead of skipping two levels to "low".
    RETRY_LEVELS = ["max", "high", "medium", "low", "minimal"]

    def validate_api_key(self) -> str:
        """Validate API key with GOOGLE_API_KEY fallback."""
        # GOOGLE_API_KEY fallback: GEMINI_API_KEY 미설정 시 복사
        if not os.environ.get("GEMINI_API_KEY") and os.environ.get("GOOGLE_API_KEY"):
            os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]
        return super().validate_api_key()

    def create_client(self, timeout_ms: int):
        """Create Gemini client with timeout."""
        api_key = self.validate_api_key()
        return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))

    @classmethod
    def uses_thinking_level(cls, model: str) -> bool:
        """True when the vendor model takes the Gemini 3.x ``thinking_level`` enum.

        Only the legacy 2.5 family still requires ``thinking_budget``. 3.x pins and
        the stable ``-latest`` aliases (``gemini-pro-latest`` currently resolves to
        ``gemini-3.1-pro-preview``) take ``thinking_level``.
        """
        return not model.startswith(cls.BUDGET_ONLY_MODEL_PREFIXES)

    @staticmethod
    def is_thinking_arg_error(error: Exception) -> bool:
        """True when an error is the API rejecting the thinking_level argument."""
        text = str(error)
        return "thinking_level" in text and ("INVALID_ARGUMENT" in text or "400" in text)

    def build_thinking_config(self, thinking_level: str, use_level: bool):
        """Build a ThinkingConfig using either the level enum or the token budget."""
        if use_level:
            return types.ThinkingConfig(
                thinking_level=self.THINKING_LEVEL_MAP.get(thinking_level, "HIGH")
            )
        return types.ThinkingConfig(thinking_budget=self.THINKING_MAP.get(thinking_level, 500))

    def _generate_once(
        self,
        client,
        model: str,
        prompt: str,
        thinking_level: str,
        use_level: bool,
        use_streaming: bool,
        temperature: float,
    ) -> str:
        config = types.GenerateContentConfig(
            thinking_config=self.build_thinking_config(thinking_level, use_level),
            temperature=temperature,
        )

        if use_streaming:
            # Streaming mode - prevents timeout for long responses
            stream = client.models.generate_content_stream(
                model=model, contents=prompt, config=config
            )
            full_response = ""
            for chunk in stream:
                if chunk.text:
                    full_response += chunk.text
            return full_response
        else:
            # Non-streaming mode
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            return response.text

    def generate(self, client, model: str, prompt: str, **kwargs) -> str:
        """Generate response using Gemini API with streaming support."""
        args = kwargs.get("args")
        thinking_level = args.thinking if hasattr(args, "thinking") else "medium"
        use_streaming = not args.no_stream if hasattr(args, "no_stream") else True
        temperature = args.temperature if hasattr(args, "temperature") else 0.7

        use_level = self.uses_thinking_level(model)
        try:
            return self._generate_once(
                client, model, prompt, thinking_level, use_level, use_streaming, temperature
            )
        except Exception as exc:
            # A model that rejects thinking_level must still answer: fall back to the
            # legacy budget knob once rather than dropping the whole Gemini lane.
            if use_level and self.is_thinking_arg_error(exc):
                print(
                    f"[Fallback] {model} rejected thinking_level - retrying with thinking_budget",
                    file=sys.stderr,
                )
                return self._generate_once(
                    client, model, prompt, thinking_level, False, use_streaming, temperature
                )
            raise

    def add_provider_args(self, parser: argparse.ArgumentParser):
        """Add Gemini-specific arguments."""
        parser.add_argument(
            "-m",
            "--model",
            choices=[
                "flash",
                "pro",
                "3.1-flash-lite",
                "3.1-pro",
                "2.5-flash",
                "2.5-pro",
                "flash-latest",
                "pro-latest",
                "flash-lite-latest",
            ],
            default="flash",
            help="Model to use (default: flash)",
        )
        parser.add_argument(
            "-t",
            "--thinking",
            choices=["minimal", "low", "medium", "high", "max"],
            default="medium",
            help="Thinking level (default: medium)",
        )
        parser.add_argument(
            "--temperature",
            type=float,
            default=0.7,
            help="Temperature for generation (default: 0.7)",
        )
        parser.add_argument(
            "--no-stream",
            action="store_true",
            help="Disable streaming (not recommended for long prompts)",
        )
        parser.add_argument(
            "--no-adaptive",
            action="store_true",
            help="Disable adaptive retry (thinking level downgrade)",
        )

    def generate_with_retry(
        self, client, model: str, prompt: str, max_retries: int = 3, **kwargs
    ) -> str:
        """Generate with adaptive retry - downgrades thinking level on timeout."""
        args = kwargs.get("args")
        thinking_level = args.thinking if hasattr(args, "thinking") else "medium"
        adaptive = not args.no_adaptive if hasattr(args, "no_adaptive") else True

        current_level = thinking_level
        current_level_idx = (
            self.RETRY_LEVELS.index(current_level) if current_level in self.RETRY_LEVELS else 1
        )

        for attempt in range(max_retries):
            try:
                # Update thinking level in kwargs for generate()
                if hasattr(args, "thinking"):
                    args.thinking = current_level
                return self.generate(client, model, prompt, **kwargs)

            except Exception as e:
                error_str = str(e)
                is_retryable, error_category = self.is_retryable_error(error_str)

                if is_retryable and attempt < max_retries - 1:
                    # Try to downgrade thinking level on timeout/overload
                    if (
                        error_category == "timeout_or_overload"
                        and adaptive
                        and current_level_idx < len(self.RETRY_LEVELS) - 1
                    ):
                        current_level_idx += 1
                        current_level = self.RETRY_LEVELS[current_level_idx]
                        print(
                            f"[Retry {attempt + 1}/{max_retries}] Timeout - downgrading thinking to '{current_level}'",
                            file=sys.stderr,
                        )

                    self.wait_with_backoff(attempt, error_category, max_retries)
                    continue
                else:
                    print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
                    sys.exit(1)

        print(f"Error: Max retries ({max_retries}) exceeded", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    GeminiProvider().run()
