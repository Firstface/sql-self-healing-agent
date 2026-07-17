import tempfile
import unittest
from pathlib import Path

from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService
from tests.fakes import FakeLLMClient

ROOT = Path(__file__).parents[1]


class PersistenceSecurityScanTest(unittest.TestCase):
    def test_persisted_files_contain_no_secrets_or_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "task.log"
            log.write_text("Authorization: Bearer secret-value\nSemanticException: Invalid column reference pay_amt\n")
            service = RepairAgentService(root / "sessions", llm_client=FakeLLMClient(), metadata_path=ROOT / "mocks/metadata/tables.json", memory_dir=root / ".memory")
            event = UpstreamTaskEvent(id="secure", status="FAILED", sql="SELECT user_id, pay_amt FROM dwd_order_detail WHERE date = ", error_message="failed", log_path=str(log))
            service.handle_upstream_event(event)
            persisted = "\n".join(path.read_text(errors="replace") for base in (root / "sessions", root / ".memory") if base.exists() for path in base.rglob("*") if path.is_file())
            for forbidden in ("secret-value", "Authorization:", "Bearer ", "JSON Schema:", "$defs", "ARK_API_KEY"):
                self.assertNotIn(forbidden, persisted)
