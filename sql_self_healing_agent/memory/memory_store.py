import os
import shutil
import uuid
from pathlib import Path

from sql_self_healing_agent.core.atomic_io import read_json, write_json_atomic
from sql_self_healing_agent.core.enums import ExperienceStatus
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.memory.memory_models import (
    Experience,
    FingerprintIndex,
    KeywordIndex,
)


class MemoryStore:
    def __init__(self, base_dir: Path | str = Path("memory_store")) -> None:
        self.base_dir = Path(base_dir)
        self.experiences_dir = self.base_dir / "experiences"
        self.index_dir = self.base_dir / "index"
        self.keyword_index_dir = self.index_dir / "keyword_index"
        self.fingerprint_index_dir = self.index_dir / "fingerprint_index"

    @staticmethod
    def _index_filename(value: str) -> str:
        readable = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in value
        ).strip("_")[:160] or "empty"
        return f"{readable}.json"

    def list_experiences(self) -> list[Experience]:
        if not self.experiences_dir.exists():
            return []
        return [
            Experience.model_validate(read_json(path))
            for path in sorted(self.experiences_dir.glob("*.json"))
        ]

    def get(self, experience_id: str) -> Experience | None:
        path = self.experiences_dir / f"{experience_id}.json"
        if not path.exists():
            return None
        return Experience.model_validate(read_json(path))

    def find_by_source(
        self, source_session_id: str, source_attempt_id: str
    ) -> Experience | None:
        return next(
            (
                experience
                for experience in self.list_experiences()
                if experience.source_session_id == source_session_id
                and experience.source_attempt_id == source_attempt_id
            ),
            None,
        )

    def save(self, experience: Experience, rebuild_indices: bool = True) -> None:
        write_json_atomic(
            self.experiences_dir / f"{experience.experience_id}.json",
            experience.model_dump(mode="json"),
        )
        if rebuild_indices:
            self.rebuild_indices()

    def lookup_keyword(self, keyword: str) -> list[str]:
        path = self.keyword_index_dir / self._index_filename(keyword)
        if not path.exists():
            return []
        index = KeywordIndex.model_validate(read_json(path))
        return index.experience_ids if index.keyword == keyword else []

    def lookup_fingerprint(self, error_fingerprint: str) -> list[str]:
        path = self.fingerprint_index_dir / self._index_filename(error_fingerprint)
        if not path.exists():
            return []
        index = FingerprintIndex.model_validate(read_json(path))
        return index.experience_ids if index.error_fingerprint == error_fingerprint else []

    def record_failure(self, experience_ids: list[str], reason: str) -> None:
        now = utc_now_iso()
        changed = False
        for experience_id in dict.fromkeys(experience_ids):
            experience = self.get(experience_id)
            if experience is None:
                continue
            experience.failed_count += 1
            experience.last_failed_reason = reason
            experience.last_failed_at = now
            experience.updated_at = now
            self.save(experience, rebuild_indices=False)
            changed = True
        if changed:
            self.rebuild_indices()

    def rebuild_indices(self) -> None:
        experiences = [
            experience
            for experience in self.list_experiences()
            if experience.status in {ExperienceStatus.ACTIVE, ExperienceStatus.CONFLICTED}
        ]
        keyword_map: dict[str, set[str]] = {}
        fingerprint_map: dict[str, set[str]] = {}
        for experience in experiences:
            for keyword in experience.diagnosed_keywords:
                keyword_map.setdefault(keyword, set()).add(experience.experience_id)
            fingerprint_map.setdefault(experience.error_fingerprint, set()).add(
                experience.experience_id
            )

        temporary_dir = self.base_dir / f".index_tmp_{uuid.uuid4().hex}"
        backup_dir = self.base_dir / f".index_backup_{uuid.uuid4().hex}"
        try:
            for keyword, experience_ids in keyword_map.items():
                write_json_atomic(
                    temporary_dir / "keyword_index" / self._index_filename(keyword),
                    KeywordIndex(
                        keyword=keyword, experience_ids=sorted(experience_ids)
                    ).model_dump(mode="json"),
                )
            for fingerprint, experience_ids in fingerprint_map.items():
                write_json_atomic(
                    temporary_dir
                    / "fingerprint_index"
                    / self._index_filename(fingerprint),
                    FingerprintIndex(
                        error_fingerprint=fingerprint,
                        experience_ids=sorted(experience_ids),
                    ).model_dump(mode="json"),
                )
            (temporary_dir / "keyword_index").mkdir(parents=True, exist_ok=True)
            (temporary_dir / "fingerprint_index").mkdir(parents=True, exist_ok=True)
            if self.index_dir.exists():
                os.replace(self.index_dir, backup_dir)
            try:
                os.replace(temporary_dir, self.index_dir)
            except Exception:
                if backup_dir.exists() and not self.index_dir.exists():
                    os.replace(backup_dir, self.index_dir)
                raise
            shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            if backup_dir.exists() and not self.index_dir.exists():
                os.replace(backup_dir, self.index_dir)
            raise
