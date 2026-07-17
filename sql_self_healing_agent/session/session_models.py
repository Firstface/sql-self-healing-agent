from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisHistoryItem


EventProcessingStatus = Literal[
    "RECEIVED", "PROCESSING", "SUCCEEDED", "FAILED", "SYSTEM_ERROR"
]


class UpstreamTaskEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str
    task_id: str
    session_id: str
    status: Literal["FAILED", "SUCCESS"]
    sql: str
    error_message: str | None = None
    log_path: str | None = None
    received_at: str
    processing_status: EventProcessingStatus = "RECEIVED"
    attempt_id: str | None = None
    result_ref: str | None = None
    error_code: str | None = None
    finished_at: str | None = None


class RepairSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    session_id: str
    task_id: str
    original_sql: str
    status: SessionStatus = SessionStatus.CREATED
    latest_sql_candidate: str | None = None
    latest_sql_candidate_attempt_id: str | None = None
    confirmed_sql: str | None = None
    confirmed_attempt_id: str | None = None
    upstream_events: list[UpstreamTaskEventRecord] = Field(default_factory=list)
    attempt_ids: list[str] = Field(default_factory=list)
    diagnosis_history: list[DiagnosisHistoryItem] = Field(default_factory=list)
    trace_path: str
    artifact_dir: str
    last_external_result_ref: str | None = None
    agent_terminal_status: str | None = None
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_candidate_pair(self) -> "RepairSession":
        if (self.latest_sql_candidate is None) != (
            self.latest_sql_candidate_attempt_id is None
        ):
            raise ValueError("latest candidate and attempt id must be updated together")
        return self


class RepairAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    attempt_id: str
    attempt_no: int
    status: AttemptStatus = AttemptStatus.CREATED
    source_event_key: str
    input_event_id: str
    input_failed_sql: str
    input_error_message: str | None = None
    input_log_path: str | None = None
    previous_attempt_id: str | None = None
    post_reflection_result_path: str | None = None
    agent_run_state_path: str | None = None
    log_digest_path: str | None = None
    diagnosis_path: str | None = None
    metadata_snapshot_path: str | None = None
    memory_retrieval_path: str | None = None
    repair_plan_path: str | None = None
    sql_candidate_path: str | None = None
    validation_result_path: str | None = None
    pre_reflection_result_path: str | None = None
    sql_candidate: str | None = None
    diagnosed_error_type: str | None = None
    diagnosed_keywords: list[str] = Field(default_factory=list)
    error_fingerprint: str | None = None
    stop_reason: str | None = None
    created_at: str
    updated_at: str
