# Judgment Eval — the discriminating arm

`benchmark/judgment_eval.py` compares **S0 (independent + synthesis)** against
**S3 (full debate)** on 50 open-ended design/review tasks, scored per rubric
criterion by a debiased cross-family judge.

## Why this exists (and why `strategy_compare.py` is not enough)

`strategy_compare.py` runs the same S0-vs-S3 ablation on GSM8K. That arm
**cannot settle the question**:

- GSM8K answers are single verifiable numbers, and frontier solvers are
  near-saturated (~95%+).
- At `--n 50` near the ceiling, the 95% CI on accuracy is roughly ±6pp. Any
  S3-over-S0 gain smaller than that is indistinguishable from noise.

So the GSM8K arm is a **cost measurement and no-regression check**, not evidence
that debate helps. The literature's negative results on debate are concentrated
exactly on verifiable tasks (Smit et al. ICML 2024; [arXiv:2508.17536](https://arxiv.org/abs/2508.17536)),
and its positive predictions are for tasks with no checkable answer. This
harness runs that arm.

## The task set

`benchmark/data/judgment_tasks.jsonl` — 50 tasks, 10 domains × 5:

`architecture`, `api-design`, `code-review`, `debugging`, `performance`,
`security`, `data-modeling`, `testing`, `refactoring`, `incident-response`

Each task carries:

| Field | Meaning |
|-------|---------|
| `prompt` | An open-ended decision or review request — no single correct answer |
| `rubric` | 4 criteria, each judged **separately** (200 criteria total) |
| `failure_mode` | What a shallow single pass usually gets wrong — **this is the hypothesis under test** |

The `failure_mode` field is the point. Example (task 0):

> **prompt** — A read-heavy API serving 40k req/s is hitting Postgres connection
> limits. The team proposes adding Redis in front of every read. Evaluate.
>
> **failure_mode** — Jumps to the cache because it was proposed, without
> separating connection exhaustion from slow queries.

If debate is worth its cost, S3 should catch that distinction more often than S0.

**Honest label:** these tasks are **authored for this repository**, not drawn
from a published benchmark. There is no ground truth — only rubric criteria
scored by a judge. The provenance block in every report says so.

## The judge is the measurement instrument

Because there is no ground truth, judge bias is the main threat to validity.
Four countermeasures, all mandatory and all recorded in the report:

| # | Measure | Rationale |
|---|---------|-----------|
| 1 | **Anonymisation** — judge sees "Response 1"/"Response 2"; arm and provider names never enter the prompt (asserted at runtime) | Identity cues drive sycophantic agreement ([arXiv:2510.07517](https://arxiv.org/abs/2510.07517)); self-preference is driven by self-recognition ([Panickssery et al., arXiv:2404.13076](https://arxiv.org/abs/2404.13076)) |
| 2 | **Position swap** — every pair judged twice, (A,B) and (B,A); a judgment counts **only** if both orders agree | Judge order bias flips rankings ([arXiv:2305.17926](https://arxiv.org/abs/2305.17926)) |
| 3 | **Rubric decomposition** — one judgment per criterion, never a holistic ruling | Decomposition cuts self-preference ~31.5% ([arXiv:2604.23178](https://arxiv.org/abs/2604.23178)); holistic rulings reward rhetoric |
| 4 | **Cross-family judge** — judge family ≠ the family that authored the candidate text (Synod's synthesiser is Claude, so the judge is not Claude) | Same self-recognition result as #1 |

### The reliability gate

Disagreement between the two position orders is recorded as a **flip**. If the
flip rate exceeds `MAX_FLIP_RATE` (0.30), the harness reports:

```
VERDICT: NO VERDICT — position-flip rate 62.5% exceeds the 30% reliability bar.
```

and **claims no winner**. A judge that contradicts itself under reordering has
not measured anything; reporting its majority would be false precision. Read
`flip_rate` before you read any win number — the summary is ordered that way
deliberately.

### Residual overlap that cannot be removed

Synod's Phase-1 solvers are Gemini and OpenAI; the synthesiser is Claude. A
cross-family judge must therefore not be Claude — but Gemini and OpenAI are
themselves solvers whose answers fed **both** arms. That overlap is unavoidable
in this topology and is emitted as an explicit warning rather than hidden:

```
Warning: Residual overlap: judge 'gemini' is also a Phase-1 solver, so its own
answers fed BOTH arms. Unavoidable in this topology; treat criterion wins as
indicative, not decisive.
```

Selecting a judge from the author family (`anthropic`) is a hard error, not a
warning — the run aborts.

## Quick start — offline (CI, no API keys)

```bash
python benchmark/judgment_eval.py --mock
python benchmark/judgment_eval.py --mock --n 10 --domain security
```

Mock mode is a **harness test, not evidence**: `MockCandidateSource` gives both
arms identical rubric coverage by default, so a tie is the expected and correct
result. Anything else would mean the harness is biased.

## Live run (BILLS PROVIDER APIs — double consent required)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # candidate synthesis
export GEMINI_API_KEY="..."             # solver + judge
export OPENAI_API_KEY="sk-..."          # solver

SYNOD_JUDGE_LIVE=1 python benchmark/judgment_eval.py \
  --live --n 50 --judge gemini \
  --output benchmark/results/judgment_eval_$(date +%Y%m%d).json
```

`SYNOD_JUDGE_LIVE=1` is required **in addition** to `--live`.

### Call volume — read before running

Per task: 2 solver calls + 1 synthesis (S0), then 2 solver critique calls +
1 synthesis (S3) = **6 candidate calls**. Then per criterion: 2 judge calls
(position swap) × 4 criteria = **8 judge calls**.

| Tasks | Candidate calls | Judge calls | Total |
|-------|-----------------|-------------|-------|
| 5 | 30 | 40 | 70 |
| 50 | 300 | 400 | **700** |

Start with `--n 5` to confirm the wiring and inspect the flip rate before
committing to the full 50. If the flip rate on 5 tasks already exceeds 30%, the
judge configuration needs fixing before a full run is worth paying for.

## Reading the report

```json
{
  "meta": {
    "judge": {"name": "gemini", "family": "google"},
    "debiasing": ["anonymised responses ...", "position swap ...", "..."],
    "max_flip_rate": 0.3,
    "warnings": ["Residual overlap: ..."],
    "dataset": {"source": "judgment_tasks_authored", "n_used": 50, "seed": 42, "...": "..."}
  },
  "report": {
    "flip_rate": 0.12,
    "wins_baseline": 41, "wins_debate": 58, "ties": 77,
    "debate_win_rate": 0.5859,
    "verdict": "DEBATE AHEAD — ...",
    "reliable": true,
    "per_domain": {"security": {"baseline": 3, "debate": 9, "tie": 7, "flipped": 1}}
  }
}
```

- `debate_win_rate` is computed over **decisive** criteria only (ties excluded),
  so a large tie count cannot drag it toward 0.5.
- A win rate in [0.45, 0.55] is reported as `NO SEPARATION`, not as a narrow win.
- `per_domain` is where the interesting signal lives: debate may pay off in
  `security` or `incident-response` while being pure overhead in `api-design`.

## Interpreting a result against cost

The two harnesses answer different halves of one question. Pair them:

| | `strategy_compare.py` | `judgment_eval.py` |
|---|---|---|
| Task type | Verifiable (GSM8K) | Open-ended design/review |
| Ground truth | Yes | No — rubric + judge |
| What it settles | **Cost** per strategy, and no-regression | Whether debate **helps** |
| Discriminating power | Low (ceiling effect) | The point of the arm |

`S3` costs roughly 2× `S0` in model calls (6 vs 3 per question in the
`strategy_compare` cost model). If `judgment_eval` shows `NO SEPARATION` on a
reliable run, the debate rounds are overhead and the gate should be tightened.

## Extending the set

Tasks live in a plain JSONL file. To add one, append a row with `id`, `domain`,
`prompt`, `rubric` (≥3 criteria) and `failure_mode`. `tests/test_judgment_eval.py`
enforces the schema: unique ids and prompts, ≥3 distinct criteria per task, a
non-empty `failure_mode`, and no `####` markers (which would smuggle a
verifiable answer back in).
