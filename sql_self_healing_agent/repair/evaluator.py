from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.agent.llm import LLMAdapter
from sql_self_healing_agent.llm.prompt_templates import (
    POST_REFLECTION_SYSTEM,
    PRE_REFLECTION_SYSTEM,
    structured_prompt,
)
from sql_self_healing_agent.repair.error_oscillation_detector import ErrorOscillationDetector
from sql_self_healing_agent.repair.reflection import (
    PostReflectionInput,
    PostReflectionResult,
    PostReflectionStatus,
    PreReflectionDecision,
    PreReflectionInput,
    PreReflectionResult,
)


class RepairEvaluator:
    def __init__(self, client: LLMClient | None = None, adapter: LLMAdapter | None = None) -> None:
        if client is not None and adapter is None:
            raise ValueError("RepairEvaluator requires an LLMAdapter when client is configured")
        self.client = client
        self.adapter = adapter

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
                prompt = structured_prompt(PRE_REFLECTION_SYSTEM, reflection_input, PreReflectionResult)
                result = self.adapter.generate_structured(
                    prompt,
                    PreReflectionResult,
                    purpose="pre_reflection",
                    input_summary="candidate semantic reflection",
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

    def post_reflect(
        self, reflection_input: PostReflectionInput
    ) -> PostReflectionResult:
        if ErrorOscillationDetector().detect(reflection_input.diagnosis_history):
            return PostReflectionResult(
                status=PostReflectionStatus.OSCILLATING,
                previous_error_resolved=False,
                new_error_introduced=True,
                recommendation_for_next_plan="停止自动修复并转人工，避免错误振荡。",
                reasons=["最近诊断 fingerprint 构成 A/B/A 或 A/B/A/B 振荡。"],
                confidence=1.0,
            )
        if self.client is not None:
            try:
                prompt = structured_prompt(POST_REFLECTION_SYSTEM, reflection_input, PostReflectionResult)
                return self.adapter.generate_structured(
                    prompt,
                    PostReflectionResult,
                    purpose="post_reflection",
                    input_summary="previous and current attempt reflection",
                )
            except LLMClientError:
                pass
        previous = reflection_input.previous_diagnosis
        current = reflection_input.current_diagnosis
        if previous.error_fingerprint == current.error_fingerprint:
            return PostReflectionResult(
                status=PostReflectionStatus.FAILED_UNCHANGED,
                previous_error_resolved=False,
                new_error_introduced=False,
                recommendation_for_next_plan="不要重复上一轮修复动作。",
                reasons=["当前错误 fingerprint 与上一轮相同。"],
                confidence=0.9,
            )
        if previous.diagnosed_error_type != current.diagnosed_error_type:
            return PostReflectionResult(
                status=PostReflectionStatus.FAILED_BUT_PROGRESSING,
                previous_error_resolved=True,
                new_error_introduced=True,
                recommendation_for_next_plan="保留上一轮已生效修改，仅处理当前新错误。",
                reasons=["错误类型已变化，上一层错误可能已解决。"],
                confidence=0.85,
            )
        return PostReflectionResult(
            status=PostReflectionStatus.FAILED_UNRELATED,
            previous_error_resolved=False,
            new_error_introduced=True,
            recommendation_for_next_plan="按当前日志和元数据重新评估，不沿用未验证结论。",
            reasons=["错误 fingerprint 变化，但错误类型未形成明确推进关系。"],
            confidence=0.6,
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
