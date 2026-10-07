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

- 2026-10-07 · stage 0 · **Vacuous test.** `test_smoke_dataset_passes_fully` was generated asserting
  `main([]) == 0`, but `main` returns 0 whenever no baseline file exists, so the test could never fail on a bad
  dataset. Caught on review before the baseline was committed. Fix: assert `report.pass_rate == 1.0` directly and
  keep a separate test for the CLI-vs-baseline path.
- 2026-10-07 · stage 0 · **Environment, not code.** First install failed because the sandbox's egress policy
  blocks PyPI (HTTP 403 on CONNECT). The code was not at fault; tests were run against preinstalled packages
  instead. CI on GitHub installs normally.
