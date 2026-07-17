from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


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

    @model_validator(mode="after")
    def validate_status_fields(self) -> "AgentExternalResult":
        if self.status == "SQL_READY" and not (self.sql and self.sql.strip()):
            raise ValueError("SQL_READY requires sql")
        if self.status == "HUMAN_REQUIRED" and not (self.message and self.message.strip()):
            raise ValueError("HUMAN_REQUIRED requires message")
        if self.status != "SQL_READY" and self.sql is not None:
            raise ValueError(f"{self.status} must not include sql")
        return self
