# Synod Module: Phase 4 - Synthesis

**Inputs:**
- All previous round outputs from `${SESSION_DIR}/`
- `trust-scores.json` - Trust scores from Phase 2
- `preliminary-ruling.md` - Judge's ruling from Phase 3
- `MODE` - Deliberation mode

**Outputs:**
- `${SESSION_DIR}/round-4-synthesis.md` - Final output
- Updated `status.json` with session complete

**Cross-references:**
- Called after Phase 3 (`synod-phase3-defense.md`) or Phase 1.5 skip_debate (`synod-phase1-5-debate-gate.md`)
- Final phase - no further processing

---

## Pre-condition: Verify Phase 1 Outputs Exist

> **⛔ MANDATORY CHECK — Synthesis MUST NOT run without real external model responses.**

```bash
# Verify Phase 1 produced actual external model responses
PHASE1_DIR="${SESSION_DIR}/round-1-solver"
MISSING_RESPONSES=""

for MODEL_NAME in gemini openai claude; do
  if [[ ! -f "${PHASE1_DIR}/${MODEL_NAME}-response.md" ]] || \
     [[ ! -s "${PHASE1_DIR}/${MODEL_NAME}-response.md" ]]; then
    MISSING_RESPONSES="${MISSING_RESPONSES} ${MODEL_NAME}"
  fi
done

if [[ -n "$MISSING_RESPONSES" ]]; then
  echo "[FATAL] Phase 4 cannot proceed — Phase 1 responses missing:${MISSING_RESPONSES}" >&2
  echo "[FATAL] Go back to Phase 1 and execute actual CLI commands." >&2
  exit 1
fi
```

**If this check fails:** Return to Phase 1 (Step 1.2) and run the actual Bash commands. Do NOT generate synthesis from Claude-only analysis.

```bash
# Emit phase start (v2.1)
synod_progress '{"event":"phase_start","phase":4,"name":"Synthesis"}'
synod_progress '{"event":"model_start","model":"claude"}'
```

## Step 4.1: Compile Final Evidence

Gather from all rounds:
- Validated claims (from Critic round)
- Trust Scores (filter T < 0.5 unless all low - see `synod-error-handling.md`)
- Defense/Prosecution strongest arguments
- Judge's preliminary ruling

> **ANTI-STYLE-BIAS:** Weigh contributions by evidence and correctness only —
> never by length, confident tone, or markdown polish.

## Step 4.2: Compute Decision Metrics (v3.8 — replaces FINAL_CONFIDENCE)

> The pre-v3.8 formula `FINAL_CONFIDENCE = Σ(T·C)/Σ(T)` is **removed**: it
> laundered uncalibrated self-reported confidence through unvalidated CRIS
> weights into a single authoritative-looking percentage. The 합의 지표 block
> reports mechanical, auditable observations instead. SID confidence values
> remain display-only context.

Compute three mechanical metrics:

1. **Claim agreement (N-of-M)** — reuse the debate gate's lexical machinery on
   the Phase 1 parsed signals (zero model calls):

```bash
AGREEMENT_JSON=$(SYNOD_DEBATE_GATE=0 python3 "${TOOLS_DIR}/debate_gate.py" \
    --signals-dir "${SESSION_DIR}/round-1-solver" 2>/dev/null)
CLAIM_AGREEMENT=$(echo "$AGREEMENT_JSON" | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['signals']['claim_agreement'])" 2>/dev/null || echo "n/a")
N_SOLVERS=$(echo "$AGREEMENT_JSON" | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['n_solvers'])" 2>/dev/null || echo "0")
```

2. **Concession count** — from Phase 3 (full-debate path only): the number of
   points the defense conceded and the prosecution conceded, as recorded in
   `judge-deliberation.md`. `0/0` on the skip_debate path.

3. **Citation coverage** — when Phase 4.5 is active, the fraction of claims
   carrying `file:line` citations; otherwise report `n/a (evidence gate off)`.

Render as the 합의 지표 block used by the output templates:

```
DECISION_METRICS="합의 {CLAIM_AGREEMENT} ({N_SOLVERS}개 모델) · 양보 {defense}/{prosecution} · 인용 커버리지 {coverage}"
```

## Step 4.3: Generate Mode-Specific Output

```bash
# Load output template from config (v2.1)
OUTPUT_TEMPLATE=$(python3 -c "
import sys; sys.path.insert(0,'${TOOLS_DIR}')
from synod_config import get_template
print(get_template('$MODE'))
" 2>/dev/null)
```

### Fallback Reference (if config unavailable)

<details>
<summary>Mode-specific templates</summary>

#### Mode: review
```markdown
## 코드 리뷰 결과

### 발견된 문제
- **[ERROR]** {critical issue} - {explanation}
- **[WARNING]** {moderate issue} - {explanation}
- **[INFO]** {suggestion} - {explanation}

### 권장 사항
{prioritized list of fixes}

### 합의 지표: {DECISION_METRICS}
{brief note on agreement/disagreement between models}
```

#### Mode: design
```markdown
## 아키텍처 결정

### 권장 접근법
{description of chosen architecture}

### 트레이드오프
| Aspect | Chosen Approach | Alternative | Rationale |
|--------|-----------------|-------------|-----------|
| ... | ... | ... | ... |

### 구현 단계
1. {step}
2. {step}
...

### 합의 지표: {DECISION_METRICS}
{note on design certainty}
```

#### Mode: debug
```markdown
## 디버그 분석

### 근본 원인
{identified cause with evidence}

### 증거 체인
1. {symptom} -> {cause}
2. {trace} -> {conclusion}

### 권장 수정
{code or steps to fix}

### 예방책
{how to avoid in future}

### 합의 지표: {DECISION_METRICS}
```

#### Mode: idea
```markdown
## 아이디어 평가

### 순위별 아이디어

#### 1. {Top Idea}
**장점:** {list}
**단점:** {list}
**실현 가능성:** {high/medium/low}

#### 2. {Second Idea}
...

### 권장 사항
{which idea to pursue and why}

### 합의 지표: {DECISION_METRICS}
```

#### Mode: general
```markdown
## 답변

{comprehensive response}

### 핵심 포인트
- {point 1}
- {point 2}
- {point 3}

### 고려 사항
{nuances, edge cases, caveats}

### 합의 지표: {DECISION_METRICS}
```

</details>

## Step 4.3b: Deanonymize Before Branded Output (default ON since v3.8)

> **Default-ON — set `SYNOD_ANONYMIZE=0` to opt out.**
> When opted out, all branding behaves exactly as in v3.6 — no change.

Unless `SYNOD_ANONYMIZE=0`, call `deanonymize()` on the compiled synthesis
content **before** rendering the branded per-model claim summary in Step 4.4.
This restores real model names so the user sees familiar provider branding in
the final output even though Phases 1-3 ran anonymously.

```bash
if [[ "${SYNOD_ANONYMIZE:-1}" == "1" ]]; then
  # Re-hydrate alias map — must be the same map built in Phase 1 Step 1.0.
  if [[ -z "${SYNOD_ANON_MAP:-}" ]]; then
    echo "[Warning] SYNOD_ANONYMIZE=1 but SYNOD_ANON_MAP is unset — rebuilding map" >&2
    SYNOD_ANON_MAP=$(python3 -c "
import sys; sys.path.insert(0,'${TOOLS_DIR}')
import model_branding, json
print(json.dumps(model_branding.build_anon_map(['claude','gemini','openai'])))
")
  fi

  # Apply deanonymization to the synthesis draft before writing the final file.
  # SYNTHESIS_DRAFT must hold the accumulated markdown text up to this point.
  SYNTHESIS_DRAFT=$(python3 -c "
import sys, json; sys.path.insert(0,'${TOOLS_DIR}')
import model_branding
anon_map = json.loads('''${SYNOD_ANON_MAP}''')
text = open('/dev/stdin').read()
print(model_branding.deanonymize(text, anon_map), end='')
" <<< "$SYNTHESIS_DRAFT")

  echo "[Phase 4] deanonymize() applied — real model names restored for user-facing output" >&2
fi
```

After this step, `SYNTHESIS_DRAFT` contains real provider names (Claude,
Gemini, OpenAI) and the branded Step 4.4 summary renders correctly with the
correct glyphs (✻ / ✦ / ❀) and trust scores.

---

## Step 4.4: Include Decision Rationale

Add a collapsible section showing the deliberation process. The "모델 기여"
list MUST be populated from each agent's PRIMARY semantic_focus claim
extracted in Phase 1, rendered under the brand emoji prefix that pairs
with each model's color identity (matches the HUD in
`tools/synod_progress.py` and the BRANDING constants in
`tools/model_branding.py`).

**Per-agent claim extraction.** For each agent in `[claude, gemini, openai]`:

1. Read `${SESSION_DIR}/round-1-solver/{agent}-parsed.json`.
2. Take `semantic_focus[0]` (PRIMARY claim).
3. If the value is empty or missing, render the placeholder
   `*(no primary claim extracted)*` instead.
4. If longer than 120 characters, truncate and append `…`.

**Brand markers** (do not substitute — these match `tools/model_branding.py`).
Each marker is a monochrome unicode glyph that visually echoes the
provider's brand shape. Claude Code's markdown renderer does not apply
HTML inline color or data-URI SVG, so colored text is unavailable on
the markdown surface — the HUD (`tools/synod_progress.py`) carries the
color identity via Rich.

| Model | Markdown marker | Brand shape | Rich color | Hex (HUD truecolor) |
|---|---|---|---|---|
| Claude | `✻` (U+273B) | Anthropic asterisk | `orange3` | `#D97757` |
| Gemini | `✦` (U+2726) | Gemini sparkle | `blue` | `#4285F4` |
| OpenAI | `❀` (U+2740) | OpenAI knot/floret | `green` | `#10A37F` |

**Mandatory Dissent subsection (v3.9).** Evidenced minority positions must
never be silently dropped by consensus — in ~25% of divergent cases the
minority is right ("Minority Sentinel" arXiv:2606.29270), and LLM-judge
majority overrides tested net-negative. Populate from `contentions.json` +
`trust-scores.json`:

- Include every claim held by a SINGLE solver (trust ≥ 0.5) that was disputed
  but NOT refuted with cited evidence during Phases 2–3.
- Render each as: claim (with its ledger id if available), holder, and why it
  was not adopted.
- If no such claim exists, render `- (기각되지 않은 소수 의견 없음)` — the
  section itself is never omitted, so its absence is auditable.

**Render exactly:**

```markdown
<details>
<summary>숙의 과정</summary>

### 모델 기여
- ✻ **Claude (Validator):** {claude_primary_claim}
- ✦ **Gemini (Architect):** {gemini_primary_claim}
- ❀ **OpenAI (Explorer):** {openai_primary_claim}

### 해결된 주요 쟁점
1. {contention} -> {resolution}
2. {contention} -> {resolution}

### 소수 의견 (Dissent)
- {claim_id}: {minority_claim} — {holder}, 채택되지 않은 이유: {reason}
- (기각되지 않은 소수 의견 없음)   ← 해당 없을 때만

### 신뢰 점수
- ✻ Claude: {score} ({rating})
- ✦ Gemini: {score} ({rating})
- ❀ OpenAI: {score} ({rating})

</details>
```

## Step 4.4b: Append Debate Quality Metrics (v3.2)

After the decision rationale, append a quality metrics summary line to the final output:

```bash
# Collect metrics from all Phase 1 parsed responses
METRICS_SUMMARY=$(python3 -c "
import sys, json; sys.path.insert(0,'${TOOLS_DIR}')
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location('synod_parser', '${TOOLS_DIR}/synod-parser.py')
sp = module_from_spec(spec); spec.loader.exec_module(sp)
results = []
for f in ['gemini','openai','claude']:
    path = '${SESSION_DIR}/round-1-solver/' + f + '-parsed.json'
    try:
        with open(path) as fh: results.append(json.load(fh))
    except: pass
if results:
    agg = sp.collect_round_metrics(results)
    print(sp.format_metrics_summary(agg))
" 2>/dev/null)

# Append to synthesis output if metrics available
if [[ -n "$METRICS_SUMMARY" ]]; then
    echo "" >> "${SESSION_DIR}/round-4-synthesis.md"
    echo "$METRICS_SUMMARY" >> "${SESSION_DIR}/round-4-synthesis.md"
fi
```

## Step 4.5: Save Final State

Save `${SESSION_DIR}/round-4-synthesis.md` with full output.

Update status.json:
```json
{
  "current_round": 4,
  "round_status": {"0": "complete", "1": "complete", "2": "complete", "3": "complete", "4": "complete"},
  "status": "complete",
  "decision_metrics": {
    "claim_agreement": {CLAIM_AGREEMENT},
    "n_solvers": {N_SOLVERS},
    "concessions": {"defense": {N}, "prosecution": {N}},
    "citation_coverage": "{fraction | n/a}"
  },
  "completed_at": "{ISO_TIMESTAMP}"
}
```

```bash
# Emit synthesis complete (v2.1)
synod_progress '{"event":"model_complete","model":"claude"}'
synod_progress '{"event":"phase_end","phase":4}'

# Cleanup progress display
kill $PROGRESS_PID 2>/dev/null; rm -f "$PROGRESS_FIFO"
```

**Session Complete:** Present final synthesis to user.
