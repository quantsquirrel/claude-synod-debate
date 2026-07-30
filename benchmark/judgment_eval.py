#!/usr/bin/env python3
"""
judgment_eval.py — the DISCRIMINATING arm of the Synod value question.

Why this exists
---------------
`strategy_compare.py` measures S0 (independent + synthesis) against S3 (full
debate) on GSM8K. That arm cannot settle the question: GSM8K answers are single
verifiable numbers and frontier solvers are near-saturated on them, so both
arms score at the ceiling and the difference is noise (see the `power_caveat`
in that harness's dataset provenance).

The multi-agent literature predicts debate helps on tasks with no single
checkable answer — design decisions, code review, incident calls — where a
second pass can catch what a first pass missed (Smit et al. ICML 2024;
arXiv:2508.17536 for the negative result on verifiable tasks). This harness
runs exactly that arm: 50 design/review tasks, each with a 4-criterion rubric
and a recorded `failure_mode` naming what a shallow single pass usually misses.

Scoring is by JUDGE, so the judge is the measurement instrument and its biases
are the main threat to validity. Four countermeasures, all mandatory:

  1. ANONYMISATION — the judge sees "Response 1" / "Response 2". Arm names
     (S0/S3) and provider names never enter the judge prompt. Identity cues
     drive sycophantic agreement (arXiv:2510.07517) and self-preference is
     driven by self-recognition (Panickssery et al., arXiv:2404.13076).
  2. POSITION SWAP — every pair is judged twice, (A,B) and (B,A). A judgment
     counts ONLY if both orders agree; disagreements are recorded as flips.
     Judge order bias flips rankings (arXiv:2305.17926).
  3. RUBRIC DECOMPOSITION — one judgment per criterion, never a holistic
     ruling. Decomposition cuts self-preference ~31.5% (arXiv:2604.23178);
     holistic rulings reward rhetoric over substance.
  4. CROSS-FAMILY JUDGE — the judge must come from a different provider family
     than the model that AUTHORED the candidate text (Synod's synthesiser is
     Claude, so the judge is not Claude). Overlap that cannot be avoided is
     recorded as an explicit warning, not silently ignored.

The harness REFUSES to declare a winner when the position-flip rate exceeds
`MAX_FLIP_RATE`: a judge that contradicts itself under reordering has not
measured anything, and reporting its majority would be false precision.

Usage (offline / CI)
--------------------
    python benchmark/judgment_eval.py --mock

Usage (live — BILLS PROVIDER APIs; double consent required)
-----------------------------------------------------------
    SYNOD_JUDGE_LIVE=1 ANTHROPIC_API_KEY=... GEMINI_API_KEY=... OPENAI_API_KEY=... \\
    python benchmark/judgment_eval.py --live --n 50 \\
      --output benchmark/results/judgment_eval.json

Mock mode is a HARNESS TEST, not evidence: MockCandidateSource gives both arms
the same rubric coverage by default, so the honest mock outcome is a tie.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TASKS_PATH = _HERE / "data" / "judgment_tasks.jsonl"

# Arms under comparison. S0 is the killer baseline; S3 is full debate.
ARM_BASELINE = "S0_independent_synthesis"
ARM_DEBATE = "S3_full_debate"

# Above this share of position-swap disagreements the judge is unreliable and
# the harness declines to name a winner.
MAX_FLIP_RATE = 0.30

# Provider families, for the cross-family constraint.
_FAMILY = {"gemini": "google", "openai": "openai", "claude": "anthropic"}

# The family that AUTHORS candidate text in Synod's topology (synthesiser).
AUTHOR_FAMILY = "anthropic"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class JudgmentTask:
    """One open-ended design/review task with a decomposed rubric."""

    id: int
    domain: str
    prompt: str
    rubric: list[str]
    failure_mode: str


@dataclass
class CriterionVerdict:
    """One rubric criterion judged in BOTH position orders."""

    task_id: int
    domain: str
    criterion_index: int
    criterion: str
    forward: str  # winner as judged with (baseline, debate)
    swapped: str  # winner as judged with (debate, baseline)
    consistent: bool
    winner: str | None  # arm name, "tie", or None when flipped


@dataclass
class JudgmentReport:
    """Aggregate outcome over all tasks and criteria."""

    n_tasks: int
    n_criteria: int
    n_consistent: int
    n_flipped: int
    flip_rate: float
    wins_baseline: int
    wins_debate: int
    ties: int
    debate_win_rate: float | None
    verdict: str
    reliable: bool
    per_domain: dict[str, dict[str, int]] = field(default_factory=dict)
    verdicts: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


def load_judgment_tasks(
    n: int | None = None,
    seed: int = 42,
    domain: str | None = None,
) -> tuple[list[JudgmentTask], dict[str, Any]]:
    """
    Load judgment tasks with provenance.

    `n=None` uses the whole set. Sampling is seeded so a committed result file
    is reproducible. Raises ValueError rather than truncating when the pool
    cannot supply `n` — the same silent-truncation trap the GSM8K loader had.
    """
    with open(_TASKS_PATH) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    pool = [JudgmentTask(**r) for r in rows]
    if domain is not None:
        pool = [t for t in pool if t.domain == domain]
        if not pool:
            raise ValueError(f"no tasks for domain {domain!r}")

    if n is None:
        tasks = pool
    else:
        if n < 1:
            raise ValueError(f"--n must be at least 1, got {n}")
        if len(pool) < n:
            raise ValueError(
                f"requested --n {n} but the pool holds only {len(pool)} tasks"
                f"{f' in domain {domain!r}' if domain else ''}. "
                f"Refusing to silently truncate."
            )
        tasks = random.Random(seed).sample(pool, n)

    provenance = {
        "source": "judgment_tasks_authored",
        "pool_size": len(pool),
        "n_used": len(tasks),
        "seed": seed if n is not None else None,
        "domain_filter": domain,
        "task_ids": [t.id for t in tasks],
        "honest_label": (
            "Tasks are AUTHORED for this repository, not drawn from a published "
            "benchmark. They are open-ended by design, so there is no ground "
            "truth — only rubric criteria scored by a judge. Treat the judge as "
            "the measurement instrument and read flip_rate before the win rate."
        ),
    }
    return tasks, provenance


# ---------------------------------------------------------------------------
# Candidate sources
# ---------------------------------------------------------------------------


class CandidateSource(ABC):
    """Produces one candidate answer per arm for a task."""

    name: str = "abstract"

    @abstractmethod
    def produce(self, task: JudgmentTask) -> dict[str, str]:
        """Return {ARM_BASELINE: text, ARM_DEBATE: text}."""


class MockCandidateSource(CandidateSource):
    """
    Deterministic offline source.

    HONEST BY DEFAULT: both arms cover the same number of rubric criteria, so
    the expected outcome is a tie. Nothing here is evidence about Synod — it
    exercises the harness. `debate_extra` exists so tests can construct a known
    asymmetry and check the aggregation maths.
    """

    name = "mock"

    def __init__(self, baseline_covers: int = 2, debate_extra: int = 0) -> None:
        self.baseline_covers = baseline_covers
        self.debate_extra = debate_extra

    def _answer(self, task: JudgmentTask, n_covered: int) -> str:
        covered = task.rubric[:n_covered]
        body = " ".join(f"On '{c}': addressed." for c in covered)
        return f"Regarding {task.domain}: {body}".strip()

    def produce(self, task: JudgmentTask) -> dict[str, str]:
        n_base = min(self.baseline_covers, len(task.rubric))
        n_debate = min(self.baseline_covers + self.debate_extra, len(task.rubric))
        return {
            ARM_BASELINE: self._answer(task, n_base),
            ARM_DEBATE: self._answer(task, n_debate),
        }


class LiveCandidateSource(CandidateSource):
    """
    Live source built on strategy_compare.LiveRunner.

    S0 = blind solvers + one synthesis pass. S3 = LiveRunner.full_debate, which
    is a programmatic APPROXIMATION of Phases 2-4 (one critique round +
    synthesis), NOT the court pipeline. Live S3 numbers are therefore a lower
    bound on what full Synod would produce.
    """

    name = "live"

    def __init__(self) -> None:
        from benchmark.strategy_compare import LiveRunner

        self.runner = LiveRunner()

    def produce(self, task: JudgmentTask) -> dict[str, str]:
        signals = self.runner.phase1_solve(task.prompt, task.id)
        return {
            ARM_BASELINE: self.runner.synthesize(task.prompt, signals, task.id),
            ARM_DEBATE: self.runner.full_debate(task.prompt, task.id),
        }


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """\
You are grading two anonymous responses against ONE criterion only. Ignore \
length, tone, formatting and confidence. Judge only whether the criterion is \
substantively satisfied.

TASK GIVEN TO BOTH RESPONDENTS:
{prompt}

CRITERION:
{criterion}

RESPONSE 1:
{first}

RESPONSE 2:
{second}

Which response better satisfies the CRITERION? Reply with exactly one token: \
1, 2, or TIE."""


class Judge(ABC):
    """Scores one criterion for an ordered pair of anonymised responses."""

    name: str = "abstract"
    family: str = "unknown"

    @abstractmethod
    def score(self, task: JudgmentTask, criterion: str, first: str, second: str) -> str:
        """Return "1", "2", or "TIE" — positional, never arm-aware."""


class MockJudge(Judge):
    """
    Deterministic offline judge: does the response mention the criterion?

    Substring coverage is a crude proxy, but it is position-symmetric, which is
    the property the harness's own tests need — a mock judge with position bias
    would make the flip-rate diagnostic untestable.
    """

    name = "mock"
    family = "none"

    def score(self, task: JudgmentTask, criterion: str, first: str, second: str) -> str:
        a = criterion in first
        b = criterion in second
        if a and not b:
            return "1"
        if b and not a:
            return "2"
        return "TIE"


class LiveJudge(Judge):
    """
    Cross-family live judge shelling to a direct-API CLI.

    Defaults to Gemini because Synod's synthesiser is Claude, so Claude would
    be grading text it authored. Gemini is nevertheless one of the SOLVERS
    whose answers feed both arms — that residual overlap is unavoidable in this
    topology and is reported as a warning rather than hidden.
    """

    def __init__(self, provider: str = "gemini", timeout: int = 120) -> None:
        from benchmark.strategy_compare import _resolve_cli

        if provider not in _FAMILY:
            raise ValueError(f"unknown judge provider {provider!r}")
        self.name = provider
        self.family = _FAMILY[provider]
        self.timeout = timeout
        cli_name = "gemini-3" if provider == "gemini" else "openai-cli"
        self.cli = _resolve_cli(cli_name)
        key = "GEMINI_API_KEY" if provider == "gemini" else "OPENAI_API_KEY"
        if not self.cli or not os.environ.get(key):
            raise RuntimeError(f"LiveJudge prerequisites missing: {cli_name} + {key}")
        self.model_args = (
            ["--model", "pro-latest", "--thinking", "low"]
            if provider == "gemini"
            else ["--model", "gpt56sol", "--reasoning", "low"]
        )

    def score(self, task: JudgmentTask, criterion: str, first: str, second: str) -> str:
        import subprocess

        prompt = _JUDGE_PROMPT.format(
            prompt=task.prompt, criterion=criterion, first=first, second=second
        )
        cmd = [sys.executable, self.cli] if self.cli.endswith(".py") else [self.cli]
        out = subprocess.run(
            cmd + self.model_args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        ).stdout
        return _parse_verdict(out)


def _parse_verdict(text: str) -> str:
    """Extract 1 / 2 / TIE from a judge reply; unparseable replies are ties."""
    upper = text.strip().upper()
    for token in upper.replace("\n", " ").split():
        stripped = token.strip(".,:;*`\"'()[]")
        if stripped in ("1", "2", "TIE"):
            return stripped
    return "TIE"


def check_cross_family(judge: Judge) -> list[str]:
    """Return warnings when the judge overlaps the candidate authors."""
    warnings = []
    if judge.family == AUTHOR_FAMILY:
        warnings.append(
            f"CROSS-FAMILY VIOLATED: judge family {judge.family!r} is the same "
            f"family that authored the candidate text. Self-preference "
            f"(arXiv:2404.13076) is not controlled — results are not usable."
        )
    if judge.family in ("google", "openai"):
        warnings.append(
            f"Residual overlap: judge {judge.name!r} is also a Phase-1 solver, "
            f"so its own answers fed BOTH arms. Unavoidable in this topology; "
            f"treat criterion wins as indicative, not decisive."
        )
    return warnings


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _assert_anonymous(*texts: str) -> None:
    """Guard: arm identifiers must never reach the judge."""
    for t in texts:
        for leak in (ARM_BASELINE, ARM_DEBATE, "S0_", "S3_"):
            if leak in t:
                raise AssertionError(f"arm identity leaked into judge input: {leak}")


def judge_criterion(
    judge: Judge,
    task: JudgmentTask,
    index: int,
    criterion: str,
    baseline: str,
    debate: str,
) -> CriterionVerdict:
    """Judge one criterion in both position orders and reconcile."""
    _assert_anonymous(baseline, debate)

    fwd = _parse_verdict(judge.score(task, criterion, baseline, debate))
    swp = _parse_verdict(judge.score(task, criterion, debate, baseline))

    # Map positional verdicts onto arms.
    fwd_arm = {"1": ARM_BASELINE, "2": ARM_DEBATE, "TIE": "tie"}[fwd]
    swp_arm = {"1": ARM_DEBATE, "2": ARM_BASELINE, "TIE": "tie"}[swp]

    consistent = fwd_arm == swp_arm
    return CriterionVerdict(
        task_id=task.id,
        domain=task.domain,
        criterion_index=index,
        criterion=criterion,
        forward=fwd_arm,
        swapped=swp_arm,
        consistent=consistent,
        winner=fwd_arm if consistent else None,
    )


def evaluate(
    tasks: list[JudgmentTask],
    source: CandidateSource,
    judge: Judge,
) -> JudgmentReport:
    """Run the full rubric-decomposed, position-swapped comparison."""
    verdicts: list[CriterionVerdict] = []
    for task in tasks:
        candidates = source.produce(task)
        baseline = candidates[ARM_BASELINE]
        debate = candidates[ARM_DEBATE]
        for i, criterion in enumerate(task.rubric):
            verdicts.append(judge_criterion(judge, task, i, criterion, baseline, debate))

    n_criteria = len(verdicts)
    flipped = [v for v in verdicts if not v.consistent]
    consistent = [v for v in verdicts if v.consistent]
    wins_base = sum(1 for v in consistent if v.winner == ARM_BASELINE)
    wins_deb = sum(1 for v in consistent if v.winner == ARM_DEBATE)
    ties = sum(1 for v in consistent if v.winner == "tie")

    flip_rate = round(len(flipped) / n_criteria, 4) if n_criteria else 0.0
    decisive = wins_base + wins_deb
    win_rate = round(wins_deb / decisive, 4) if decisive else None

    reliable = flip_rate <= MAX_FLIP_RATE
    if not reliable:
        verdict = (
            f"NO VERDICT — position-flip rate {flip_rate:.1%} exceeds the "
            f"{MAX_FLIP_RATE:.0%} reliability bar. The judge contradicted "
            f"itself under reordering this often, so neither arm can be "
            f"declared better from this run."
        )
    elif decisive == 0:
        verdict = (
            "NO SEPARATION — every consistent judgment was a tie. The rubric "
            "did not discriminate between the arms on this sample."
        )
    elif win_rate is not None and 0.45 <= win_rate <= 0.55:
        verdict = (
            f"NO SEPARATION — debate won {win_rate:.1%} of {decisive} decisive "
            f"criteria, indistinguishable from a coin flip."
        )
    elif win_rate is not None and win_rate > 0.55:
        verdict = (
            f"DEBATE AHEAD — {ARM_DEBATE} won {win_rate:.1%} of {decisive} "
            f"decisive criteria. Weigh against its cost from strategy_compare."
        )
    else:
        verdict = (
            f"BASELINE AHEAD — {ARM_BASELINE} won {1 - (win_rate or 0):.1%} of "
            f"{decisive} decisive criteria. Debate rounds are not paying for "
            f"themselves on this set."
        )

    per_domain: dict[str, dict[str, int]] = {}
    for v in verdicts:
        d = per_domain.setdefault(v.domain, {"baseline": 0, "debate": 0, "tie": 0, "flipped": 0})
        if not v.consistent:
            d["flipped"] += 1
        elif v.winner == ARM_BASELINE:
            d["baseline"] += 1
        elif v.winner == ARM_DEBATE:
            d["debate"] += 1
        else:
            d["tie"] += 1

    return JudgmentReport(
        n_tasks=len(tasks),
        n_criteria=n_criteria,
        n_consistent=len(consistent),
        n_flipped=len(flipped),
        flip_rate=flip_rate,
        wins_baseline=wins_base,
        wins_debate=wins_deb,
        ties=ties,
        debate_win_rate=win_rate,
        verdict=verdict,
        reliable=reliable,
        per_domain=per_domain,
        verdicts=[asdict(v) for v in verdicts],
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(report: JudgmentReport, dataset: dict[str, Any]) -> str:
    """Render a compact human-readable summary."""
    lines = [
        f"Tasks: {report.n_tasks}   Criteria judged: {report.n_criteria} "
        f"(each in both position orders)",
        f"Dataset: {dataset['source']} — {dataset['n_used']} of "
        f"{dataset['pool_size']}, seed {dataset['seed']}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Position-flip rate | {report.flip_rate:.1%} ({report.n_flipped}/{report.n_criteria}) |",
        f"| Consistent judgments | {report.n_consistent} |",
        f"| {ARM_BASELINE} wins | {report.wins_baseline} |",
        f"| {ARM_DEBATE} wins | {report.wins_debate} |",
        f"| Ties | {report.ties} |",
        f"| Debate win rate (decisive only) | "
        f"{'n/a' if report.debate_win_rate is None else f'{report.debate_win_rate:.1%}'} |",
        "",
        f"VERDICT: {report.verdict}",
    ]
    if report.per_domain:
        lines += [
            "",
            "| Domain | Baseline | Debate | Tie | Flipped |",
            "|--------|----------|--------|-----|---------|",
        ]
        for dom, c in sorted(report.per_domain.items()):
            lines.append(
                f"| {dom} | {c['baseline']} | {c['debate']} | {c['tie']} | {c['flipped']} |"
            )
    return "\n".join(lines)


def build_report(
    report: JudgmentReport,
    dataset: dict[str, Any],
    source: CandidateSource,
    judge: Judge,
    warnings: list[str],
) -> dict[str, Any]:
    """Assemble the full JSON report."""
    return {
        "meta": {
            "arms": {"baseline": ARM_BASELINE, "debate": ARM_DEBATE},
            "candidate_source": source.name,
            "judge": {"name": judge.name, "family": judge.family},
            "debiasing": [
                "anonymised responses (arm identity asserted absent)",
                "position swap with agreement required",
                "rubric decomposition (one judgment per criterion)",
                f"cross-family judge (author family: {AUTHOR_FAMILY})",
            ],
            "max_flip_rate": MAX_FLIP_RATE,
            "warnings": warnings,
            "dataset": dataset,
        },
        "report": asdict(report),
        "summary": format_report(report, dataset),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synod judgment-task evaluation (debiased cross-family judge)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mock", action="store_true", help="Offline harness test")
    parser.add_argument("--live", action="store_true", help="Live providers (bills APIs)")
    parser.add_argument("--n", type=int, default=None, help="Tasks to use (default: all)")
    parser.add_argument("--seed", type=int, default=42, help="Sampling seed (default: 42)")
    parser.add_argument("--domain", default=None, help="Restrict to one domain")
    parser.add_argument(
        "--judge", default="gemini", choices=["gemini", "openai"], help="Live judge provider"
    )
    parser.add_argument("--output", default=None, help="Write JSON report here")
    args = parser.parse_args(argv)

    if args.live and args.mock:
        print("Error: --mock and --live are mutually exclusive.", file=sys.stderr)
        return 1
    if not args.live and not args.mock:
        args.mock = True
        print("Info: defaulting to --mock (harness test only).", file=sys.stderr)

    try:
        tasks, dataset = load_judgment_tasks(args.n, args.seed, args.domain)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    source: CandidateSource
    judge: Judge
    if args.mock:
        source = MockCandidateSource()
        judge = MockJudge()
    else:
        if os.environ.get("SYNOD_JUDGE_LIVE") != "1":
            print(
                "Error: live mode bills provider APIs for every candidate and "
                "every criterion (x2 for the position swap). Set "
                "SYNOD_JUDGE_LIVE=1 in addition to --live to confirm.",
                file=sys.stderr,
            )
            return 1
        try:
            source = LiveCandidateSource()
            judge = LiveJudge(args.judge)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print("No API calls were made.", file=sys.stderr)
            return 1

    warnings = check_cross_family(judge)
    if any(w.startswith("CROSS-FAMILY VIOLATED") for w in warnings):
        for w in warnings:
            print(f"Error: {w}", file=sys.stderr)
        return 1

    report = evaluate(tasks, source, judge)
    payload = build_report(report, dataset, source, judge, warnings)

    print("=" * 70)
    if args.mock:
        print("⚠️  MOCK — both arms are given equal rubric coverage by")
        print("    construction, so a tie is the expected result. This")
        print("    exercises the harness; it is NOT evidence about Synod.")
        print("=" * 70)
    print(payload["summary"])
    for w in warnings:
        print(f"\nWarning: {w}")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nReport saved to {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
