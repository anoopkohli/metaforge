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

## Stage 5 (triage) — to write before building
- Agent acts on injected content in logs; agent proposes remediation outside its tenant scope.
