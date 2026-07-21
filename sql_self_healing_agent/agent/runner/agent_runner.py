import time
import uuid
from typing import Protocol

from pydantic import ValidationError

from sql_self_healing_agent.agent.models.action import AgentAction
from sql_self_healing_agent.agent.models.context import AgentContext
from sql_self_healing_agent.agent.models.observation import Observation
from sql_self_healing_agent.agent.models.run_state import AgentRunLimits, AgentRunState
from sql_self_healing_agent.agent.planning.progress_detector import ProgressDetector
from sql_self_healing_agent.agent.planning.execution_plan_updater import ExecutionPlanUpdater
from sql_self_healing_agent.agent.planning.execution_plan_validator import InvalidExecutionPlan
from sql_self_healing_agent.agent.runner.agent_result import AgentRunResult
from sql_self_healing_agent.core.time_utils import utc_now_iso


class MainAgent(Protocol):
    def next_action(self, context: AgentContext, run_state: AgentRunState) -> AgentAction: ...


class ActionExecutor(Protocol):
    def execute(self, action: AgentAction, context: AgentContext, run_state: AgentRunState) -> Observation: ...


class CandidateGate(Protocol):
    def run(self, context: AgentContext, run_state: AgentRunState) -> AgentRunResult: ...


class AgentRunner:
    def __init__(self, main_agent: MainAgent, action_executor: ActionExecutor, candidate_gate: CandidateGate, limits: AgentRunLimits | None = None, context_manager=None) -> None:
        self.main_agent = main_agent
        self.action_executor = action_executor
        self.candidate_gate = candidate_gate
        self.limits = limits or AgentRunLimits()
        self.context_manager = context_manager
        self.plan_updater = ExecutionPlanUpdater()

    def run(self, context: AgentContext, run_state: AgentRunState) -> AgentRunResult:
        started = time.monotonic()
        while run_state.status == "RUNNING":
            run_state.wall_time_ms = int((time.monotonic() - started) * 1000)
            stop_reason = self._stop_reason(run_state)
            if stop_reason:
                return self._stop(run_state, context, stop_reason)
            try:
                if self.context_manager:
                    self.context_manager.compact_if_needed(context, run_state)
                main_input = self.context_manager.prepare_for_main_agent(context, run_state, limits=self.limits) if self.context_manager else context
                action = self.main_agent.next_action(main_input, run_state)
                if not isinstance(action, AgentAction):
                    action = AgentAction.model_validate(action)
            except (ValidationError, ValueError, TypeError):
                run_state.status = "HUMAN_REQUIRED"
                run_state.stop_reason = "INVALID_AGENT_ACTION"
                return self._result("HUMAN_REQUIRED", context, run_state, "INVALID_AGENT_ACTION")
            run_state.step_count += 1
            context.last_action = action
            if action.type == "RETURN_NO_SQL":
                run_state.status = "NO_SQL"
                return self._result("NO_SQL", context, run_state, action.reason)
            if action.type == "RETURN_HUMAN_REQUIRED":
                run_state.status = "HUMAN_REQUIRED"
                return self._result("HUMAN_REQUIRED", context, run_state, action.reason)
            if action.type == "UPDATE_PLAN":
                try:
                    context.execution_plan = self.plan_updater.replace(context.execution_plan, action.execution_plan, run_state, self.limits)
                except InvalidExecutionPlan as error:
                    rejection = Observation(
                        observation_id=f"obs_{uuid.uuid4().hex}", action_type="UPDATE_PLAN", status="BLOCKED",
                        summary=f"ExecutionPlan rejected: {error}", created_at=utc_now_iso(),
                    )
                    context.recent_observations.append(rejection)
                    run_state.no_progress_steps += 1
                    continue
                context.recent_observations.append(Observation(
                    observation_id=f"obs_{uuid.uuid4().hex}", action_type="UPDATE_PLAN", status="SUCCEEDED",
                    summary=f"ExecutionPlan revision={context.execution_plan.revision}", created_at=utc_now_iso(),
                ))
                run_state.no_progress_steps = 0
                continue
            try:
                observation = self.action_executor.execute(action, context, run_state)
            except Exception:
                run_state.status = "FAILED"
                run_state.stop_reason = "AGENT_RUNTIME_ERROR"
                return self._result("FAILED", context, run_state, "AGENT_RUNTIME_ERROR")
            if action.type in {"TOOL_CALL", "RUN_SUB_AGENT"}:
                observation = observation.model_copy(update={"plan_step_id": context.execution_plan.current_step_id})
            progress = ProgressDetector.made_progress(observation, context.recent_observations)
            context.recent_observations.append(observation)
            context.last_action = action
            run_state.no_progress_steps = 0 if progress else run_state.no_progress_steps + 1
            self.plan_updater.apply_observation(context.execution_plan, observation)
            if action.type == "TOOL_CALL":
                run_state.tool_call_count += 1
            elif action.type == "RUN_SUB_AGENT":
                run_state.sub_agent_call_count += 1
            if action.type == "PROPOSE_SQL_CANDIDATE":
                workspace_candidate = context.workspace.get("candidate_sql")
                if (
                    workspace_candidate is None
                    or workspace_candidate.status != "AVAILABLE"
                    or not workspace_candidate.summary
                    or workspace_candidate.summary != action.candidate_sql
                    or not workspace_candidate.artifact_ref
                    or context.candidate.draft_artifact_ref != workspace_candidate.artifact_ref
                ):
                    run_state.status = "HUMAN_REQUIRED"
                    run_state.stop_reason = "NO_CANDIDATE_ARTIFACT"
                    return self._result("HUMAN_REQUIRED", context, run_state, "NO_CANDIDATE_ARTIFACT")
                context.candidate.draft_sql = workspace_candidate.summary
                context.candidate.status = "DRAFT"
                context.phase = "GATING"
                return self.candidate_gate.run(context, run_state)
        return self._result("FAILED", context, run_state, run_state.stop_reason)

    def _stop_reason(self, state: AgentRunState) -> str | None:
        limits = self.limits
        checks = (
            (state.step_count >= limits.max_steps, "MAX_STEPS"),
            (state.tool_call_count >= limits.max_tool_calls, "MAX_TOOL_CALLS"),
            (state.sub_agent_call_count >= limits.max_sub_agent_calls, "MAX_SUB_AGENT_CALLS"),
            (state.wall_time_ms >= limits.max_wall_time_ms, "MAX_WALL_TIME"),
            (state.plan_revision_count >= limits.max_plan_revisions, "MAX_PLAN_REVISIONS"),
            (state.gate_repair_rounds > limits.max_gate_repair_rounds, "MAX_GATE_REPAIR_ROUNDS"),
            (state.no_progress_steps >= limits.max_no_progress_steps, "NO_PROGRESS"),
        )
        return next((reason for reached, reason in checks if reached), None)

    def _stop(self, state: AgentRunState, context: AgentContext, reason: str) -> AgentRunResult:
        state.status = "HUMAN_REQUIRED"
        state.stop_reason = reason
        return self._result("HUMAN_REQUIRED", context, state, reason)

    @staticmethod
    def _result(status: str, context: AgentContext, state: AgentRunState, reason: str | None) -> AgentRunResult:
        return AgentRunResult(
            status=status,
            candidate_sql=context.candidate.formal_sql if status == "CANDIDATE_READY" else None,
            candidate_artifact_ref=context.candidate.draft_artifact_ref if status == "CANDIDATE_READY" else None,
            reason=reason,
            stop_reason=state.stop_reason,
            plan_revision=context.execution_plan.revision,
            step_count=state.step_count,
        )
