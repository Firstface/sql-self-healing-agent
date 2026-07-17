from sql_self_healing_agent.core.models import AgentExternalResult


class ExternalResultFactory:
    @staticmethod
    def sql_ready(sql: str) -> AgentExternalResult:
        return AgentExternalResult(status="SQL_READY", sql=sql)

    @staticmethod
    def no_sql(message: str | None = None) -> AgentExternalResult:
        return AgentExternalResult(status="NO_SQL", message=message)

    @staticmethod
    def human_required(message: str) -> AgentExternalResult:
        return AgentExternalResult(status="HUMAN_REQUIRED", message=message)

    @staticmethod
    def success_ack(message: str | None = None) -> AgentExternalResult:
        return AgentExternalResult(status="SUCCESS_ACK", message=message)
