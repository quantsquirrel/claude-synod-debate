"""Tests for tools/exec_arbiter.py — bounded test execution arbiter (v3.9).

Covers:
- passed / failed suites reported with exit code and bounded tail
- skipped when probe artifacts are absent or collected == 0
- timeout reported as UNSETTLED, not failing
- CLI fail-safe: always exit 0, JSON in-band
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

_tool_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "exec_arbiter.py"
)
_spec = importlib.util.spec_from_file_location("exec_arbiter", _tool_path)
_ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ea)


def _write_probe(tmp_path, collected):
    probe = tmp_path / "probe"
    probe.mkdir(exist_ok=True)
    (probe / "test_collect.json").write_text(json.dumps({"collected": collected}))
    return str(probe)


@pytest.fixture()
def passing_repo(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    return tmp_path


@pytest.fixture()
def failing_repo(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n")
    return tmp_path


class TestRunArbiter:
    def test_passing_suite(self, passing_repo):
        probe = _write_probe(passing_repo, 1)
        r = _ea.run_arbiter(str(passing_repo), probe, timeout=60)
        assert r["status"] == "passed"
        assert r["exit_code"] == 0
        assert r["collected"] == 1
        assert "report_tail" in r

    def test_failing_suite(self, failing_repo):
        probe = _write_probe(failing_repo, 1)
        r = _ea.run_arbiter(str(failing_repo), probe, timeout=60)
        assert r["status"] == "failed"
        assert r["exit_code"] != 0

    def test_skip_without_probe(self, passing_repo):
        r = _ea.run_arbiter(str(passing_repo), str(passing_repo / "no-probe"), timeout=60)
        assert r["status"] == "skipped"
        assert "test_collect.json" in r["reason"]

    def test_skip_zero_collected(self, passing_repo):
        probe = _write_probe(passing_repo, 0)
        r = _ea.run_arbiter(str(passing_repo), probe, timeout=60)
        assert r["status"] == "skipped"

    def test_skip_corrupt_collect_json(self, passing_repo):
        probe = passing_repo / "probe"
        probe.mkdir()
        (probe / "test_collect.json").write_text("NOT JSON {{{")
        r = _ea.run_arbiter(str(passing_repo), str(probe), timeout=60)
        assert r["status"] == "skipped"

    def test_timeout_reported_unsettled(self, tmp_path):
        (tmp_path / "test_slow.py").write_text(
            "import time\n\ndef test_slow():\n    time.sleep(30)\n"
        )
        probe = _write_probe(tmp_path, 1)
        r = _ea.run_arbiter(str(tmp_path), probe, timeout=3)
        assert r["status"] == "timeout"
        assert "UNSETTLED" in r["summary"]

    def test_tail_is_bounded(self, failing_repo):
        probe = _write_probe(failing_repo, 1)
        r = _ea.run_arbiter(str(failing_repo), probe, timeout=60)
        assert len(r["report_tail"].splitlines()) <= 30


class TestCLI:
    def _run(self, *args):
        out = subprocess.run([sys.executable, _tool_path, *args], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    def test_cli_passing(self, passing_repo):
        probe = _write_probe(passing_repo, 1)
        r = self._run("--target", str(passing_repo), "--probe-dir", probe, "--timeout", "60")
        assert r["status"] == "passed"

    def test_cli_bad_target_fail_safe(self, tmp_path):
        r = self._run("--target", str(tmp_path / "nope"), "--probe-dir", str(tmp_path))
        assert r["status"] == "skipped"
