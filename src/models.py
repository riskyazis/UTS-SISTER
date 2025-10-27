"""
Model untuk Event yang akan diproses oleh aggregator.
Menggunakan Pydantic untuk validasi schema.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict
from datetime import datetime


class Event(BaseModel):
    """
    Model Event dengan validasi schema.
    
    Attributes:
        topic: Nama topic/kategori event
        event_id: Unique identifier untuk event (untuk deduplication)
        timestamp: Waktu event dalam format ISO8601
        source: Sumber event (publisher identifier)
        payload: Data event dalam bentuk dictionary
    """
    topic: str = Field(..., min_length=1, max_length=255)
    event_id: str = Field(..., min_length=1, max_length=255)
    timestamp: str = Field(...)
    source: str = Field(..., min_length=1, max_length=255)
    payload: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator('timestamp')
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validasi format ISO8601 timestamp"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('timestamp must be in ISO8601 format')
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "topic": "application.logs",
                "event_id": "evt-123456",
                "timestamp": "2025-10-25T10:30:00Z",
                "source": "service-a",
                "payload": {
                    "level": "INFO",
                    "message": "User logged in",
                    "user_id": "user-001"
                }
            }
        }


class EventBatch(BaseModel):
    """Model untuk batch events"""
    events: list[Event] = Field(..., min_length=1)


class StatsResponse(BaseModel):
    """Response model untuk endpoint /stats"""
    received: int = Field(..., description="Total events received")
    unique_processed: int = Field(..., description="Total unique events processed")
    duplicate_dropped: int = Field(..., description="Total duplicate events dropped")
    topics: list[str] = Field(..., description="List of unique topics")
    uptime: float = Field(..., description="Uptime in seconds")
