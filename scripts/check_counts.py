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
    
    count = await conn.fetchval("SELECT COUNT(*) FROM training_documents")
    visited = await conn.fetchval("SELECT COUNT(*) FROM visited_urls")
    
    print(f"Training Documents: {count}")
    print(f"Visited URLs: {visited}")
    
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
