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
| 2 | `mcp_server/` + `attacks/`: tenant-scoped MCP tools, human-confirmed writes, 13-attack suite (secure vs naive) | done (`stage-2`) |
| 3 | `rag/` + `attacks/`: multi-tenant RAG, pooled (Postgres + RLS) vs siloed isolation, 12 attacks × 6 configurations | done (`stage-3`) |
| 4 | `evals/`: mature harness with cost/latency tracking and a CI regression gate | planned |
| 5 | `triage/` + `chaos/`: fault injection and an incident-triage agent | planned |

## Quick start

```bash
make install
make test      # offline, no API key needed
make evals     # smoke eval + baseline gate
make seed      # demo tenants into ./metaforge.db
```

Python 3.11+. The only core runtime dependency is `pydantic`. The MCP SDK, provider SDKs and OpenTelemetry are optional
extras (`.[mcp]`, `.[pg]`, `.[llm]`, `.[otel]`); `.[dev]` includes the MCP SDK and psycopg so their
tests run in CI.

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

## Stage 2: the MCP server

Four tools (`list_objects`, `describe_object`, `propose_change`, `apply_change`) for one tenant per
server process. The tenant comes from a signed token, never from a tool argument. `apply_change` asks
the user to approve the exact change through the MCP client and writes nothing without that approval.

Add it to Claude Code (use absolute paths; `METAFORGE_DB` should be the store `make seed` created):

```bash
export METAFORGE_AUTH_SECRET=dev-only-secret
make seed
claude mcp add metaforge \
  -e METAFORGE_AUTH_SECRET=dev-only-secret \
  -e METAFORGE_DB="$PWD/metaforge.db" \
  -e METAFORGE_TOKEN="$(python -m core.cli token fjord)" \
  -- "$PWD/.venv/bin/python" -m mcp_server
```

Keep approvals manual: the confirmation step is only as strong as the client's approval prompt.
Results, attack table and mutation check: [`docs/results-stage-2.md`](docs/results-stage-2.md).

## Stage 3: multi-tenant RAG

Two isolation strategies behind one interface: **pooled** (one Postgres + pgvector table, application
filter + forced row-level security + output guard) and **siloed** (one SQLite database per tenant).
The attack suite runs every attack in six configurations and asserts the outcome of each cell.

```bash
brew install postgresql@17 pgvector   # once
make pg-up                            # project-local Postgres in .pgdata/ (socket only, no TCP)
make test                             # pg tests skip if Postgres is down; CI requires them
```

The headline result: with correct application code but no RLS, one raw query that forgets the tenant
filter leaks every tenant; with RLS, the same bug is contained, and RLS even absorbs a fully naive
application except for its cache. Full matrix: [`docs/results-stage-3.md`](docs/results-stage-3.md).

## Docs

- [`CLAUDE.md`](CLAUDE.md): standing rules for the AI coding tools working in this repo
- [`docs/architecture.md`](docs/architecture.md): data model and design decisions
- [`docs/threat-model.md`](docs/threat-model.md): threats, controls, and the tests that cover them
- [`docs/results-stage-1.md`](docs/results-stage-1.md): stage 1 eval results
- [`docs/results-stage-2.md`](docs/results-stage-2.md): stage 2 attack results
- [`docs/results-stage-3.md`](docs/results-stage-3.md): stage 3 attack matrix and RAG eval
- [`docs/ai-assist-log.md`](docs/ai-assist-log.md): where AI-generated code was wrong and what caught it
