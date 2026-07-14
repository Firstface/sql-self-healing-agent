from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    event_id: str
    session_id: str
    attempt_id: str | None = None
    event_type: str
    stage: str
    timestamp: str
    payload: dict = Field(default_factory=dict)
