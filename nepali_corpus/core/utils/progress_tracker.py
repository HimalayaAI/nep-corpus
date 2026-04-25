"""Simple progress tracking for long runs.

Writes progress to a file that you can watch with `tail -f`.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime


class ProgressTracker:
    """Track and report scraping progress."""
    
    def __init__(self, output_file: str = "progress.json", total_expected: Optional[int] = None):
        self.output_file = Path(output_file)
        self.total_expected = total_expected
        
        self.start_time = time.time()
        self.processed = 0
        self.enriched = 0
        self.failed = 0
        self.current_source = ""
        
        # Source-specific stats
        self.source_stats: Dict[str, Dict] = {}
    
    def update(self, source: str, success: bool, enriched: bool = False):
        """Update progress with one record."""
        self.processed += 1
        
        if success and enriched:
            self.enriched += 1
        elif not success:
            self.failed += 1
        
        self.current_source = source
        
        # Update source stats
        if source not in self.source_stats:
            self.source_stats[source] = {"total": 0, "enriched": 0, "failed": 0}
        
        self.source_stats[source]["total"] += 1
        if success and enriched:
            self.source_stats[source]["enriched"] += 1
        elif not success:
            self.source_stats[source]["failed"] += 1
        
        # Write every 10 records
        if self.processed % 10 == 0:
            self._write()
    
    def _write(self):
        """Write current progress to file."""
        elapsed = time.time() - self.start_time
        
        data = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": int(elapsed),
            "processed": self.processed,
            "enriched": self.enriched,
            "failed": self.failed,
            "enrichment_rate": round(self.enriched / self.processed * 100, 1) if self.processed > 0 else 0,
            "records_per_minute": round(self.processed / elapsed * 60, 1) if elapsed > 0 else 0,
            "current_source": self.current_source,
            "sources": {
                k: {
                    "rate": round(v["enriched"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
                    **v
                }
                for k, v in sorted(self.source_stats.items(), key=lambda x: -x[1]["total"])[:10]
            }
        }
        
        if self.total_expected:
            data["percent_complete"] = round(self.processed / self.total_expected * 100, 1)
            data["eta_minutes"] = int((elapsed / self.processed * self.total_expected - elapsed) / 60) if self.processed > 0 else 0
        
        # Atomic write
        temp = self.output_file.with_suffix(".tmp")
        with open(temp, "w") as f:
            json.dump(data, f, indent=2)
        temp.rename(self.output_file)
    
    def finish(self):
        """Mark run as complete."""
        self._write()
        final_data = json.loads(self.output_file.read_text())
        final_data["status"] = "complete"
        self.output_file.write_text(json.dumps(final_data, indent=2))
    
    def get_summary(self) -> str:
        """Get human-readable summary."""
        elapsed = time.time() - self.start_time
        rate = self.enriched / self.processed * 100 if self.processed > 0 else 0
        rpm = self.processed / elapsed * 60 if elapsed > 0 else 0
        
        return (
            f"Processed: {self.processed} | "
            f"Enriched: {self.enriched} ({rate:.1f}%) | "
            f"Failed: {self.failed} | "
            f"Speed: {rpm:.1f}/min"
        )


def watch_progress(file_path: str = "progress.json", interval: int = 5):
    """Watch progress file and print updates (for terminal use)."""
    import time
    
    path = Path(file_path)
    last_mtime = 0
    
    print(f"Watching {file_path}... (Ctrl+C to stop)")
    
    while True:
        try:
            if path.exists() and path.stat().st_mtime != last_mtime:
                last_mtime = path.stat().st_mtime
                
                try:
                    with open(path) as f:
                        data = json.load(f)
                    
                    print(f"\n[{data['timestamp']}]")
                    print(f"  Processed: {data['processed']}")
                    print(f"  Enriched: {data['enriched']} ({data['enrichment_rate']}%)")
                    print(f"  Speed: {data['records_per_minute']}/min")
                    
                    if 'percent_complete' in data:
                        print(f"  Progress: {data['percent_complete']}%")
                        print(f"  ETA: {data['eta_minutes']} min")
                    
                    print(f"  Current: {data['current_source']}")
                    
                    # Top 3 sources
                    for src, stats in list(data['sources'].items())[:3]:
                        print(f"    {src}: {stats['total']} total, {stats['rate']}% enriched")
                        
                except Exception:
                    pass
            
            time.sleep(interval)
            
        except KeyboardInterrupt:
            print("\nStopped watching.")
            break
