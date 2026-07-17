from collections.abc import Callable
from typing import TypeVar

from sql_self_healing_agent.llm.llm_client import LLMClientError, LLMErrorType

T = TypeVar("T")


class LLMRetryHook:
    def run(self, operation: Callable[[str | None], T]) -> T:
        transient_retries = 0
        schema_retries = 0
        schema_feedback: str | None = None
        while True:
            try:
                return operation(schema_feedback)
            except LLMClientError as error:
                if error.error_type is LLMErrorType.SCHEMA_ERROR and schema_retries < 1:
                    schema_retries += 1
                    schema_feedback = "上一次输出未通过结构化 Schema，仅返回合法 JSON。"
                    continue
                if error.error_type in {LLMErrorType.TRANSIENT_ERROR, LLMErrorType.TIMEOUT, LLMErrorType.SERVICE_ERROR} and transient_retries < 2:
                    transient_retries += 1
                    continue
                raise
