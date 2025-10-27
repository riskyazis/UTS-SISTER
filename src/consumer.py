"""
Consumer untuk memproses event dari queue dengan idempotency.
Menggunakan asyncio.Queue untuk pipeline publisher → consumer.
"""
import asyncio
import logging
from typing import Dict, Any
from datetime import datetime
from .models import Event
from .dedup_store import DedupStore

logger = logging.getLogger(__name__)


class EventConsumer:
    """
    Idempotent consumer yang memproses event dari queue.
    Menerapkan at-least-once delivery semantics dengan deduplication.
    """
    
    def __init__(self, dedup_store: DedupStore, queue: asyncio.Queue):
        """
        Initialize consumer.
        
        Args:
            dedup_store: DedupStore instance untuk deduplication
            queue: asyncio.Queue untuk menerima event
        """
        self.dedup_store = dedup_store
        self.queue = queue
        self.running = False
        self._task = None
        
        # Statistics
        self.stats = {
            'received': 0,
            'unique_processed': 0,
            'duplicate_dropped': 0,
            'start_time': datetime.now()
        }
        
        logger.info("EventConsumer initialized")
    
    async def start(self):
        """Start consumer background task"""
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._consume_loop())
            logger.info("EventConsumer started")
    
    async def stop(self):
        """Stop consumer background task"""
        if self.running:
            self.running = False
            if self._task:
                await self._task
            logger.info("EventConsumer stopped")
    
    async def _consume_loop(self):
        """Main consumer loop - continuously process events from queue"""
        logger.info("Consumer loop started")
        
        while self.running:
            try:
                # Wait for event with timeout to allow graceful shutdown
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._process_event(event)
                self.queue.task_done()
            except asyncio.TimeoutError:
                # No event received, continue loop
                continue
            except Exception as e:
                logger.error(f"Error in consumer loop: {e}", exc_info=True)
        
        logger.info("Consumer loop stopped")
    
    async def _process_event(self, event: Event):
        """
        Process single event dengan idempotency check.
        
        Args:
            event: Event to process
        """
        self.stats['received'] += 1
        
        # Check for duplicate using (topic, event_id)
        if self.dedup_store.is_duplicate(event.topic, event.event_id):
            self.stats['duplicate_dropped'] += 1
            logger.info(
                f"Duplicate event dropped: topic={event.topic}, "
                f"event_id={event.event_id}, source={event.source}"
            )
            return
        
        # Mark as processed (atomic operation)
        if self.dedup_store.mark_processed(event.topic, event.event_id):
            self.stats['unique_processed'] += 1
            logger.info(
                f"Event processed: topic={event.topic}, "
                f"event_id={event.event_id}, source={event.source}"
            )
            
            # Simulate event processing (dapat diganti dengan logic sesungguhnya)
            await self._do_process(event)
        else:
            # Race condition: another thread marked it first
            self.stats['duplicate_dropped'] += 1
            logger.info(
                f"Duplicate event (race condition): topic={event.topic}, "
                f"event_id={event.event_id}"
            )
    
    async def _do_process(self, event: Event):
        """
        Actual event processing logic.
        Implementasi dapat disesuaikan dengan kebutuhan (mis. aggregasi, transform, dll).
        
        Args:
            event: Event to process
        """
        # Simulasi processing time
        await asyncio.sleep(0.001)
        
        # Log event payload untuk demonstrasi
        logger.debug(f"Processing payload: {event.payload}")
        
        # Di sini bisa ditambahkan logic seperti:
        # - Aggregasi data per topic
        # - Transform data
        # - Trigger actions berdasarkan event
        # - dll.
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get consumer statistics.
        
        Returns:
            Dictionary containing stats
        """
        uptime = (datetime.now() - self.stats['start_time']).total_seconds()
        
        return {
            'received': self.stats['received'],
            'unique_processed': self.stats['unique_processed'],
            'duplicate_dropped': self.stats['duplicate_dropped'],
            'topics': self.dedup_store.get_all_topics(),
            'uptime': uptime
        }
    
    def get_events(self, topic: str = None) -> list[Dict[str, str]]:
        """
        Get processed events by topic.
        
        Args:
            topic: Filter by topic, None for all
            
        Returns:
            List of events
        """
        events = self.dedup_store.get_events_by_topic(topic)
        return [
            {'topic': t, 'event_id': eid}
            for t, eid in events
        ]
