import unittest

from pydantic import ValidationError

from sql_self_healing_agent.core.models import AgentExternalResult


class ExternalResultContractTest(unittest.TestCase):
    def test_sql_ready_requires_sql(self) -> None:
        with self.assertRaises(ValidationError):
            AgentExternalResult(status="SQL_READY")

    def test_human_required_requires_message(self) -> None:
        with self.assertRaises(ValidationError):
            AgentExternalResult(status="HUMAN_REQUIRED")

    def test_internal_fields_are_forbidden(self) -> None:
        with self.assertRaises(ValidationError):
            AgentExternalResult(status="SUCCESS_ACK", session_id="secret")
