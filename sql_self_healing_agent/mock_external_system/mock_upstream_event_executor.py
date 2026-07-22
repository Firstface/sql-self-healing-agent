from sql_self_healing_agent.mock_external_system.mock_upstream_models import (
    MockExecutionResult,
    MockScenario,
)


class MockUpstreamEventExecutor:
    def run(
        self, scenario: MockScenario, round_no: int, sql: str
    ) -> MockExecutionResult:
        round_def = next(
            (item for item in scenario.rounds if item.round_no == round_no), None
        )
        if round_def is None:
            return MockExecutionResult(status="FAILED", error_message="Mock round missing")
        condition = round_def.success_condition or {}
        must_contain = condition.get("must_contain")
        if must_contain and must_contain.casefold() not in sql.casefold():
            return MockExecutionResult(
                status="FAILED",
                error_message=round_def.error_message,
                log_path=round_def.log_path,
            )
        must_not_contain = condition.get("must_not_contain")
        if must_not_contain and must_not_contain.casefold() in sql.casefold():
            return MockExecutionResult(
                status="FAILED",
                error_message=round_def.error_message,
                log_path=round_def.log_path,
            )
        if condition.get("success") is True:
            return MockExecutionResult(status="SUCCESS")
        return MockExecutionResult(
            status="FAILED",
            error_message=round_def.error_message,
            log_path=round_def.log_path,
        )
