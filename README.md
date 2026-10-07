# metaforge

A synthetic, multi-tenant, metadata-driven platform used as a testbed for AI engineering: structured
generation, tool use (MCP), multi-tenant RAG, evals, and incident triage. Built in stages with AI coding tools,
with tests and a threat model written first so the AI-generated code is checked rather than trusted.

> Personal project. Uses synthetic data only and shares no code, schemas or designs with any employer.

## Stages

Each stage is a git tag and is useful on its own.

| Stage | What | Status |
|---|---|---|
| 0 | Core: tenant store, token auth, `TenantContext`, LLM adapter, tracing, synthetic data, thin eval runner | done (`stage-0`) |
| 1 | `generator/`: natural language to a validated, tenant-scoped metadata proposal; repair loop; golden-set evals (replay + live) | done (`stage-1`) |
| 2 | `mcp_server/`: tenant-scoped MCP tools over the store | planned |
| 3 | `rag/` + `attacks/`: multi-tenant RAG, two isolation strategies, canary-based leak attacks | planned |
| 4 | `evals/`: mature harness with cost/latency tracking and a CI regression gate | planned |
| 5 | `triage/` + `chaos/`: fault injection and an incident-triage agent | planned |

## Quick start

```bash
make install
make test      # offline, no API key needed
make evals     # smoke eval + baseline gate
make seed      # demo tenants into ./metaforge.db
```

Python 3.11+. The only runtime dependency is `pydantic`. Provider SDKs and OpenTelemetry are optional extras
(`.[llm]`, `.[otel]`).

## The core idea

A tenant ID comes only from a `TenantContext` built from a verified token. Never from a request body, and never
from a model. There is no default tenant, and every store method needs a context.

```python
from core.auth import issue_token, verify_token
from core.store import MetadataStore
from core.synthetic import seed_demo

store = MetadataStore(":memory:")
tenants = seed_demo(store)  # 5 synthetic tenants, each with a unique canary

ctx = verify_token("dev-secret", issue_token("dev-secret", tenants[0].tenant_id))
print(store.list_objects(ctx))  # only this tenant's view
```

Leaks are detected objectively: every tenant's data contains a unique canary string. If a call made as tenant A
returns tenant B's canary, the test fails. See `tests/test_store_isolation.py`.

## Stage 1: the generator

```bash
export METAFORGE_AUTH_SECRET=dev-only-secret ANTHROPIC_API_KEY=...
pip install -e ".[llm]"
make seed
TOKEN=$(python -m core.cli token fjord)
python -m generator --token "$TOKEN" "Track company vehicles: plate number, model year, assigned worker"
python -m generator --token "$TOKEN" "Add a badge number to workers" --apply
```

The model's reply is untrusted: it is parsed strictly, checked against this tenant's own catalog, repaired
with feedback at most 3 times, and written only on `--apply`, which re-checks first. Results and the
mutation check are in [`docs/results-stage-1.md`](docs/results-stage-1.md).

## Docs

- [`CLAUDE.md`](CLAUDE.md): standing rules for the AI coding tools working in this repo
- [`docs/architecture.md`](docs/architecture.md): data model and design decisions
- [`docs/threat-model.md`](docs/threat-model.md): threats, controls, and the tests that cover them
- [`docs/results-stage-1.md`](docs/results-stage-1.md): stage 1 eval results
- [`docs/ai-assist-log.md`](docs/ai-assist-log.md): where AI-generated code was wrong and what caught it
