import json
import os
import subprocess
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
DEFAULT_OLLAMA_MODEL = "modelscope2ollama-registry.azurewebsites.net/qwen/Qwen2.5-1.5B-Instruct-gguf:latest"


class LLMClientError(RuntimeError):
    pass


class LLMClient(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        """Generate and validate exactly one structured response."""


class OllamaLLMClient(LLMClient):
    def __init__(self, model: str | None = None, timeout_seconds: int = 120) -> None:
        self.model = model or os.environ.get("SQL_HEAL_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.timeout_seconds = timeout_seconds

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        base_prompt = prompt.split("\nJSON Schema:\n", 1)[0]
        repair_prompt = base_prompt
        last_error: Exception | None = None
        for _ in range(2):
            try:
                completed = subprocess.run(
                    ["ollama", "run", self.model, "--format", schema, repair_prompt],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env={**os.environ, "OLLAMA_NOHISTORY": "1"},
                )
                return response_model.model_validate(json.loads(completed.stdout))
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValidationError) as error:
                last_error = error
                repair_prompt = (
                    base_prompt
                    + "\n上一次输出不符合 JSON Schema。只输出一个合法单行 JSON 对象，不要 Markdown。"
                )
        raise LLMClientError(f"Ollama structured output failed: {last_error}") from last_error
