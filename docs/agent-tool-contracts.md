# Agent 工具契约与权限矩阵

## 1. 调用链

```text
用户/事件 → Agent → 工具网关 → 身份与范围策略 → 参数校验 → MES应用服务 → 脱敏结果 → Agent
```

模型永远不获得数据库连接、设备凭据或通用 HTTP 请求工具。每次调用使用短期身份，默认拒绝，按工厂/车间/角色/对象范围授权。

## 2. 第一阶段工具

| 工具 | 输入 | 主要返回 | 风险 | 角色/范围 | 副作用 |
|---|---|---|---|---|---|
| get_work_order | workOrderId | 状态、数量、交期、版本、工序 | R0 | 授权车间只读 | 无 |
| list_delayed_work_orders | workshopId、asOf、limit | 延期/风险工单及证据 | R0 | 授权车间只读 | 无 |
| get_operation_history | operationTaskId | 状态事件、人员、设备、时间 | R0 | 授权车间只读 | 无 |
| get_machine_status | equipmentId、asOf | 当前状态、报警、数据新鲜度 | R0 | 授权设备只读 | 无 |
| get_product_genealogy | productSerial | 人机料法测关联及版本 | R1 | 产品/客户数据范围 | 无 |
| get_quality_results | productSerial | 计划版本、结果、不合格和处置 | R1 | 质量数据范围 | 无 |
| get_product_genealogy | productSerial | 人、机、料、法、测完整谱系与证据时间 | R1 | 关联工单车间范围 | 无 |
| list_active_exceptions | workshopId、severity、limit | 活动异常和关联对象 | R0 | 授权车间只读 | 无 |
| search_work_instruction | query、productId、operationCode | 有效作业指导书片段和版本 | R1 | 文档密级范围 | 无 |
| create_recommendation_draft | type、objectRefs、summary、evidence、risk | recommendationId、状态 | R2 | Agent可创建；人类查看 | 仅新增建议草稿 |

R0=普通只读；R1=敏感只读；R2=非生产写入；R3=生产事务写入；R4=工艺/质量高风险；R5=设备与安全控制。阶段一仅开放 R0—R2。

## 3. 通用请求与响应

请求必须包含：`requestId`、`agentId`、`userContext`、`purpose`、`toolVersion`、业务参数。响应必须包含：`requestId`、`asOf`、`dataFreshness`、`sourceObjects`、`policyDecision`、`data` 或结构化错误。

工具不得返回“推测状态”。找不到、无权限、数据过期和系统故障必须分别返回 `NOT_FOUND`、`FORBIDDEN`、`STALE_DATA`、`UNAVAILABLE`。

`create_recommendation_draft` 使用 `requestId` 幂等；相同请求不得重复创建建议。建议必须引用工具返回的证据对象和数据时间，不得只有自然语言结论。

## 4. 权限矩阵

| 动作 | 操作员 | 计划员 | 班组长 | 检验员 | 质量经理 | 工艺工程师 | Agent-L2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 查询授权工单/设备 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 查询完整产品谱系 | 本班组 | ✓ | ✓ | 质量范围 | ✓ | ✓ | 授权范围 |
| 查询质量结果 | 本工序 | ✓ | ✓ | ✓ | ✓ | ✓ | 授权范围 |
| 派工/改派 | — | ✓ | ✓ | — | — | — | 禁止 |
| 开始/暂停/完成工序 | ✓ | — | ✓ | — | — | — | 禁止 |
| 填写检验结果 | — | — | — | ✓ | ✓ | — | 禁止 |
| 隔离产品 | — | — | — | ✓ | ✓ | — | 禁止 |
| 批准返工/报废/让步 | — | — | — | — | ✓ | 参与评审 | 禁止 |
| 发布工艺/NC程序版本 | — | — | — | — | — | ✓且需审批 | 禁止 |
| 创建建议草稿 | — | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 直接写数据库 | 禁止 | 禁止 | 禁止 | 禁止 | 禁止 | 禁止 | 禁止 |
| 控制PLC/CNC | 受控终端/机床权限 | 禁止 | 禁止 | 禁止 | 禁止 | 受工程流程约束 | 永久禁止 |

## 5. 明确禁止注册为 Agent 工具

- 任意 SQL 执行
- 任意文件系统读写
- 任意外部 URL 请求
- 删除生产记录
- 修改历史检验值
- 发布或替换 NC 程序
- 修改加工参数
- 释放、让步或报废产品
- PLC/CNC/机器人控制
- 禁用安全联锁或绕过审批

未来进入 L3 时，只能新增业务含义明确、参数收敛、可幂等、可审计、可回滚且通过风险评审的专用工具。
