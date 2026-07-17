import json
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ConfigDict
from uuid import uuid4

from sql_self_healing_agent.agent.context import ContextManager
from sql_self_healing_agent.agent.config import AgentConfig
from sql_self_healing_agent.agent.gates.gate_models import GateRequest
from sql_self_healing_agent.agent.gates.gate_runner import GateRunner
from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.context import AgentContext, WorkspaceValue
from sql_self_healing_agent.agent.models.observation import Observation
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState
from sql_self_healing_agent.agent.runner.agent_runner import AgentRunner
from sql_self_healing_agent.agent.runner.agent_result import AgentRunResult
from sql_self_healing_agent.agent.tools.tool_registry import ToolRegistry
from sql_self_healing_agent.agent.models.subagent_models import SubAgentRequest, SubAgentResult
from sql_self_healing_agent.agent.runner.subagent_runner import SubAgentRunner
from sql_self_healing_agent.core.enums import DiagnosedErrorType
from sql_self_healing_agent.core.time_utils import utc_now_iso
from sql_self_healing_agent.diagnostics.diagnosis_fusion import DiagnosisFusion
from sql_self_healing_agent.diagnostics.diagnosis_models import DiagnosisInput, LLMDiagnosisResult
from sql_self_healing_agent.diagnostics.rule_classifier import RuleClassifier
from sql_self_healing_agent.logs.log_compressor import LogCompressor
from sql_self_healing_agent.memory.memory_retriever import MemoryRetriever
from sql_self_healing_agent.metadata.metadata_models import MetadataSnapshot
from sql_self_healing_agent.metadata.mock_metadata_provider import MockMetadataProvider
from sql_self_healing_agent.metadata.sql_table_extractor import SQLTableExtractor
from sql_self_healing_agent.repair.repair_models import RepairPlannerInput, SQLGeneratorInput
from sql_self_healing_agent.repair.repair_planner import RepairPlanner
from sql_self_healing_agent.repair.sql_generator import SQLGenerator


class DeterministicMainAgent:
    """Chooses the next action from evidence availability; it does not run the workflow itself."""

    def next_action(self, context: AgentContext, run_state: AgentRunState) -> AgentAction:
        if "log_digest" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="build_log_digest", tool_input={})
        if "diagnosis" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="diagnose", tool_input={})
        diagnosis = context.workspace.get("diagnosis")
        if (
            diagnosis is not None
            and "UNKNOWN" in (diagnosis.summary or "")
            and "subagent_diagnosis" not in context.workspace
            and run_state.sub_agent_call_count == 0
        ):
            return AgentAction(
                type="RUN_SUB_AGENT",
                sub_agent_request=SubAgentRequest(
                    task_name="diagnose_sql_error",
                    objective="仅基于现有受控证据补充诊断建议，不生成或提交 SQL",
                    context_refs=[],
                    allowed_tools=[],
                    expected_output_schema="DiagnosisResult",
                ),
            )
        if "metadata_snapshot" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="query_metadata", tool_input={})
        if "memory_retrieval" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="retrieve_memory", tool_input={})
        if "repair_plan" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="build_repair_plan", tool_input={})
        if "candidate_sql" not in context.workspace:
            return AgentAction(type="TOOL_CALL", tool_name="generate_candidate", tool_input={})
        candidate = context.workspace["candidate_sql"].summary
        if not candidate:
            return AgentAction(type="RETURN_HUMAN_REQUIRED", reason="无法安全生成候选 SQL。")
        return AgentAction(type="PROPOSE_SQL_CANDIDATE", candidate_sql=candidate)


@dataclass
class ProcessorDependencies:
    keyword_vocab: dict[str, list[str]]
    metadata_provider: MockMetadataProvider
    memory_retriever: MemoryRetriever
    repair_planner: RepairPlanner
    sql_generator: SQLGenerator
    llm_diagnoser: object | None = None
    evaluator: object | None = None
    allow_medium_risk: bool = False


class InternalToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InternalToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    artifact_refs: list[str]


class InternalBusinessTool:
    description = "受控复用现有业务能力"
    input_model = InternalToolInput
    output_model = InternalToolOutput
    allowed_phases = {"INIT", "DIAGNOSING", "PLANNING", "GENERATING"}
    max_output_tokens = 2000
    produces_artifact = True

    def __init__(self, name: str, handler: Callable[[AgentContext], list[str]]) -> None:
        self.name = name
        self.handler = handler

    def run(self, context: AgentContext, input_data: InternalToolInput) -> InternalToolOutput:
        keys = self.handler(context)
        refs = [context.workspace[key].artifact_ref for key in keys if context.workspace[key].artifact_ref]
        return InternalToolOutput(summary=",".join(keys), artifact_refs=refs)


class AgenticActionExecutor:
    TOOL_NAMES = {
        "build_log_digest": "ReadLogTool",
        "diagnose": "DiagnoseTool",
        "query_metadata": "MetadataQueryTool",
        "retrieve_memory": "MemoryRetrieveTool",
        "build_repair_plan": "BuildRepairPlanTool",
        "generate_candidate": "GenerateCandidateTool",
    }

    def __init__(self, dependencies: ProcessorDependencies, event, artifact_store, hook_manager=None) -> None:
        self.deps = dependencies
        self.event = event
        self.artifact_store = artifact_store
        self.hook_manager = hook_manager
        self.log_compressor = LogCompressor()
        self.rule_classifier = RuleClassifier()
        self.fusion = DiagnosisFusion()
        self.table_extractor = SQLTableExtractor()
        self.objects: dict[str, object] = {}
        self.tool_registry = ToolRegistry()
        handlers = {
            "build_log_digest": self._log,
            "diagnose": self._diagnose,
            "query_metadata": self._metadata,
            "retrieve_memory": self._memory,
            "build_repair_plan": self._plan,
            "generate_candidate": self._generate,
        }
        for action_name, handler in handlers.items():
            self.tool_registry.register(InternalBusinessTool(self.TOOL_NAMES[action_name], handler))

    def execute(self, action: AgentAction, context: AgentContext, run_state: AgentRunState) -> Observation:
        if action.type == "PROPOSE_SQL_CANDIDATE":
            summary = action.candidate_sql or ""
            return self._observation(action, "SUCCEEDED", summary, [])
        if action.type == "RUN_SUB_AGENT" and action.sub_agent_request is not None:
            runner = SubAgentRunner(
                lambda request, view: SubAgentResult(
                    status="SUCCEEDED",
                    summary="SubAgent 已检查受控证据；结果仅作为诊断建议。",
                    structured_output={"task_name": request.task_name},
                )
            )
            result = (
                self.hook_manager.execute_sub_agent(
                    lambda: runner.run(action.sub_agent_request, context),
                    session_id=context.session_id,
                    attempt_id=context.attempt_id,
                    purpose=action.sub_agent_request.task_name,
                    input_summary="restricted context refs only",
                )
                if self.hook_manager is not None
                else runner.run(action.sub_agent_request, context)
            )
            context.workspace["subagent_diagnosis"] = WorkspaceValue(
                status="AVAILABLE" if result.status == "SUCCEEDED" else "FAILED",
                summary=result.summary,
                artifact_ref=result.artifact_ref,
                updated_at=utc_now_iso(),
            )
            return self._observation(action, result.status, result.summary, ["subagent_diagnosis"])
        if action.type != "TOOL_CALL" or not action.tool_name:
            return self._observation(action, "BLOCKED", "unsupported action", [])
        registry_name = self.TOOL_NAMES.get(action.tool_name)
        if registry_name is None:
            return self._observation(action, "BLOCKED", "unregistered internal action", [])
        result = (
            self.hook_manager.execute_tool_call(
                lambda: self.tool_registry.execute(registry_name, context, action.tool_input or {}, run_state),
                session_id=context.session_id,
                attempt_id=context.attempt_id,
                purpose=registry_name,
                input_summary=registry_name,
            )
            if self.hook_manager is not None
            else self.tool_registry.execute(registry_name, context, action.tool_input or {}, run_state)
        )
        keys = [key for key in context.workspace if key in self.objects or key == "candidate_sql"]
        return self._observation(action, result.status, result.summary or registry_name, keys)

    def _save(self, context: AgentContext, key: str, value, artifact_type: str) -> str:
        ref = self.artifact_store.save_json_ref(context.session_id, context.attempt_id, f"{key}.json", value.model_dump(mode="json"), artifact_type)
        self.objects[key] = value
        context.workspace[key] = WorkspaceValue(status="AVAILABLE", summary=value.model_dump_json(), artifact_ref=ref.model_dump_json(), updated_at=utc_now_iso())
        return ref.model_dump_json()

    def _log(self, context):
        value = self.log_compressor.build_digest(self.event.log_path, self.event.error_message, self.deps.keyword_vocab)
        self._save(context, "log_digest", value, "LOG_DIGEST")
        return ["log_digest"]

    def _diagnose(self, context):
        log_digest = self.objects["log_digest"]
        value_input = DiagnosisInput(failed_sql=self.event.sql, error_message=self.event.error_message, log_digest=log_digest, keyword_vocab=self.deps.keyword_vocab, allowed_error_types=[item.value for item in DiagnosedErrorType])
        rule = self.rule_classifier.classify(value_input)
        llm = None
        if self.deps.llm_diagnoser is not None:
            try: llm = self.deps.llm_diagnoser.diagnose(value_input)
            except Exception: llm = None
        if llm is None:
            allowed = set(self.deps.keyword_vocab.get(rule.diagnosed_error_type.value, []))
            llm = LLMDiagnosisResult(diagnosed_error_type=rule.diagnosed_error_type, diagnosed_keywords=[item for item in rule.diagnosed_keywords if item in allowed], primary_evidence=rule.primary_evidence, root_cause_summary=log_digest.root_cause_summary or self.event.error_message or "No reliable root cause", confidence=rule.confidence, is_repairable=rule.diagnosed_error_type not in {DiagnosedErrorType.UNKNOWN, DiagnosedErrorType.PERMISSION_ERROR, DiagnosedErrorType.RESOURCE_EXHAUSTED, DiagnosedErrorType.INFRASTRUCTURE_ERROR})
        value = self.fusion.fuse(value_input, rule, llm)
        self._save(context, "diagnosis", value, "DIAGNOSIS")
        return ["diagnosis"]

    def _metadata(self, context):
        extraction=self.table_extractor.extract(self.event.sql); tables=[]; missing=[]; errors=[]
        for ref in extraction.tables:
            try:
                item=self.deps.metadata_provider.get_table_metadata(ref.normalized_name)
                missing.append(ref.normalized_name) if item is None else tables.append(item)
            except Exception as exc: errors.append(f"{ref.normalized_name}:{type(exc).__name__}")
        value=MetadataSnapshot(extraction_result=extraction,tables=tables,missing_tables=missing,provider_errors=errors,created_at=utc_now_iso())
        self._save(context,"metadata_snapshot",value,"METADATA_SNAPSHOT")
        return ["metadata_snapshot"]

    def _memory(self, context):
        diagnosis=self.objects["diagnosis"]
        value=self.deps.memory_retriever.retrieve_keywords(diagnosis.diagnosed_keywords,diagnosis.root_cause_summary)
        self._save(context,"memory_retrieval",value,"MEMORY_RETRIEVAL")
        return ["memory_retrieval"]

    def _plan(self, context):
        value=self.deps.repair_planner.plan(RepairPlannerInput(failed_sql=self.event.sql,diagnosis=self.objects["diagnosis"],log_digest=self.objects["log_digest"],metadata_snapshot=self.objects["metadata_snapshot"],memory_retrieval=self.objects["memory_retrieval"]))
        self._save(context,"repair_plan",value,"TRACE_PAYLOAD")
        return ["repair_plan"]

    def _generate(self, context):
        generation=self.deps.sql_generator.generate(SQLGeneratorInput(failed_sql=self.event.sql,repair_plan=self.objects["repair_plan"]))
        self.objects["generation"]=generation
        candidate=generation.sql_candidate if generation.generated else None
        ref=None
        if candidate:
            candidate_ref=self.artifact_store.save_text_ref(context.session_id,context.attempt_id,"candidate_v1.sql",candidate,"CANDIDATE_SQL")
            ref=candidate_ref.model_dump_json()
            context.candidate.draft_artifact_ref=ref
        context.workspace["candidate_sql"]=WorkspaceValue(status="AVAILABLE" if candidate else "FAILED",summary=candidate,artifact_ref=ref,updated_at=utc_now_iso())
        return ["candidate_sql"]

    @staticmethod
    def _observation(action,status,summary,keys):
        return Observation(observation_id=f"obs_{uuid4().hex}",action_type=action.type,status=status,summary=summary,produced_workspace_keys=keys,created_at=utc_now_iso())


class OnlineGateAdapter:
    def __init__(self, gate_runner: GateRunner, executor: AgenticActionExecutor, hook_manager=None) -> None:
        self.gate_runner=gate_runner; self.executor=executor; self.hook_manager=hook_manager
    def run(self, context: AgentContext, run_state: AgentRunState) -> AgentRunResult:
        request=GateRequest(original_sql=context.original_sql,candidate_sql=context.candidate.draft_sql or "",diagnosis=self.executor.objects["diagnosis"],metadata_snapshot=self.executor.objects.get("metadata_snapshot"),memory_retrieval=self.executor.objects.get("memory_retrieval"),existing_plan=self.executor.objects.get("repair_plan"),candidate_artifact_ref=context.candidate.draft_artifact_ref,attempt_id=context.attempt_id,event_key=context.event_key,allow_medium_risk=self.executor.deps.allow_medium_risk)
        first = (
            self.hook_manager.execute_gate(lambda: self.gate_runner.run(context, run_state, request), session_id=context.session_id, attempt_id=context.attempt_id, purpose="candidate_gate_v1", input_summary="candidate hash validated by GateRunner")
            if self.hook_manager is not None
            else self.gate_runner.run(context, run_state, request)
        )
        last = self.gate_runner.last_result
        regenerate = last is not None and any(item.code == "SEMANTIC_REGENERATE" for item in last.feedback)
        if not regenerate or run_state.gate_repair_rounds >= 1:
            return first
        instruction = next((item.message for item in last.feedback if item.code == "SEMANTIC_REGENERATE"), None)
        plan = self.executor.objects["repair_plan"]
        regenerated = self.executor.deps.sql_generator.generate(SQLGeneratorInput(failed_sql=self.executor.event.sql, repair_plan=plan), instruction)
        if not regenerated.generated or not regenerated.sql_candidate:
            return first
        candidate = regenerated.sql_candidate
        ref = self.executor.artifact_store.save_text_ref(context.session_id, context.attempt_id, "candidate_v2.sql", candidate, "CANDIDATE_SQL")
        context.candidate.draft_sql = candidate
        context.candidate.draft_artifact_ref = ref.model_dump_json()
        context.candidate.gate_feedback.clear()
        repaired_request = request.model_copy(update={"candidate_sql": candidate, "candidate_artifact_ref": ref.model_dump_json()})
        gate_result = (
            self.hook_manager.execute_gate(
                lambda: self.gate_runner.run_repair(repaired_request, candidate, run_state),
                session_id=context.session_id,
                attempt_id=context.attempt_id,
                purpose="candidate_gate_v2",
                input_summary="regenerated candidate hash validated by GateRunner",
            )
            if self.hook_manager is not None
            else self.gate_runner.run_repair(repaired_request, candidate, run_state)
        )
        if gate_result.decision == "PASS":
            context.candidate.formal_sql = candidate
            context.candidate.status = "READY"
            run_state.status = "SUCCEEDED"
            return AgentRunResult(status="CANDIDATE_READY", candidate_sql=candidate, candidate_artifact_ref=ref.model_dump_json(), risk_level=gate_result.risk_level, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)
        context.candidate.status = "GATE_REJECTED"
        run_state.status = "HUMAN_REQUIRED" if gate_result.decision == "HUMAN_REQUIRED" else "NO_SQL"
        return AgentRunResult(status="HUMAN_REQUIRED" if gate_result.decision == "HUMAN_REQUIRED" else "NO_SQL", risk_level=gate_result.risk_level, reason=gate_result.feedback[0].message if gate_result.feedback else gate_result.decision, plan_revision=context.execution_plan.revision, step_count=run_state.step_count)


class AgenticFailedEventProcessor:
    def __init__(self, dependencies: ProcessorDependencies, artifact_store, hook_manager=None, config: AgentConfig | None = None) -> None:
        self.dependencies=dependencies; self.artifact_store=artifact_store; self.hook_manager=hook_manager
        self.config = config or AgentConfig()
        self.context_manager = ContextManager(artifact_store, compaction_limits=self.config.compaction_limits)
    def run(self,event,session,attempt) -> tuple[AgentRunResult,AgentContext,AgentRunState,AgenticActionExecutor]:
        from sql_self_healing_agent.agent.models.execution_plan import build_initial_execution_plan
        context=AgentContext(session_id=session.session_id,attempt_id=attempt.attempt_id,event_key=attempt.source_event_key,original_sql=event.sql,error_message=event.error_message,log_path=event.log_path,execution_plan=build_initial_execution_plan())
        state=AgentRunState(started_at=utc_now_iso())
        limits=self.config.run_limits
        if self.hook_manager is not None:
            from sql_self_healing_agent.agent.hooks.budget_hook import BudgetHook
            budget_hooks = [hook for hook in self.hook_manager.hooks if isinstance(hook, BudgetHook)]
            if len(budget_hooks) != 1:
                raise RuntimeError("run-scoped HookManager must contain exactly one BudgetHook")
            budget_hooks[0].run_state = state
            budget_hooks[0].limits = limits
        executor=AgenticActionExecutor(self.dependencies,event,self.artifact_store,self.hook_manager)
        self.context_manager.compact_if_needed(context, state)
        self.context_manager.prepare_for_main_agent(
            context,
            state,
            available_tools=executor.tool_registry.list_available(context.phase),
            limits=limits,
        )
        from sql_self_healing_agent.agent.gates.semantic_pre_reflection_gate import SemanticPreReflectionGate
        gate_runner = GateRunner(semantic_gate=SemanticPreReflectionGate(self.dependencies.evaluator))
        executor.gate_runner = gate_runner
        result=AgentRunner(DeterministicMainAgent(),executor,OnlineGateAdapter(gate_runner,executor,self.hook_manager),limits).run(context,state)
        self.context_manager.compact_if_needed(context, state)
        for snapshot in self.context_manager.snapshots:
            self.artifact_store.save_json_ref(
                context.session_id,
                context.attempt_id,
                f"{snapshot.snapshot_id}.json",
                snapshot.model_dump(mode="json"),
                "CONTEXT_SNAPSHOT",
            )
        return result,context,state,executor
