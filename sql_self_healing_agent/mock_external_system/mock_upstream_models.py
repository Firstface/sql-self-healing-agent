from typing import Literal

from pydantic import BaseModel, ConfigDict

from sql_self_healing_agent.core.models import AgentExternalResult, UpstreamTaskEvent


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MockRound(StrictModel):
    round_no: int
    upstream_status: Literal["FAILED", "SUCCESS"]
    error_message: str | None = None
    log_path: str | None = None
    success_condition: dict | None = None


class MockScenario(StrictModel):
    scenario_id: str
    task_id: str
    max_retry_count: int = 3
    allow_medium_risk: bool = True
    initial_sql: str
    rounds: list[MockRound]

    def to_agent_failed_event(
        self, sql: str, round_def: MockRound
    ) -> UpstreamTaskEvent:
        return UpstreamTaskEvent(
            id=self.task_id,
            status="FAILED",
            sql=sql,
            error_message=round_def.error_message,
            log_path=round_def.log_path,
        )

    def to_agent_success_event(self, sql: str) -> UpstreamTaskEvent:
        return UpstreamTaskEvent(id=self.task_id, status="SUCCESS", sql=sql)


class MockExecutionResult(StrictModel):
    status: Literal["FAILED", "SUCCESS"]
    error_message: str | None = None
    log_path: str | None = None


class MockFinalResult(StrictModel):
    scenario_id: str
    task_id: str
    status: Literal[
        "MOCK_SUCCESS",
        "MOCK_HUMAN_REQUIRED",
        "MOCK_NO_SQL",
        "MOCK_RETRY_EXHAUSTED",
        "MOCK_UNEXPECTED",
    ]
    attempt_count: int
    message: str | None = None

    @classmethod
    def from_agent_result(
        cls,
        scenario: MockScenario,
        status: Literal["MOCK_HUMAN_REQUIRED", "MOCK_NO_SQL", "MOCK_UNEXPECTED"],
        attempt_count: int,
        result: AgentExternalResult,
    ) -> "MockFinalResult":
        return cls(
            scenario_id=scenario.scenario_id,
            task_id=scenario.task_id,
            status=status,
            attempt_count=attempt_count,
            message=result.message,
        )
