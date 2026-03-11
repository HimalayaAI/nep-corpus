#!/usr/bin/env python3
"""Clear all data from Nepali Corpus database tables.

This script truncates all tables in the database, preserving the schema.
Useful for debugging and starting fresh with new data.

Usage:
    python scripts/clear_db.py [--confirm]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()

import asyncpg


TABLES_TO_CLEAR = [
    "training_documents",
    "visited_urls",
    "raw_records",
]


async def clear_database(
    host: str = "localhost",
    port: int = 5432,
    database: str = "nepali_corpus",
    user: str = "postgres",
    password: str = "postgres",
    dry_run: bool = False,
):
    """Clear all data from database tables."""
    
    print(f"Connecting to database '{database}' on {host}:{port}...")
    
    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        
        print(f"✓ Connected to database '{database}'")
        
        # Get row counts before clearing
        print("\n📊 Current row counts:")
        counts_before = {}
        for table in TABLES_TO_CLEAR:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                counts_before[table] = count
                print(f"  {table:25} {count:>8} rows")
            except Exception as e:
                print(f"  {table:25} ERROR: {e}")
                counts_before[table] = None
        
        total_rows = sum(c for c in counts_before.values() if c is not None)
        print(f"\n  {'TOTAL':25} {total_rows:>8} rows")
        
        if total_rows == 0:
            print("\n✓ Database is already empty!")
            await conn.close()
            return
        
        if dry_run:
            print("\n🔍 DRY RUN - No changes will be made")
            await conn.close()
            return
        
        # Clear tables
        print("\n🗑️  Clearing tables...")
        for table in TABLES_TO_CLEAR:
            if counts_before.get(table, 0) > 0:
                try:
                    await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
                    print(f"  ✓ Cleared {table}")
                except Exception as e:
                    print(f"  ✗ Failed to clear {table}: {e}")
        
        # Verify tables are empty
        print("\n✅ Verification:")
        all_clear = True
        for table in TABLES_TO_CLEAR:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                status = "✓" if count == 0 else "✗"
                print(f"  {status} {table:25} {count:>8} rows")
                if count > 0:
                    all_clear = False
            except Exception as e:
                print(f"  ✗ {table:25} ERROR: {e}")
                all_clear = False
        
        if all_clear:
            print("\n✓ All tables cleared successfully!")
        else:
            print("\n⚠️  Some tables still have data")
        
        await conn.close()
        
    except asyncpg.exceptions.InvalidCatalogNameError:
        print(f"✗ Database '{database}' does not exist")
        sys.exit(1)
    except asyncpg.exceptions.InvalidPasswordError:
        print("✗ Invalid database credentials")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Clear all data from Nepali Corpus database tables"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt and clear immediately"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be cleared without making changes"
    )
    parser.add_argument(
        "--host",
        default=os.getenv("DB_HOST", "localhost"),
        help="Database host (default: from DB_HOST env or localhost)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DB_PORT", "5432")),
        help="Database port (default: from DB_PORT env or 5432)"
    )
    parser.add_argument(
        "--database",
        default=os.getenv("DB_NAME", "nepali_corpus"),
        help="Database name (default: from DB_NAME env or nepali_corpus)"
    )
    parser.add_argument(
        "--user",
        default=os.getenv("DB_USER", "postgres"),
        help="Database user (default: from DB_USER env or postgres)"
    )
    parser.add_argument(
        "--password",
        default=os.getenv("DB_PASSWORD", "postgres"),
        help="Database password (default: from DB_PASSWORD env or postgres)"
    )
    
    args = parser.parse_args()
    
    if not args.confirm and not args.dry_run:
        print("⚠️  WARNING: This will DELETE ALL DATA from the following tables:")
        for table in TABLES_TO_CLEAR:
            print(f"  - {table}")
        print(f"\nDatabase: {args.database} on {args.host}:{args.port}")
        
        response = input("\nAre you sure you want to continue? (yes/no): ")
        if response.lower() not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)
    
    asyncio.run(clear_database(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
        password=args.password,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
