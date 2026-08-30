# 停机—返工场景桌面推演

## 场景

传动轴 `SN-SHAFT-0001` 属于工单 `WO-SHAFT-001`，路线版本 A，当前在 `LATHE-02` 执行精车工序 `OT-SHAFT-001-30`。机床报警停机；Agent分析影响并提出换机建议但人不采纳。设备恢复后完成加工，终检失败，产品隔离、返工、复检合格，最后查询完整谱系。

## 推演记录

| 步骤 | 命令/事实 | 对象变化 | 事件 | 权限与审计 | Agent边界 |
|---:|---|---|---|---|---|
| 1 | 计划员创建并释放工单 | WorkOrder DRAFT→RELEASED；生成工序 | WorkOrderCreated、WorkOrderReleased | 记录冻结版本 | 只可查询 |
| 2 | 前序完成、精车齐套 | Operation PENDING→READY | OperationBecameReady | 记录齐套检查 | 只可解释 |
| 3 | 班组长派至 LATHE-02 | READY→DISPATCHED | OperationDispatched | 校验设备能力 | 不可派工 |
| 4 | 操作员校验版本后开工 | DISPATCHED→RUNNING；设备 IDLE→RUNNING | OperationStarted、MachineStateChanged、ToolLoaded | 校验资质、程序和刀具 | 不可开机 |
| 5 | 网关上报主轴报警 | 设备 RUNNING→DOWN；工序 RUNNING→PAUSED | MachineAlarmRaised、OperationPaused | sourceEventId去重，记录实际/接收时间 | 可被事件唤起分析 |
| 6 | Agent查询影响 | 无生产状态变化 | AgentToolCallRecorded | 只读工具、范围策略、数据时间 | 查询工单/设备/候选能力 |
| 7 | Agent生成换机建议 | 新增 Recommendation DRAFT | RecommendationDraftCreated | 引用证据、风险R2、幂等 | 不能改派或执行 |
| 8 | 班组长不采纳 | 建议 DRAFT→REJECTED；生产不变 | RecommendationRejected | 记录人类理由 | 学习结果但不得自行重试执行 |
| 9 | 维护确认恢复 | 设备 DOWN→IDLE | MachineRestored | 维护身份和处理记录 | 只读 |
| 10 | 操作员恢复加工 | 工序 PAUSED→RUNNING；设备 IDLE→RUNNING | OperationResumed、MachineStateChanged | 重新校验设备和会话 | 不可恢复设备 |
| 11 | 精车完成 | 工序 RUNNING→COMPLETED；设备→IDLE | OperationCompleted、MachineStateChanged | 数量守恒、形成谱系 | 只读 |
| 12 | 创建并执行终检 | 质量 NOT_INSPECTED→INSPECTION_PENDING | InspectionRequested、InspectionCompleted | 冻结检验计划和量具 | 不可填写结果 |
| 13 | 尺寸超差 | INSPECTION_PENDING→REJECTED→HELD | QualityFailed、ProductHeld | 检验员记录，质量员隔离 | 可总结证据，不可放行 |
| 14 | 质量经理批准返工 | HELD→REWORK；创建返工单 | ReworkCreated | 处置审批、返工路线版本 | 不可批准 |
| 15 | 完成返工并申请复检 | REWORK→REINSPECTION | OperationCompleted、ReinspectionRequested | 新执行与原谱系相连 | 只读 |
| 16 | 复检合格 | REINSPECTION→ACCEPTED | InspectionCompleted、InspectionAccepted | 原失败结果不覆盖 | 不可判定合格 |
| 17 | 查询完整谱系 | 无状态变化 | AgentToolCallRecorded | 返回来源对象、版本、asOf | 可生成带证据总结 |

## 推演结论

- 所有生产状态变化均有合法人类/设备主体、前置条件和对应事件。
- Agent只新增建议草稿，不会改变工单、工序、设备或质量状态。
- 人工拒绝建议不影响生产事实，并形成可用于后续评测的决策结果。
- 首次不合格结果被永久保留，返工和复检通过新记录扩展谱系。
- 断开模型服务不影响步骤9—16的MES人工流程。

## 发现并冻结的设计决策

1. `SUSPENDED` 工单恢复统一进入 `IN_PROGRESS`；阶段一不保留“从 RELEASED 暂停再恢复到 RELEASED”的复杂历史状态。
2. 设备报警不会由 MES 自动恢复；必须存在维护或授权人员确认。
3. Agent建议拒绝后不得自动改写建议并反复提交，新的建议必须有新证据和新 requestId。
4. 质量失败必须先进入 HELD，返工、报废和让步均从 HELD 发起。

