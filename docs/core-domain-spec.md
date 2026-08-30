# 自主 MES 核心领域与状态机规范

版本：0.1  
状态：阶段一设计基线  
适用范围：精密机械加工虚拟车间 `WS-MACH-01`

## 1. 设计原则

1. MES 是生产事实和事务状态的唯一可信来源；模型输出不是生产事实。
2. 已参与生产的产品、工艺、图纸、NC 程序和检验规范不得覆盖修改，只能发布新版本。
3. 状态只能通过领域命令校验后转换，并生成不可变制造事件；禁止直接改状态字段。
4. 事件必须可去重、可关联、可审计，并保留实际发生时间与系统接收时间。
5. Agent 只能通过注册工具访问 MES，不能直接访问生产数据库、PLC 或 CNC。
6. 第一阶段 Agent 为 L2：查询、解释和生成建议，不改变生产状态。
7. 设备安全和确定性控制永久由 PLC、CNC、机器人控制器及安全回路承担。

## 2. 聚合边界

| 聚合 | 聚合根 | 负责内容 | 不负责内容 |
|---|---|---|---|
| 组织资源 | Factory | 工厂、车间、工作中心、设备归属 | 实时设备控制 |
| 产品定义 | ProductRevision | 产品、BOM、图纸及生效版本 | 实际生产执行 |
| 工艺定义 | RoutingRevision | 工序定义、资源能力、工装、程序和检验要求 | 工单状态 |
| 生产执行 | WorkOrder | 工单、工序任务、派工与执行状态 | 质量处置审批 |
| 产品谱系 | ProductUnit | 序列号、投入、消耗、人员、设备、方法和测量关联 | 主数据版本发布 |
| 质量 | Nonconformance | 检验、不合格、隔离、返工、报废和让步处置 | NC 程序变更 |
| 设备 | Equipment | 能力、可用性、运行状态和报警 | 运动控制 |
| Agent 治理 | AgentAction | 工具调用、建议、审批、执行结果与审计 | 绕过业务规则 |

跨聚合变化使用领域服务和事件完成，单个事务不跨越多个数据库所有者。

## 3. 核心对象字典

### 3.1 组织与资源

| 对象 | 主键 | 必填字段 | 关键关系 | 删除与审计 |
|---|---|---|---|---|
| Factory | factoryId | code、name、timezone、status | 包含 Workshop | 使用后只能停用 |
| Workshop | workshopId | factoryId、code、name | 包含 WorkCenter | 使用后只能停用 |
| WorkCenter | workCenterId | workshopId、code、name | 包含 Equipment | 使用后只能停用 |
| Equipment | equipmentId | workCenterId、code、type、status | 关联 Capability | 所有状态变化审计 |
| Capability | capabilityId | code、name、unit | 被设备与工序要求引用 | 被引用后不可删除 |
| Person | personId | employeeNo、name、status | 关联 Qualification | 离职后停用 |
| Qualification | qualificationId | personId、capabilityCode、validFrom、validTo | 开工校验 | 变更留痕 |
| Tool | toolId | toolCode、type、lifeStatus | 工序使用记录 | 使用后不可物理删除 |
| Fixture | fixtureId | fixtureCode、type、status | 工序装夹记录 | 使用后不可物理删除 |
| Gauge | gaugeId | gaugeCode、type、calibrationDueAt | 检验记录 | 校准过期禁止使用 |

### 3.2 产品与工艺定义

| 对象 | 主键 | 必填字段 | 版本规则 | 发布后规则 |
|---|---|---|---|---|
| Product | productId | code、name、traceMode | 非版本容器 | 可停用，不删除 |
| ProductRevision | productRevisionId | productId、revision、status、effectiveFrom | `(productId, revision)` 唯一 | RELEASED 后不可覆盖 |
| BomRevision | bomRevisionId | productRevisionId、revision、lines | 独立版本 | RELEASED 后不可覆盖 |
| DrawingRevision | drawingRevisionId | productRevisionId、documentNo、revision、fileHash | 内容哈希留证 | RELEASED 后不可替换文件 |
| RoutingRevision | routingRevisionId | productRevisionId、revision、operations | 有效期不能冲突 | RELEASED 后不可覆盖 |
| OperationDefinition | operationDefinitionId | routingRevisionId、sequence、code、name | 随路线版本冻结 | 不单独修改 |
| ResourceRequirement | requirementId | operationDefinitionId、capabilityCode、quantity | 随工序冻结 | 不单独修改 |
| NcProgramRevision | ncProgramRevisionId | programNo、revision、fileHash、approvedBy | 审批后发布 | 生产引用版本不可覆盖 |
| InspectionPlanRevision | inspectionPlanRevisionId | planNo、revision、characteristics | 审批后发布 | 检验引用版本不可覆盖 |
| WorkInstructionRevision | instructionRevisionId | documentNo、revision、fileHash | 审批后发布 | 生产引用版本不可覆盖 |

版本状态统一为 `DRAFT → UNDER_REVIEW → RELEASED → OBSOLETE`。只有 `RELEASED` 且在有效期内的版本可用于新工单；既有工单继续引用创建时快照。

### 3.3 生产执行

| 对象 | 主键 | 必填字段 | 关键约束 | 删除规则 |
|---|---|---|---|---|
| ProductionOrder | productionOrderId | source、productRevisionId、quantity、dueAt、priority | 外部订单号幂等 | 释放后不可删除 |
| WorkOrder | workOrderId | productionOrderId、routingRevisionId、quantity、status | 保存主数据版本快照 | 创建后只可取消/关闭 |
| OperationTask | operationTaskId | workOrderId、operationDefinitionId、sequence、status | 前序完成和资源齐套后 READY | 不可删除 |
| Dispatch | dispatchId | operationTaskId、equipmentId、plannedStart、actor | 设备能力必须匹配 | 撤回需事件 |
| ExecutionSession | sessionId | operationTaskId、equipmentId、operatorId、startedAt | 同一设备不可有冲突活动会话 | 不可删除 |
| MaterialConsumption | consumptionId | sessionId、materialLot、quantity、unit | 不允许负数量；可冲销不可覆盖 | 冲销留反向记录 |
| ToolUsage | toolUsageId | sessionId、toolId、loadedAt | 校验刀具状态/寿命 | 卸载后封存记录 |
| ProductionDeclaration | declarationId | operationTaskId、goodQty、rejectQty、declaredAt | 数量守恒 | 更正用新记录 |

### 3.4 质量与谱系

| 对象 | 主键 | 必填字段 | 关键约束 | 删除规则 |
|---|---|---|---|---|
| ProductUnit | productSerial | productRevisionId、workOrderId、genealogyStatus | 序列号全局唯一 | 永不删除 |
| InspectionRequest | inspectionRequestId | productSerial、planRevisionId、status | 引用冻结的检验版本 | 不可删除 |
| InspectionResult | inspectionResultId | requestId、characteristic、value、result、measuredAt | 原始结果不可覆盖 | 修正新增结果并关联原记录 |
| Nonconformance | nonconformanceId | productSerial、defectCode、status、detectedAt | 一个缺陷有独立处置生命周期 | 关闭后只读 |
| Hold | holdId | productSerial、reason、placedAt | HELD 产品禁止继续流转 | 释放必须授权事件 |
| Disposition | dispositionId | nonconformanceId、decision、approvedBy | 决策为 REWORK/SCRAP/USE_AS_IS | 审批后不可覆盖 |
| ReworkOrder | reworkOrderId | nonconformanceId、routingRevisionId、status | 独立返工路线和谱系 | 不可删除 |
| GenealogyLink | genealogyLinkId | productSerial、relationType、objectType、objectId、occurredAt | 只追加 | 永不物理删除 |

## 4. 状态机

状态转换的机器可读版本见 `schemas/state-machines.json`。

### 4.1 工单

```text
DRAFT ──release──> RELEASED ──start──> IN_PROGRESS ──complete──> COMPLETED ──close──> CLOSED
  │                    │                    │
  └────cancel─────────>CANCELLED<────cancel┘
                       ▲
RELEASED/IN_PROGRESS ─suspend─> SUSPENDED ─resume─> 原活动状态
```

规则：释放前必须存在有效产品、路线、BOM和必要版本；完成要求所有必须工序完成且数量守恒；关闭要求质量处置结束。

### 4.2 工序任务

```text
PENDING → READY → DISPATCHED → RUNNING → COMPLETED
                       │          │
                       └cancel    ├pause→ PAUSED →resume→ RUNNING
                                  └fail → FAILED
```

规则：READY 要求前序完成、物料齐套和定义有效；RUNNING 要求操作员资质、设备能力、工装/程序版本校验通过；COMPLETED 要求报工数量有效且必要检验已创建。

### 4.3 质量

```text
NOT_INSPECTED → INSPECTION_PENDING → ACCEPTED
                        │
                        └fail→ REJECTED → HELD ┬→ REWORK → REINSPECTION ┬→ ACCEPTED
                                               │                         └→ HELD
                                               ├→ SCRAPPED
                                               └→ ACCEPTED_BY_CONCESSION
```

规则：质量失败必须先隔离；让步接收需要质量授权角色；返工必须引用批准的返工路线；报废不可逆，只能通过补偿业务记录处理错误。

### 4.4 设备

设备状态：`OFFLINE`、`IDLE`、`SETUP`、`RUNNING`、`STARVED`、`BLOCKED`、`DOWN`、`MAINTENANCE`。

核心约束：只有 `IDLE/SETUP` 可进入 `RUNNING`；报警可使活动状态转为 `DOWN`；`DOWN` 经维修确认进入 `MAINTENANCE` 或恢复 `IDLE`；边缘网关上报状态，MES记录事实但不承担安全控制。

## 5. 命令、事件与审计

一个请求必须经过：身份认证 → 对象权限 → 状态前置条件 → 业务规则 → 幂等校验 → 事务写入 → 事件追加 → 审计记录。

审计至少包含：`auditId`、`occurredAt`、`actorType`、`actorId`、`action`、`objectType`、`objectId`、`requestId`、`correlationId`、`policyDecision`、`beforeHash`、`afterHash`、`result`、`reason`。

生产记录不提供物理删除 API。纠错使用冲销、撤回、取消或新版本，并保留原事实。

## 6. 一致性规则

- ProductUnit 只能引用一个创建时的 ProductRevision。
- WorkOrder 必须冻结 RoutingRevision、BomRevision、DrawingRevision 等版本引用。
- OperationTask 的 sequence 在同一工单内唯一。
- 同一 ExecutionSession 只能绑定一台设备和一名主操作员。
- 设备事件必须保留 sourceEventId；相同来源和 sourceEventId 只处理一次。
- 质量为 HELD/SCRAPPED 的产品禁止进入正常下一工序。
- AgentAction 与每次工具调用、建议和审批结果关联。
- 任何模型故障不得阻断人工 MES 主流程。

## 7. 阶段一非目标

- 自动修改排程或工艺参数
- 自动发布 NC 程序
- LLM 直接控制机床、PLC 或机器人
- 跨工厂自治
- 以模型推断替代检验或生产原始记录
- 训练自有基础大模型

