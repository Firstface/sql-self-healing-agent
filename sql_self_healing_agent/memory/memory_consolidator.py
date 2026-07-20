import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone

from sql_self_healing_agent.memory.memory_models import ConsolidationGroup, ConsolidationReport
from sql_self_healing_agent.memory.memory_store import MemoryStore


class MemoryConsolidator:
    """Offline, deterministic consolidation of equivalent confirmed experiences."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    @staticmethod
    def _section(body: str, title: str) -> str:
        marker = f"## {title}"
        if marker not in body:
            return ""
        value = body.split(marker, 1)[1]
        return value.split("\n## ", 1)[0].strip()

    @staticmethod
    def _shape(value: str) -> str:
        value = value.casefold()
        value = re.sub(r"'[^']*'|\"[^\"]*\"", "?", value)
        value = re.sub(r"\b\d+\b", "?", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _signature(self, experience_id: str) -> str:
        frontmatter = self.store.read_frontmatter(experience_id)
        body = self.store.read_body(experience_id)
        components = [
            ",".join(sorted(frontmatter.keyword)),
            self._shape(self._section(body, "Modification")),
        ]
        return hashlib.sha256("|".join(components).encode()).hexdigest()

    def consolidate(self, *, dry_run: bool = True) -> ConsolidationReport:
        ids = self.store.list_experience_ids()
        buckets: dict[str, list[str]] = defaultdict(list)
        for experience_id in ids:
            buckets[self._signature(experience_id)].append(experience_id)
        groups = [sorted(group) for group in buckets.values() if len(group) > 1]
        report_groups: list[ConsolidationGroup] = []
        merged = 0
        for members in sorted(groups):
            canonical = members[0]
            report_groups.append(ConsolidationGroup(canonical_id=canonical, member_ids=members, reason="same keywords, repair shape, and applicability"))
            if dry_run:
                continue
            canonical_text = self.store.experience_path(canonical).read_text(encoding="utf-8").rstrip()
            logical_keys: list[str] = []
            for member in members:
                logical_keys.extend(re.findall(r"logical-key:\s*([^\s]+)", self.store.read_body(member)))
            provenance = "\n".join(f"<!-- logical-key: {key} -->" for key in sorted(set(logical_keys)))
            note = f"\n\n## Consolidation\n\n- merged-at: {datetime.now(timezone.utc).isoformat()}\n- source-count: {len(members)}\n{provenance}\n"
            for member in members[1:]:
                self.store.experience_path(member).unlink(missing_ok=True)
                merged += 1
            self.store.experience_path(canonical).write_text(canonical_text + note, encoding="utf-8")
        if not dry_run:
            self.store.rebuild_index()
        return ConsolidationReport(scanned_count=len(ids), duplicate_group_count=len(groups), merged_count=merged, groups=report_groups, dry_run=dry_run)
