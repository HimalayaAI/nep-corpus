import asyncio
import os
from nepali_corpus.core.services.storage.env_storage import EnvStorageService

async def check():
    storage = EnvStorageService()
    await storage.initialize()
    
    async with storage._db.pool.acquire() as conn:
        total_raw = await conn.fetchval("SELECT count(*) FROM raw_records")
        null_raw = await conn.fetchval("SELECT count(*) FROM raw_records WHERE content IS NULL")
        total_train = await conn.fetchval("SELECT count(*) FROM training_documents")
        
        print("-" * 40)
        print(f"Total Raw Records:    {total_raw:,}")
        print(f"NULL Content Records: {null_raw:,}")
        print(f"Training Documents:   {total_train:,}")
        print("-" * 40)
        
        if total_raw > 0:
            success_rate = (total_train / total_raw) * 100
            print(f"Current Success Rate: {success_rate:.2f}%")
        
    await storage.close()

if __name__ == "__main__":
    asyncio.run(check())
