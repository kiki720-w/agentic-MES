# CAPAXION

**Manufacturing Decision OS｜制造决策操作系统**

本工作区正在构建面向机械加工与装备制造的制造智能控制层。产品不替换企业现有 ERP、MES、WMS、QMS 或设备平台，而是在其上建立可追溯的制造数字镜像、有限产能 APS、分域开放的 L4 Agent 和安全回写治理。

## L4 排产自治运行时（2026-09-13）

桌面 0.4.3 与 Core 已实现排产域第一条 L4 目标闭环：观察、计划、策略授权、幂等执行、回读核验、失败回滚和停止开关。默认处于 SHADOW、循环关闭、执行目标为空；真实验证会拒绝演示投影、缺工艺标准、能力缺口和非原子计划替换。模拟适配器只用于自动测试，尚未接入真实 MES 写回，因此当前声明为 `L4_RUNTIME_IMPLEMENTED_NOT_PRODUCTION_VALIDATED`。详见 [L4 排产运行时说明](docs/l4-scheduling-runtime.md)。

Agent 工作台已完成 Codex 式附件链路：全窗口拖入、本机解析、附件随问题进入当前本地模型并返回有依据的回答。XLSX 使用 Docling 的离线结构识别，并在 Core 中保留工作表、连续数据区和字段映射边界；支持常见文字文件、CSV/XLSX、DOCX、文本或扫描 PDF，以及 PNG/JPG 本地 OCR。详见 [本地附件拖放与理解](docs/local-attachment-understanding.md)和 [真实 Excel 解析决策](docs/xlsx-document-intelligence.md)。

早期自建 MES 流程仍保留为离线模拟器和回归测试夹具，不再作为正式产品入口或权威生产数据源。

## 本地 AI 与云端 MES 的独立网络边界（2026-09-13）

桌面 0.2.3 与 Core 默认使用本机 Qwen3 4B，云模型出口关闭；模型与业务系统分别授权。本地模型原始基准仍为 6/9，加入固定公式、单位、来源冲突和权限证据后，制造任务路径连续两次达到 9/9。工作台也改为全量汇总后按问题选择相关记录，避免本地模型上下文溢出。该结果只适用于固定合成题，不等于生产准确率。详见 [准确率优化记录](docs/manufacturing-accuracy.md)和[网络边界说明](docs/independent-network-boundaries.md)。

## 当前产品边界

- 单个虚拟机械加工车间
- 10 台设备：数控车床、加工中心、磨床
- 3 种产品，每种 4—8 道工序
- 输入：连接既有 ERP/MES/WMS/QMS、机床和传感器，并保留来源、版本和观测时间
- 核心：统一制造语义、滚动有限产能排产、约束证据、事件追溯和策略治理
- 输出：在预授权范围内自主执行并核验；超界任务转人工，外部系统只经专用连接器回写
- 异常：插单、设备停机、缺料、质量失败、刀具异常
- Agent：产品目标为分域 L4；排产自治运行时已实现第一版，当前默认影子运行，尚未通过真实 MES 和工厂现场验收
- 推理：DeepSeek、Kimi 或其他 OpenAI 兼容 API，经可热切换模型网关调用
- 部署：本地优先；支持厂内模型服务器，云模型仅由客户主动选择
- 人工输入：Agent 工作台支持文字、按钮选取和全窗口文件拖入；文字、表格、DOCX、PDF 和图片 OCR 均在本机解析，XLSX/CSV 还可经预检、指纹确认后生成标准快照
- 工时模型：工序需求 = 准备工时 + 剩余数量 × 单件工时，缺失时才使用排产回退值
- 大样本界面：输入、产能、计算与全宽结果分离，长表按搜索和分页查看

## 最小文字能力验收

桌面和网页 Agent 工作台提供“文字能力自检”，使用三项固定合成样本验证事实引用、缺失事实和计算。DeepSeek `deepseek-v4-flash` 本次实测 3/3 通过；模型状态区分 CONFIGURED / VERIFIED / DEGRADED / DISABLED，失败继续安全降级。VERIFIED 仅表示请求有效，生产准确率、PDF/图片理解和完整多轮会话尚未验收。报告保存在本机 `core/.capaxion/capability-checks/`，不提交 Git。详见 [验收记录](docs/minimum-ai-acceptance.md)。

## 制造能力验收中心

桌面“AI 能力验收”提供 9 道制造合成题、逐题证据、历史、JSON 导出和兼容报告对比。历史原始测试中 DeepSeek 三轮 9/9，本机 Qwen3 4B 三轮 6/9；新版系统路径把确定性证据与模型组合，两次为 9/9。界面明确标记“原始模型/规则辅助”，不同请求版本不可直接比较。文件、PDF、图片等能力仍分别标记接入与验收状态。详见 [能力验收中心说明](docs/capability-benchmark-center.md)。

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
- `docs/step-34-unified-aps-acceptance.md`：CAPAXION 与 paichan 排产思路统一、有限产能排产及计划治理验收记录
- `docs/step-35-control-plane-and-scheduling-agent-acceptance.md`：控制层产品重构、外部排产快照和L3智能体迁移验收记录
- `docs/step-36-connector-developer-kit-acceptance.md`：供应商中立排产快照Schema、样例和HMAC推送客户端验收记录
- `docs/step-37-agent-workspace-and-capacity-input-acceptance.md`：统一附件工作台、工艺工时、可编辑产能与全宽结果页验收记录
- `docs/step-38-person-centric-scheduling-acceptance.md`：全页面 Agent 入口、真实输入摘要及人员日/周排程验收记录
- `docs/step-39-provider-neutral-model-gateway-acceptance.md`：供应商中立模型网关、管理员 BYOK 设置与安全回退验收记录
- `docs/desktop-client-foundation.md`：Windows 桌面客户端、安全 IPC、文件预检和本机打包验收记录
- `docs/local-attachment-understanding.md`：全窗口拖放、本地文件解析、OCR、附件问答边界与实测证据
- `GET /workspace`：自然语言、XLSX/CSV 预检与衡策任务工作台
- `GET /capacity`：版本化人员与工作单元产能维护
- `GET /planning/results`：标明人工/虚拟员工身份的全宽排产结果
- `GET /settings/models`：管理员切换模型供应商、Base URL、模型 ID 与 API Key
- `desktop/`：CAPAXION Windows 桌面客户端，提供统一 Agent 工作台、文件选择、Core 自动连接及原有业务页面入口

## Windows 桌面版

当前已提供可直接运行的原生桌面客户端。它不是单纯的浏览器快捷方式：Electron 主进程负责窗口、文件选择、本地 Core 健康检查和受限 IPC，React 原生组件直接调用 Core API，渲染进程不能直接访问 Node.js 或数据库。控制塔、排产、结果、产能和模型设置已经统一为同一套桌面设计系统。

开发启动：

    cd D:\mes\desktop
    pnpm dev

生成 Windows 便携版：

    pnpm desktop:build

本机产物：`D:\mes\desktop\release\CAPAXION-0.2.1-x64.exe`。便携版从该目录启动时会查找 `D:\mes\core` 并在需要时启动本地 Core；也可通过 `CAPAXION_REPOSITORY_ROOT`、`CAPAXION_PYTHON` 和 `CAPAXION_CORE_URL` 显式配置。

## 当前决策门

软件底座已经具备。当前无需连接公司生产系统，可先通过可下载的三表XLSX模板验证完整排产链。下一项必须人工选择的是首个真实系统适配器；视觉附件要进入语义诊断链时，还需选择具备图像能力的模型提供方和数据出厂策略。
