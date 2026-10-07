"""Tiny dev CLI: `python -m core.cli seed|tenants|token <tenant_id>`."""

from __future__ import annotations

import argparse
import sys

from core.auth import issue_token
from core.config import Settings
from core.store.sqlite_store import MetadataStore
from core.synthetic import seed_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="metaforge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    seed = sub.add_parser("seed", help="create demo tenants in the local store")
    seed.add_argument("--tenants", type=int, default=5)
    seed.add_argument("--seed", type=int, default=7)
    sub.add_parser("tenants", help="list tenants that have data")
    tok = sub.add_parser("token", help="issue a dev token for a tenant")
    tok.add_argument("tenant_id")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = MetadataStore(settings.db_path)
    try:
        if args.cmd == "seed":
            tenants = seed_demo(store, n_tenants=args.tenants, seed=args.seed)
            print(f"seeded {len(tenants)} tenants into {settings.db_path}:")
            for t in tenants:
                print(f"  {t.tenant_id}")
        elif args.cmd == "tenants":
            for tid in store.platform_list_tenant_ids():
                print(tid)
        elif args.cmd == "token":
            print(issue_token(settings.auth_secret, args.tenant_id))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
