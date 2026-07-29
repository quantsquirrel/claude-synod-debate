# Synod Module: Phase 3 - Defense Round

**Inputs:**
- `${SESSION_DIR}/round-2-critic/` - Critic round outputs
- `${SESSION_DIR}/round-1-solver/` - Original solutions
- `PROBLEM` - Original problem statement
- `GEMINI_CLI`, `OPENAI_CLI` - CLI executable paths
- `trust-scores.json` - Trust scores from critic round

**Outputs:**
- `${SESSION_DIR}/round-3-defense/judge-deliberation.md`
- `${SESSION_DIR}/round-3-defense/defense-args.md`
- `${SESSION_DIR}/round-3-defense/prosecution-args.md`
- `${SESSION_DIR}/round-3-defense/preliminary-ruling.md`
- Updated `status.json` with round 3 complete

**Cross-references:**
- Called after Phase 2 (`synod-phase2-critic.md`)
- Outputs consumed by Phase 4 (`synod-phase4-synthesis.md`)
- Timeout failures trigger `synod-error-handling.md`

---

```bash
# Emit phase start (v2.1)
synod_progress '{"event":"phase_start","phase":3,"name":"Defense Round"}'

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

## Step 3.0: Deliberation Anonymization (default ON since v3.8)

> **Default-ON — set `SYNOD_ANONYMIZE=0` to opt out.**
> When opted out, all branding and labelling behaves exactly as before — no change.

Unless `SYNOD_ANONYMIZE=0`:

```bash
if [[ "${SYNOD_ANONYMIZE:-1}" == "1" ]]; then
  # Re-hydrate the alias map (originally built in Phase 1 Step 1.0).
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

  echo "[Phase 3] Anonymization ON — court references use aliases: claude=$ALIAS_CLAUDE gemini=$ALIAS_GEMINI openai=$ALIAS_OPENAI" >&2
else
  ALIAS_CLAUDE="Claude"
  ALIAS_GEMINI="Gemini"
  ALIAS_OPENAI="OpenAI"
fi
```

**When anonymization is active**, all court-facing context must use aliases:

- The defense prompt (`defense-prompt.txt`) and prosecution prompt
  (`prosecution-prompt.txt`) must reference the **defendant solution**
  by its alias (e.g. `$ALIAS_GEMINI proposed …`), never by provider name.
- `BEST_SOLUTION_SUMMARY` and `CONTENTIONS_FROM_CRITIC_ROUND` inserted into
  prompts must have real model names replaced with their aliases.
- Saved artefacts (`defense-args.md`, `prosecution-args.md`,
  `preliminary-ruling.md`, `judge-deliberation.md`) are written with aliases
  so Phase 4 receives anonymized input and calls `deanonymize()` only once at
  display time.

---

## Step 3.1: Identify Defense Target

Select the solution with highest Trust Score as the "defendant."

## Step 3.2: Assign Court Roles (authorship-aware since v3.8)

> Pre-v3.8 the roles were hardcoded (Gemini=Defense, OpenAI=Prosecutor), which
> let a provider end up prosecuting its own winning solution — a role confound
> that made Phase 3 outcomes partly an artifact of costume assignment. Roles
> are now derived from who AUTHORED the defendant solution.

- **Judge (Claude)**: Always Claude — neutral arbiter, makes final rulings.
  Claude never argues as counsel for or against a candidate it authored.
- **Defense Lawyer**: The external provider that authored the defendant
  solution — its author is its natural, non-conflicted advocate.
- **Prosecutor**: The other external provider.

Assignment table (defendant author → roles):

| Defendant author | Defense CLI | Prosecution CLI |
|---|---|---|
| Gemini | `$GEMINI_CLI` | `$OPENAI_CLI` |
| OpenAI | `$OPENAI_CLI` | `$GEMINI_CLI` |
| Claude | `$GEMINI_CLI` (neither external authored it) | `$OPENAI_CLI` |

```bash
DEFENDANT_AUTHOR="{claude|gemini|openai — author of the highest-trust solution}"
case "$DEFENDANT_AUTHOR" in
  openai) DEFENSE_CLI="$OPENAI_CLI"; PROSECUTION_CLI="$GEMINI_CLI"
          DEFENSE_MODEL_LABEL="openai"; PROSECUTION_MODEL_LABEL="gemini" ;;
  *)      DEFENSE_CLI="$GEMINI_CLI"; PROSECUTION_CLI="$OPENAI_CLI"
          DEFENSE_MODEL_LABEL="gemini"; PROSECUTION_MODEL_LABEL="openai" ;;
esac
echo "[Phase 3] defendant=$DEFENDANT_AUTHOR defense=$DEFENSE_MODEL_LABEL prosecution=$PROSECUTION_MODEL_LABEL" >&2
```

## Step 3.3: Defense Execution (`$DEFENSE_CLI`)

**Bash tool timeout: `${BASH_TIMEOUT_MS:-300000}` ms for all CLI executions in this phase.**

```bash
synod_progress "{\"event\":\"model_start\",\"model\":\"${DEFENSE_MODEL_LABEL}\"}"
```

Write to `${TEMP_DIR}/defense-prompt.txt` and execute with `$DEFENSE_CLI`:

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

Before arguing, confirm the debate still answers this original question;
if it has drifted, flag the drift explicitly before your defense.

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

## Step 3.4: Prosecution Execution (`$PROSECUTION_CLI`)

```bash
synod_progress "{\"event\":\"model_start\",\"model\":\"${PROSECUTION_MODEL_LABEL}\"}"
```

Write to `${TEMP_DIR}/prosecution-prompt.txt` and execute with `$PROSECUTION_CLI`:

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

Before arguing, confirm the debate still answers this original question;
if it has drifted, flag the drift explicitly before your prosecution.

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

## Step 3.5: Claude Judge Deliberation (rubric-decomposed since v3.8)

As the Judge, score each side on THREE SEPARATE rubric dimensions before any
holistic ruling — decomposed scoring measurably reduces judge self-preference
and style bias (single holistic rulings reward rhetoric over substance):

| Dimension | Question | Defense | Prosecution |
|---|---|---|---|
| **Correctness** | Are the factual/technical claims right? | 0–10 | 0–10 |
| **Evidence** | Are claims backed by specific, checkable evidence? | 0–10 | 0–10 |
| **Edge-case coverage** | Are failure modes and boundaries addressed? | 0–10 | 0–10 |

> **ANTI-STYLE-BIAS INSTRUCTION (apply to every dimension):**
> Judge arguments on substance ONLY. Do NOT reward length, confident tone,
> markdown polish, formatting, or rhetorical flourish — style bias exceeds
> position bias in LLM judges. A short plain paragraph with one verifiable
> citation outranks a long polished section with none.

Then:

1. **Evaluate Defense Strength** - Did they address all criticisms? Is evidence compelling?
2. **Evaluate Prosecution Strength** - Are the attacks valid? Is the alternative viable?
3. **Make Preliminary Ruling** - Which side has the stronger case? The ruling
   must cite the per-dimension scores; if the holistic impression disagrees
   with the dimension totals, explain why explicitly.

## Step 3.6: Save Defense Round State

Save to `${SESSION_DIR}/round-3-defense/`:
- `judge-deliberation.md` - Your analysis as Judge (including the rubric score table)
- `defense-args.md` - Defense counsel's arguments (`$DEFENSE_MODEL_LABEL`)
- `prosecution-args.md` - Prosecutor's arguments (`$PROSECUTION_MODEL_LABEL`)
- `preliminary-ruling.md` - Initial judgment

Update status.json to round 3 complete.

```bash
# Emit completions and phase end (v2.1)
synod_progress "{\"event\":\"model_complete\",\"model\":\"${DEFENSE_MODEL_LABEL}\"}"
synod_progress "{\"event\":\"model_complete\",\"model\":\"${PROSECUTION_MODEL_LABEL}\"}"
synod_progress '{"event":"phase_end","phase":3}'
```

**Next Phase:** Proceed to Phase 4 (see `synod-phase4-synthesis.md`)
