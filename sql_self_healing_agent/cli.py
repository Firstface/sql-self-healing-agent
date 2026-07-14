import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from sql_self_healing_agent.core.atomic_io import read_json
from sql_self_healing_agent.core.models import UpstreamTaskEvent
from sql_self_healing_agent.orchestrator.repair_agent_service import RepairAgentService


def run_handle_upstream_event(event_path: str) -> None:
    event = UpstreamTaskEvent.model_validate(read_json(Path(event_path)))
    result = RepairAgentService().handle_upstream_event(event)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


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
            _not_implemented("mock-upstream-event-run")
        elif args.command == "inspect":
            _not_implemented("inspect")
        elif args.command == "memory":
            _not_implemented(f"memory {args.memory_command}")
    except (OSError, ValueError, ValidationError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
