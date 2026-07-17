import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.artifacts.artifact_store import ArtifactAccessError, ArtifactIntegrityError, ArtifactStore


class ArtifactStoreContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ArtifactStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sanitizes_and_returns_owned_ref(self) -> None:
        ref = self.store.save_text_ref("sess_1", "attempt_1", "log.txt", "Authorization: Bearer abcdef123456\napi_key=secret123", "RAW_LOG")
        self.assertTrue(ref.sanitized)
        self.assertEqual(ref.session_id, "sess_1")
        self.assertEqual(ref.attempt_id, "attempt_1")
        content = self.store.load(ref, session_id="sess_1", attempt_id="attempt_1")
        self.assertNotIn("abcdef123456", content)
        self.assertNotIn("secret123", content)
        self.assertIn("<REDACTED>", content)

    def test_rejects_cross_session_and_unsanitized_ref(self) -> None:
        ref = self.store.save_text_ref("sess_1", "attempt_1", "x.txt", "safe", "LOG_DIGEST")
        with self.assertRaises(ArtifactAccessError):
            self.store.load(ref, session_id="sess_2")
        unsafe = ref.model_copy(update={"sanitized": False})
        with self.assertRaises(ArtifactAccessError):
            self.store.load(unsafe)

    def test_detects_tampering(self) -> None:
        ref = self.store.save_text_ref("sess_1", "attempt_1", "x.txt", "safe", "LOG_DIGEST")
        path = Path(self.tmp.name) / "sess_1" / "attempts" / "attempt_1" / "artifacts" / "x.txt"
        path.write_text("tampered", encoding="utf-8")
        with self.assertRaises(ArtifactIntegrityError):
            self.store.load(ref)

    def test_ref_schema_is_strict(self) -> None:
        with self.assertRaises(Exception):
            ArtifactRef.model_validate({"artifact_id": "a", "unexpected": True})

class ReadArtifactToolTest(unittest.TestCase):
    def test_tool_enforces_ref_ownership_and_sanitization(self) -> None:
        from sql_self_healing_agent.agent.models.context import AgentContext
        from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
        from sql_self_healing_agent.agent.tools.read_artifact_tool import ReadArtifactInput, ReadArtifactTool

        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            ref = store.save_text_ref("sess_1", "attempt_1", "log.txt", "safe", "RAW_LOG")
            context = AgentContext(session_id="sess_1", attempt_id="attempt_1", event_key="e", original_sql="SELECT 1", execution_plan=build_initial_execution_plan())
            tool = ReadArtifactTool(store)
            self.assertEqual(tool.run(context, ReadArtifactInput(artifact_ref=ref)).status, "SUCCEEDED")
            foreign = ref.model_copy(update={"session_id": "sess_2"})
            self.assertEqual(tool.run(context, ReadArtifactInput(artifact_ref=foreign)).status, "FORBIDDEN")
            unsafe = ref.model_copy(update={"sanitized": False})
            self.assertEqual(tool.run(context, ReadArtifactInput(artifact_ref=unsafe)).status, "FORBIDDEN")

class ReadArtifactToolContextTest(unittest.TestCase):
    def test_tool_enforces_ref_ownership_and_integrity(self) -> None:
        from datetime import datetime, timezone
        from sql_self_healing_agent.agent.models.context import AgentContext
        from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
        from sql_self_healing_agent.agent.tools.read_artifact_tool import ReadArtifactInput, ReadArtifactTool

        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            ref = store.save_text_ref("sess_1", "attempt_1", "x.txt", "safe content", "LOG_DIGEST")
            context = AgentContext(session_id="sess_1", attempt_id="attempt_1", event_key="event_1", original_sql="SELECT 1", execution_plan=build_initial_execution_plan())
            result = ReadArtifactTool(store).run(context, ReadArtifactInput(artifact_ref=ref))
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertEqual(result.content_summary, "safe content")
            forbidden = ReadArtifactTool(store).run(context, ReadArtifactInput(artifact_ref=ref.model_copy(update={"session_id": "sess_2"})))
            self.assertEqual(forbidden.status, "FORBIDDEN")
