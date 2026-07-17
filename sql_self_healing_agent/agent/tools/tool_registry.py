from pydantic import ValidationError

from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.run_state import AgentRunState
from sql_self_healing_agent.agent.models.tool_models import ToolCallResult, ToolSpec
from sql_self_healing_agent.agent.tools.tool import Tool
from sql_self_healing_agent.core.time_utils import utc_now_iso


class ToolRegistry:
    ALLOWED_NAMES = {"ReadLogTool", "MetadataQueryTool", "MemoryRetrieveTool", "ReadArtifactTool", "RunSubAgentTool"}
    FORBIDDEN_NAMES = {"ExecuteSQLTool", "WriteSessionTool", "WriteMemoryTool", "BypassGateTool", "KnowledgeRetrieveTool", "TodoWriteTool"}

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name not in self.ALLOWED_NAMES or tool.name in self.FORBIDDEN_NAMES:
            raise ValueError(f"tool registration is forbidden: {tool.name}")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def list_available(self, phase: str) -> list[ToolSpec]:
        return [self._spec(tool) for tool in self._tools.values() if phase in tool.allowed_phases]

    def execute(self, name: str, context: AgentContext, tool_input: dict[str, object], run_state: AgentRunState) -> ToolCallResult:
        started = utc_now_iso()
        try:
            tool = self.get(name)
        except KeyError:
            return self._failed(name, "BLOCKED", "TOOL_NOT_REGISTERED", started)
        if context.phase not in tool.allowed_phases:
            return self._failed(name, "BLOCKED", "TOOL_PHASE_FORBIDDEN", started)
        try:
            validated = tool.input_model.model_validate(tool_input)
        except ValidationError:
            return self._failed(name, "INVALID_INPUT", "TOOL_INPUT_INVALID", started)
        try:
            output = tool.output_model.model_validate(tool.run(context, validated))
        except Exception as error:
            return ToolCallResult(tool_name=name, status="FAILED", error_code="TOOL_FAILED", error_message=type(error).__name__, started_at=started, finished_at=utc_now_iso())
        return ToolCallResult(tool_name=name, status="SUCCEEDED", summary=getattr(output, "summary", None), artifact_refs=[ref for ref in self._artifact_refs(output) if ref], started_at=started, finished_at=utc_now_iso())

    @staticmethod
    def _artifact_refs(output) -> list[str | None]:
        return [value for key, value in output.model_dump().items() if key.endswith("_ref") and isinstance(value, str)]

    @staticmethod
    def _failed(name: str, status: str, code: str, started: str) -> ToolCallResult:
        return ToolCallResult(tool_name=name, status=status, error_code=code, started_at=started, finished_at=utc_now_iso())

    @staticmethod
    def _spec(tool: Tool) -> ToolSpec:
        return ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_model.model_json_schema(), allowed_phases=sorted(tool.allowed_phases), side_effect_level="INTERNAL_ARTIFACT_WRITE" if tool.produces_artifact else "READ_ONLY")
