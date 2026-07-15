# SQL Self-Healing Agent

SQL Self-Healing Agent 是一个事件驱动的 SQL 修复组件。上游系统负责执行 SQL、判定成功以及控制重试轮次；Agent 每次只处理一个 `UpstreamTaskEvent`，并返回一个 `AgentExternalResult`。Agent 本身不会执行 SQL，也不会把 `SQL_READY` 当作修复成功；真正成功只能由后续 `SUCCESS` 事件确认。

当前已完成 **M1 骨架与事件入口**、**M2 单候选生成** 和 **M3 Mock 上游闭环**。

## 当前能力

当前已支持：

- 唯一外部入口：`UpstreamTaskEvent → handle_upstream_event → AgentExternalResult`
- FAILED/SUCCESS 事件持久化与幂等处理
- 日志有界读取、脱敏、证据提取和结构化摘要
- 规则诊断、结构化 LLM 诊断及固定 `keyword_vocab`
- `MockMetadataProvider` 元数据查询
- 结构化 `RepairPlan`
- SQLGenerator 按 RepairPlan 做最小修改
- 基于 sqlglot 和保守 fallback 的 SQL Diff
- Validation 硬门禁和 PreReflection
- 同一 Attempt 最多一次受控 `REGENERATE`
- Ollama 和 Ark/OpenAI 两种 LLM Provider
- Session、Attempt、Trace 和 Artifact 本地持久化
- 所有终止结果的重复事件一致返回
- Mock 上游控制的多轮 FAILED → SQL_READY → SUCCESS 闭环
- 上一候选真实失败后的 PostReflection 和错误振荡检测
- SUCCESS SQL 通过 SQLMatcher 命中历史候选后确认成功
- 仅在匹配 SUCCESS 后写入最小 Experience，按 Session + Attempt 幂等
- Memory 写入失败保持结构化外部响应，重复 SUCCESS 可补齐幂等写入
- 错误振荡在所有诊断类型进入 Planner 前统一转人工

Validation 会阻断危险语句、写类型引入、WHERE 弱化、JOIN 条件变化、GROUP BY 粒度变化、INSERT 目标或静态分区变化，以及 RepairPlan 之外的修改。写类型 SQL 无法可靠确认时 fail-closed。

## 环境要求

- Python 3.10+
- Pydantic 2.x
- sqlglot
- OpenAI SDK（Ark Provider）
- Ollama CLI（仅使用 Ollama Provider 时需要，需自行安装并准备本地模型）

安装：

```bash
cd /Users/bytedance/IdeaProjects/sql-self-healing-agent
python3.12 -m pip install -e .
sql-heal --help
```

## LLM Provider 配置

CLI 默认使用 Ollama。环境变量由当前 Shell 读取，项目不会自动加载 `.env` 文件。

### Ollama

```bash
export SQL_HEAL_LLM_PROVIDER=ollama
# 可选；未设置时使用项目默认模型
export SQL_HEAL_OLLAMA_MODEL='<本地已存在的模型名>'
```

确认模型已存在后，可通过 `ollama list` 查看模型名称。Agent 使用 Ollama CLI 获取严格的结构化 JSON 输出，不使用 Ollama HTTP API。

### Ark/OpenAI

```bash
export SQL_HEAL_LLM_PROVIDER=ark
export ARK_API_KEY='<你的 API Key>'
# 可选覆盖
export ARK_BASE_URL='<Ark API Base URL>'
export ARK_MODEL='<Ark Endpoint 或模型 ID>'
```

也可以复制 `.env.example` 为 `.env`，填写配置后再显式加载：

```bash
cp .env.example .env
source .env
```

不要提交真实 API Key；`.env` 已被 Git 忽略。

## 处理上游事件

示例 FAILED 事件位于：

```text
mocks/events/task_123_failed_round_1.json
```

执行：

```bash
sql-heal handle-upstream-event \
  --event mocks/events/task_123_failed_round_1.json
```

通过 Validation 和 PreReflection 时，返回示例：

```json
{
  "status": "SQL_READY",
  "sql": "SELECT user_id, payment_amount FROM dwd_order_detail WHERE date = ",
  "message": null
}
```

`SQL_READY` 只表示候选 SQL 可以交给上游重跑，不表示执行成功。候选被安全门禁阻断时返回 `NO_SQL`；缺乏可靠自动修复依据时返回 `HUMAN_REQUIRED`；收到上游成功事件时返回 `SUCCESS_ACK`。

重复提交同一事件不会创建新的 Attempt、重复追加 Trace，且会返回首次持久化的外部结果。

## Mock 上游闭环

两轮失败后成功：

```bash
sql-heal mock-upstream-event-run \
  --scenario mocks/scenarios/two_step_column_then_type.json
```

预期关键输出：

```text
Mock upstream event run finished
status: MOCK_SUCCESS
task_id: task_two_step_column_then_type
attempt_count: 2
memory_written: true
```

该场景第一轮将 `pay_amt` 替换为 `payment_amount`；Mock 上游重跑后反馈类型错误，第二轮触发 PostReflection 并生成 `CAST(payment_amount AS BIGINT)`；Mock 上游确认成功后推送 SUCCESS，Agent 匹配历史候选并写入 Experience。

三轮均失败：

```bash
sql-heal mock-upstream-event-run \
  --scenario mocks/scenarios/retry_exhausted_manual_required.json
```

预期关键输出：

```text
status: MOCK_RETRY_EXHAUSTED
attempt_count: 3
memory_written: false
```

循环只存在于 Mock 上游 Runner；Agent 每次仍只处理一个事件，不执行 SQL、不控制上游重试次数。

## 本地运行数据

每个任务的运行数据保存在：

```text
sessions/{session_id}/
├── session.json
├── attempts/
│   └── attempt_001.json
├── artifacts/
│   └── attempt_001/
└── trace.jsonl
```

M2/M3 主要 Artifact 包括：

```text
upstream_event.json
log_digest.json
diagnosis.json
metadata_snapshot.json
memory_retrieval.json
repair_plan.json
sql_generation_result.json
sql_candidate.sql
sql_diff_summary.json
validation_result.json
pre_reflection_result.json
post_reflection_result.json
external_result.json
```

所有 JSON/Text Artifact 使用临时文件、`fsync` 和 `os.replace` 原子写入；`trace.jsonl` 为 append-only。

## 测试与验收

```bash
cd /Users/bytedance/IdeaProjects/sql-self-healing-agent
python3.12 -m compileall -q sql_self_healing_agent
python3.12 -m unittest discover -v
git diff --check
```

M3 验收基线：

```text
Ran 64 tests
OK
```

测试使用 Fake/Stub/Mock Provider，不会调用真实 Ollama、Ark 或执行真实 SQL。

## 当前边界

以下内容尚未实现，属于 M4 或 MVP 范围外：

- Memory keyword/fingerprint 双索引与检索复用
- `memory list` 和 `memory consolidate`
- Consolidation Proposal 与索引原子重建
- `inspect` CLI 的完整实现
- 真实元数据 API 和真实 SQL 执行

同一 Attempt 内最多一次 `REGENERATE` 是 M2 的候选生成机制，不是 Agent 自主管理的上游重试循环。

## 已知说明

Validation 或 PreReflection 返回 `NO_SQL` 后，Attempt 会进入对应 blocked 状态；Session 当前保持 `RUNNING`。设计中的 `SessionStatus` 没有 `NO_SQL`/blocked 终态，因此在设计明确前不新增枚举。
