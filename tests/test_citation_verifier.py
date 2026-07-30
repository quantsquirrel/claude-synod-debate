"""Tests for tools/citation_verifier.py — mechanical file:line verification (v3.9).

Covers:
- verified: exact relative path, line in range
- bad_line: file exists, line exceeds length (fabrication signal)
- not_found: no such file (fabrication signal)
- unique basename resolution; ambiguous basenames not scored
- absolute path outside target -> outside, not scored
- dedup of repeated citations; range citations
- CLI --file and --dir modes; fail-safe exit 0 on bad target
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

_tool_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "citation_verifier.py"
)
_spec = importlib.util.spec_from_file_location("citation_verifier", _tool_path)
_cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cv)


@pytest.fixture()
def repo(tmp_path):
    """A tiny fake target repo."""
    (tmp_path / "app.py").write_text("line1\nline2\nline3\nline4\nline5\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "util.py").write_text("x = 1\ny = 2\n")
    # Two files sharing a basename -> ambiguous when cited bare
    dup1 = tmp_path / "a"
    dup2 = tmp_path / "b"
    dup1.mkdir()
    dup2.mkdir()
    (dup1 / "dup.py").write_text("only\n")
    (dup2 / "dup.py").write_text("only\n")
    return tmp_path


class TestVerdicts:
    def test_verified_exact_path(self, repo):
        r = _cv.verify_text("- bug at app.py:3 confirmed", str(repo))
        assert r["verified"] == 1
        assert r["fabricated"] == 0
        assert r["citations"][0]["verdict"] == "verified"

    def test_bad_line_is_fabrication(self, repo):
        r = _cv.verify_text("- see app.py:9999", str(repo))
        assert r["fabricated"] == 1
        assert r["citations"][0]["verdict"] == "bad_line"
        assert r["citations"][0]["file_lines"] == 5
        assert "app.py:9999" in r["fabricated_citations"]

    def test_not_found_is_fabrication(self, repo):
        r = _cv.verify_text("- see ghost.py:1", str(repo))
        assert r["fabricated"] == 1
        assert r["citations"][0]["verdict"] == "not_found"

    def test_relative_subdir_path(self, repo):
        r = _cv.verify_text("pkg/util.py:2 defines y", str(repo))
        assert r["citations"][0]["verdict"] == "verified"

    def test_unique_basename_resolves(self, repo):
        # util.py exists exactly once under repo
        r = _cv.verify_text("util.py:1 sets x", str(repo))
        assert r["citations"][0]["verdict"] == "verified"

    def test_ambiguous_basename_not_scored(self, repo):
        r = _cv.verify_text("dup.py:1 is duplicated", str(repo))
        assert r["citations"][0]["verdict"] == "ambiguous"
        assert r["fabricated"] == 0
        assert r["undecidable"] == 1

    def test_relative_traversal_outside_target(self, repo, tmp_path_factory):
        """A ../ citation must not escape TARGET_PATH and earn 'verified'."""
        parent_file = repo.parent / "escape.py"
        parent_file.write_text("secret\n")
        r = _cv.verify_text("- see ../escape.py:1", str(repo))
        assert r["citations"][0]["verdict"] == "outside"
        assert r["fabricated"] == 0
        assert r["verified"] == 0

    def test_absolute_outside_target(self, repo, tmp_path_factory):
        other = tmp_path_factory.mktemp("elsewhere")
        (other / "x.py").write_text("z\n")
        r = _cv.verify_text(f"see {other}/x.py:1", str(repo))
        assert r["citations"][0]["verdict"] == "outside"
        assert r["fabricated"] == 0

    def test_range_citation_within_file(self, repo):
        r = _cv.verify_text("app.py:2-4 covers the loop", str(repo))
        assert r["citations"][0]["verdict"] == "verified"

    def test_range_citation_exceeding_file(self, repo):
        r = _cv.verify_text("app.py:2-400 covers everything", str(repo))
        assert r["citations"][0]["verdict"] == "bad_line"

    def test_duplicate_citations_deduped(self, repo):
        r = _cv.verify_text("app.py:1 and again app.py:1", str(repo))
        assert r["total_citations"] == 1

    def test_verified_rate(self, repo):
        r = _cv.verify_text("app.py:1 ok\nghost.py:5 bad", str(repo))
        assert r["verified_rate"] == 0.5

    def test_no_citations(self, repo):
        r = _cv.verify_text("no citations here at all", str(repo))
        assert r["total_citations"] == 0
        assert r["verified_rate"] is None

    def test_keyword_overlap_reported_not_scored(self, repo):
        r = _cv.verify_text("- line2 mentioned at app.py:2", str(repo))
        c = r["citations"][0]
        assert c["verdict"] == "verified"
        assert "keyword_overlap" in c


class TestCLI:
    def _run(self, *args):
        out = subprocess.run(
            [sys.executable, _tool_path, *args], capture_output=True, text=True
        )
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    def test_file_mode(self, repo, tmp_path):
        md = tmp_path / "resp.md"
        md.write_text("- app.py:1 is fine\n- ghost.py:9 is invented\n")
        r = self._run("--target", str(repo), "--file", str(md))
        assert r["verified"] == 1
        assert r["fabricated"] == 1
        assert r["source"] == "resp.md"

    def test_dir_mode_per_model(self, repo, tmp_path):
        d = tmp_path / "round-1-solver"
        d.mkdir()
        (d / "gemini-response.md").write_text("app.py:1 ok")
        (d / "openai-response.md").write_text("ghost.py:1 bad")
        r = self._run("--target", str(repo), "--dir", str(d))
        by_source = {rep["source"]: rep for rep in r["reports"]}
        assert by_source["gemini-response.md"]["verified"] == 1
        assert by_source["openai-response.md"]["fabricated"] == 1

    def test_bad_target_fail_safe(self, tmp_path):
        md = tmp_path / "resp.md"
        md.write_text("app.py:1")
        r = self._run("--target", str(tmp_path / "nope"), "--file", str(md))
        assert r["status"] == "error"


class TestTrustFromCitations:
    """v3.10 CRIS demotion: trust derived mechanically from verified-citation rate."""

    def test_mapping_endpoints(self):
        assert _cv.trust_from_rate(1.0) == 2.0    # all verified -> trust_cap
        assert _cv.trust_from_rate(0.0) == 0.25   # all fabricated -> below exclude
        assert _cv.trust_from_rate(None) == 1.0   # nothing decidable -> neutral

    def test_all_fabricated_falls_below_exclude_threshold(self, repo):
        r = _cv.verify_text("ghost.py:1 and phantom.py:2", str(repo))
        assert r["trust_score"] == 0.25
        assert r["trust_score"] < 0.5  # trust_exclude in synod-modes.yaml

    def test_all_verified_hits_cap(self, repo):
        r = _cv.verify_text("app.py:1 and app.py:2", str(repo))
        assert r["trust_score"] == 2.0

    def test_dir_mode_emits_trust_map(self, repo, tmp_path):
        import subprocess

        d = tmp_path / "round-1-solver"
        d.mkdir()
        (d / "gemini-response.md").write_text("app.py:1 ok")
        (d / "openai-response.md").write_text("ghost.py:1 invented")
        out = subprocess.run(
            [sys.executable, _tool_path, "--target", str(repo), "--dir", str(d)],
            capture_output=True,
            text=True,
        )
        r = json.loads(out.stdout)
        assert r["trust"] == {"gemini": 2.0, "openai": 0.25}
