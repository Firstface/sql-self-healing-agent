import uuid
from datetime import datetime, timezone
from pathlib import Path

from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.memory.keyword_list import KeywordList
from sql_self_healing_agent.memory.memory_models import ConfirmedExperienceInput
from sql_self_healing_agent.memory.memory_store import MemoryStore
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.repair.repair_models import RepairPlan
from sql_self_healing_agent.session.session_models import RepairAttempt, RepairSession


class MemoryWriter:
    def __init__(self, base_dir: Path | str = Path(".memory"), keyword_list: KeywordList | None = None) -> None:
        self.store = MemoryStore(base_dir)
        self.keyword_list = keyword_list or KeywordList()

    def write_confirmed_experience(self, data: ConfirmedExperienceInput) -> str:
        existing = self.store.find_by_logical_key(data.session_id, data.attempt_id)
        if existing:
            self.store.rebuild_index()
            return existing
        keywords = self.keyword_list.normalize(data.diagnosed_keywords)
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
        experience_id = f"exp_{date}_{uuid.uuid4().hex[:8]}"
        keyword_yaml = "\n".join(f"  - {keyword}" for keyword in keywords)
        content = f'''---
keyword:
{keyword_yaml}
description: {self._single_line(data.description)}
---

## Problem

{self._body(data.description)}

## Original SQL

{data.original_sql}

## Error

{self._body(data.error_summary)}

## Confirmed SQL

{data.confirmed_sql}

## Modification

{self._body(data.modification_summary)}

## Applicable Conditions

- 当前错误关键词与本经验一致；
- 当前元数据仍支持该修改；
- 修改必须重新通过三关 Gate。

<!-- logical-key: {data.session_id}:{data.attempt_id} -->
'''
        self.store.save_markdown(experience_id, content)
        return experience_id

    def write_success_experience(self, session: RepairSession, attempt: RepairAttempt, confirmed_sql: str, metadata_snapshot: MetadataSnapshot | None, repair_plan: RepairPlan | None = None) -> str:
        if session.status is not SessionStatus.UPSTREAM_CONFIRMED_SUCCESS or attempt.status is not AttemptStatus.UPSTREAM_CONFIRMED_SUCCESS:
            raise ValueError("success is not confirmed")
        if session.latest_sql_candidate != confirmed_sql or session.latest_sql_candidate_attempt_id != attempt.attempt_id:
            raise ValueError("SUCCESS does not match current candidate")
        modification = "; ".join(action.reason for action in repair_plan.actions) if repair_plan else "上游确认候选 SQL 成功"
        return self.write_confirmed_experience(ConfirmedExperienceInput(session_id=session.session_id, attempt_id=attempt.attempt_id, original_sql=session.original_sql, confirmed_sql=confirmed_sql, diagnosed_keywords=attempt.diagnosed_keywords, description=attempt.diagnosed_error_type or "SQL 修复经验", modification_summary=modification, error_summary=attempt.input_error_message or ""))

    @staticmethod
    def _single_line(value: str) -> str:
        return " ".join(value.replace("---", "").split())[:500]

    @staticmethod
    def _body(value: str) -> str:
        return value.replace("---", "—")[:20000]
