#!/usr/bin/env python3
"""Execution Arbiter — bounded test execution to settle factual disputes (v3.9).

Background
----------
For coding/debug questions, execution is the arbiter the 2026 literature
trusts: SWE-bench SOTA selects among candidates by running tests (CWM
arXiv:2510.02387), and grounded debate beats ungrounded debate (Tool-MAD
arXiv:2601.04742). Models should debate only what execution cannot settle.

This tool runs the target's OWN test suite, bounded, and emits a machine-
verified result for injection into the Phase 2 critic context under the
Phase 0.5 "Primary Evidence (machine-verified)" convention.

Guard rails
-----------
- Only runs when ground_truth_probe's test_collect.json shows collected > 0
  (the probe already did discovery; we never guess at test layout).
- `pytest -x -q` with a HARD timeout (default 120s) — first failure stops the
  run; a hung suite cannot stall the debate.
- Output is bounded (tail of the report) so prompts stay small.
- Fail-safe: every outcome — including "could not run" — is reported as JSON
  with exit code 0. The arbiter informs; it never blocks the pipeline.

Flag/mode gating (SYNOD_EXEC_ARBITER=1, debug/review modes only) lives in the
calling module (synod-phase2-critic.md), not here.

Usage
-----
  exec_arbiter.py --target <repo> --probe-dir <phase0.5/probe> [--timeout 120]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_TAIL_LINES = 30


def run_arbiter(target: str, probe_dir: str, timeout: int) -> dict:
    collect_path = os.path.join(probe_dir, "test_collect.json")
    if not os.path.isfile(collect_path):
        return {
            "status": "skipped",
            "reason": f"no test_collect.json in {probe_dir} — run ground_truth_probe first",
        }
    try:
        with open(collect_path) as f:
            collect = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "skipped", "reason": f"unreadable test_collect.json: {exc}"}

    collected = collect.get("collected", 0)
    if not isinstance(collected, int) or collected <= 0:
        return {
            "status": "skipped",
            "reason": f"probe collected {collected} tests — nothing to execute",
        }

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=target,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "collected": collected,
            "timeout_sec": timeout,
            "summary": f"suite exceeded {timeout}s hard timeout — treat as UNSETTLED, not failing",
        }
    except FileNotFoundError:
        return {"status": "skipped", "reason": "pytest not found in environment"}

    output = (result.stdout + result.stderr).strip()
    tail = "\n".join(output.splitlines()[-_TAIL_LINES:])
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "collected": collected,
        "report_tail": tail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the target's test suite (bounded) as a debate arbiter."
    )
    parser.add_argument("--target", required=True, help="Codebase root to run tests in.")
    parser.add_argument(
        "--probe-dir", required=True, help="Phase 0.5 probe dir holding test_collect.json."
    )
    parser.add_argument("--timeout", type=int, default=120, help="Hard timeout in seconds.")
    args = parser.parse_args()

    try:
        if not os.path.isdir(args.target):
            result = {"status": "skipped", "reason": f"target not a directory: {args.target}"}
        else:
            result = run_arbiter(args.target, args.probe_dir, args.timeout)
    except Exception as exc:  # fail-safe: report, never block
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
