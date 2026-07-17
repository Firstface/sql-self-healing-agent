import json
import os
import tempfile
import unittest
from tests.llm_test_adapter import build_test_llm_adapter
from pathlib import Path
from unittest.mock import patch

from sql_self_healing_agent.core.enums import DiagnosedErrorType, RiskLevel
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult, LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import (
    ArkLLMClient,
    LLMClientError,
    OllamaLLMClient,
    build_llm_client_from_env,
)
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from sql_self_healing_agent.repair.evaluator import RepairEvaluator
from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionInput
from sql_self_healing_agent.repair.repair_models import (
    RepairAction,
    RepairActionType,
    RepairPlan,
    SQLDiffSummary,
    SQLGeneratorInput,
    SQLGeneratorLLMOutput,
    ValidationResult,
)
from sql_self_healing_agent.repair.sql_generator import SQLGenerator


ARK_SECRET = "sk-ark-secret-should-never-leak"
METADATA_PATH = Path(__file__).parents[1] / "mocks/metadata/tables.json"

DIAGNOSIS_PAYLOAD = {
    "diagnosed_error_type": "COLUMN_NOT_FOUND",
    "diagnosed_keywords": ["column_not_found", "missing_field"],
    "primary_evidence": "Invalid column reference pay_amt",
    "root_cause_summary": "pay_amt missing on dwd_order_detail",
    "confidence": 0.95,
    "is_repairable": True,
    "manual_repair_reason": None,
}
GENERATOR_PAYLOAD = {
    "generated": True,
    "sql_candidate": "SELECT user_id, payment_amount FROM dwd_order_detail WHERE date = ",
    "cannot_generate_safely": False,
    "reason": None,
    "changed_fragments": [
        {
            "before": "pay_amt",
            "after": "payment_amount",
            "action_type": "REPLACE_COLUMN",
            "reason": "execute RepairPlan",
        }
    ],
}
REFLECTION_PAYLOAD = {
    "decision": "RETURN_SQL",
    "confidence": 0.96,
    "follows_repair_plan": True,
    "minimal_change": True,
    "semantic_risk_level": "LOW",
    "reasons": ["single metadata-verified column replacement"],
    "violated_constraints": [],
    "regeneration_instruction": None,
}


def _make_choice(content: str):
    class _Message:
        def __init__(self, value: str) -> None:
            self.content = value

    class _Choice:
        def __init__(self, value: str) -> None:
            self.message = _Message(value)

    class _Response:
        def __init__(self, value: str) -> None:
            self.choices = [_Choice(value)]

    return _Response(content)


class FakeArkSDKClient:
    """Minimal stand-in for openai.OpenAI. Records calls; returns pre-programmed payloads."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, outer: "FakeArkSDKClient") -> None:
            self.completions = FakeArkSDKClient._Completions(outer)

    class _Completions:
        def __init__(self, outer: "FakeArkSDKClient") -> None:
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            if not self._outer._responses:
                raise AssertionError("No more programmed responses")
            item = self._outer._responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item


class ArkStructuredOutputTest(unittest.TestCase):
    def test_normal_structured_output(self) -> None:
        fake = FakeArkSDKClient([_make_choice(json.dumps(DIAGNOSIS_PAYLOAD))])
        client = ArkLLMClient(model="ep-x", client=fake)
        result = client.generate_structured("SYS\n<<<INPUT_START>>>\n{}\n<<<INPUT_END>>>", LLMDiagnosisResult)
        self.assertEqual(result.diagnosed_error_type, DiagnosedErrorType.COLUMN_NOT_FOUND)
        call = fake.calls[0]
        self.assertEqual(call["model"], "ep-x")
        self.assertEqual(call["response_format"], {"type": "json_object"})
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertEqual(call["messages"][1]["role"], "user")

    def test_invalid_output_has_no_implicit_retry(self) -> None:
        fake = FakeArkSDKClient([
            _make_choice("not json at all"),
            _make_choice(json.dumps(DIAGNOSIS_PAYLOAD)),
        ])
        client = ArkLLMClient(model="ep-x", client=fake)
        with self.assertRaises(LLMClientError) as raised:
            client.generate_structured("SYS\n<<<INPUT_START>>>\n{}\n<<<INPUT_END>>>", LLMDiagnosisResult)
        self.assertEqual(raised.exception.error_type.value, "SCHEMA_ERROR")
        self.assertEqual(len(fake.calls), 1)

    def test_two_invalid_raises_llm_client_error(self) -> None:
        fake = FakeArkSDKClient([
            _make_choice("still not json"),
            _make_choice("also not json"),
        ])
        client = ArkLLMClient(model="ep-x", client=fake)
        with self.assertRaises(LLMClientError):
            client.generate_structured("SYS\n<<<INPUT_START>>>\n{}\n<<<INPUT_END>>>", LLMDiagnosisResult)

    def test_timeout_is_llm_client_error_without_leaking_key(self) -> None:
        class TimeoutError_(Exception):
            def __str__(self) -> str:  # pragma: no cover - message shape not asserted
                return f"timeout details include key {ARK_SECRET}"

        fake = FakeArkSDKClient([TimeoutError_("boom")])
        client = ArkLLMClient(model="ep-x", client=fake, api_key=ARK_SECRET)
        try:
            client.generate_structured("SYS\n<<<INPUT_START>>>\n{}\n<<<INPUT_END>>>", LLMDiagnosisResult)
            self.fail("Expected LLMClientError")
        except LLMClientError as error:
            self.assertNotIn(ARK_SECRET, str(error))

    def test_sdk_error_does_not_leak_credentials(self) -> None:
        class BoomError(Exception):
            def __str__(self) -> str:
                return f"upstream said key={ARK_SECRET} was bad"

        fake = FakeArkSDKClient([BoomError()])
        client = ArkLLMClient(model="ep-x", client=fake, api_key=ARK_SECRET)
        try:
            client.generate_structured("SYS\n<<<INPUT_START>>>\n{}\n<<<INPUT_END>>>", LLMDiagnosisResult)
            self.fail("Expected LLMClientError")
        except LLMClientError as error:
            message = str(error)
            self.assertNotIn(ARK_SECRET, message)
            self.assertIsNone(error.__cause__)


class ProviderSelectionTest(unittest.TestCase):
    def test_unknown_provider_fail_closed(self) -> None:
        with patch.dict(os.environ, {"SQL_HEAL_LLM_PROVIDER": "gemini"}, clear=False):
            with self.assertRaises(LLMClientError):
                build_llm_client_from_env()

    def test_ollama_provider_returns_ollama_client(self) -> None:
        with patch.dict(os.environ, {"SQL_HEAL_LLM_PROVIDER": "ollama"}, clear=False):
            client = build_llm_client_from_env()
            self.assertIsInstance(client, OllamaLLMClient)

    def test_ark_provider_returns_ark_client(self) -> None:
        env = {"SQL_HEAL_LLM_PROVIDER": "ark", "ARK_API_KEY": ARK_SECRET}
        with patch.dict(os.environ, env, clear=False):
            with patch("openai.OpenAI") as ctor:
                ctor.return_value = object()
                client = build_llm_client_from_env()
        self.assertIsInstance(client, ArkLLMClient)


class ArkBusinessDegradationTest(unittest.TestCase):
    def _build_diagnosis(self) -> DiagnosisResult:
        return DiagnosisResult(
            diagnosed_error_type=DiagnosedErrorType.COLUMN_NOT_FOUND,
            diagnosed_keywords=["column_not_found"],
            error_fingerprint="COLUMN_NOT_FOUND:pay_amt:hive",
            confidence=0.9,
            is_repairable=True,
        )

    def test_sql_generator_returns_cannot_generate_safely_on_invalid_ark(self) -> None:
        fake = FakeArkSDKClient([_make_choice("garbage"), _make_choice("still garbage")])
        ark = ArkLLMClient(model="ep-x", client=fake)
        plan = RepairPlan(
            plan_id="plan",
            repairable=True,
            actions=[RepairAction(
                action_type=RepairActionType.REPLACE_COLUMN,
                target_fragment="pay_amt",
                replacement_fragment="payment_amount",
                reason="test",
                risk_level="LOW",
            )],
            confidence=0.9,
        )
        result = SQLGenerator(ark, build_test_llm_adapter(ark)).generate(SQLGeneratorInput(failed_sql="SELECT pay_amt", repair_plan=plan))
        self.assertFalse(result.generated)
        self.assertTrue(result.cannot_generate_safely)

    def test_pre_reflection_returns_block_on_invalid_ark(self) -> None:
        fake = FakeArkSDKClient([_make_choice("garbage"), _make_choice("still garbage")])
        ark = ArkLLMClient(model="ep-x", client=fake)
        plan = RepairPlan(
            plan_id="plan",
            repairable=True,
            actions=[RepairAction(
                action_type=RepairActionType.REPLACE_COLUMN,
                target_fragment="a",
                replacement_fragment="b",
                reason="test",
                risk_level="LOW",
            )],
            confidence=0.9,
        )
        reflection_input = PreReflectionInput(
            failed_sql="SELECT a",
            sql_candidate="SELECT b",
            diagnosis=self._build_diagnosis(),
            repair_plan=plan,
            validation_result=ValidationResult(risk_level=RiskLevel.LOW, passed=True, allow_return_sql=True),
            sql_diff_summary=SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], parse_success=True),
        )
        result = RepairEvaluator(ark, build_test_llm_adapter(ark)).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)

    def test_ark_generator_failure_does_not_produce_system_error(self) -> None:
        fake = FakeArkSDKClient([
            _make_choice(json.dumps(DIAGNOSIS_PAYLOAD)),
            _make_choice("garbage"),
            _make_choice("still garbage"),
        ])
        ark = ArkLLMClient(model="ep-x", client=fake)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "task.log"
            log_path.write_text("SemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=ark, metadata_path=METADATA_PATH)
            event = UpstreamTaskEvent(
                id="task_ark_fail",
                status="FAILED",
                sql="SELECT pay_amt FROM dwd_order_detail",
                error_message="Invalid column reference pay_amt",
                log_path=str(log_path),
            )
            result = service.handle_upstream_event(event)
            self.assertEqual(result.status, "HUMAN_REQUIRED")
            session = json.loads((root / "sessions/sess_task_ark_fail/session.json").read_text())
            attempt = json.loads((root / "sessions/sess_task_ark_fail/attempts/attempt_001.json").read_text())
            self.assertEqual(session["status"], "HUMAN_REQUIRED")
            self.assertEqual(attempt["status"], "HUMAN_REQUIRED")

    def test_validation_blocked_is_not_overridden_by_ark(self) -> None:
        fake = FakeArkSDKClient([_make_choice(json.dumps({**REFLECTION_PAYLOAD, "decision": "RETURN_SQL"}))])
        ark = ArkLLMClient(model="ep-x", client=fake)
        plan = RepairPlan(
            plan_id="plan",
            repairable=True,
            actions=[RepairAction(
                action_type=RepairActionType.REPLACE_COLUMN,
                target_fragment="a",
                replacement_fragment="b",
                reason="test",
                risk_level="LOW",
            )],
            confidence=0.9,
        )
        reflection_input = PreReflectionInput(
            failed_sql="SELECT a",
            sql_candidate="SELECT b",
            diagnosis=self._build_diagnosis(),
            repair_plan=plan,
            validation_result=ValidationResult(
                risk_level=RiskLevel.BLOCKED,
                passed=False,
                allow_return_sql=False,
                reason="blocked",
            ),
            sql_diff_summary=SQLDiffSummary(changed_fragment_count=1, changed_fragments=[], parse_success=True),
        )
        result = RepairEvaluator(ark, build_test_llm_adapter(ark)).pre_reflect(reflection_input)
        self.assertEqual(result.decision, PreReflectionDecision.BLOCK)
        self.assertEqual(len(fake.calls), 0)
