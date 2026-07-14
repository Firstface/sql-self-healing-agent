from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.llm.prompt_templates import PRE_REFLECTION_SYSTEM, structured_prompt
from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionInput, PreReflectionResult
class RepairEvaluator:
 def __init__(self,client: LLMClient | None=None)->None:self.client=client
 def pre_reflect(self,reflection_input:PreReflectionInput)->PreReflectionResult:
  v=reflection_input.validation_result
  if not v.allow_return_sql:return PreReflectionResult(decision=PreReflectionDecision.BLOCK,confidence=1.0,follows_repair_plan=False,minimal_change=False,semantic_risk_level=v.risk_level,reasons=[v.reason or "Validation blocked"],violated_constraints=[i.code for i in v.issues])
  if self.client is not None:
   try:return self.client.generate_structured(structured_prompt(PRE_REFLECTION_SYSTEM,reflection_input,PreReflectionResult),PreReflectionResult)
   except LLMClientError as error:return PreReflectionResult(decision=PreReflectionDecision.BLOCK,confidence=0.0,follows_repair_plan=False,minimal_change=False,semantic_risk_level=v.risk_level,reasons=["LLM 未能返回合法的 PreReflection 结果。"])
  follows=reflection_input.sql_diff_summary.changed_fragment_count==len(reflection_input.repair_plan.actions); minimal=reflection_input.sql_diff_summary.changed_fragment_count<=max(1,len(reflection_input.repair_plan.actions)); decision=PreReflectionDecision.RETURN_SQL if follows and minimal else PreReflectionDecision.BLOCK
  return PreReflectionResult(decision=decision,confidence=.95,follows_repair_plan=follows,minimal_change=minimal,semantic_risk_level=v.risk_level,reasons=["候选 SQL 仅执行 RepairPlan 中的最小修改。"] if decision is PreReflectionDecision.RETURN_SQL else ["候选 SQL 未忠实执行 RepairPlan。"])
