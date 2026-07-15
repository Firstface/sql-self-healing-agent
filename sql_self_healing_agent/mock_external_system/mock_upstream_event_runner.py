from sql_self_healing_agent.mock_external_system.mock_upstream_event_executor import (
    MockUpstreamEventExecutor,
)
from sql_self_healing_agent.mock_external_system.mock_upstream_models import (
    MockFinalResult,
    MockScenario,
)
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService


class MockUpstreamEventRunner:
    def __init__(
        self,
        agent_service: RepairAgentService,
        executor: MockUpstreamEventExecutor,
        max_retry_count: int = 3,
    ) -> None:
        self.agent_service = agent_service
        self.executor = executor
        self.max_retry_count = max_retry_count

    def run(self, scenario: MockScenario) -> MockFinalResult:
        current_sql = scenario.initial_sql
        round_limit = min(
            self.max_retry_count, scenario.max_retry_count, len(scenario.rounds)
        )
        for round_index in range(round_limit):
            round_def = scenario.rounds[round_index]
            failed_event = scenario.to_agent_failed_event(current_sql, round_def)
            agent_result = self.agent_service.handle_upstream_event(failed_event)
            attempt_count = round_index + 1
            if agent_result.status == "HUMAN_REQUIRED":
                return MockFinalResult.from_agent_result(
                    scenario, "MOCK_HUMAN_REQUIRED", attempt_count, agent_result
                )
            if agent_result.status == "NO_SQL":
                return MockFinalResult.from_agent_result(
                    scenario, "MOCK_NO_SQL", attempt_count, agent_result
                )
            if agent_result.status != "SQL_READY" or not agent_result.sql:
                return MockFinalResult.from_agent_result(
                    scenario, "MOCK_UNEXPECTED", attempt_count, agent_result
                )
            execution_result = self.executor.run(
                scenario, round_def.round_no, agent_result.sql
            )
            if execution_result.status == "SUCCESS":
                ack = self.agent_service.handle_upstream_event(
                    scenario.to_agent_success_event(agent_result.sql)
                )
                if ack.status != "SUCCESS_ACK":
                    return MockFinalResult.from_agent_result(
                        scenario, "MOCK_UNEXPECTED", attempt_count, ack
                    )
                return MockFinalResult(
                    scenario_id=scenario.scenario_id,
                    task_id=scenario.task_id,
                    status="MOCK_SUCCESS",
                    attempt_count=attempt_count,
                )
            current_sql = agent_result.sql
        return MockFinalResult(
            scenario_id=scenario.scenario_id,
            task_id=scenario.task_id,
            status="MOCK_RETRY_EXHAUSTED",
            attempt_count=round_limit,
            message="Mock upstream retry count exhausted.",
        )
