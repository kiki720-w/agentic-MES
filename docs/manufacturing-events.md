# 制造事件目录与处理规则

## 1. 标准信封

所有事件遵循 `schemas/manufacturing-event.schema.json`。标准字段为：事件ID、类型、版本、发生时间、接收时间、工厂/车间、业务对象、操作者、来源、关联ID、因果ID和载荷。

生产事件采用追加写。消费者以 `eventId` 幂等；设备来源额外以 `(source, sourceEventId)` 去重。接收时间早于发生时间、时间漂移超限或序列倒退时标记数据质量告警，不静默修正原始时间。

## 2. 事件目录

| 领域 | 事件 | 触发者 | 关键载荷 | 主要消费者 |
|---|---|---|---|---|
| 工单 | WorkOrderCreated | 计划员/ERP | productRevision、qty、dueAt | 执行、审计 |
| 工单 | WorkOrderReleased | 计划员 | frozenRevisions | 工序生成、事件流 |
| 工单 | WorkOrderStarted | MES | firstOperation | 看板 |
| 工单 | WorkOrderSuspended | 班组长/规则 | reason | 调度、告警 |
| 工单 | WorkOrderResumed | 班组长 | reason | 看板 |
| 工单 | WorkOrderCompleted | MES | goodQty、rejectQty | 质量、ERP |
| 工单 | WorkOrderClosed | 计划员 | closeReason | ERP、归档 |
| 工单 | WorkOrderCancelled | 计划员 | reason | 调度、审计 |
| 工序 | OperationBecameReady | MES | readinessChecks | 派工 |
| 工序 | OperationDispatched | 计划员/班组长 | equipment、plannedTime | 终端、设备关联 |
| 工序 | OperationStarted | 操作员 | operator、equipment、versions | 谱系、看板 |
| 工序 | OperationPaused | 操作员/异常规则 | reason | 调度、异常 |
| 工序 | OperationResumed | 操作员 | reason | 看板 |
| 工序 | OperationCompleted | 操作员/MES | goodQty、rejectQty | 谱系、质量 |
| 工序 | OperationFailed | MES/班组长 | reason | 异常、质量 |
| 设备 | MachineStateChanged | 边缘网关 | from、to、sourceEventId | 状态投影、OEE |
| 设备 | MachineAlarmRaised | 边缘网关 | alarmCode、severity | 异常、维护、影响分析 |
| 设备 | MaintenanceStarted | 维护员 | workRequest | 设备状态 |
| 设备 | MachineRestored | 维护员/网关 | resolution | 设备状态、调度 |
| 物料 | MaterialConsumed | 操作员/设备 | lot、qty、unit | 库存、谱系 |
| 工装 | ToolLoaded | 操作员/设备 | toolId、lifeSnapshot | 谱系、刀具管理 |
| 工装 | ToolChanged | 操作员/设备 | oldTool、newTool、reason | 谱系、异常 |
| 质量 | InspectionRequested | MES | planRevision、productSerial | QMS/检验 |
| 质量 | InspectionCompleted | 检验员/量具 | characteristics | 谱系、质量状态 |
| 质量 | InspectionAccepted | MES/检验员 | requestId | 放行规则 |
| 质量 | QualityFailed | 检验员/MES | defectCode、evidence | 不合格、隔离 |
| 质量 | ProductHeld | 质量员 | reason、scope | 流转拦截 |
| 质量 | ReworkCreated | 质量工程师 | disposition、reworkRoute | 返工执行 |
| 质量 | ReinspectionRequested | MES | planRevision | 检验 |
| 质量 | ProductScrapped | 质量经理 | approval、reason | 库存、成本、ERP |
| 质量 | ConcessionAccepted | 质量经理 | approval、conditions | 放行、审计 |
| Agent | RecommendationDraftCreated | Agent | evidence、proposal、risk | 人工工作台 |
| Agent | AgentToolCallRecorded | Agent网关 | model、tool、decision、latency | 审计、评测 |

## 3. 发布与消费保证

- 领域事务和待发布事件使用同一事务写入 Outbox。
- 发布器至少一次投递；消费者必须幂等。
- 事件不可携带密码、API密钥或未授权的敏感图纸内容。
- schemaVersion 发生不兼容变化时创建新主版本；旧消费者保留迁移期。
- 失败事件进入隔离队列，人工修复后重放；不得丢弃。
- 状态投影可以从快照加事件重建并与事务表核验。

