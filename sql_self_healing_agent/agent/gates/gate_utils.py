import hashlib
from uuid import uuid4

from sql_self_healing_agent.agent.gates.gate_models import GateFeedback, GateResult
from sql_self_healing_agent.core.time_utils import utc_now_iso


def candidate_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def result(
    gate_name: str,
    sql: str,
    decision: str,
    risk_level: str,
    *,
    code: str | None = None,
    message: str | None = None,
    failed: list[str] | None = None,
    checked: list[str] | None = None,
    evidence_refs: list[str] | None = None,
) -> GateResult:
    digest = candidate_hash(sql)
    feedback = []
    if code and message:
        severity = "BLOCK" if risk_level == "BLOCKED" else "ERROR" if decision in {"REJECT", "HUMAN_REQUIRED"} else "WARNING"
        feedback.append(GateFeedback(feedback_id=f"feedback_{uuid4().hex}", gate_name=gate_name, code=code, severity=severity, message=message, candidate_hash=digest, created_at=utc_now_iso()))
    return GateResult(
        decision=decision,
        risk_level=risk_level,
        gate_name=gate_name,
        candidate_hash=digest,
        feedback=feedback,
        evidence_refs=evidence_refs or [],
        checked_invariants=checked or [],
        failed_invariants=failed or [],
        created_at=utc_now_iso(),
    )
