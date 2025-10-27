"""
Publisher simulasi untuk testing.
Mengirim event ke aggregator dengan simulasi duplikasi.
"""
import asyncio
import httpx
import logging
import random
import uuid
from datetime import datetime
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGGREGATOR_URL = os.getenv("AGGREGATOR_URL", "http://localhost:8080")


async def generate_event(topic: str, event_id: str = None, source: str = "publisher") -> dict:
    """Generate sample event"""
    if not event_id:
        event_id = f"evt-{uuid.uuid4().hex[:8]}"
    
    return {
        "topic": topic,
        "event_id": event_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "source": source,
        "payload": {
            "level": random.choice(["INFO", "WARNING", "ERROR"]),
            "message": f"Sample log message {event_id}",
            "random_value": random.randint(1, 1000)
        }
    }


async def publish_event(client: httpx.AsyncClient, event: dict):
    """Publish single event"""
    try:
        response = await client.post(f"{AGGREGATOR_URL}/publish", json=event)
        response.raise_for_status()
        logger.info(f"Published: {event['event_id']}")
    except Exception as e:
        logger.error(f"Failed to publish {event['event_id']}: {e}")


async def simulate_duplicate_delivery():
    """
    Simulasi at-least-once delivery dengan duplikasi.
    Mengirim 5000+ events dengan ~20% duplikasi.
    """
    logger.info("Starting publisher simulation...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Wait for aggregator to be ready
        for i in range(30):
            try:
                response = await client.get(f"{AGGREGATOR_URL}/health")
                if response.status_code == 200:
                    logger.info("Aggregator is ready")
                    break
            except:
                logger.info(f"Waiting for aggregator... ({i+1}/30)")
                await asyncio.sleep(2)
        
        topics = ["application.logs", "system.metrics", "user.events", "error.reports"]
        
        # Generate unique events
        unique_events = []
        for i in range(4000):
            topic = random.choice(topics)
            event = await generate_event(topic, f"unique-{i:05d}")
            unique_events.append(event)
        
        logger.info(f"Generated {len(unique_events)} unique events")
        
        # Create duplicates (20% duplication rate)
        all_events = unique_events.copy()
        num_duplicates = int(len(unique_events) * 0.25)  # 25% untuk mencapai 5000+ total
        
        for _ in range(num_duplicates):
            duplicate = random.choice(unique_events)
            all_events.append(duplicate)
        
        # Shuffle untuk random order
        random.shuffle(all_events)
        
        logger.info(f"Total events to send: {len(all_events)} (including duplicates)")
        
        # Send events in batches
        batch_size = 50
        for i in range(0, len(all_events), batch_size):
            batch = all_events[i:i+batch_size]
            tasks = [publish_event(client, event) for event in batch]
            await asyncio.gather(*tasks)
            
            if (i // batch_size + 1) % 10 == 0:
                logger.info(f"Progress: {i + len(batch)}/{len(all_events)} events sent")
        
        logger.info(f"Finished sending {len(all_events)} events")
        
        # Get final stats
        await asyncio.sleep(2)  # Wait for processing
        try:
            response = await client.get(f"{AGGREGATOR_URL}/stats")
            stats = response.json()
            logger.info(f"Final stats: {stats}")
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")


if __name__ == "__main__":
    asyncio.run(simulate_duplicate_delivery())
