from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput, LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import LLMClient
from sql_self_healing_agent.llm.prompt_templates import DIAGNOSIS_SYSTEM, structured_prompt


class LLMDiagnoser:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def diagnose(self, diagnosis_input: DiagnosisInput) -> LLMDiagnosisResult:
        result = self.client.generate_structured(
            structured_prompt(DIAGNOSIS_SYSTEM, diagnosis_input, LLMDiagnosisResult),
            LLMDiagnosisResult,
        )
        allowed_keywords = {item for values in diagnosis_input.keyword_vocab.values() for item in values}
        result.diagnosed_keywords = [item for item in result.diagnosed_keywords if item in allowed_keywords]
        return result
