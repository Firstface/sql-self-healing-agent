import hashlib

from sql_self_healing_agent.agent.gates.gate_models import GateResult
from sql_self_healing_agent.agent.models.candidate import CandidateState
from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.session.session_models import RepairAttempt, RepairSession
from sql_self_healing_agent.session.session_store import SessionStore


class CandidateCommitter:
    def __init__(self, session_store: SessionStore, artifact_store: ArtifactStore) -> None:
        self.session_store = session_store
        self.artifact_store = artifact_store

    def commit(self, session: RepairSession, attempt: RepairAttempt, candidate: CandidateState, gate_result: GateResult) -> None:
        if candidate.status != "READY" or not candidate.formal_sql or gate_result.decision not in {"PASS", "PASS_WITH_WARNING"}:
            raise ValueError("candidate is not ready for commit")
        if attempt.attempt_id != session.attempt_ids[-1] or attempt.source_event_key == "":
            raise ValueError("attempt ownership mismatch")
        digest = hashlib.sha256(candidate.formal_sql.encode("utf-8")).hexdigest()
        if digest != gate_result.candidate_hash:
            raise ValueError("candidate hash mismatch")
        if not candidate.draft_artifact_ref:
            raise ValueError("candidate artifact is missing")
        ref = ArtifactRef.model_validate_json(candidate.draft_artifact_ref)
        if ref.session_id != session.session_id or ref.attempt_id != attempt.attempt_id or not ref.sanitized or not self.artifact_store.exists(ref):
            raise ValueError("candidate artifact is invalid")
        session.latest_sql_candidate = candidate.formal_sql
        session.latest_sql_candidate_attempt_id = attempt.attempt_id
        session.status = SessionStatus.SQL_READY_PENDING_UPSTREAM
        session.updated_at = utc_now_iso()
        attempt.sql_candidate = candidate.formal_sql
        attempt.status = AttemptStatus.SQL_READY
        attempt.updated_at = utc_now_iso()
        self.session_store.save_attempt(session, attempt)
        self.session_store.save_session(session)
