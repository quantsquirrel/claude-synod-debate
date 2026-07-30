#!/usr/bin/env python3
"""
Tests for benchmark/judgment_eval.py — the judgment-task arm.

The judge is the measurement instrument here, so most of these tests are about
the DEBIASING machinery rather than the happy path: a judge that leaks arm
identity, ignores the position swap, or silently truncates the task pool would
produce numbers that look fine and mean nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_BENCHMARK = _ROOT / "benchmark"

for _p in (str(_ROOT), str(_BENCHMARK)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from benchmark.judgment_eval import (  # noqa: E402
    ARM_BASELINE,
    ARM_DEBATE,
    AUTHOR_FAMILY,
    MAX_FLIP_RATE,
    CandidateSource,
    Judge,
    JudgmentTask,
    MockCandidateSource,
    MockJudge,
    _assert_anonymous,
    _parse_verdict,
    build_report,
    check_cross_family,
    evaluate,
    format_report,
    judge_criterion,
    load_judgment_tasks,
)

_TASKS_PATH = _BENCHMARK / "data" / "judgment_tasks.jsonl"


# ---------------------------------------------------------------------------
# 1. Task-set integrity
# ---------------------------------------------------------------------------


class TestTaskSet:
    @staticmethod
    def _rows() -> list[dict]:
        with open(_TASKS_PATH) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_has_50_tasks(self) -> None:
        assert len(self._rows()) == 50

    def test_ten_domains_evenly_covered(self) -> None:
        counts: dict[str, int] = {}
        for r in self._rows():
            counts[r["domain"]] = counts.get(r["domain"], 0) + 1
        assert len(counts) == 10
        assert set(counts.values()) == {5}

    def test_every_task_has_at_least_three_criteria(self) -> None:
        assert all(len(r["rubric"]) >= 3 for r in self._rows())

    def test_every_task_names_a_failure_mode(self) -> None:
        """The failure_mode IS the hypothesis: what a single pass misses."""
        assert all(r["failure_mode"].strip() for r in self._rows())

    def test_ids_and_prompts_unique(self) -> None:
        rows = self._rows()
        assert len({r["id"] for r in rows}) == len(rows)
        assert len({r["prompt"] for r in rows}) == len(rows)

    def test_rubric_criteria_are_distinct_within_a_task(self) -> None:
        for r in self._rows():
            assert len(set(r["rubric"])) == len(r["rubric"]), f"task {r['id']}"

    def test_prompts_are_open_ended_not_arithmetic(self) -> None:
        """A verifiable numeric answer would put us back on the GSM8K ceiling."""
        for r in self._rows():
            assert "####" not in r["prompt"]


# ---------------------------------------------------------------------------
# 2. Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_default_loads_whole_pool(self) -> None:
        tasks, prov = load_judgment_tasks()
        assert len(tasks) == 50 == prov["n_used"]
        assert prov["seed"] is None

    def test_returns_judgment_task_objects(self) -> None:
        tasks, _ = load_judgment_tasks(3)
        assert all(isinstance(t, JudgmentTask) for t in tasks)

    def test_refuses_to_truncate(self) -> None:
        with pytest.raises(ValueError, match="Refusing to silently truncate"):
            load_judgment_tasks(500)

    def test_rejects_non_positive_n(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            load_judgment_tasks(0)

    def test_seed_is_deterministic(self) -> None:
        a, _ = load_judgment_tasks(20, seed=5)
        b, _ = load_judgment_tasks(20, seed=5)
        assert [t.id for t in a] == [t.id for t in b]

    def test_different_seeds_differ(self) -> None:
        a, _ = load_judgment_tasks(20, seed=1)
        b, _ = load_judgment_tasks(20, seed=2)
        assert [t.id for t in a] != [t.id for t in b]

    def test_domain_filter(self) -> None:
        tasks, prov = load_judgment_tasks(domain="security")
        assert len(tasks) == 5
        assert {t.domain for t in tasks} == {"security"}
        assert prov["domain_filter"] == "security"

    def test_unknown_domain_raises(self) -> None:
        with pytest.raises(ValueError, match="no tasks for domain"):
            load_judgment_tasks(domain="astrology")

    def test_domain_filter_respects_pool_bound(self) -> None:
        with pytest.raises(ValueError, match="in domain 'security'"):
            load_judgment_tasks(6, domain="security")

    def test_provenance_carries_honest_label(self) -> None:
        _, prov = load_judgment_tasks(2)
        assert "AUTHORED" in prov["honest_label"]
        assert "no ground truth" in prov["honest_label"]


# ---------------------------------------------------------------------------
# 3. Anonymisation
# ---------------------------------------------------------------------------


class TestAnonymisation:
    def test_leak_of_arm_name_raises(self) -> None:
        with pytest.raises(AssertionError, match="arm identity leaked"):
            _assert_anonymous(f"this came from {ARM_DEBATE}")

    def test_clean_text_passes(self) -> None:
        _assert_anonymous("a perfectly anonymous response")

    def test_mock_candidates_are_anonymous(self) -> None:
        tasks, _ = load_judgment_tasks(5)
        src = MockCandidateSource()
        for t in tasks:
            c = src.produce(t)
            _assert_anonymous(c[ARM_BASELINE], c[ARM_DEBATE])

    def test_judge_never_receives_arm_labels(self) -> None:
        """Capture what the judge actually sees and assert identity is absent."""
        seen: list[str] = []

        class SpyJudge(Judge):
            name = "spy"
            family = "none"

            def score(self, task, criterion, first, second):  # type: ignore[no-untyped-def]
                seen.append(first + "\n" + second)
                return "TIE"

        tasks, _ = load_judgment_tasks(3)
        evaluate(tasks, MockCandidateSource(), SpyJudge())
        assert seen
        for blob in seen:
            assert ARM_BASELINE not in blob and ARM_DEBATE not in blob


# ---------------------------------------------------------------------------
# 4. Position swap
# ---------------------------------------------------------------------------


class _FixedJudge(Judge):
    """Always returns the same positional token."""

    name = "fixed"
    family = "none"

    def __init__(self, token: str) -> None:
        self.token = token

    def score(self, task, criterion, first, second):  # type: ignore[no-untyped-def]
        return self.token


class TestPositionSwap:
    def _task(self) -> JudgmentTask:
        return load_judgment_tasks(1, seed=3)[0][0]

    def test_positional_verdicts_map_onto_arms(self) -> None:
        """'1' forward means baseline; '1' swapped means debate."""
        t = self._task()
        v = judge_criterion(_FixedJudge("1"), t, 0, t.rubric[0], "alpha", "beta")
        assert v.forward == ARM_BASELINE
        assert v.swapped == ARM_DEBATE

    def test_first_position_bias_is_caught_as_a_flip(self) -> None:
        t = self._task()
        v = judge_criterion(_FixedJudge("1"), t, 0, t.rubric[0], "alpha", "beta")
        assert v.consistent is False
        assert v.winner is None

    def test_consistent_tie_is_recorded_as_tie(self) -> None:
        t = self._task()
        v = judge_criterion(_FixedJudge("TIE"), t, 0, t.rubric[0], "alpha", "beta")
        assert v.consistent is True
        assert v.winner == "tie"

    def test_every_criterion_is_judged_twice(self) -> None:
        calls: list[int] = []

        class CountingJudge(Judge):
            name = "counting"
            family = "none"

            def score(self, task, criterion, first, second):  # type: ignore[no-untyped-def]
                calls.append(1)
                return "TIE"

        tasks, _ = load_judgment_tasks(4, seed=9)
        n_criteria = sum(len(t.rubric) for t in tasks)
        evaluate(tasks, MockCandidateSource(), CountingJudge())
        assert len(calls) == 2 * n_criteria


# ---------------------------------------------------------------------------
# 5. Reliability gate
# ---------------------------------------------------------------------------


class TestReliabilityGate:
    def test_position_biased_judge_yields_no_verdict(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=4)
        report = evaluate(tasks, MockCandidateSource(), _FixedJudge("1"))
        assert report.flip_rate == 1.0
        assert report.reliable is False
        assert report.verdict.startswith("NO VERDICT")

    def test_no_winner_is_claimed_when_unreliable(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=4)
        report = evaluate(tasks, MockCandidateSource(), _FixedJudge("1"))
        assert report.wins_baseline == 0 and report.wins_debate == 0

    def test_flip_rate_threshold_is_the_documented_one(self) -> None:
        assert MAX_FLIP_RATE == 0.30

    def test_equal_coverage_gives_no_separation(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=4)
        report = evaluate(tasks, MockCandidateSource(), MockJudge())
        assert report.reliable is True
        assert report.flip_rate == 0.0
        assert report.debate_win_rate is None
        assert report.verdict.startswith("NO SEPARATION")


# ---------------------------------------------------------------------------
# 6. Aggregation maths
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_extra_coverage_makes_debate_win(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=6)
        src = MockCandidateSource(baseline_covers=2, debate_extra=2)
        report = evaluate(tasks, src, MockJudge())
        assert report.wins_debate > 0
        assert report.wins_baseline == 0
        assert report.debate_win_rate == 1.0
        assert report.verdict.startswith("DEBATE AHEAD")

    def test_extra_coverage_for_baseline_makes_baseline_win(self) -> None:
        """Symmetry check — the harness must not be biased toward debate."""
        tasks, _ = load_judgment_tasks(10, seed=6)
        src = MockCandidateSource(baseline_covers=4, debate_extra=-2)
        report = evaluate(tasks, src, MockJudge())
        assert report.wins_baseline > 0
        assert report.wins_debate == 0
        assert report.debate_win_rate == 0.0
        assert report.verdict.startswith("BASELINE AHEAD")

    def test_counts_sum_to_criteria_total(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=6)
        report = evaluate(tasks, MockCandidateSource(1, 2), MockJudge())
        total = report.wins_baseline + report.wins_debate + report.ties + report.n_flipped
        assert total == report.n_criteria

    def test_per_domain_totals_match_overall(self) -> None:
        tasks, _ = load_judgment_tasks(20, seed=8)
        report = evaluate(tasks, MockCandidateSource(1, 2), MockJudge())
        summed = sum(sum(c.values()) for c in report.per_domain.values())
        assert summed == report.n_criteria

    def test_win_rate_excludes_ties(self) -> None:
        tasks, _ = load_judgment_tasks(10, seed=6)
        report = evaluate(tasks, MockCandidateSource(2, 2), MockJudge())
        assert report.ties > 0
        # Rate is over decisive criteria only, so ties cannot drag it toward 0.5.
        assert report.debate_win_rate == 1.0


# ---------------------------------------------------------------------------
# 7. Cross-family constraint
# ---------------------------------------------------------------------------


class TestCrossFamily:
    def test_author_family_judge_is_a_violation(self) -> None:
        class SelfJudge(Judge):
            name = "claude"
            family = AUTHOR_FAMILY

            def score(self, task, criterion, first, second):  # type: ignore[no-untyped-def]
                return "TIE"

        warnings = check_cross_family(SelfJudge())
        assert any(w.startswith("CROSS-FAMILY VIOLATED") for w in warnings)

    def test_solver_family_judge_gets_residual_overlap_warning(self) -> None:
        class GeminiJudge(Judge):
            name = "gemini"
            family = "google"

            def score(self, task, criterion, first, second):  # type: ignore[no-untyped-def]
                return "TIE"

        warnings = check_cross_family(GeminiJudge())
        assert warnings
        assert not any(w.startswith("CROSS-FAMILY VIOLATED") for w in warnings)
        assert any("Residual overlap" in w for w in warnings)

    def test_independent_judge_has_no_warnings(self) -> None:
        assert check_cross_family(MockJudge()) == []


# ---------------------------------------------------------------------------
# 8. Verdict parsing
# ---------------------------------------------------------------------------


class TestParseVerdict:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1", "1"),
            ("2", "2"),
            ("TIE", "TIE"),
            ("  2  ", "2"),
            ("tie", "TIE"),
            ("**1**", "1"),
            ("Answer: 2", "2"),
            ("`TIE`", "TIE"),
        ],
    )
    def test_parses_expected_tokens(self, raw: str, expected: str) -> None:
        assert _parse_verdict(raw) == expected

    def test_unparseable_reply_defaults_to_tie(self) -> None:
        """An unreadable judge reply must not silently become a win."""
        assert _parse_verdict("I cannot decide between these responses.") == "TIE"

    def test_empty_reply_defaults_to_tie(self) -> None:
        assert _parse_verdict("") == "TIE"


# ---------------------------------------------------------------------------
# 9. Report shape
# ---------------------------------------------------------------------------


class TestReport:
    def _payload(self) -> dict:
        tasks, dataset = load_judgment_tasks(5, seed=2)
        src = MockCandidateSource()
        judge = MockJudge()
        report = evaluate(tasks, src, judge)
        return build_report(report, dataset, src, judge, check_cross_family(judge))

    def test_meta_records_debiasing_measures(self) -> None:
        meta = self._payload()["meta"]
        assert len(meta["debiasing"]) == 4
        assert any("position swap" in d for d in meta["debiasing"])
        assert any("rubric decomposition" in d for d in meta["debiasing"])

    def test_meta_records_judge_and_arms(self) -> None:
        meta = self._payload()["meta"]
        assert meta["judge"]["name"] == "mock"
        assert meta["arms"]["baseline"] == ARM_BASELINE
        assert meta["arms"]["debate"] == ARM_DEBATE

    def test_meta_carries_dataset_provenance(self) -> None:
        assert self._payload()["meta"]["dataset"]["source"] == "judgment_tasks_authored"

    def test_summary_leads_with_flip_rate(self) -> None:
        """Flip rate must be readable before any win number."""
        summary = self._payload()["summary"]
        assert summary.index("Position-flip rate") < summary.index("wins")

    def test_report_is_json_serialisable(self) -> None:
        json.dumps(self._payload())

    def test_format_report_includes_verdict(self) -> None:
        tasks, dataset = load_judgment_tasks(5, seed=2)
        report = evaluate(tasks, MockCandidateSource(), MockJudge())
        assert "VERDICT:" in format_report(report, dataset)


# ---------------------------------------------------------------------------
# 10. CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_mock_run_exits_zero(self) -> None:
        from benchmark.judgment_eval import main

        assert main(["--mock", "--n", "5"]) == 0

    def test_mock_and_live_are_exclusive(self) -> None:
        from benchmark.judgment_eval import main

        assert main(["--mock", "--live"]) == 1

    def test_live_requires_double_consent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from benchmark.judgment_eval import main

        monkeypatch.delenv("SYNOD_JUDGE_LIVE", raising=False)
        assert main(["--live", "--n", "2"]) == 1

    def test_oversized_n_exits_one(self) -> None:
        from benchmark.judgment_eval import main

        assert main(["--mock", "--n", "500"]) == 1

    def test_writes_output_file(self, tmp_path: Path) -> None:
        from benchmark.judgment_eval import main

        out = tmp_path / "nested" / "judgment.json"
        assert main(["--mock", "--n", "3", "--output", str(out)]) == 0
        payload = json.loads(out.read_text())
        assert payload["report"]["n_tasks"] == 3


# ---------------------------------------------------------------------------
# 11. Candidate source contract
# ---------------------------------------------------------------------------


class TestCandidateSource:
    def test_mock_is_honest_by_default(self) -> None:
        """Default mock must NOT hand debate an advantage by construction."""
        tasks, _ = load_judgment_tasks(5, seed=1)
        src = MockCandidateSource()
        for t in tasks:
            c = src.produce(t)
            assert c[ARM_BASELINE] == c[ARM_DEBATE]

    def test_produce_returns_both_arms(self) -> None:
        tasks, _ = load_judgment_tasks(1)
        c = MockCandidateSource().produce(tasks[0])
        assert set(c) == {ARM_BASELINE, ARM_DEBATE}

    def test_abstract_source_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            CandidateSource()  # type: ignore[abstract]

    def test_abstract_judge_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            Judge()  # type: ignore[abstract]
