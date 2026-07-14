import json
from pathlib import Path

from sql_self_healing_agent.artifacts.artifact_store import ArtifactStore
from sql_self_healing_agent.core.enums import AttemptStatus, DiagnosedErrorType, SessionStatus
from sql_self_healing_agent.core.models import AgentExternalResult, UpstreamTaskEvent
from sql_self_healing_agent.core.sql_matcher import SQLMatcher
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.diagnostics.diagnosis_fusion import DiagnosisFusion
from sql_self_healing_agent.diagnostics.llm_diagnoser import LLMDiagnoser
from sql_self_healing_agent.llm.llm_client import LLMClient, LLMClientError
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisHistoryItem, DiagnosisInput
from sql_self_healing_agent.diagnostics.rule_classifier import RuleClassifier
from sql_self_healing_agent.logs.log_compressor import LogCompressor
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.metadata.sql_table_extractor import SQLTableExtractor
from sql_self_healing_agent.repair.evaluator import RepairEvaluator
from sql_self_healing_agent.repair.reflection import PreReflectionDecision, PreReflectionInput
from sql_self_healing_agent.repair.repair_models import RepairPlannerInput, SQLGeneratorInput
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.repair.sql_generator import SQLGenerator, build_diff
from sql_self_healing_agent.repair.validator import Validator
from sql_self_healing_agent.session.session_models import RepairSession
from sql_self_healing_agent.session.session_store import SessionStore
from sql_self_healing_agent.trace.trace_writer import TraceWriter


class RepairAgentService:
    def __init__(self, sessions_dir: Path | str = Path("sessions"), llm_client: LLMClient | None = None, metadata_path: Path | str = Path("mocks/metadata/tables.json"), keyword_vocab_path: Path | str | None = None, allow_medium_risk: bool = False) -> None:
        self.session_store = SessionStore(sessions_dir)
        self.trace_writer = TraceWriter(sessions_dir)
        self.artifact_store = ArtifactStore(sessions_dir)
        self.sql_matcher = SQLMatcher()
        self.metadata_provider = MockMetadataProvider(metadata_path)
        default_vocab = Path(__file__).parents[1] / "logs" / "keyword_vocab.json"
        self.keyword_vocab = json.loads(Path(keyword_vocab_path or default_vocab).read_text(encoding="utf-8"))
        self.log_compressor = LogCompressor()
        self.rule_classifier = RuleClassifier()
        self.diagnosis_fusion = DiagnosisFusion()
        self.llm_client = llm_client
        self.llm_diagnoser = LLMDiagnoser(self.llm_client) if self.llm_client is not None else None
        self.table_extractor = SQLTableExtractor()
        self.memory_retriever = MemoryRetriever()
        self.repair_planner = RepairPlanner(self.metadata_provider)
        self.sql_generator = SQLGenerator(self.llm_client)
        self.validator = Validator(allow_medium_risk=allow_medium_risk)
        self.evaluator = RepairEvaluator(self.llm_client)

    def handle_upstream_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        if event.status == "FAILED":
            return self._handle_failed_event(event)
        if event.status == "SUCCESS":
            return self._handle_success_event(event)
        return AgentExternalResult(status="NO_SQL", message=f"Unsupported upstream event status: {event.status}")

    def _is_duplicate_failed_event(self, session: RepairSession, event: UpstreamTaskEvent) -> bool:
        return any(record.task_id == event.id and record.status == event.status and self.sql_matcher.match(record.sql, event.sql) and record.log_path == event.log_path for record in session.upstream_events)

    def _is_duplicate_success_event(self, session: RepairSession, event: UpstreamTaskEvent) -> bool:
        return any(record.task_id == event.id and record.status == event.status and self.sql_matcher.match(record.sql, event.sql) for record in session.upstream_events)

    def _handle_failed_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        session = self.session_store.load_or_create_for_event(event)
        if self._is_duplicate_failed_event(session, event):
            if session.latest_sql_candidate:
                return AgentExternalResult(status="SQL_READY", sql=session.latest_sql_candidate)
            return AgentExternalResult(status="HUMAN_REQUIRED", message="该失败事件已处理，但没有安全候选 SQL。")
        event_record = self.session_store.create_event_record(event)
        self.session_store.append_upstream_event(session, event_record)
        self.trace_writer.emit(session.session_id, "upstream_event_received", "upstream_event", {"status": event.status})
        session.status = SessionStatus.RUNNING
        session.updated_at = utc_now_iso()
        self.session_store.save_session(session)
        attempt = self.session_store.create_attempt(session, event_record)
        self.artifact_store.save_json(session.session_id, attempt.attempt_id, "upstream_event.json", event_record.model_dump(mode="json"))
        self.trace_writer.emit(session.session_id, "attempt_created", "orchestrator", {"attempt_no": attempt.attempt_no}, attempt.attempt_id)
        try:
            if not event.log_path and not event.error_message:
                return self._human(session, attempt, "缺少可用于诊断的日志和错误信息。")
            self.trace_writer.emit(session.session_id, "log_digest_started", "log_digest", {}, attempt.attempt_id)
            log_digest = self.log_compressor.build_digest(event.log_path, event.error_message, self.keyword_vocab)
            attempt.log_digest_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "log_digest.json", log_digest.model_dump(mode="json"))
            self.trace_writer.emit(session.session_id, "log_digest_finished", "log_digest", {"log_readable": log_digest.log_readable}, attempt.attempt_id)

            diagnosis_input = DiagnosisInput(failed_sql=event.sql, error_message=event.error_message, log_digest=log_digest, keyword_vocab=self.keyword_vocab, allowed_error_types=[item.value for item in DiagnosedErrorType], diagnosis_history=session.diagnosis_history)
            self.trace_writer.emit(session.session_id, "diagnosis_started", "diagnosis", {}, attempt.attempt_id)
            rule = self.rule_classifier.classify(diagnosis_input)
            from sql_self_healing_agent.diagnostics.diagnosis_models import LLMDiagnosisResult
            allowed_keywords = set(self.keyword_vocab.get(rule.diagnosed_error_type.value, []))
            llm = None
            if self.llm_diagnoser is not None:
                try:
                    llm = self.llm_diagnoser.diagnose(diagnosis_input) if self.llm_diagnoser is not None else None
                except LLMClientError:
                    llm = None
            if llm is None:
                llm = LLMDiagnosisResult(
                    diagnosed_error_type=rule.diagnosed_error_type,
                    diagnosed_keywords=[item for item in rule.diagnosed_keywords if item in allowed_keywords],
                    primary_evidence=rule.primary_evidence,
                    root_cause_summary=log_digest.root_cause_summary or event.error_message or "No reliable root cause",
                    confidence=rule.confidence,
                    is_repairable=rule.diagnosed_error_type not in {DiagnosedErrorType.UNKNOWN, DiagnosedErrorType.PERMISSION_ERROR, DiagnosedErrorType.RESOURCE_EXHAUSTED, DiagnosedErrorType.INFRASTRUCTURE_ERROR},
                    manual_repair_reason=None,
                )
            diagnosis = self.diagnosis_fusion.fuse(diagnosis_input, rule, llm)
            attempt.diagnosis_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "diagnosis.json", diagnosis.model_dump(mode="json"))
            attempt.diagnosed_error_type = diagnosis.diagnosed_error_type.value
            attempt.diagnosed_keywords = diagnosis.diagnosed_keywords
            attempt.error_fingerprint = diagnosis.error_fingerprint
            attempt.status = AttemptStatus.DIAGNOSED
            attempt.updated_at = utc_now_iso()
            session.diagnosis_history.append(DiagnosisHistoryItem(attempt_id=attempt.attempt_id, diagnosed_error_type=diagnosis.diagnosed_error_type, diagnosed_keywords=diagnosis.diagnosed_keywords, error_fingerprint=diagnosis.error_fingerprint, primary_entity=diagnosis.primary_entity, confidence=diagnosis.confidence, created_at=utc_now_iso()))
            self.session_store.save_attempt(session, attempt)
            self.session_store.save_session(session)
            self.trace_writer.emit(session.session_id, "diagnosis_finished", "diagnosis", {"diagnosed_error_type": diagnosis.diagnosed_error_type.value, "confidence": diagnosis.confidence}, attempt.attempt_id)

            extraction = self.table_extractor.extract(event.sql)
            tables, missing, provider_errors = [], [], []
            for table_ref in extraction.tables:
                try:
                    metadata = self.metadata_provider.get_table_metadata(table_ref.normalized_name)
                    if metadata is None:
                        missing.append(table_ref.normalized_name)
                    else:
                        tables.append(metadata)
                except Exception as error:
                    provider_errors.append(f"{table_ref.normalized_name}: {error}")
            snapshot = MetadataSnapshot(extraction_result=extraction, tables=tables, missing_tables=missing, provider_errors=provider_errors, created_at=utc_now_iso())
            attempt.metadata_snapshot_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "metadata_snapshot.json", snapshot.model_dump(mode="json"))
            memory = self.memory_retriever.retrieve(diagnosis, event.sql, snapshot)
            attempt.memory_retrieval_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "memory_retrieval.json", memory.model_dump(mode="json"))

            self.trace_writer.emit(session.session_id, "repair_plan_started", "repair_plan", {}, attempt.attempt_id)
            plan = self.repair_planner.plan(RepairPlannerInput(failed_sql=event.sql, diagnosis=diagnosis, log_digest=log_digest, metadata_snapshot=snapshot, memory_retrieval=memory))
            attempt.repair_plan_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "repair_plan.json", plan.model_dump(mode="json"))
            attempt.status = AttemptStatus.PLANNED
            self.session_store.save_attempt(session, attempt)
            self.trace_writer.emit(session.session_id, "repair_plan_finished", "repair_plan", {"repairable": plan.repairable}, attempt.attempt_id)
            if not plan.repairable:
                return self._human(session, attempt, plan.manual_repair_recommendation or "没有安全修复计划。")

            generation = self.sql_generator.generate(SQLGeneratorInput(failed_sql=event.sql, repair_plan=plan))
            self.artifact_store.save_json(session.session_id, attempt.attempt_id, "sql_generation_result.json", generation.model_dump(mode="json"))
            if not generation.generated or not generation.sql_candidate:
                return self._human(session, attempt, generation.reason or "无法安全生成候选 SQL。")
            attempt.sql_candidate = generation.sql_candidate
            attempt.sql_candidate_path = self.artifact_store.save_text(session.session_id, attempt.attempt_id, "sql_candidate.sql", generation.sql_candidate)
            attempt.status = AttemptStatus.GENERATED
            diff = build_diff(event.sql, generation)
            self.artifact_store.save_json(session.session_id, attempt.attempt_id, "sql_diff_summary.json", diff.model_dump(mode="json"))

            validation = self.validator.validate(event.sql, generation.sql_candidate, plan, diff)
            attempt.validation_result_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "validation_result.json", validation.model_dump(mode="json"))
            if not validation.allow_return_sql:
                attempt.status = AttemptStatus.VALIDATION_BLOCKED
                self.session_store.save_attempt(session, attempt)
                return AgentExternalResult(status="NO_SQL", message=validation.reason or "候选 SQL 被 Validation 阻断。")

            reflection = self.evaluator.pre_reflect(PreReflectionInput(failed_sql=event.sql, sql_candidate=generation.sql_candidate, diagnosis=diagnosis, repair_plan=plan, validation_result=validation, sql_diff_summary=diff, metadata_snapshot=snapshot, memory_retrieval=memory))
            attempt.pre_reflection_result_path = self.artifact_store.save_json(session.session_id, attempt.attempt_id, "pre_reflection_result.json", reflection.model_dump(mode="json"))
            if reflection.decision is not PreReflectionDecision.RETURN_SQL:
                attempt.status = AttemptStatus.REFLECTION_BLOCKED
                self.session_store.save_attempt(session, attempt)
                return AgentExternalResult(status="NO_SQL", message="候选 SQL 未通过 PreReflection。")
            attempt.status = AttemptStatus.SQL_READY
            attempt.updated_at = utc_now_iso()
            session.status = SessionStatus.SQL_READY_PENDING_UPSTREAM
            session.latest_sql_candidate = generation.sql_candidate
            session.latest_sql_candidate_attempt_id = attempt.attempt_id
            session.updated_at = utc_now_iso()
            self.session_store.save_attempt(session, attempt)
            self.session_store.save_session(session)
            self.trace_writer.emit(session.session_id, "sql_ready_returned", "orchestrator", {}, attempt.attempt_id)
            return AgentExternalResult(status="SQL_READY", sql=generation.sql_candidate)
        except Exception as error:
            attempt.status = AttemptStatus.SYSTEM_ERROR
            attempt.updated_at = utc_now_iso()
            session.status = SessionStatus.SYSTEM_ERROR
            session.updated_at = utc_now_iso()
            self.session_store.save_attempt(session, attempt)
            self.session_store.save_session(session)
            self.trace_writer.emit(session.session_id, "system_error", "orchestrator", {"error": str(error)}, attempt.attempt_id)
            return AgentExternalResult(status="HUMAN_REQUIRED", message="Agent 内部处理失败，请人工介入。")

    def _human(self, session: RepairSession, attempt, message: str) -> AgentExternalResult:
        attempt.status = AttemptStatus.HUMAN_REQUIRED
        attempt.updated_at = utc_now_iso()
        session.status = SessionStatus.HUMAN_REQUIRED
        session.updated_at = utc_now_iso()
        self.session_store.save_attempt(session, attempt)
        self.session_store.save_session(session)
        self.trace_writer.emit(session.session_id, "human_required_returned", "orchestrator", {"reason": message}, attempt.attempt_id)
        return AgentExternalResult(status="HUMAN_REQUIRED", message=message)

    def _handle_success_event(self, event: UpstreamTaskEvent) -> AgentExternalResult:
        session = self.session_store.load_or_create_for_event(event)
        if not self._is_duplicate_success_event(session, event):
            event_record = self.session_store.create_event_record(event)
            self.session_store.append_upstream_event(session, event_record)
            self.trace_writer.emit(session.session_id, "upstream_success_received", "upstream_event", {})
        return AgentExternalResult(status="SUCCESS_ACK")
