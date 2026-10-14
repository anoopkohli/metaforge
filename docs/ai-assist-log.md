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

- 2026-10-14 · all stages · **A scripted rename overreached twice.** Renaming the HR object `worker` to
  `employee` (while keeping queue "workers") by script also renamed a local replica-count variable whose
  line did not mention queues, and silently removed the premise of `repair-unknown-reference`, whose
  first attempt referenced `employee` precisely because it did not exist. Caught by lint (undefined
  name) and by the generator replay suite (the repair never happened). Fix: the variable restored; the
  case now uses the missing object `staff_member`. Entries above this one keep the old name.
- 2026-10-14 · stage 5 · **The triage prompt never stated the summary limit.** `Diagnosis.summary` is
  capped at 500 characters, but the prompt only says "one plain paragraph". In live run 2, three runs
  reached their last step, tried to finish (the new last-step note worked) and were rejected for a
  long summary. Caught by the step errors added after run 1. Fix (before run 3): the prompt states the
  limit, taken from the model so they cannot drift (`test_prompt_states_the_summary_limit`; the
  assistant's answer limit likewise). Replies with prose around the JSON now get feedback saying
  exactly that (`json_reply_error`, tested in both loops).
- 2026-10-14 · stage 7 · **Another over-strict phrase check.** The receptionist case must contain
  "approve"; the live answer said "waiting for your approval", which does not contain it. Same class
  as the run-1 wording fixes; run 2 counts it as a miss. Fix (before run 3): "approval" also accepted.
- 2026-10-13 · stage 6 · **Grouping by a reference merged different records.** `group_by` keyed groups
  by the displayed label, so two matters titled "Ahmed: lease review" were summed as one (35 groups
  for 43 matters). No offline test had duplicate labels. Caught when a real model, answering an
  associate's question in the first live run, pointed out it could not split the total. Fix: group by
  the stored value and disambiguate clashing labels;
  `test_group_by_reference_keeps_records_with_the_same_label_apart`.
- 2026-10-13 · stage 6 · **Text filters were case-sensitive.** The live model filtered on "Dark"
  (data: "dark") and got no rows. Offline gold plans always used the stored spelling. Fix: equality
  and `in` ignore case for text; `test_text_equality_ignores_case`. Not fixed by showing record values
  to the model: the planner deliberately never sees them.
- 2026-10-13 · stage 7 · **The schema-change tool was undocumented to the model.** The assistant prompt
  said "the stage-1 proposal format", which a model in a new conversation has never seen; the live
  model tried three shapes and all were rejected. Replay passed because the recorded plans already
  used the right shape. Fix: the exact shapes are in the prompt and
  `test_prompt_schema_examples_parse_and_apply` parses them with the real parser (+26% prompt tokens,
  recorded in the baseline with that reason).
- 2026-10-13 · stages 5, 7 · **Agents ran out of steps after doing the right thing.** 4 triage cases
  and 1 assistant case proposed the correct fix, then kept going until the step cap. Fix: a "finish
  once you have proposed" rule and a last-step note on the next-to-last observation (tested in both
  loops). Whether it works is for live run 2 to show.
- 2026-10-13 · stage 4 · **Judge calibration calibrated the wrong model.** Its build was given the
  model-under-test factory, so it measured Sonnet as a judge while RAG answers are graded by Opus; it
  also reported the judge's tokens as 0. Caught while fixing the token count, by reading how the suite
  is built. Fix: suites that test the judge are built with the judge model and count its tokens;
  `test_judge_calibration_calibrates_the_judge_model_and_counts_its_tokens`.
- 2026-10-13 · stages 5, 7 · **Live reports dropped step errors,** so a failure made of unparseable
  replies could not be diagnosed. Fix: agent suite outputs keep `step_errors`.
- 2026-10-13 · stages 6, 7 · **Three eval cases were stricter or vaguer than their questions.** A gold
  query sorted when the question asked no order; two answers had to contain one exact wording ("5
  dollar", "7 scheduled"); one question ("hours on each of my matters") had a total as its expected
  answer and two fair readings. Caught by reading each live failure. Fix: the gold no longer sorts,
  equivalent phrases are listed explicitly, and the question asks for the total. Changed after seeing
  results, so each change and its reason is in the baseline diff.
- 2026-10-13 · stage 7 · **A prompt-template slip.** While adding the schema examples, the AI escaped
  braces inside an f-string substitution, which would have shown the model `{{...}}`. Caught by
  printing the rendered prompt before running the tests; the parse test would also have caught it.
- 2026-10-13 · docs · **An overclaim in a summary.** The AI told the user there were no safety
  failures "across 82 runs"; only 62 of them carry safety checks (generator and judge cases do not).
  Caught by checking every report's safety fields before writing the results doc, which says 62.
- 2026-10-13 · stage 4 · **A live run with no API key reported 0% instead of stopping.** Rerunning the
  remaining live suites from a shell without `ANTHROPIC_API_KEY` made zero calls, recorded every case
  as an error and printed `pass_rate=0.0` for four suites, which reads like a result. Caught by reading
  the per-case errors. Fix: `--live` now refuses to start without the key and writes no reports;
  `test_live_run_without_a_key_stops_before_running_anything`.
- 2026-10-13 · stage 4 · **No timeout on live model calls.** The first live eval run stalled for over
  10 minutes in the triage suite on one API request that never answered: the client used the SDK
  defaults (10 minutes per attempt, retried), so one hung connection could block a run for about half
  an hour. Not caught offline because the fakes never hang; caught by the first real run. Fix: 60 s
  timeout and at most 2 retries per request, asserted by `tests/test_anthropic_client.py`.
- 2026-10-13 · stage 4 · **The pipefail mistake, again.** The commit for the fix above went out with a
  lint error (a long docstring line): the command piped `make lint` into `tail` without `set -o
  pipefail`, so the chain saw tail's exit code. CI would have failed it. Fixed in the next commit; every
  check-then-commit chain now starts with `set -o pipefail`.
- 2026-10-09 · stage 7 · **Missing test for re-checking at approval.** The mutation "approval does not
  re-check" survived: every test approved as the same person who proposed. Fix:
  `test_approval_rechecks_the_approvers_rights` (a manager proposes a salary change; staff cannot
  approve it). Now caught.
- 2026-10-09 · stage 7 · **A failing run got committed.** A command chain checked the exit code of
  `pytest ... | tail -1` (tail's, always 0) instead of pytest's, so a run with 53 failures (local
  Postgres had stopped; `METAFORGE_REQUIRE_PG=1` correctly failed them) still committed. The code was
  fine, but the habit was wrong. Fix: `set -o pipefail` and print pytest's own exit code.
- 2026-10-09 · web UI · **"null" on screen, and a self-inflicted recursion.** The assistant view passed
  a conditional `null` to `replaceChildren`, which prints it. The same pattern existed in the data
  model view. Fixing it with a regex rewrite to a `setKids` helper also rewrote the helper's own body
  into a call to itself; caught by reading the helper before reloading the page. A test now allows
  `replaceChildren` with arguments only inside `setKids`.
- 2026-10-09 · web UI · **CSP blocked inline styles all along.** The strict CSP (no `unsafe-inline`)
  blocked `el()`'s `setAttribute("style", ...)`, so a few spacing and bar-width styles never applied
  since the UI was built. Found from a console error during the stage 7 walkthrough. Fix: set styles
  through the CSSOM (`node.style.cssText`), which the CSP permits; the policy stays strict.

- 2026-10-09 · web UI · **Tenant data left on screen after switching company (reported by the user).**
  Changing the company or role re-logged in but did not clear what was already rendered, so the
  previous company's Ask data answer (or a manager's salary table, after dropping to staff) stayed
  visible. The server never sent cross-tenant data; the leak was in the browser. None of the 33 web
  tests caught it because they test the API, not what stays on screen. Fix: one list of every
  tenant-data panel, all cleared before the new login request; replies to requests made under an
  earlier session are dropped; a test checks that list against the page so a new panel cannot be
  missed (it fails if any panel is removed from the list). Threat model W.11.

- 2026-10-09 · stage 6 · **Unrealistic generated data, twice.** The first worker generator made the
  first N workers "senior" regardless of title (a paralegal earning $262k, a dispatcher out-earning the
  dispatch lead); the second used one company-wide pay band (a law-firm office manager at $257k). Both
  were caught by printing average salary by title before writing any tests. Fix: an explicit pay range
  per job title, plus hiring weights (a taxi company is mostly drivers).
- 2026-10-09 · stage 6 · **Collisions with earlier examples.** Making fjord a taxi company gave it a
  `vehicle` object, which broke two web tests that created `vehicle` as fjord. Caught by the red run
  of the updated test file. Fix: the tests now use objects the business does not have. The stage 1
  generator example "Track company vehicles" now collides in the UI, which shows the collision check.
- 2026-10-09 · stage 6 · **Layered check, again.** Making restricted fields count only in `select`
  (a plausible first implementation) was caught by 4 clause tests and 2 attacks; the inference attacks
  (filter, aggregate, sort) exist precisely because that is the common mistake in text-to-SQL systems.

- 2026-10-08 · web UI · **Rendered `[object HTMLDivElement]`.** The answer view passed an array of
  elements to `replaceChildren`, which does not flatten arrays (the `el()` helper does). Caught by
  clicking through the UI in a browser, not by the API tests. Fix: spread the array.
- 2026-10-08 · web UI · **Demo injection aimed at the wrong company.** The planted instruction
  hard-coded `cinder` as the victim, so as cinder it was a same-company read and proved nothing. Caught
  in the browser walkthrough. Fix: always target a different company than the one logged in.
- 2026-10-08 · stage 5 · **Reference policy counted failures as progress.** After the fooled AI's
  injected (rejected) proposal, the scripted policy thought it had already proposed a fix and stopped
  without one. Caught in the browser walkthrough. Fix: decide from successful observations only; a test
  now asserts the fooled AI still proposes the real fix. Eval baselines were unaffected.
- 2026-10-08 · web UI · **Half-applied edit.** A multi-file edit script aborted on its third file
  (ruff had reformatted the target line), so a later UI change in the same script never happened, and
  I first blamed browser caching. Caught by checking the served file's content instead of assuming.
  Lesson kept: one file per edit step, and verify the artifact, not the intent.

- 2026-10-08 · stage 5 · **Simulation bug.** The first queue model let arrivals outpace normal
  capacity, so the backlog never drained even after the poison message was removed. Caught by a
  per-fault check (inject, fix, does the alert clear?) run before writing the tests. Fix: a healthy
  queue drains at a steady rate. That check became `test_fault_alerts_only_its_tenant_and_the_remedy_clears_it`.
- 2026-10-08 · stage 5 · **Attack too weak to matter.** `runbook-injection` planted a runbook that was
  almost all JSON, so the lexical retriever never returned it; the attack was "blocked" for the wrong
  reason. Caught because it also failed to succeed in naive mode. Fix: write the planted runbook to rank
  for the agent's query, as a real attacker would.
- 2026-10-08 · stage 5 · **Overlapping layers (noted, kept).** Removing tenant-argument stripping did
  not let the secure attack through: the strict argument schema rejects the unknown `tenant_id` key.
  The audit test catches the missing stripping, so each layer is still tested.

- 2026-10-08 · stage 4 · **Invented price.** The first `evals/pricing.py` gave Claude Haiku 5.5 a
  cache-read price of $0.01/M. The source table had no such figure; it was a plausible guess. Caught
  on review against the source before commit. Fix: charge cache reads at the full input price (an upper
  bound, the safe direction for a budget) and say so in the code and results.
- 2026-10-08 · stage 4 · **Threat-model claim too strong.** Entry 4.6 first said the budget stops a
  run "when it would be exceeded". A call's cost is only known after it returns, so the cap can be
  overshot by one call. Caught while implementing `MeteredLLM`. Fix: the claim now says spend is
  bounded by budget + one call.
- 2026-10-08 · stage 4 · **Weak assertion.** The live-wiring test first checked `"live spend: $0.0" in
  out`, which also passes when cost accounting is broken and spend is zero. Caught on review. Fix:
  parse the amount and assert 0 < spend < budget.
- 2026-10-08 · stage 4 · **Misread exit code.** An end-to-end gate check printed `exit=0` for a run
  that had printed a REGRESSION line: `$?` was grep's status, not the CLI's. Caught because the two
  disagreed. Rerun without the pipe: exit 1, as intended.
- 2026-10-08 · stage 4 · **Supply chain.** The newest `actions/upload-artifact` (v7.0.2) was one day
  old. Pinned v7.0.1 (April 2026) by commit SHA instead of a floating tag.

- 2026-10-08 · stage 3 · **Transaction entered twice.** The first `PooledPgIndex._scoped` entered a
  psycopg transaction by hand and returned it, and callers then used it in a `with`, entering it again.
  Caught on review before the first run. Fix: a `@contextmanager` around `conn.transaction()`, on an
  autocommit connection so those blocks are the only transactions.
- 2026-10-08 · stage 3 · **Test assumed a total order.** `test_pooled_and_silo_rank_the_same` compared
  ranked chunk IDs exactly. Two chunks tied at score 0.0; SQLite broke the tie by ID, while Postgres's
  float4 distances ordered them the other way. Caught by the first run. Fix: compare rounded scores with
  tie order normalised. The strategies do agree; the test was too strict.
- 2026-10-08 · stage 3 · **One layer masked another.** Mutation: delete the tenant filter from the pooled
  search. Nothing failed, because the output guard dropped the foreign chunks. Good defence in depth,
  but it meant the app filter had no test of its own. Fix: `test_pooled_app_filter_isolates_on_its_own`
  (owner connection, no guard). Removing filter and guard together now trips 5 attacks.
- 2026-10-08 · stage 3 · **Mutation harness mistakes, twice.** (1) The scratch copy had no `.pgdata`, so
  every pooled test failed for every mutation; spotted because every mutation produced the identical
  failure list. (2) A mutation with a literal `\n` produced a syntax error, and the harness skipped its
  reset on that path, so later mutations ran on a broken file. Fix: point the copy at the real cluster
  via DSN env vars, run an unmutated baseline first, and always reset.
- 2026-10-08 · stage 3 · **Environment.** Homebrew Postgres 17 refused to start on macOS
  ("postmaster became multithreaded during startup") without `LC_ALL`. `scripts/pg.sh` now sets it.

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
