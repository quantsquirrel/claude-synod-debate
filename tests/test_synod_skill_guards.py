"""Tests for Synod skill MCP guard directives.

Validates that:
1. allowed-tools frontmatter excludes MCP tools
2. MCP prohibition directives exist in skill markdown files
"""

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "skills"
SYNOD_MD = SKILLS_DIR / "synod.md"
PHASE0_MD = SKILLS_DIR / "modules" / "synod-phase0-setup.md"
PHASE1_MD = SKILLS_DIR / "modules" / "synod-phase1-solver.md"


def _parse_frontmatter(filepath: Path) -> dict:
    """Parse YAML frontmatter from a markdown file."""
    text = filepath.read_text()
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


class TestAllowedToolsFrontmatter:
    """Verify allowed-tools excludes MCP tools."""

    def test_synod_frontmatter_excludes_mcp_tools(self):
        fm = _parse_frontmatter(SYNOD_MD)
        allowed = fm.get("allowed-tools", "")

        # Must not contain MCP tool names
        assert "ask_codex" not in allowed
        assert "ask_gemini" not in allowed
        assert "mcp__" not in allowed

        # Must contain exactly the expected tools
        expected = {"Read", "Write", "Bash", "Glob", "Grep", "Task"}
        tools = {t.strip().strip("[]") for t in allowed.split(",")}
        assert tools == expected


class TestMCPProhibitionDirectives:
    """Verify MCP prohibition directives exist in skill files."""

    def test_synod_md_has_mcp_prohibition_directive(self):
        text = SYNOD_MD.read_text()
        assert "MCP TOOL PROHIBITION" in text
        assert "ask_codex" in text
        assert "ask_gemini" in text
        assert "mcp__*" in text

    def test_phase1_has_mcp_prohibition_in_guard(self):
        text = PHASE1_MD.read_text()
        assert "MANDATORY EXTERNAL EXECUTION" in text
        assert "ask_codex" in text
        assert "ask_gemini" in text

    def test_phase0_has_cli_only_note(self):
        text = PHASE0_MD.read_text()
        assert "CLI tools" in text or "CLI" in text
        assert "ask_codex" in text
        assert "ask_gemini" in text
