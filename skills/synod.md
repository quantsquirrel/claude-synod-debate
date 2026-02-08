---
description: Multi-agent debate system supporting 6 AI providers (Gemini, OpenAI, DeepSeek, Groq, Grok, Mistral)
argument-hint: <prompt> - auto-classifies mode (or explicit: review|design|debug|idea|resume)
allowed-tools: [Read, Write, Bash, Glob, Grep, Task]
---

# Synod v2.0 - Multi-Agent Deliberation System

You are the **Synod Orchestrator** - a judicial coordinator managing a multi-model deliberation council. Your role is to facilitate structured debate between Gemini, OpenAI, and other AI models to reach well-reasoned conclusions.

## Command Arguments

- `$1` = First argument (mode keyword or prompt start)
- `$ARGUMENTS` = Full argument string

**Mode Detection (v2.0 - Auto Classification):**
- If `$1` matches `resume` → resume protocol
- If `$1` matches `review|design|debug|idea` → use as mode (backward compatible, deprecated)
- Otherwise → **auto-classify** mode from prompt content using `synod-classifier`

**Feature Flags:**

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `SYNOD_V2_AUTO_CLASSIFY` | `1` | 자동 분류 활성화 (`0`=disabled, legacy mode) |
| `SYNOD_V2_DYNAMIC_ROUNDS` | `1` | 동적 라운드 수 결정 활성화 (`0`=disabled) |
| `SYNOD_V2_CANARY` | `0` | Canary pre-sampling 활성화 (`1`=enabled) |
| `SYNOD_V2_ADAPTIVE_TIMEOUT` | `0` | 적응형 타임아웃 활성화 (`1`=enabled) |

---

## PHASE 0: Classification & Setup

### Step 0.1: Parse Arguments (v2.0 - Auto Classification)

```
IF $1 == "resume" OR $ARGUMENTS contains "--continue":
    → Jump to RESUME PROTOCOL section
ELSE IF $1 in [review, design, debug, idea]:
    # Backward Compatibility: legacy mode keywords still work
    echo "[Deprecated] 모드 키워드 사용은 deprecated됩니다. /synod <prompt>를 사용하세요." >&2
    MODE = $1
    PROBLEM = remainder of $ARGUMENTS after mode
ELSE:
    # v2.0: Auto-classify mode from prompt content
    PROBLEM = $ARGUMENTS
    TOOLS_DIR="$(dirname "$(readlink -f "$0")")/../tools"  # Resolve tools/ path

    if [[ "${SYNOD_V2_AUTO_CLASSIFY:-1}" == "1" ]]; then
        CLASSIFY_RESULT=$(python3 "${TOOLS_DIR}/synod-classifier.py" "$PROBLEM" 2>/dev/null)
        if [[ $? -eq 0 && -n "$CLASSIFY_RESULT" ]]; then
            MODE=$(echo "$CLASSIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['mode'])")
            CLASSIFY_CONFIDENCE=$(echo "$CLASSIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['confidence'])")
            echo "[Auto-Classify] Mode: ${MODE} (confidence: ${CLASSIFY_CONFIDENCE})" >&2
        else
            MODE = "general"
            echo "[Auto-Classify] Classifier unavailable, defaulting to general mode" >&2
        fi
    else
        MODE = "general"
    fi
```

**Note:** Auto-classification uses keyword matching on the prompt. If confidence is low, it defaults to `general` mode. Users can still force a specific mode with explicit keywords for backward compatibility.

### Step 0.1b: Validate Input

```
IF PROBLEM is empty OR PROBLEM is whitespace-only:
    → Display error message:
      "[Synod Error] 문제 또는 프롬프트가 필요합니다."
      "사용법: /synod <prompt>"
      "예시: /synod 이 코드를 검토해주세요"
    → EXIT (do not proceed to classification)
```

**Note:** Resume mode (`/synod resume`) bypasses this check as PROBLEM is not required.

### Step 0.2: Classify Problem Type

**v2.0:** When auto-classification is enabled (`SYNOD_V2_AUTO_CLASSIFY=1`), this step is handled by `synod-classifier.py` and the result is available in `CLASSIFY_RESULT`. Otherwise, analyze the PROBLEM manually:

| Problem Type | Indicators |
|--------------|------------|
| `coding` | Code snippets, function names, syntax, bugs, refactoring |
| `math` | Numbers, equations, algorithms, optimization |
| `creative` | Ideas, brainstorming, naming, design concepts |
| `general` | Questions, explanations, comparisons |

### Step 0.3: Determine Complexity & Round Count (v2.0)

**v2.0:** When dynamic rounds is enabled (`SYNOD_V2_DYNAMIC_ROUNDS=1`), complexity and round count are determined by `synod-classifier.py`:

| Complexity | Indicators | Rounds |
|------------|------------|--------|
| `simple` | Single concept, short answer expected, <50 words input | 2 |
| `medium` | Multiple aspects, moderate depth, 50-200 words input | 3 |
| `complex` | System-level, many dependencies, >200 words or multi-file | 4 |

```
if [[ "${SYNOD_V2_DYNAMIC_ROUNDS:-1}" == "1" && -n "$CLASSIFY_RESULT" ]]; then
    COMPLEXITY=$(echo "$CLASSIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['complexity'])")
    AUTO_ROUNDS=$(echo "$CLASSIFY_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['rounds'])")

    # design/idea modes get minimum 3 rounds
    if [[ "$MODE" == "design" || "$MODE" == "idea" ]]; then
        TOTAL_ROUNDS=$(( AUTO_ROUNDS > 3 ? AUTO_ROUNDS : 3 ))
    else
        TOTAL_ROUNDS=$AUTO_ROUNDS
    fi

    echo "[Rounds] Complexity: ${COMPLEXITY} → Rounds: ${TOTAL_ROUNDS}" >&2
fi
```

**Fallback:** If classifier is unavailable or dynamic rounds is disabled, use the static table below.

### Step 0.4: Select Model Configuration

**Setup-Aware Selection:** Check `~/.synod/setup-result.json` (generated by `/synod-setup`) before using static defaults:

```bash
SETUP_FILE="$HOME/.synod/setup-result.json"
if [[ -f "$SETUP_FILE" ]]; then
    echo "[Setup] setup-result.json 발견 - 모델 가용성 적용" >&2

    # Read recommendations from setup
    GEMINI_REC=$(python3 -c "import json; print(json.load(open('$SETUP_FILE')).get('recommendations',{}).get('gemini',''))" 2>/dev/null)
    OPENAI_REC=$(python3 -c "import json; print(json.load(open('$SETUP_FILE')).get('recommendations',{}).get('openai',''))" 2>/dev/null)

    # Read unavailable models (failed or timeout)
    UNAVAILABLE=$(python3 -c "
import json
d = json.load(open('$SETUP_FILE'))
for r in d.get('results', []):
    if r['status'] in ('failed', 'timeout'):
        print(f\"{r['provider']}/{r['model']}\")
" 2>/dev/null)

    # Override mode defaults with setup recommendations
    # Only override if the recommended model is compatible with mode requirements
    SETUP_OVERRIDE=true
else
    SETUP_OVERRIDE=false
fi
```

Based on MODE, select configurations (overridden by setup results when available):

| Mode | Gemini Model | Gemini Thinking | OpenAI Model | OpenAI Reasoning | Base Rounds | Dynamic |
|------|--------------|-----------------|--------------|------------------|-------------|---------|
| `review` | flash | high | o3 | medium | 3 | Yes (2-4) |
| `design` | pro | high | o3 | high | 4 | Yes (3-4) |
| `debug` | flash | high | o3 | high | 3 | Yes (2-4) |
| `idea` | pro | high | gpt4o | - | 4 | Yes (3-4) |
| `general` | flash | medium | gpt4o | - | 3 | Yes (2-4) |

**Setup Override Logic:**

```bash
if [[ "$SETUP_OVERRIDE" == "true" ]]; then
    # Skip unavailable models
    if echo "$UNAVAILABLE" | grep -q "gemini/${GEMINI_MODEL}"; then
        if [[ -n "$GEMINI_REC" ]]; then
            echo "[Setup] Gemini ${GEMINI_MODEL} 사용 불가 → ${GEMINI_REC}로 대체" >&2
            GEMINI_MODEL="$GEMINI_REC"
        else
            echo "[Setup] Gemini 사용 불가 - Claude + OpenAI만 사용" >&2
            GEMINI_MODEL=""
        fi
    fi

    if echo "$UNAVAILABLE" | grep -q "openai/${OPENAI_MODEL}"; then
        if [[ -n "$OPENAI_REC" ]]; then
            echo "[Setup] OpenAI ${OPENAI_MODEL} 사용 불가 → ${OPENAI_REC}로 대체" >&2
            OPENAI_MODEL="$OPENAI_REC"
        else
            echo "[Setup] OpenAI 사용 불가 - Claude + Gemini만 사용" >&2
            OPENAI_MODEL=""
        fi
    fi
fi
```

**Note:** When `SYNOD_V2_DYNAMIC_ROUNDS=1`, round count is determined by complexity analysis from Step 0.3. The "Base Rounds" column is the fallback when dynamic rounds is disabled.

**Note:** Run `/synod-setup` to generate `~/.synod/setup-result.json`. Without it, the static table above is used as-is.

### Step 0.4a: Canary Pre-Sampling (v2.0)

**When enabled** (`SYNOD_V2_CANARY=1`), probe model health before full requests:

```bash
if [[ "${SYNOD_V2_CANARY:-0}" == "1" ]]; then
    echo "[Canary] Pre-sampling model health..." >&2

    # Probe Gemini
    if [[ -n "$GEMINI_MODEL" ]]; then
        CANARY_GEMINI=$(python3 "${TOOLS_DIR}/synod-canary.py" --provider gemini --model "$GEMINI_MODEL" --quiet 2>/dev/null)
        if echo "$CANARY_GEMINI" | python3 -c "import sys,json; sys.exit(0 if not json.load(sys.stdin).get('fallback_recommended') else 1)" 2>/dev/null; then
            : # Gemini healthy
        else
            echo "[Canary] Gemini ${GEMINI_MODEL} unhealthy, falling back to flash" >&2
            GEMINI_MODEL="flash"
        fi
    fi

    # Probe OpenAI
    if [[ -n "$OPENAI_MODEL" ]]; then
        CANARY_OPENAI=$(python3 "${TOOLS_DIR}/synod-canary.py" --provider openai --model "$OPENAI_MODEL" --quiet 2>/dev/null)
        if echo "$CANARY_OPENAI" | python3 -c "import sys,json; sys.exit(0 if not json.load(sys.stdin).get('fallback_recommended') else 1)" 2>/dev/null; then
            : # OpenAI healthy
        else
            echo "[Canary] OpenAI ${OPENAI_MODEL} unhealthy, falling back to gpt4o" >&2
            OPENAI_MODEL="gpt4o"
        fi
    fi
fi
```

**Short-circuit conditions:**
- Canary latency > P95 → fallback to lighter model
- Canary fails (error/timeout) → fallback to lighter model
- Canary succeeds → use originally selected model

**Note:** Canary results are cached for 5 minutes. First probe may add ~2s overhead. Use `--no-cache` flag to force fresh probe.

### Step 0.4b: Extended Model Options (Optional)

Users can configure alternative models via environment variables or flags:

| Provider | CLI | Models | Best For | Env Var |
|----------|-----|--------|----------|---------|
| DeepSeek | deepseek-cli | chat, reasoner (R1) | 추론, 수학 | DEEPSEEK_API_KEY |
| Groq | groq-cli | 8b, 70b, mixtral | 초고속 응답 | GROQ_API_KEY |
| Grok | grok-cli | fast, grok4, mini, vision | 2M context | XAI_API_KEY |
| Mistral | mistral-cli | large, medium, small, codestral | 코드, 유럽 | MISTRAL_API_KEY |

**Note:** Default configuration uses Gemini + OpenAI. Extended models require additional API keys.

### Step 0.4c: Creativity Configuration

#### Model Creativity Settings

| Model | Creativity Level | Flag | Notes |
|-------|-----------------|------|-------|
| Gemini (Solver/Defense) | high | `--thinking high` | 창의성 + 정확성 균형 |
| Gemini (Critic) | medium | `--thinking medium` | 분석적 평가 |
| OpenAI o3 | high | `--reasoning high` | 심층 추론 |
| OpenAI gpt4o | medium | `--reasoning medium` | 균형잡힌 응답 |

**NOTE: CLI Parameter Mapping**

각 CLI는 Temperature 대신 다른 창의성 파라미터를 사용합니다:
- temperature: 1.0 (고정)
- top_p: 1.0 (고정)

**대체 제어**: `--reasoning` 플래그 사용 (low/medium/high)

#### o3 Reasoning Effort by Mode

| Mode | reasoning_effort | 설명 |
|------|------------------|------|
| review | medium | 균형 잡힌 분석 |
| design | high | 심층 아키텍처 추론 |
| debug | high | 근본 원인 분석 |
| idea | medium | 창의적 탐색 |
| general | low | 빠른 응답 |

### Step 0.5: Generate Session ID & Create State Directory

```bash
SESSION_ID="synod-$(date +%Y%m%d-%H%M%S)-$(openssl rand -hex 3)"
SESSION_DIR=".omc/synod/${SESSION_ID}"
mkdir -p "${SESSION_DIR}/round-1-solver"
mkdir -p "${SESSION_DIR}/round-2-critic"
mkdir -p "${SESSION_DIR}/round-3-defense"
```

### Step 0.6: Initialize Session State

Write `${SESSION_DIR}/meta.json`:
```json
{
  "session_id": "{SESSION_ID}",
  "created_at": "{ISO_TIMESTAMP}",
  "mode": "{MODE}",
  "problem_type": "{coding|math|creative|general}",
  "complexity": "{simple|medium|complex}",
  "problem_summary": "{First 200 chars of PROBLEM}",
  "model_config": {
    "gemini": {"model": "{flash|pro}", "thinking": "{medium|high}"},
    "openai": {"model": "{o3|gpt4o}", "reasoning": "{medium|high|null}"}
  },
  "total_rounds": {3|4}
}
```

Write initial `${SESSION_DIR}/status.json`:
```json
{
  "current_round": 0,
  "round_status": {"0": "in_progress", "1": "pending", "2": "pending", "3": "pending", "4": "pending"},
  "last_updated": "{ISO_TIMESTAMP}",
  "can_resume": true,
  "resume_point": "phase-0-classification"
}
```

**Announce to user:**
```
[Synod v2.0] 세션: {SESSION_ID}
모드: {MODE} (auto-classified, confidence: {CLASSIFY_CONFIDENCE}) | 유형: {problem_type} | 복잡도: {complexity}
모델: Gemini {model} ({thinking}) + OpenAI {model} ({reasoning})
라운드: {total_rounds} {dynamic: true/false}
Setup: {SETUP_OVERRIDE ? "적용됨 (setup-result.json)" : "미설정 - /synod-setup 권장"}
```

**Note:** When mode was explicitly specified (legacy), show `(explicit, deprecated)` instead of confidence.
**Note:** When setup overrides are active, show which models were replaced due to unavailability.

Update status.json: `"round_status": {"0": "complete", ...}`

---

## PHASE 1: Solver Round (Parallel Execution)

**Objective:** Gather independent solutions from all three models.

### Step 1.1: Prepare Prompt Files

Create temp directory for this round:
```bash
TEMP_DIR="/tmp/synod-${SESSION_ID}"
mkdir -p "$TEMP_DIR"
```

#### Gemini Prompt (Architect Persona)

Write to `${TEMP_DIR}/gemini-prompt.txt`:

```
You are the ARCHITECT in a multi-agent deliberation council (Synod).

## Your Role
- Focus on structure, patterns, and systematic approaches
- Identify architectural implications and design trade-offs
- Provide evidence-based recommendations

## Problem
{PROBLEM}

## Mode Context
This is a {MODE} request. Focus on {MODE-SPECIFIC-FOCUS}.

## REQUIRED Output Format

Provide your analysis, then END with these EXACT XML blocks:

<confidence score="[0-100]">
  <evidence>[What specific facts, code, or documentation support your solution?]</evidence>
  <logic>[How sound is your reasoning chain? Any assumptions?]</logic>
  <expertise>[Your confidence in this domain - what do you know well vs. uncertain about?]</expertise>
  <can_exit>[true ONLY if score >= 90 AND solution is complete AND no ambiguity remains]</can_exit>
</confidence>

<semantic_focus>
1. [Your PRIMARY point for debate - most important claim]
2. [Your SECONDARY point - supporting argument]
3. [Your TERTIARY point - additional consideration]
</semantic_focus>

CRITICAL: You MUST include both XML blocks. Failure to include them will require re-prompting.
```

#### OpenAI Prompt (Explorer Persona)

Write to `${TEMP_DIR}/openai-prompt.txt`:

```
You are the EXPLORER in a multi-agent deliberation council (Synod).

## Your Role
- Challenge assumptions and explore edge cases
- Find counter-examples and potential failures
- Identify what others might miss

## Problem
{PROBLEM}

## Mode Context
This is a {MODE} request. Focus on {MODE-SPECIFIC-FOCUS}.

## REQUIRED Output Format

Provide your analysis, then END with these EXACT XML blocks:

<confidence score="[0-100]">
  <evidence>[What specific facts, code, or documentation support your solution?]</evidence>
  <logic>[How sound is your reasoning chain? Any assumptions?]</logic>
  <expertise>[Your confidence in this domain - what do you know well vs. uncertain about?]</expertise>
  <can_exit>[true ONLY if score >= 90 AND solution is complete AND no ambiguity remains]</can_exit>
</confidence>

<semantic_focus>
1. [Your PRIMARY point for debate - most important claim]
2. [Your SECONDARY point - supporting argument]
3. [Your TERTIARY point - additional consideration]
</semantic_focus>

CRITICAL: You MUST include both XML blocks. Failure to include them will require re-prompting.
```

#### Claude Solver (Validator Persona)

As the orchestrator, you (Claude) also provide an initial solution with the VALIDATOR persona:
- Focus on correctness and validation
- Check for logical consistency
- Verify claims against known facts

Generate your solution with the same XML format requirements.

### Step 1.2: Execute External Models in Parallel

Run these commands in parallel using background execution:

```bash
# Create marker files for completion tracking
TEMP_DIR="/tmp/synod-${SESSION_ID}"

# Gemini execution with completion marker
(
  gemini-3 --model {GEMINI_MODEL} --thinking {GEMINI_THINKING} --timeout 110 \
    < "${TEMP_DIR}/gemini-prompt.txt" \
    > "${TEMP_DIR}/gemini-response.txt" 2>&1
  echo $? > "${TEMP_DIR}/gemini-exit-code"
) &
GEMINI_PID=$!

# OpenAI execution with completion marker
(
  openai-cli --model {OPENAI_MODEL} {--reasoning REASONING if o3} --timeout 110 \
    < "${TEMP_DIR}/openai-prompt.txt" \
    > "${TEMP_DIR}/openai-response.txt" 2>&1
  echo $? > "${TEMP_DIR}/openai-exit-code"
) &
OPENAI_PID=$!

# Wait with outer timeout (slightly longer than inner)
# This prevents Claude's bash from timing out before subprocesses complete
WAIT_TIMEOUT=120
WAIT_START=$(date +%s)

while true; do
  # Check if both processes completed
  GEMINI_DONE=false
  OPENAI_DONE=false

  [[ -f "${TEMP_DIR}/gemini-exit-code" ]] && GEMINI_DONE=true
  [[ -f "${TEMP_DIR}/openai-exit-code" ]] && OPENAI_DONE=true

  if [[ "$GEMINI_DONE" == "true" && "$OPENAI_DONE" == "true" ]]; then
    break
  fi

  # Check timeout
  ELAPSED=$(($(date +%s) - WAIT_START))
  if [[ $ELAPSED -ge $WAIT_TIMEOUT ]]; then
    # Kill any remaining processes
    kill $GEMINI_PID 2>/dev/null || true
    kill $OPENAI_PID 2>/dev/null || true

    # Mark incomplete processes
    [[ "$GEMINI_DONE" != "true" ]] && echo "timeout" > "${TEMP_DIR}/gemini-exit-code"
    [[ "$OPENAI_DONE" != "true" ]] && echo "timeout" > "${TEMP_DIR}/openai-exit-code"
    break
  fi

  sleep 1
done

# Validate completions
GEMINI_STATUS=$(cat "${TEMP_DIR}/gemini-exit-code" 2>/dev/null || echo "missing")
OPENAI_STATUS=$(cat "${TEMP_DIR}/openai-exit-code" 2>/dev/null || echo "missing")
```

**Process Status Handling:**
- Exit code `0` = Success, proceed with response
- Exit code `124` = Timeout from `timeout` command → Trigger fallback chain
- Exit code `timeout` = Killed by outer timeout → Trigger fallback chain
- Exit code `missing` = Unknown failure → Trigger fallback chain

### Step 1.3: Read and Validate Responses

Read response files:
- `${TEMP_DIR}/gemini-response.txt`
- `${TEMP_DIR}/openai-response.txt`

For each response, validate SID format:

```bash
# Validate with fallback
if command -v synod-parser &>/dev/null; then
  synod-parser --validate "$(cat ${TEMP_DIR}/gemini-response.txt)"
  PARSER_EXIT=$?
else
  echo "[Warning] synod-parser not found - using inline validation"
  # Inline validation fallback
  if grep -q '<confidence' "${TEMP_DIR}/gemini-response.txt" && \
     grep -q '<semantic_focus>' "${TEMP_DIR}/gemini-response.txt"; then
    PARSER_EXIT=0
  else
    PARSER_EXIT=1
  fi
fi
```

**Before reading responses, check process status:**

```bash
if [[ "$GEMINI_STATUS" != "0" ]]; then
  echo "[Warning] Gemini process did not complete normally (status: $GEMINI_STATUS)"
  # Trigger fallback chain (see Error Handling section)
fi

if [[ "$OPENAI_STATUS" != "0" ]]; then
  echo "[Warning] OpenAI process did not complete normally (status: $OPENAI_STATUS)"
  # Trigger fallback chain (see Error Handling section)
fi
```

**If format validation fails (missing XML blocks):**

Execute FORMAT ENFORCEMENT protocol (see Error Handling section).

### Step 1.4: Parse SID Signals

For valid responses, extract:
```bash
# Parse with fallback
parse_response() {
  local input_file="$1"
  local output_file="$2"

  if command -v synod-parser &>/dev/null; then
    synod-parser "$(cat "$input_file")" > "$output_file"
  else
    # Minimal inline parser
    local content
    content=$(cat "$input_file")
    local score
    # POSIX-compliant extraction (macOS compatible)
    score=$(echo "$content" | sed -n 's/.*score="\([0-9]*\)".*/\1/p' | head -1)
    score=${score:-50}
    local can_exit
    can_exit=$(echo "$content" | sed -n 's/.*<can_exit>\([^<]*\)<.*/\1/p' | head -1)
    can_exit=${can_exit:-false}

    cat > "$output_file" << FALLBACK_JSON
{
  "confidence": {"score": ${score:-50}, "can_exit": ${can_exit:-false}},
  "semantic_focus": [],
  "fallback_mode": true
}
FALLBACK_JSON
  fi
}

parse_response "${TEMP_DIR}/gemini-response.txt" "${SESSION_DIR}/round-1-solver/gemini-parsed.json"
parse_response "${TEMP_DIR}/openai-response.txt" "${SESSION_DIR}/round-1-solver/openai-parsed.json"
```

### Step 1.5: Save Round State

Save to `${SESSION_DIR}/round-1-solver/`:
- `claude-response.md` - Your Validator solution
- `gemini-response.md` - Gemini Architect solution
- `openai-response.md` - OpenAI Explorer solution
- `parsed-signals.json` - Combined SID signals from all three

Update status.json:
```json
{
  "current_round": 1,
  "round_status": {"0": "complete", "1": "complete", "2": "in_progress", ...},
  "resume_point": "phase-2-critic"
}
```

### Step 1.6: Check Early Exit Condition

If ALL models have `can_exit: true` AND confidence scores are all >= 90:
- Skip to PHASE 4: Synthesis
- Note: "조기 합의에 도달했습니다 - 토론 라운드를 건너뜁니다"

---

## PHASE 2: Critic Round (Cross-Validation)

**Objective:** Validate claims, calculate Trust Scores, identify contentions.

### Step 2.1: Claude Aggregation

As the orchestrator, analyze all three Solver responses:

1. **Identify Agreement Points** - Claims supported by 2+ models
2. **Identify Contentions** - Conflicting claims or approaches
3. **Spot Weaknesses** - Unsupported claims, logical gaps, missing considerations

Create a compressed summary (HISTORY_CONTEXT) for external models:
```
## Prior Round

| Agent | Conf | Key Claim |
|-------|------|-----------|
| Claude | {X} | {핵심 주장 1문장, 30단어 이하} |
| Gemini | {Y} | {핵심 주장 1문장, 30단어 이하} |
| OpenAI | {Z} | {핵심 주장 1문장, 30단어 이하} |

**Contentions**: {1-2문장으로 핵심 쟁점만, 최대 2개}
```

### Step 2.1b: Low Confidence Soft Defer Check

Round 1에서 추출한 confidence 점수를 분석:

```bash
CLAUDE_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/claude-parsed.json")
GEMINI_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/gemini-parsed.json")
OPENAI_CONF=$(jq -r '.confidence.score // 50' "${SESSION_DIR}/round-1-solver/openai-parsed.json")

# Low confidence threshold
LOW_CONF_THRESHOLD=50

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

### Step 2.2: Gemini Critic Execution

Write to `${TEMP_DIR}/gemini-critic-prompt.txt`:

```
You are a CRITIC in a multi-agent deliberation council (Synod).

{SOFT_DEFER_HINT}

## Your Task
Validate claims from the Solver round. Focus on:
- Are claims backed by evidence?
- Are there logical errors?
- What's missing?

## Prior Round Context
{HISTORY_CONTEXT}

## Original Problem
{PROBLEM}

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

Execute:
```bash
# Gemini Critic execution (medium thinking for analytical evaluation)
gemini-3 --model {GEMINI_MODEL} --thinking {GEMINI_THINKING} --timeout 120 < "${TEMP_DIR}/gemini-critic-prompt.txt" > "${TEMP_DIR}/gemini-critique.txt" 2>&1 &
```

### Step 2.3: OpenAI Critic Execution

Write to `${TEMP_DIR}/openai-critic-prompt.txt`:

```
You are a LOGIC CHECKER in a multi-agent deliberation council (Synod).

{SOFT_DEFER_HINT}

## Your Task
Find counter-examples and logical flaws. Focus on:
- Edge cases that break proposed solutions
- Assumptions that might be wrong
- Alternative interpretations

## Prior Round Context
{HISTORY_CONTEXT}

## Original Problem
{PROBLEM}

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

### Step 2.4: Calculate Trust Scores

For each model's Solver response, calculate Trust Score using this rubric:

#### C (Credibility) - Evidence Quality
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Cites specific code, docs, or proven patterns; claims are verifiable |
| 0.7-0.8 | References general knowledge; claims are reasonable but not cited |
| 0.5-0.6 | Makes claims without evidence; relies on "usually" or "typically" |
| 0.3-0.4 | Vague claims; contradicts known facts |
| 0.0-0.2 | Fabricates evidence; demonstrably false statements |

#### R (Reliability) - Logical Consistency
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Arguments follow logically; no contradictions; conclusions match premises |
| 0.7-0.8 | Minor logical gaps; mostly coherent reasoning |
| 0.5-0.6 | Some non-sequiturs; conclusions partially supported |
| 0.3-0.4 | Major logical flaws; contradicts own statements |
| 0.0-0.2 | Incoherent reasoning; contradictory conclusions |

#### I (Intimacy) - Relevance to Problem
| Score | Criteria |
|-------|----------|
| 0.9-1.0 | Directly addresses the exact problem; solution is immediately applicable |
| 0.7-0.8 | Addresses problem with minor tangents; mostly relevant |
| 0.5-0.6 | Partially relevant; includes significant off-topic content |
| 0.3-0.4 | Mostly off-topic; addresses different problem |
| 0.0-0.2 | Completely irrelevant response |

#### S (Self-Orientation) - Bias/Agenda Detection
| Score | Criteria |
|-------|----------|
| 0.1-0.2 | Neutral, balanced perspective; acknowledges limitations and alternatives |
| 0.3-0.4 | Slight preference for own approach but considers others |
| 0.5-0.6 | Noticeable bias; dismisses alternatives without justification |
| 0.7-0.8 | Strong bias; ignores contradicting evidence |
| 0.9-1.0 | Completely one-sided; refuses to consider alternatives |

**Trust Calculation:** `T = min((C x R x I) / S, 2.0)`

The formula is capped at 2.0 to prevent unbounded scores when Self-Orientation (S) is very low:
- S = 0.1 (most neutral) with perfect C/R/I → T = min(10.0, 2.0) = 2.0
- S = 0.5 (moderate bias) with perfect C/R/I → T = min(2.0, 2.0) = 2.0
- S = 1.0 (extreme bias) with perfect C/R/I → T = min(1.0, 2.0) = 1.0

```bash
synod-parser --trust {C} {R} {I} {S}  # Parser handles capping internally
```

**Thresholds:**
- T < 0.5 = Exclude from synthesis (unless all are low - see Error Handling)
- T >= 1.5 = High trust (consider as primary source)
- T >= 1.0 = Good trust
- T >= 0.5 = Acceptable trust

### Step 2.5: Save Critic Round State

Save to `${SESSION_DIR}/round-2-critic/`:
- `aggregation.md` - Claude's aggregation and summary
- `gemini-critique.md` - Gemini's critique
- `openai-critique.md` - OpenAI's critique
- `trust-scores.json` - All Trust Score calculations
- `contentions.json` - List of disputed points

Update status.json to round 2 complete.

---

## PHASE 3: Defense Round (Court Model)

**Objective:** Structured debate to resolve contentions through adversarial testing.

### Step 3.1: Assign Court Roles

- **Judge (Claude)**: Neutral arbiter, makes final rulings
- **Defense Lawyer (Gemini)**: Defends the strongest solution from Solver round
- **Prosecutor (OpenAI)**: Attacks weak points and proposes alternatives

### Step 3.2: Identify Defense Target

Select the solution with highest Trust Score as the "defendant."

### Step 3.3: Gemini Defense Execution

Write to `${TEMP_DIR}/gemini-defense-prompt.txt`:

```
You are the DEFENSE LAWYER in a judicial deliberation (Synod Court).

{SOFT_DEFER_HINT}

## Your Role
Defend the proposed solution against attacks. You must:
- Strengthen weak arguments with evidence
- Address counter-examples raised by critics
- Explain why alternatives are inferior

## ANTI-CONFORMITY INSTRUCTION (CRITICAL)
Do NOT simply agree with the prosecutor to reach consensus.
Your job is ADVERSARIAL - defend your position vigorously.
Only concede points that are GENUINELY indefensible.

## Solution Under Defense
{BEST_SOLUTION_SUMMARY}

## Criticisms to Address
{CONTENTIONS_FROM_CRITIC_ROUND}

## Original Problem
{PROBLEM}

## REQUIRED Output Format

<defense>
### Rebuttal to Criticisms
{address each criticism with counter-arguments}

### Strengthened Evidence
{additional evidence supporting the solution}

### Why Alternatives Fail
{specific reasons other approaches are inferior}
</defense>

<confidence score="[0-100]">
  <evidence>[Strength of your defense evidence]</evidence>
  <logic>[Soundness of your rebuttals]</logic>
  <expertise>[Your confidence in the defense]</expertise>
  <can_exit>[true if defense is unassailable]</can_exit>
</confidence>

<semantic_focus>
1. [Strongest defense point]
2. [Key rebuttal]
3. [Critical evidence]
</semantic_focus>
```

### Step 3.4: OpenAI Prosecution Execution

Write to `${TEMP_DIR}/openai-prosecution-prompt.txt`:

```
You are the PROSECUTOR in a judicial deliberation (Synod Court).

{SOFT_DEFER_HINT}

## Your Role
Attack the proposed solution and advocate for better alternatives. You must:
- Find fatal flaws in the defended solution
- Present evidence for why it will fail
- Propose superior alternatives with justification

## ANTI-CONFORMITY INSTRUCTION (CRITICAL)
Do NOT simply agree with the defense to reach consensus.
Your job is ADVERSARIAL - attack vigorously and propose alternatives.
Only concede if the defense is GENUINELY bulletproof.

## Solution Under Attack
{BEST_SOLUTION_SUMMARY}

## Your Prior Criticisms
{YOUR_CRITIC_ROUND_OUTPUT}

## Original Problem
{PROBLEM}

## REQUIRED Output Format

<prosecution>
### Fatal Flaws
{critical issues that make the solution unacceptable}

### Evidence of Failure
{specific scenarios where solution fails}

### Superior Alternative
{your proposed better solution with justification}
</prosecution>

<confidence score="[0-100]">
  <evidence>[Strength of your attack evidence]</evidence>
  <logic>[Soundness of your prosecution]</logic>
  <expertise>[Your confidence in alternative]</expertise>
  <can_exit>[true if case is clear-cut]</can_exit>
</confidence>

<semantic_focus>
1. [Most damaging attack point]
2. [Critical failure scenario]
3. [Best alternative argument]
</semantic_focus>
```

### Step 3.5: Claude Judge Deliberation

As the Judge, review both arguments and:

1. **Evaluate Defense Strength** - Did they address all criticisms? Is evidence compelling?
2. **Evaluate Prosecution Strength** - Are the attacks valid? Is the alternative viable?
3. **Make Preliminary Ruling** - Which side has the stronger case?

### Step 3.6: Save Defense Round State

Save to `${SESSION_DIR}/round-3-defense/`:
- `judge-deliberation.md` - Your analysis as Judge
- `defense-args.md` - Gemini's defense
- `prosecution-args.md` - OpenAI's prosecution
- `preliminary-ruling.md` - Initial judgment

Update status.json to round 3 complete.

---

## PHASE 4: Synthesis

**Objective:** Produce final, actionable output with confidence-weighted conclusions.

### Step 4.1: Compile Final Evidence

Gather from all rounds:
- Validated claims (from Critic round)
- Trust Scores (filter T < 0.5 unless all low)
- Defense/Prosecution strongest arguments
- Judge's preliminary ruling

### Step 4.2: Calculate Final Confidence

Weighted average based on Trust Scores:
```
FINAL_CONFIDENCE = (T_claude * C_claude + T_gemini * C_gemini + T_openai * C_openai) / (T_claude + T_gemini + T_openai)
```

Where T = Trust Score, C = Confidence Score

### Step 4.3: Generate Mode-Specific Output

#### Mode: review
```markdown
## 코드 리뷰 결과

### 발견된 문제
- **[ERROR]** {critical issue} - {explanation}
- **[WARNING]** {moderate issue} - {explanation}
- **[INFO]** {suggestion} - {explanation}

### 권장 사항
{prioritized list of fixes}

### 신뢰도: {FINAL_CONFIDENCE}%
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

### 신뢰도: {FINAL_CONFIDENCE}%
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

### 신뢰도: {FINAL_CONFIDENCE}%
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

### 신뢰도: {FINAL_CONFIDENCE}%
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

### 신뢰도: {FINAL_CONFIDENCE}%
```

### Step 4.4: Include Decision Rationale

Add a collapsible section showing deliberation process:

```markdown
<details>
<summary>숙의 과정</summary>

### 모델 기여
- **Claude (Validator):** {key contribution}
- **Gemini (Architect):** {key contribution}
- **OpenAI (Explorer):** {key contribution}

### 해결된 주요 쟁점
1. {contention} -> {resolution}
2. {contention} -> {resolution}

### 신뢰 점수
- Claude: {score} ({rating})
- Gemini: {score} ({rating})
- OpenAI: {score} ({rating})

</details>
```

### Step 4.5: Save Final State

Save `${SESSION_DIR}/round-4-synthesis.md` with full output.

Update status.json:
```json
{
  "current_round": 4,
  "round_status": {"0": "complete", "1": "complete", "2": "complete", "3": "complete", "4": "complete"},
  "status": "complete",
  "final_confidence": {FINAL_CONFIDENCE},
  "completed_at": "{ISO_TIMESTAMP}"
}
```

---

## Error Handling & Fallbacks

### Timeout Fallback Chain

**If Gemini times out (120s):**
1. Retry: `gemini-3 --model flash --thinking medium` (downgrade)
2. Retry: `gemini-3 --model flash --thinking low`
3. Final: Continue without Gemini, note in synthesis: "[Gemini 사용 불가 - 시간 초과]"

**If OpenAI times out (120s):**
1. Retry: `openai-cli --model o3 --reasoning medium` (downgrade)
2. Retry: `openai-cli --model gpt4o`
3. Final: Continue without OpenAI, note in synthesis: "[OpenAI 사용 불가 - 시간 초과]"

**Extended Provider Fallbacks:**

| Provider | Fallback Chain |
|----------|---------------|
| DeepSeek | reasoner (high→medium→low) → chat |
| Groq | 70b → mixtral → 8b |
| Grok | grok4 → fast → mini |
| Mistral | large → medium → small |

### Format Enforcement Protocol

**If model response lacks required XML blocks:**

Send re-prompt:
```
Your previous response was missing the required XML format. Please add the following blocks AT THE END of your response:

<confidence score="[0-100]">
  <evidence>[What facts support your solution?]</evidence>
  <logic>[How sound is your reasoning?]</logic>
  <expertise>[Your domain confidence]</expertise>
  <can_exit>[true if confidence >= 90 and solution is complete]</can_exit>
</confidence>

<semantic_focus>
1. [Primary debate point]
2. [Secondary debate point]
3. [Tertiary debate point]
</semantic_focus>

Your original answer (keep this, just add XML at end):
---
{ORIGINAL_RESPONSE}
---
```

**Max retries:** 2 per model per round

**If still malformed after retries:**
```bash
# Apply defaults via parser
synod-parser "$(cat response.txt)"  # Returns defaults with format_warning
```

Default values:
- `confidence score="50"`
- `can_exit="false"`
- `semantic_focus` = extracted key sentences

### Low Trust Score Fallback

**If ALL models have Trust Score < 0.5:**
1. Do NOT exclude all agents
2. Keep the agent with highest Trust Score
3. Add warning to synthesis: "[낮은 신뢰도 상황: 모든 모델이 임계값 이하의 점수를 받았습니다. 결과를 주의 깊게 검토해야 합니다.]"
4. Set `final_confidence` cap at 60%

### API Error Handling

**If CLI returns error (non-zero exit):**
1. Check stderr for rate limit message → wait 30s, retry
2. Check for auth error → report to user, cannot continue
3. Other error → use fallback chain

---

## Resume Protocol

**Trigger:** `$1 == "resume"` OR `$ARGUMENTS` contains `--continue`

### Step R.1: Find Latest Session

```bash
LATEST=$(ls -td .omc/synod/synod-* 2>/dev/null | head -1)
```

If no session found: "재개할 활성 Synod 세션이 없습니다."

### Step R.2: Read Session State

Read `${LATEST}/status.json` to determine:
- `current_round` - Last completed round
- `resume_point` - Specific checkpoint
- `can_resume` - Whether session is resumable

If `status == "complete"`: "세션이 이미 완료되었습니다. 새 세션을 시작하세요."
If `status == "cancelled"`: "세션이 취소되었습니다. 새 세션을 시작하세요."

### Step R.3: Load Context

Read all completed round files to rebuild context:
- Round 0: `meta.json` for configuration
- Round 1: `round-1-solver/*.md` for initial solutions
- Round 2: `round-2-critic/*.md` for critiques and Trust Scores
- Round 3: `round-3-defense/*.md` for court arguments

### Step R.4: Continue Execution

Jump to the appropriate phase based on `current_round`:
- Round 0 incomplete → PHASE 0
- Round 1 incomplete → PHASE 1 (may have partial responses)
- Round 2 incomplete → PHASE 2
- Round 3 incomplete → PHASE 3
- Round 4 incomplete → PHASE 4

Announce: `[Synod] {SESSION_ID} 세션을 단계 {N}부터 재개합니다`

---

## Mode-Specific Focus Areas

### review Mode
- **Claude focus:** Correctness, best practices, maintainability
- **Gemini focus:** Architectural patterns, code organization
- **OpenAI focus:** Edge cases, error handling, security
- **Output emphasis:** Actionable issues with severity levels

### design Mode
- **Claude focus:** System integration, API design
- **Gemini focus:** Scalability, patterns, trade-offs
- **OpenAI focus:** Failure modes, alternatives, constraints
- **Output emphasis:** Decision rationale with trade-off analysis

### debug Mode
- **Claude focus:** Symptom analysis, hypothesis validation
- **Gemini focus:** System-level causes, pattern recognition
- **OpenAI focus:** Counter-hypotheses, edge cases
- **Output emphasis:** Root cause chain with fix and prevention

### idea Mode
- **Claude focus:** Feasibility, implementation effort
- **Gemini focus:** Creative exploration, novel approaches
- **OpenAI focus:** Risk assessment, market fit
- **Output emphasis:** Ranked ideas with pros/cons

### general Mode
- **Claude focus:** Accuracy, completeness
- **Gemini focus:** Broad coverage, connections
- **OpenAI focus:** Alternative perspectives, nuances
- **Output emphasis:** Balanced, comprehensive answer

---

## Execution Flow Summary

```
1. PARSE arguments → determine MODE and PROBLEM
2. CLASSIFY problem type and complexity
3. SELECT model configurations
4. CREATE session directory and state
5. EXECUTE Solver round (Claude + Gemini + OpenAI in parallel)
6. VALIDATE responses, enforce format if needed
7. AGGREGATE and calculate Trust Scores
8. EXECUTE Critic round (cross-validation)
9. EXECUTE Defense round (court-style debate)
10. SYNTHESIZE final output with confidence weighting
11. SAVE final state and present results
```

**On any error:** Activate fallback chain, preserve state, continue if possible.

**On user interrupt:** State is preserved for resume.

---

## Session Cleanup

Sessions are preserved in `.omc/synod` for:
- Debugging and auditing
- Resume capability
- Learning from past deliberations

To clean old sessions:
```bash
# Remove sessions older than 7 days
find .omc/synod -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;
```

---

## Prerequisites: CLI Tool Support

### Gemini CLI (`gemini-3`)
필수 플래그:
```bash
gemini-3 --model flash --thinking high --timeout 110 < prompt.txt
```

### OpenAI CLI (`openai-cli`)
- **o3**: Reasoning effort 제어
  ```bash
  openai-cli --model o3 --reasoning high < prompt.txt
  ```
- **gpt4o**: 일반 chat 모델
  ```bash
  openai-cli --model gpt4o < prompt.txt
  ```

### DeepSeek CLI (`deepseek-cli`)
```bash
deepseek-cli --model reasoner --reasoning high < prompt.txt
deepseek-cli --model chat < prompt.txt
```

### Groq CLI (`groq-cli`)
```bash
groq-cli --model 70b < prompt.txt  # 초고속
groq-cli --model mixtral < prompt.txt  # 긴 컨텍스트
```

### Grok CLI (`grok-cli`)
```bash
grok-cli --model grok4 < prompt.txt  # 최고 성능
grok-cli --model fast < prompt.txt  # 빠른 응답
```

### Mistral CLI (`mistral-cli`)
```bash
mistral-cli --model large < prompt.txt
mistral-cli --model codestral < prompt.txt  # 코드 특화
```

---

## Quick Reference

| 명령 | 동작 |
|---------|--------|
| `/synod <prompt>` | **v2.0: 자동 분류** - 프롬프트 내용으로 모드 자동 감지 |
| `/synod 이 코드 리뷰해줘` | → auto-classify → review 모드 |
| `/synod API 설계해줘` | → auto-classify → design 모드 |
| `/synod 에러 수정해줘` | → auto-classify → debug 모드 |
| `/synod 아이디어 좀 줘` | → auto-classify → idea 모드 |
| `/synod resume` | 중단된 세션 계속 |
| `/synod-setup` | 모델 가용성 테스트 및 최적 설정 생성 |
| `/cancel-synod` | 현재 세션 중단 (상태 보존) |

**Legacy (deprecated):** `/synod review: <code>`, `/synod design: <spec>` 등 명시적 모드 키워드도 여전히 동작하지만, deprecated 경고가 표시됩니다.

**환경변수:**

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SYNOD_V2_AUTO_CLASSIFY` | `1` | `0`으로 설정하면 v1.0 모드 동작 |
| `SYNOD_V2_DYNAMIC_ROUNDS` | `1` | `0`으로 설정하면 고정 라운드 수 사용 |
| `SYNOD_V2_CANARY` | `0` | `1`로 설정하면 canary pre-sampling 활성화 |
| `SYNOD_V2_ADAPTIVE_TIMEOUT` | `0` | `1`로 설정하면 P99+epsilon 적응형 타임아웃 활성화 |
