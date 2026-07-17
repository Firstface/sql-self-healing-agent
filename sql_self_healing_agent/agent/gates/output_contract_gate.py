from sql_self_healing_agent.agent.gates.gate_models import GateRequest, GateResult
from sql_self_healing_agent.agent.gates.gate_utils import candidate_hash, result


class OutputContractGate:
    def run(self, request: GateRequest, previous_results: list[GateResult]) -> GateResult:
        blockers = [item for item in previous_results if item.decision in {"REJECT", "HUMAN_REQUIRED"} or item.risk_level in {"HIGH", "BLOCKED", "MEDIUM"}]
        if blockers:
            return result("OutputContractGate", request.candidate_sql, "HUMAN_REQUIRED", "HIGH", code="PRIOR_GATE_NOT_PASS", message="前置 Gate 未全部通过。", failed=["ALL_PRIOR_GATES_PASS"])
        if not request.candidate_sql.strip() or any(item.candidate_hash != candidate_hash(request.candidate_sql) for item in previous_results):
            return result("OutputContractGate", request.candidate_sql, "HUMAN_REQUIRED", "HIGH", code="OUTPUT_CONTRACT_INVALID", message="候选为空或候选哈希与前置 Gate 不一致。", failed=["CANDIDATE_HASH_STABLE"])
        return result("OutputContractGate", request.candidate_sql, "PASS", "LOW", checked=["CANDIDATE_NON_EMPTY", "CANDIDATE_HASH_STABLE", "PRIOR_GATES_PASS"])
