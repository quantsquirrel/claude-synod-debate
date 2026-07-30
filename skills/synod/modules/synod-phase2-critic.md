# Synod Module: Phase 2 - Critic Round

**Inputs:**
- `${SESSION_DIR}/round-1-solver/` - All Solver responses
- `PROBLEM` - Original problem statement
- `GEMINI_CLI`, `OPENAI_CLI` - CLI executable paths
- `SYNOD_PARSER_CLI` - Parser executable path

**Outputs:**
- `${SESSION_DIR}/round-2-critic/aggregation.md`
- `${SESSION_DIR}/round-2-critic/gemini-critique.md`
- `${SESSION_DIR}/round-2-critic/openai-critique.md`
- `${SESSION_DIR}/round-2-critic/trust-scores.json`
- `${SESSION_DIR}/round-2-critic/contentions.json`
- Updated `status.json` with round 2 complete

**Cross-references:**
- Called after Phase 1 (`synod-phase1-solver.md`)
- Outputs consumed by Phase 3 (`synod-phase3-defense.md`)
- Timeout failures trigger `synod-error-handling.md`

---

```bash
# Emit phase start (v2.1)
synod_progress '{"event":"phase_start","phase":2,"name":"Critic Round"}'

# Load tier-aware timeouts (v3.3).
# Tier is read from meta.json (written by Phase 0 classifier); falls back to
# 'standard' so the behavior is identical to v2.1 when meta.json is absent.
_META_TIER=$(python3 -c \
  "import json,os; f='${SESSION_DIR}/meta.json'; \
   d=json.load(open(f)) if os.path.exists(f) else {}; \
   print(d.get('tier','standard'))" 2>/dev/null || echo "standard")
MODEL_TIMEOUT=$(python3 "${TOOLS_DIR}/synod_config.py" timeouts model --tier "$_META_TIER" 2>/dev/null || echo "180")
BASH_TIMEOUT=$(python3 "${TOOLS_DIR}/synod_config.py" timeouts bash  --tier "$_META_TIER" 2>/dev/null || echo "300")
BASH_TIMEOUT_MS=$((BASH_TIMEOUT * 1000))
```

## Step 2.0: Deliberation Anonymization (default ON since v3.8)

> **Default-ON — set `SYNOD_ANONYMIZE=0` to opt out.**
> When opted out, all branding and labelling behaves exactly as before — no change.

Unless `SYNOD_ANONYMIZE=0`:

```bash
if [[ "${SYNOD_ANONYMIZE:-1}" == "1" ]]; then
  # Re-hydrate the alias map exported by Phase 1.
  # SYNOD_ANON_MAP is a JSON string: {"claude":"Agent-1","gemini":"Agent-2","openai":"Agent-3"}
  if [[ -z "${SYNOD_ANON_MAP:-}" ]]; then
    echo "[Warning] SYNOD_ANONYMIZE=1 but SYNOD_ANON_MAP is unset — rebuilding map" >&2
    SYNOD_ANON_MAP=$(python3 -c "
import sys; sys.path.insert(0,'${TOOLS_DIR}')
import model_branding, json
print(json.dumps(model_branding.build_anon_map(['claude','gemini','openai'])))
")
  fi

  ALIAS_CLAUDE=$(echo "$SYNOD_ANON_MAP" | python3 -c "import json,sys; print(json.load(sys.stdin)['claude'])")
  ALIAS_GEMINI=$(echo "$SYNOD_ANON_MAP" | python3 -c "import json,sys; print(json.load(sys.stdin)['gemini'])")
  ALIAS_OPENAI=$(echo "$SYNOD_ANON_MAP" | python3 -c "import json,sys; print(json.load(sys.stdin)['openai'])")

  echo "[Phase 2] Anonymization ON — aliases in scope: claude=$ALIAS_CLAUDE gemini=$ALIAS_GEMINI openai=$ALIAS_OPENAI" >&2
else
  ALIAS_CLAUDE="Claude"
  ALIAS_GEMINI="Gemini"
  ALIAS_OPENAI="OpenAI"
fi
```

**When anonymization is active**, substitute aliases for real model names in:

- The `HISTORY_CONTEXT` table assembled for external critic prompts (Step 2.1):
  replace `| Claude |`, `| Gemini |`, `| OpenAI |` with `| $ALIAS_CLAUDE |`,
  `| $ALIAS_GEMINI |`, `| $ALIAS_OPENAI |`.
- The saved artefacts (`aggregation.md`, `trust-scores.json`, `contentions.json`):
  write aliases, not real names, so Phase 3 never sees provider identity.

---

## Step 2.1: Claude Aggregation

As the orchestrator, analyze all three Solver responses:

1. **Identify Agreement Points** - Claims supported by 2+ models
2. **Identify Contentions** - Conflicting claims or approaches
3. **Spot Weaknesses** - Unsupported claims, logical gaps, missing considerations

**v3.9 — lossless claim list.** The pre-v3.9 HISTORY_CONTEXT compressed each
solver to one ≤30-word sentence — exactly the cross-round factual-attrition
mechanism the literature documents (arXiv:2606.03032: up to 72% of
issue-critical facts erased as rounds progress). HISTORY_CONTEXT is now built
MECHANICALLY from the parsed signals, preserving all semantic_focus claims
verbatim with stable ids:

```bash
CLAIM_LIST=$(run_cli "$SYNOD_PARSER_CLI" --claim-list "${SESSION_DIR}/round-1-solver")
```

```
## Prior Round — Claim Ledger

{CLAIM_LIST}
# | ID | Agent | Conf | Claim |  ← C1..C3 Claude, G1..G3 Gemini, O1..O3 OpenAI

**Contentions**: {1-2문장으로 핵심 쟁점만, 최대 2개}
```

If the parser call fails or returns empty, fall back to the legacy hand-written
table (fail-safe — never block Phase 2 on the formatter). When anonymization is
active, alias the Agent column exactly as in the pre-v3.9 table.

## Step 2.1b: Low Confidence Soft Defer Check

Round 1에서 추출한 confidence 점수를 분석:

```bash
CLAUDE_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/claude-parsed.json")
GEMINI_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/gemini-parsed.json")
OPENAI_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/openai-parsed.json")

# Load low confidence threshold from config (v2.1)
LOW_CONF_THRESHOLD=$(python3 "${TOOLS_DIR}/synod_config.py" thresholds low_confidence 2>/dev/null || echo "50")

# Generate soft defer hints
SOFT_DEFER_HINT=""
if [[ $GEMINI_CONF -lt $LOW_CONF_THRESHOLD ]] || [[ $OPENAI_CONF -lt $LOW_CONF_THRESHOLD ]]; then
  SOFT_DEFER_HINT="
## IMPORTANT: Preserve Unique Perspectives
Some agents expressed low confidence (score < 50) in the previous round.
This often indicates genuine uncertainty or novel insights.
Do NOT rush to consensus - maintain your unique analytical perspective.
If you disagree with other agents, articulate WHY with evidence.
"
fi
```

**Claude Confidence 제외 근거**:
- Claude는 orchestrator 역할로서 전체 세션을 조율함
- Claude의 low confidence는 조기 종료 조건(can_exit)에서만 사용됨
- Soft defer 힌트는 외부 모델(Gemini/OpenAI)이 합의를 서두르지 않도록 하는 목적
- Claude 자신은 프롬프트를 받는 대상이 아니므로 힌트 삽입 대상이 아님

## Step 2.2: Gemini Critic Execution

Write to `${TEMP_DIR}/gemini-critic-prompt.txt`:

```
You are a CRITIC in a multi-agent deliberation council (Synod).

{SOFT_DEFER_HINT}

## Your Task
Validate claims from the Solver round. Focus on:
- Are claims backed by evidence?
- Are there logical errors?
- What's missing?

Where possible, reference specific claim IDs from the ledger below (e.g.
"G2 lacks evidence because…"). Free-text critique is also accepted.

## Prior Round Context
{HISTORY_CONTEXT}

## Original Problem
{PROBLEM}

Before critiquing, confirm the discussion still answers this original
question; if it has drifted, flag the drift explicitly as your first point.

## REQUIRED Output Format

<critique>
### Validated Claims (with evidence)
{list claims that are well-supported}

### Disputed Claims (with reasons)
{list claims that lack evidence or have issues}

### Missing Considerations
{what did solvers overlook?}
</critique>

<confidence score="[0-100]">
  <evidence>[Evidence quality of your critique]</evidence>
  <logic>[Soundness of your analysis]</logic>
  <expertise>[Your domain confidence]</expertise>
  <can_exit>[true if debate should end]</can_exit>
</confidence>

<semantic_focus>
1. [Most important critique point]
2. [Secondary critique]
3. [Tertiary critique]
</semantic_focus>
```

Execute (**Bash tool timeout: `${BASH_TIMEOUT_MS:-300000}` ms**):
```bash
# Gemini Critic execution (medium thinking for analytical evaluation)
synod_progress '{"event":"model_start","model":"gemini"}'
$GEMINI_CLI --model {GEMINI_MODEL} --thinking {GEMINI_THINKING} --timeout ${MODEL_TIMEOUT:-180} < "${TEMP_DIR}/gemini-critic-prompt.txt" > "${TEMP_DIR}/gemini-critique.txt" 2>&1 &
```

## Step 2.3: OpenAI Critic Execution

Write to `${TEMP_DIR}/openai-critic-prompt.txt`:

```
You are a LOGIC CHECKER in a multi-agent deliberation council (Synod).

{SOFT_DEFER_HINT}

## Your Task
Find counter-examples and logical flaws. Focus on:
- Edge cases that break proposed solutions
- Assumptions that might be wrong
- Alternative interpretations

Where possible, reference specific claim IDs from the ledger below (e.g.
"O1 fails when…"). Free-text critique is also accepted.

## Prior Round Context
{HISTORY_CONTEXT}

## Original Problem
{PROBLEM}

Before critiquing, confirm the discussion still answers this original
question; if it has drifted, flag the drift explicitly as your first point.

## REQUIRED Output Format

<critique>
### Counter-Examples Found
{specific cases that challenge solutions}

### Logical Flaws Detected
{invalid reasoning, false premises}

### Alternative Interpretations
{different ways to view the problem}
</critique>

<confidence score="[0-100]">
  <evidence>[Evidence for your counter-examples]</evidence>
  <logic>[Soundness of your logical analysis]</logic>
  <expertise>[Your domain confidence]</expertise>
  <can_exit>[true if no major issues found]</can_exit>
</confidence>

<semantic_focus>
1. [Most critical counter-example or flaw]
2. [Secondary issue]
3. [Tertiary issue]
</semantic_focus>
```

## Step 2.3b: Execution Arbiter (default-on since v3.12; debug/review modes only)

> **Mode-gated — runs when `MODE` is `debug` or `review` AND Phase 0.5 ran with
> a `TARGET_PATH` AND the probe collected at least one test. Set
> `SYNOD_EXEC_ARBITER=0` to disable; because this step executes code, the check
> is `== "1"` so any unrecognised value fails toward NOT running.**
> Execution is the arbiter the literature trusts for code disputes
> (SWE-bench execution-grounded selection, arXiv:2510.02387; Tool-MAD
> arXiv:2601.04742): models debate only what execution cannot settle.
>
> The three remaining conditions already select exactly the situations where
> execution can settle anything, so the extra opt-in flag only suppressed a
> signal the pipeline had already qualified. **It runs the target repo's own
> test suite** — pytest imports `conftest.py` and every test module during
> collection, so set `SYNOD_EXEC_ARBITER=0` for targets whose suite has real
> side effects (live services, shared databases, outbound mail).

```bash
if [[ "${SYNOD_EXEC_ARBITER:-1}" == "1" && ( "$MODE" == "debug" || "$MODE" == "review" ) \
      && -n "${TARGET_PATH:-}" && -d "${SESSION_DIR}/phase0.5/probe" ]]; then
  python3 "${TOOLS_DIR}/exec_arbiter.py" \
      --target "$TARGET_PATH" \
      --probe-dir "${SESSION_DIR}/phase0.5/probe" \
      --timeout 120 \
      > "${SESSION_DIR}/round-2-critic/exec-arbiter.json"
  ARBITER_STATUS=$(python3 -c \
      "import json; print(json.load(open('${SESSION_DIR}/round-2-critic/exec-arbiter.json'))['status'])" \
      2>/dev/null || echo "error")
  echo "[Phase 2] Execution arbiter: ${ARBITER_STATUS}" >&2
fi
```

When `status` is `passed` or `failed`, append to HISTORY_CONTEXT (and to the
Phase 3 court context) under the Phase 0.5 machine-verified convention:

```
## Primary Evidence (machine-verified — authoritative)
Test suite execution: {status} (exit={exit_code}, {collected} tests collected)
{report_tail, indented}
Claims contradicted by this execution result are REFUTED — do not re-litigate
them; debate only what execution cannot settle.
```

`skipped`/`timeout`/`error` statuses are logged but NOT injected — an absent or
hung suite settles nothing (`timeout` explicitly means UNSETTLED, not failing).

## Step 2.4: Calculate Trust Scores (v3.10 — mechanical, CRIS rubric demoted)

> **The self-graded C/R/I/S rubric is demoted in v3.10.** It was Claude
> grading itself and its rivals on unmeasurable qualities — the judge-bias
> literature counter-indicates LLM-judged trust overrides (net-negative,
> arXiv:2606.29270). Trust now comes from auditable, mechanical signals.
> The `--trust C R I S` parser CLI (CortexDebate formula) remains available
> as a utility but is no longer part of the default flow.

**Path A — TARGET_PATH set (evidence-verifiable runs):**

Trust = verified-citation rate per model, computed by the citation verifier
over the Phase 1 responses:

```bash
CITE_TRUST=$(python3 "${TOOLS_DIR}/citation_verifier.py" \
    --target "$TARGET_PATH" \
    --dir "${SESSION_DIR}/round-1-solver" | python3 -c \
    "import json,sys; print(json.dumps(json.load(sys.stdin).get('trust',{})))")
# e.g. {"claude": 1.56, "gemini": 2.0, "openai": 0.25}
```

Mapping (`trust_from_rate`): `T = 0.25 + 1.75 × verified_rate`, so all-verified
→ 2.0 (trust_cap), all-fabricated → 0.25 (below trust_exclude 0.5 — the model
is excluded from synthesis), no decidable citations → 1.0 (neutral).

**Path B — no TARGET_PATH (design/general questions):**

Uniform neutral trust `T = 1.0` for every model. There is no mechanically
verifiable evidence to grade, and self-graded rubrics are not a substitute.
Defendant selection in Phase 3 falls back to the highest SID confidence as a
tiebreak (a selection heuristic only — confidence remains display-only
everywhere else).

**Both paths** write `trust-scores.json` with the same schema as before
(`{model: {trust_score, ...}}`) plus a `"basis"` field:
`"citation-verification"` or `"uniform"` — so Phase 3/4 consumers and the
resume path are unchanged.

**Thresholds (unchanged, from config):**
- T < 0.5 = Exclude from synthesis (unless all are low - see `synod-error-handling.md`)
- T >= 1.5 = High trust (consider as primary source)
- T >= 1.0 = Good trust
- T >= 0.5 = Acceptable trust

## Step 2.5: Save Critic Round State

Save to `${SESSION_DIR}/round-2-critic/`:
- `aggregation.md` - Claude's aggregation and summary
- `gemini-critique.md` - Gemini's critique
- `openai-critique.md` - OpenAI's critique
- `trust-scores.json` - All Trust Score calculations
- `contentions.json` - List of disputed points

Update status.json to round 2 complete.

```bash
# Emit model completions and phase end (v2.1)
synod_progress '{"event":"model_complete","model":"gemini"}'
synod_progress '{"event":"model_complete","model":"openai"}'
synod_progress '{"event":"phase_end","phase":2}'
```

**Next Phase:** Proceed to Phase 3 (see `synod-phase3-defense.md`)
