# Stage 1 results: generator

Natural language -> validated metadata proposal for one tenant (`create_object`, `override_object` or
`refuse`), with a separate, re-checked `apply_proposal()`.

## How to reproduce

```bash
make test                                         # 125 tests, offline
make evals                                        # every suite, gated on evals/baselines/*.json
python -m evals.run --suite generator --live      # real model; needs ANTHROPIC_API_KEY and .[llm]
```

## Offline (replay) results — committed baseline

Dataset: `evals/datasets/generator.jsonl`, 19 cases. In replay mode the model is a `FakeLLM` playing back
recorded outputs, so these numbers measure the **validation and repair pipeline**, not model quality.

| Metric | Value |
|---|---|
| Cases | 19 (2 create, 3 override, 5 repair, 2 collision, 2 tenancy, 5 injection) |
| Pass rate | 1.00 (baseline: `evals/baselines/generator.json`) |
| Cases needing a repair attempt | 11 / 19, each rejected first for the intended reason |
| Cases that must fail | 1 (`exhaust-attempts`: stops after 3 attempts, `ok=False`) |
| Tokens (FakeLLM word-count estimate) | 12,320 in / 450 out across the suite |

First-attempt rejection reasons, checked by hand on 2026-10-07:

| Case | Rejected because |
|---|---|
| `repair-invented-key` | `object.description`: extra inputs are not permitted |
| `repair-wrong-type` | `fields.0.type`: not one of the allowed field types |
| `repair-coerced-bool` | `fields.0.required`: `"yes"` is not a boolean (strict mode, no coercion) |
| `repair-unknown-reference` | `employee` is not an object this tenant can see |
| `cross-tenant-reference` | `cinder_asset` is not an object this tenant can see (same message as a non-existent object, so no existence oracle) |
| `tenant-id-smuggle` | `tenant_id`: extra inputs are not permitted |
| `collision-platform-object` | `worker` is a platform object; hint to use `override_object` |
| `collision-existing-custom` | `fjord_asset` already exists for this tenant |
| `injection-platform-wide` | override would hide required field `hire_date` |
| `unknown-action` | `grant_role` does not match any allowed action |
| `multiline-label` | label must be a single line of printable text |

## Mutation check

On a scratch copy, each control was removed in turn and the test suite rerun. Every mutation was caught:

| Control removed | Tests that failed |
|---|---|
| Strict parsing (`strict=True`) | `test_wrong_types_are_not_coerced` |
| Override merge | `test_override_merges_with_existing_customisations`, `test_override_add_field_already_on_tenant_is_rejected` |
| Reference visibility check | `test_reference_to_other_tenants_object_is_rejected`, `test_reference_to_unknown_object_is_rejected` |
| Printable-label check | `test_multiline_label_is_rejected` (3 of 4 cases; the 81-char case is caught by the length cap) |
| Re-check at apply | `test_apply_rechecks_against_current_state` |
| Description tag neutralising | `test_description_is_delimited_as_data` |
| Existing-name check | `test_create_cannot_overwrite_existing_custom_object`, `test_apply_rechecks_against_current_state` |

## Live results

**Not run yet.** Live mode runs the 10 cases not tagged `replay-only` against a real model and writes
`evals/reports/generator-live.json` (gitignored). Record here: date, model, pass rate, mean score, total
tokens, p95 latency, and any failing case with the reason. Live runs never gate CI.

| Date | Model | Pass rate | Mean score | Tokens in / out | p95 latency (s) | Notes |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

## Known limits
- Relabelling a field the tenant added itself is rejected (`ObjectOverride.apply` only relabels base
  fields). Fine for now; revisit if the eval shows models asking for it.
- `create_object` cannot update an existing custom object. Edits arrive with `propose_change` in stage 2.
- Live expectations allow name alternatives (`plate_number|plate|license_plate`) but are still a judgment
  call; stage 4 adds an LLM-as-judge scorer for cases where exact names should not matter.
