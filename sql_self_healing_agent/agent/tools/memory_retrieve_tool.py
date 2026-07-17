from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.agent.models.context import AgentContext


class MemoryRetrieveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diagnosed_keywords: list[str]
    query_summary: str | None = None
    limit: int | None = Field(default=None, ge=1, le=20)


class ExperienceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience_id: str
    keyword: list[str]
    description: str
    matched_by: list[str]
    artifact_ref: str | None = None


class MemoryToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matched: bool
    matched_experiences: list[ExperienceSummary]
    scanned_count: int
    discarded_count: int
    artifact_ref: str | None = None
    summary: str | None = None


class MemoryRetrieveTool:
    name = "MemoryRetrieveTool"
    description = "检索成功经验摘要"
    input_model = MemoryRetrieveInput
    output_model = MemoryToolOutput
    allowed_phases = {"DIAGNOSING", "PLANNING", "GENERATING"}
    max_output_tokens = 2000
    produces_artifact = True

    def __init__(self, retriever) -> None:
        self.retriever = retriever

    def run(self, context: AgentContext, input_data: MemoryRetrieveInput) -> MemoryToolOutput:
        result = self.retriever.retrieve_keywords(input_data.diagnosed_keywords, input_data.query_summary, input_data.limit)
        return MemoryToolOutput.model_validate(result)
