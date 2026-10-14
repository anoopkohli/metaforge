# metaforge

[![ci](https://github.com/anoopkohli/metaforge/actions/workflows/ci.yml/badge.svg)](https://github.com/anoopkohli/metaforge/actions/workflows/ci.yml)

**A multi-tenant app with seven AI features, built with AI coding tools, where the AI is treated as
untrusted: both the AI inside the app and the AI that wrote the code.**

Each company's data must never reach another company, a driver must never see another driver's rides,
and no AI, however badly it is fooled, may act beyond the signed-in person's rights or without their
approval. Every one of those claims is an attack in the repo that runs on every push.

| | |
|---|---|
| ![A driver asks the assistant about their rides and sees only their own](docs/images/1-assistant-driver.png) | ![A dispatcher asks for average salaries and is denied](docs/images/2-ask-data-denied.png) |
| A driver asks the assistant: it sees only their 21 rides. | Staff ask for salaries: denied in every clause, not only the select. |
| ![An AI fooled by a planted log line proposes scaling to 5,000 workers and is blocked](docs/images/3-fooled-ai-blocked.png) | ![The audit log with its hash chain verified](docs/images/4-audit-verify.png) |
| An AI that obeys a planted instruction: out-of-policy fix blocked, smuggled tenant ignored. | Every step is in a hash-chained audit log; Verify finds edits. |

## In numbers

- **53 attacks** across tool use (MCP), document Q&A (RAG), an incident agent, data questions and the
  assistant. All blocked in the secure code; the same attacks succeed against a deliberately unsafe
  build, which proves each attack can actually detect a failure.
- **552 offline tests**, plus Postgres row-level-security tests that CI runs against a real database.
- **8 eval suites** behind a per-case regression gate (a judge-calibration set among them) that also
  fails on prompt growth and on datasets edited without a reasoned baseline update.
- **About 65 safeguard-removal (mutation) checks:** a safeguard was deleted on a scratch copy to confirm
  some test fails. Where none did, a test was added; those cases are in the log below.
- **35 logged AI mistakes** in [`docs/ai-assist-log.md`](docs/ai-assist-log.md).

## How it was built with AI

I directed AI coding tools (Claude Code) under standing rules in [`CLAUDE.md`](CLAUDE.md): write the
threat model before each stage, write the attack or eval before the code, keep a naive mode that the
attacks must break, and log every time the AI's code was wrong. A sample of what that log caught:

- **A UI tenant leak.** After switching companies, the previous company's answer stayed on screen.
  Found by using the app; every tenant view is now cleared on a switch, with a test.
- **Tests that could not fail.** Mutation checks found attacks that passed even with the safeguard
  removed, and a missing test for re-checking the approver's rights at approval time.
- **A failing run committed as green.** A command checked `tail`'s exit code instead of pytest's.

Three lessons from doing it this way:

1. **The AI writes plausible tests that cannot fail.** Deleting the safeguard and watching a test go
   red is the only way I found to know.
2. **The tenant must never come from anything the model can touch.** It comes from the signed token
   alone; tenant arguments in tool calls are ignored, and every layer re-checks.
3. **Show the attack failing first.** An unsafe mode that the attacks visibly break is what makes
   "blocked" mean something.

## Architecture

```mermaid
flowchart LR
  U[Browser / MCP client] -->|signed token| A[Auth: TenantContext + person + roles]
  A --> F[AI features: generator, MCP tools, RAG, Ask data, assistant, triage agent]
  F -->|all model calls| L[LLMClient: budget, tracing, validated output]
  F -->|context passed explicitly| S[(Stores: SQLite metadata + business data,<br/>Postgres + pgvector with forced RLS)]
  F --> G[Output guard: canaries, restricted fields, row scope]
  F --> AU[(Audit log: per-tenant hash chain)]
  F -.->|proposals only| H[Human approval, re-checked with the approver's rights]
```

## Honest limits

- A testbed with synthetic data and a demo person-picker instead of a real identity provider.
- Postgres row-level security covers RAG documents; the core metadata store is SQLite.
- **Not yet run against a live model.** Offline runs use recorded or scripted stand-ins; the live,
  budget-capped run is wired up (`make evals-live`) but its numbers are not in yet.
- Every attack written so far is blocked. That is strong evidence, not a guarantee against attacks not
  yet written.

> Personal project. Uses synthetic data only and shares no code, schemas or designs with any employer.

Licensed under the [MIT License](LICENSE).

## Stages

Each stage is a git tag and is useful on its own.

| Stage | What | Results |
|---|---|---|
| 0 | Core: tenant store, token auth, `TenantContext`, LLM adapter, tracing, synthetic data, thin eval runner | `stage-0` |
| 1 | `generator/`: natural language to a validated, tenant-scoped metadata proposal; repair loop; golden-set evals | [stage 1](docs/results-stage-1.md) |
| 2 | `mcp_server/` + `attacks/`: tenant-scoped MCP tools, human-confirmed writes, 13 attacks | [stage 2](docs/results-stage-2.md) |
| 3 | `rag/`: multi-tenant RAG, pooled (Postgres + RLS) vs siloed isolation, 12 attacks × 6 configurations | [stage 3](docs/results-stage-3.md) |
| 4 | `evals/`: per-case regression gate, dataset fingerprints, reasoned baseline updates, cost tracking, LLM judge | [stage 4](docs/results-stage-4.md) |
| 5 | `chaos/` + `triage/`: three simulated faults, one triage agent loop, injection attacks with a fully obedient model | [stage 5](docs/results-stage-5.md) |
| 6 | `core/business.py` + `dataqa/`: five synthetic businesses, roles, restricted fields, Ask data (JSON queries, never SQL) | [stage 6](docs/results-stage-6.md) |
| 7 | `assistant/` + `core/audit.py`: one assistant, sign-in as a person, row-level access, hash-chained audit log | [stage 7](docs/results-stage-7.md) |

## Try it in a browser

```bash
make install
make ui        # http://127.0.0.1:8765
```

A local web app over every stage: log in as one of five synthetic companies, draft a data change
with AI and approve it, ask the company's documents, break the simulated platform and watch an AI
triage it (including one that obeys planted instructions), run every attack in secure vs unsafe mode,
and run the eval gate. It works offline with stand-in models; set `ANTHROPIC_API_KEY` first to use
Claude (budget-capped by `METAFORGE_UI_BUDGET_USD`, default $2). `make pg-up` first adds the Postgres
row-level-security configurations to the security lab.

![The security lab: each attack blocked in the secure code and succeeding in the unsafe build](docs/images/5-security-lab.png)

The UI is held to the same standard as the rest: tenant from a signed `HttpOnly`/`SameSite=Strict`
cookie only, CSRF header checks, strict CSP with no inline script, all data rendered as text,
localhost only, and unsafe mode confined to a child process (threat model entries W.1-W.11).

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

## Stage 4: the eval harness

Every push runs every offline suite through a gate that fails on any individual case regressing, on a
dataset being edited without a baseline update, and on prompt growth (+5% tokens). Changing a baseline
needs a written reason, and recording a regression needs `--accept-regression`; both show in the diff.
Live runs are budget-capped and use an LLM judge that grades meaning only, after deterministic leak
checks. Results, the end-to-end gate checks and the mutation table: [`docs/results-stage-4.md`](docs/results-stage-4.md).

```bash
make evals                                                   # gated, offline
python -m evals.run --update-baseline --reason "why"         # deliberate change
python -m evals.run --live --max-cost-usd 1.00               # needs ANTHROPIC_API_KEY
```

## Stage 5: chaos and the triage agent

A deterministic simulation injects a database-latency, queue-backlog or bad-metadata fault for one
tenant. The triage agent (one loop, at most 10 steps) reads that tenant's alerts, metrics, logs,
metadata changes and runbooks, proposes a fix, and writes a diagnosis. It cannot execute anything:
a human approves, and the eval checks the alert actually clears.

Logs carry tenant-controlled text, so the attacks use a scripted model that obeys every instruction it
reads. Even then, no out-of-policy, cross-tenant or unapproved action gets through; in naive mode all
eight attacks succeed. Details: [`docs/results-stage-5.md`](docs/results-stage-5.md).

## Stage 6: business data and Ask data

The five tenants are now a taxi company, a law firm, a dental office, a builder and a coffee roaster,
with synthetic staff (department, title, badge number, salary) and a few hundred business records each.
Logins carry a role; salaries, patients' dates of birth, billing rates and similar fields are restricted
to managers. Ask data turns a question into a small JSON query the server validates against the
company's schema and the caller's role, in every clause: staff cannot select, filter, average or sort
by a salary. Details: [`docs/results-stage-6.md`](docs/results-stage-6.md).

## Stage 7: the assistant, row-level access and the audit log

Sign in as a person from a company's directory; their roles come from the directory. Some jobs see
only their own rows (a driver's own rides, a hygienist's own appointments), in lists, counts and
averages alike. One assistant searches documents, runs as many data queries as it needs, and proposes
record or schema changes, always as the signed-in person; nothing changes until they approve. Every
sign-in, question, step, denial, proposal and approval goes to a per-company, hash-chained audit log
that managers can read and verify. Details: [`docs/results-stage-7.md`](docs/results-stage-7.md).

## Docs

- [`CLAUDE.md`](CLAUDE.md): standing rules for the AI coding tools working in this repo
- [`docs/architecture.md`](docs/architecture.md): data model and design decisions
- [`docs/threat-model.md`](docs/threat-model.md): threats, controls, and the tests that cover them
- [`docs/results-stage-1.md`](docs/results-stage-1.md): stage 1 eval results
- [`docs/results-stage-2.md`](docs/results-stage-2.md): stage 2 attack results
- [`docs/results-stage-3.md`](docs/results-stage-3.md): stage 3 attack matrix and RAG eval
- [`docs/results-stage-4.md`](docs/results-stage-4.md): stage 4 eval gate
- [`docs/results-stage-5.md`](docs/results-stage-5.md): stage 5 triage attacks and eval
- [`docs/results-stage-6.md`](docs/results-stage-6.md): stage 6 Ask data attacks and eval
- [`docs/results-stage-7.md`](docs/results-stage-7.md): stage 7 assistant, row access and audit
- [`docs/ai-assist-log.md`](docs/ai-assist-log.md): where AI-generated code was wrong and what caught it
- [`docs/demo-script.md`](docs/demo-script.md): a 90-second walkthrough of the UI
