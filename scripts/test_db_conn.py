import asyncio
import os
import asyncpg

async def test_conn():
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "5432"))
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    db_name = os.getenv("DB_NAME", "nepali_corpus")
    
    print(f"Testing connection to {user}@{host}:{port}/{db_name} ...")
    try:
        conn = await asyncpg.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            database=db_name
        )
        print("✓ SUCCESS!")
        await conn.close()
    except Exception as e:
        print(f"✗ FAILED: {e}")

if __name__ == "__main__":
    asyncio.run(test_conn())
