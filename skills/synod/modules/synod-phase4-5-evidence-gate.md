# Synod Module: Phase 4.5 — Evidence Coverage + Citation Verification Gate

> **v3.5.0** introduced the coverage counter; **v3.9.0** upgrades it to a
> citation VERIFIER. The counter only measured how many claims contained a
> citation-shaped string — a fabricated `utils.py:9999` scored the same as a
> real citation, so "evidence-based" could be earned with invented evidence.
> Grounded-debate research says reliability comes from verifying evidence,
> not counting it (Tool-MAD arXiv:2601.04742: +5.5% over ungrounded debate
> with faithfulness-scored judging; MAST arXiv:2503.13657: 21% of multi-agent
> failures trace to weak verification).
> Does NOT block output — the user sees everything plus the metrics.

Only runs when Phase 0.5 was active (i.e., `SYNOD_EVIDENCE_FIRST=1` or
`--evidence-first` was passed). Legacy flow skips this gate.

**Input:** Final synthesis markdown from Phase 4; `round-1-solver/*.md`
per-model responses; `TARGET_PATH` (when set).
**Output:** Coverage + verification annotation appended to the user-visible
verdict; `${SESSION_DIR}/phase4.5/citations.json` audit artifact.

---

## Part A — Coverage counting (all runs)

1. Parse the Phase 4 synthesis markdown.
2. Enumerate candidate claims: each `- bullet` or `N.` numbered item, excluding:
   - Lines inside fenced code blocks
   - Headers (`#`, `##`, ...)
   - Meta lines starting with "Overall Verdict:", "Summary:", "Top-N:"
3. For each claim, check whether it contains a citation matching
   `\S+\.(py|ts|js|go|rs|java|rb|md|json|toml):\d+` OR `<file>:<start>-<end>`.
4. Compute `coverage = cited_claims / max(total_claims, 1)`.

## Part B — Mechanical citation verification (TARGET_PATH set only)

Runs `tools/citation_verifier.py` — strictly mechanical checks (file exists
under TARGET_PATH, line within file length; semantic relatedness is NOT
judged — that would require an LLM again):

```bash
mkdir -p "${SESSION_DIR}/phase4.5"
# Per-model verification over Phase 1 responses (not just the synthesis) —
# fabrication is attributed to the model that fabricated, before synthesis
# blends sources.
python3 "${TOOLS_DIR}/citation_verifier.py" \
    --target "$TARGET_PATH" \
    --dir "${SESSION_DIR}/round-1-solver" \
    > "${SESSION_DIR}/phase4.5/citations.json"

# Synthesis-level verification for the user-facing annotation
python3 "${TOOLS_DIR}/citation_verifier.py" \
    --target "$TARGET_PATH" \
    --file "${SESSION_DIR}/round-4-synthesis.md" \
    > "${SESSION_DIR}/phase4.5/citations-synthesis.json"
```

Per-citation verdicts: `verified` / `bad_line` (line exceeds file length) /
`not_found` (no such file) / `ambiguous` / `outside`. **`bad_line` and
`not_found` are fabrication signals** and become first-class findings.

When `TARGET_PATH` is unset (design/general questions with no repo), Part B is
skipped and the annotation reports counting only — never imply verification
that did not happen.

## Thresholds (Part A label)

| Coverage | Label | Meaning |
|:-:|---|---|
| ≥ 70% | `evidence-based` | Concrete claims mostly traceable |
| 30% – 70% | `partial` | Mix of evidence and narrative; treat with caution |
| < 30% | `narrative-based` | Essentially qualitative opinion, not audit |

**v3.9:** the `evidence-based` label additionally requires
`fabricated == 0` in the synthesis verification (Part B, when it ran). A
synthesis with any fabricated citation is labeled at most `partial`, with the
fabrications listed.

## Output format

Append to the final verdict, immediately after the last synthesis section:

```
📊 Evidence Coverage: 78% (31/40 claims cite file:line).
🔎 Citation Verification: 29/31 verified against {TARGET_PATH}.
   ⚠ FABRICATED: parser.py:412 (file is 240 lines), helpers/util.py:12 (no such file)
   Fabrications are per-model attributed in phase4.5/citations.json.
```

With no fabrications:

```
📊 Evidence Coverage: 78% (31/40) — verdict is evidence-based.
🔎 Citation Verification: 31/31 verified against {TARGET_PATH}.
```

Without TARGET_PATH:

```
📊 Evidence Coverage: 58% (23/40 claims cite file:line) — counting only,
   no TARGET_PATH to verify against.
```

## Why 70% and not 90%

- 90% is unreachable in natural prose — topic sentences, transitions, and
  judgment calls are legitimately uncitable.
- 50% is too lenient — lets "vibes-based review" pass as evidence.
- 70% empirically forces most concrete recommendations to ground in primary
  evidence while allowing reasonable narrative glue. Tune via
  `config/synod-modes.yaml` → `evidence_gate.min_coverage` if needed.

## Orchestrator implementation

Part A runs in the lead's Phase 4 context — no external CLI call. ~30 lines
of Python on the synthesis markdown per the pseudocode below. Part B is the
`citation_verifier.py` CLI shown above.

```python
import re

CITATION_RE = re.compile(r"\S+\.(py|ts|js|go|rs|java|rb|md|json|toml):\d+(-\d+)?")
CLAIM_RE = re.compile(r"^\s*(?:-|\*|\d+\.)\s+")

def coverage(markdown: str) -> tuple[int, int]:
    in_code = False
    claims = 0
    cited = 0
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.startswith("#"):
            continue
        if line.lstrip().lower().startswith(("overall verdict:", "summary:", "top-")):
            continue
        if CLAIM_RE.match(line):
            claims += 1
            if CITATION_RE.search(line):
                cited += 1
    return cited, claims
```

The gate is additive — if parsing or verification fails for any reason, skip
the corresponding annotation rather than blocking output
(`citation_verifier.py` always exits 0 and reports errors in-band). Synod's
rule #3 ("자동 폐기 금지") applies: the gate informs, the human decides.
