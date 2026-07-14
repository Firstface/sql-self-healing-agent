from pathlib import Path

from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.core.models import AgentExternalResult, UpstreamTaskEvent
from sql_self_healing_agent.core.sql_matcher import SQLMatcher
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.session.session_models import RepairSession
from sql_self_healing_agent.session.session_store import SessionStore
from sql_self_healing_agent.trace.trace_writer import TraceWriter


class RepairAgentService:
    def __init__(self, sessions_dir: Path | str = Path("sessions")) -> None:
        self.session_store = SessionStore(sessions_dir)
        self.trace_writer = TraceWriter(sessions_dir)
        self.artifact_store = ArtifactStore(sessions_dir)
        self.sql_matcher = SQLMatcher()

    def handle_upstream_event(
        self, event: UpstreamTaskEvent
    ) -> AgentExternalResult:
        if event.status == "FAILED":
            return self._handle_failed_event(event)

        if event.status == "SUCCESS":
            return self._handle_success_event(event)

        return AgentExternalResult(
            status="NO_SQL",
            message=f"Unsupported upstream event status: {event.status}",
        )

    def _is_duplicate_failed_event(
        self, session: RepairSession, event: UpstreamTaskEvent
    ) -> bool:
        return any(
            record.task_id == event.id
            and record.status == event.status
            and self.sql_matcher.match(record.sql, event.sql)
            and record.log_path == event.log_path
            for record in session.upstream_events
        )

    def _is_duplicate_success_event(
        self, session: RepairSession, event: UpstreamTaskEvent
    ) -> bool:
        return any(
            record.task_id == event.id
            and record.status == event.status
            and self.sql_matcher.match(record.sql, event.sql)
            for record in session.upstream_events
        )

    def _handle_failed_event(
        self, event: UpstreamTaskEvent
    ) -> AgentExternalResult:
        session = self.session_store.load_or_create_for_event(event)
        if self._is_duplicate_failed_event(session, event):
            return AgentExternalResult(
                status="HUMAN_REQUIRED",
                message="M1 skeleton only; repair pipeline not implemented.",
            )

        event_record = self.session_store.create_event_record(event)
        self.session_store.append_upstream_event(session, event_record)
        self.trace_writer.emit(
            session.session_id,
            "upstream_event_received",
            "upstream_event",
            {"status": event.status},
        )

        session.status = SessionStatus.RUNNING
        session.updated_at = utc_now_iso()
        self.session_store.save_session(session)

        attempt = self.session_store.create_attempt(session, event_record)
        self.artifact_store.save_json(
            session.session_id,
            attempt.attempt_id,
            "upstream_event.json",
            event_record.model_dump(mode="json"),
        )
        self.trace_writer.emit(
            session.session_id,
            "attempt_created",
            "orchestrator",
            {"attempt_no": attempt.attempt_no},
            attempt.attempt_id,
        )

        attempt.status = AttemptStatus.HUMAN_REQUIRED
        attempt.updated_at = utc_now_iso()
        self.session_store.save_attempt(session, attempt)
        session.status = SessionStatus.HUMAN_REQUIRED
        session.updated_at = utc_now_iso()
        self.session_store.save_session(session)
        self.trace_writer.emit(
            session.session_id,
            "human_required_returned",
            "orchestrator",
            {"reason": "M1 skeleton only; repair pipeline not implemented."},
            attempt.attempt_id,
        )
        return AgentExternalResult(
            status="HUMAN_REQUIRED",
            message="M1 skeleton only; repair pipeline not implemented.",
        )

    def _handle_success_event(
        self, event: UpstreamTaskEvent
    ) -> AgentExternalResult:
        session = self.session_store.load_or_create_for_event(event)
        if not self._is_duplicate_success_event(session, event):
            event_record = self.session_store.create_event_record(event)
            self.session_store.append_upstream_event(session, event_record)
            self.trace_writer.emit(
                session.session_id,
                "upstream_success_received",
                "upstream_event",
                {},
            )
        return AgentExternalResult(status="SUCCESS_ACK")
