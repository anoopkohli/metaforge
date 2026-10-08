# Stage 3 results: multi-tenant RAG

Tenants ingest documents and ask questions; answers come from retrieved chunks through `LLMClient`.
Two isolation strategies behind one interface:

- **pooled**: one shared Postgres 17 table with pgvector. Layers: application `tenant_id` filter, forced
  row-level security keyed on a transaction-local `app.tenant_id` for a non-owner role, and an output guard.
- **siloed**: one SQLite database per tenant (owner recorded inside and checked on open), plus the guard.

## How to reproduce

```bash
brew install postgresql@17 pgvector     # once
make pg-up                              # project-local cluster in .pgdata/, unix socket only
make test                               # 287 tests; pg tests skip if Postgres is down
METAFORGE_REQUIRE_PG=1 make test        # what CI runs: pg tests must not skip
make evals                              # smoke, generator, mcp_attacks, rag
```

CI runs the same suite against a `pgvector/pgvector:pg17` service container.

## Attack matrix

12 attacks in `attacks/rag.py`, in 6 configurations. The attacker is `fjord`, the victim `cinder`.
"Leaked" means the victim's canary reached the attacker, the attacker's chunks reached the victim, or the
victim's private document changed. Generated from real runs on 2026-10-08; every cell is also asserted
by `tests/test_attacks_rag.py` against `expected_success()` (0 mismatches).

| Attack | silo secure | silo naive | pooled secure | pooled secure, no RLS | pooled naive + RLS | pooled naive, no RLS |
|---|---|---|---|---|---|---|
| `query-victim-content` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `filter-injection-query` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `poison-victim-results` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `ingest-tenant-override` | blocked | blocked | blocked | blocked | blocked | blocked |
| `doc-id-collision` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `delete-victim-doc` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `error-oracle` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `cache-leak` | blocked | **leaked** | blocked | blocked | **leaked** | **leaked** |
| `injected-doc-exfiltration` | blocked | blocked | blocked | blocked | blocked | **leaked** |
| `log-leak` | blocked | blocked | blocked | blocked | blocked | blocked |
| `missing-context` | blocked | blocked | blocked | blocked | blocked | blocked |
| `rls-direct-sql` | n/a | n/a | blocked | **leaked** | blocked | **leaked** |

"No RLS" means the index connects as the table owner (a superuser here), which bypasses RLS.
`filter-injection-query` leaks only where nothing filters at all: the query text never reaches SQL, so
the injection itself does nothing, but with no filter any query leaks.

### What the matrix shows

1. **Secure mode blocks everything in every configuration**, except `rls-direct-sql` without RLS. That
   attack simulates an application bug (a raw query that forgets the tenant filter). With correct code
   but no database layer, one such bug leaks every tenant's data. With RLS, the same bug is contained.
2. **RLS absorbs the naive application.** With the app filters, cache scoping and output guard all
   removed, RLS still blocks every retrieval and write attack. Only `cache-leak` gets through, because
   the cache sits in front of the database.
3. **Siloing removes most failure modes structurally.** Naive siloed mode leaks only through the shared
   cache. The cost is operational: one database per tenant.
4. **The cache is the weak point in both strategies.** No database control protects a cache keyed on
   the question alone. Tenant scoping of cache keys is an application-layer control and must be tested
   as one.

## Layer independence (mutation check)

Each control was removed on a scratch copy and the suites rerun against the local Postgres.

| Control removed | Caught by |
|---|---|
| Pooled search tenant filter (guard kept) | `test_pooled_app_filter_isolates_on_its_own` (the guard masks it from the attacks) |
| Pooled search filter **and** output guard | 5 attacks in "pooled secure, no RLS" + the guard and layer-1 tests |
| Output guard | `test_output_guard_drops_foreign_chunks` |
| RLS (`ENABLE`/`FORCE` removed) | 8 naive attacks, `rls-direct-sql` (secure), 3 RLS tests |
| RLS keyed to a fixed tenant | 14+ attack cells (every pooled config) |
| Tenant in cache key | `cache-leak` (3 secure configs), cache test |
| Question text in logs | `log-leak` (6 configs), `test_logs_carry_no_text` |
| Ingest honours a payload `tenant_id` | `ingest-tenant-override` (6 configs), ingest test |
| Pooled delete tenant filter | `delete-victim-doc`, `error-oracle` (secure, no RLS) |
| Distinct "owned by another tenant" error | `error-oracle` (5 configs) |

## RAG eval (replay)

`evals/datasets/rag.jsonl`: 14 questions over 3 tenants, all six documents. Every tenant has the same
documents with different facts, so an answer from the wrong tenant is wrong. The replay model answers
with the top retrieved chunk and cites it, so the score measures the offline embedder's retrieval
end to end. Any leak scores 0.

| Metric | Value |
|---|---|
| Pass rate (all three checks) | 0.9286 (13/14), committed baseline |
| Mean score | 0.9762 |
| Retrieved expected doc in top 5 | 14/14 |
| Leaks | 0 |

The one miss, `lost-asset-desk-t2` ("Which desk do I report a stolen asset to?"): the right document is
retrieved, but the lexical embedder ranks its second paragraph ("Every asset has a tag...") above the
paragraph naming the desk, and the extractive replay model answers from the top chunk only. Left in the
dataset as a known weakness of lexical embeddings; a semantic embedder or a real model reading all five
chunks should fix it. Live mode (`make evals-live`) has not been run yet.

## Known limits
- **Offline embedder only.** `HashingEmbedder` is lexical. The `Embedder` interface is ready for a
  provider; none is wired in yet.
- **The metadata store has no RLS.** Stage 0's store is still SQLite; RLS covers RAG chunks only.
- **Injected documents can mislead their own tenant** (3.7, accepted). They cannot reach other tenants'
  data because the model never receives it.
- **Brute-force search.** Neither index has an ANN index (HNSW/IVF). Fine at this scale; an ANN index
  on the pooled table would need the tenant filter applied with care (filtered ANN recall).
