"""Tests for tools/model_branding.py — single source of truth for synod model brand colors."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import model_branding  # noqa: E402


class TestBrandingDict:
    def test_three_known_models(self):
        assert set(model_branding.BRANDING.keys()) == {"claude", "gemini", "openai"}

    def test_each_entry_has_required_fields(self):
        required = {"label", "hex", "rich"}
        for key, entry in model_branding.BRANDING.items():
            assert required <= set(entry.keys()), f"{key} missing fields"

    def test_hex_codes_are_well_formed(self):
        for entry in model_branding.BRANDING.values():
            hex_code = entry["hex"]
            assert hex_code.startswith("#")
            assert len(hex_code) == 7  # #RRGGBB
            assert all(c in "0123456789ABCDEFabcdef" for c in hex_code[1:])

    def test_glyph_is_black_medium_small_square(self):
        # The shared marker glyph must be the small-square box that pairs
        # with HTML inline color in markdown surfaces.
        assert model_branding.GLYPH == "◾"


class TestGetBranding:
    def test_claude_returns_warm_coral(self):
        b = model_branding.get("claude")
        assert b["hex"] == "#D97757"
        assert b["label"] == "Claude"
        assert b["rich"] == "orange3"

    def test_gemini_returns_google_blue(self):
        b = model_branding.get("gemini")
        assert b["hex"] == "#4285F4"
        assert b["label"] == "Gemini"
        assert b["rich"] == "blue"

    def test_openai_returns_signature_teal(self):
        b = model_branding.get("openai")
        assert b["hex"] == "#10A37F"
        assert b["label"] == "OpenAI"
        assert b["rich"] == "green"

    def test_lookup_is_case_insensitive(self):
        assert model_branding.get("CLAUDE")["hex"] == "#D97757"
        assert model_branding.get("Gemini")["hex"] == "#4285F4"
        assert model_branding.get("OpenAI")["hex"] == "#10A37F"

    def test_unknown_model_raises_keyerror(self):
        with pytest.raises(KeyError):
            model_branding.get("mistral")


class TestMarkdownMarker:
    def test_claude_marker_is_color_spanned_glyph(self):
        assert (
            model_branding.markdown_marker("claude")
            == '<span style="color:#D97757">◾</span>'
        )

    def test_gemini_marker_is_color_spanned_glyph(self):
        assert (
            model_branding.markdown_marker("gemini")
            == '<span style="color:#4285F4">◾</span>'
        )

    def test_openai_marker_is_color_spanned_glyph(self):
        assert (
            model_branding.markdown_marker("openai")
            == '<span style="color:#10A37F">◾</span>'
        )

    def test_marker_is_case_insensitive(self):
        assert "color:#D97757" in model_branding.markdown_marker("CLAUDE")

    def test_marker_unknown_model_raises_keyerror(self):
        with pytest.raises(KeyError):
            model_branding.markdown_marker("mistral")
