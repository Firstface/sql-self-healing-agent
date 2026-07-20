from pydantic import BaseModel, ConfigDict, Field, model_validator

from sql_self_healing_agent.agent.context import CompactionLimits
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits
from sql_self_healing_agent.agent.models.subagent_models import SubAgentLimits


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_limits: AgentRunLimits = Field(default_factory=AgentRunLimits)
    sub_agent_limits: SubAgentLimits = Field(default_factory=SubAgentLimits)
    compaction_limits: CompactionLimits = Field(default_factory=CompactionLimits)
    memory_max_context_hits: int = Field(default=5, ge=1, le=20)
    memory_unknown_scan_budget: int = Field(default=500, ge=1)
    llm_schema_retries: int = Field(default=1, ge=0, le=1)
    llm_transient_retries: int = Field(default=2, ge=0, le=2)
    agentic_enabled: bool = True
    llm_main_agent_enabled: bool = True

    @model_validator(mode="after")
    def validate_cross_limits(self) -> "AgentConfig":
        if self.run_limits.max_gate_repair_rounds != 1:
            raise ValueError("max_gate_repair_rounds must equal 1")
        if self.sub_agent_limits.max_steps > self.run_limits.max_steps:
            raise ValueError("SubAgent steps cannot exceed parent Agent")
        if self.sub_agent_limits.max_tool_calls > self.run_limits.max_tool_calls:
            raise ValueError("SubAgent tool calls cannot exceed parent Agent")
        if self.sub_agent_limits.max_wall_time_ms > self.run_limits.max_wall_time_ms:
            raise ValueError("SubAgent wall time cannot exceed parent Agent")
        if self.compaction_limits.max_calls > 2 or self.compaction_limits.timeout_ms > 10000:
            raise ValueError("compaction budget exceeds independent safety ceiling")
        return self
