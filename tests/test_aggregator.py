"""
Unit tests untuk Pub-Sub Log Aggregator.
Coverage: dedup, persistensi, schema validation, stats, stress test.
"""
import pytest
import asyncio
import httpx
import tempfile
import os
from datetime import datetime
from pathlib import Path

from src.models import Event, EventBatch, StatsResponse
from src.dedup_store import DedupStore
from src.consumer import EventConsumer
from src.main import app


@pytest.fixture
def temp_db():
    """Temporary database untuk testing"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def dedup_store(temp_db):
    """DedupStore instance untuk testing"""
    return DedupStore(db_path=temp_db)


@pytest.fixture
async def consumer(dedup_store):
    """EventConsumer instance untuk testing"""
    queue = asyncio.Queue()
    consumer = EventConsumer(dedup_store, queue)
    await consumer.start()
    yield consumer
    await consumer.stop()


@pytest.fixture
async def client():
    """HTTP client untuk testing API"""
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield client


# Test 1: Validasi Schema Event
def test_event_schema_validation():
    """Test validasi schema event dengan Pydantic"""
    # Valid event
    valid_event = Event(
        topic="test.topic",
        event_id="evt-001",
        timestamp="2025-10-25T10:30:00Z",
        source="test-source",
        payload={"key": "value"}
    )
    assert valid_event.topic == "test.topic"
    assert valid_event.event_id == "evt-001"
    
    # Invalid timestamp
    with pytest.raises(ValueError):
        Event(
            topic="test",
            event_id="evt-002",
            timestamp="invalid-timestamp",
            source="test",
            payload={}
        )
    
    # Missing required field
    with pytest.raises(ValueError):
        Event(
            topic="test",
            event_id="evt-003",
            # Missing timestamp
            source="test",
            payload={}
        )


# Test 2: Deduplication - Kirim Duplikat
def test_deduplication_basic(dedup_store):
    """Test deduplication: event yang sama hanya diproses sekali"""
    topic = "test.topic"
    event_id = "evt-dedup-001"
    
    # First event: should be new
    is_dup = dedup_store.is_duplicate(topic, event_id)
    assert is_dup is False
    
    # Mark as processed
    marked = dedup_store.mark_processed(topic, event_id)
    assert marked is True
    
    # Second attempt: should be duplicate
    is_dup = dedup_store.is_duplicate(topic, event_id)
    assert is_dup is True
    
    # Try to mark again: should fail
    marked = dedup_store.mark_processed(topic, event_id)
    assert marked is False


# Test 3: Persistensi Dedup Store - Restart Simulation
def test_dedup_persistence_after_restart(temp_db):
    """Test persistensi dedup store: data tetap ada setelah restart"""
    topic = "persistent.topic"
    event_id = "evt-persist-001"
    
    # First instance
    store1 = DedupStore(db_path=temp_db)
    store1.mark_processed(topic, event_id)
    
    # Simulate restart: create new instance dengan database yang sama
    store2 = DedupStore(db_path=temp_db)
    
    # Should still be marked as processed
    is_dup = store2.is_duplicate(topic, event_id)
    assert is_dup is True
    
    # Should prevent reprocessing
    marked = store2.mark_processed(topic, event_id)
    assert marked is False


# Test 4: GET /stats Konsisten dengan Data
@pytest.mark.asyncio
async def test_stats_endpoint_consistency(consumer):
    """Test endpoint /stats mengembalikan data yang konsisten"""
    # Initial stats
    stats = consumer.get_stats()
    assert stats['received'] == 0
    assert stats['unique_processed'] == 0
    assert stats['duplicate_dropped'] == 0
    assert stats['uptime'] >= 0
    
    # Add events
    event1 = Event(
        topic="stats.test",
        event_id="evt-stats-001",
        timestamp=datetime.utcnow().isoformat() + "Z",
        source="test",
        payload={}
    )
    event2 = Event(
        topic="stats.test",
        event_id="evt-stats-001",  # Duplicate
        timestamp=datetime.utcnow().isoformat() + "Z",
        source="test",
        payload={}
    )
    
    await consumer.queue.put(event1)
    await consumer.queue.put(event2)
    
    # Wait for processing
    await asyncio.sleep(0.5)
    
    stats = consumer.get_stats()
    assert stats['received'] == 2
    assert stats['unique_processed'] == 1
    assert stats['duplicate_dropped'] == 1
    assert 'stats.test' in stats['topics']


# Test 5: GET /events Filtering by Topic
@pytest.mark.asyncio
async def test_get_events_filtering(consumer):
    """Test endpoint /events dengan filtering by topic"""
    # Add events dengan berbagai topic
    events = [
        Event(topic="topic.a", event_id="evt-a-001", timestamp=datetime.utcnow().isoformat() + "Z", source="test", payload={}),
        Event(topic="topic.a", event_id="evt-a-002", timestamp=datetime.utcnow().isoformat() + "Z", source="test", payload={}),
        Event(topic="topic.b", event_id="evt-b-001", timestamp=datetime.utcnow().isoformat() + "Z", source="test", payload={}),
    ]
    
    for event in events:
        await consumer.queue.put(event)
    
    await asyncio.sleep(0.5)
    
    # Get events by topic
    events_a = consumer.get_events(topic="topic.a")
    assert len(events_a) == 2
    assert all(e['topic'] == 'topic.a' for e in events_a)
    
    events_b = consumer.get_events(topic="topic.b")
    assert len(events_b) == 1
    assert events_b[0]['topic'] == 'topic.b'
    
    # Get all events
    all_events = consumer.get_events()
    assert len(all_events) == 3


# Test 6: Stress Test - 5000+ Events dengan Duplikasi
@pytest.mark.asyncio
async def test_stress_batch_processing(consumer):
    """Test performa dengan 5000+ events (20% duplikasi)"""
    import time
    
    num_unique = 4000
    num_duplicates = 1000
    
    # Generate unique events
    unique_events = []
    for i in range(num_unique):
        event = Event(
            topic=f"topic.{i % 10}",
            event_id=f"evt-stress-{i:05d}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            source="stress-test",
            payload={"index": i}
        )
        unique_events.append(event)
    
    # Add duplicates
    import random
    all_events = unique_events.copy()
    for _ in range(num_duplicates):
        duplicate = random.choice(unique_events)
        all_events.append(duplicate)
    
    random.shuffle(all_events)
    
    # Measure time
    start_time = time.time()
    
    # Put all events to queue
    for event in all_events:
        await consumer.queue.put(event)
    
    # Wait for processing (with timeout)
    await asyncio.sleep(10)  # Give enough time for processing
    
    elapsed_time = time.time() - start_time
    
    # Verify stats
    stats = consumer.get_stats()
    
    assert stats['received'] == len(all_events)
    assert stats['unique_processed'] == num_unique
    assert stats['duplicate_dropped'] == num_duplicates
    
    # Performance check: should process within reasonable time
    assert elapsed_time < 30, f"Processing took too long: {elapsed_time:.2f}s"
    
    # Calculate throughput
    throughput = len(all_events) / elapsed_time
    print(f"\nStress test results:")
    print(f"  Total events: {len(all_events)}")
    print(f"  Unique: {num_unique}")
    print(f"  Duplicates: {num_duplicates}")
    print(f"  Time: {elapsed_time:.2f}s")
    print(f"  Throughput: {throughput:.2f} events/sec")


# Test 7: Idempotency - Multiple Retries
@pytest.mark.asyncio
async def test_idempotent_consumer_retries(consumer):
    """Test idempotency: consumer tetap idempotent meski ada retry"""
    event = Event(
        topic="retry.test",
        event_id="evt-retry-001",
        timestamp=datetime.utcnow().isoformat() + "Z",
        source="retry-test",
        payload={"attempt": 1}
    )
    
    # Simulate multiple retries (send same event 5 times)
    for i in range(5):
        await consumer.queue.put(event)
    
    await asyncio.sleep(0.5)
    
    # Should only process once
    stats = consumer.get_stats()
    assert stats['unique_processed'] == 1
    assert stats['duplicate_dropped'] == 4


# Test 8: Topic Isolation
def test_topic_isolation(dedup_store):
    """Test bahwa event_id yang sama di topic berbeda tidak bentrok"""
    event_id = "evt-same-id"
    
    # Same event_id, different topics
    marked_a = dedup_store.mark_processed("topic.a", event_id)
    marked_b = dedup_store.mark_processed("topic.b", event_id)
    
    # Both should succeed (isolated by topic)
    assert marked_a is True
    assert marked_b is True
    
    # Verify both are marked
    assert dedup_store.is_duplicate("topic.a", event_id) is True
    assert dedup_store.is_duplicate("topic.b", event_id) is True


# Test 9: Empty Payload Handling
def test_empty_payload_handling():
    """Test handling event dengan payload kosong"""
    event = Event(
        topic="empty.test",
        event_id="evt-empty-001",
        timestamp="2025-10-25T10:30:00Z",
        source="test",
        payload={}
    )
    
    assert event.payload == {}
    assert event.topic == "empty.test"


# Test 10: Concurrent Processing
@pytest.mark.asyncio
async def test_concurrent_event_processing(consumer):
    """Test concurrent processing dari multiple events"""
    # Create batch of events
    events = [
        Event(
            topic=f"concurrent.{i % 3}",
            event_id=f"evt-concurrent-{i:03d}",
            timestamp=datetime.utcnow().isoformat() + "Z",
            source="concurrent-test",
            payload={"index": i}
        )
        for i in range(100)
    ]
    
    # Send all at once
    for event in events:
        await consumer.queue.put(event)
    
    await asyncio.sleep(1)
    
    # All should be processed
    stats = consumer.get_stats()
    assert stats['unique_processed'] == 100
    assert stats['duplicate_dropped'] == 0
