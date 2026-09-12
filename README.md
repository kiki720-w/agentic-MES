# 自主 MES 阶段一验证工程

本工作区用于验证一个面向精密机械加工的最小 MES 闭环，以及受控 Agent 的安全接入方式。

## 已冻结的试点边界

- 单个虚拟机械加工车间
- 10 台设备：数控车床、加工中心、磨床
- 3 种产品，每种 4—8 道工序
- 主流程：订单 → 工单 → 加工 → 检验 → 返工/完工 → 追溯
- 异常：插单、设备停机、缺料、质量失败、刀具异常
- Agent：只读查询、异常解释、建议生成，以及受策略约束的自然语言动作提案；模型不直接修改生产状态
- 推理：DeepSeek API，经可替换模型网关调用
- 部署：本地 MES/边缘采集 + 云端模型

## 当前交付物

- `docs/pilot-scope.md`：试点范围、场景和退出标准
- `docs/acceptance-tests.md`：端到端验收用例
- `docs/mes-selection.md`：候选 MES 评估方法
- `selection/mes-candidate-scorecard.csv`：候选评分表
- `schemas/manufacturing-event.schema.json`：制造事件 JSON Schema
- `data/virtual-factory.json`：虚拟车间基线数据
- `docs/core-domain-spec.md`：领域对象、聚合、版本规则与状态机规范
- `schemas/state-machines.json`：四套可执行状态机定义
- `docs/manufacturing-events.md`：事件目录与可靠处理规则
- `docs/agent-tool-contracts.md`：Agent工具契约、风险等级和权限矩阵
- `docs/desktop-walkthrough.md`：停机—返工桌面推演记录
- `docs/adr/README.md`：编码前技术架构决策索引
- `docs/step-2-adr-acceptance.md`：第二步技术ADR验收记录
- `core/`：自主MES第一条可运行核心垂直切片
- `docs/step-3-core-slice-acceptance.md`：最小核心代码验收记录
- `docs/step-4-persistence-progress.md`：PostgreSQL持久化切片实施进度
- `docs/step-5-outbox-worker-acceptance.md`：Outbox Worker验收记录
- `docs/step-6-visual-console-acceptance.md`：可视化HTML生产控制台验收记录
- `docs/step-7-quality-rework-acceptance.md`：质量检验、隔离和返工闭环验收记录
- `docs/step-8-deepseek-gateway-acceptance.md`：DeepSeek 诊断解释网关与安全降级验收记录
- `docs/step-9-natural-language-interface-acceptance.md`：只读自然语言生产查询与写意图拦截验收记录
- `docs/step-10-governed-natural-language-action-acceptance.md`：自然语言复工提案、审批门和执行隔离验收记录
- `docs/step-11-natural-language-incident-analysis-acceptance.md`：自然语言触发异常分析与提案分级验收记录
- `docs/step-12-goal-alignment-and-quality-draft.md`：初始目标对齐审计、L2/L3隔离与质量建议草稿验收记录
- `docs/step-13-product-genealogy-acceptance.md`：产品序列号制造谱系第一条垂直切片验收记录
- `docs/step-14-execution-material-trace-acceptance.md`：加工会话、人员与物料批次谱系验收记录
- `docs/step-15-process-resource-trace-acceptance.md`：刀具、夹具与NC程序实绩校验和追溯验收记录
- `docs/step-16-gauge-calibration-trace-acceptance.md`：量具校准有效性和检验实绩追溯验收记录
- `docs/step-17-agent-genealogy-tool-acceptance.md`：Agent完整制造谱系只读工具与范围审计验收记录
- `docs/step-18-resource-master-data-center-acceptance.md`：内置制造资源权威台账与外部来源适配边界验收记录
- `docs/step-19-resource-console-acceptance.md`：制造资源可视化台账与通用登记表单验收记录
- `docs/step-20-resource-csv-import-acceptance.md`：通用CSV模板、预检指纹与原子批量导入验收记录
- `docs/step-21-signed-inbound-connector-acceptance.md`：HMAC签名入站连接器与防重放验收记录
- `docs/step-22-factory-edge-l3-acceptance.md`：工厂独立实例身份与L3审批人白名单验收记录
- `docs/step-23-oidc-identity-acceptance.md`：标准OIDC身份、角色/工厂范围与可选Keycloak参考部署验收记录
- `docs/step-24-scalable-console-and-rbac-acceptance.md`：全写操作RBAC、浏览器PKCE登录与大样本控制台验收记录
- `docs/step-25-million-event-scale-benchmark.md`：10万工单/100万事件实测与事件游标分页验收记录
- `docs/step-26-operational-read-models-acceptance.md`：设备、资源与质量任务大样本读模型和分页验收记录
- `docs/step-27-normalized-work-order-operations-acceptance.md`：工单工序关系化、兼容回填和全库待检池验收记录
- `docs/step-28-operation-projection-and-agent-quality-tool-acceptance.md`：工序双写一致性巡检与Agent质量候选只读工具验收记录
- `docs/step-29-event-driven-quality-agent-acceptance.md`：OperationCompleted事件触发、幂等R2质检建议与因果链验收记录
- `docs/step-30-human-confirmed-quality-inspection-acceptance.md`：质量人员确认Agent建议、单事务建检与目标唯一性验收记录
- `docs/step-31-quality-risk-and-event-explorer-acceptance.md`：确定性质量风险评分、抽样建议与制造事件双向检索验收记录
- `docs/step-32-governed-quality-risk-policy-acceptance.md`：可配置、版本化、审批生效与可追溯回滚的质量风险策略验收记录

## 当前决策门

在选择底座之前，每个候选项目必须完成：许可证复核、可重复部署、核心流程演示、API/数据库检查、权限审计检查和扩展成本评估。未通过许可证或安全硬门槛的项目不进入评分阶段。
