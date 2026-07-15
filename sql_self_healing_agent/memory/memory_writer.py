import uuid
from datetime import datetime, timezone
from pathlib import Path

from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.memory.memory_models import Experience, RepairStep
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import RepairPlan
from sql_self_healing_agent.session.session_models import RepairAttempt, RepairSession


class MemoryWriter:
    def __init__(self, base_dir: Path | str = Path("memory_store")) -> None:
        self.store = MemoryStore(base_dir)

    def write_success_experience(
        self,
        session: RepairSession,
        attempt: RepairAttempt,
        confirmed_sql: str,
        metadata_snapshot: MetadataSnapshot | None,
        repair_plan: RepairPlan | None = None,
    ) -> Experience:
        if session.status is not SessionStatus.UPSTREAM_CONFIRMED_SUCCESS:
            raise ValueError("Session is not upstream-confirmed success")
        if attempt.status is not AttemptStatus.UPSTREAM_CONFIRMED_SUCCESS:
            raise ValueError("Attempt is not upstream-confirmed success")
        if attempt.sql_candidate is None:
            raise ValueError("Confirmed attempt has no SQL candidate")

        existing = self.store.find_by_source(session.session_id, attempt.attempt_id)
        if existing is not None:
            self.store.rebuild_indices()
            return existing

        now = utc_now_iso()
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        steps = []
        if repair_plan is not None:
            steps = [
                RepairStep(
                    step_no=index,
                    description=action.reason,
                    before_fragment=action.target_fragment,
                    after_fragment=action.replacement_fragment,
                )
                for index, action in enumerate(repair_plan.actions, start=1)
            ]
        metadata_summary = {}
        if metadata_snapshot is not None:
            metadata_summary = {
                "tables": [table.normalized_table_name for table in metadata_snapshot.tables],
                "missing_tables": metadata_snapshot.missing_tables,
            }
        experience = Experience(
            experience_id=f"exp_{date}_{uuid.uuid4().hex[:8]}",
            source_session_id=session.session_id,
            source_attempt_id=attempt.attempt_id,
            task_id=session.task_id,
            diagnosed_error_type=attempt.diagnosed_error_type,
            diagnosed_keywords=attempt.diagnosed_keywords,
            error_fingerprint=attempt.error_fingerprint or "UNKNOWN:unknown:unknown",
            primary_entity=(attempt.error_fingerprint.split(":", 2)[1] if attempt.error_fingerprint and ":" in attempt.error_fingerprint else None),
            original_sql=session.original_sql,
            failed_sql=attempt.input_failed_sql,
            confirmed_sql=confirmed_sql,
            repair_steps=steps,
            metadata_summary=metadata_summary,
            created_at=now,
            updated_at=now,
            last_verified_at=now,
        )
        self.store.save(experience)
        return experience
