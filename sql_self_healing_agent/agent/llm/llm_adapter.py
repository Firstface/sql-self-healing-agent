from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from sql_self_healing_agent.agent.hooks.hook_manager import HookManager
from sql_self_healing_agent.llm.llm_client import LLMClient

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMCallContext:
    session_id: str
    attempt_id: str
    caller: str = "MAIN_AGENT"


class LLMAdapter:
    """Routes structured provider calls through the run-scoped HookManager."""

    def __init__(self, client: LLMClient, hook_manager: HookManager, context: LLMCallContext) -> None:
        self.client = client
        self.hook_manager = hook_manager
        self.context = context

    def generate_structured(self, prompt: str, response_model: type[T], *, purpose: str, input_summary: str, timeout_ms: int | None = None) -> T:
        return self.hook_manager.execute_llm_call(
            lambda schema_feedback: self._generate(
                self._with_schema_feedback(prompt, schema_feedback), response_model, timeout_ms
            ),
            session_id=self.context.session_id,
            attempt_id=self.context.attempt_id,
            purpose=purpose,
            input_summary=input_summary,
            caller=self.context.caller,
        )

    def _generate(self, prompt: str, response_model: type[T], timeout_ms: int | None) -> T:
        if timeout_ms is None or not hasattr(self.client, "timeout_seconds"):
            return self.client.generate_structured(prompt, response_model)
        original = self.client.timeout_seconds
        self.client.timeout_seconds = max(1, timeout_ms // 1000)
        try:
            return self.client.generate_structured(prompt, response_model)
        finally:
            self.client.timeout_seconds = original

    @staticmethod
    def _with_schema_feedback(prompt: str, schema_feedback: str | None) -> str:
        if not schema_feedback:
            return prompt
        return f"{prompt}\nSCHEMA_RETRY_FEEDBACK_START\n{schema_feedback}\nSCHEMA_RETRY_FEEDBACK_END"
