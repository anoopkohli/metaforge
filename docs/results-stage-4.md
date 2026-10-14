# Stage 4 results: the eval harness

Stage 4 makes the eval gate something you can trust: it checks individual cases, not just averages;
it notices when a dataset is edited; it makes lowering a baseline a deliberate, recorded act; it
counts tokens and money; and it adds an LLM judge for live runs that cannot be talked into a grade.

## How to reproduce

```bash
make test                       # 318 tests (pg tests need `make pg-up`, as in stage 3)
make evals                      # every offline suite, gated; exit 1 on any regression
python -m evals.run --update-baseline --reason "why"          # deliberate baseline change
python -m evals.run --live --max-cost-usd 1.00                # real model; never gates
```

CI runs the gate on every push, writes the summary table to the GitHub job summary, and uploads the
JSON reports as the `eval-reports` artifact (14-day retention).

## Suites

| Suite | Modes | Cases | Offline pass rate | What it measures |
|---|---|---|---|---|
| `smoke` | replay | 6 | 1.0000 | effective-schema logic over seeded tenants |
| `generator` | replay, live | 19 (10 live) | 1.0000 | NL -> proposal pipeline: parsing, checks, repair |
| `mcp_attacks` | replay | 13 | 1.0000 | MCP attacks blocked (stage 2) |
| `rag` | replay, live | 14 | 0.9286 | retrieval, citation, fact; any leak scores 0 |
| `judge_calibration` | live | 10 | n/a | does the LLM judge agree with known verdicts, including 4 injection attempts |

The RAG suite's one known miss (`lost-asset-desk-t2`) is documented in `results-stage-3.md`.
`rag` and `mcp_attacks` run attacks and leak checks inside the gate, so a security regression fails CI
the same way a quality regression does. The stage 3 RAG attack matrix runs as tests (`pytest`), not as
an eval suite, because it needs Postgres for half its cells.

## What the gate checks

| Check | Offline | Live |
|---|---|---|
| No case that passed in the baseline now fails | gate | report |
| Pass rate not below baseline (tolerance 0 by default) | gate | report |
| Dataset fingerprint unchanged (ids, inputs, expectations, tags) | gate | n/a |
| No baselined case missing from the run | gate | n/a |
| Token totals within +5% of baseline | gate | report |
| Every baselined suite ran | gate | n/a |
| Cost within `--max-cost-usd` | n/a | hard stop |

Reports include p50/p95 latency and per-tag pass rates; latency is reported, never gated (it is noise
on shared CI runners).

## Does the gate catch what it claims? (end to end)

Run on a scratch copy with the real CLI:

| Change made | Gate result |
|---|---|
| Added one sentence to the generator's system prompt | **exit 1**: input tokens 13,024 exceed baseline 12,320 by more than 5% |
| Commented out one RAG case | **exit 1**: dataset fingerprint changed, case `laptop-days-t2` missing, pass rate below baseline |
| `--update-baseline` with no `--reason` | refused by the CLI |
| Clean copy | exit 0 |

The prompt example is real: in stage 1, a prompt edit moved tokens from 12,224 to 12,320 and was
noticed only by reading the output. Now that kind of change needs a deliberate baseline update.

## Mutation check

Each control removed in turn on a scratch copy; `tests/test_eval_harness.py` rerun. 13/13 caught.

| Control removed | Caught by |
|---|---|
| Per-case regression check | `test_case_level_regression_fails_even_if_rate_holds` |
| Dataset fingerprint check | `test_changed_dataset_fails_until_baseline_updated` |
| Missing-case check | `test_removed_cases_fail_the_gate` |
| Reason required for updates | `test_update_requires_reason` |
| Refuse to record a regression | `test_lowering_baseline_requires_accept_regression` |
| Token-growth gate | `test_token_growth_fails_the_gate` |
| Secret scrubbing in reports | `test_reports_contain_no_environment_secrets` |
| Judge prompt tag neutralising | `test_judge_candidate_is_delimited` |
| Leak gate before the judge | `test_judge_never_scores_leaks` |
| Every baselined suite must run | `test_missing_suite_fails_the_gate` |
| Live budget cap | `test_budget_stops_a_live_run` |
| Refuse unknown models | `test_unknown_model_is_refused` |
| A scorer that passes everything | `test_every_scorer_rejects_a_wrong_output[mcp_attacks]` |

## Cost

Prices (USD per million tokens, Anthropic first-party, 2026-10-06) live in `evals/pricing.py`:

| Model | Input | Output | Cache read |
|---|---|---|---|
| `claude-fable-5-1` | 10.00 | 50.00 | 0.25 |
| `claude-opus-5-5` | 4.00 | 20.00 | 0.20 |
| `claude-sonnet-5-5` | 2.00 | 10.00 | 0.20 |
| `claude-haiku-5-5` | 0.10 | 0.50 | 0.10 (upper bound; not in the source table) |

Live runs share one budget between the model under test (`METAFORGE_MODEL`, default
`claude-sonnet-5-5`) and the judge (`METAFORGE_JUDGE_MODEL`, default `claude-opus-5-5`). No call is
made once the budget is reached, so spend is bounded by the budget plus one call.

## Live results

**Run 1 (2026-10-13):** see [`results-live.md`](results-live.md). That run found that judge calibration was built with the model under test rather than the judge model; fixed, with a test. The live path is tested end to end with a fake
provider (`test_live_mode_wiring_with_a_fake_provider`): shared budget, judge, reports, no gating.
Record the first real run here: date, models, per-suite pass rate, judge calibration agreement
(overall and on the 4 adversarial items), tokens, cost.

## Known limits
- **The judge is only as good as its calibration.** 10 items is a smoke test, not a validation set.
  Grow it from real disagreements once live runs exist.
- **Replay token counts are word counts** from `FakeLLM`, good for detecting growth, not for pricing.
- **Baseline history is honour-system plus diff review.** A reason is required and recorded, but
  nothing verifies the reason is true; that is what code review of the baseline diff is for.
