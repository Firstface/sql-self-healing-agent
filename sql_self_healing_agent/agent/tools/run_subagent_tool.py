from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_runner import SubAgentRunner


class RunSubAgentTool:
    name = "RunSubAgentTool"
    description = "启动受限且不可递归的 SubAgent"
    input_model = SubAgentRequest
    output_model = SubAgentResult
    allowed_phases = {"DIAGNOSING", "PLANNING", "GENERATING"}
    max_output_tokens = 2000
    produces_artifact = True

    def __init__(self, runner: SubAgentRunner) -> None:
        self.runner = runner

    def run(self, context: AgentContext, input_data: SubAgentRequest) -> SubAgentResult:
        return self.runner.run(input_data, context)
