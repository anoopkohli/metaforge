"""Dev CLI: `python -m generator --token TOKEN "description" [--apply]`.

The tenant comes only from the signed token (see `python -m core.cli token <tenant_id>`). Without
`--apply` this is a dry run: it prints the proposal and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

from core.auth import AuthError, verify_token
from core.config import Settings
from core.llm.anthropic_client import AnthropicLLM
from core.store.sqlite_store import MetadataStore
from generator.generate import ProposalRejected, apply_proposal, generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generator")
    parser.add_argument("description")
    parser.add_argument("--token", required=True, help="signed dev token; sets the tenant")
    parser.add_argument("--apply", action="store_true", help="write the proposal to the store")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    try:
        ctx = verify_token(settings.auth_secret, args.token)
    except AuthError as exc:
        print(f"auth failed: {exc}", file=sys.stderr)
        return 2

    store = MetadataStore(settings.db_path)
    try:
        llm = AnthropicLLM(model=settings.model)
        result = generate(llm, store, ctx, args.description, max_attempts=args.attempts)
        for i, attempt in enumerate(result.attempts, 1):
            for err in attempt.errors:
                print(f"attempt {i} rejected: {err}", file=sys.stderr)
        print(
            f"tenant={ctx.tenant_id} attempts={len(result.attempts)} "
            f"tokens_in={result.usage.input_tokens} tokens_out={result.usage.output_tokens} "
            f"latency_s={result.latency_s:.2f}",
            file=sys.stderr,
        )
        if result.proposal is None:
            print("no valid proposal", file=sys.stderr)
            return 1
        print(json.dumps(result.proposal.model_dump(mode="json"), indent=2))
        if args.apply:
            try:
                version = apply_proposal(store, ctx, result.proposal)
            except ProposalRejected as exc:
                print(f"not applied: {exc}", file=sys.stderr)
                return 1
            print(f"applied (version {version})", file=sys.stderr)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
