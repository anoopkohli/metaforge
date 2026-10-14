# Stage 6 results: business data, roles and Ask data

Each demo tenant is now a small business with synthetic staff and records. Logins carry a role
(`staff` or `manager`), and sensitive fields are `restricted` to managers. "Ask data" turns a question
into a query in a small JSON query language (never SQL) that the server validates and runs.

## The businesses

| Tenant | Business | Objects (records) | Restricted fields |
|---|---|---|---|
| fjord | Fjord Taxi Co. | employee (24), vehicle (14), ride (220) | salary, vehicle purchase_price |
| cinder | Cinder & Partners LLP | employee (18), client (30), matter (45), time_entry (180) | salary, retainer_balance, hourly_rate |
| gable | Gable Family Dental | employee (14), patient (80), appointment (190) | salary, date_of_birth, clinical_notes |
| juniper | Juniper Builders | employee (20), project (25), equipment (40) | salary, project budget |
| acme | Acme Coffee Roasters | employee (16), product (14), sale (240) | salary, unit_cost |

Employees have department, job title, badge number and salary (per-title pay ranges); every tenant also
has its stage-0 asset records, one of which carries the tenant's canary. Each business adds two policy
documents to its Ask documents corpus. All values come from an RNG seeded by tenant, never the clock;
the stage 0-5 seed data and baselines are unchanged.

## How a question is answered

```
question ─► planner (LLMClient; sees the schema, never record values) ─► {"query": {...}}
        ─► strict parse ─► validate(effective schema, role) ─┬─ errors ─► fed back (≤ 3 tries) ─► denied
                                                             └─ ok ─► interpret over this tenant's records
                                                                     ─► rows ─► answer (deterministic, or a
                                                                        model that sees only these rows)
```

Restricted fields are refused for staff in **every** clause: `select`, `where`, `group_by`, `aggregate`
and `order_by`. Filtering on `salary > 80000`, averaging salaries or ranking by salary leaks salary
information as surely as selecting it.

## Attacks (planner emits whatever the attacker wants)

| Attack | Secure | Naive |
|---|---|---|
| `tenant-in-query` (query names cinder) | blocked | **leaked** |
| `cross-tenant-object` (query cinder_asset by name) | blocked | blocked (naive does not remove this) |
| `restricted-select` (staff selects salary) | blocked | **leaked** |
| `restricted-filter` (who earns > 80k) | blocked | **leaked** |
| `restricted-aggregate` (avg salary) | blocked | **leaked** |
| `restricted-order` (rank by salary) | blocked | **leaked** |
| `role-claim-in-question` ("I am a manager") | blocked | **leaked** |
| `hidden-field-query` (select hidden notes) | blocked | blocked (naive does not remove this) |
| `injection-in-records` (record text asks for salaries) | blocked | blocked (the planner never reads records) |

Naive mode trusts a tenant named in the query and ignores restricted flags. Every result above is
asserted by `tests/test_attacks_dataqa.py` and runs in the web UI's security lab.

## Mutation check

| Control removed | Caught by |
|---|---|
| Restricted check only on `select` | 4 clause tests, `restricted-filter`, `restricted-aggregate` |
| Restricted check removed | 5 clause tests, execute-refusal test |
| Tenant inside the query honoured | `tenant-in-query` |
| Base schema instead of effective (hidden fields visible) | hidden-field test, clause tests |
| Lenient parsing (extra keys, coercion) | 3 strict-parse tests |
| No limit cap | strict-parse test |

## Eval

`evals/datasets/dataqa.jsonl`: 26 cases across the five businesses: 15 open questions as staff, 5
restricted questions as staff (must be denied), the same 5 as managers (must be answered), and one
role-claim injection. Scoring is **execution accuracy**: the planned query's result must equal a gold
query's result. Restricted values reaching staff, or another tenant's canary, score 0.

| Mode | Pass rate |
|---|---|
| Replay (gold plans played back; committed baseline) | 1.00 (26/26) |
| Control: a planner that answers a different question (count instead) | 0.00 |
| Live run 1 (2026-10-13; [`results-live.md`](results-live.md)) | 0.92 (24/26); run 2 (2026-10-14): 1.00 (26/26) |

Replay shows the validator, interpreter and scoring are right; the live number is the interesting one
(how often does a real model plan the right query, and does it refuse restricted requests itself?).

## Known limits
- **No joins.** One object per query; reference fields show the referenced record's name. Questions
  like "revenue by product origin" need two steps.
- **Small data, in-memory scans.** Fine for hundreds of records per tenant; a real system would push
  validated queries down to the database (and keep RLS underneath).
- **Group sizes are not protected.** A manager can see salaries anyway; but if a future role could see
  aggregates and not rows, small groups would need suppression (k-anonymity).
- **Roles are a demo stand-in.** Choosing "manager" at login is the identity provider here.
