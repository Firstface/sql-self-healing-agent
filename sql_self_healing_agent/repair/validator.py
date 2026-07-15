import re

from sql_self_healing_agent.core.enums import RiskLevel
from sql_self_healing_agent.repair.repair_models import (
    RepairActionType,
    RepairPlan,
    SQLDiffSummary,
    ValidationIssue,
    ValidationResult,
)
from sql_self_healing_agent.repair.sql_generator import candidate_matches_plan


_DANGEROUS_PATTERN = re.compile(r"(?i)\b(DROP|DELETE|TRUNCATE|ALTER)\b")
_WRITE_PATTERN = re.compile(
    r"(?i)\b(INSERT\s+(?:INTO|OVERWRITE)|UPDATE|MERGE|CREATE|REPLACE)\b"
)
_INSERT_PATTERN = re.compile(r"(?i)\bINSERT\s+(?:INTO|OVERWRITE)\b")


class Validator:
    def __init__(self, allow_medium_risk: bool = False) -> None:
        self.allow_medium_risk = allow_medium_risk

    def validate(
        self,
        failed_sql: str,
        sql_candidate: str,
        plan: RepairPlan,
        diff: SQLDiffSummary,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if _DANGEROUS_PATTERN.search(sql_candidate):
            issues.append(
                ValidationIssue(
                    code="DANGEROUS_SQL",
                    message="候选 SQL 包含禁止语句",
                    risk_level=RiskLevel.BLOCKED,
                )
            )

        failed_write = bool(_WRITE_PATTERN.search(failed_sql))
        candidate_write = bool(_WRITE_PATTERN.search(sql_candidate))
        candidate_insert = bool(_INSERT_PATTERN.search(sql_candidate))
        if candidate_write and not failed_write:
            issues.append(
                ValidationIssue(
                    code="WRITE_INTRODUCED",
                    message="非写入 SQL 被改为写入 SQL",
                    risk_level=RiskLevel.BLOCKED,
                )
            )
        if candidate_write and not candidate_insert:
            issues.append(
                ValidationIssue(
                    code="UNSUPPORTED_WRITE_SQL",
                    message="MVP 无法安全验证该写入型 SQL",
                    risk_level=RiskLevel.BLOCKED,
                )
            )
        if candidate_insert and (
            not diff.parse_success
            or diff.changed_insert_target
            or diff.changed_static_partition
        ):
            issues.append(
                ValidationIssue(
                    code="INSERT_TARGET_UNVERIFIED",
                    message="无法确认写入目标和静态分区保持不变",
                    risk_level=RiskLevel.BLOCKED,
                )
            )

        for enabled, code, message in (
            (diff.removed_where, "WHERE_WEAKENED", "候选 SQL 删除或弱化了 WHERE"),
            (
                diff.removed_join_condition,
                "JOIN_CONDITION_CHANGED",
                "候选 SQL 删除或改变了 JOIN 条件",
            ),
            (diff.changed_group_by, "GROUP_BY_CHANGED", "候选 SQL 改变了 GROUP BY 粒度"),
        ):
            if enabled:
                issues.append(
                    ValidationIssue(
                        code=code,
                        message=message,
                        risk_level=RiskLevel.BLOCKED,
                    )
                )

        if not candidate_matches_plan(failed_sql, sql_candidate, plan):
            issues.append(
                ValidationIssue(
                    code="UNAUTHORIZED_CHANGE",
                    message="候选 SQL 包含 RepairPlan 外修改或未完整执行 RepairPlan",
                    risk_level=RiskLevel.BLOCKED,
                )
            )
        if diff.changed_fragment_count != len(plan.actions):
            issues.append(
                ValidationIssue(
                    code="PLAN_ACTION_COUNT_MISMATCH",
                    message="候选修改数量与 RepairPlan 不一致",
                    risk_level=RiskLevel.BLOCKED,
                )
            )

        action_risks = [action.risk_level for action in plan.actions]
        risk = (
            RiskLevel.BLOCKED
            if issues
            else RiskLevel.HIGH
            if "HIGH" in action_risks
            else RiskLevel.MEDIUM
            if "MEDIUM" in action_risks
            or any(
                action.action_type is RepairActionType.ADD_CAST
                for action in plan.actions
            )
            else RiskLevel.LOW
        )
        allow = risk is RiskLevel.LOW or (
            risk is RiskLevel.MEDIUM and self.allow_medium_risk
        )
        return ValidationResult(
            risk_level=risk,
            passed=not issues,
            issues=issues,
            allow_return_sql=allow,
            reason=None if allow else "候选 SQL 未通过返回门禁",
        )
