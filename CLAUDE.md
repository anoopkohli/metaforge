# CLAUDE.md — standing rules for metaforge

metaforge is a synthetic, multi-tenant, metadata-driven app platform used as a testbed for AI features
(generation, tool use, RAG, evals, incident triage). It is a personal project. It shares no code, schemas,
data or naming with any employer system.

## Build order (each stage is a git tag: `stage-0`, `stage-1`, ...)

0. `core/`        tenant store, TenantContext, auth, observability, LLM adapter, synthetic data
1. `generator/`   natural language -> validated metadata model (writes into the store)
2. `mcp_server/`  tenant-scoped MCP tools over the store
3. `rag/`         multi-tenant RAG, two isolation strategies, `attacks/` red-team suite
4. `evals/`       matured harness: datasets, scorers, cost/latency, CI regression gate
5. `triage/`      chaos faults + incident-triage agent (cap: 3 fault types, 1 agent loop)

`evals/` is a spine, not a late stage. A thin runner exists from stage 0 and grows with every stage.

## Non-negotiable rules

### Tenancy
- A tenant ID comes **only** from `TenantContext`, which is derived from the authenticated token.
  Never read it from a request body, query string, header the client controls, or an LLM prompt/output.
- No `TenantContext` means a hard error (`MissingTenantContext`). There is no default tenant.
- Every store/query function takes the `TenantContext` as an explicit parameter. Request handlers get it from
  `require_tenant()` (set by auth middleware via `tenant_scope`) and pass it down. Never add an unscoped
  "get all" path to non-test code; `platform_*` methods are for seeding and admin tooling only.
- LLM output is untrusted input. If a model is asked to call a tool, the tenant argument is injected by the
  server from `TenantContext` and ignored if the model supplies one.
- Isolation is enforced in layers: application filter, database enforcement where available, output guard.

### AI features
- Every LLM-calling feature ships with at least one eval case in the same PR/commit.
- All model calls go through `core.llm.LLMClient`. No direct provider SDK calls elsewhere.
- Structured output is validated (pydantic) with bounded retries. Never `eval`/`exec` model output.
- Tests must run offline using `FakeLLM`. Real-provider tests are marked `@pytest.mark.live` and skipped by default.

### Process
- Write the `docs/threat-model.md` entries for a stage **before** building it.
- Write the attack or eval first, then make it pass.
- Keep `docs/ai-assist-log.md` current: whenever AI-generated code was wrong, or a bug was caught by a test or
  by review, add a dated entry (what was wrong, how it was caught, the fix). Do not hide these.
- Keep a deliberately vulnerable "naive" mode where it makes the attack suite meaningful. It must be impossible
  to enable by accident (explicit env var, loud log line, tests assert it is off by default).

### Data and IP
- Synthetic data only. Generate it with `core.synthetic` using fixed seeds.
- No references to any employer, product name, internal term or internal design.
- Never commit secrets. API keys come from the environment; `.env` is gitignored.

## Conventions
- Python 3.11+, type hints everywhere, `ruff` for lint and format, `pytest` for tests.
- Small modules, explicit imports, no global mutable state except contextvars in `core.tenancy`.
- Prefer boring and readable over clever. Add a short docstring stating the invariant a module protects.
- Commits are small and staged by milestone. Tag the end of each stage.

## Commands
```
make install     # create venv and install with dev extras
make test        # offline test suite
make lint        # ruff check + format check
make seed        # create demo tenants in the local store
make up / down   # docker compose (Postgres) for later stages
```

## Definition of done for a stage
- Tests pass offline in CI.
- Eval dataset and baseline numbers committed in `docs/results-stage-N.md`.
- Threat-model section updated and attacks (if any) green in secure mode, red in naive mode.
- README stage table updated; tag created.
