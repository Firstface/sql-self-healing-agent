import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from sql_self_healing_agent.core.atomic_io import read_json
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.llm.llm_client import build_llm_client_from_env
from sql_self_healing_agent.mock_external_system.mock_upstream_event_executor import MockUpstreamEventExecutor
from sql_self_healing_agent.mock_external_system.mock_upstream_event_runner import MockUpstreamEventRunner
from sql_self_healing_agent.mock_external_system.mock_upstream_models import MockScenario
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService


def run_handle_upstream_event(event_path: str) -> None:
    event = UpstreamTaskEvent.model_validate(read_json(Path(event_path)))
    result = RepairAgentService(llm_client=build_llm_client_from_env()).handle_upstream_event(event)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


def run_mock_upstream_event(scenario_path: str) -> None:
    scenario = MockScenario.model_validate(read_json(Path(scenario_path)))
    experience_dir = Path("memory_store/experiences")
    before_experiences = (
        {path.name for path in experience_dir.glob("*.json")}
        if experience_dir.exists()
        else set()
    )
    service = RepairAgentService(
        llm_client=build_llm_client_from_env(),
        allow_medium_risk=scenario.allow_medium_risk,
    )
    result = MockUpstreamEventRunner(
        service,
        MockUpstreamEventExecutor(),
        max_retry_count=scenario.max_retry_count,
    ).run(scenario)
    print("Mock upstream event run finished")
    print(f"status: {result.status}")
    print(f"task_id: {result.task_id}")
    print(f"attempt_count: {result.attempt_count}")
    after_experiences = (
        {path.name for path in experience_dir.glob("*.json")}
        if experience_dir.exists()
        else set()
    )
    memory_written = bool(after_experiences - before_experiences)
    print(f"memory_written: {str(memory_written).lower()}")
    if result.message:
        print(f"message: {result.message}")


def _not_implemented(command: str) -> None:
    raise SystemExit(f"{command} is not implemented in M1.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sql-heal")
    subparsers = parser.add_subparsers(dest="command", required=True)

    event_parser = subparsers.add_parser("handle-upstream-event")
    event_parser.add_argument("--event", required=True)

    mock_parser = subparsers.add_parser("mock-upstream-event-run")
    mock_parser.add_argument("--scenario", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--session-id", required=True)
    inspect_parser.add_argument("--attempt-id", required=False)
    inspect_parser.add_argument("--show-artifacts", action="store_true")

    memory_parser = subparsers.add_parser("memory")
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_command", required=True
    )
    memory_list_parser = memory_subparsers.add_parser("list")
    memory_list_parser.add_argument("--error-type", required=False)
    memory_list_parser.add_argument("--keyword", required=False)
    memory_subparsers.add_parser("consolidate")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "handle-upstream-event":
            run_handle_upstream_event(args.event)
        elif args.command == "mock-upstream-event-run":
            run_mock_upstream_event(args.scenario)
        elif args.command == "inspect":
            _not_implemented("inspect")
        elif args.command == "memory":
            _not_implemented(f"memory {args.memory_command}")
    except (OSError, ValueError, ValidationError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
