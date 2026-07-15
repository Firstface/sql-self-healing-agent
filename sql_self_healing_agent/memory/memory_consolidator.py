import uuid
from collections import defaultdict
from pathlib import Path

from sql_self_healing_agent.core.atomic_io import write_json_atomic
from sql_self_healing_agent.core.enums import ExperienceStatus
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.memory.memory_models import (
    ConsolidationAction,
    ConsolidationProposal,
    Experience,
)
from sql_self_healing_agent.memory.memory_store import MemoryStore


class MemoryConsolidator:
    def __init__(self, base_dir: Path | str = Path("memory_store")) -> None:
        self.store = MemoryStore(base_dir)
        self.proposals_dir = self.store.base_dir / "consolidation" / "proposals"

    def consolidate(self) -> tuple[ConsolidationProposal, Path, dict[str, int]]:
        experiences = self.store.list_experiences()
        groups: dict[tuple[str, str], list[Experience]] = defaultdict(list)
        for experience in experiences:
            groups[(experience.error_fingerprint, experience.confirmed_sql)].append(
                experience
            )

        actions: list[ConsolidationAction] = []
        counts = {
            "scanned": len(experiences),
            "merged": 0,
            "marked_conflicted": 0,
            "marked_deprecated": 0,
            "updated": 0,
            "kept": 0,
        }
        grouped_ids: set[str] = set()
        for group in groups.values():
            grouped_ids.update(item.experience_id for item in group)
            if len(group) == 1:
                actions.append(
                    ConsolidationAction(
                        action="KEEP",
                        source_experience_ids=[group[0].experience_id],
                        target_summary="经验唯一且无冲突，保持不变。",
                    )
                )
                counts["kept"] += 1
                continue
            actions.append(
                ConsolidationAction(
                    action="MERGE",
                    source_experience_ids=[item.experience_id for item in group],
                    target_summary="相同错误指纹和确认 SQL 的成功经验可合并。",
                )
            )
            counts["merged"] += len(group) - 1

        by_fingerprint: dict[str, list[Experience]] = defaultdict(list)
        for experience in experiences:
            by_fingerprint[experience.error_fingerprint].append(experience)
        for group in by_fingerprint.values():
            confirmed = {item.confirmed_sql for item in group}
            if len(confirmed) > 1:
                actions.append(
                    ConsolidationAction(
                        action="MARK_CONFLICT",
                        source_experience_ids=[item.experience_id for item in group],
                        target_summary="相同错误指纹存在不同确认 SQL，需要人工判断适用条件。",
                    )
                )
                counts["marked_conflicted"] += len(group)
        for experience in experiences:
            if experience.status is ExperienceStatus.DEPRECATED:
                actions.append(
                    ConsolidationAction(
                        action="MARK_DEPRECATED",
                        source_experience_ids=[experience.experience_id],
                        target_summary="经验已标记过时，索引重建时继续排除。",
                    )
                )
                counts["marked_deprecated"] += 1
            elif experience.failed_count > experience.verified_count:
                actions.append(
                    ConsolidationAction(
                        action="UPDATE_CARD",
                        source_experience_ids=[experience.experience_id],
                        target_summary="失败次数高于验证次数，建议人工更新经验卡片。",
                    )
                )
                counts["updated"] += 1

        now = utc_now_iso()
        proposal = ConsolidationProposal(
            proposal_id=f"proposal_{uuid.uuid4().hex}",
            created_at=now,
            actions=actions,
        )
        proposal_path = self.proposals_dir / f"{proposal.proposal_id}.json"
        write_json_atomic(proposal_path, proposal.model_dump(mode="json"))
        self.store.rebuild_indices()
        return proposal, proposal_path, counts
