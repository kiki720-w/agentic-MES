# Agentic Manufacturing Control Plane

本工作区正在构建面向机械加工与装备制造的制造智能控制层。产品不替换企业现有 ERP、MES、WMS、QMS 或设备平台，而是在其上建立可追溯的制造数字镜像、有限产能 APS、受控 L3 智能体和安全回写治理。

早期自建 MES 流程仍保留为离线模拟器和回归测试夹具，不再作为正式产品入口或权威生产数据源。

## 当前产品边界

- 单个虚拟机械加工车间
- 10 台设备：数控车床、加工中心、磨床
- 3 种产品，每种 4—8 道工序
- 输入：连接既有 ERP/MES/WMS/QMS、机床和传感器，并保留来源、版本和观测时间
- 核心：统一制造语义、滚动有限产能排产、约束证据、事件追溯和策略治理
- 输出：形成建议或待审批计划，经授权人员批准后再由专用连接器回写
- 异常：插单、设备停机、缺料、质量失败、刀具异常
- Agent：L3 排产智能体可自动感知变化、生成并提交方案，但不能自批、自发或直接控制设备
- 推理：DeepSeek、Kimi 或其他 OpenAI 兼容 API，经可热切换模型网关调用
- 部署：本地优先；支持厂内模型服务器，云模型仅由客户主动选择
- 人工输入：Agent 工作台支持文字与附件入口；XLSX 经预检、指纹确认后生成标准快照
- 工时模型：工序需求 = 准备工时 + 剩余数量 × 单件工时，缺失时才使用排产回退值
- 大样本界面：输入、产能、计算与全宽结果分离，长表按搜索和分页查看

## 当前交付物

- PROJECT_HANDOFF.md：新对话续接所需的产品决策、当前状态、安全边界和开发顺序
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
- `docs/step-33-policy-simulation-and-maker-checker-acceptance.md`：策略发布前历史影响回放与创建/审批双人分离验收记录
- `docs/step-34-unified-aps-acceptance.md`：Agentic MES 与 paichan 思路统一、有限产能排产及计划治理验收记录
- `docs/step-35-control-plane-and-scheduling-agent-acceptance.md`：控制层产品重构、外部排产快照和L3智能体迁移验收记录
- `docs/step-36-connector-developer-kit-acceptance.md`：供应商中立排产快照Schema、样例和HMAC推送客户端验收记录
- `docs/step-37-agent-workspace-and-capacity-input-acceptance.md`：统一附件工作台、工艺工时、可编辑产能与全宽结果页验收记录
- `docs/step-38-person-centric-scheduling-acceptance.md`：全页面 Agent 入口、真实输入摘要及人员日/周排程验收记录
- `docs/step-39-provider-neutral-model-gateway-acceptance.md`：供应商中立模型网关、管理员 BYOK 设置与安全回退验收记录
- `GET /workspace`：自然语言、XLSX/CSV预检与L3排产协作工作台
- `GET /capacity`：版本化人员与工作单元产能维护
- `GET /planning/results`：标明人工/虚拟员工身份的全宽排产结果
- `GET /settings/models`：管理员切换模型供应商、Base URL、模型 ID 与 API Key

## 当前决策门

软件底座已经具备。当前无需连接公司生产系统，可先通过可下载的三表XLSX模板验证完整排产链。下一项必须人工选择的是首个真实系统适配器；视觉附件要进入语义诊断链时，还需选择具备图像能力的模型提供方和数据出厂策略。
