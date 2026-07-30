# Strategy Compare — Accuracy vs Cost Benchmark Harness

`benchmark/strategy_compare.py` compares four Synod debate strategies over a
GSM8K sample and reports **accuracy AND cost** (model-call count, wall-time,
and token estimate) for each.

## Strategies

| ID | Name | Description |
|----|------|-------------|
| S0 | `S0_independent_synthesis` | **Killer baseline** — solvers answer blind, one synthesis pass, zero cross-talk. If S2/S3 can't beat this, debate rounds are overhead (Smit et al. ICML 2024; arXiv:2508.17536) |
| S1 | `S1_single_solver` | One strong solver call per question — cheapest |
| S2 | `S2_debate_gate` | Phase-1 solvers → `debate_gate.decide` → vote OR full debate |
| S3 | `S3_full_debate` | Always runs the full 4-phase Synod pipeline — most thorough |

## Quick start — offline (CI, no API keys)

```bash
# Run all four strategies on 10 mock GSM8K questions
python benchmark/strategy_compare.py --mock --n 10

# Enable the debate gate so S2 actually skips debate on high-agreement items
SYNOD_DEBATE_GATE=1 python benchmark/strategy_compare.py --mock --n 10
```

## Live run (BILLS THREE PROVIDER APIs — double consent required)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="..."
export OPENAI_API_KEY="sk-..."

# SYNOD_BENCH_LIVE=1 is required IN ADDITION to --live (spending guard)
SYNOD_BENCH_LIVE=1 SYNOD_DEBATE_GATE=1 \
  python benchmark/strategy_compare.py \
    --live \
    --n 50 \
    --output benchmark/results/strategy_compare_$(date +%Y%m%d).json
```

### Live mode wiring (v3.10)

- Phase-1 solvers: `gemini-3` + `openai-cli` direct-API CLIs, resolved like the
  skill does (`~/.synod/bin` → `~/.local/bin` → PATH → `tools/*.py`).
- S0 synthesis: Anthropic SDK (Claude-as-synthesizer, matching Synod topology).
- `LiveRunner.full_debate` is a programmatic APPROXIMATION of Phases 2–4
  (one critique round + synthesis), NOT the court pipeline — treat live S3
  numbers as a lower bound on full-Synod cost.
- Solver confidence is a fixed proxy (80) — live SID extraction is out of
  scope for the harness, so the S2 gate keys on answer/claim agreement.

## Debate gate toggle

S2's skip behaviour is controlled by `SYNOD_DEBATE_GATE` (v3.8: default-on):

| Value | Effect |
|-------|--------|
| unset / `1` (default) | Gate active — S2 skips debate when solvers' primary claims agree |
| `0` | Gate off — S2 always runs full debate (legacy behaviour) |

Additional gate thresholds (see `tools/debate_gate.py` for full reference):

```bash
SYNOD_GATE_AGREE_THRESHOLD=0.80   # minimum claim-agreement score to skip
SYNOD_GATE_MIN_CONF=60            # fail-closed confidence floor (not an agreement signal)
```

Retired in v3.8: `SYNOD_GATE_HIGH_CONF`, `SYNOD_GATE_MIN_TRUST`,
`SYNOD_GATE_MIN_CANEXIT` — self-reported signals no longer gate the skip.

## Token / cost model

The harness does **not** make real token-counting API calls. Costs are
estimated by multiplying model-call counts by per-tier token constants:

| Call type | Default tokens | Env override |
|-----------|---------------|-------------|
| Strong solver (S1) | 1 500 | `SYNOD_BENCH_TOKENS_STRONG` |
| Phase-1 solver (S2/S3) | 800 | `SYNOD_BENCH_TOKENS_SOLVER` |
| Full debate phase | 3 000 | `SYNOD_BENCH_TOKENS_DEBATE` |
| Blended cost/token | $0.000002 | (hardcoded, adjust in source) |

## Running tests

```bash
rtk proxy python3 -m pytest tests/test_strategy_compare.py -v
```

28 tests cover: harness completion, call-count efficiency (S2 < S3 on
high-agreement items), accuracy computation, and report shape validity.
All tests run fully offline with `MockRunner`.

---

## Live-verification gap

> **Real accuracy and cost numbers are NOT produced by this harness in its
> current state.**
>
> The `MockRunner` uses scripted correct/wrong answers and scripted agreement
> patterns — it validates harness *logic* only, not model performance.
>
> Producing real accuracy-vs-cost measurements requires:
> 1. Wiring `LiveRunner` to shell out to `gemini-3` / `openai-cli`.
> 2. Valid API keys for Anthropic, Gemini, and OpenAI.
> 3. Running against the real GSM8K test split (downloaded via
>    `benchmark/scripts/download_datasets.py`).
>
> Until those steps are completed, treat all numbers from `--mock` runs as
> proxy values that confirm the *harness works*, not that Synod debate beats
> cheaper strategies.
