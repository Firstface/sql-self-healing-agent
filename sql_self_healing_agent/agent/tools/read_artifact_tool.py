from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import AgentContext


class ReadArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_ref: str
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
    description = "读取当前 Session 所属 Artifact 的受限内容"
    input_model = ReadArtifactInput
    output_model = ReadArtifactOutput
    allowed_phases = {"INIT", "DIAGNOSING", "PLANNING", "GENERATING", "GATING"}
    max_output_tokens = 3000
    produces_artifact = False

    def run(self, context: AgentContext, input_data: ReadArtifactInput) -> ReadArtifactOutput:
        prefix = f"artifact://{context.session_id}/"
        if not input_data.artifact_ref.startswith(prefix):
            return ReadArtifactOutput(status="FORBIDDEN")
        path = Path(input_data.artifact_ref.removeprefix("artifact://"))
        if not path.exists():
            return ReadArtifactOutput(status="NOT_FOUND")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ReadArtifactOutput(status="FAILED")
        return ReadArtifactOutput(status="SUCCEEDED", content_summary=text[: input_data.max_chars], truncated=len(text) > input_data.max_chars)
