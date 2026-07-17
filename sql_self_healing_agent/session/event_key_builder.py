import hashlib
import json
import re

from sql_self_healing_agent.core.models import UpstreamTaskEvent


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_event_key(event: UpstreamTaskEvent) -> str:
    payload = {
        "id": event.id,
        "status": event.status,
        "sql": _normalize_sql(event.sql),
        "error_message": _normalize_text(event.error_message),
        "log_path": _normalize_text(event.log_path),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
