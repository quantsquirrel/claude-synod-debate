#!/usr/bin/env python3
"""
strategy_compare.py — Accuracy-vs-Cost benchmark harness for Synod strategies.

Compares four strategies over a GSM8K sample:

  S0 — independent + synthesis: solvers answer blind, one synthesis pass —
                                the KILLER BASELINE the literature says
                                explains most of MAD's gains
  S1 — single strong solver   : one model call per question
  S2 — debate-gate            : Phase-1 solvers → debate_gate.decide → vote OR debate
  S3 — full 4-phase debate    : always runs the full deliberation pipeline

The question Synod must answer for itself: does S3 (or S2) beat S0 on the
real workload? If not, the debate rounds are overhead (Smit et al. ICML 2024;
arXiv:2508.17536).

Architecture
------------
The harness is built around a pluggable Runner interface.  In CI (offline),
use MockRunner with scripted answers.  For live evaluation, LiveRunner (v3.10)
shells to the direct-API CLIs (gemini-3 / openai-cli) and uses the Anthropic
SDK for synthesis. LiveRunner.full_debate is a programmatic APPROXIMATION
(critique round + synthesis), not the full court pipeline.

Cost model
----------
We do NOT make real API calls to measure tokens.  Instead we multiply
model-call counts by per-tier token estimates (configurable).  This is a
proxy for real cost — see the "Live-verification gap" section in
benchmark/README_strategy_compare.md.

Usage (offline / CI)
--------------------
    python benchmark/strategy_compare.py --mock --n 10

Usage (live — BILLS THREE PROVIDER APIs; double consent required)
------------
    SYNOD_BENCH_LIVE=1 ANTHROPIC_API_KEY=... GEMINI_API_KEY=... OPENAI_API_KEY=... \\
    python benchmark/strategy_compare.py --live --n 50 --output results/strategy_compare.json

Live-verification gap
---------------------
Mock-mode numbers validate the harness only (S3 is scripted-correct BY
CONSTRUCTION; S0's mock synthesis is an honest majority vote). Only
LiveRunner results are evidence about Synod accuracy.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Locate project root / tools import
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_TOOLS = _ROOT / "tools"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _import_debate_gate():
    """Import tools/debate_gate.py (has hyphen-safe path)."""
    gate_path = _TOOLS / "debate_gate.py"
    spec = importlib.util.spec_from_file_location("debate_gate", gate_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SolverSignal:
    """Signals produced by a single Phase-1 solver for one question."""

    model: str
    answer: str  # extracted numeric string
    confidence: float  # 0-100
    can_exit: bool
    semantic_focus: list[str]
    trust_score: float = 1.0


@dataclass
class QuestionResult:
    """Result of running a single strategy on one question."""

    question_id: int
    expected: str
    predicted: str | None
    is_correct: bool
    model_calls: int  # total provider calls made
    wall_seconds: float
    token_estimate: int  # proxy token count
    gate_decision: str | None = None  # 'skip_debate' | 'run_debate' | None
    strategy: str = ""


@dataclass
class StrategyReport:
    """Aggregate report for one strategy over all questions."""

    strategy: str
    n_questions: int
    n_correct: int
    accuracy: float
    total_calls: int
    calls_per_question: float
    total_wall_seconds: float
    total_token_estimate: int
    estimated_cost_usd: float
    per_question_results: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Token / cost model
# ---------------------------------------------------------------------------

# Tokens per call tier (rough proxy — not real measurements).
# Adjust via SYNOD_BENCH_TOKENS_STRONG / _SOLVER / _DEBATE env vars.
_DEFAULT_TOKENS_STRONG = 1_500  # single strong solver call
_DEFAULT_TOKENS_SOLVER = 800  # one Phase-1 solver call (fast/medium)
_DEFAULT_TOKENS_DEBATE = 3_000  # one full debate-phase call

# Cost per token in USD (blended fast-tier estimate: ~$1/M input, ~$3/M output)
_COST_PER_TOKEN = 2e-6  # $2 per 1M tokens blended


def _token_cfg() -> dict[str, int]:
    def _e(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (ValueError, TypeError):
            return default

    return {
        "strong": _e("SYNOD_BENCH_TOKENS_STRONG", _DEFAULT_TOKENS_STRONG),
        "solver": _e("SYNOD_BENCH_TOKENS_SOLVER", _DEFAULT_TOKENS_SOLVER),
        "debate": _e("SYNOD_BENCH_TOKENS_DEBATE", _DEFAULT_TOKENS_DEBATE),
    }


# ---------------------------------------------------------------------------
# Answer normalisation (reuse run_gsm8k pattern)
# ---------------------------------------------------------------------------


def _extract_numeric(text: str) -> str | None:
    """Extract a numeric answer from free-form text."""
    patterns = [
        r"####\s*(-?\d+(?:,\d+)*(?:\.\d+)?)",
        r"(?:answer|Answer)[:\s]+(-?\d+(?:,\d+)*(?:\.\d+)?)",
        r"(?:therefore|thus|so|Hence)[,\s]*.*?(-?\d+(?:,\d+)*(?:\.\d+)?)",
        r"\*\*(-?\d+(?:,\d+)*(?:\.\d+)?)\*\*",
    ]
    for pat in patterns:
        m = re.findall(pat, text, re.IGNORECASE)
        if m:
            return m[-1].replace(",", "")
    nums = re.findall(r"(?<!\d)(-?\d+(?:\.\d+)?)(?!\d)", text)
    return nums[-1] if nums else None


def _answers_match(expected: str, predicted: str | None) -> bool:
    """Numeric-tolerant exact match (mirrors evaluator.GSM8KEvaluator.is_correct)."""
    if predicted is None:
        return False
    try:
        return abs(float(expected) - float(predicted)) < 0.001
    except ValueError:
        return expected.strip() == predicted.strip()


# ---------------------------------------------------------------------------
# Runner interface
# ---------------------------------------------------------------------------


class Runner(ABC):
    """
    Pluggable solver interface.

    Implementations must be stateless per-call (they may hold config).
    """

    # ---------- Phase-1 solvers ----------

    @abstractmethod
    def phase1_solve(self, prompt: str, question_id: int) -> list[SolverSignal]:
        """
        Run Phase-1 solvers.  Returns one SolverSignal per model.
        For S1 this is called with n_models=1.
        For S2/S3 this returns signals from all configured solvers.
        """

    # ---------- Full debate ----------

    @abstractmethod
    def full_debate(self, prompt: str, question_id: int) -> str:
        """
        Run the full 4-phase Synod debate pipeline.
        Returns the final synthesised answer text.
        """

    # ---------- Weighted vote (S2 skip path) ----------

    def vote(self, signals: list[SolverSignal]) -> str:
        """
        Default: pick the answer with the highest weighted-confidence sum.
        Override for custom consensus logic.
        """
        scores: dict[str, float] = {}
        for s in signals:
            key = s.answer or ""
            scores[key] = scores.get(key, 0.0) + s.confidence * s.trust_score
        if not scores:
            return ""
        return max(scores, key=lambda k: scores[k])

    # ---------- Synthesis (S0 path) ----------

    def synthesize(self, prompt: str, signals: list[SolverSignal], question_id: int) -> str:
        """
        S0: one synthesis pass over the INDEPENDENT solver answers — the
        "killer baseline" the debate literature says explains most of MAD's
        gains (Smit et al. ICML 2024; arXiv:2508.17536). Zero cross-talk
        between solvers; the synthesizer reads their answers cold.

        Default implementation reuses vote(); Live/Mock runners override with
        an actual synthesis call / honest deterministic proxy.
        """
        return self.vote(signals)


# ---------------------------------------------------------------------------
# MockRunner — deterministic offline runner for CI
# ---------------------------------------------------------------------------


class MockRunner(Runner):
    """
    Deterministic offline runner.  No model calls.

    Script format
    -------------
    answers : dict[int, str]
        question_id → correct numeric answer string.
        Any id NOT in this dict gets a wrong answer ("999999").

    agreement_ids : set[int]
        Question IDs where Phase-1 solvers agree AND are confident enough
        for debate_gate to skip debate (used by S2 tests).

    call_log : list[str]
        Appended to on each call for assertion in tests.
    """

    # Number of Phase-1 solver models simulated
    N_SOLVERS = 2

    def __init__(
        self,
        answers: dict[int, str],
        agreement_ids: list[int] | None = None,
    ) -> None:
        self.answers = answers
        self.agreement_ids: set = set(agreement_ids or [])
        self.call_log: list[str] = []

    # --- Phase-1 ---

    def phase1_solve(self, prompt: str, question_id: int) -> list[SolverSignal]:
        self.call_log.append(f"phase1:{question_id}")
        correct_answer = self.answers.get(question_id, "999999")
        is_agree = question_id in self.agreement_ids

        signals = []
        for i in range(self.N_SOLVERS):
            model_name = f"mock_model_{i}"
            if is_agree:
                # All solvers agree on the correct answer with high confidence
                signals.append(
                    SolverSignal(
                        model=model_name,
                        answer=correct_answer,
                        confidence=92.0,
                        can_exit=True,
                        semantic_focus=[
                            f"the answer is {correct_answer} based on arithmetic",
                            "step by step computation",
                        ],
                        trust_score=1.2,
                    )
                )
            else:
                # Solvers disagree — model 0 correct, model 1 wrong
                answer = correct_answer if i == 0 else str(int(correct_answer) + 7)
                signals.append(
                    SolverSignal(
                        model=model_name,
                        answer=answer,
                        confidence=65.0,
                        can_exit=False,
                        semantic_focus=[f"answer {answer} via calculation"],
                        trust_score=0.8,
                    )
                )
        return signals

    # --- Full debate ---

    def full_debate(self, prompt: str, question_id: int) -> str:
        self.call_log.append(f"debate:{question_id}")
        # SCRIPTED: the mock hands full debate the correct answer BY CONSTRUCTION.
        # Any S3 "win" in mock mode is an artifact of this line, not evidence
        # that debate improves accuracy. Only LiveRunner results are evidence.
        return f"#### {self.answers.get(question_id, '999999')}"

    # --- S0 synthesis ---

    def synthesize(self, prompt: str, signals: list[SolverSignal], question_id: int) -> str:
        self.call_log.append(f"synthesize:{question_id}")
        # HONEST mock (unlike full_debate above): majority answer, ties broken
        # by highest confidence. Deliberately NOT scripted-correct, so mock-mode
        # S0-vs-S3 comparisons expose the S3 script rather than hide it.
        counts: dict[str, int] = {}
        best_conf: dict[str, float] = {}
        for s in signals:
            key = s.answer or ""
            counts[key] = counts.get(key, 0) + 1
            best_conf[key] = max(best_conf.get(key, 0.0), s.confidence)
        if not counts:
            return "#### 999999"
        winner = max(counts, key=lambda k: (counts[k], best_conf[k]))
        return f"#### {winner}"


# ---------------------------------------------------------------------------
# LiveRunner — direct-API CLIs (gemini-3 / openai-cli) + Anthropic synthesis
# ---------------------------------------------------------------------------

_SOLVE_PROMPT = """Solve this problem step by step. Be concise.

Problem:
{question}

REQUIRED: End your response with the final answer in this exact format:
#### <number>
"""

_SYNTH_PROMPT = """You are a synthesizer. {n} models independently answered the
same problem (they never saw each other's work). Read their answers and output
the answer you judge correct.

Problem:
{question}

Independent answers:
{answers}

REQUIRED: End your response with the final answer in this exact format:
#### <number>
"""

_CRITIQUE_PROMPT = """You previously answered a problem. Other models answered
independently. Review all answers, point out any error in your own reasoning,
then give your final answer.

Problem:
{question}

All answers (yours is {model}):
{answers}

REQUIRED: End your response with the final answer in this exact format:
#### <number>
"""


def _resolve_cli(cmd: str) -> str | None:
    """Resolve a synod CLI following the skill's lookup order."""
    import shutil

    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, ".synod", "bin", cmd),
        os.path.join(home, ".local", "bin", cmd),
    ):
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(cmd)
    if found:
        return found
    script = _TOOLS / f"{cmd}.py"
    if script.exists():
        return str(script)
    return None


class LiveRunner(Runner):
    """
    Live runner against the CURRENT direct-API lanes (v3.10 — the retired
    agy-cli/cliproxy-cli wiring is gone):

      - Phase-1 solvers: `gemini-3` + `openai-cli` CLIs (GEMINI_API_KEY /
        OPENAI_API_KEY), resolved like the skill does (~/.synod/bin →
        ~/.local/bin → PATH → tools/*.py).
      - S0 synthesis: Anthropic SDK (ANTHROPIC_API_KEY), matching Synod's
        Claude-as-synthesizer topology.
      - full_debate: a programmatic APPROXIMATION of Phases 2-4 — one critique
        round (each solver sees the others' answers and may revise) followed
        by synthesis. It is NOT the court pipeline; treat S3-live numbers as
        a lower bound on full-Synod cost and an approximation of its accuracy.

    Costs real money. Guarded by SYNOD_BENCH_LIVE=1 in main(); constructor
    raises RuntimeError when prerequisites are missing rather than failing
    mid-run.
    """

    SOLVER_CONF = 80.0  # fixed proxy — live SID extraction is out of scope here

    def __init__(self, timeout: int = 120) -> None:
        self.timeout = timeout
        self.gemini_cli = _resolve_cli("gemini-3")
        self.openai_cli = _resolve_cli("openai-cli")
        missing = []
        if not self.gemini_cli or not os.environ.get("GEMINI_API_KEY"):
            missing.append("gemini-3 CLI + GEMINI_API_KEY")
        if not self.openai_cli or not os.environ.get("OPENAI_API_KEY"):
            missing.append("openai-cli + OPENAI_API_KEY")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            missing.append("ANTHROPIC_API_KEY (S0 synthesis)")
        if missing:
            raise RuntimeError(f"LiveRunner prerequisites missing: {'; '.join(missing)}")

    # ---- provider calls ----

    def _run_cli(self, cli: str, model_args: list[str], prompt: str) -> str:
        cmd = ([sys.executable, cli] if cli.endswith(".py") else [cli]) + model_args
        result = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout
        )
        return result.stdout.strip()

    def _claude(self, prompt: str) -> str:
        from anthropic import Anthropic

        response = Anthropic().messages.create(
            model="claude-sonnet-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _solver_calls(self, prompt: str) -> dict[str, str]:
        solve = _SOLVE_PROMPT.format(question=prompt)
        return {
            "gemini": self._run_cli(
                self.gemini_cli, ["--model", "pro-latest", "--thinking", "low"], solve
            ),
            "openai": self._run_cli(
                self.openai_cli, ["--model", "gpt56sol", "--reasoning", "low"], solve
            ),
        }

    # ---- Runner interface ----

    def phase1_solve(self, prompt: str, question_id: int) -> list[SolverSignal]:
        signals = []
        for model, text in self._solver_calls(prompt).items():
            answer = _extract_numeric(text)
            signals.append(
                SolverSignal(
                    model=model,
                    answer=answer,
                    confidence=self.SOLVER_CONF,
                    can_exit=False,
                    semantic_focus=[f"the answer is {answer} for this problem"],
                    trust_score=1.0,
                )
            )
        return signals

    def synthesize(self, prompt: str, signals: list[SolverSignal], question_id: int) -> str:
        answers = "\n".join(f"- model {i + 1}: {s.answer}" for i, s in enumerate(signals))
        return self._claude(_SYNTH_PROMPT.format(n=len(signals), question=prompt, answers=answers))

    def full_debate(self, prompt: str, question_id: int) -> str:
        first = self._solver_calls(prompt)
        answer_block = "\n".join(f"- {m}: {_extract_numeric(t)}" for m, t in first.items())
        revised = []
        for model in first:
            cli, margs = (
                (self.gemini_cli, ["--model", "pro-latest", "--thinking", "low"])
                if model == "gemini"
                else (self.openai_cli, ["--model", "gpt56sol", "--reasoning", "low"])
            )
            text = self._run_cli(
                cli,
                margs,
                _CRITIQUE_PROMPT.format(question=prompt, model=model, answers=answer_block),
            )
            revised.append(f"- {model} (revised): {_extract_numeric(text)}")
        return self._claude(
            _SYNTH_PROMPT.format(n=len(revised), question=prompt, answers="\n".join(revised))
        )


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


class StrategyS0:
    """
    S0 — Independent answers + one synthesis pass. ZERO cross-talk.

    The killer baseline: the literature attributes most of MAD's measured
    gains to exactly this (independent generation + aggregation), with the
    discussion phase adding no expected correctness on consensus-reachable
    problems (Smit et al. ICML 2024; martingale result arXiv:2508.17536).
    If S3 cannot beat S0 on Synod's own workload, the debate rounds are
    overhead.
    """

    name = "S0_independent_synthesis"

    def __init__(self, runner: Runner, token_cfg: dict[str, int] | None = None) -> None:
        self.runner = runner
        self.tc = token_cfg or _token_cfg()

    def run_question(self, prompt: str, question_id: int, expected: str) -> QuestionResult:
        t0 = time.perf_counter()

        signals = self.runner.phase1_solve(prompt, question_id)
        n_solvers = len(signals)
        synth_text = self.runner.synthesize(prompt, signals, question_id)
        predicted = _extract_numeric(synth_text)

        wall = time.perf_counter() - t0
        calls = n_solvers + 1  # solvers + one synthesis call
        tokens = self.tc["solver"] * n_solvers + self.tc["strong"]

        return QuestionResult(
            question_id=question_id,
            expected=expected,
            predicted=predicted,
            is_correct=_answers_match(expected, predicted),
            model_calls=calls,
            wall_seconds=round(wall, 4),
            token_estimate=tokens,
            gate_decision=None,
            strategy=self.name,
        )


class StrategyS1:
    """
    S1 — Single strong solver.

    One call → extract answer.  Cheapest strategy.
    """

    name = "S1_single_solver"

    def __init__(self, runner: Runner, token_cfg: dict[str, int] | None = None) -> None:
        self.runner = runner
        self.tc = token_cfg or _token_cfg()

    def run_question(self, prompt: str, question_id: int, expected: str) -> QuestionResult:
        t0 = time.perf_counter()
        signals = self.runner.phase1_solve(prompt, question_id)
        wall = time.perf_counter() - t0

        # Use just the first solver signal
        first = signals[0] if signals else None
        predicted = first.answer if first else None
        calls = 1
        tokens = self.tc["strong"]

        return QuestionResult(
            question_id=question_id,
            expected=expected,
            predicted=predicted,
            is_correct=_answers_match(expected, predicted),
            model_calls=calls,
            wall_seconds=round(wall, 4),
            token_estimate=tokens,
            gate_decision=None,
            strategy=self.name,
        )


class StrategyS2:
    """
    S2 — Debate-gate strategy.

    Phase-1 solvers → debate_gate.decide:
      skip_debate → weighted vote (cheap)
      run_debate  → full 4-phase debate (expensive)

    Requires SYNOD_DEBATE_GATE=1 for the gate to actually skip.
    """

    name = "S2_debate_gate"

    def __init__(self, runner: Runner, token_cfg: dict[str, int] | None = None) -> None:
        self.runner = runner
        self.tc = token_cfg or _token_cfg()
        self._gate = _import_debate_gate()

    def _signals_to_gate_dicts(self, signals: list[SolverSignal]) -> list[dict[str, Any]]:
        return [
            {
                "model": s.model,
                "confidence": s.confidence,
                "can_exit": s.can_exit,
                "semantic_focus": s.semantic_focus,
                "trust_score": s.trust_score,
            }
            for s in signals
        ]

    def run_question(self, prompt: str, question_id: int, expected: str) -> QuestionResult:
        t0 = time.perf_counter()

        # Phase-1: solver calls (N_SOLVERS calls)
        signals = self.runner.phase1_solve(prompt, question_id)
        n_solvers = len(signals)
        solver_tokens = self.tc["solver"] * n_solvers

        # Gate decision
        gate_dicts = self._signals_to_gate_dicts(signals)
        gate_result = self._gate.decide(gate_dicts)
        decision = gate_result["decision"]

        if decision == "skip_debate":
            # Cheap path: weighted vote over solver answers
            predicted = self.runner.vote(signals)
            calls = n_solvers
            tokens = solver_tokens
        else:
            # Expensive path: full debate
            debate_text = self.runner.full_debate(prompt, question_id)
            predicted = _extract_numeric(debate_text)
            calls = n_solvers + 4  # approx phase 2/3/4/synthesis calls
            tokens = solver_tokens + self.tc["debate"]

        wall = time.perf_counter() - t0

        return QuestionResult(
            question_id=question_id,
            expected=expected,
            predicted=predicted,
            is_correct=_answers_match(expected, predicted),
            model_calls=calls,
            wall_seconds=round(wall, 4),
            token_estimate=tokens,
            gate_decision=decision,
            strategy=self.name,
        )


class StrategyS3:
    """
    S3 — Full 4-phase debate, always.

    Most expensive strategy; highest expected accuracy ceiling.
    """

    name = "S3_full_debate"

    def __init__(self, runner: Runner, token_cfg: dict[str, int] | None = None) -> None:
        self.runner = runner
        self.tc = token_cfg or _token_cfg()

    def run_question(self, prompt: str, question_id: int, expected: str) -> QuestionResult:
        t0 = time.perf_counter()

        # Phase-1 solvers
        signals = self.runner.phase1_solve(prompt, question_id)
        n_solvers = len(signals)
        # Always debate regardless of agreement
        debate_text = self.runner.full_debate(prompt, question_id)

        wall = time.perf_counter() - t0

        predicted = _extract_numeric(debate_text)
        calls = n_solvers + 4
        tokens = self.tc["solver"] * n_solvers + self.tc["debate"]

        return QuestionResult(
            question_id=question_id,
            expected=expected,
            predicted=predicted,
            is_correct=_answers_match(expected, predicted),
            model_calls=calls,
            wall_seconds=round(wall, 4),
            token_estimate=tokens,
            gate_decision="run_debate",  # always
            strategy=self.name,
        )


# ---------------------------------------------------------------------------
# Harness orchestrator
# ---------------------------------------------------------------------------


def run_strategy(
    strategy: Any,
    questions: list[dict[str, Any]],
) -> StrategyReport:
    """
    Run one strategy over a list of question dicts.

    Each question dict must have:
        id       : int
        question : str
        answer   : str   (GSM8K-format, e.g. "#### 42")
    """
    results = []
    for q in questions:
        qid = q["id"]
        expected_raw = q["answer"]
        expected = _extract_gsm8k_answer(expected_raw)
        result = strategy.run_question(q["question"], qid, expected)
        results.append(result)

    n = len(results)
    n_correct = sum(1 for r in results if r.is_correct)
    total_calls = sum(r.model_calls for r in results)
    total_wall = sum(r.wall_seconds for r in results)
    total_tokens = sum(r.token_estimate for r in results)
    accuracy = n_correct / n if n else 0.0

    return StrategyReport(
        strategy=strategy.name,
        n_questions=n,
        n_correct=n_correct,
        accuracy=round(accuracy, 4),
        total_calls=total_calls,
        calls_per_question=round(total_calls / n, 2) if n else 0.0,
        total_wall_seconds=round(total_wall, 4),
        total_token_estimate=total_tokens,
        estimated_cost_usd=round(total_tokens * _COST_PER_TOKEN, 6),
        per_question_results=[asdict(r) for r in results],
    )


def _extract_gsm8k_answer(text: str) -> str:
    """Extract numeric answer after #### (GSM8K label format)."""
    m = re.search(r"####\s*(-?\d+(?:,\d+)*(?:\.\d+)?)", text)
    if m:
        return m.group(1).replace(",", "")
    # Fallback: treat whole string as the answer
    return text.strip()


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _fmt_report_table(reports: list[StrategyReport]) -> str:
    """Render a compact markdown table."""
    header = (
        "| Strategy | Accuracy | Calls/Q | Total Calls | "
        "Token Est. | Est. Cost USD | Wall (s) |\n"
        "|----------|----------|---------|-------------|"
        "-----------|---------------|----------|\n"
    )
    rows = []
    for r in reports:
        rows.append(
            f"| {r.strategy} "
            f"| {r.accuracy:.1%} "
            f"| {r.calls_per_question:.1f} "
            f"| {r.total_calls} "
            f"| {r.total_token_estimate:,} "
            f"| ${r.estimated_cost_usd:.4f} "
            f"| {r.total_wall_seconds:.3f} |"
        )
    return header + "\n".join(rows)


def build_report(
    reports: list[StrategyReport],
    dataset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the full JSON report dict.

    `dataset` carries question-set provenance (source, pool size, seed) so a
    committed result file states what it was measured on.
    """
    return {
        "meta": {
            "n_questions": reports[0].n_questions if reports else 0,
            "cost_model": {
                "tokens_strong": _DEFAULT_TOKENS_STRONG,
                "tokens_solver": _DEFAULT_TOKENS_SOLVER,
                "tokens_debate": _DEFAULT_TOKENS_DEBATE,
                "cost_per_token_usd": _COST_PER_TOKEN,
            },
            "live_verification_gap": (
                "Token estimates and wall-time are proxies only. "
                "Real accuracy/cost numbers require the live lanes "
                "(gemini-3 + openai-cli CLIs for solvers, Anthropic SDK for "
                "synthesis). Run with --live after setting ANTHROPIC_API_KEY, "
                "GEMINI_API_KEY, OPENAI_API_KEY."
            ),
            "dataset": dataset,
        },
        "strategies": [asdict(r) for r in reports],
        "table": _fmt_report_table(reports),
    }


# ---------------------------------------------------------------------------
# Inline mock GSM8K loader (no HuggingFace required offline)
# ---------------------------------------------------------------------------


def load_mock_gsm8k(n: int = 10) -> list[dict[str, Any]]:
    """
    Return a tiny deterministic GSM8K-shaped dataset for offline CI use.
    Answers are all small integers to simplify verification.
    """
    problems = [
        ("Janet has 3 apples. She buys 4 more. How many apples does she have?", "#### 7"),
        ("A store sells 12 items at $5 each. What is the total revenue?", "#### 60"),
        ("Tom walks 3 miles north and 4 miles east. What is the straight-line distance?", "#### 5"),
        ("A class has 30 students. 12 are absent. How many are present?", "#### 18"),
        ("Sara earns $120 in 8 hours. What is her hourly rate?", "#### 15"),
        ("A rectangle is 6 m long and 4 m wide. What is its area?", "#### 24"),
        ("There are 5 rows of 7 seats. How many seats total?", "#### 35"),
        ("A car travels 180 km in 3 hours. What is the average speed?", "#### 60"),
        ("If 8 workers finish a job in 6 days, how many days for 12 workers?", "#### 4"),
        ("A pizza is cut into 8 slices. 3 people each eat 2 slices. How many are left?", "#### 2"),
    ]
    return [{"id": i, "question": q, "answer": a} for i, (q, a) in enumerate(problems[:n])]


MOCK_POOL_SIZE = 10
_VENDORED_POOL = _HERE / "data" / "gsm8k_style_pool.jsonl"


def _load_vendored_pool() -> list[dict[str, Any]]:
    with open(_VENDORED_POOL) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_gsm8k(n: int, seed: int = 42) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Load `n` GSM8K questions for a LIVE run, with provenance.

    Prefers the real GSM8K test split when `datasets` is installed; otherwise
    falls back to the vendored multi-step pool in `benchmark/data/`. Sampling is
    seeded so a committed result file is reproducible.

    Raises ValueError when the pool cannot supply `n` questions. The inline
    `load_mock_gsm8k` this replaces for live runs did `problems[:n]`, so
    `--n 50` silently measured 10 single-step questions — a billed run that
    could not distinguish the strategies.

    Returns (questions, provenance).
    """
    if n < 1:
        raise ValueError(f"--n must be at least 1, got {n}")

    pool: list[dict[str, Any]] = []
    source = ""
    try:
        from datasets import load_dataset

        ds = load_dataset("gsm8k", "main", split="test")
        pool = [
            {"id": i, "question": r["question"], "answer": r["answer"]} for i, r in enumerate(ds)
        ]
        source = "gsm8k_test_hf"
    except Exception:
        pool = []

    if not pool:
        pool = _load_vendored_pool()
        source = "gsm8k_style_vendored"

    if len(pool) < n:
        raise ValueError(
            f"requested --n {n} but the '{source}' pool holds only {len(pool)} "
            f"questions. Install `datasets` for the full GSM8K test split "
            f"(1319 items), or lower --n. Refusing to silently truncate."
        )

    rng = random.Random(seed)
    sample = rng.sample(pool, n)
    questions = [{"id": q["id"], "question": q["question"], "answer": q["answer"]} for q in sample]
    provenance = {
        "source": source,
        "pool_size": len(pool),
        "n_requested": n,
        "n_used": len(questions),
        "seed": seed,
        "question_ids": [q["id"] for q in questions],
        "power_caveat": (
            "GSM8K is near-saturated for frontier solvers (~95%+). At n=50 the "
            "95% CI on accuracy is roughly +/-6pp, so an S3-over-S0 gain "
            "smaller than that is indistinguishable from noise. Treat this arm "
            "as a cost measurement and a no-regression check, not as evidence "
            "that debate helps; the judgment-task set is the discriminating arm."
        ),
    }
    if source == "gsm8k_style_vendored":
        provenance["honest_label"] = (
            "Authored GSM8K-STYLE problems (2-4 arithmetic steps), not items "
            "from the published GSM8K test split. Every answer is computed from "
            "an explicit expression recorded in the pool file's `expr` field."
        )
    return questions, provenance


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synod accuracy-vs-cost strategy benchmark harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Use deterministic MockRunner (offline, no API keys needed)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Use LiveRunner (requires API keys and running model services)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=10,
        help="Number of questions to evaluate (default: 10)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for GSM8K sample selection (default: 42)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write JSON report (default: print to stdout only)",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        default=False,
        help="Enable debate gate for S2 (sets SYNOD_DEBATE_GATE=1)",
    )
    args = parser.parse_args(argv)

    if args.live and args.mock:
        print("Error: --mock and --live are mutually exclusive.", file=sys.stderr)
        return 1

    if not args.mock and not args.live:
        # Default to mock if no keys are set
        has_keys = all(
            os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
        )
        args.mock = not has_keys
        if args.mock:
            print(
                "Info: API keys not set — running with MockRunner. Pass --live to force live mode.",
                file=sys.stderr,
            )

    # Enable gate for S2 if requested
    if args.gate:
        os.environ["SYNOD_DEBATE_GATE"] = "1"

    # Build runner
    if args.mock:
        if args.n > MOCK_POOL_SIZE:
            print(
                f"Warning: --mock pool holds {MOCK_POOL_SIZE} questions; "
                f"--n {args.n} is capped to {MOCK_POOL_SIZE}. Use --live for larger runs.",
                file=sys.stderr,
            )
        questions = load_mock_gsm8k(args.n)
        dataset = {
            "source": "mock_inline",
            "pool_size": MOCK_POOL_SIZE,
            "n_requested": args.n,
            "n_used": len(questions),
            "seed": None,
        }
        # All even-ID questions are "agreement" questions (S2 will skip debate)
        agreement_ids = [q["id"] for q in questions if q["id"] % 2 == 0]
        correct_answers = {q["id"]: _extract_gsm8k_answer(q["answer"]) for q in questions}
        runner: Runner = MockRunner(answers=correct_answers, agreement_ids=agreement_ids)
    else:
        # Double consent for spending: --live AND SYNOD_BENCH_LIVE=1.
        if os.environ.get("SYNOD_BENCH_LIVE") != "1":
            print(
                "Error: live mode bills three provider APIs per question. "
                "Set SYNOD_BENCH_LIVE=1 in addition to --live to confirm.",
                file=sys.stderr,
            )
            return 1
        try:
            questions, dataset = load_gsm8k(args.n, args.seed)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(
            f"Dataset: {dataset['source']} — {dataset['n_used']} of "
            f"{dataset['pool_size']} questions, seed {dataset['seed']}",
            file=sys.stderr,
        )
        try:
            runner = LiveRunner()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print(
                "No API calls were made. Set the missing variables and re-run; "
                "see benchmark/README_strategy_compare.md for the live wiring.",
                file=sys.stderr,
            )
            return 1

    tc = _token_cfg()

    # Run all four strategies (S0 = the killer baseline: independent + synthesis)
    s0 = StrategyS0(runner, tc)
    s1 = StrategyS1(runner, tc)
    s2 = StrategyS2(runner, tc)
    s3 = StrategyS3(runner, tc)

    print("Running S0 (independent + synthesis)...")
    r0 = run_strategy(s0, questions)
    print("Running S1 (single solver)...")
    r1 = run_strategy(s1, questions)
    print("Running S2 (debate-gate)...")
    r2 = run_strategy(s2, questions)
    print("Running S3 (full debate)...")
    r3 = run_strategy(s3, questions)

    report = build_report([r0, r1, r2, r3], dataset=dataset)

    # Print table
    print("\n" + "=" * 70)
    print("Accuracy vs Cost — Strategy Comparison")
    if args.mock:
        print("⚠️  SCRIPTED MOCK — S3 (full debate) wins BY CONSTRUCTION:")
        print("    MockRunner.full_debate returns the ground-truth answer directly.")
        print("    These numbers exercise the harness only; they are NOT evidence")
        print("    of Synod accuracy. Use LiveRunner for real measurements.")
    print("=" * 70)
    print(report["table"])
    print()

    # Optionally save JSON
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
