import json
import uuid
from pathlib import Path

from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.trace.trace_models import TraceEvent


class TraceWriter:
    def __init__(self, base_dir: Path | str = Path(".sessions")) -> None:
        self.base_dir = Path(base_dir)

    def trace_path(self, session_id: str) -> Path:
        return self.base_dir / session_id / "trace.jsonl"

    def emit(
        self,
        session_id: str,
        event_type: str,
        stage: str,
        payload: dict,
        attempt_id: str | None = None,
    ) -> None:
        event = TraceEvent(
            event_id=str(uuid.uuid4()),
            session_id=session_id,
            attempt_id=attempt_id,
            event_type=event_type,
            stage=stage,
            timestamp=utc_now_iso(),
            payload=payload,
        )
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
        try:
            path = self.trace_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(line)
                file.flush()
        except Exception as error:
            self.write_trace_error(session_id, error)

    def write_trace_error(self, session_id: str, error: Exception) -> None:
        try:
            path = self.base_dir / session_id / "trace_errors.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as file:
                file.write(f"{utc_now_iso()} {type(error).__name__}: {error}\n")
                file.flush()
        except Exception:
            pass
