from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.llm.prompt_templates import (
    PRE_REFLECTION_SYSTEM,
    structured_prompt,
)
from sql_self_healing_agent.repair.reflection import (
    PreReflectionDecision,
    PreReflectionInput,
    PreReflectionResult,
)


class RepairEvaluator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client

    def pre_reflect(
        self, reflection_input: PreReflectionInput
    ) -> PreReflectionResult:
        validation = reflection_input.validation_result
        if not validation.allow_return_sql:
            return PreReflectionResult(
                decision=PreReflectionDecision.BLOCK,
                confidence=1.0,
                follows_repair_plan=False,
                minimal_change=False,
                semantic_risk_level=validation.risk_level,
                reasons=[validation.reason or "Validation blocked"],
                violated_constraints=[item.code for item in validation.issues],
            )
        if self.client is not None:
            try:
                result = self.client.generate_structured(
                    structured_prompt(
                        PRE_REFLECTION_SYSTEM,
                        reflection_input,
                        PreReflectionResult,
                    ),
                    PreReflectionResult,
                )
            except LLMClientError:
                return PreReflectionResult(
                    decision=PreReflectionDecision.BLOCK,
                    confidence=0.0,
                    follows_repair_plan=False,
                    minimal_change=False,
                    semantic_risk_level=validation.risk_level,
                    reasons=["LLM 未能返回合法的 PreReflection 结果。"],
                )
            return self._enforce_result_consistency(result)

        follows = (
            reflection_input.sql_diff_summary.changed_fragment_count
            == len(reflection_input.repair_plan.actions)
        )
        minimal = follows and not any(
            (
                reflection_input.sql_diff_summary.removed_where,
                reflection_input.sql_diff_summary.removed_join_condition,
                reflection_input.sql_diff_summary.changed_group_by,
                reflection_input.sql_diff_summary.changed_insert_target,
                reflection_input.sql_diff_summary.changed_static_partition,
            )
        )
        decision = (
            PreReflectionDecision.RETURN_SQL
            if follows and minimal
            else PreReflectionDecision.BLOCK
        )
        return PreReflectionResult(
            decision=decision,
            confidence=0.95,
            follows_repair_plan=follows,
            minimal_change=minimal,
            semantic_risk_level=validation.risk_level,
            reasons=["候选 SQL 仅执行 RepairPlan 中的最小修改。"]
            if decision is PreReflectionDecision.RETURN_SQL
            else ["候选 SQL 未忠实执行 RepairPlan。"],
        )

    @staticmethod
    def _enforce_result_consistency(
        result: PreReflectionResult,
    ) -> PreReflectionResult:
        if result.decision is PreReflectionDecision.RETURN_SQL:
            consistent = (
                result.follows_repair_plan
                and result.minimal_change
                and result.semantic_risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}
                and not result.violated_constraints
            )
            if not consistent:
                return result.model_copy(
                    update={
                        "decision": PreReflectionDecision.BLOCK,
                        "reasons": [
                            *result.reasons,
                            "PreReflection RETURN_SQL 字段自相矛盾，已安全阻断。",
                        ],
                    }
                )
        if (
            result.decision is PreReflectionDecision.REGENERATE
            and not result.regeneration_instruction
        ):
            return result.model_copy(
                update={
                    "decision": PreReflectionDecision.BLOCK,
                    "reasons": [
                        *result.reasons,
                        "REGENERATE 缺少 regeneration_instruction。",
                    ],
                }
            )
        return result
