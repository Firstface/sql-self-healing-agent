import json
from pathlib import Path

from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.agent.artifacts.artifact_ref import ArtifactRef
from sql_self_healing_agent.core.atomic_io import read_json
from sql_self_healing_agent.core.enums import AttemptStatus, SessionStatus
from sql_self_healing_agent.core.models import AgentExternalResult, UpstreamTaskEvent
from sql_self_healing_agent.core.sql_matcher import SQLMatcher
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.orchestrator.external_result_factory import ExternalResultFactory
from sql_self_healing_agent.orchestrator.agentic_failed_event_processor import AgenticFailedEventProcessor, ProcessorDependencies
from sql_self_healing_agent.agent.hooks import BudgetHook, CompressionAdapterHook, HookManager, RetryAdapterHook, SafetyHook, TraceHook
from sql_self_healing_agent.diagnostics.llm_diagnoser import LLMDiagnoser
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisResult
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.llm.llm_client import LLMClient
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.memory.memory_writer import MemoryWriter
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.repair.evaluator import RepairEvaluator
from sql_self_healing_agent.repair.reflection import PostReflectionInput
from sql_self_healing_agent.repair.repair_models import RepairPlan
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.repair.sql_generator import SQLGenerator
from sql_self_healing_agent.session.session_models import RepairSession
from sql_self_healing_agent.session.session_store import SessionStore
from sql_self_healing_agent.session.event_key_builder import build_event_key
from sql_self_healing_agent.session.session_lock import SessionLockTimeout
from sql_self_healing_agent.trace.trace_writer import TraceWriter


class RepairAgentService:
    def __init__(self, sessions_dir: Path | str = Path(".sessions"), llm_client: LLMClient | None = None, metadata_path: Path | str = Path("mocks/metadata/tables.json"), keyword_vocab_path: Path | str | None = None, allow_medium_risk: bool = False, memory_dir: Path | str = Path(".memory")) -> None:
        self.session_store = SessionStore(sessions_dir)
        self.trace_writer = TraceWriter(sessions_dir)
        self.artifact_store = ArtifactStore(sessions_dir)
        self.sql_matcher = SQLMatcher()
        self.metadata_provider = MockMetadataProvider(metadata_path)
        default_vocab = Path(__file__).parents[1] / "logs" / "keyword_vocab.json"
        self.keyword_vocab = json.loads(Path(keyword_vocab_path or default_vocab).read_text(encoding="utf-8"))
        self.llm_client = llm_client
        self.llm_diagnoser = LLMDiagnoser(self.llm_client) if self.llm_client is not None else None
        self.memory_retriever = MemoryRetriever(memory_dir)
        self.memory_writer = MemoryWriter(memory_dir)
        self.repair_planner = RepairPlanner(self.metadata_provider)
        self.sql_generator = SQLGenerator(self.llm_client)
        self.allow_medium_risk = allow_medium_risk
        self.evaluator = RepairEvaluator(self.llm_client)
        self.external_results = ExternalResultFactory()
        self.hook_manager = HookManager([TraceHook(self.trace_writer), BudgetHook(), SafetyHook(), CompressionAdapterHook(), RetryAdapterHook()])

    def handle_upstream_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        if event.status == "FAILED":
            return self._handle_failed_event(event)
        if event.status == "SUCCESS":
            try:
                return self._handle_success_event(event)
            except Exception as error:
                session = self.session_store.load_or_create_for_event(event)
                self.trace_writer.emit(
                    session.session_id,
                    "system_error",
                    "orchestrator",
                    {"error_type": type(error).__name__},
                )
                return self.external_results.human_required("SUCCESS 事件处理失败，请人工检查本地状态。")
        return self.external_results.no_sql(f"Unsupported upstream event status: {event.status}")

    def _is_duplicate_failed_event(self, session: RepairSession, event: UpstreamTaskEvent) -> bool:
        return self.session_store.find_event(session, build_event_key(event)) is not None

    def _is_duplicate_success_event(self, session: RepairSession, event: UpstreamTaskEvent) -> bool:
        return self.session_store.find_event(session, build_event_key(event)) is not None

    def _load_processed_failed_result(
        self, session: RepairSession, event: UpstreamTaskEvent
    ) -> AgentExternalResult | None:
        event_key = build_event_key(event)
        record = self.session_store.find_event(session, event_key)
        if record is not None and record.result_ref and Path(record.result_ref).exists():
            return AgentExternalResult.model_validate(read_json(Path(record.result_ref)))
        return None

    def _persist_external_result(
        self, session: RepairSession, attempt, result: AgentExternalResult
    ) -> AgentExternalResult:
        result_ref = self.artifact_store.save_json(
            session.session_id,
            attempt.attempt_id,
            "external_result.json",
            result.model_dump(mode="json"),
        )
        session.last_external_result_ref = result_ref
        record = self.session_store.find_event(session, attempt.source_event_key)
        if record is not None:
            self.session_store.finish_event(session, record, result_ref)
        else:
            self.session_store.save_session(session)
        return result

    def _handle_failed_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        try:
            with self.session_store.lock_for_task(event.id):
                session = self.session_store.load_or_create_for_event(event)
                existing = self.session_store.find_event(session, build_event_key(event))
                if existing is not None:
                    processed = self._load_processed_failed_result(session, event)
                    return processed or self.external_results.human_required("该事件正在处理或需要从持久化状态恢复。")
                record = self.session_store.create_event_record(session, event)
                self.session_store.append_upstream_event(session, record)
                attempt = self.session_store.create_attempt(session, record)
                session.status = SessionStatus.RUNNING
                session.updated_at = utc_now_iso()
                self.session_store.save_session(session)
        except SessionLockTimeout:
            return self.external_results.human_required("Session 正在处理中，请稍后重试。")

        if not event.log_path and not event.error_message:
            return self._human(session, attempt, "缺少可用于诊断的日志和错误信息。")
        previous_attempt = None
        if session.latest_sql_candidate == event.sql and session.latest_sql_candidate_attempt_id:
            previous_attempt = self.session_store.load_attempt(session, session.latest_sql_candidate_attempt_id)
            previous_attempt.status = AttemptStatus.UPSTREAM_FAILED
            previous_attempt.updated_at = utc_now_iso()
            self.session_store.save_attempt(session, previous_attempt)
        try:
            dependencies = ProcessorDependencies(
                keyword_vocab=self.keyword_vocab,
                metadata_provider=self.metadata_provider,
                memory_retriever=self.memory_retriever,
                repair_planner=self.repair_planner,
                sql_generator=self.sql_generator,
                llm_diagnoser=self.llm_diagnoser,
                evaluator=self.evaluator,
                allow_medium_risk=self.allow_medium_risk,
            )
            run_result, context, run_state, executor = AgenticFailedEventProcessor(dependencies, self.artifact_store, self.hook_manager).run(event, session, attempt)
            attempt.agent_run_state_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "agent_run_state.json", run_state.model_dump(mode="json"))
            for key, field in (("log_digest", "log_digest_path"), ("diagnosis", "diagnosis_path"), ("metadata_snapshot", "metadata_snapshot_path"), ("memory_retrieval", "memory_retrieval_path"), ("repair_plan", "repair_plan_path")):
                workspace_value = context.workspace.get(key)
                if workspace_value is not None and workspace_value.artifact_ref:
                    setattr(attempt, field, workspace_value.artifact_ref)
            diagnosis = executor.objects.get("diagnosis")
            plan = executor.objects.get("repair_plan")
            if previous_attempt is not None and diagnosis is not None:
                previous_diagnosis = self._load_attempt_artifact(previous_attempt.diagnosis_path, DiagnosisResult)
                previous_plan = self._load_attempt_artifact(previous_attempt.repair_plan_path, RepairPlan)
                current_log = executor.objects.get("log_digest")
                if previous_diagnosis is not None and previous_plan is not None and current_log is not None and previous_attempt.sql_candidate:
                    post = self.evaluator.post_reflect(PostReflectionInput(previous_attempt=previous_attempt, previous_diagnosis=previous_diagnosis, previous_repair_plan=previous_plan, previous_sql_candidate=previous_attempt.sql_candidate, current_failed_sql=event.sql, current_log_digest=current_log, current_diagnosis=diagnosis, diagnosis_history=session.diagnosis_history))
                    post_ref = self.artifact_store.save_json_ref(session.session_id, attempt.attempt_id, "post_reflection_result.json", post.model_dump(mode="json"), "TRACE_PAYLOAD")
                    attempt.post_reflection_result_path = post_ref.model_dump_json()
            if diagnosis is not None:
                attempt.diagnosed_error_type = diagnosis.diagnosed_error_type.value
                attempt.diagnosed_keywords = diagnosis.diagnosed_keywords
                attempt.error_fingerprint = diagnosis.error_fingerprint
            if run_result.status != "CANDIDATE_READY" or not run_result.candidate_sql:
                static_blocked = bool(context.candidate.gate_feedback and context.candidate.gate_feedback[-1].gate_name == "StaticValidationGate")
                attempt.status = (
                    AttemptStatus.HUMAN_REQUIRED
                    if run_result.status in {"HUMAN_REQUIRED", "FAILED"}
                    else AttemptStatus.VALIDATION_BLOCKED
                    if static_blocked
                    else AttemptStatus.REFLECTION_BLOCKED
                )
                attempt.stop_reason = run_result.reason or run_result.stop_reason
                self.session_store.save_attempt(session, attempt)
                if run_result.status == "NO_SQL":
                    return self._persist_external_result(session, attempt, self.external_results.no_sql(run_result.reason))
                return self._human(session, attempt, run_result.reason or "Agent 无法安全生成候选 SQL。")

            attempt.sql_candidate = run_result.candidate_sql
            attempt.sql_candidate_path = run_result.candidate_artifact_ref
            attempt.status = AttemptStatus.SQL_READY
            attempt.updated_at = utc_now_iso()
            with self.session_store.lock_for_task(event.id):
                latest = self.session_store.load_for_task(event.id)
                if latest is None:
                    return self._human(session, attempt, "Session 丢失，无法提交候选 SQL。")
                current = self.session_store.find_event(latest, attempt.source_event_key)
                if current is None or current.attempt_id != attempt.attempt_id:
                    return self._human(session, attempt, "事件状态已变化，无法安全提交候选 SQL。")
                latest.latest_sql_candidate = run_result.candidate_sql
                latest.latest_sql_candidate_attempt_id = attempt.attempt_id
                latest.status = SessionStatus.SQL_READY_PENDING_UPSTREAM
                latest.updated_at = utc_now_iso()
                self.session_store.save_attempt(latest, attempt)
                self.session_store.save_session(latest)
                session = latest
            return self._persist_external_result(session, attempt, self.external_results.sql_ready(run_result.candidate_sql))
        except SessionLockTimeout:
            return self._human(session, attempt, "Session 正在处理中，候选提交失败。")
        except Exception as error:
            self.trace_writer.emit(session.session_id, "agentic_processor_failed", "agent_runner", {"error_type": type(error).__name__}, attempt.attempt_id)
            return self._human(session, attempt, "Agent 内部处理失败，请人工介入。")

    def _load_attempt_artifact(self, reference: str | None, model_type):
        if not reference:
            return None
        try:
            ref = ArtifactRef.model_validate_json(reference)
            content = self.artifact_store.load(ref, session_id=ref.session_id, attempt_id=ref.attempt_id)
            return model_type.model_validate_json(content)
        except Exception:
            path = Path(reference)
            return model_type.model_validate(read_json(path)) if path.exists() else None

    def _human(self, session: RepairSession, attempt, message: str) -> AgentExternalResult:
        attempt.status = AttemptStatus.HUMAN_REQUIRED
        attempt.updated_at = utc_now_iso()
        session.status = SessionStatus.HUMAN_REQUIRED
        session.updated_at = utc_now_iso()
        self.session_store.save_attempt(session, attempt)
        self.session_store.save_session(session)
        self.trace_writer.emit(session.session_id, "human_required_returned", "orchestrator", {"reason": message}, attempt.attempt_id)
        return self._persist_external_result(
            session,
            attempt,
            self.external_results.human_required(message),
        )

    def _handle_success_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        try:
            with self.session_store.lock_for_task(event.id):
                session = self.session_store.load_or_create_for_event(event)
                event_key = build_event_key(event)
                existing = self.session_store.find_event(session, event_key)
                if existing is not None and existing.processing_status == "SUCCEEDED":
                    return self.external_results.success_ack()
                if existing is not None:
                    event_record = existing
                else:
                    event_record = self.session_store.create_event_record(session, event)
                self.session_store.append_upstream_event(session, event_record)
                self.trace_writer.emit(session.session_id, "upstream_success_received", "upstream_event", {})

                current_sql = session.latest_sql_candidate
                current_attempt_id = session.latest_sql_candidate_attempt_id
                if current_sql is None or current_attempt_id is None or event.sql != current_sql:
                    self.trace_writer.emit(session.session_id, "upstream_success_unmatched_candidate", "upstream_event", {})
                    self.session_store.finish_event(session, event_record, None, error_code="UNMATCHED_SUCCESS")
                    return self.external_results.success_ack(
                        "SUCCESS 未匹配当前候选，已记录但未确认历史 Attempt"
                    )
                matched_attempt = self.session_store.load_attempt(session, current_attempt_id)
                matched_attempt.status = AttemptStatus.UPSTREAM_CONFIRMED_SUCCESS
                matched_attempt.updated_at = utc_now_iso()
                session.status = SessionStatus.UPSTREAM_CONFIRMED_SUCCESS
                session.confirmed_sql = current_sql
                session.confirmed_attempt_id = current_attempt_id
                session.updated_at = utc_now_iso()
                self.session_store.save_attempt(session, matched_attempt)
                self.session_store.save_session(session)
                self.trace_writer.emit(session.session_id, "upstream_success_matched", "upstream_event", {"attempt_id": current_attempt_id}, current_attempt_id)
        except SessionLockTimeout:
            return self.external_results.human_required("Session 正在处理中，请稍后重试。")

        try:
            metadata = self._load_attempt_artifact(matched_attempt.metadata_snapshot_path, MetadataSnapshot)
            repair_plan = self._load_attempt_artifact(matched_attempt.repair_plan_path, RepairPlan)
            self.trace_writer.emit(session.session_id, "memory_write_started", "memory", {}, matched_attempt.attempt_id)
            experience_id = self.memory_writer.write_success_experience(
                session, matched_attempt, current_sql, metadata, repair_plan
            )
            self.trace_writer.emit(session.session_id, "memory_write_finished", "memory", {"experience_id": experience_id}, matched_attempt.attempt_id)
            with self.session_store.lock_for_task(event.id):
                session = self.session_store.load_for_task(event.id)
                if session is None:
                    return self.external_results.human_required("Session 丢失，无法完成 SUCCESS 事件。")
                record = self.session_store.find_event(session, event_key)
                if record is not None:
                    self.session_store.finish_event(session, record, None)
            return self.external_results.success_ack("当前候选已被上游确认成功")
        except Exception as error:
            self.trace_writer.emit(
                session.session_id,
                "stage_failed",
                "memory",
                {"error_type": type(error).__name__},
                matched_attempt.attempt_id,
            )
            return self.external_results.human_required(
                "上游成功已确认，但成功经验写入失败，请人工检查存储。"
            )
