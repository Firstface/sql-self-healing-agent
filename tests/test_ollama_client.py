import json
import subprocess
import unittest
from unittest.mock import patch

from sql_self_healing_agent.diagnostics.diagnosis_models import LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import OllamaLLMClient


class OllamaClientTest(unittest.TestCase):
    def test_uses_cli_and_validates_schema(self) -> None:
        payload = {"diagnosed_error_type": "COLUMN_NOT_FOUND", "diagnosed_keywords": ["column_not_found"], "primary_evidence": "bad column", "root_cause_summary": "bad column", "confidence": 0.9, "is_repairable": True, "manual_repair_reason": None}
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")) as run:
            result = OllamaLLMClient(model="local-model").generate_structured("prompt", LLMDiagnosisResult)
            self.assertEqual(result.diagnosed_error_type.value, "COLUMN_NOT_FOUND")
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["ollama", "run", "local-model"])
            self.assertEqual(command[3], "--format")
            self.assertEqual(json.loads(command[4])["type"], "object")

    def test_error_does_not_leak_prompt_or_schema(self) -> None:
        secret_prompt = "TOP_SECRET_PROMPT_VALUE"
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["ollama", secret_prompt])):
            with self.assertRaises(Exception) as raised:
                OllamaLLMClient(model="local-model").generate_structured(secret_prompt, LLMDiagnosisResult)
        message = str(raised.exception)
        self.assertNotIn(secret_prompt, message)
        self.assertNotIn("properties", message)
        self.assertIn("CalledProcessError", message)
