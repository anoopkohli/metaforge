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

- 2026-10-08 · stage 2 · **Out-of-date SDK knowledge.** The plan assumed the MCP Python SDK's `FastMCP`
  API. The installed SDK is 2.3.0, where `FastMCP` was renamed `MCPServer`, types moved to `mcp_types`,
  and the new 2026-07-28 protocol has no back-channel for elicitation. Caught by reading the installed
  source and running two throwaway prototypes before writing any server code. The prototypes also showed
  that `MCPServer` silently drops unknown arguments, which is why the adapter uses the low-level `Server`.
- 2026-10-08 · stage 2 · **Vacuous attack.** `forged-request-state` sent the string `"forged-state"`, which
  the server rejects as malformed before checking the HMAC. The mutation check (HMAC check deleted) showed
  no attack failing. Fix: forge a well-formed state (right tenant, change ID and expiry) with a made-up
  MAC. The mutation is now caught.
- 2026-10-08 · stage 2 · **False-positive attack.** `tenant-arg-describe` counted a leak whenever the
  victim's object name appeared in the response, but the server's not-found error repeats the name the
  attacker typed. The secure server "failed" the attack. Caught by the first test run. Fix: for error
  messages only the victim's canary counts, since the attacker cannot know it in advance.
- 2026-10-08 · stage 2 · **Mutation harness guard.** The second mutation run added a check that the
  code under test came from the scratch copy. That check itself failed at first because macOS resolves
  temporary directories under `/private`, so the comparison never matched. Fixed by resolving the path.

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
