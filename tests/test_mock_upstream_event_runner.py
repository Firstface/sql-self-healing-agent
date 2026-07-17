import json
import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.mock_external_system.mock_upstream_event_executor import MockUpstreamEventExecutor
from sql_self_healing_agent.mock_external_system.mock_upstream_event_runner import MockUpstreamEventRunner
from sql_self_healing_agent.mock_external_system.mock_upstream_models import MockScenario
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService


PROJECT_ROOT = Path(__file__).parents[1]


class MockUpstreamEventRunnerTest(unittest.TestCase):
    def _scenario(self, name: str) -> MockScenario:
        return MockScenario.model_validate(
            json.loads((PROJECT_ROOT / "mocks/scenarios" / name).read_text())
        )

    def test_mock_upstream_two_step_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scenario = self._scenario("two_step_column_then_type.json")
            service = RepairAgentService(
                root / "sessions",
                metadata_path=PROJECT_ROOT / "mocks/metadata/tables.json",
                allow_medium_risk=scenario.allow_medium_risk,
                memory_dir=root / ".memory",
            )
            result = MockUpstreamEventRunner(
                service, MockUpstreamEventExecutor()
            ).run(scenario)
            self.assertEqual(result.status, "MOCK_SUCCESS")
            self.assertEqual(result.attempt_count, 2)
            session_dir = root / "sessions" / f"sess_{scenario.task_id}"
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(session["status"], "UPSTREAM_CONFIRMED_SUCCESS")
            self.assertEqual(session["confirmed_attempt_id"], "attempt_002")
            first_attempt = json.loads((session_dir / "attempts/attempt_001.json").read_text())
            second_attempt = json.loads((session_dir / "attempts/attempt_002.json").read_text())
            self.assertEqual(first_attempt["status"], "UPSTREAM_FAILED")
            self.assertEqual(second_attempt["status"], "UPSTREAM_CONFIRMED_SUCCESS")
            self.assertEqual(second_attempt["previous_attempt_id"], "attempt_001")
            post = json.loads((session_dir / "artifacts/attempt_002/post_reflection_result.json").read_text())
            self.assertEqual(post["status"], "FAILED_BUT_PROGRESSING")
            experiences = list((root / ".memory/experiences").glob("*.md"))
            self.assertEqual(len(experiences), 1)

    def test_mock_upstream_retry_exhausted_writes_no_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            scenario = self._scenario("retry_exhausted_manual_required.json")
            service = RepairAgentService(
                root / "sessions",
                metadata_path=PROJECT_ROOT / "mocks/metadata/tables.json",
                allow_medium_risk=scenario.allow_medium_risk,
                memory_dir=root / ".memory",
            )
            result = MockUpstreamEventRunner(
                service, MockUpstreamEventExecutor()
            ).run(scenario)
            self.assertEqual(result.status, "MOCK_RETRY_EXHAUSTED")
            self.assertEqual(result.attempt_count, 3)
            self.assertFalse((root / ".memory/experiences").exists())
            session_dir = root / "sessions" / f"sess_{scenario.task_id}"
            session = json.loads((session_dir / "session.json").read_text())
            self.assertEqual(len(session["attempt_ids"]), 3)

    def test_mock_rules_are_not_in_agent_event(self) -> None:
        scenario = self._scenario("two_step_column_then_type.json")
        event = scenario.to_agent_failed_event(scenario.initial_sql, scenario.rounds[0])
        payload = event.model_dump(mode="json")
        self.assertEqual(set(payload), {"id", "status", "sql", "error_message", "log_path"})
        self.assertNotIn("success_condition", json.dumps(payload))
        self.assertNotIn("must_contain", json.dumps(payload))
