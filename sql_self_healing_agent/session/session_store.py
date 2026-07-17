import re
from pathlib import Path

from sql_self_healing_agent.core.atomic_io import read_json, write_json_atomic
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.session.event_key_builder import build_event_key
from sql_self_healing_agent.session.session_lock import SessionLock
from sql_self_healing_agent.session.session_models import (
    RepairAttempt,
    RepairSession,
    UpstreamTaskEventRecord,
)


class SessionStore:
    def __init__(self, base_dir: Path | str = Path(".sessions")) -> None:
        self.base_dir = Path(base_dir)
        self.lock_dir = self.base_dir / ".locks"

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        safe_task_id = re.sub(r"[^A-Za-z0-9._-]", "_", task_id)
        return safe_task_id or "task"

    def lock_for_task(self, task_id: str, timeout_seconds: float = 5.0) -> SessionLock:
        return SessionLock(self.lock_dir, task_id, timeout_seconds=timeout_seconds)

    def session_id_for_task(self, task_id: str) -> str:
        return f"sess_{self._safe_task_id(task_id)}"

    def session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def load_for_task(self, task_id: str) -> RepairSession | None:
        path = self.session_dir(self.session_id_for_task(task_id)) / "session.json"
        if not path.exists():
            return None
        return RepairSession.model_validate(read_json(path))

    def load_or_create_for_event(self, event: UpstreamTaskEvent) -> RepairSession:
        existing = self.load_for_task(event.id)
        if existing is not None:
            return existing
        session_id = self.session_id_for_task(event.id)
        now = utc_now_iso()
        session_dir = self.session_dir(session_id)
        (session_dir / "attempts").mkdir(parents=True, exist_ok=True)
        (session_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        session = RepairSession(
            session_id=session_id,
            task_id=event.id,
            original_sql=event.sql,
            trace_path=str(session_dir / "trace.jsonl"),
            artifact_dir=str(session_dir / "artifacts"),
            created_at=now,
            updated_at=now,
        )
        self.save_session(session)
        return session

    def load_by_task_id_and_sql_hash(self, task_id: str, sql: str) -> RepairSession | None:
        session = self.load_for_task(task_id)
        return session if session is not None and session.original_sql == sql else None

    def save_session(self, session: RepairSession) -> None:
        write_json_atomic(
            self.session_dir(session.session_id) / "session.json",
            session.model_dump(mode="json"),
        )

    def create_event_record(
        self, session: RepairSession, event: UpstreamTaskEvent
    ) -> UpstreamTaskEventRecord:
        return UpstreamTaskEventRecord(
            event_key=build_event_key(event),
            task_id=event.id,
            session_id=session.session_id,
            status=event.status,
            sql=event.sql,
            error_message=event.error_message,
            log_path=event.log_path,
            received_at=utc_now_iso(),
        )

    @staticmethod
    def find_event(
        session: RepairSession, event_key: str
    ) -> UpstreamTaskEventRecord | None:
        return next(
            (record for record in session.upstream_events if record.event_key == event_key),
            None,
        )

    def append_upstream_event(
        self, session: RepairSession, event_record: UpstreamTaskEventRecord
    ) -> None:
        if self.find_event(session, event_record.event_key) is None:
            session.upstream_events.append(event_record)
            session.updated_at = utc_now_iso()
            self.save_session(session)

    def save_event_record(
        self, session: RepairSession, event_record: UpstreamTaskEventRecord
    ) -> None:
        for index, current in enumerate(session.upstream_events):
            if current.event_key == event_record.event_key:
                session.upstream_events[index] = event_record
                session.updated_at = utc_now_iso()
                self.save_session(session)
                return
        raise ValueError("event record is not attached to session")

    def finish_event(
        self,
        session: RepairSession,
        event_record: UpstreamTaskEventRecord,
        result_ref: str | None,
        *,
        error_code: str | None = None,
        processing_status: str = "SUCCEEDED",
    ) -> None:
        event_record.processing_status = processing_status
        event_record.result_ref = result_ref
        event_record.error_code = error_code
        event_record.finished_at = utc_now_iso()
        self.save_event_record(session, event_record)

    def create_attempt(
        self, session: RepairSession, event_record: UpstreamTaskEventRecord
    ) -> RepairAttempt:
        if event_record.status != "FAILED":
            raise ValueError("only FAILED events create attempts")
        if event_record.attempt_id is not None:
            return self.load_attempt(session, event_record.attempt_id)
        attempt_no = len(session.attempt_ids) + 1
        attempt_id = f"attempt_{attempt_no:03d}"
        now = utc_now_iso()
        attempt = RepairAttempt(
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            source_event_key=event_record.event_key,
            input_event_id=event_record.event_key,
            input_failed_sql=event_record.sql,
            input_error_message=event_record.error_message,
            input_log_path=event_record.log_path,
            previous_attempt_id=session.attempt_ids[-1] if session.attempt_ids else None,
            created_at=now,
            updated_at=now,
        )
        event_record.attempt_id = attempt_id
        event_record.processing_status = "PROCESSING"
        session.attempt_ids.append(attempt_id)
        session.updated_at = now
        self.save_attempt(session, attempt)
        self.save_event_record(session, event_record)
        return attempt

    def load_attempt(self, session: RepairSession, attempt_id: str) -> RepairAttempt:
        return RepairAttempt.model_validate(
            read_json(self.session_dir(session.session_id) / "attempts" / f"{attempt_id}.json")
        )

    def save_attempt(self, session: RepairSession, attempt: RepairAttempt) -> None:
        write_json_atomic(
            self.session_dir(session.session_id) / "attempts" / f"{attempt.attempt_id}.json",
            attempt.model_dump(mode="json"),
        )
