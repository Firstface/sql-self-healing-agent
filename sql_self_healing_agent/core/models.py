from typing import Literal

from pydantic import BaseModel, ConfigDict


class UpstreamTaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["FAILED", "SUCCESS"]
    sql: str
    error_message: str | None = None
    log_path: str | None = None


class AgentExternalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["SQL_READY", "NO_SQL", "HUMAN_REQUIRED", "SUCCESS_ACK"]
    sql: str | None = None
    message: str | None = None
