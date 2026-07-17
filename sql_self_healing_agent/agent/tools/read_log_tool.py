from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.logs.log_compressor import LogCompressor


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

    def __init__(
        self,
        artifact_store: ArtifactStore | None = None,
        keyword_vocab: dict[str, list[str]] | None = None,
    ) -> None:
        self.artifact_store = artifact_store or ArtifactStore()
        self.keyword_vocab = keyword_vocab or {}
        self.compressor = LogCompressor()

    def run(self, context: AgentContext, input_data: ReadLogInput) -> ReadLogOutput:
        if input_data.log_path != context.log_path:
            return ReadLogOutput(status="FAILED", error_code="LOG_PATH_FORBIDDEN")
        if not Path(input_data.log_path).exists():
            return ReadLogOutput(status="MISSING", error_code="LOG_NOT_FOUND")
        digest = self.compressor.build_digest(
            input_data.log_path,
            context.error_message,
            self.keyword_vocab,
        )
        if not digest.log_readable:
            return ReadLogOutput(status="FAILED", error_code="LOG_READ_FAILED")
        ref = self.artifact_store.save_json_ref(
            context.session_id,
            context.attempt_id,
            "read_log_digest.json",
            digest.model_dump(mode="json"),
            "LOG_DIGEST",
        )
        summary = digest.root_cause_summary or digest.suspected_engine_error or "log digest available"
        return ReadLogOutput(
            status="AVAILABLE",
            summary=summary[:4000],
            log_digest_ref=ref.model_dump_json(),
        )
