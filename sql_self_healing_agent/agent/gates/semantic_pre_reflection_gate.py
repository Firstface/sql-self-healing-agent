from sql_self_healing_agent.agent.gates.gate_models import GateRequest, GateResult, StaticGateOutcome
from sql_self_healing_agent.agent.gates.gate_utils import result
from sql_self_healing_agent.repair.evaluator import RepairEvaluator
from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionInput


class SemanticPreReflectionGate:
    def __init__(self, evaluator: RepairEvaluator | None = None) -> None:
        self.evaluator = evaluator or RepairEvaluator()

    def run(self, request: GateRequest, static: StaticGateOutcome) -> GateResult:
        if static.repair_plan is None or static.sql_diff_summary is None or static.validation_result is None:
            return result("SemanticPreReflectionGate", request.candidate_sql, "HUMAN_REQUIRED", "HIGH", code="STATIC_EVIDENCE_MISSING", message="静态校验证据不完整。", failed=["STATIC_EVIDENCE_AVAILABLE"])
        try:
            reflection = self.evaluator.pre_reflect(PreReflectionInput(
                failed_sql=request.original_sql,
                sql_candidate=request.candidate_sql,
                diagnosis=request.diagnosis,
                repair_plan=static.repair_plan,
                validation_result=static.validation_result,
                sql_diff_summary=static.sql_diff_summary,
                metadata_snapshot=request.metadata_snapshot,
                memory_retrieval=request.memory_retrieval,
            ))
        except Exception:
            return result("SemanticPreReflectionGate", request.candidate_sql, "HUMAN_REQUIRED", "HIGH", code="SEMANTIC_GATE_FAILED", message="语义反思失败，候选不可交付。", failed=["SEMANTIC_REFLECTION_SUCCEEDED"])
        risk = reflection.semantic_risk_level.value
        if reflection.decision is PreReflectionDecision.RETURN_SQL and risk == "LOW":
            decision = "PASS"
        elif reflection.decision is PreReflectionDecision.RETURN_SQL and risk == "MEDIUM":
            decision = "PASS_WITH_WARNING" if request.allow_medium_risk else "HUMAN_REQUIRED"
        elif reflection.decision is PreReflectionDecision.REGENERATE:
            decision = "REJECT"
        elif reflection.decision is PreReflectionDecision.MANUAL_REQUIRED or risk in {"HIGH", "MEDIUM"}:
            decision = "HUMAN_REQUIRED"
        else:
            decision = "REJECT"
        return result(
            "SemanticPreReflectionGate", request.candidate_sql, decision, risk,
            code=None if decision == "PASS" else "SEMANTIC_REGENERATE" if reflection.decision is PreReflectionDecision.REGENERATE else "SEMANTIC_REFLECTION_BLOCKED",
            message=None if decision == "PASS" else reflection.regeneration_instruction if reflection.decision is PreReflectionDecision.REGENERATE else "; ".join(reflection.reasons) or "PreReflection 未放行候选。",
            failed=reflection.violated_constraints,
            checked=["FOLLOWS_REPAIR_PLAN", "MINIMAL_CHANGE", "SEMANTIC_RISK"],
        )
