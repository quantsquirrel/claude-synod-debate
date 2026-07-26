"""The four version declarations in this repo must agree.

`.claude-plugin/marketplace.json` is the catalog Claude Code actually reads when
installing or updating the plugin, and the install path is version-scoped
(`~/.claude/plugins/cache/synod/synod/<version>/`). It sat at 3.1.0 from v3.1.0
through v3.7.0 while the other files were bumped, so every "update" in between
resolved to the stale 3.1.0 slot instead of installing the new code.

Nothing caught that, because no test read the file. These do.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _plugin_json() -> str:
    return json.loads((REPO_ROOT / "plugin.json").read_text())["version"]


def _marketplace_json() -> str:
    return json.loads((REPO_ROOT / "marketplace.json").read_text())["version"]


def _claude_plugin_catalog() -> str:
    """The catalog Claude Code reads — the one that actually drives installs."""
    data = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    plugins = data["plugins"]
    assert len(plugins) == 1, f"expected exactly one plugin entry, got {len(plugins)}"
    return plugins[0]["version"]


def _pyproject() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version field found in pyproject.toml"
    return match.group(1)


SOURCES = {
    "plugin.json": _plugin_json,
    "marketplace.json": _marketplace_json,
    ".claude-plugin/marketplace.json": _claude_plugin_catalog,
    "pyproject.toml": _pyproject,
}


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_version_is_valid_semver(name):
    version = SOURCES[name]()
    assert SEMVER.match(version), f"{name} version {version!r} is not X.Y.Z"


def test_all_version_declarations_agree():
    """A release bump must touch every declaration, not just the obvious ones."""
    versions = {name: get() for name, get in SOURCES.items()}
    distinct = set(versions.values())
    assert len(distinct) == 1, "version declarations have drifted: " + ", ".join(
        f"{name}={version}" for name, version in sorted(versions.items())
    )


def test_claude_plugin_catalog_is_covered_by_this_check():
    """Guard the guard: the catalog file is the one that silently drifted before.

    If it is ever renamed or dropped from SOURCES, this fails rather than letting
    the consistency check pass over a file it no longer reads.
    """
    assert ".claude-plugin/marketplace.json" in SOURCES
    assert (REPO_ROOT / ".claude-plugin" / "marketplace.json").is_file()
