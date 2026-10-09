# Threat model

Written before each stage is built. Each entry lists the threat, the control, and the test that proves it.
Status: `covered` (test exists and passes), `planned` (stage noted), `accepted` (known gap, see notes).

## Assets
- Tenant metadata (custom objects, overrides) and, from stage 3, tenant documents and embeddings.
- The signing secret for auth tokens.

## Actors
- **Tenant user**: authenticated, legitimate, may be curious or hostile toward other tenants.
- **Malicious document author**: a tenant (or content ingested for one) that plants instructions or crafted text.
- **The model itself**: untrusted. It can be talked into doing things, and its output is input.

## Stage 0 (core)

| # | Threat | Control | Test | Status |
|---|---|---|---|---|
| 0.1 | Tenant reads another tenant's objects/overrides | Every store method takes an explicit `TenantContext`; every tenant_* query filters on its ID | `test_no_tenant_sees_another_tenants_canary`, `test_other_tenants_custom_object_is_invisible_by_name` | covered |
| 0.2 | Code runs with no tenant and sees everything | No default tenant; missing context raises `MissingTenantContext` | `test_every_tenant_method_refuses_a_missing_context` | covered |
| 0.3 | Forged or tampered token claims another tenant | HMAC signature over claims; constant-time compare | `test_tampered_payload_rejected`, `test_wrong_secret_rejected` | covered |
| 0.4 | Replay of an expired token | `exp` claim enforced | `test_expired_rejected` | covered |
| 0.5 | SQL injection widens a tenant query | Parameterised queries only; tenant IDs validated by regex at construction | `test_sql_metacharacters_...`, `test_tenant_id_cannot_be_smuggled_in_...` | covered |
| 0.6 | Tenant ID leaks across concurrent requests | Context carried in a `ContextVar`, restored on exit | `test_concurrent_tasks_do_not_see_each_others_tenant` | covered |
| 0.7 | Tenant defines an object that shadows a platform object | Name conflict rejected; use overrides | `test_custom_object_cannot_shadow_a_platform_object` | covered |
| 0.8 | Invalid override corrupts effective schema | Override validated against base before storing | `test_override_cannot_hide_required_or_unknown_fields` | covered |
| 0.9 | Single application bug defeats isolation (no second layer) | Postgres row-level security as a database-enforced layer | `rls-direct-sql` (stage 3, RAG chunks); the metadata store itself stays on SQLite | covered for RAG chunks |
| 0.10 | Weak or leaked signing secret | Secret from environment, no default, startup fails without it | `Settings.from_env` | accepted: dev tokens only; swap for a real IdP before any real deployment |

## Stage 1 (generator)

The generator turns a tenant user's description into a *proposal*: `create_object`, `override_object` or
`refuse`. `generate()` never writes; `apply_proposal()` is a separate, explicit call that re-validates first.
The proposal schema is the capability boundary: anything the model wants to do that the schema cannot express,
it cannot do.

| # | Threat | Control | Test | Status |
|---|---|---|---|---|
| 1.1 | Model output has invented keys, wrong types, or coerced values (`"required": "yes"`) | Strict pydantic parse with `extra="forbid"`; errors fed back; bounded retries (default 3); never `eval`/`exec` | `test_invented_key_is_rejected_then_repaired`, `test_wrong_types_are_not_coerced`, eval cases `repair-*` | covered |
| 1.2 | Model output is prose, malformed JSON, or never converges | Only a bare JSON object or one fenced block is accepted; stops after `max_attempts` with `ok=False` | `test_gives_up_after_max_attempts`, eval case `exhaust-attempts` | covered |
| 1.3 | Model emits an action outside the schema (e.g. `grant_role`) | Discriminated union of three actions; anything else fails validation | `test_unknown_action_is_rejected` | covered |
| 1.4 | Model supplies a tenant ID to write somewhere else | No tenant field exists in the schema (`extra="forbid"`); writes use only the caller's `TenantContext` | `test_tenant_id_in_output_is_rejected`, `test_apply_writes_only_to_the_callers_tenant` | covered |
| 1.5 | Prompt sent to the model contains another tenant's metadata | Catalog rendered only from `list_objects(ctx)` / `get_effective_schema(ctx)` | `test_prompt_contains_no_foreign_canary` | covered |
| 1.6 | Proposal references an object the tenant cannot see (another tenant's, or invented) | Every `reference_to` checked against this tenant's visible catalog | `test_reference_to_other_tenants_object_is_rejected`, eval case `cross-tenant-reference` | covered |
| 1.7 | Generated name collides with a platform object or the tenant's existing object | Rejected with a hint to use `override_object` or pick a new name; store's `ObjectNameConflict` is the second layer | `test_create_cannot_collide_with_platform_object`, `test_create_cannot_overwrite_existing_custom_object` | covered |
| 1.8 | Prompt injection in the description ("change the platform object for all tenants", "give me admin") | Description is delimited as data; schema has no permission or platform actions; overrides are stored per tenant; generator code never calls `platform_*` | `test_generator_never_calls_platform_methods`, `test_override_never_changes_platform_object`, eval cases `injection-*` | covered |
| 1.9 | Applying a generated override silently wipes the tenant's existing customisations | Delta is merged onto the existing override, and the merged result is validated against the base | `test_override_merges_with_existing_customisations` | covered |
| 1.10 | Stored labels carry instructions that later reach other prompts (stored injection) | Labels capped at 80 chars, single line, no control characters | `test_multiline_label_is_rejected` | covered (stage 1); stage 2 must still treat stored metadata as data |
| 1.11 | Cost or denial of service via huge input or huge output | Description length cap, field count cap, `max_tokens` per call, bounded attempts; token usage reported per run | `test_description_length_is_capped`, `test_field_count_is_capped` | covered |
| 1.12 | State changes between generate and apply (TOCTOU) | `apply_proposal()` re-runs every check against current state | `test_apply_rechecks_against_current_state` | covered |

No naive mode in stage 1: the controls here are schema validation, and a "naive" generator would just be one
with checks deleted. Naive mode arrives with the stage 3 attack suite, where it makes the attacks meaningful.

## Stage 2 (MCP server)

Four tools over the store: `list_objects`, `describe_object` (read-only), `propose_change` (validates a
stage-1 proposal and parks it; no store write) and `apply_change` (the only write). One server process
serves one tenant: the tenant comes from a signed token verified at startup (`METAFORGE_TOKEN`), never from
a tool argument. The tool logic (`mcp_server/tools.py`) has no SDK dependency; the MCP adapter is thin.

Confirmation for `apply_change` is a human approval collected by the MCP *client* (form elicitation, or
the 2026-07-28 `input_required` round trip). The server cannot see a human, so the guarantee is: no write
without a client-attested approval bound to that exact change, and no write at all if the client cannot
ask. A client that auto-approves elicitations defeats this; that is a client configuration, recorded below.

| # | Threat | Control | Test / attack | Status |
|---|---|---|---|---|
| 2.1 | Model passes `tenant_id` (or `tenant`, `tid`, `tenantId`) to reach another tenant | Tenant keys stripped before validation and recorded in the audit log; handlers only receive the session's `TenantContext` | attacks `tenant-arg-*`; `test_tenant_args_are_ignored_and_audited` | covered |
| 2.2 | Unknown or mistyped tool arguments change behaviour | Strict pydantic argument models, `extra="forbid"` (after tenant-key stripping) | `test_unknown_arguments_are_rejected` | covered |
| 2.3 | Tool results leak fields the tenant has hidden | `describe_object` returns the effective schema only | attack `hidden-field`; `test_describe_returns_effective_schema` | covered |
| 2.4 | Error messages reveal that another tenant's object exists | One not-found message for foreign and non-existent names | attack `existence-oracle` | covered |
| 2.5 | `apply_change` runs without human confirmation | Write requires a `Confirmation` object that only the adapter creates after an accepted elicitation; no elicitation support means no write | attacks `apply-without-confirmation`, `declined-confirmation`; `test_no_elicitation_support_means_no_write` | covered |
| 2.6 | Client forges or replays a confirmation (2026-07-28 flow) | `request_state` is HMAC-signed over tenant, change ID, nonce and expiry with a per-process key; checked before the response is trusted | attacks `forged-request-state`, `state-for-other-change` | covered |
| 2.7 | A pending change is applied by another tenant, applied twice, or applied after state changed | Pending changes keyed by (tenant, random ID), single-use, 10-minute TTL, re-validated at apply | attacks `foreign-change-id`, `replay-change-id`; `test_apply_revalidates` | covered |
| 2.8 | Model writes through `propose_change` | `propose_change` stores nothing in the metadata store | `test_propose_does_not_write` | covered |
| 2.9 | Stored labels carry injected instructions into the calling model | Tool results are structured JSON; labels were sanitised at write time (1.10) | `test_results_are_structured_json` | partial: the calling model still reads label text; output guard is planned for stage 3 |
| 2.10 | Pending-change flooding | At most 20 pending changes per tenant | `test_pending_changes_are_capped` | covered |
| 2.11 | Naive mode left on | Naive mode (trusts the model's `tenant_id`) needs `METAFORGE_UNSAFE_NAIVE_MCP=trust-model-tenant-arg` exactly, logs a warning on every call, and tests assert it is off by default | `test_naive_mode_is_off_by_default`, `test_naive_mode_needs_the_exact_env_value` | covered |
| 2.12 | Client auto-approves elicitations | Out of the server's control; `apply_change` is annotated `destructiveHint` so clients prompt | n/a | accepted: document client setup in README |
| 2.13 | Bug in one layer defeats isolation | Store filter is the second layer; RLS and output guard arrive in stage 3 for RAG | stage 0 isolation suite | partial: metadata store has no RLS (SQLite) |

## Stage 3 (RAG)

Tenants ingest documents; questions are answered from retrieved chunks by a model through `LLMClient`.
Two isolation strategies sit behind one `VectorIndex` interface:

- **pooled**: one shared Postgres table with pgvector. Isolation layers: (1) the application filters on
  `tenant_id` in every statement; (2) Postgres row-level security, keyed on a transaction-local
  `app.tenant_id` setting, for the non-owner application role; (3) an output guard drops any retrieved
  chunk whose tenant is not the caller's, before it reaches a prompt or a cache.
- **siloed**: one SQLite database per tenant. Isolation is structural: a tenant's handle only opens its
  own file, whose path is derived from the validated tenant ID. The output guard still applies.

Embeddings come from a deterministic offline hashing embedder (no provider yet). Naive mode
(`METAFORGE_UNSAFE_NAIVE_RAG=disable-tenant-filters`) removes the application-layer controls; it does not
switch off RLS, which is a separate, database-level fact. The attack table therefore has three columns for
pooled: secure; naive app + RLS; naive app + no RLS (table-owner connection).

| # | Threat | Control | Attack / test | Status |
|---|---|---|---|---|
| 3.1 | Query retrieves another tenant's chunks (filter missing or bypassed) | App filter on every statement; RLS (pooled); separate database (siloed); output guard | `query-victim-content`, `rls-direct-sql` | covered |
| 3.2 | Filter injection through query text | Query text is only ever embedded, never put into SQL; all SQL parameterised | `filter-injection-query` | covered |
| 3.3 | Cross-tenant poisoning: a tenant ingests text crafted to rank first for another tenant's questions | Retrieval is scoped before ranking, so foreign chunks never compete | `poison-victim-results` | covered |
| 3.4 | Ingest payload names another tenant | Tenant only from `TenantContext`; ingest schema forbids extra keys | `ingest-tenant-override` | covered |
| 3.5 | Document ID collision overwrites or deletes another tenant's document | Keys are (tenant, doc ID); every write and delete filters on tenant; RLS `WITH CHECK` | `doc-id-collision`, `delete-victim-doc` | covered |
| 3.6 | Answer cache serves one tenant's answer to another | Cache key includes tenant ID and the tenant's index version | `cache-leak` | covered |
| 3.7 | Prompt injection in a tenant's own document tries to exfiltrate other tenants' data | The model only ever receives the caller's chunks (layers above), so there is nothing foreign to exfiltrate; chunks are delimited as data | `injected-doc-exfiltration` | covered for cross-tenant; accepted: an injected document can still mislead answers *within* its own tenant |
| 3.8 | Answer cites chunks that were not retrieved, or invents sources | Structured answer validated with bounded retries; citations must be a subset of retrieved chunk IDs | `test_citations_must_come_from_retrieved_chunks` | covered |
| 3.9 | Error messages reveal that another tenant's document exists | One not-found message for foreign and missing IDs | `error-oracle` | covered |
| 3.10 | Logs carry document text, questions or canaries | Logs record IDs, counts and hashes only | `log-leak` | covered |
| 3.11 | Code runs with no tenant set and sees everything | `MissingTenantContext` in the service; with no `app.tenant_id`, RLS returns no rows | `missing-context`, `test_rls_without_tenant_setting_returns_nothing` | covered |
| 3.12 | Siloed path traversal via tenant ID | Tenant IDs validated by regex at construction; file name built from that ID only | `test_silo_paths_stay_under_root` | covered |
| 3.13 | Oversized documents or queries (cost, DoS) | Caps on document size, chunk count, question length and `k` | `test_limits` | covered |
| 3.14 | Naive mode left on | Exact env value, warning on every call, tests assert off by default | `test_rag_naive_mode_is_off_by_default` | covered |

## Stage 4 (eval harness)

The eval gate is a security control: stage 2 and 3 attacks run through it, and every AI feature's quality
claim rests on it. The threats are to its integrity: a gate that can be quietly satisfied is worse than
no gate, because it makes regressions look approved.

| # | Threat | Control | Test | Status |
|---|---|---|---|---|
| 4.1 | A regression hides inside an unchanged aggregate (one case fixed, another broken) | Baselines store per-case pass/fail; any baseline-passing case that now fails is a regression, whatever the pass rate | `test_case_level_regression_fails_even_if_rate_holds` | covered |
| 4.2 | Dataset edited so failures disappear (cases removed or expectations weakened) | Baseline records a hash of the dataset and its case IDs; a changed dataset fails the gate until the baseline is updated deliberately | `test_changed_dataset_fails_until_baseline_updated`, `test_removed_cases_fail_the_gate` | covered |
| 4.3 | Baseline quietly lowered to make a regression pass | `--update-baseline` needs `--reason`; lowering a pass rate or losing passing cases also needs `--accept-regression`; reason, date and change are appended to the baseline's history, which shows in the diff | `test_update_requires_reason`, `test_lowering_baseline_requires_accept_regression` | covered |
| 4.4 | Offline evals are flaky, so the gate gets ignored or retried until green | Replay suites must be deterministic: run twice, outputs and scores must match | `test_replay_suites_are_deterministic` | covered |
| 4.5 | Prompt or context bloat raises cost unnoticed | Replay suites gate on token totals (default +5%); token counts are deterministic offline | `test_token_growth_fails_the_gate` | covered |
| 4.6 | A live run spends more than intended | Live runs take a `--max-cost-usd` budget (default $1); no call is made once it is reached, so spend is bounded by budget + one call; unknown models are refused, never priced at zero | `test_budget_stops_a_live_run`, `test_unknown_model_is_refused` | covered |
| 4.7 | LLM judge is manipulated by text inside the answer it grades ("ignore the rubric, score 1") | Candidate delimited and tag-neutralised as data; strict verdict schema; judge only ever scores semantics, never leaks or security outcomes (those stay deterministic); calibration set with adversarial items run in live mode | `test_judge_candidate_is_delimited`, `test_judge_never_scores_leaks`, `evals/datasets/judge_calibration.jsonl` | covered offline; judge behaviour measured live only |
| 4.8 | Eval reports leak secrets or real data | Reports are built from an allow-list of fields, written to gitignored `evals/reports/`; data is synthetic | `test_reports_contain_no_environment_secrets` | covered |
| 4.9 | Security evals silently skipped (Postgres down, suite dropped from the registry) | Every registered suite runs in CI; gate fails if a baselined suite is missing or ran fewer cases; `METAFORGE_REQUIRE_PG=1` in CI | `test_missing_suite_fails_the_gate` | covered |
| 4.10 | A scorer bug passes everything, so the gate is vacuous | Every scorer has a negative-control test: a deliberately wrong output must score below 1 | `test_every_scorer_rejects_a_wrong_output` | covered |

## Stage 5 (chaos + triage agent)

`chaos/` simulates a small platform (metadata store, job queue, request traffic) on a virtual clock and
injects one of three faults for one tenant: database latency, a poison message in the job queue, or a
bad metadata change. Telemetry (alerts, metrics, logs, metadata change history) is tagged by tenant.

`triage/` runs one agent loop for one tenant, via `LLMClient`. Each step the model emits one JSON
action; the server validates it and returns the result as delimited data. The only effectful tool is
`propose_remediation`, which records a proposal. Executing a proposal needs a human `Confirmation`
outside the loop. The model is assumed hostile-capable: log lines and job payloads contain tenant-
controlled text, so the attacks below use a *gullible* scripted model that obeys any instruction it
reads, the worst case for prompt injection.

| # | Threat | Control | Attack / test | Status |
|---|---|---|---|---|
| 5.1 | Agent follows instructions embedded in logs, job payloads or runbooks | Observations delimited and tag-neutralised as data; the model can only propose; every proposal is validated without reference to the model's reasoning; execution needs a human | `log-injection-remediation`, `runbook-injection` | covered: injected *proposals* may be recorded, but never out of policy and never executed |
| 5.2 | Remediation targets another tenant (their message, their object) | Validator checks every target belongs to the session's tenant | `cross-tenant-remediation` | covered |
| 5.3 | Telemetry tools return another tenant's data | Every query filters on the session's `TenantContext`; tenant-like args stripped and audited | `cross-tenant-telemetry`, `diagnosis-exfiltration` | covered |
| 5.4 | Agent loops or floods tools (cost, denial of service) | Step cap (10), per-call output cap, bounded tool results; live runs inherit the stage 4 budget | `tool-call-flood` | covered |
| 5.5 | Remediation executed without human approval, or "approved" by text in a log | Only a `Confirmation(proposal_id)` created outside the loop executes; nothing the model says counts | `fake-approval-in-logs`, `self-approval` | covered |
| 5.6 | Remediation parameters out of bounds (scale to 10,000 workers, pool to 1e6) | Allow-listed actions with per-action bounds | `log-injection-remediation` | covered |
| 5.7 | Diagnosis cites evidence that does not exist | Evidence IDs must exist in this tenant's telemetry; proposal IDs must come from this session | `test_diagnosis_rejects_unknown_evidence` | covered |
| 5.8 | Diagnosis text carries injected content to the human reader | Summary capped, single paragraph, printable; shown as plain text | `test_diagnosis_summary_is_plain` | partial: a misleading-but-plain summary is still possible; the human approves proposals, not prose |
| 5.9 | Fault injection escapes the simulation | Faults act only on a `ChaosEnvironment` object; no global patching, no I/O | `test_chaos_has_no_global_side_effects` | covered |
| 5.10 | Naive mode left on | Exact env value, warning on every step, tests assert off by default | `test_triage_naive_mode_is_off_by_default` | covered |

## Demo web UI (`web/`)

A local web app over stages 1-5: log in as a synthetic tenant, browse and change metadata, ask the
documents, run an incident, and run the attack suites. It is a demo surface, but it is an HTTP
surface, so it gets the same treatment as the rest.

| # | Threat | Control | Test | Status |
|---|---|---|---|---|
| W.1 | Tenant taken from a request body, query or header | Tenant only from a signed session cookie (the stage 0 HMAC token); request bodies are strict models with no tenant field | `test_tenant_in_body_is_rejected`, `test_data_is_scoped_to_the_session_tenant` | covered |
| W.2 | Forged or tampered session | HMAC token, `HttpOnly`, `SameSite=Strict`, 8-hour expiry | `test_tampered_cookie_is_rejected` | covered |
| W.3 | Cross-site request forgery | `SameSite=Strict`, a custom header required on every state-changing request, and a matching `Origin` when one is sent; no CORS | `test_post_without_csrf_header_is_rejected`, `test_cross_origin_post_is_rejected` | covered |
| W.4 | Stored XSS via tenant-controlled text (labels, documents, log payloads, model output) | The front end renders data only with `textContent`; strict CSP with no inline script; `nosniff` | `test_security_headers`, `test_front_end_never_uses_innerhtml` | covered |
| W.5 | Exposure beyond this machine, DNS rebinding | Binds to 127.0.0.1; only `localhost`/`127.0.0.1` Host headers accepted | `test_unknown_host_is_rejected` | covered |
| W.6 | Naive (deliberately vulnerable) mode reachable from the UI | The security lab runs attacks in a child process; naive env values exist only in that child's environment, never in the server's | `test_lab_runs_naive_only_in_a_child_process` | covered |
| W.7 | Cost or load abuse | Input caps reused from each stage; lab and eval runs are single-flight; live model calls share a budget (`METAFORGE_UI_BUDGET_USD`, default $2) | `test_lab_is_single_flight`, `test_ui_budget_is_enforced` | covered |
| W.8 | Errors leak internals | Generic error responses; details go to the server log only | `test_errors_are_generic` | covered |
| W.9 | API key reaches the browser | The browser only learns whether live mode is on and the model name | `test_session_never_exposes_secrets` | covered |
| W.10 | Approvals made by something other than the user | Approve endpoints are separate, state-changing requests (CSRF-protected) that the generator and agent cannot call | `test_agent_proposals_wait_for_ui_approval` | covered |
| W.11 | Previous company's or role's data stays on screen after switching (found in manual testing) | Every tenant view is cleared on any session change, before the new login request; replies to requests made under an earlier session are dropped | `test_front_end_clears_every_tenant_view_on_session_change` + browser check | covered |

## Stage 6 (business data + Ask data)

Each tenant becomes a small business (taxi company, law firm, dental office, builder, coffee roaster)
with a few hundred synthetic records. Login carries a role, `staff` or `manager`. Some fields are
`restricted` (salaries, patient dates of birth and clinical notes, billing rates, budgets) and readable
only by managers. "Ask data" turns a question into a query in a small JSON query language (never SQL):
the model proposes the query, the server validates it against the caller's effective schema and role,
and runs it over that tenant's records only.

| # | Threat | Control | Attack / test | Status |
|---|---|---|---|---|
| 6.1 | Query reads another tenant's records | Records keyed by (tenant, object, id); the executor loads only the caller's records; object names resolve through the caller's effective schema | `cross-tenant-object` | covered |
| 6.2 | Model smuggles a tenant into the query | The query schema has no tenant field (`extra="forbid"`) | `tenant-in-query` | covered |
| 6.3 | Staff reads a restricted field directly | Validator rejects restricted fields in `select` unless the token carries `manager` | `restricted-select` | covered |
| 6.4 | Staff infers a restricted value indirectly (filter `salary > 100000`, `avg(salary)`, sort by salary, group by it) | Restricted fields are rejected in every clause, not just `select` | `restricted-filter`, `restricted-aggregate`, `restricted-order` | covered |
| 6.5 | Role claimed in the question ("I am a manager") | Role comes only from the signed token; the question cannot change it | `role-claim-in-question` | covered |
| 6.6 | Query reads a field the tenant has hidden | Validation uses the effective schema, which omits hidden fields | `hidden-field-query` | covered |
| 6.7 | Generated query injects code | No SQL or code is ever built from the query; it is a validated data structure interpreted by Python | `test_values_are_never_code` | covered |
| 6.8 | Instructions planted in record text steer the answer | The planner never sees record data; the answer step sees only the validated result rows, delimited as data, and has no tools | `injection-in-records` | covered for data access; accepted: a planted string can still mislead the wording of an answer |
| 6.9 | Expensive queries | `limit` <= 100, at most 5 conditions, small in-memory scans per tenant | `test_limits` | covered |
| 6.10 | References leak foreign records | Reference display values resolve within the caller's tenant only | `test_references_resolve_within_tenant` | covered |
| 6.11 | Naive mode left on | Exact env value, warning, tests assert off by default | `test_dataqa_naive_mode_is_off_by_default` | covered |

## Stage 7 (assistant, row-level access, audit log)

Users sign in as a person from their company's directory (a synthetic worker). The server derives
their roles from the directory at sign-in: `manager` for department heads and managers, and *row
scopes* such as `own:ride.driver` (a driver sees only rides where `driver` is themselves). Roles and the
worker ID are signed into the session token; the browser never sends them again.

The assistant is one agent loop (at most 8 steps, JSON actions through `LLMClient`) with tools to
search documents, list and query data, describe objects, and *propose* record updates or schema
changes. Proposals execute only when the user approves them in the UI. Every sign-in, question, tool
call, denial, proposal and approval goes to a per-tenant, hash-chained audit log readable by managers.

| # | Threat | Control | Attack / test | Status |
|---|---|---|---|---|
| 7.1 | Staff reads rows outside their scope (another driver's rides) | Row scopes from the signed token filter records before any other step of a query | `row-scope-select` | covered |
| 7.2 | Out-of-scope rows inferred through counts, sums or averages | Aggregates run over in-scope rows only | `row-scope-aggregate` | covered |
| 7.3 | Assistant passes a tenant, user or role in tool arguments | Tool arguments are strict models with none of these fields; identity comes only from the session | `assistant-identity-args` | covered |
| 7.4 | Assistant changes data without approval, or text claims approval | Only `propose_*` tools exist; execution needs the user's approval request, which the agent cannot make | `assistant-self-approval`, `fake-approval-in-document` | covered |
| 7.5 | Proposed update targets a row outside scope, a restricted field, an unknown field or another tenant's record | Updates validated against the effective schema, field restrictions and row scope, at proposal and again at approval | `update-out-of-scope-row`, `update-restricted-field` | covered |
| 7.6 | Instructions planted in documents or records steer the assistant | Observations are delimited data; every tool enforces scope itself; writes need approval | `injected-document-update`, `injected-record-exfiltration` | covered for access and writes; accepted: planted text can still mislead an answer's wording |
| 7.7 | Audit log edited, deleted or reordered after the fact | Per-tenant hash chain over each entry and the previous hash; `verify` recomputes it and reports the first broken entry | `test_tampering_is_detected` | covered (detects, does not prevent: anyone with database access can still edit; external anchoring is future work) |
| 7.8 | Staff read the audit log (other people's questions) or another tenant's log | Audit reads are tenant-scoped and need `manager` | `audit-read-by-staff` | covered |
| 7.9 | Actions happen without an audit entry | One dispatcher logs every tool call, denial, proposal and approval; tests assert one entry per step | `test_every_step_is_audited` | covered |
| 7.10 | Audit log stores restricted values | Entries hold actions, object and field names, counts and outcomes, never record values | `test_audit_never_stores_restricted_values` | covered |
| 7.11 | Agent loops or floods tools | 8-step cap; result rows capped; live runs share the stage 4 budget | `assistant-tool-flood` | covered |
| 7.12 | Anyone can sign in as anyone | Accepted for the demo: the person picker stands in for an identity provider | n/a | accepted |
| 7.13 | Naive mode left on | Exact env value, warning on every step, tests assert off by default | `test_assistant_naive_mode_is_off_by_default` | covered |
