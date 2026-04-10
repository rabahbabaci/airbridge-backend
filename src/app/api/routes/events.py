"""Analytics event ingestion endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.api.middleware.auth import get_optional_user
from app.db import get_db
from app.db.models import Event, User

router = APIRouter(tags=["events"])


class EventRequest(BaseModel):
    event_name: str = Field(..., max_length=100)
    metadata: dict | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def limit_metadata_size(cls, v):
        """Reject metadata payloads larger than 10KB to prevent abuse."""
        if v is not None:
            import json
            if len(json.dumps(v)) > 10_000:
                raise ValueError("metadata payload exceeds 10KB limit")
        return v


@router.post("", status_code=200)
async def record_event(
    body: EventRequest,
    user: User | None = Depends(get_optional_user),
    db=Depends(get_db),
):
    if db is None:
        return {"status": "recorded"}

    event = Event(
        event_name=body.event_name,
        user_id=user.id if user else None,
        event_metadata=body.metadata,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return {"status": "recorded", "event_id": str(event.id)}
