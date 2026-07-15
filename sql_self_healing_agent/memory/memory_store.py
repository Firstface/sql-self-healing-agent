from pathlib import Path

from sql_self_healing_agent.core.atomic_io import read_json, write_json_atomic
from sql_self_healing_agent.memory.memory_models import Experience


class MemoryStore:
    def __init__(self, base_dir: Path | str = Path("memory_store")) -> None:
        self.base_dir = Path(base_dir)
        self.experiences_dir = self.base_dir / "experiences"

    def list_experiences(self) -> list[Experience]:
        if not self.experiences_dir.exists():
            return []
        return [
            Experience.model_validate(read_json(path))
            for path in sorted(self.experiences_dir.glob("*.json"))
        ]

    def find_by_source(
        self, source_session_id: str, source_attempt_id: str
    ) -> Experience | None:
        for experience in self.list_experiences():
            if (
                experience.source_session_id == source_session_id
                and experience.source_attempt_id == source_attempt_id
            ):
                return experience
        return None

    def save(self, experience: Experience) -> None:
        write_json_atomic(
            self.experiences_dir / f"{experience.experience_id}.json",
            experience.model_dump(mode="json"),
        )
