from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput, LLMDiagnosisResult
from sql_self_healing_agent.llm.llm_client import LLMClient
from sql_self_healing_agent.agent.llm import LLMAdapter
from sql_self_healing_agent.llm.prompt_templates import DIAGNOSIS_SYSTEM, structured_prompt


class LLMDiagnoser:
    def __init__(self, client: LLMClient, adapter: LLMAdapter | None = None) -> None:
        self.client = client
        self.adapter = adapter

    def diagnose(self, diagnosis_input: DiagnosisInput) -> LLMDiagnosisResult:
        prompt = structured_prompt(DIAGNOSIS_SYSTEM, diagnosis_input, LLMDiagnosisResult)
        result = (
            self.adapter.generate_structured(prompt, LLMDiagnosisResult, purpose="diagnosis", input_summary="diagnosis input and log digest")
            if self.adapter is not None
            else self.client.generate_structured(prompt, LLMDiagnosisResult)
        )
        allowed_keywords = {item for values in diagnosis_input.keyword_vocab.values() for item in values}
        result.diagnosed_keywords = [item for item in result.diagnosed_keywords if item in allowed_keywords]
        return result
