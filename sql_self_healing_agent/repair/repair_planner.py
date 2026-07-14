import uuid

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.repair.repair_models import RepairAction, RepairActionType, RepairPlan, RepairPlannerInput


CONSTRAINTS = [
    "不修改 FROM 表，除非 action 明确要求", "不删除 WHERE 条件", "不删除 JOIN 条件",
    "不改变 GROUP BY 粒度", "不改变窗口函数分区和排序", "不修改 INSERT 目标表",
    "不修改静态分区", "只修改与当前错误相关的 fragment",
]


class RepairPlanner:
    def __init__(self, metadata_provider: MockMetadataProvider) -> None:
        self.metadata_provider = metadata_provider

    def plan(self, planner_input: RepairPlannerInput) -> RepairPlan:
        diagnosis = planner_input.diagnosis
        if not diagnosis.is_repairable:
            return self._manual(diagnosis.manual_repair_reason or "当前错误不可安全自动修复。")
        if diagnosis.diagnosed_error_type is not DiagnosedErrorType.COLUMN_NOT_FOUND:
            return self._manual("M2 当前没有该错误类型的安全修复动作。")
        if not diagnosis.primary_entity or not planner_input.metadata_snapshot:
            return self._manual("无法确认缺失字段或当前表元数据。")
        candidates = self.metadata_provider.find_column_candidates(diagnosis.primary_entity, planner_input.metadata_snapshot.tables)
        if not candidates:
            return self._manual("元数据中没有可靠字段候选。")
        best = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else -1.0
        if best.score < 0.4 or best.score - second_score < 0.05:
            return self._manual("字段候选分数过低或候选不唯一。")
        return RepairPlan(plan_id=f"plan_{uuid.uuid4().hex}", repairable=True, actions=[RepairAction(action_type=RepairActionType.REPLACE_COLUMN, target_fragment=diagnosis.primary_entity, replacement_fragment=best.candidate_name, reason="元数据确认存在最可靠候选字段", evidence=diagnosis.primary_evidence, risk_level="LOW")], constraints=CONSTRAINTS, confidence=min(0.95, best.score))

    @staticmethod
    def _manual(message: str) -> RepairPlan:
        return RepairPlan(plan_id=f"plan_{uuid.uuid4().hex}", repairable=False, actions=[], constraints=CONSTRAINTS, manual_repair_recommendation=message, confidence=0.0)
