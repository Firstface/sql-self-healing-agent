import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef, ArtifactType
from sql_self_healing_agent.core.atomic_io import write_text_atomic
from sql_self_healing_agent.core.persistence_sanitizer import PersistenceSanitizer


class ArtifactAccessError(PermissionError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, base_dir: Path | str = Path(".sessions"), sanitizer: PersistenceSanitizer | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.sanitizer = sanitizer or PersistenceSanitizer()

    def _artifact_path(self, session_id: str, attempt_id: str | None, name: str) -> Path:
        safe_name = Path(name).name
        if safe_name != name or not safe_name:
            raise ValueError("artifact name must be a plain file name")
        if attempt_id is None:
            return self.base_dir / session_id / "artifacts" / safe_name
        return self.base_dir / session_id / "attempts" / attempt_id / "artifacts" / safe_name

    def _legacy_artifact_path(self, session_id: str, attempt_id: str, name: str) -> Path:
        return self.base_dir / session_id / "artifacts" / attempt_id / name

    @staticmethod
    def _build_ref(session_id: str, attempt_id: str | None, artifact_type: ArtifactType, path: Path, content: str) -> ArtifactRef:
        encoded = content.encode("utf-8")
        return ArtifactRef(
            artifact_id=f"art_{uuid4().hex}",
            session_id=session_id,
            attempt_id=attempt_id,
            artifact_type=artifact_type,
            path=f"artifact://{session_id}/{attempt_id or '_session'}/{path.name}",
            content_hash=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            token_estimate=(len(content) + 3) // 4,
            sanitized=True,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def save_text_ref(self, session_id: str, attempt_id: str | None, name: str, content: str, artifact_type: ArtifactType) -> ArtifactRef:
        sanitized = self.sanitizer.sanitize(content)
        path = self._artifact_path(session_id, attempt_id, name)
        write_text_atomic(path, sanitized)
        return self._build_ref(session_id, attempt_id, artifact_type, path, sanitized)

    def save_json_ref(self, session_id: str, attempt_id: str | None, name: str, value: dict[str, object], artifact_type: ArtifactType) -> ArtifactRef:
        serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        return self.save_text_ref(session_id, attempt_id, name, serialized, artifact_type)

    def _resolve(self, ref: ArtifactRef) -> Path:
        if not ref.sanitized:
            raise ArtifactAccessError("unsanitized artifact is forbidden")
        prefix = f"artifact://{ref.session_id}/{ref.attempt_id or '_session'}/"
        if not ref.path.startswith(prefix):
            raise ArtifactAccessError("artifact ownership mismatch")
        name = ref.path.removeprefix(prefix)
        return self._artifact_path(ref.session_id, ref.attempt_id, name)

    def exists(self, ref: ArtifactRef) -> bool:
        try:
            return self._resolve(ref).is_file()
        except (ArtifactAccessError, ValueError):
            return False

    def load(self, ref: ArtifactRef, max_chars: int | None = None, *, session_id: str | None = None, attempt_id: str | None = None) -> str:
        if session_id is not None and ref.session_id != session_id:
            raise ArtifactAccessError("artifact session ownership mismatch")
        if attempt_id is not None and ref.attempt_id != attempt_id:
            raise ArtifactAccessError("artifact attempt ownership mismatch")
        path = self._resolve(ref)
        content = path.read_text(encoding="utf-8")
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != ref.content_hash:
            raise ArtifactIntegrityError("artifact hash mismatch")
        return content if max_chars is None else content[:max_chars]

    # Transitional path-returning API for the existing phase-one orchestrator.
    def save_json(self, session_id: str, attempt_id: str, name: str, payload: dict) -> str:
        serialized = self.sanitizer.sanitize(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        path = self._legacy_artifact_path(session_id, attempt_id, name)
        write_text_atomic(path, serialized)
        return str(path)

    def save_text(self, session_id: str, attempt_id: str, name: str, text: str) -> str:
        sanitized = self.sanitizer.sanitize(text)
        path = self._legacy_artifact_path(session_id, attempt_id, name)
        write_text_atomic(path, sanitized)
        return str(path)
