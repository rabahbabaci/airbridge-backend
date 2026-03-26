"""Analytics event ingestion endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.middleware.auth import get_optional_user
from app.db import get_db
from app.db.models import Event, User

router = APIRouter(tags=["events"])


class EventRequest(BaseModel):
    event_name: str
    metadata: dict | None = None


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
