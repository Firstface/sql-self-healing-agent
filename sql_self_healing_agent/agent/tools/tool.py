from typing import Protocol

from pydantic import BaseModel

from sql_self_healing_agent.agent.models.context import AgentContext


class Tool(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    allowed_phases: set[str]
    max_output_tokens: int
    produces_artifact: bool

    def run(self, context: AgentContext, input_data: BaseModel) -> BaseModel: ...
