import re
import sqlglot
from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.llm.prompt_templates import SQL_GENERATOR_SYSTEM, structured_prompt
from sql_self_healing_agent.repair.repair_models import ChangedFragment, RepairActionType, SQLDiffSummary, SQLGenerationResult, SQLGeneratorInput, SQLGeneratorLLMOutput

class SQLGenerator:
    def __init__(self, client: LLMClient | None = None) -> None: self.client = client
    def generate(self, generator_input: SQLGeneratorInput) -> SQLGenerationResult:
        if not generator_input.repair_plan.repairable: return SQLGenerationResult(generated=False, cannot_generate_safely=True, reason="RepairPlan is not repairable")
        if self.client is not None:
            try:
                result=self.client.generate_structured(structured_prompt(SQL_GENERATOR_SYSTEM,generator_input,SQLGeneratorLLMOutput),SQLGeneratorLLMOutput)
            except LLMClientError as error:
                return SQLGenerationResult(generated=False, cannot_generate_safely=True, reason="LLM 未能返回合法的结构化 SQL 结果。")
            return SQLGenerationResult.model_validate(result.model_dump())
        candidate=generator_input.failed_sql; changes=[]
        for action in generator_input.repair_plan.actions:
            if action.action_type is not RepairActionType.REPLACE_COLUMN or not action.target_fragment or not action.replacement_fragment: return SQLGenerationResult(generated=False,cannot_generate_safely=True,reason="Unsupported M2 repair action")
            candidate,count=re.subn(rf"(?<![A-Za-z0-9_]){re.escape(action.target_fragment)}(?![A-Za-z0-9_])",action.replacement_fragment,candidate,flags=re.IGNORECASE)
            if count != 1: return SQLGenerationResult(generated=False,cannot_generate_safely=True,reason="Target fragment was not uniquely replaceable")
            changes.append(ChangedFragment(before=action.target_fragment,after=action.replacement_fragment,action_type=action.action_type,reason=action.reason))
        return SQLGenerationResult(generated=True,sql_candidate=candidate,changed_fragments=changes)

def build_diff(failed_sql: str, generation: SQLGenerationResult) -> SQLDiffSummary:
    candidate=generation.sql_candidate or ""
    try: sqlglot.parse_one(failed_sql,read="hive"); sqlglot.parse_one(candidate,read="hive"); parse_success=True
    except sqlglot.errors.SqlglotError: parse_success=False
    f,c=failed_sql.casefold(),candidate.casefold(); removed_where=" where " in f" {f} " and " where " not in f" {c} "; removed_join=" join " in f" {f} " and (" join " not in f" {c} " or (" on " in f" {f} " and " on " not in f" {c} ")); changed_group=(" group by " in f" {f} ") != (" group by " in f" {c} ")
    fi=re.search(r"(?i)\binsert\s+(?:into|overwrite)(?:\s+table)?\s+([`\w.]+)",failed_sql); ci=re.search(r"(?i)\binsert\s+(?:into|overwrite)(?:\s+table)?\s+([`\w.]+)",candidate); changed_target=bool(fi or ci) and (not fi or not ci or fi.group(1).casefold()!=ci.group(1).casefold())
    part=lambda x: re.search(r"(?i)\bpartition\s*\(([^)]*)\)",x); fp,cp=part(failed_sql),part(candidate); changed_partition=bool(fp or cp) and ((fp.group(1) if fp else None)!=(cp.group(1) if cp else None))
    return SQLDiffSummary(changed_fragment_count=len(generation.changed_fragments),changed_fragments=generation.changed_fragments,removed_where=removed_where,removed_join_condition=removed_join,changed_group_by=changed_group,changed_insert_target=changed_target,changed_static_partition=changed_partition,parse_success=parse_success)
