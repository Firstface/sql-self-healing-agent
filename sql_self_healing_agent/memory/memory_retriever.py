from difflib import SequenceMatcher
from pathlib import Path

from sql_self_healing_agent.core.enums import ExperienceStatus
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.memory.memory_models import (
    MemoryRetrievalResult,
    RetrievedExperience,
)
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot


class MemoryRetriever:
    def __init__(
        self, base_dir: Path | str = Path("memory_store"), top_k: int = 5
    ) -> None:
        self.store = MemoryStore(base_dir)
        self.top_k = top_k

    def retrieve(
        self,
        diagnosis: DiagnosisResult,
        failed_sql: str,
        metadata_snapshot: MetadataSnapshot | None,
    ) -> MemoryRetrievalResult:
        fingerprint_matches = self.store.lookup_fingerprint(
            diagnosis.error_fingerprint
        )
        keyword_matches: list[str] = []
        for keyword in diagnosis.diagnosed_keywords:
            keyword_matches.extend(self.store.lookup_keyword(keyword))
        keyword_matches = list(dict.fromkeys(keyword_matches))
        candidate_ids = list(dict.fromkeys(fingerprint_matches + keyword_matches))
        retrieved: list[RetrievedExperience] = []
        for experience_id in candidate_ids:
            experience = self.store.get(experience_id)
            if experience is None or experience.status is ExperienceStatus.DEPRECATED:
                continue
            score = 0.0
            reasons: list[str] = []
            if experience_id in fingerprint_matches:
                score += 10
                reasons.append("fingerprint_match")
            if experience.diagnosed_error_type is diagnosis.diagnosed_error_type:
                score += 5
                reasons.append("same_error_type")
            overlap = sorted(
                set(experience.diagnosed_keywords) & set(diagnosis.diagnosed_keywords)
            )
            if overlap:
                score += 3 * len(overlap)
                reasons.append(f"keyword_overlap:{','.join(overlap)}")
            if self._metadata_entity_matches(
                experience.primary_entity or diagnosis.primary_entity,
                metadata_snapshot,
            ):
                score += 4
                reasons.append("metadata_entity_match")
            similarity = SequenceMatcher(
                None, experience.failed_sql.casefold(), failed_sql.casefold()
            ).ratio()
            score += 3 * similarity
            if similarity > 0:
                reasons.append(f"sql_similarity:{similarity:.3f}")
            score += min(experience.verified_count, 5)
            reasons.append(f"verified_count:{experience.verified_count}")
            score -= experience.failed_count
            if experience.failed_count:
                reasons.append(f"failed_count:-{experience.failed_count}")
            if experience.status is ExperienceStatus.CONFLICTED:
                score -= 3
                reasons.append("conflicted:-3")
            retrieved.append(
                RetrievedExperience(
                    experience_id=experience.experience_id,
                    score=round(score, 6),
                    match_reasons=reasons,
                    experience=experience,
                )
            )
        retrieved.sort(key=lambda item: (-item.score, item.experience_id))
        return MemoryRetrievalResult(
            retrieved=retrieved[: self.top_k],
            fingerprint_matches=fingerprint_matches,
            keyword_matches=keyword_matches,
        )

    @staticmethod
    def _metadata_entity_matches(
        entity: str | None, metadata_snapshot: MetadataSnapshot | None
    ) -> bool:
        if not entity or metadata_snapshot is None:
            return False
        normalized = entity.casefold()
        return any(
            column.name.casefold() == normalized
            for table in metadata_snapshot.tables
            for column in table.columns
        )
