from pathlib import Path

from sql_self_healing_agent.core.atomic_io import write_json_atomic, write_text_atomic


class ArtifactStore:
    def __init__(self, base_dir: Path | str = Path(".sessions")) -> None:
        self.base_dir = Path(base_dir)

    def _artifact_path(
        self, session_id: str, attempt_id: str, name: str
    ) -> Path:
        return self.base_dir / session_id / "artifacts" / attempt_id / name

    def save_json(
        self,
        session_id: str,
        attempt_id: str,
        name: str,
        payload: dict,
    ) -> str:
        path = self._artifact_path(session_id, attempt_id, name)
        write_json_atomic(path, payload)
        return str(path)

    def save_text(
        self,
        session_id: str,
        attempt_id: str,
        name: str,
        text: str,
    ) -> str:
        path = self._artifact_path(session_id, attempt_id, name)
        write_text_atomic(path, text)
        return str(path)
