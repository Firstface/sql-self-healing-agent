from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_models import MemoryRetrievalResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import RepairPlan, RepairPlannerInput, SQLDiffSummary, SQLGenerationResult
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.repair.sql_generator import build_diff
from sql_self_healing_agent.logs.log_models import LogDigest


class RepairPlanBuilder:
    def __init__(self, planner: RepairPlanner | None = None) -> None:
        self.planner = planner

    def build(
        self,
        failed_sql: str,
        diagnosis: DiagnosisResult,
        metadata_snapshot: MetadataSnapshot | None,
        memory_retrieval: MemoryRetrievalResult | None,
        existing_plan: RepairPlan | None = None,
    ) -> RepairPlan:
        if existing_plan is not None:
            return RepairPlan.model_validate(existing_plan.model_dump(mode="json"))
        if self.planner is None:
            raise ValueError("RepairPlanner is required when no existing plan is supplied")
        digest = LogDigest(log_readable=False, root_cause_summary=diagnosis.root_cause_summary)
        plan = self.planner.plan(
            RepairPlannerInput(
                failed_sql=failed_sql,
                diagnosis=diagnosis,
                log_digest=digest,
                metadata_snapshot=metadata_snapshot,
                memory_retrieval=memory_retrieval,
            )
        )
        if not plan.repairable or not plan.actions:
            raise ValueError("cannot construct a repairable RepairPlan")
        return plan


class SQLDiffBuilder:
    def build(self, failed_sql: str, candidate_sql: str, plan: RepairPlan) -> SQLDiffSummary:
        if not candidate_sql.strip():
            raise ValueError("candidate SQL is empty")
        return build_diff(failed_sql, SQLGenerationResult(generated=True, sql_candidate=candidate_sql), plan)
