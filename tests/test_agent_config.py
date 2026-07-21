import unittest

from pydantic import ValidationError

from sql_self_healing_agent.agent.config import AgentConfig
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits
from sql_self_healing_agent.agent.models.subagent_models import SubAgentLimits


class AgentConfigTest(unittest.TestCase):
    def test_llm_main_agent_is_enabled_with_fail_safe_by_default(self) -> None:
        self.assertTrue(AgentConfig().llm_main_agent_enabled)

    def test_defaults_pass_cross_validation(self) -> None:
        config = AgentConfig()
        self.assertEqual(config.run_limits.max_gate_repair_rounds, 1)
        self.assertLessEqual(config.sub_agent_limits.max_tool_calls, config.run_limits.max_tool_calls)

    def test_invalid_gate_repair_limit_fails_closed(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfig(run_limits=AgentRunLimits(max_gate_repair_rounds=0))

    def test_subagent_cannot_exceed_parent(self) -> None:
        with self.assertRaises(ValidationError):
            AgentConfig(
                run_limits=AgentRunLimits(max_tool_calls=2),
                sub_agent_limits=SubAgentLimits(max_tool_calls=3),
            )
