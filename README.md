# SQL Self-Healing Agent

An event-driven SQL repair component. The upstream system owns SQL execution,
success determination, and retry control. The Agent handles one
`UpstreamTaskEvent` per invocation and returns one `AgentExternalResult`.

## M1 quick start

```bash
python3 -m pip install -e .
sql-heal handle-upstream-event --event mocks/events/task_123_failed_round_1.json
```

M1 provides the project skeleton, protocol models, local Session/Attempt
persistence, Trace and Artifact stores, and a repair-service stub. Candidate
SQL generation starts in M2.
