import re

import sqlglot
from sqlglot import exp

from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.llm.prompt_templates import SQL_GENERATOR_SYSTEM, structured_prompt
from sql_self_healing_agent.repair.repair_models import (
    ChangedFragment,
    RepairAction,
    RepairActionType,
    RepairPlan,
    SQLDiffSummary,
    SQLGenerationResult,
    SQLGeneratorInput,
    SQLGeneratorLLMOutput,
)


_IDENTIFIER_ACTIONS = {
    RepairActionType.REPLACE_COLUMN,
    RepairActionType.REPLACE_TABLE,
    RepairActionType.QUALIFY_COLUMN,
}


class SQLGenerator:
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client

    def generate(
        self,
        generator_input: SQLGeneratorInput,
        regeneration_instruction: str | None = None,
    ) -> SQLGenerationResult:
        if not generator_input.repair_plan.repairable:
            return SQLGenerationResult(
                generated=False,
                cannot_generate_safely=True,
                reason="RepairPlan is not repairable",
            )
        if self.client is not None:
            prompt = structured_prompt(
                SQL_GENERATOR_SYSTEM, generator_input, SQLGeneratorLLMOutput
            )
            if regeneration_instruction:
                prompt += (
                    "\nREGENERATION_INSTRUCTION_START\n"
                    + regeneration_instruction
                    + "\nREGENERATION_INSTRUCTION_END"
                )
            try:
                result = self.client.generate_structured(prompt, SQLGeneratorLLMOutput)
            except LLMClientError:
                return SQLGenerationResult(
                    generated=False,
                    cannot_generate_safely=True,
                    reason="LLM 未能返回合法的结构化 SQL 结果。",
                )
            return SQLGenerationResult(
                generated=result.generated,
                sql_candidate=result.sql_candidate,
                cannot_generate_safely=result.cannot_generate_safely,
                reason=result.reason,
            )

        candidate, changes = apply_repair_plan(
            generator_input.failed_sql, generator_input.repair_plan
        )
        if candidate is None:
            return SQLGenerationResult(
                generated=False,
                cannot_generate_safely=True,
                reason="RepairPlan cannot be applied uniquely and safely",
            )
        return SQLGenerationResult(generated=True, sql_candidate=candidate)


def _replace_action(sql: str, action: RepairAction) -> tuple[str, int]:
    if not action.target_fragment or action.replacement_fragment is None:
        return sql, 0
    if action.action_type in _IDENTIFIER_ACTIONS:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(action.target_fragment)}(?![A-Za-z0-9_])"
        return re.subn(
            pattern,
            action.replacement_fragment,
            sql,
            flags=re.IGNORECASE,
        )
    return sql.replace(action.target_fragment, action.replacement_fragment), sql.count(
        action.target_fragment
    )


def apply_repair_plan(
    failed_sql: str, plan: RepairPlan
) -> tuple[str | None, list[ChangedFragment]]:
    candidate = failed_sql
    changes: list[ChangedFragment] = []
    for action in plan.actions:
        if action.action_type is RepairActionType.NO_SAFE_REPAIR:
            return None, []
        candidate, count = _replace_action(candidate, action)
        if count != 1:
            return None, []
        changes.append(
            ChangedFragment(
                before=action.target_fragment or "",
                after=action.replacement_fragment or "",
                action_type=action.action_type,
                reason=action.reason,
            )
        )
    return candidate, changes


def _normalized_text(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").casefold()


def _canonical_sql(sql: str) -> tuple[str | None, exp.Expression | None]:
    try:
        tree = sqlglot.parse_one(sql, read="hive")
    except (sqlglot.errors.SqlglotError, ValueError):
        return None, None
    return tree.sql(dialect="hive", pretty=False, normalize=True), tree


def sql_equivalent(left: str, right: str) -> bool:
    left_canonical, _ = _canonical_sql(left)
    right_canonical, _ = _canonical_sql(right)
    if left_canonical is not None and right_canonical is not None:
        return left_canonical == right_canonical
    return _normalized_text(left) == _normalized_text(right)


def candidate_matches_plan(failed_sql: str, candidate: str, plan: RepairPlan) -> bool:
    expected, _ = apply_repair_plan(failed_sql, plan)
    return expected is not None and sql_equivalent(expected, candidate)


def _query_expression(tree: exp.Expression) -> exp.Expression:
    if isinstance(tree, exp.Insert):
        query = tree.args.get("expression")
        if isinstance(query, exp.Expression):
            return query
    return tree


def _where_terms(tree: exp.Expression) -> set[str]:
    where = _query_expression(tree).args.get("where")
    if not isinstance(where, exp.Where) or not isinstance(where.this, exp.Expression):
        return set()
    condition = where.this
    terms = condition.flatten() if isinstance(condition, exp.And) else [condition]
    return {
        term.sql(dialect="hive", pretty=False, normalize=True)
        for term in terms
    }


def _join_conditions(tree: exp.Expression) -> list[str | None]:
    conditions: list[str | None] = []
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        conditions.append(
            on.sql(dialect="hive", pretty=False, normalize=True) if on else None
        )
    return conditions


def _group_expressions(tree: exp.Expression) -> list[str]:
    group = _query_expression(tree).args.get("group")
    if not isinstance(group, exp.Group):
        return []
    return [
        item.sql(dialect="hive", pretty=False, normalize=True)
        for item in group.expressions
    ]


def _insert_target(tree: exp.Expression) -> str | None:
    insert = tree if isinstance(tree, exp.Insert) else tree.find(exp.Insert)
    if insert is None:
        return None
    target = insert.this
    return target.sql(dialect="hive", pretty=False, normalize=True) if target else None


def _static_partition(sql: str) -> str | None:
    match = re.search(r"(?is)\bpartition\s*\(([^)]*)\)", sql)
    return _normalized_text(match.group(1)) if match else None


def build_diff(
    failed_sql: str,
    generation: SQLGenerationResult,
    plan: RepairPlan,
) -> SQLDiffSummary:
    candidate = generation.sql_candidate or ""
    expected, expected_changes = apply_repair_plan(failed_sql, plan)
    failed_canonical, failed_tree = _canonical_sql(failed_sql)
    expected_canonical, expected_tree = _canonical_sql(expected or "")
    candidate_canonical, candidate_tree = _canonical_sql(candidate)
    parse_success = failed_tree is not None and candidate_tree is not None
    plan_match = expected is not None and sql_equivalent(expected, candidate)
    protected_tree = expected_tree if expected_tree is not None else failed_tree
    protected_sql = expected if expected is not None else failed_sql

    removed_where = False
    removed_join = False
    changed_group = False
    changed_target = False
    ast_comparison_succeeded = protected_tree is not None and candidate_tree is not None
    if ast_comparison_succeeded:
        try:
            protected_where = _where_terms(protected_tree)
            candidate_where = _where_terms(candidate_tree)
            removed_where = not protected_where.issubset(candidate_where)
            removed_join = _join_conditions(protected_tree) != _join_conditions(candidate_tree)
            changed_group = _group_expressions(protected_tree) != _group_expressions(candidate_tree)
            changed_target = _insert_target(protected_tree) != _insert_target(candidate_tree)
        except (AttributeError, TypeError, ValueError, sqlglot.errors.SqlglotError):
            ast_comparison_succeeded = False

    if not ast_comparison_succeeded:
        parse_success = False
        protected_folded = _normalized_text(protected_sql)
        candidate_folded = _normalized_text(candidate)
        protected_has_where = bool(re.search(r"\bwhere\b", protected_folded))
        candidate_has_where = bool(re.search(r"\bwhere\b", candidate_folded))
        removed_where = protected_has_where and not candidate_has_where
        protected_has_join = bool(re.search(r"\bjoin\b", protected_folded))
        candidate_has_join = bool(re.search(r"\bjoin\b", candidate_folded))
        removed_join = protected_has_join and not candidate_has_join
        protected_group = re.search(r"\bgroup\s+by\b", protected_folded)
        candidate_group = re.search(r"\bgroup\s+by\b", candidate_folded)
        changed_group = bool(protected_group) != bool(candidate_group)

    return SQLDiffSummary(
        changed_fragment_count=len(expected_changes) if plan_match else 0,
        changed_fragments=expected_changes if plan_match else [],
        removed_where=removed_where,
        removed_join_condition=removed_join,
        changed_group_by=changed_group,
        changed_insert_target=changed_target,
        changed_static_partition=_static_partition(protected_sql)
        != _static_partition(candidate),
        parse_success=parse_success,
    )
