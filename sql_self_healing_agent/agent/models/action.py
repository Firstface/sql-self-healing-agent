from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from sql_self_healing_agent.agent.models.execution_plan import ExecutionPlan


class SubAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    task: str
    context_keys: list[str]


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["TOOL_CALL", "UPDATE_PLAN", "RUN_SUB_AGENT", "PROPOSE_SQL_CANDIDATE", "RETURN_NO_SQL", "RETURN_HUMAN_REQUIRED"]
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    execution_plan: ExecutionPlan | None = None
    sub_agent_request: SubAgentRequest | None = None
    candidate_sql: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "AgentAction":
        required = {
            "TOOL_CALL": self.tool_name is not None and self.tool_input is not None,
            "UPDATE_PLAN": self.execution_plan is not None,
            "RUN_SUB_AGENT": self.sub_agent_request is not None,
            "PROPOSE_SQL_CANDIDATE": bool(self.candidate_sql and self.candidate_sql.strip()),
            "RETURN_NO_SQL": bool(self.reason),
            "RETURN_HUMAN_REQUIRED": bool(self.reason),
        }
        if not required[self.type]:
            raise ValueError(f"invalid fields for action {self.type}")
        return self
