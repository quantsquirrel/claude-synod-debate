# Synod Module: Phase 1.5 — Debate Gate

> **DEFAULT-ON since v3.8.0** (introduced opt-in in v3.7.0) — pre-debate
> consensus check. Runs AFTER Phase 1 (Step 1.7 HALT) and BEFORE Phase 2.
> Opt out with `SYNOD_DEBATE_GATE=0` to force the full Phase 2–3 path on
> every run (legacy 4-phase behavior).
> **Deep/ultra tier always runs the full debate** regardless of agreement —
> hard contested problems are where debate pays (arXiv:2505.22960).

**Inputs:**
- `${SESSION_DIR}/round-1-solver/` — solver output directory produced by Phase 1
  (files matching `*-parsed.json`, written by `synod-parser.py`)
- `${SESSION_DIR}/meta.json` — provides `tier` (deep/ultra force `run_debate`)
- `SYNOD_DEBATE_GATE` — feature flag; active unless set to `0`

**Outputs:**
- `${SESSION_DIR}/phase1.5/gate.json` — gate decision record (always written when
  flag is active, for auditability)
- Either: continues to Phase 2/3/4 unchanged (`decision=run_debate`)
- Or: jumps directly to lightweight Phase 4 synthesis (`decision=skip_debate`),
  recording the skip in `${SESSION_DIR}/status.json`

**Cross-references:**
- Runs after Phase 1 (`synod-phase1-solver.md`), specifically after Step 1.7 HALT
- If `decision=skip_debate`, Phase 2 (`synod-phase2-critic.md`) and Phase 3
  (`synod-phase3-defense.md`) are bypassed entirely
- If `decision=run_debate`, Phase 2/3/4 proceed without modification (legacy path)
- Phase 4.5 evidence gate (`synod-phase4-5-evidence-gate.md`) runs after whichever
  Phase 4 path was taken, subject to its own flag

---

## Why this phase exists

The full Synod debate cycle (Phases 2–3) is expensive in both latency and external
model calls. When solvers have already converged on the same answer — same primary
claim, high confidence — running a critic round and a court-style defense adds noise,
not signal. The original responses simply get echoed back to each other.

Phase 1.5 detects this convergence cheaply before any Phase 2 API call is made:

1. **Claim Agreement** — pairwise Jaccard over the primary semantic-focus tokens of
   each solver's response (negation-polarity-aware). High overlap means solvers are
   making the same core claim. **This is the primary gate signal** — self-reported
   confidence is deliberately NOT an agreement input, because verbal self-confidence
   is near-uninformative and escalates across rounds regardless of merit
   (arXiv:2505.19184).
2. **Confidence floor** — a single modest fail-closed guard (min per-solver
   confidence ≥ 60). Its only cost is fewer skips; it never causes a skip on its own.
3. **Tier override** — deep/ultra tier always runs the full debate.
4. Any failure (including parsing errors or missing files) forces `run_debate`
   for safety.

The gate makes zero external model calls — it operates entirely on the JSON files
already written by Phase 1.

## Step 1.5.1 — Check Feature Flag

```bash
if [[ "${SYNOD_DEBATE_GATE:-1}" == "0" ]]; then
    echo "[Phase 1.5] Skipped — gate disabled (SYNOD_DEBATE_GATE=0)" >&2
    # → proceed directly to Phase 2 (legacy path unchanged)
fi
```

The gate is active by default. Only when the flag is explicitly `0` is the rest
of this phase skipped and Phase 2 begins immediately as in pre-v3.8 versions.

## Step 1.5.2 — Run Debate Gate

```bash
mkdir -p "${SESSION_DIR}/phase1.5"
# Tier from meta.json (Phase 0 classifier); deep/ultra force run_debate.
_META_TIER=$(python3 -c \
  "import json,os; f='${SESSION_DIR}/meta.json'; \
   d=json.load(open(f)) if os.path.exists(f) else {}; \
   print(d.get('tier','standard'))" 2>/dev/null || echo "standard")
python3 "${PLUGIN_ROOT}/tools/debate_gate.py" \
    --signals-dir "${SESSION_DIR}/round-1-solver" \
    --tier "$_META_TIER" \
    > "${SESSION_DIR}/phase1.5/gate.json"
GATE_EXIT=$?
GATE_DECISION=$(python3 -c \
    "import json; print(json.load(open('${SESSION_DIR}/phase1.5/gate.json'))['decision'])" \
    2>/dev/null || echo "run_debate")
```

On any non-zero exit or parse failure, `GATE_DECISION` defaults to `run_debate`
(fail-safe). The gate never blocks the pipeline.

## Step 1.5.3 — Decision Routing

```
IF GATE_DECISION == "skip_debate":
    → Record skip in status.json, then jump to Step 1.5.4 (lightweight synthesis)

IF GATE_DECISION == "run_debate":
    → Continue to Phase 2 unchanged (no modification to critic/defense flow)
```

Decision table:

| `decision` | `agreement_score` (claim agreement) | `min_confidence` | tier | Action |
|---|---|---|---|---|
| `skip_debate` | ≥ 0.80 | ≥ 60 | fast/standard | Bypass Phases 2–3; lightweight Phase 4 |
| `run_debate` | < 0.80 OR < 60 | (any) | (any) | Full Phase 2 → 3 → 4 path |
| `run_debate` (tier) | (any) | (any) | deep/ultra | Full path — debate reserved for hard problems |
| `run_debate` (fail-safe) | parse error | (any) | (any) | Full path (gate failed open) |

Thresholds are set by `debate_gate.py` defaults (`SYNOD_GATE_AGREE_THRESHOLD=0.80`,
`SYNOD_GATE_MIN_CONF=60`). They are intentionally conservative: the gate only skips
when evidence of agreement is strong.

## Step 1.5.4 — Lightweight Phase 4 Synthesis (skip_debate path only)

Runs only when `GATE_DECISION == "skip_debate"`. Phases 2 and 3 are not executed.

```bash
# Record skip in session state
python3 -c "
import json, pathlib
p = pathlib.Path('${SESSION_DIR}/status.json')
s = json.loads(p.read_text()) if p.exists() else {}
gate = json.load(open('${SESSION_DIR}/phase1.5/gate.json'))
s['phase1_5_skipped_debate'] = True
s['agreement_score'] = gate.get('agreement_score')
s['dominant_model'] = gate.get('dominant_model')
p.write_text(json.dumps(s, indent=2))
"
```

Then proceed directly to Phase 4 synthesis (`synod-phase4-synthesis.md`) with the
following adjustments:

- **Input source**: Use solver outputs from `round-1-solver/` directly (no critic
  or defense files exist — Phase 4 must not expect them).
- **Decision metrics**: Use `agreement_score`, `n_solvers`, and `dominant_model`
  from `gate.json` for the Phase 4 합의 지표 block (no CRIS trust scores exist on
  this path; `vote_confidence` is display-only context).
- **Verdict note**: Prepend the following line to the final synthesis output,
  immediately after the mode header:

  ```
  ⚡ Debate skipped — solvers reached consensus (agreement={agreement_score:.2f},
     confidence={vote_confidence:.0f}%). Phases 2–3 bypassed.
  ```

- **Phase 4.5** evidence gate runs normally afterward if `SYNOD_EVIDENCE_FIRST=1`.

## Opt-out path (`SYNOD_DEBATE_GATE=0`)

If the flag is explicitly `0`:

```bash
echo "[Phase 1.5] Skipped — legacy mode (SYNOD_DEBATE_GATE=0)" >&2
```

Phase 2 proceeds immediately with raw Phase 1 outputs, identical to pre-v3.8
versions. No files are written to `phase1.5/`. This preserves an escape hatch
for users who always want the full debate.
