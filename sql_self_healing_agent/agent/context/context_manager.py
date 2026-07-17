import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone

from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.agent.context.context_models import (
    CompactionLimits,
    ContextSnapshot,
    ContextSummary,
    GateEvidence,
    MainAgentInput,
    SubAgentInput,
)
from sql_self_healing_agent.agent.models.context import AgentContext, WorkspaceValue
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest
from sql_self_healing_agent.agent.models.tool_models import ToolSpec
from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore


class ContextCompactionError(RuntimeError):
    """Signals that safety-critical context cannot be preserved."""


SummaryCallable = Callable[[dict[str, object], CompactionLimits], ContextSummary]


class ContextManager:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        inline_char_limit: int = 4000,
        max_inline_chars: int = 16000,
        max_recent_observations: int = 8,
        soft_token_limit: int = 8000,
        compaction_limits: CompactionLimits | None = None,
        summary_callable: SummaryCallable | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.inline_char_limit = inline_char_limit
        self.max_inline_chars = max_inline_chars
        self.max_recent_observations = max_recent_observations
        self.soft_token_limit = soft_token_limit
        self.compaction_limits = compaction_limits or CompactionLimits()
        self.summary_callable = summary_callable
        self.compaction_call_count = 0
        self.snapshots: list[ContextSnapshot] = []

    @staticmethod
    def _hash(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _artifact_ref_from_workspace(value: WorkspaceValue) -> ArtifactRef | None:
        if not value.artifact_ref:
            return None
        try:
            return ArtifactRef.model_validate_json(value.artifact_ref)
        except Exception:
            return None

    @staticmethod
    def _candidate_sql(context: AgentContext) -> str | None:
        return context.candidate.formal_sql or context.candidate.draft_sql

    def store_long_content(self, context: AgentContext, key: str, content: str, artifact_type: str, summary: str) -> WorkspaceValue:
        if len(content) <= self.inline_char_limit:
            value = WorkspaceValue(status="AVAILABLE", summary=content, updated_at=datetime.now(timezone.utc).isoformat())
        else:
            ref = self.artifact_store.save_text_ref(context.session_id, context.attempt_id, f"{key}.txt", content, artifact_type)  # type: ignore[arg-type]
            value = WorkspaceValue(status="AVAILABLE", summary=summary[: self.inline_char_limit], artifact_ref=ref.model_dump_json(), updated_at=datetime.now(timezone.utc).isoformat())
        context.workspace[key] = value
        return value

    def prepare_for_main_agent(self, context: AgentContext, run_state: AgentRunState, *, available_tools: list[ToolSpec] | None = None, limits: AgentRunLimits | None = None) -> MainAgentInput:
        limits = limits or AgentRunLimits()
        refs = [ref for value in context.workspace.values() if (ref := self._artifact_ref_from_workspace(value)) is not None and ref.sanitized and ref.session_id == context.session_id]
        feedback = [f"{item.gate_name}:{item.decision}:{item.reason}" for item in context.candidate.gate_feedback]
        candidate = self._candidate_sql(context)
        return MainAgentInput(
            goal="修复当前失败 SQL，并仅在通过全部 Gate 后提交候选",
            original_sql=context.original_sql,
            error_message=context.error_message,
            execution_plan_summary=context.execution_plan.summary or context.execution_plan.current_step_id or "",
            current_phase=context.phase,
            workspace_summaries={key: value.summary or value.status for key, value in context.workspace.items()},
            recent_observations=[item.summary for item in context.recent_observations],
            candidate_summary=f"status={context.candidate.status}; candidate_present={candidate is not None}",
            gate_feedback_summary=feedback,
            artifact_refs=refs,
            available_tools=available_tools or [],
            remaining_budget={
                "steps": max(0, limits.max_steps - run_state.step_count),
                "tool_calls": max(0, limits.max_tool_calls - run_state.tool_call_count),
                "sub_agent_calls": max(0, limits.max_sub_agent_calls - run_state.sub_agent_call_count),
                "wall_time_ms": max(0, limits.max_wall_time_ms - run_state.wall_time_ms),
            },
        )

    def prepare_for_sub_agent(self, context: AgentContext, request: SubAgentRequest) -> SubAgentInput:
        refs: list[ArtifactRef] = []
        inline: dict[str, str] = {}
        for key in request.context_refs:
            value = context.workspace.get(key)
            if value is None:
                continue
            ref = self._artifact_ref_from_workspace(value)
            if ref is not None:
                if ref.session_id != context.session_id or not ref.sanitized:
                    raise ContextCompactionError("sub-agent artifact ownership or sanitization check failed")
                refs.append(ref)
            elif value.summary is not None:
                inline[key] = value.summary
        return SubAgentInput(
            task_name=request.task_name,
            objective=request.objective,
            constraints=["不得递归调用 SubAgent", "不得修改 Session、Attempt、Memory 或 Gate", "不得直接提交 SQL_READY"],
            inline_context=inline,
            artifact_refs=refs,
            expected_output_schema=request.expected_output_schema,
            remaining_budget={"steps": request.limits.max_steps, "tool_calls": request.limits.max_tool_calls, "wall_time_ms": request.limits.max_wall_time_ms, "context_requests": request.limits.max_context_requests},
        )

    def prepare_for_gate(self, context: AgentContext) -> GateEvidence:
        candidate = self._candidate_sql(context)
        if not context.original_sql or not context.attempt_id or not context.event_key or not candidate:
            raise ContextCompactionError("safety-critical gate context is missing")
        target = context.workspace.get("target_table")
        partition = context.workspace.get("static_partition")
        metadata = context.workspace.get("metadata_snapshot")
        diagnosis = context.workspace.get("diagnosis")
        return GateEvidence(
            original_sql=context.original_sql,
            candidate_sql=candidate,
            target_table=target.summary if target else None,
            static_partition=partition.summary if partition else None,
            metadata_snapshot_ref=metadata.artifact_ref if metadata else None,
            diagnosis_ref=diagnosis.artifact_ref if diagnosis else None,
            gate_feedback=[f"{item.gate_name}:{item.decision}:{item.reason}" for item in context.candidate.gate_feedback],
            candidate_hash=hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
            attempt_id=context.attempt_id,
            event_key=context.event_key,
        )

    def _clean_memory_result(self, value: WorkspaceValue) -> WorkspaceValue:
        if not value.summary:
            return value
        try:
            data = json.loads(value.summary)
        except (TypeError, json.JSONDecodeError):
            return value
        allowed = {key: data[key] for key in ("matched_count", "scanned_count", "discarded_count", "matched", "matched_by") if key in data}
        return value.model_copy(update={"summary": json.dumps(allowed, ensure_ascii=False, sort_keys=True)})

    def deterministic_trim(self, context: AgentContext) -> list[str]:
        removed: list[str] = []
        if "memory_retrieval" in context.workspace:
            context.workspace["memory_retrieval"] = self._clean_memory_result(context.workspace["memory_retrieval"])
        for key in list(context.workspace):
            if key.startswith("memory_temp_") or key.startswith("expired_"):
                removed.append(key)
                del context.workspace[key]
        seen: set[tuple[str, str, str]] = set()
        retained = []
        for observation in reversed(context.recent_observations):
            marker = (observation.action_type, observation.status, observation.summary)
            if marker in seen:
                continue
            seen.add(marker)
            if observation.status == "SUCCEEDED" and observation.artifact_refs and observation.plan_step_id != context.execution_plan.current_step_id:
                continue
            retained.append(observation)
        context.recent_observations = list(reversed(retained[: self.max_recent_observations]))
        return removed

    def _validate_critical_context(self, context: AgentContext) -> None:
        if not context.original_sql or not context.attempt_id or not context.event_key:
            raise ContextCompactionError("CONTEXT_COMPACTION_FAILED: required P0 context is missing")
        if context.candidate.status != "NONE" and not self._candidate_sql(context):
            raise ContextCompactionError("CONTEXT_COMPACTION_FAILED: current candidate cannot be recovered")
        if context.execution_plan.current_step_id and context.execution_plan.current_step_id not in {item.step_id for item in context.execution_plan.steps}:
            raise ContextCompactionError("CONTEXT_COMPACTION_FAILED: current plan step is invalid")

    def _estimated_size(self, context: AgentContext) -> tuple[int, int]:
        data = context.model_dump_json()
        return len(data), (len(data) + 3) // 4

    def compact_if_needed(self, context: AgentContext, run_state: AgentRunState, *, operation_type: str = "AGENT_ACTION") -> AgentContext:
        self._validate_critical_context(context)
        if operation_type == "CONTEXT_COMPACTION":
            return context
        before = self._hash(context.model_dump(mode="json"))
        removed = self.deterministic_trim(context)
        chars, tokens = self._estimated_size(context)
        method = "DETERMINISTIC_TRIM"
        if (chars > self.max_inline_chars or tokens > self.soft_token_limit) and self.summary_callable is not None and self.compaction_call_count < self.compaction_limits.max_calls:
            self.compaction_call_count += 1
            try:
                summary = self.summary_callable(context.model_dump(mode="json"), self.compaction_limits)
                self._validate_summary(context, summary)
                context.workspace["context_summary"] = WorkspaceValue(status="AVAILABLE", summary=summary.model_dump_json(), updated_at=datetime.now(timezone.utc).isoformat())
                method = "LLM_SUMMARY"
            except Exception:
                self.deterministic_trim(context)
        self._validate_critical_context(context)
        after = self._hash(context.model_dump(mode="json"))
        snapshot = ContextSnapshot(
            snapshot_id=f"snapshot_{len(self.snapshots) + 1:04d}",
            session_id=context.session_id,
            attempt_id=context.attempt_id,
            before_hash=before,
            after_hash=after,
            retained_keys=sorted(context.workspace),
            removed_keys=sorted(removed),
            artifact_refs=[value.artifact_ref for value in context.workspace.values() if value.artifact_ref],
            compaction_method=method,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.snapshots.append(snapshot)
        return context

    def _validate_summary(self, context: AgentContext, summary: ContextSummary) -> None:
        if summary.current_plan_step != context.execution_plan.current_step_id:
            raise ContextCompactionError("summary changed current plan step")
        if summary.candidate_status != context.candidate.status:
            raise ContextCompactionError("summary changed candidate status")
        valid_refs = {value.artifact_ref for value in context.workspace.values() if value.artifact_ref}
        if not set(summary.important_artifact_refs).issubset(valid_refs):
            raise ContextCompactionError("summary forged artifact reference")
        expected_constraints = {item.reason for item in context.candidate.gate_feedback}
        if not expected_constraints.issubset(set(summary.gate_constraints)):
            raise ContextCompactionError("summary removed gate constraint")

    def read_artifact(self, context: AgentContext, ref: ArtifactRef, max_chars: int | None = None) -> str:
        return self.artifact_store.load(ref, max_chars, session_id=context.session_id, attempt_id=ref.attempt_id)
