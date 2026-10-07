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
| 0.9 | Single application bug defeats isolation (no second layer) | Postgres row-level security as a database-enforced layer | n/a | planned (stage 3) |
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

## Stage 2 (MCP server) — to write before building
- Model supplies a different tenant ID as a tool argument (server must inject and ignore it).
- Tools that mutate (`propose_change`) invoked without confirmation.
- Over-broad tool results leaking fields a tenant has hidden.

## Stage 3 (RAG) — to write before building
- Filter bypass, prompt injection, cross-tenant poisoning, embedding-space attacks, ingest-path leaks,
  cache leaks, error/log leaks. See the attack catalogue in `attacks/`.

## Stage 5 (triage) — to write before building
- Agent acts on injected content in logs; agent proposes remediation outside its tenant scope.
