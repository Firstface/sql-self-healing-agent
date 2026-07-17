from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactType = Literal[
    "RAW_LOG",
    "LOG_DIGEST",
    "DIAGNOSIS",
    "METADATA_SNAPSHOT",
    "MEMORY_RETRIEVAL",
    "CANDIDATE_SQL",
    "GATE_RESULT",
    "SUB_AGENT_OUTPUT",
    "CONTEXT_SNAPSHOT",
    "TRACE_PAYLOAD",
]


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    session_id: str
    attempt_id: str | None = None
    artifact_type: ArtifactType
    path: str
    content_hash: str
    size_bytes: int = Field(ge=0)
    token_estimate: int = Field(ge=0)
    sanitized: bool
    created_at: str
