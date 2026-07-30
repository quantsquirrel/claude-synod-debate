# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Custom model configuration per session (not just global defaults)
- Debate visualization dashboard showing confidence trajectories
- Integration with Claude Code's native analysis capabilities
- Batch processing for multiple problems in sequence
- Export debates to markdown reports with embedded confidence metrics
- **Run the live judgment-task eval** (`SYNOD_JUDGE_LIVE=1 … --live --n 50`) and commit results to `benchmark/results/` — but not before the two items below. Start at `--n 5` (70 calls) and check the flip rate before paying for the full 50 (700 calls)
- **Run the live S0-vs-S3 GSM8K ablation** (`SYNOD_BENCH_LIVE=1 … --live --n 50`) and commit results to `benchmark/results/`. Now measures 50 real questions rather than 10 — but read the power caveat first: this arm bounds cost, it does not settle whether debate helps
- Validate the judgment-task set before trusting any number it produces: measure separability (bootstrap CIs) and judge-vs-human agreement, per [arXiv:2408.08808](https://arxiv.org/abs/2408.08808). Candidates for an externally-validated primary arm: CODAL-Bench / [CodeUltraFeedback](https://arxiv.org/abs/2403.09032), [CodeJudgeBench](https://arxiv.org/pdf/2507.10535), [Arena-Hard-Auto](https://github.com/lmarena/arena-hard-auto). Licence/redistribution terms unchecked
- Fix the LiveRunner topology mismatch: Synod's Claude is the **in-session** model (`synod-phase1-solver.md` writes `CLAUDE_SOLVER_RESPONSE` from the session), but LiveRunner calls the Anthropic API with a hardcoded `claude-sonnet-5` / 1024-token config, and runs 2 solvers where Phase 1 has 3 (claude Validator + gemini Architect + openai Explorer). So live numbers do not measure Synod as shipped, and `ANTHROPIC_API_KEY` is a harness artifact rather than a Synod requirement

---

## [3.11.0] - 2026-07-30

Benchmark-honesty release. No skill or pipeline behaviour changes — the
deliberation path is identical to 3.10.0. What changed is that Synod's
self-measurement no longer overstates what it measures, and the marketplace
catalog no longer advertises mechanisms that 3.8-3.10 retired.

### Changed

- **Marketplace and plugin descriptions corrected.** The catalog Claude Code
  shows on install still advertised "SID confidence scoring" and the
  "CortexDebate Trust Score" as headline features, and claimed "3-round debates
  capture >95% of quality improvements". v3.8 demoted self-reported confidence
  to display-only and deleted the dynamic-rounds machinery as a placebo knob;
  v3.10 replaced the CRIS/CortexDebate trust rubric with trust derived
  mechanically from the verified-citation rate. The descriptions now state the
  mechanical signals that actually drive decisions, and the feature list names
  the debate gate, citation verifier, claim ledger with mandatory dissent,
  anonymised review, and the opt-in execution arbiter.

### Added

- **Judgment-task evaluation arm** (`benchmark/judgment_eval.py` +
  `benchmark/data/judgment_tasks.jsonl`). 50 authored design/review tasks
  across 10 domains (5 each), every task carrying a 4-criterion rubric and a
  `failure_mode` naming what a shallow single pass usually misses — 200 judged
  criteria in total. This is the arm the literature says should separate S0
  from S3, because GSM8K cannot (see Fixed below).

  Scoring is by judge, so the judge is the measurement instrument. Four
  countermeasures are mandatory and recorded in every report: anonymised
  responses with a runtime assertion that arm identity never reaches the judge
  ([arXiv:2510.07517](https://arxiv.org/abs/2510.07517),
  [arXiv:2404.13076](https://arxiv.org/abs/2404.13076)); position swap with
  agreement required ([arXiv:2305.17926](https://arxiv.org/abs/2305.17926));
  rubric decomposition instead of holistic rulings
  ([arXiv:2604.23178](https://arxiv.org/abs/2604.23178)); and a cross-family
  judge — selecting a judge from the authoring family (`anthropic`) aborts the
  run, while the unavoidable solver overlap (Gemini/OpenAI judge a text their
  own answers fed) is emitted as an explicit warning.

  A **reliability gate** refuses to name a winner when the position-flip rate
  exceeds 30%: a judge that contradicts itself under reordering has measured
  nothing, so reporting its majority would be false precision. Win rate is
  computed over decisive criteria only, and a rate in [0.45, 0.55] reports as
  `NO SEPARATION` rather than a narrow win. Mock mode gives both arms equal
  rubric coverage by construction, so a tie is the correct offline result.
  Docs: `benchmark/README_judgment_eval.md`. 62 new tests.

### Fixed

- **The live S0-vs-S3 ablation could not have measured anything.** `main()` fed
  the LIVE branch from `load_mock_gsm8k(args.n)`, a 10-problem hardcoded pool
  returned as `problems[:n]` — so the documented `--live --n 50` silently ran
  10 questions, `--seed` was accepted but never used, and those 10 problems are
  single-step arithmetic that every strategy answers correctly. Billing three
  providers for that measures a ceiling effect.

  Adds `load_gsm8k(n, seed)`: prefers the real GSM8K test split when `datasets`
  is installed, else a vendored 50-problem pool of 2-4 step problems whose
  answers are each computed from an `expr` recorded in the file (a test
  recomputes all 50). Seeded sampling, and the loader **raises** rather than
  truncating when the pool is too small. Reports gain `meta.dataset`
  provenance (source, pool size, seed, question ids) plus a power caveat: at
  n=50 near the GSM8K ceiling the 95% CI is ~±6pp, so this arm is a cost
  measurement and no-regression check — `judgment_eval.py` is the
  discriminating arm.
- LiveRunner prerequisite failures now print an actionable message instead of a
  traceback, and state that no API calls were made.
- `live_verification_gap` in strategy-compare reports no longer names the
  retired `agy-cli`/`cliproxy-cli` wiring, with a test pinning it to the
  current lanes.
- Mock mode warns instead of silently capping when `--n` exceeds its 10-problem
  pool.

---

## [3.10.0] - 2026-07-30

Measurement-readiness release — the final tranche of the 2026-07 research
audit roadmap. CRIS is demoted to an auditable mechanical signal, and the
killer-baseline ablation (S0) plus a real LiveRunner make Synod's core value
claim locally measurable for the first time.

### Changed

- **CRIS rubric demoted (Phase 2 Step 2.4).** The self-graded C/R/I/S 5-band
  tables and `T = min((C·R·I)/S, cap)` are removed from the default flow —
  LLM-judged trust overrides tested net-negative (arXiv:2606.29270). Trust is
  now mechanical: with TARGET_PATH, `T = 0.25 + 1.75 × verified-citation-rate`
  (`citation_verifier.py trust_from_rate`; all-fabricated → 0.25, below the
  0.5 exclusion threshold; all-verified → 2.0 cap; nothing decidable →
  neutral 1.0); without TARGET_PATH, uniform 1.0 with SID-confidence tiebreak
  for Phase 3 defendant selection only. `trust-scores.json` keeps its schema
  plus a `basis` field ("citation-verification" | "uniform") so Phase 3/4 and
  resume are unchanged. The parser `--trust` CLI (CortexDebate) survives as a
  tested utility outside the default flow.
- **`citation_verifier.py --dir` now emits a per-model `trust` map** and each
  report carries `trust_score`, wiring Phase 2 Path A in one call.

### Added

- **S0 strategy arm (`S0_independent_synthesis`)** in
  `benchmark/strategy_compare.py`: independent solver answers + ONE synthesis
  pass, zero cross-talk — the ablation the literature says explains most of
  MAD's gains (Smit et al. ICML 2024; martingale arXiv:2508.17536). Runs
  alongside S1/S2/S3 in mock and live modes. MockRunner's S0 synthesis is an
  HONEST majority vote (deliberately not scripted-correct, unlike mock S3's
  documented by-construction win).
- **LiveRunner implemented against the current direct-API lanes** (was a
  NotImplementedError stub targeting the retired agy-cli/cliproxy-cli):
  Phase-1 via `gemini-3` + `openai-cli` CLIs, S0 synthesis via the Anthropic
  SDK (Claude-as-synthesizer, matching Synod topology), and `full_debate` as
  a documented programmatic APPROXIMATION of Phases 2–4 (critique round +
  synthesis, not the court pipeline). Spending is double-guarded: `--live`
  AND `SYNOD_BENCH_LIVE=1`, with clear prerequisite errors at construction.
  No live run has been executed — mock numbers remain harness-validation only.

---

## [3.9.0] - 2026-07-30

Verification-first release — the second tranche of the 2026-07 research audit.
v3.8 fixed the signal plumbing; v3.9 adds the machinery the grounded-debate
literature says reliability actually comes from: verify evidence mechanically,
preserve claims losslessly, let execution settle what it can. The full
research basis (citations + evidence-strength labels + honest limitations) is
now documented in README.md "Research-Driven Changes (v3.8–v3.9)".

### Added

- **`tools/citation_verifier.py` + Phase 4.5 upgrade (counter → verifier).**
  Strictly mechanical checks per citation: file exists under TARGET_PATH,
  line within file length. Verdicts: verified / bad_line / not_found
  (fabrication signals, surfaced as first-class findings) / ambiguous /
  outside. Runs per-model over `round-1-solver/*.md` (fabrication attributed
  to the model that fabricated) plus the synthesis. The `evidence-based`
  label now additionally requires zero fabricated citations. Semantic
  relatedness is deliberately NOT judged — that would need an LLM again;
  keyword overlap is reported as observability only. (Tool-MAD
  arXiv:2601.04742; MAST arXiv:2503.13657 — pre-v3.9, a fabricated
  `utils.py:9999` scored the same as a real citation.)
- **Lossless claim ledger (`synod-parser.py --claim-list`).** Phase 2
  HISTORY_CONTEXT no longer compresses each solver to a ≤30-word sentence —
  the exact factual-attrition mechanism the literature measures (up to 72% of
  issue-critical facts erased across rounds, arXiv:2606.03032). All
  semantic_focus claims survive verbatim with stable ids (C1../G1../O1..);
  critics are asked to reference claim ids (free-text still accepted —
  no new format-failure mode). Fail-safe falls back to the legacy table.
- **Mandatory Dissent section in Phase 4.** Any claim held by a single solver
  (trust ≥ 0.5) that was disputed but not refuted with cited evidence is
  listed — never silently dropped ("~25% of divergent cases the minority is
  right", arXiv:2606.29270). The section renders explicitly even when empty,
  so its absence is auditable.
- **Execution arbiter (`tools/exec_arbiter.py`, `SYNOD_EXEC_ARBITER=1`).**
  Debug/review modes with a TARGET_PATH: runs the target's own suite
  (`pytest -x -q`, hard timeout, tail-bounded output) using
  ground_truth_probe's existing test_collect.json for discovery, and injects
  pass/fail into the critic context under the machine-verified Primary
  Evidence convention. Timeout is reported as UNSETTLED, never as failing.
  Models debate only what execution cannot settle (CWM arXiv:2510.02387).
- **Question re-anchoring drift check.** One-line addition to every Phase 2/3
  external prompt: confirm the discussion still answers the original
  question, flag drift first (76–89% drift on subjective tasks,
  arXiv:2502.19559).
- **README (en/ko): "Research-Driven Changes (v3.8–v3.9)"** — full mapping of
  every mechanism change to its citations, with evidence-strength labels
  (multi-source / replicated / single-paper preprint), what was deliberately
  NOT changed and why, and honest limitations (no local benchmark yet; the
  heterogeneous-panel-vs-single-frontier question remains empirically open).

---

## [3.8.0] - 2026-07-30

Research-alignment release. A 2025–2026 multi-agent debate literature audit
(60 sources, adversarially verified) found synod's skeleton — heterogeneous
cross-provider panel, orchestrator synthesis, independent Phase 1, consensus
gate — well-supported, but three mechanisms contradicted: self-reported
confidence as a control signal ("When Two LLMs Debate" arXiv:2505.19184),
fixed court roles that let a provider prosecute its own winning solution
(judge-bias literature), and a dynamic-rounds knob that never changed
execution. This release consolidates decisions onto mechanical signals.

### Changed

- **Debate gate (Phase 1.5) is now DEFAULT-ON and re-keyed on claim agreement.**
  `SYNOD_DEBATE_GATE` defaults to `1` (`0` = always full debate).
  `agreement_score` is now pure negation-aware claim agreement — the v3.7
  composite folded 50% self-reported signals (can_exit/high-conf fractions)
  into it. The `vote_confidence>=85`, `min_trust>=1.0` (vacuous — trust_score
  never present in Phase-1 files), and `frac_can_exit>=0.5` skip conditions
  are removed; one modest fail-closed floor remains (`SYNOD_GATE_MIN_CONF`,
  default 80→60). New `--tier` argument: **deep/ultra tier always runs the
  full debate** (debate pays on hard contested problems, arXiv:2505.22960).
  Fixed the 0.70-vs-0.80 doc/code threshold drift in the module doc.
- **Anonymization is now DEFAULT-ON** (`SYNOD_ANONYMIZE=1`) across Phases 1–4,
  honestly reframed: it prevents brand-deference in the stateless external
  CLIs (arXiv:2510.07517); the in-session Claude judge cannot be blinded.
- **Phase 3 court roles are authorship-aware.** The provider that authored the
  defendant solution defends it; the other prosecutes; Claude never argues as
  counsel for a candidate it authored. Pre-v3.8 the hardcoded
  Gemini=Defense/OpenAI=Prosecutor table could make OpenAI prosecute its own
  winning solution.
- **Judge rulings are rubric-decomposed** (correctness / evidence / edge-case
  coverage scored separately) with an explicit anti-length/anti-markdown
  instruction — style bias exceeds position bias in LLM judges.
- **benchmark/config.yaml + baselines.py track the live direct-API roster**
  (was the retired gpt-4o / claude-sonnet-4 / gemini-2.0-flash set);
  baselines now read models from config.yaml. `gpt4o_only` → `openai_only`.
- **README research table retitled** so protocol-borrowed numbers (ReConcile's
  >95%) cannot read as Synod results; Free-MAD/SID rows reclassified as
  in-house heuristics (no verifiable citations exist); DOWN citation added.
- **MockRunner banner strengthened**: mock output now states loudly that S3
  wins BY CONSTRUCTION and is not evidence of Synod accuracy.

### Removed

- **Phase 1 confidence early exit** (all can_exit + all ≥90 → skip to Phase 4).
  Skipping is now decided solely by the Phase 1.5 gate on claim agreement.
  The unused `early_exit_confidence` threshold is gone from synod-modes.yaml.
- **`SYNOD_V2_DYNAMIC_ROUNDS` and the complexity→rounds machinery**
  (classifier `rounds` output, `get_rounds`/`get_complexity_rounds`,
  per-mode `rounds:` blocks, `TOTAL_ROUNDS`/`BASE_ROUNDS`). Round count only
  ever labeled the session — the phase structure is fixed. Complexity→tier
  mapping (which IS load-bearing for models/timeouts) is preserved.
- Retired gate env vars: `SYNOD_GATE_HIGH_CONF`, `SYNOD_GATE_MIN_TRUST`,
  `SYNOD_GATE_MIN_CANEXIT` (frac_can_exit/frac_high_conf remain as
  observability-only signals in gate.json).
- **Phase 4 `FINAL_CONFIDENCE = Σ(T·C)/Σ(T)`** — it laundered uncalibrated
  self-reports through unvalidated CRIS weights. Templates now render a
  mechanical 합의 지표 block: N-of-M claim agreement (reuse of the gate's
  lexical machinery), concession counts from the judge, and citation coverage
  when Phase 4.5 is active. Step 2.1b soft defer is deliberately KEPT — it is
  an anti-sycophancy use of the confidence signal, not a gate.

---

## [3.7.0] - 2026-07-25

### Changed

- **BREAKING — `direct` is now the default provider backend.** `provider_backend.DEFAULT_BACKEND` flips from `bridge` to `direct`, so both lanes call the vendor APIs with your own keys. **`GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and `OPENAI_API_KEY` are now required**; the previous local-session/proxy auth no longer applies. The `agy-cli` (Antigravity) and `cliproxy-cli` (CLIProxyAPI) bridges expired ~2026-06-30 and are retired — reachable only via `SYNOD_PROVIDER_BACKEND=bridge` for recovery on an old roster. An unknown backend value now falls back to `direct`, so a typo cannot silently route through the retired bridges. Validate with `python3 tools/cutover_check.py`.
- **BREAKING — model vocabulary.** `config/model_matrix.json` and `config/synod-modes.yaml` are re-authored in direct vocabulary: Gemini `pro-latest` (= `gemini-pro-latest`, currently resolving to `gemini-3.1-pro-preview`) and OpenAI `gpt56sol` (= `gpt-5.6-sol`). The stable alias is used rather than a preview pin, per the 3.0 preview-EOL incident of 2026-03-09. Retired bridge keys (`3.1-pro`, `3.5-flash`, `gpt55fast`) still translate for recovery, and direct keys now map to themselves so direct→direct resolution is idempotent.
- **Reasoning depth is now split by tier** rather than set globally, because depth and latency trade off directly:

  | Tier | Gemini `thinking` | OpenAI `reasoning` | model timeout |
  |---|---|---|---|
  | simple | `low` | (`gpt54mini`, default) | 60s |
  | standard | `low` | `low` | 120s |
  | deep | `high` | `high` | 240s |
  | ultra | `high` | `xhigh` | 1800s |

  `deep`'s OpenAI lane stays at `high`: `xhigh` measures ~191s against a 240s ceiling, too little headroom. Raising it requires lifting that ceiling and the 300s/360s outer/bash layers first.
- `synod-setup` now treats `gemini-3`/`openai-cli` as the primary CLIs with the bridges as fallbacks, and requires `google-genai` again.
- CI pins `ruff==0.15.12`. An unpinned ruff silently redefines "formatted" and fails unrelated PRs; 0.16 additionally formats Python code blocks inside Markdown, which this repo has not been through yet.

### Added

- **Native `thinking_level` support for Gemini 3.x** (`tools/gemini-3.py`). `thinking_budget` **saturates** on 3.x, so maximum reasoning depth was previously unreachable — asking for `--thinking max` produced *less* thinking than `high`. Measured on `gemini-3.1-pro-preview` (hard prompt, 2026-07-25), thought tokens / wall clock:

  | Control | Thought tokens | Latency |
  |---|---|---|
  | `thinking_budget=200` | 1,153 | 22.3s |
  | `thinking_budget=2000` | 5,766 | 55.5s |
  | `thinking_budget=10000` | 5,137 | 52.6s — no gain, saturated |
  | `thinking_level=LOW` | 2,140 | 30.0s |
  | `thinking_level=HIGH` | **8,473** | **74.8s** |

  `HIGH` is the deepest level the API accepts; `max` collapses to it (a literal `thinking_level="max"` is a 400), and level/budget are mutually exclusive. The legacy 2.5 family keeps `thinking_budget`.
- **SDK capability gating** — `sdk_supports_thinking_level()` probes `ThinkingConfig.model_fields`, because an older `google-genai` rejects the field at *construction* time with a pydantic `ValidationError` before any network call. `is_thinking_level_model()` (model family) and `uses_thinking_level()` (family **and** SDK) are now separate. An SDK too old to reach maximum depth warns on stderr rather than silently degrading to the saturating budget knob.
- **`gpt-5.6-sol` with `xhigh` reasoning** (`tools/openai-cli.py`). Measured on the same prompt: `low` 1,024 reasoning tokens / 31.6s · `high` 6,656 / 120.4s · `xhigh` 11,548 / 190.6s. `XHIGH_MODELS` records which models accept `xhigh` (gpt-5.6-sol, gpt-5.5, gpt-5.4, gpt-5.4-mini, o3); `clamp_reasoning()` degrades `xhigh`→`high` with a stderr notice for models that reject it (gpt-5-mini, gpt-4o), so one shared tier config never 400s on a subset of models.

### Fixed

- **`tiers.fast` paired `thinking: high` with a 60s model timeout** — at 74.8s measured, a guaranteed timeout. Now `low`.
- **`RETRY_LEVELS` omitted `max`** in `gemini-3.py`, so a timeout at `max` fell through to index 1 and skipped two levels straight to `low` instead of stepping down to `high`. Same fix on the OpenAI side, where `xhigh` now leads the ladder.
- **Shell/Python backend defaults disagreed.** `${SYNOD_PROVIDER_BACKEND:-bridge}` in `SKILL.md` and `synod-phase1-solver.md` is now `:-direct`, matching `DEFAULT_BACKEND`; a mismatch there desyncs the CLI lane from the model vocabulary.
- **mypy aborted before reaching `tools/`.** `python_version = "3.9"` also applies to third-party sources mypy follows into, and a newer `anyio` (transitive via `httpx`) uses `match` statements. Added a `follow_imports = "skip"` override for `anyio`. With mypy reaching the code again it flagged a real issue: `ThinkingConfig.thinking_level` is typed as the enum, not `str`.
- `README.ko.md` had drifted out of sync with `README.md` and still documented Gemini 3.5 Flash.
- `.gitignore` now covers `.omo/`, `.claude/eval/`, and `.playwright-mcp/`.

---

## [3.6.0] - 2026-05-09

### Added
- **Branded per-model claim summary in Phase 4 collapsible** (`skills/synod/modules/synod-phase4-synthesis.md`). The "숙의 과정" / "모델 기여" list now renders each agent's PRIMARY `semantic_focus` claim under a brand-shape unicode glyph that visually echoes the provider's mark:
  - `✻` (U+273B HEAVY EIGHT TEARDROP-SPOKED ASTERISK) — Anthropic asterisk for Claude
  - `✦` (U+2726 BLACK FOUR POINTED STAR) — Gemini sparkle
  - `❀` (U+2740 BLACK FLORETTE) — OpenAI knot/floret
  Markers are monochrome in the markdown surface because Claude Code's renderer does not apply HTML inline color or data-URI SVG; the brand color identity is preserved on the HUD surface (Rich) via per-model hex codes stored in `tools/model_branding.py`.
- **`tools/model_branding.py`** — single source of truth for the `(label, hex, rich-color, glyph)` tuple per first-party provider, plus a `markdown_marker(model)` helper returning the unicode glyph used in markdown. Both `tools/synod_progress.py` HUD and the Phase 4 markdown layer source from this module so the two surfaces cannot drift.
- **Hex codes available to HUD** — `MODEL_CONFIG` now stores the truecolor hex per model in addition to the existing Rich named-color, opening a path to a future truecolor HUD without further data plumbing.
- **Documentation-as-test guard** — `tests/test_phase4_branding.py` asserts that the Phase 4 instruction text references the correct emoji and hex codes for every model, so emoji-or-color drift surfaces as a CI failure rather than a silent visual regression.

### Fixed
- **`semantic_focus[0]` numeric prefix leak** (`tools/synod-parser.py`). The split regex consumed the newline before each `\d+. ` marker for items 1..N, but the first item — starting at `content.strip()` with no leading newline — kept its `"1. "` prefix. `extract_semantic_focus()` now strips a leading `\d+\.\s*` from every item so the output is uniform. Regression test added.

### Changed
- **HUD claude row color**: `magenta` → `orange3`. The 256-color name is the closest fit for the `#D97757` brand coral; truecolor terminals can already render the exact hex via the new `MODEL_CONFIG[model]["hex"]` field.

---

## [3.5.0] - 2026-05-09

### Added
- **Phase 0.5 — Ground-Truth Probe + Prompt Lint + Tier Select** (`skills/synod/modules/synod-phase0-5-ground-truth.md`). Opt-in via `SYNOD_EVIDENCE_FIRST=1` or `--evidence-first`. Runs before Phase 1 Solver and enriches the PROBLEM passed to external models with machine-verified Primary Evidence and Known Limitations sections. Backward-compatible: legacy flow unchanged when the flag is unset.
- **Phase 4.5 — Evidence Coverage Gate** (`skills/synod/modules/synod-phase4-5-evidence-gate.md`). Post-synthesis annotation that reports the % of claims backed by `file:line` citations (`evidence-based` ≥70%, `partial` 30–70%, `narrative-based` <30%). Informational only — never blocks output.
- **Mechanical probes**:
  - `tools/ground_truth_probe.py` — inspects a target path's import/test/version state and emits `integrity.json`, `top_findings`, and a `file_tree.txt` snapshot.
  - `tools/prompt_linter.py` — regex audit for unbacked claims (e.g., `default X`, `providers/ 추상화`, `22/22 regression green`); high-severity findings can gate Phase 1 unless `--skip-lint`.
  - `tools/tier_matrix.py` — explicit tier→model roster mapping that replaces latency-based "recommended" defaults so reasoning-strong models (pro-thinking, o3-high) are not silently demoted.
- **Tier roster config**: `config/model_matrix.json` — `simple`/`standard`/`deep`/`ultra` tier definitions with provider/cli/model/thinking|reasoning fields and `async_threshold_sec` for wall-clock budgeting.

### Compatibility
- Default flow is unchanged — Phase 0.5 and 4.5 are dormant unless `SYNOD_EVIDENCE_FIRST=1` (or `--evidence-first`) is set, so existing v3.4.x users see no behavioral difference until they opt in.
- `evidence_gate.py` is intentionally not shipped as a CLI; the gate runs inline in the Phase 4 orchestrator context per the pseudocode in `synod-phase4-5-evidence-gate.md`.

---

## [3.4.0] - 2026-05-07

### Added
- **OpenAI lineup expanded**: `gpt55` (gpt-5.5, released 2026-04-23) and `gpt54mini` (gpt-5.4-mini) added to `MODEL_MAP`. Both registered as reasoning-capable in `REASONING_MODELS`.
- **Gemini stable aliases**: `flash-latest`, `pro-latest`, `flash-lite-latest` added — these route to provider-side stable pointers, removing dependency on `*-preview` model IDs that have caused EOL migration incidents.
- **TIMEOUT_CONFIG entries**: gpt55 (low/medium/high = 90/120/180s) and gpt54mini (60/90/120s) calibrated against measured p50/max latency on a 5-problem A/B run and applied as OpenAI provider defaults.

### Changed
- **OpenAI `DEFAULT_MODEL`**: `gpt4o` → `gpt54mini`. Direct `openai-cli` invocations without `--model` now use the more recent and cheaper model. Synod-mode-specific defaults (review/design/debug/idea/general) are unchanged.
- **Fast tier Gemini default**: `flash` (gemini-3-flash-preview) → `flash-lite-latest` (gemini-flash-lite-latest). Measured p50 latency dropped from 12.7s to 1.9s (≈6.7× faster) with no accuracy regression on 5-problem GSM8K-style A/B. Standard and deep tiers unchanged.
- **OpenAI reasoning support**: `gpt5mini` now receives `reasoning_effort`, matching the rest of the GPT-5-family aliases.

### Verified
- **Live API smoke test**: 11/11 currently-configured models reachable.
- **SID format compliance**: 10/10 reachable candidates produce valid `<confidence>` and `<semantic_focus>` blocks.
- **Reachability gotcha**: `gemini-3-pro-preview`, `gpt-5-pro`, `gpt-5.4-pro`, and `gpt-5.5-pro` are NOT exposed as aliases because they are unavailable or return 404 on chat completions. Will revisit when access is granted.

---

## [3.3.0] - 2026-03-16

### Added
- **Model lineup v3.3**: GPT-5.4, Llama 4, Magistral, Grok 4.1 support
- **Gemini 3.1 models**: 3.1-flash-lite, 3.1-pro added with COLD_START_DEFAULTS
- **Multi-source API key resolution**: env var → `~/.synod/.env` → macOS Keychain fallback chain

### Fixed
- **Gemini pro migration**: Urgent migration to 3.1-pro-preview (3.0 EOL 2026-03-09)

### Changed
- **Skills restructured**: Directory-based `SKILL.md` format (e.g., `skills/synod/SKILL.md`)
- **Korean README improved**: Natural language rewrite with missing sections added

---

## [3.2.0] - 2026-02-21

### Added
- **Debate quality metrics**: `parse_response()` now emits per-response metrics (response_length, format_compliance, confidence_score, semantic_focus_count, has_evidence, has_logic, has_code)
- **Round metrics aggregation**: `collect_round_metrics()` aggregates metrics across model responses
- **Metrics display**: `format_metrics_summary()` produces one-line summary, displayed in Phase 4 synthesis output
- **Confidence-to-tier linkage**: `get_tier()` accepts optional confidence parameter; promotes queries one tier when classifier confidence is low
- **Problem type activation**: `problem_type` from classifier now influences Phase 0 model selection (coding->high thinking, math->o3, creative->pro)

### Changed
- **AGENTS.md overhauled**: Slimmed from 1,438 LOC to ~224 LOC; updated to v3.2.0 with accurate paths and directory structure
- **Extended providers relocated**: deepseek, groq, grok, mistral, openrouter moved to `tools/providers/extended/`
- **Feature flags simplified**: Removed `SYNOD_V2_CANARY` (archived); 3 core flags + 2 provider guards remain
- **Adaptive timeout simplified**: Inline cold-start defaults replace `model_stats.py` dependency

### Removed
- **Canary system archived**: `canary.py`, `synod-canary.py`, `model_stats.py` moved to `tools/archived/`; corresponding tests to `tests/archived/`
- **SYNOD_V2_CANARY flag**: Completely removed from all active code and documentation

---

## [1.0.0] - 2026-01-31

### Added

#### Core Deliberation System
- **3-round structured debate framework**: Solver → Critic → Defense/Prosecution with full court-style arbitration
- **Multi-agent coordination**: Parallel execution of Claude (Validator), Gemini, and OpenAI models with intelligent fallback chains
- **SID (Self-Signals Driven) confidence scoring**: 0-100 scale with semantic focus anchoring (primary, secondary, tertiary claims)
- **CortexDebate Trust Score calculation**: Formula-based trust assessment = min((C×R×I)/S, 2.0)
  - Credibility (evidence quality)
  - Reliability (logical consistency)
  - Intimacy (problem relevance)
  - Self-Orientation (bias detection)

#### Specialized Modes
- **review mode**: Code review and analysis with severity levels (ERROR, WARNING, INFO)
- **design mode**: Architecture and system design with trade-off analysis
- **debug mode**: Root cause analysis with evidence chains and prevention strategies
- **idea mode**: Brainstorming with ranked ideas and feasibility assessment
- **general mode**: Balanced question answering with comprehensive coverage

#### Anti-Conformity & Debate Quality
- **Free-MAD methodology**: Explicit anti-conformity instructions preventing premature consensus
- **Soft defer mechanism (ConfMAD)**: Preserved low-confidence perspectives instead of forcing agreement
- **Adversarial roles**: Defense lawyer vs. prosecutor model enforcing balanced argumentation
- **ReConcile convergence pattern**: 3-round structured agreement resolution process

#### Session Management
- **Full session persistence**: Complete debate history stored in structured directories
- **Session resume capability**: Continue interrupted debates from any checkpoint
- **Session state tracking**: Current round, completion status, and resume points
- **Configurable session directory**: `SYNOD_SESSION_DIR` environment variable support
- **Session metadata**: Mode, problem type, complexity classification, and model configuration

#### Robustness & Error Handling
- **Timeout fallback chains**: Graceful degradation with 110-second internal timeouts and automatic retry logic
- **Format enforcement protocol**: Re-prompting for malformed responses with XML validation
- **Low trust score fallback**: Prevents exclusion of all agents even when confidence is universally low
- **API error handling**: Rate limit detection, authentication error handling, and cached response fallback
- **Parser redundancy**: Inline fallback parser activates if external `synod-parser` unavailable

#### CLI Tools
- **synod-parser.py**: SID signal extraction, XML validation, and Trust Score calculation
  - Validates response format compliance
  - Extracts confidence scores and semantic focus
  - Calculates CortexDebate Trust Scores
  - Applies intelligent defaults for malformed responses

- **gemini-3.py**: Google Gemini API integration with adaptive thinking
  - Support for Gemini Flash and Gemini Pro models
  - Configurable thinking effort (low/medium/high)
  - Adaptive temperature control (0.5-0.7 per persona)
  - Automatic timeout retry with exponential backoff

- **openai-cli.py**: OpenAI integration with advanced reasoning support
  - Support for GPT-4o and o3 models (o3 for complex reasoning)
  - Reasoning effort levels (medium/high) for o3
  - Fallback models for degraded operations
  - Temperature configuration for gpt4o (o3 maintains fixed 1.0)

#### Benchmark Suite
- **GSM8K benchmark**: Math reasoning evaluation dataset
- **Baseline implementations**: Reference implementations for model comparison
- **Evaluator framework**: Structured evaluation metrics and result aggregation
- **Analysis tools**: Statistical analysis and confidence metrics reporting

#### Configuration System
- **Environment variables**: API keys, session directory, and retention policies
- **Mode-specific model selection**: Different models for each mode (Flash for review/debug, Pro for design/idea)
- **Temperature configuration per persona**: Solver (0.7), Critic (0.5), different models for different roles
- **Custom model override**: Optional `.claude/synod-config.json` for per-user customization

#### Output & Reporting
- **Mode-specific output formatting**: Tailored results for review, design, debug, idea, and general modes
- **Confidence-weighted synthesis**: Final conclusions weighted by Trust Scores
- **Decision rationale collapsible section**: Shows deliberation process with model contributions
- **XML-based structured output**: Confidence blocks with evidence, logic, and expertise annotations

### Technical Foundation

- **Multi-model orchestration**: Parallel execution with intelligent coordination and state management
- **Deterministic session IDs**: `synod-YYYYMMDD-HHMMSS-xxx` format for easy identification and resume
- **Structured session directories**: Organized round-by-round response storage with JSON state tracking
- **POSIX-compatible tooling**: macOS and Linux support with bash fallbacks for all utilities
- **Stdin/stdout piping**: Native Claude Code integration with streaming support

### Documentation

- **Comprehensive README**: Installation, usage, configuration, and troubleshooting guides
- **Skill definitions**: `/synod` command with full argument documentation
- **Error reference**: Common issues and solutions with diagnostic hints
- **Theoretical foundation**: Research citations and methodology explanations
- **API documentation**: Detailed response format specifications with examples

### Dependencies

- Python 3.9+ (for CLI tools and benchmarking)
- System utilities: `jq`, `openssl`, `bash`
- API credentials: `GEMINI_API_KEY`, `OPENAI_API_KEY`
- Claude Code: v1.0.0 or later

### Known Limitations

- Debate duration: Typically 2-5 minutes per 3-round session
- Token usage: ~5,000-15,000 tokens per debate (varies by prompt length)
- API timeouts: 110-second internal limits with automatic retry and degradation
- Model selection: Limited to Gemini (Flash/Pro) and OpenAI (GPT-4o/o3) models

---

## How to Report Issues

For bug reports, feature requests, or discussions:
- **Issues**: https://github.com/quantsquirrel/claude-synod-debate/issues
- **Discussions**: https://github.com/quantsquirrel/claude-synod-debate/discussions
- **Repository**: https://github.com/quantsquirrel/claude-synod-debate

## Upgrade Guide

### Installing v1.0.0

#### Via Plugin (Recommended)
```bash
/plugin install quantsquirrel/claude-synod-debate
```

#### Manual Installation
```bash
git clone https://github.com/quantsquirrel/claude-synod-debate.git
cd synod
pip install -r requirements.txt
cp skills/*.md ~/.claude/commands/
chmod +x tools/*.py
export PATH="$PATH:$(pwd)/tools"
```

#### Environment Setup
```bash
export GEMINI_API_KEY="your-gemini-api-key"
export OPENAI_API_KEY="your-openai-api-key"
export SYNOD_SESSION_DIR="~/.synod/sessions"  # Optional, defaults shown
```

### Quick Start
```bash
# Code review with multi-agent debate
/synod review Analyze the performance of this function

# Architecture design discussion
/synod design Design a JWT authentication system

# Debugging with cross-model analysis
/synod debug Why is this test failing?

# Brainstorming with idea evaluation
/synod idea How can we improve user onboarding?

# Resume interrupted debate
/synod resume

# General question with balanced perspective
/synod How should we approach this problem?
```

---

[Unreleased]: https://github.com/quantsquirrel/claude-synod-debate/compare/v3.3.0...HEAD
[3.3.0]: https://github.com/quantsquirrel/claude-synod-debate/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/quantsquirrel/claude-synod-debate/compare/v1.0.0...v3.2.0
[1.0.0]: https://github.com/quantsquirrel/claude-synod-debate/releases/tag/v1.0.0
