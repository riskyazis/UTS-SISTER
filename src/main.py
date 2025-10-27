"""
FastAPI application untuk Pub-Sub Log Aggregator.
Menyediakan endpoint untuk publish event dan query statistics.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .models import Event, EventBatch, StatsResponse
from .dedup_store import DedupStore
from .consumer import EventConsumer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global instances
event_queue: asyncio.Queue = None
dedup_store: DedupStore = None
consumer: EventConsumer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifecycle manager untuk startup dan shutdown.
    Initialize dedup store, queue, dan consumer.
    """
    global event_queue, dedup_store, consumer
    
    logger.info("Starting Pub-Sub Log Aggregator...")
    
    # Initialize components
    event_queue = asyncio.Queue()
    dedup_store = DedupStore()
    consumer = EventConsumer(dedup_store, event_queue)
    
    # Start consumer
    await consumer.start()
    
    logger.info("Pub-Sub Log Aggregator started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Pub-Sub Log Aggregator...")
    await consumer.stop()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Pub-Sub Log Aggregator",
    description="Idempotent consumer dengan deduplication untuk event/log aggregation",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "Pub-Sub Log Aggregator",
        "status": "running",
        "version": "1.0.0"
    }


@app.post("/publish", status_code=202)
async def publish_events(payload: Union[Event, EventBatch]):
    """
    Publish event atau batch events ke aggregator.
    
    Args:
        payload: Single Event atau EventBatch
        
    Returns:
        Acceptance message
        
    Raises:
        HTTPException: Jika validation gagal
    """
    try:
        # Handle single event atau batch
        if isinstance(payload, Event):
            events = [payload]
        else:
            events = payload.events
        
        # Put events ke queue untuk processing
        for event in events:
            await event_queue.put(event)
        
        logger.info(f"Accepted {len(events)} event(s) for processing")
        
        return {
            "status": "accepted",
            "count": len(events),
            "message": f"{len(events)} event(s) queued for processing"
        }
    
    except Exception as e:
        logger.error(f"Error publishing events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/events")
async def get_events(topic: Optional[str] = Query(None, description="Filter by topic")):
    """
    Get daftar event unik yang telah diproses.
    
    Args:
        topic: Optional filter by topic
        
    Returns:
        List of processed events
    """
    try:
        events = consumer.get_events(topic)
        
        return {
            "count": len(events),
            "topic": topic,
            "events": events
        }
    
    except Exception as e:
        logger.error(f"Error getting events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    """
    Get statistics aggregator.
    
    Returns:
        Statistics: received, unique_processed, duplicate_dropped, topics, uptime
    """
    try:
        stats = consumer.get_stats()
        return StatsResponse(**stats)
    
    except Exception as e:
        logger.error(f"Error getting stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """
    Health check endpoint untuk monitoring.
    
    Returns:
        Health status
    """
    stats = consumer.get_stats()
    
    return {
        "status": "healthy",
        "consumer_running": consumer.running,
        "queue_size": event_queue.qsize(),
        "unique_processed": stats['unique_processed'],
        "uptime": stats['uptime']
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
