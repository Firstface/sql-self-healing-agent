from typing import TypeVar

from pydantic import BaseModel

from sql_self_healing_agent.diagnostics.diagnosis_models import LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import LLMClient
from sql_self_healing_agent.repair.reflection import PreReflectionResult
from sql_self_healing_agent.repair.repair_models import SQLGeneratorLLMOutput

T = TypeVar("T", bound=BaseModel)


class FakeLLMClient(LLMClient):
    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        if response_model is LLMDiagnosisResult:
            payload = {
                "diagnosed_error_type": "COLUMN_NOT_FOUND",
                "diagnosed_keywords": ["column_not_found", "missing_field"],
                "primary_evidence": "SemanticException: Invalid column reference pay_amt",
                "root_cause_summary": "Hive cannot resolve pay_amt",
                "confidence": 0.95,
                "is_repairable": True,
                "manual_repair_reason": None,
            }
        elif response_model is SQLGeneratorLLMOutput:
            payload = {
                "generated": True,
                "sql_candidate": "SELECT user_id, payment_amount FROM dwd_order_detail WHERE date = ",
                "cannot_generate_safely": False,
                "reason": None,
                "changed_fragments": [
                    {"before": "pay_amt", "after": "payment_amount", "action_type": "REPLACE_COLUMN", "reason": "execute RepairPlan"}
                ],
            }
        elif response_model is PreReflectionResult:
            payload = {
                "decision": "RETURN_SQL",
                "confidence": 0.96,
                "follows_repair_plan": True,
                "minimal_change": True,
                "semantic_risk_level": "LOW",
                "reasons": ["single metadata-verified column replacement"],
                "violated_constraints": [],
                "regeneration_instruction": None,
            }
        else:
            raise AssertionError(response_model)
        return response_model.model_validate(payload)
