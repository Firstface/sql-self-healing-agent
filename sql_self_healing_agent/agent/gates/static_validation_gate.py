from sql_self_healing_agent.agent.gates.builders import RepairPlanBuilder, SQLDiffBuilder
from sql_self_healing_agent.agent.gates.gate_models import GateRequest, StaticGateOutcome
from sql_self_healing_agent.agent.gates.gate_utils import result
from sql_self_healing_agent.repair.validator import Validator


class StaticValidationGate:
    def __init__(self, plan_builder: RepairPlanBuilder | None = None, diff_builder: SQLDiffBuilder | None = None, validator: Validator | None = None) -> None:
        self.plan_builder = plan_builder or RepairPlanBuilder()
        self.diff_builder = diff_builder or SQLDiffBuilder()
        self.validator = validator or Validator(allow_medium_risk=False)

    def run(self, request: GateRequest) -> StaticGateOutcome:
        try:
            plan = self.plan_builder.build(request.original_sql, request.diagnosis, request.metadata_snapshot, request.memory_retrieval, request.existing_plan)
            diff = self.diff_builder.build(request.original_sql, request.candidate_sql, plan)
        except Exception:
            return StaticGateOutcome(result=result("StaticValidationGate", request.candidate_sql, "HUMAN_REQUIRED", "HIGH", code="PLAN_OR_DIFF_UNAVAILABLE", message="无法为候选构造合法 RepairPlan 或 SQLDiffSummary。", failed=["PLAN_AND_DIFF_AVAILABLE"]))
        validation = self.validator.validate(request.original_sql, request.candidate_sql, plan, diff)
        checked = ["REPAIR_PLAN_BOUNDARY", "SQL_DIFF_BOUNDARY", "VALIDATOR_RED_LINES"]
        if validation.risk_level.value == "BLOCKED":
            decision = "REJECT"
        elif validation.risk_level.value in {"HIGH", "MEDIUM"}:
            decision = "HUMAN_REQUIRED"
        elif not validation.allow_return_sql or not validation.passed:
            decision = "REJECT"
        else:
            decision = "PASS"
        gate_result = result(
            "StaticValidationGate",
            request.candidate_sql,
            decision,
            validation.risk_level.value,
            code=validation.issues[0].code if validation.issues else ("RISK_REQUIRES_REVIEW" if decision == "HUMAN_REQUIRED" else None),
            message=validation.issues[0].message if validation.issues else (validation.reason or "候选风险需要人工确认" if decision == "HUMAN_REQUIRED" else None),
            failed=[item.code for item in validation.issues],
            checked=checked,
            evidence_refs=[request.candidate_artifact_ref] if request.candidate_artifact_ref else [],
        )
        return StaticGateOutcome(result=gate_result, repair_plan=plan, sql_diff_summary=diff, validation_result=validation)
