#!/usr/bin/env python3
"""
CLI tool to onboard a Postgres database for the FHI RCA Agent.

Produces a schema_profile.json that the chat server loads at startup.
Re-running is a no-op unless --refresh is passed.

Usage
-----
    # Basic onboarding
    python onboard.py --dsn "postgresql://user:pass@host/db"

    # Specify schema and glossary table
    python onboard.py --dsn "postgresql://user:pass@host/db" \\
                      --schema reporting \\
                      --glossary-table dataset_note

    # Force refresh after a migration
    python onboard.py --dsn "postgresql://user:pass@host/db" --refresh
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.db import create_introspection_pool, get_dsn_hash, redact_dsn
from app.introspection import run_introspection
from app.schema_profile import SchemaProfile

logging.basicConfig(
    level=logging.WARNING,  # keep CLI output clean; only show warnings+
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onboard",
        description=(
            "Onboard a Postgres database for the FHI RCA Agent.\n"
            "Writes a schema_profile.json to PROFILES_DIR (default: schema_profiles/)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dsn",
        required=True,
        metavar="DSN",
        help='PostgreSQL connection string, e.g. "postgresql://user:pass@host/db"',
    )
    parser.add_argument(
        "--schema",
        default="public",
        metavar="SCHEMA",
        help="Target schema to introspect (default: public)",
    )
    parser.add_argument(
        "--glossary-table",
        dest="glossary_table",
        default=None,
        metavar="TABLE",
        help=(
            "Explicitly name a table to treat as business glossary. "
            "Auto-detection also looks for tables named readme/glossary/etc."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-onboarding even if a cached profile already exists",
    )
    parser.add_argument(
        "--profiles-dir",
        dest="profiles_dir",
        default="schema_profiles",
        metavar="DIR",
        help="Directory to store schema profiles (default: schema_profiles/)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return parser


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    profiles_dir = Path(args.profiles_dir)
    profiles_dir.mkdir(parents=True, exist_ok=True)

    dsn: str = args.dsn
    schema: str = args.schema
    dsn_hash = get_dsn_hash(dsn, schema)
    profile_path = profiles_dir / f"{dsn_hash}.json"

    # ── Check cache ───────────────────────────────────────────────────────────
    if not args.refresh and profile_path.exists():
        print(f"\n✓  Cached profile exists: {profile_path}")
        print("   Pass --refresh to re-run onboarding.\n")
        profile = _load_profile(profile_path)
        if profile:
            _print_summary(profile)
        return 0

    # ── Connect ───────────────────────────────────────────────────────────────
    print(f"\n🔍  Connecting to: {redact_dsn(dsn)}")
    print(f"    Schema:  {schema}")
    if args.glossary_table:
        print(f"    Glossary table:  {args.glossary_table}")
    print()

    try:
        pool = await create_introspection_pool(dsn)
    except Exception as exc:
        print(f"❌  Connection failed: {exc}", file=sys.stderr)
        return 1

    # ── Introspect ────────────────────────────────────────────────────────────
    print("⏳  Introspecting schema …", end="", flush=True)
    try:
        profile = await run_introspection(
            pool=pool,
            schema=schema,
            glossary_table=args.glossary_table,
            dsn_hash=dsn_hash,
        )
    except Exception as exc:
        print(f"\n❌  Introspection failed: {exc}", file=sys.stderr)
        logger.exception("Introspection error")
        return 1
    finally:
        await pool.close()

    print(" done.")

    # ── Save ──────────────────────────────────────────────────────────────────
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    profile.profile_path = str(profile_path.resolve())
    print(f"✅  Profile written to: {profile_path}\n")

    _print_summary(profile)
    return 0


def _load_profile(path: Path) -> SchemaProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SchemaProfile.model_validate(data)
    except Exception as exc:
        print(f"  (Could not parse profile: {exc})", file=sys.stderr)
        return None


def _print_summary(profile: SchemaProfile) -> None:
    w = 62
    print("─" * w)
    print(f"  Schema:           {profile.schema_name}")
    print(f"  Profiled:         {profile.profiled_at.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Tables:           {len(profile.tables)}")
    print(f"  FK relationships: {len(profile.foreign_keys)}")
    print(f"  Glossary entries: {len(profile.glossary)}")
    print(f"  Large schema:     {profile.is_large_schema}")

    # Date ranges
    date_ranges: list[str] = []
    for table in profile.tables:
        for col in table.columns:
            if col.date_min and col.date_max:
                date_ranges.append(
                    f"    {table.name}.{col.name}: {col.date_min} → {col.date_max}"
                )
    if date_ranges:
        print(f"\n  Date ranges ({len(date_ranges)} column(s)):")
        for dr in date_ranges[:8]:
            print(dr)
        if len(date_ranges) > 8:
            print(f"    … and {len(date_ranges) - 8} more")

    # Tables
    print(f"\n  Tables ({len(profile.tables)}):")
    for t in profile.tables:
        rc = f"{t.row_count:,}" if t.row_count is not None else "?"
        pk_str = f"PK: {', '.join(t.primary_keys)}" if t.primary_keys else "no PK"
        print(f"    {t.name:<30} {rc:>10} rows   {len(t.columns)} cols   {pk_str}")

    # Glossary preview
    if profile.glossary:
        print(f"\n  Business glossary ({len(profile.glossary)} entries — first 5):")
        for g in profile.glossary[:5]:
            defn = g.definition[:70] + ("…" if len(g.definition) > 70 else "")
            print(f"    [{g.source}] {g.term}: {defn}")
        if len(profile.glossary) > 5:
            print(f"    … and {len(profile.glossary) - 5} more")

    print("─" * w + "\n")


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
