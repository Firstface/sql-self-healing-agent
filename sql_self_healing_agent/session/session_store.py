import re
import uuid
from pathlib import Path

from sql_self_healing_agent.core.atomic_io import read_json, write_json_atomic
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.session.session_models import (
    RepairAttempt,
    RepairSession,
    UpstreamTaskEventRecord,
)


class SessionStore:
    def __init__(self, base_dir: Path | str = Path("sessions")) -> None:
        self.base_dir = Path(base_dir)

    @staticmethod
    def _safe_task_id(task_id: str) -> str:
        safe_task_id = re.sub(r"[^A-Za-z0-9._-]", "_", task_id)
        return safe_task_id or "task"

    def session_id_for_task(self, task_id: str) -> str:
        return f"sess_{self._safe_task_id(task_id)}"

    def session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def load_or_create_for_event(self, event: UpstreamTaskEvent) -> RepairSession:
        session_id = self.session_id_for_task(event.id)
        session_path = self.session_dir(session_id) / "session.json"
        if session_path.exists():
            return RepairSession.model_validate(read_json(session_path))

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

    def load_by_task_id_and_sql_hash(
        self, task_id: str, sql: str
    ) -> RepairSession | None:
        session_path = (
            self.session_dir(self.session_id_for_task(task_id)) / "session.json"
        )
        if not session_path.exists():
            return None
        session = RepairSession.model_validate(read_json(session_path))
        return session if session.original_sql == sql else None

    def save_session(self, session: RepairSession) -> None:
        write_json_atomic(
            self.session_dir(session.session_id) / "session.json",
            session.model_dump(mode="json"),
        )

    def create_event_record(self, event: UpstreamTaskEvent) -> UpstreamTaskEventRecord:
        return UpstreamTaskEventRecord(
            event_id=f"evt_{str(uuid.uuid4()).replace('-', '_')}",
            task_id=event.id,
            status=event.status,
            sql=event.sql,
            error_message=event.error_message,
            log_path=event.log_path,
            received_at=utc_now_iso(),
        )

    def append_upstream_event(
        self, session: RepairSession, event_record: UpstreamTaskEventRecord
    ) -> None:
        session.upstream_events.append(event_record)
        session.updated_at = utc_now_iso()
        self.save_session(session)

    def create_attempt(
        self, session: RepairSession, event_record: UpstreamTaskEventRecord
    ) -> RepairAttempt:
        attempt_no = len(session.attempt_ids) + 1
        attempt_id = f"attempt_{attempt_no:03d}"
        now = utc_now_iso()
        attempt = RepairAttempt(
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            input_event_id=event_record.event_id,
            input_failed_sql=event_record.sql,
            input_error_message=event_record.error_message,
            input_log_path=event_record.log_path,
            created_at=now,
            updated_at=now,
        )
        session.attempt_ids.append(attempt_id)
        session.updated_at = now
        self.save_attempt(session, attempt)
        self.save_session(session)
        return attempt

    def load_attempt(self, session: RepairSession, attempt_id: str) -> RepairAttempt:
        return RepairAttempt.model_validate(
            read_json(self.session_dir(session.session_id) / "attempts" / f"{attempt_id}.json")
        )

    def save_attempt(self, session: RepairSession, attempt: RepairAttempt) -> None:
        write_json_atomic(
            self.session_dir(session.session_id)
            / "attempts"
            / f"{attempt.attempt_id}.json",
            attempt.model_dump(mode="json"),
        )
