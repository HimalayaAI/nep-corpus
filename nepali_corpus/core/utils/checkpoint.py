"""Checkpoint system for resuming long scraping runs.

Saves progress every N records so you can resume if the run crashes.
"""

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set


class RunCheckpoint:
    """Manages checkpoints during a scraping run."""
    
    def __init__(self, checkpoint_dir: str = ".checkpoints", run_id: Optional[str] = None):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(exist_ok=True)
        
        # Use timestamp as run_id if not provided
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
        self.checkpoint_file = self.checkpoint_dir / f"{self.run_id}.json"
        
        # Track state
        self.processed_urls: Set[str] = set()
        self.record_count = 0
        self.start_time = time.time()
        
        # Load existing if resuming
        self._load()
    
    def _load(self):
        """Load existing checkpoint if present."""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file) as f:
                    data = json.load(f)
                    self.processed_urls = set(data.get("processed_urls", []))
                    self.record_count = data.get("record_count", 0)
                    print(f"Resumed from checkpoint: {len(self.processed_urls)} URLs already processed")
            except Exception as e:
                print(f"Could not load checkpoint: {e}")
    
    def save(self, force: bool = False):
        """Save current state to checkpoint file."""
        data = {
            "run_id": self.run_id,
            "processed_urls": list(self.processed_urls),
            "record_count": self.record_count,
            "timestamp": time.time(),
            "elapsed_seconds": time.time() - self.start_time
        }
        
        # Write to temp file then rename for atomicity
        temp_file = self.checkpoint_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(data, f)
        temp_file.rename(self.checkpoint_file)
    
    def mark_processed(self, url: str, batch_size: int = 10):
        """Mark URL as processed. Saves every N records."""
        self.processed_urls.add(url)
        self.record_count += 1
        
        # Save every batch_size records
        if self.record_count % batch_size == 0:
            self.save()
    
    def is_processed(self, url: str) -> bool:
        """Check if URL was already processed."""
        return url in self.processed_urls
    
    def get_stats(self) -> Dict:
        """Get current run statistics."""
        elapsed = time.time() - self.start_time
        return {
            "run_id": self.run_id,
            "processed_urls": len(self.processed_urls),
            "records_per_minute": (self.record_count / elapsed * 60) if elapsed > 0 else 0,
            "elapsed_minutes": elapsed / 60
        }
    
    def cleanup(self):
        """Remove checkpoint file after successful completion."""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()


def list_checkpoints(checkpoint_dir: str = ".checkpoints") -> List[str]:
    """List available checkpoint files."""
    dir_path = Path(checkpoint_dir)
    if not dir_path.exists():
        return []
    
    checkpoints = []
    for f in dir_path.glob("*.json"):
        try:
            with open(f) as file:
                data = json.load(file)
                checkpoints.append({
                    "run_id": data.get("run_id"),
                    "urls": len(data.get("processed_urls", [])),
                    "records": data.get("record_count", 0),
                    "elapsed_min": int(data.get("elapsed_seconds", 0) / 60),
                    "file": str(f)
                })
        except:
            pass
    
    return sorted(checkpoints, key=lambda x: x["run_id"], reverse=True)


def resume_from_checkpoint(checkpoint_file: str) -> Optional[RunCheckpoint]:
    """Resume a run from a specific checkpoint file."""
    path = Path(checkpoint_file)
    if not path.exists():
        return None
    
    run_id = path.stem
    return RunCheckpoint(run_id=run_id)
