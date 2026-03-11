import asyncio
import os
from dotenv import load_dotenv
from nepali_corpus.core.services.storage.env_storage import EnvStorageService

async def main():
    load_dotenv()
    print("Initializing Database Storage...")
    storage = EnvStorageService()
    await storage.initialize() # This applies schema.sql
    print("Storage Initialized.")
    
    count = await storage._db.fetch_value("SELECT COUNT(*) FROM training_documents")
    visited = await storage._db.fetch_value("SELECT COUNT(*) FROM visited_urls")
    
    print(f"Training Documents: {count}")
    print(f"Visited URLs: {visited}")
    
    await storage.close()

if __name__ == "__main__":
    asyncio.run(main())
