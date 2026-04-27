"""Minimal CLI for local ops: init the DB, mint an admin tenant, run the API."""

from __future__ import annotations

import argparse
import sys

from .config import get_settings
from .db import init_db
from .personas import DEFAULT_PERSONA, PERSONAS
from .tenancy import create_tenant


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npo-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create the SQLite schema")

    p_create = sub.add_parser("create-tenant", help="Create a tenant + API key")
    p_create.add_argument("--name", required=True)
    p_create.add_argument(
        "--persona",
        default=DEFAULT_PERSONA,
        choices=list(PERSONAS.keys()),
    )

    p_serve = sub.add_parser("serve", help="Run the API with uvicorn")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "init-db":
        init_db()
        print(f"Initialized DB at {get_settings().db_path}")
        return 0

    if args.cmd == "create-tenant":
        init_db()
        tenant, api_key = create_tenant(name=args.name, persona=args.persona)
        print(f"tenant_id: {tenant.id}")
        print(f"name:      {tenant.name}")
        print(f"persona:   {tenant.persona}")
        print(f"api_key:   {api_key}")
        print("(store the api_key now — it is not retrievable later)")
        return 0

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "npo_agent.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
