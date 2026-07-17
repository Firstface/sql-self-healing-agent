import json
import os
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
DEFAULT_OLLAMA_MODEL = "modelscope2ollama-registry.azurewebsites.net/qwen/Qwen2.5-1.5B-Instruct-gguf:latest"
DEFAULT_ARK_BASE_URL = "https://ark-i18n-tt.byteintl.net/api/v3"
DEFAULT_ARK_MODEL = "ep-20260414061830-6zg7m"


class LLMErrorType(str, Enum):
    SCHEMA_ERROR = "SCHEMA_ERROR"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    TIMEOUT = "TIMEOUT"
    AUTH_ERROR = "AUTH_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    SERVICE_ERROR = "SERVICE_ERROR"


class LLMClientError(RuntimeError):
    def __init__(self, error_type: LLMErrorType | str, message: str | None = None) -> None:
        if message is None:
            message = str(error_type)
            error_type = LLMErrorType.SERVICE_ERROR
        self.error_type = LLMErrorType(error_type)
        super().__init__(message)


class LLMClient(ABC):
    @abstractmethod
    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        """Perform exactly one provider call and one schema validation."""


class OllamaLLMClient(LLMClient):
    def __init__(self, model: str | None = None, timeout_seconds: int = 120) -> None:
        self.model = model or os.environ.get("SQL_HEAL_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.timeout_seconds = timeout_seconds

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        try:
            completed = subprocess.run(
                ["ollama", "run", self.model, "--format", schema, prompt.split("\nJSON Schema:\n", 1)[0]],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env={**os.environ, "OLLAMA_NOHISTORY": "1"},
            )
            return response_model.model_validate(json.loads(completed.stdout))
        except subprocess.TimeoutExpired:
            raise LLMClientError(LLMErrorType.TIMEOUT, "Ollama request timed out") from None
        except (json.JSONDecodeError, ValidationError):
            raise LLMClientError(LLMErrorType.SCHEMA_ERROR, "Ollama returned invalid structured output") from None
        except OSError:
            raise LLMClientError(LLMErrorType.SERVICE_ERROR, "Ollama process unavailable") from None
        except subprocess.SubprocessError:
            raise LLMClientError(LLMErrorType.SERVICE_ERROR, "Ollama process failed") from None


class ArkLLMClient(LLMClient):
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None, timeout_seconds: int = 60, client: object | None = None) -> None:
        self.model = model or os.environ.get("ARK_MODEL", DEFAULT_ARK_MODEL)
        self.base_url = base_url or os.environ.get("ARK_BASE_URL", DEFAULT_ARK_BASE_URL)
        self.timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.environ.get("ARK_API_KEY")
            if not resolved_key:
                raise LLMClientError(LLMErrorType.AUTH_ERROR, "Ark API key is not configured")
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=resolved_key, max_retries=0)

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        system_prompt, user_prompt = self._split_prompt(prompt)
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                timeout=self.timeout_seconds,
            )
            content = response.choices[0].message.content or ""
            return response_model.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError):
            raise LLMClientError(LLMErrorType.SCHEMA_ERROR, "Ark returned invalid structured output") from None
        except TimeoutError:
            raise LLMClientError(LLMErrorType.TIMEOUT, "Ark request timed out") from None
        except Exception as error:
            name = type(error).__name__.casefold()
            if "auth" in name or "permission" in name:
                error_type = LLMErrorType.AUTH_ERROR
            elif "badrequest" in name or "invalid" in name:
                error_type = LLMErrorType.INVALID_REQUEST
            elif "timeout" in name:
                error_type = LLMErrorType.TIMEOUT
            elif "connection" in name or "rate" in name:
                error_type = LLMErrorType.TRANSIENT_ERROR
            else:
                error_type = LLMErrorType.SERVICE_ERROR
            raise LLMClientError(error_type, f"Ark request failed: {type(error).__name__}") from None

    @staticmethod
    def _split_prompt(prompt: str) -> tuple[str, str]:
        marker = "\n<<<INPUT_START>>>\n"
        if marker in prompt:
            system_prompt, rest = prompt.split(marker, 1)
            return system_prompt, marker.lstrip("\n") + rest
        return "You are an assistant that outputs a single JSON object.", prompt


def build_llm_client_from_env() -> LLMClient:
    provider = os.environ.get("SQL_HEAL_LLM_PROVIDER", "").strip().lower()
    if provider == "ollama":
        return OllamaLLMClient()
    if provider == "ark":
        return ArkLLMClient()
    raise LLMClientError(LLMErrorType.INVALID_REQUEST, "SQL_HEAL_LLM_PROVIDER must be explicitly set to 'ark' or 'ollama'")
