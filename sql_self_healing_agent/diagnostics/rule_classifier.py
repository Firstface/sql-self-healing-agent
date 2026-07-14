import re

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput, RuleDiagnosisResult


class RuleClassifier:
    def classify(self, diagnosis_input: DiagnosisInput) -> RuleDiagnosisResult:
        evidence = diagnosis_input.log_digest.suspected_engine_error or diagnosis_input.error_message or ""
        categories = diagnosis_input.log_digest.matched_categories
        category = categories[0] if categories else "UNKNOWN"
        try:
            error_type = DiagnosedErrorType(category)
        except ValueError:
            error_type = DiagnosedErrorType.UNKNOWN
        keywords = list(diagnosis_input.keyword_vocab.get(error_type.value, []))
        confidence = 0.9 if categories else (0.45 if evidence else 0.0)
        if error_type is DiagnosedErrorType.UNKNOWN:
            lowered = evidence.casefold()
            rules = [
                (DiagnosedErrorType.COLUMN_NOT_FOUND, ("invalid column", "unknown column", "column not found", "invalid column reference")),
                (DiagnosedErrorType.TABLE_NOT_FOUND, ("table not found", "table does not exist")),
                (DiagnosedErrorType.TYPE_MISMATCH, ("type mismatch", "cannot cast", "cannot compare")),
                (DiagnosedErrorType.PERMISSION_ERROR, ("permission denied", "access denied", "not authorized")),
                (DiagnosedErrorType.SQL_SYNTAX_ERROR, ("syntax error", "parseexception")),
            ]
            for candidate, phrases in rules:
                if any(phrase in lowered for phrase in phrases):
                    error_type = candidate
                    keywords = list(diagnosis_input.keyword_vocab.get(candidate.value, []))
                    confidence = 0.7
                    break
        return RuleDiagnosisResult(
            diagnosed_error_type=error_type,
            diagnosed_keywords=keywords,
            primary_evidence=evidence or None,
            confidence=confidence,
            matched_rules=[error_type.value] if error_type is not DiagnosedErrorType.UNKNOWN else [],
        )
