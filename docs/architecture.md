# Architecture

metaforge is a small synthetic platform where tenants define business objects through metadata.
It is generic by design and shares nothing with any employer system.

```
  Stage 5   Incident triage agent      uses MCP tools + RAG over runbooks
  Stage 3   Multi-tenant RAG           docs + metadata, leak-tested
  Stage 2   MCP server                 tenant-scoped tools over the store
  Stage 1   NL -> metadata generator   writes into the store
  Stage 0   Core                       tenant store, auth, observability, LLM adapter, synthetic data
  Spine     Eval harness               thin in stage 0, matured in stage 4
```

## Stage 0 data model

- **Base objects** (`base_objects`): platform-level definitions shared by all tenants, read-only to them.
- **Tenant objects** (`tenant_objects`): a tenant's own custom objects, keyed by `(tenant_id, name)`.
- **Tenant overrides** (`tenant_overrides`): a tenant's changes on top of a base object (add fields, hide
  optional fields, relabel). Validated against the base before they are stored.
- **Effective schema**: what a tenant actually sees. Custom object if one exists, otherwise base + override.

## Decisions

1. **Explicit `TenantContext` on every store call.** The context comes from `verify_token`, never from
   request data. A `ContextVar` carries it through async code (`tenant_scope`), but store methods take it as a
   parameter so a forgotten scope is a type error, not a silent global lookup.
2. **No default tenant.** Missing context is a hard error.
3. **SQLite in stage 0.** Zero-setup and offline. The interface is the thing that matters; stage 3 adds
   Postgres + pgvector with row-level security as a second, database-enforced layer.
4. **One LLM interface.** `LLMClient` with a `FakeLLM` for tests, so the suite is deterministic and free.
5. **Evals from day one.** The thin runner is committed with a baseline file; CI fails on regression.
6. **Canary strings.** Synthetic tenants carry unique canaries so leakage is detectable by string search.
7. **Hand-rolled tokens.** Stdlib HMAC tokens keep dependencies at one (pydantic). They are a stand-in for
   an identity provider and are documented as dev-only.

## Not yet decided
- Web framework for the HTTP surface (FastAPI is the likely choice at stage 2 or 3).
- Embedding model for stage 3.
