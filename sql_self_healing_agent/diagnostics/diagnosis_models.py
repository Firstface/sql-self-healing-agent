from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.logs.log_models import LogDigest

KeywordVocab = dict[str, list[str]]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosisHistoryItem(StrictModel):
    attempt_id: str
    diagnosed_error_type: DiagnosedErrorType
    diagnosed_keywords: list[str]
    error_fingerprint: str
    primary_entity: str | None = None
    confidence: float
    created_at: str


class DiagnosisInput(StrictModel):
    failed_sql: str
    error_message: str | None = None
    log_digest: LogDigest
    keyword_vocab: KeywordVocab
    allowed_error_types: list[str]
    diagnosis_history: list[DiagnosisHistoryItem] = Field(default_factory=list)
    post_reflection_context: Any | None = None


class RuleDiagnosisResult(StrictModel):
    diagnosed_error_type: DiagnosedErrorType
    diagnosed_keywords: list[str]
    primary_evidence: str | None = None
    confidence: float
    matched_rules: list[str] = Field(default_factory=list)


class LLMDiagnosisResult(StrictModel):
    diagnosed_error_type: DiagnosedErrorType
    diagnosed_keywords: list[str]
    primary_evidence: str | None = None
    root_cause_summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    is_repairable: bool
    manual_repair_reason: str | None = None


class DiagnosisResult(StrictModel):
    diagnosed_error_type: DiagnosedErrorType
    diagnosed_keywords: list[str]
    error_fingerprint: str
    primary_evidence: str | None = None
    root_cause_summary: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rule_result: RuleDiagnosisResult | None = None
    llm_result: LLMDiagnosisResult | None = None
    fusion_reason: str | None = None
    is_repairable: bool
    manual_repair_reason: str | None = None
    primary_entity: str | None = None
    engine_hint: str | None = None
