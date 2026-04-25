from __future__ import annotations

import argparse
import json

from .config import Settings
from .retrieval import RetrievalService


def main() -> None:
    parser = argparse.ArgumentParser(description="Local MCP search maintenance CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show index status")
    reindex_parser = subparsers.add_parser("reindex", help="Rebuild semantic index")
    reindex_parser.add_argument(
        "--mode",
        choices=["auto", "full", "incremental"],
        default="auto",
        help="Reindex mode. auto uses git/manifest-based change detection.",
    )
    pack_parser = subparsers.add_parser("context-pack", help="Build a compact code context pack")
    pack_parser.add_argument("query")
    pack_parser.add_argument("--max-results", type=int, default=8)
    pack_parser.add_argument("--max-chars", type=int, default=None)

    args = parser.parse_args()
    service = RetrievalService(Settings.from_env())

    if args.command == "status":
        print(json.dumps(service.index_status(), ensure_ascii=False, indent=2))
        return
    if args.command == "reindex":
        print(json.dumps(service.reindex(mode=args.mode), ensure_ascii=False, indent=2))
        return
    if args.command == "context-pack":
        print(
            json.dumps(
                service.code_context_pack(
                    args.query,
                    max_results=args.max_results,
                    max_chars=args.max_chars,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
