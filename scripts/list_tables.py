import asyncio
import asyncpg
import os
from dotenv import load_dotenv

async def main():
    load_dotenv()
    conn = await asyncpg.connect(
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', 'postgres'),
        database=os.getenv('DB_NAME', 'nepali_corpus'),
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432')
    )
    
    rows = await conn.fetch("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    
    print("\n[DB Tables]")
    if not rows:
        print("No tables found in public schema.")
    for row in rows:
        print(f" - {row['table_name']}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
