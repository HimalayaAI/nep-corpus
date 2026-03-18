import asyncio
import os
import time
from datetime import datetime
import asyncpg
from dotenv import load_dotenv

async def get_stats():
    load_dotenv()
    conn = None
    try:
        conn = await asyncpg.connect(
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres'),
            database=os.getenv('DB_NAME', 'nepali_corpus'),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432')
        )
        
        training_count = await conn.fetchval("SELECT COUNT(*) FROM training_documents")
        visited_count = await conn.fetchval("SELECT COUNT(*) FROM visited_urls")
        raw_count = await conn.fetchval("SELECT COUNT(*) FROM raw_records")
        
        # Get count per category if possible
        category_stats = await conn.fetch("""
            SELECT category, COUNT(*) as c 
            FROM training_documents 
            GROUP BY category 
            ORDER BY c DESC
        """)
        
        return {
            "training": training_count,
            "visited": visited_count,
            "raw": raw_count,
            "categories": category_stats
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if conn:
            await conn.close()

async def monitor(interval=60):
    print("\033[H\033[J", end="") # Clear screen
    print(f"🚀 Nepali Corpus - Million Article Run Monitor")
    print(f"Press Ctrl+C to stop monitor (Run will continue in background)\n")
    
    last_count = 0
    start_time = time.time()
    
    while True:
        stats = await get_stats()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Move cursor to top
        print("\033[H", end="")
        print(f"🚀 Nepali Corpus - Million Article Run Monitor")
        print(f"Last Updated: {now}")
        print("-" * 50)
        
        if "error" in stats:
            print(f"❌ Error: {stats['error']}")
        else:
            training = stats['training']
            delta = training - last_count if last_count > 0 else 0
            rate = delta / (interval / 60) if last_count > 0 else 0
            
            print(f"📁 Training Documents: \033[1;32m{training:,}\033[0m")
            print(f"🔗 Visited URLs:      {stats['visited']:,}")
            print(f"📦 Raw Records:       {stats['raw']:,}")
            print("-" * 50)
            
            if training > 0:
                print(f"📈 Velocity:         +{delta} docs / last min")
                print(f"🎯 Target Progress:  {(training/1000000)*100:.2f}% of 1M")
            
            if stats['categories']:
                print("\n📂 Documents by Category:")
                for cat in stats['categories']:
                    print(f"  - {cat['category'] or 'Unknown'}: {cat['c']:,}")
            
            last_count = training
            
        print("\n\033[KWaiting for next update...")
        await asyncio.sleep(interval)

if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("\nMonitor stopped. Scraper is still running in background.")
