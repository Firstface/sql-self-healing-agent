import json
import os
import subprocess
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
DEFAULT_OLLAMA_MODEL = "modelscope2ollama-registry.azurewebsites.net/qwen/Qwen2.5-1.5B-Instruct-gguf:latest"
DEFAULT_ARK_BASE_URL = "https://ark-i18n-tt.byteintl.net/api/v3"
DEFAULT_ARK_MODEL = "ep-20260414061830-6zg7m"


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


class ArkLLMClient(LLMClient):
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 60,
        client: object | None = None,
    ) -> None:
        self.model = model or os.environ.get("ARK_MODEL", DEFAULT_ARK_MODEL)
        self.base_url = base_url or os.environ.get("ARK_BASE_URL", DEFAULT_ARK_BASE_URL)
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.environ.get("ARK_API_KEY")
            if not resolved_key:
                raise LLMClientError("Ark API key is not configured (ARK_API_KEY missing).")
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=resolved_key)

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        system_prompt, user_prompt = self._split_prompt(prompt)
        repair_user_prompt = user_prompt
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": repair_user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    timeout=self.timeout_seconds,
                )
                content = response.choices[0].message.content or ""
                return response_model.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError) as error:
                last_error = error
                repair_user_prompt = (
                    user_prompt
                    + "\n上一次输出不符合 JSON Schema。只输出一个合法单行 JSON 对象，不要 Markdown。"
                )
            except Exception as error:
                raise LLMClientError(
                    f"Ark structured output failed: {type(error).__name__}"
                ) from None
        raise LLMClientError(
            f"Ark structured output failed after retry: {type(last_error).__name__}"
        ) from None

    @staticmethod
    def _split_prompt(prompt: str) -> tuple[str, str]:
        marker = "\n<<<INPUT_START>>>\n"
        if marker in prompt:
            system_prompt, rest = prompt.split(marker, 1)
            return system_prompt, marker.lstrip("\n") + rest
        return "You are an assistant that outputs a single JSON object.", prompt


def build_llm_client_from_env() -> LLMClient:
    provider = os.environ.get("SQL_HEAL_LLM_PROVIDER", "ollama").strip().lower()
    if provider == "ollama":
        return OllamaLLMClient()
    if provider == "ark":
        return ArkLLMClient()
    raise LLMClientError(
        f"Unknown SQL_HEAL_LLM_PROVIDER: {provider!r} (expected 'ollama' or 'ark')."
    )
