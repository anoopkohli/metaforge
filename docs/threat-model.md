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

## Stage 1 (generator) — to write before building
- Model output with invented keys, wrong types, or reference to objects that do not exist.
- Prompt injection through the user's description ("also grant yourself ...").
- Generated object names colliding with platform objects.

## Stage 2 (MCP server) — to write before building
- Model supplies a different tenant ID as a tool argument (server must inject and ignore it).
- Tools that mutate (`propose_change`) invoked without confirmation.
- Over-broad tool results leaking fields a tenant has hidden.

## Stage 3 (RAG) — to write before building
- Filter bypass, prompt injection, cross-tenant poisoning, embedding-space attacks, ingest-path leaks,
  cache leaks, error/log leaks. See the attack catalogue in `attacks/`.

## Stage 5 (triage) — to write before building
- Agent acts on injected content in logs; agent proposes remediation outside its tenant scope.
