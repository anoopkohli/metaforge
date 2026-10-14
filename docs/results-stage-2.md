# Stage 2 results: MCP server

Four tenant-scoped tools over the metadata store (`list_objects`, `describe_object`, `propose_change`,
`apply_change`), served over stdio with the MCP Python SDK 2.x. One process serves one tenant, fixed by a
signed token. Every write needs a human confirmation that the client collects and the server verifies.

## How to reproduce

```bash
make test                                   # 185 tests, offline; includes a real stdio subprocess test
make evals                                  # smoke + generator + mcp_attacks, gated on baselines
METAFORGE_UNSAFE_NAIVE_MCP=trust-model-tenant-arg \
  python -c "from attacks.mcp import *; print({a: run_attack(a, naive=True) for a in ATTACKS})"
```

## Attack suite: secure vs naive

13 attacks in `attacks/mcp.py`. The attacker is tenant `fjord`, the victim `cinder`. An attack
*succeeds* if the victim's canary or object reaches the attacker, or a write lands without a valid
confirmation. Run on 2026-10-08.

| Attack | What it tries | Secure | Naive |
|---|---|---|---|
| `tenant-arg-list` | `list_objects(tenant_id=cinder)` | blocked | **succeeded** |
| `tenant-arg-describe` | `describe_object(cinder_asset, tenant=cinder)` | blocked | **succeeded** |
| `tenant-arg-write` | propose + apply with `tenantId`/`tid=cinder`, even with a real approval | blocked | **succeeded** |
| `hidden-field` | read `employee.notes`, which fjord hid | blocked | blocked |
| `existence-oracle` | compare errors for a foreign vs a missing object | blocked | blocked |
| `apply-without-confirmation` | `apply_change` with no confirmation | blocked | blocked |
| `confirmation-for-other-change` | approval for change B used on change A | blocked | blocked |
| `foreign-change-id` | apply cinder's pending change ID as fjord | blocked | blocked |
| `replay-change-id` | apply the same change twice | blocked | blocked |
| `declined-confirmation` | user declines in the client | blocked | blocked |
| `no-elicitation-support` | client cannot ask the user | blocked | blocked |
| `forged-request-state` | well-formed `request_state` with a made-up HMAC | blocked | blocked |
| `state-for-other-change` | genuine signed state for change A replayed on change B | blocked | blocked |

Secure: 13/13 blocked (baseline `evals/baselines/mcp_attacks.json`, gated in CI).
Naive: exactly the 3 tenant-argument attacks succeed, which is what naive mode is meant to break. The
other 10 test controls that naive mode does not touch.

## Mutation check

On a scratch copy, each control was removed in turn; tests and attacks were rerun.

| Control removed | Caught by |
|---|---|
| HMAC check on `request_state` | `forged-request-state` (only after the attack was fixed; see the AI-assist log) |
| `request_state` bound to tenant and change | `state-for-other-change` |
| Server honours the user's answer | `declined-confirmation`, `test_declined_apply_does_not_write[auto, legacy]` |
| Tool layer requires a `Confirmation` | `apply-without-confirmation`, `confirmation-for-other-change`, `test_apply_requires_matching_confirmation` |
| Pending changes are single-use | `replay-change-id`, `test_change_ids_are_single_use_and_tenant_scoped` |
| Tenant-argument stripping | 3 naive-mode attack tests, `test_tenant_args_are_ignored_and_audited` (4 keys), stdio test |
| Uniform not-found message | `existence-oracle`, `test_not_found_message_is_identical_for_foreign_and_missing` |

## Protocol coverage

Both MCP protocol eras are tested through a real in-process client:
- **2025-11-25** (`mode="legacy"`): confirmation by form elicitation.
- **2026-07-28** (default): elicitation has no back-channel; the server returns `input_required` with a
  signed `request_state`, and the client retries with the user's answer.

`test_stdio_server_end_to_end` launches `python -m mcp_server` as a subprocess and checks that a smuggled
`tenant_id` is ignored over real stdio.

## Eval note

The MCP server calls no model, so this stage's eval is the attack suite. A live tool-use eval (does a model
pick the right tools and recover from validation errors?) needs an agent loop; it lands with stage 5.

## Known limits
- **Client-side approval is trusted.** A client configured to auto-approve elicitations defeats the
  confirmation step (threat 2.12, accepted). The server cannot see a human.
- Pending changes live in process memory: they are lost on restart, and one process serves one tenant.
  The HTTP transport (multi-tenant, per-request tokens) is deferred until there is a web framework.
- Labels in tool results are still read by the calling model (2.9, partial). Stage 3 adds an output guard.
