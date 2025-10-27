"""
Deduplication Store menggunakan SQLite untuk persistensi.
Menyimpan (topic, event_id) yang sudah diproses untuk idempotency.
"""
import sqlite3
import logging
from pathlib import Path
from typing import Set, Optional
import threading

logger = logging.getLogger(__name__)


class DedupStore:
    """
    Persistent deduplication store menggunakan SQLite.
    Thread-safe untuk concurrent access.
    """
    
    def __init__(self, db_path: str = "/app/data/dedup.db"):
        """
        Initialize dedup store dengan SQLite database.
        
        Args:
            db_path: Path ke SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.Lock()
        
        # Create directory if not exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_db()
        logger.info(f"DedupStore initialized with database: {db_path}")
    
    def _init_db(self):
        """Create table if not exists"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    topic TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (topic, event_id)
                )
            """)
            # Create index for faster lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_topic 
                ON processed_events(topic)
            """)
            conn.commit()
    
    def is_duplicate(self, topic: str, event_id: str) -> bool:
        """
        Check apakah event sudah pernah diproses.
        
        Args:
            topic: Topic event
            event_id: Event ID
            
        Returns:
            True jika duplicate (sudah ada), False jika baru
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT 1 FROM processed_events WHERE topic = ? AND event_id = ?",
                    (topic, event_id)
                )
                result = cursor.fetchone()
                return result is not None
    
    def mark_processed(self, topic: str, event_id: str) -> bool:
        """
        Mark event sebagai sudah diproses.
        
        Args:
            topic: Topic event
            event_id: Event ID
            
        Returns:
            True jika berhasil mark (event baru), False jika sudah ada (duplicate)
        """
        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "INSERT INTO processed_events (topic, event_id) VALUES (?, ?)",
                        (topic, event_id)
                    )
                    conn.commit()
                    logger.debug(f"Marked as processed: topic={topic}, event_id={event_id}")
                    return True
            except sqlite3.IntegrityError:
                # Already exists (duplicate)
                logger.info(f"Duplicate detected: topic={topic}, event_id={event_id}")
                return False
    
    def get_all_topics(self) -> list[str]:
        """
        Get list of unique topics.
        
        Returns:
            List of unique topics
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT DISTINCT topic FROM processed_events")
                return [row[0] for row in cursor.fetchall()]
    
    def get_events_by_topic(self, topic: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Get processed events by topic.
        
        Args:
            topic: Filter by topic, None for all events
            
        Returns:
            List of (topic, event_id) tuples
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                if topic:
                    cursor = conn.execute(
                        "SELECT topic, event_id FROM processed_events WHERE topic = ?",
                        (topic,)
                    )
                else:
                    cursor = conn.execute(
                        "SELECT topic, event_id FROM processed_events"
                    )
                return cursor.fetchall()
    
    def count_processed(self) -> int:
        """
        Get total count of unique processed events.
        
        Returns:
            Total unique events processed
        """
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM processed_events")
                return cursor.fetchone()[0]
    
    def clear(self):
        """Clear all data (for testing purposes)"""
        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM processed_events")
                conn.commit()
                logger.warning("DedupStore cleared")
