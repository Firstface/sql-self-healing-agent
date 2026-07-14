import re

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput, DiagnosisResult, LLMDiagnosisResult, RuleDiagnosisResult


class DiagnosisFusion:
    NON_REPAIRABLE = {
        DiagnosedErrorType.PERMISSION_ERROR,
        DiagnosedErrorType.RESOURCE_EXHAUSTED,
        DiagnosedErrorType.INFRASTRUCTURE_ERROR,
    }

    @staticmethod
    def _entity(evidence: str | None) -> str | None:
        if not evidence:
            return None
        patterns = (
            r"(?i)invalid column reference\s+[`'\"]?([A-Za-z_][\w$]*)",
            r"(?i)(?:column not found|unknown column|cannot resolve column)\s*[: ]+[`'\"]?([A-Za-z_][\w$]*)",
            r"(?i)table(?: not found| does not exist)\s*[: ]+[`'\"]?([\w.]+)",
        )
        for pattern in patterns:
            match = re.search(pattern, evidence)
            if match:
                return match.group(1)
        return None

    def fuse(self, diagnosis_input: DiagnosisInput, rule_result: RuleDiagnosisResult, llm_result: LLMDiagnosisResult | None) -> DiagnosisResult:
        chosen_type = rule_result.diagnosed_error_type
        confidence = rule_result.confidence
        reason = "rule fallback"
        keywords = list(rule_result.diagnosed_keywords)
        repairable = chosen_type not in self.NON_REPAIRABLE and chosen_type is not DiagnosedErrorType.UNKNOWN
        manual_reason = None if repairable else "当前错误缺少安全自动修复依据。"
        root_summary = diagnosis_input.log_digest.root_cause_summary
        evidence = rule_result.primary_evidence
        if llm_result is not None:
            if llm_result.diagnosed_error_type == rule_result.diagnosed_error_type or llm_result.confidence >= rule_result.confidence:
                chosen_type = llm_result.diagnosed_error_type
                confidence = max(rule_result.confidence, llm_result.confidence)
                reason = "rule and LLM fusion"
                evidence = llm_result.primary_evidence or evidence
                root_summary = llm_result.root_cause_summary
                repairable = llm_result.is_repairable
                manual_reason = llm_result.manual_repair_reason
            allowed_for_type = diagnosis_input.keyword_vocab.get(chosen_type.value, [])
            llm_keywords = [item for item in llm_result.diagnosed_keywords if item in allowed_for_type]
            keywords = list(dict.fromkeys(llm_keywords + [item for item in keywords if item in allowed_for_type]))
        if not keywords:
            keywords = list(diagnosis_input.keyword_vocab.get(chosen_type.value, diagnosis_input.keyword_vocab.get("UNKNOWN", [])))
        entity = self._entity(evidence or root_summary)
        engine_hint = "hive" if any("hive" in value.casefold() for value in (evidence or "", root_summary or "")) else "unknown"
        normalized_entity = (entity or "unknown").casefold()
        return DiagnosisResult(
            diagnosed_error_type=chosen_type,
            diagnosed_keywords=keywords,
            error_fingerprint=f"{chosen_type.value}:{normalized_entity}:{engine_hint}",
            primary_evidence=evidence,
            root_cause_summary=root_summary,
            confidence=confidence,
            rule_result=rule_result,
            llm_result=llm_result,
            fusion_reason=reason,
            is_repairable=repairable,
            manual_repair_reason=manual_reason,
            primary_entity=entity,
            engine_hint=engine_hint,
        )
