from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.artifacts.artifact_store import ArtifactAccessError, ArtifactIntegrityError, ArtifactStore


class ReadArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_ref: ArtifactRef
    section: str | None = None
    max_chars: int = Field(default=12000, ge=1, le=12000)


class ReadArtifactOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["SUCCEEDED", "NOT_FOUND", "FORBIDDEN", "FAILED"]
    content_summary: str | None = None
    content_ref: str | None = None
    truncated: bool = False


class ReadArtifactTool:
    name = "ReadArtifactTool"
    description = "读取当前 Session 所属且已经脱敏的 Artifact 受限内容"
    input_model = ReadArtifactInput
    output_model = ReadArtifactOutput
    allowed_phases = {"INIT", "DIAGNOSING", "PLANNING", "GENERATING", "GATING"}
    max_output_tokens = 3000
    produces_artifact = False

    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    def run(self, context: AgentContext, input_data: ReadArtifactInput) -> ReadArtifactOutput:
        ref = input_data.artifact_ref
        if ref.session_id != context.session_id or (ref.attempt_id is not None and ref.attempt_id != context.attempt_id) or not ref.sanitized:
            return ReadArtifactOutput(status="FORBIDDEN")
        if not self.store.exists(ref):
            return ReadArtifactOutput(status="NOT_FOUND")
        try:
            full_content = self.store.load(ref, session_id=context.session_id, attempt_id=ref.attempt_id)
        except ArtifactAccessError:
            return ReadArtifactOutput(status="FORBIDDEN")
        except (ArtifactIntegrityError, OSError):
            return ReadArtifactOutput(status="FAILED")
        content = full_content[: input_data.max_chars]
        return ReadArtifactOutput(status="SUCCEEDED", content_summary=content, content_ref=ref.path, truncated=len(full_content) > input_data.max_chars)
