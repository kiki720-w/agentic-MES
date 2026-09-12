# 第二十九步：事件驱动质量Agent验收

结论：质量Agent已能消费`OperationCompleted`制造事件，并自动生成去重、可追溯的R2质检建议草稿。自动化只扩展“观察与建议”，没有获得创建检验、隔离产品、批准返工或直接写生产状态的权限。

## 触发链路

```text
工序完工
  -> OperationCompleted写入Outbox
  -> 独立Worker安全领取事件
  -> 质量Agent重新读取当前工单、工序、设备和检验事实
  -> 已存在检验：跳过
  -> 尚无检验：创建CREATE_QUALITY_INSPECTION建议草稿
  -> AgentProposalCreated写回Outbox
  -> 原事件继续发布
```

持续消费命令：

```powershell
python -m autonomous_mes.worker --quality-agent-auto-draft --watch --poll-seconds 1
```

`--quality-agent-auto-draft`必须显式提供；普通Outbox Worker维持原行为，避免部署升级后未经授权自动启用主动Agent。

## 安全与一致性

- 只响应聚合类型为`WorkOrder`的`OperationCompleted`。
- 工序号必须是正整数，畸形事件进入现有重试/隔离流程，不静默执行。
- 处理事件时重新读取权威MES状态，不信任模型或事件携带的业务结论。
- 已有相同工单/工序检验时直接跳过，不生成过期建议。
- 草稿风险等级为`R2`，状态为`OBSERVED`，没有可自动执行的业务动作。
- 去重键稳定绑定`work_order_id + operation_sequence`，不随工单后续版本变化。
- 数据库唯一指纹与处理后重新读取共同防止重复投递和并发竞争产生重复草稿。
- `AgentProposalCreated.causation_id`和`payload.triggerEventId`均指向源`OperationCompleted.event_id`。
- PostgreSQL事件查询现在完整返回聚合类型、Schema版本、相关ID和因果ID，便于审计链还原。

## 验收结果

- Ruff通过。
- mypy通过，35个源文件无类型错误。
- 64项自动化测试通过。
- 测试覆盖重复事件只生成一份草稿、已有检验时跳过、因果ID保留和下游发布不被截断。
- 真实PostgreSQL创建验证工单`WO-S29-1f9e3a5d`并完成工序后，Worker自动生成一份`CREATE_QUALITY_INSPECTION`草稿。
- 同一真实事件额外重放两次，草稿数量保持1。
- 自动生成事件已由Outbox继续发布，源事件ID同时保存在因果字段和事件负载中。

## 尚未开放

本步不会把建议转成质量检验任务。下一步若要允许检验员从建议草稿一键创建检验，需要新增明确的人工确认端点、QUALITY角色校验、执行时状态复核和幂等约束，必须单独授权。
