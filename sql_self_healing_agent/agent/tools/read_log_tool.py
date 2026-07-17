from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import AgentContext


class ReadLogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    log_path: str = Field(min_length=1)
    max_lines: int | None = Field(default=None, ge=1, le=2000)


class ReadLogOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["AVAILABLE", "MISSING", "FAILED"]
    summary: str | None = None
    log_digest_ref: str | None = None
    error_code: str | None = None


class ReadLogTool:
    name = "ReadLogTool"
    description = "读取当前事件日志并返回受限摘要"
    input_model = ReadLogInput
    output_model = ReadLogOutput
    allowed_phases = {"INIT", "DIAGNOSING"}
    max_output_tokens = 1000
    produces_artifact = False

    def run(self, context: AgentContext, input_data: ReadLogInput) -> ReadLogOutput:
        if input_data.log_path != context.log_path:
            return ReadLogOutput(status="FAILED", error_code="LOG_PATH_FORBIDDEN")
        path = Path(input_data.log_path)
        if not path.exists():
            return ReadLogOutput(status="MISSING", error_code="LOG_NOT_FOUND")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ReadLogOutput(status="FAILED", error_code="LOG_READ_FAILED")
        selected = lines[: input_data.max_lines or 200]
        summary = " | ".join(line[:300] for line in selected[-20:])[:4000]
        return ReadLogOutput(status="AVAILABLE", summary=summary)
