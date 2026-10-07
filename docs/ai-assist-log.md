# AI-assist log

Honest record of where AI-generated code was wrong, what caught it, and the fix. Newest first.
This is part of the project: the point is to show how the tools were directed and checked, not only the output.

## Format
`YYYY-MM-DD · stage · what was wrong · how it was caught · fix`

## Verification notes
- 2026-10-07 · stage 0 · **Mutation check on the isolation suite.** On a scratch copy, removed the tenant filter
  from `get_object` and `list_objects`. 8 isolation tests failed (canary sweep, name-visibility, same-name,
  delete, and all four SQL-metacharacter cases), so the suite detects a broken filter rather than passing
  vacuously. Repeat this check whenever a new read path is added.

## Entries

- 2026-10-07 · stage 1 · **Data-loss bug caught at design time.** The obvious way to apply a generated
  override is `store.put_override(ctx, target, proposal.override)`. Reading `put_override` showed that it
  *replaces* the tenant's whole override, so adding one field would silently drop the tenant's existing
  hidden fields and relabels. Caught on review of the store before writing the generator. Fix:
  `merge_override()` layers the delta onto the existing override, and the merged result is what gets
  validated and stored. Threat 1.9; `test_override_merges_with_existing_customisations`.
- 2026-10-07 · stage 1 · **Vacuous mutation check.** The first mutation run copied the repo to a scratch
  directory and ran pytest there, but the venv's editable install still imported the *original*
  `generator/`, so every mutation "survived" without being tested. Caught because the output was empty
  rather than showing failures. Fix: rerun with `PYTHONPATH` pointing at the copy and print
  `generator.__file__` to prove which code was under test. Also noted: one `sed` mutation silently did not
  apply (wrong indentation), so the rerun checks that the mutated line actually changed.
- 2026-10-07 · stage 1 · **Scorer crash on optional keys.** The first version of `proposal_matches` indexed
  `f["required"]` and `p["override"]["hide_fields"]` directly. Pydantic dumps always include them, but the
  scorer's own unit tests build smaller dicts, so it would have raised `KeyError`. Caught on review before the
  first run. Fix: `.get()` with defaults.

- 2026-10-07 · stage 0 · **Vacuous test.** `test_smoke_dataset_passes_fully` was generated asserting
  `main([]) == 0`, but `main` returns 0 whenever no baseline file exists, so the test could never fail on a bad
  dataset. Caught on review before the baseline was committed. Fix: assert `report.pass_rate == 1.0` directly and
  keep a separate test for the CLI-vs-baseline path.
- 2026-10-07 · stage 0 · **Environment, not code.** First install failed because the sandbox's egress policy
  blocks PyPI (HTTP 403 on CONNECT). The code was not at fault; tests were run against preinstalled packages
  instead. CI on GitHub installs normally.
