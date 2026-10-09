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

## Stage 1 generator

```
description ─► prompt (this tenant's catalog only) ─► LLMClient ─► strict parse ─► semantic checks
                                                         ▲                              │ errors
                                                         └──── feedback, ≤ 3 attempts ◄─┘
                                                                                        │ ok
                                              proposal (dry run) ─► apply_proposal: re-check ─► store(ctx)
```

- **Proposal, not mutation.** The model can only emit `create_object`, `override_object` or `refuse`.
  The schema is the capability boundary; there is no tenant field and no permission action.
- **Strict parsing.** `extra="forbid"` and pydantic strict mode: `"required": "yes"` is an error, not `True`.
- **Checked against the caller's view.** References, name collisions and override validity are checked
  against `list_objects(ctx)`. Foreign and non-existent objects get the same error, so errors are not an
  existence oracle.
- **Overrides merge.** `put_override` replaces a tenant's override wholesale, so the generator merges the
  proposed delta onto the existing override before validating and writing.
- **Generate and apply are separate.** `apply_proposal` re-runs every check against current state.
- **Two eval modes on one dataset.** `replay` (FakeLLM, CI-gated) tests the pipeline; `live` tests the model.

## Stage 2 MCP server

```
MCP client ──stdio──► server.py (low-level SDK Server) ──► tools.py (TenantTools) ──► store(ctx)
                         │  ctx fixed at startup from METAFORGE_TOKEN
                         │  apply_change: elicit / input_required + signed request_state
                         └─ Confirmation(change_id) ──► only then: apply_proposal (stage 1 checks)
```

- **Low-level SDK server, not `MCPServer`.** The high-level server silently drops unknown arguments
  before the handler runs. The low-level one hands over raw arguments, so a smuggled `tenant_id` can be
  stripped *and audited*, and the remaining arguments validated strictly by our own models.
- **SDK-free core.** `tools.py` has no MCP import; the adapter is ~150 lines. Tool logic is tested
  without the SDK, the adapter through a real in-process client, and the stdio entry point as a subprocess.
- **Propose, then apply.** `propose_change` reuses the stage-1 proposal schema and checks, and parks the
  result under (tenant, random ID). `apply_change` is the only write.
- **Confirmation is a capability object.** The tool layer writes only when handed `Confirmation(change_id)`.
  Only the adapter makes one, after an accepted elicitation; the model cannot construct it.
- **Both protocol eras.** 2025-11-25 uses server-to-client elicitation. 2026-07-28 has no back-channel,
  so the server returns `input_required`; its `request_state` is HMAC-signed over tenant, change ID and
  expiry because the client echoes it back.
- **One tenant per process (stdio).** HTTP with per-request tokens waits for the web framework decision.

## Stage 3 multi-tenant RAG

```
ingest(ctx, doc) ─► chunk ─► Embedder ─► VectorIndex.upsert(ctx, ...)
answer(ctx, q) ─► cache[(tenant, version, hash(q), k)] ─► Embedder ─► VectorIndex.search(ctx, ...)
                 ─► output guard (drop chunk.tenant_id != ctx) ─► LLMClient ─► validate citations
VectorIndex = PooledPgIndex  (shared table: WHERE tenant_id + RLS on app.tenant_id, non-owner role)
            | SiloIndex      (<root>/<tenant_id>.sqlite, owner recorded in the file)
```

- **Two strategies, one interface.** Same service, same attacks, so the comparison is like for like.
- **RLS keyed on a transaction-local setting.** `set_config('app.tenant_id', ..., true)` in every
  transaction; unset means no rows. The app role is not the owner, not superuser, `NOBYPASSRLS`, and the
  table uses `FORCE ROW LEVEL SECURITY`.
- **Every layer tested alone.** The guard masks a missing app filter from the attacks, so the app filter
  has its own owner-connection test, and the guard its own leaky-index test.
- **Cache scoped by tenant and index version.** Version bumps on every ingest and delete for that tenant.
- **Offline embedder.** Feature-hashed unigrams and bigrams (sha256, not `hash()`), behind `Embedder`.
- **Project-local Postgres.** `scripts/pg.sh` runs Homebrew Postgres from `.pgdata/` on a unix socket,
  with no background service; CI uses a `pgvector/pgvector:pg17` service container.

## Stage 4 eval harness

```
suites.py (registry) ─► run_suite ─► summarize ─► gate.check(baseline) ─► exit code
     │                       │            │                │
 negative controls     MeteredLLM     report.py        update_baseline(reason,
 (scorer self-test)   + Ledger cap   (scrubbed)        accept_regression) + history
```

- **Per-case baselines.** A baseline records every case's pass/fail, so a fix in one case cannot hide
  a break in another.
- **Dataset fingerprint.** Hash of ids, inputs, expectations and tags, order-independent. Editing a
  dataset is fine; doing it without a baseline update is not.
- **Tokens gated offline only.** Replay token counts are deterministic, so growth is a real signal.
  Live counts vary, so they are reported, not gated.
- **Judge after determinism.** Leak and isolation checks never go to a model. The judge sees the
  candidate as delimited data and fails closed.
- **One registry.** CLI, CI and tests read `evals/suites.py`, so a suite cannot be half-registered.

## Stage 5 chaos + triage

```
ChaosEnvironment (virtual clock) ── inject(fault, tenant) ──► metrics / logs / changes (tenant-tagged)
        ▲                                                            │
        │ approve(ctx, id, Confirmation)  ◄── human                  ▼
TriageTools(ctx-scoped) ◄── JSON action ── LLMClient ◄── <observation> (data) ── run_triage (≤10 steps)
        └─ propose_remediation: allow-list + bounds + tenant check ─► Proposal (status: proposed)
```

- **One loop, JSON actions.** `LLMClient` has no native tool calling, so each reply is one JSON
  action; this keeps every model call behind the same interface as stages 1-4.
- **Propose, never execute.** The same capability-object pattern as stage 2: execution needs a
  `Confirmation` the model cannot create.
- **Test against the worst model.** The gullible policy obeys every injected instruction, so the
  attacks measure the controls. Resistance to in-policy suggestions is a model property, measured live.
- **Runbook facts drive the simulation.** Thresholds and sizes come from each tenant's synthetic
  runbooks, so retrieving the runbook genuinely helps.

## Stage 6 business data + Ask data

- **Records are tenant rows in the store** (`tenant_records`, keyed by tenant, object, id) and are read
  only via `list_records(ctx, ...)`.
- **Restricted is a field property** (`FieldDef.restricted`), enforced by the reader (`dataqa`), with the
  role taken from the token (`TenantContext.roles`).
- **A query language, not SQL.** `dataqa.Query` is a strict pydantic model interpreted in Python. Nothing
  the model writes becomes code; validation uses the effective schema, so hidden and foreign fields and
  objects simply do not exist.
- **The planner never sees data.** It gets the schema and the question; only the validated result rows
  reach the (optional) answer model.

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
- Web framework for the HTTP surface. Stage 2 shipped stdio only; FastAPI is still the likely choice
  when an HTTP surface is needed (stage 3 RAG API or MCP over streamable HTTP).
- Embedding provider. Stage 3 uses an offline hashing embedder behind the `Embedder` interface.
