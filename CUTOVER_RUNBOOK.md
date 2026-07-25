# Synod Backend Runbook — direct (default) · bridge (retired)

**Status:** cutover **completed** 2026-07-25 · bridges expired ~2026-06-30

## What this is

Synod routes its Gemini and OpenAI lanes through two backends:

| Backend | Gemini CLI | OpenAI CLI | Auth | Lifetime |
|---|---|---|---|---|
| **direct** (default) | `gemini-3.py` | `openai-cli.py` | `GEMINI_API_KEY` / `OPENAI_API_KEY` | permanent |
| **bridge** (retired) | `agy-cli` (Antigravity) | `cliproxy-cli` (CLIProxyAPI :8317) | personal OAuth / proxy | **expired ~2026-06-30** |

`config/model_matrix.json` and `config/synod-modes.yaml` are now authored in
**direct** vocabulary, and `provider_backend.DEFAULT_BACKEND` is `direct`. An
unknown `SYNOD_PROVIDER_BACKEND` value falls back to `direct`, so a typo can
never silently route through the retired bridges.

`tools/provider_backend.py` still carries the bridge translation table so an old
bridge-authored roster keeps resolving; direct models map to themselves, making
direct→direct resolution an identity operation.

### Model vocabulary

| Provider | Model key | Vendor model | Notes |
|---|---|---|---|
| gemini | `pro-latest` | `gemini-pro-latest` → `gemini-3.1-pro-preview` | stable alias, no preview EOL exposure |
| gemini | `flash-latest` | `gemini-flash-latest` | timeout-fallback lane |
| openai | `gpt56sol` | `gpt-5.6-sol` | standard/deep/ultra |
| openai | `gpt54mini` | `gpt-5.4-mini` | simple tier |

Retired bridge keys still translate: `3.1-pro`→`pro-latest`,
`3.5-flash`→`flash-latest`, `gpt55fast`→`gpt55`. `gpt55` (= gpt-5.5) remains a
valid recovery key.

### Reasoning depth is tier-controlled

`gemini-3.py` passes `--thinking` to the Gemini 3.x **native `thinking_level`
enum**, not `thinking_budget`. This matters: `thinking_budget` saturates on 3.x
and cannot reach maximum depth. Measured on `gemini-3.1-pro-preview` with a hard
reasoning prompt (2026-07-25), thought tokens / wall clock:

| Control | Thought tokens | Latency |
|---|---|---|
| `thinking_budget=200` | 1,153 | 22.3s |
| `thinking_budget=2000` | 5,766 | 55.5s |
| `thinking_budget=10000` | 5,137 | 52.6s — **no gain, saturated** |
| `thinking_level=LOW` | 2,140 | 30.0s |
| `thinking_level=HIGH` | **8,473** | **74.8s** |

`HIGH` is the deepest level the API accepts (`max` collapses to it; a literal
`thinking_level="max"` is a 400). Level and budget are mutually exclusive (400).

The OpenAI lane has the same shape. `gpt-5.6-sol` on the same prompt:

| `reasoning_effort` | Reasoning tokens | Latency |
|---|---|---|
| `low` | 1,024 | 31.6s |
| `high` | 6,656 | 120.4s |
| `xhigh` | **11,548** | **190.6s** |

`xhigh` is accepted by gpt-5.6-sol / gpt-5.5 / gpt-5.4 / gpt-5.4-mini / o3, and
rejected by gpt-5-mini (`minimal|low|medium|high`) and gpt-4o (no
`reasoning_effort` at all). `openai-cli.py` clamps `xhigh`→`high` with a stderr
notice for the models that reject it, so a shared tier config never 400s.

Because depth and latency trade off directly, the tiers split:

| Tier | gemini thinking | openai reasoning | model timeout | Rationale |
|---|---|---|---|---|
| simple | `low` | (gpt54mini, default) | 60s | `high` (~75s) would exceed the ceiling |
| standard | `low` | `low` | 120s | balanced |
| deep | `high` | `high` | 240s | Gemini maxed; OpenAI `xhigh` (~191s) leaves too little headroom |
| ultra | `high` | `xhigh` | 1800s | both lanes at maximum depth |

**Do not raise simple/standard to `high`** without also raising their timeouts.
Raising deep's OpenAI lane to `xhigh` requires lifting the 240s ceiling (and the
300s/360s outer/bash layers) first.

## Readiness check (offline — run any time)

```bash
python3 tools/cutover_check.py            # structural readiness (exit 0 = ready)
python3 tools/cutover_check.py --json     # machine-readable report
```

This verifies, with **no network call**:
1. every tier in `model_matrix.json` resolves cleanly to the direct backend,
2. each resolved direct model is a real key in the CLI `MODEL_MAP` **and** an
   accepted `--model` choice,
3. `gemini-3.py` and `openai-cli.py` exist,
4. API keys are set (advisory; enforce with `--require-keys`).

## Required setup

```bash
export GEMINI_API_KEY="..."     # or GOOGLE_API_KEY
export OPENAI_API_KEY="..."
```

No `SYNOD_PROVIDER_BACKEND` export is needed — `direct` is the default. Verify:

```bash
python3 tools/tier_matrix.py --tier deep
# expect cli: gemini-3 / openai-cli, model: pro-latest / gpt55, thinking: high
```

### Optional: decommission the bridge wrappers

```bash
rm -f ~/.synod/bin/agy-cli ~/.synod/bin/cliproxy-cli
```

## Rollback to bridge (recovery only)

The bridges have expired, so this is expected to fail against the live services.
It remains available for reproducing an old roster:

```bash
export SYNOD_PROVIDER_BACKEND=bridge
```

`bridge` is an identity pass-through — the roster is served exactly as authored.

## Verification evidence

Captured 2026-07-25 on this branch:

- `python3 tools/cutover_check.py` → **READY** (all 4 tiers × 2 providers resolve
  and validate; both API keys detected).
- `python3 tools/tier_matrix.py --tier deep` → `cli: gemini-3`, `model: pro-latest`,
  `thinking: high`, `timeout_sec: 240`; `--tier simple` → `thinking: low`, 60s.
- `python3 tools/gemini-3.py --model pro-latest --thinking high` → live response
  in 3.3s on a trivial prompt.
- `python3 -m pytest tests/ -q` → **829 passed, 14 skipped**.
- Suites covering this path: `tests/test_provider_backend.py`,
  `tests/test_cutover_check.py`, `tests/test_direct_cli_currency.py`,
  `tests/test_gemini.py`, `tests/test_synod_setup_routing.py`,
  `tests/test_tier_routing.py`.
