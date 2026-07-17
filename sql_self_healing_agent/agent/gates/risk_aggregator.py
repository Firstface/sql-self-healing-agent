from sql_self_healing_agent.agent.gates.gate_models import GateResult
from sql_self_healing_agent.core.time_utils import utc_now_iso

_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "BLOCKED": 3}


def aggregate_results(*results: GateResult, allow_medium_risk: bool = False) -> GateResult:
    if not results:
        raise ValueError("at least one GateResult is required")
    risk = max((item.risk_level for item in results), key=_RISK_ORDER.__getitem__)
    decisions = {item.decision for item in results}
    if risk == "BLOCKED" or "REJECT" in decisions:
        decision = "REJECT"
    elif risk == "HIGH" or "HUMAN_REQUIRED" in decisions:
        decision = "HUMAN_REQUIRED"
    elif risk == "MEDIUM":
        decision = "PASS_WITH_WARNING" if allow_medium_risk else "HUMAN_REQUIRED"
    elif "PASS_WITH_WARNING" in decisions:
        decision = "PASS_WITH_WARNING"
    else:
        decision = "PASS"
    return GateResult(
        decision=decision,
        risk_level=risk,
        gate_name="GateRunner",
        candidate_hash=results[0].candidate_hash,
        feedback=[feedback for result in results for feedback in result.feedback],
        evidence_refs=list(dict.fromkeys(ref for result in results for ref in result.evidence_refs)),
        checked_invariants=list(dict.fromkeys(value for result in results for value in result.checked_invariants)),
        failed_invariants=list(dict.fromkeys(value for result in results for value in result.failed_invariants)),
        created_at=utc_now_iso(),
    )
