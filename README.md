# 自主 MES 阶段一验证工程

本工作区用于验证一个面向精密机械加工的最小 MES 闭环，以及只读 Agent 的安全接入方式。

## 已冻结的试点边界

- 单个虚拟机械加工车间
- 10 台设备：数控车床、加工中心、磨床
- 3 种产品，每种 4—8 道工序
- 主流程：订单 → 工单 → 加工 → 检验 → 返工/完工 → 追溯
- 异常：插单、设备停机、缺料、质量失败、刀具异常
- Agent：只读查询、异常解释、建议生成；不直接修改生产状态
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

## 当前决策门

在选择底座之前，每个候选项目必须完成：许可证复核、可重复部署、核心流程演示、API/数据库检查、权限审计检查和扩展成本评估。未通过许可证或安全硬门槛的项目不进入评分阶段。
