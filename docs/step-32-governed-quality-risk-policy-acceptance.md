# 第 32 步：受治理的质量风险策略

## 目标

把第 31 步写死在程序中的 `QUALITY-RISK-V1` 规则升级为可配置、可版本化、可审批、可定时生效和可追溯回滚的质量策略，同时保持 Agent 不可直接改变质量结论或生产状态。

## 已实现范围

- 策略作用域：`GLOBAL`、`PRODUCT`、`OPERATION`、`PRODUCT_OPERATION`。
- 命中优先级：产品与工序组合 > 单产品/单工序 > 全局；同一层级取已生效的最新版本。
- 生命周期：`DRAFT → PENDING_APPROVAL → APPROVED`。
- 发布必须填写审批原因和带时区的生效时间；未来生效的版本不会提前参与计算。
- 规则配置覆盖回看天数、风险分级阈值、各等级抽样比例/上限，以及四组评分因子。
- 每次创建、提交和批准均原子写入 Outbox，事件只保存配置哈希和治理元数据，不复制整份配置。
- 回滚不会修改旧版本，而是复制一个新的草稿版本，经过同一审批流程后才能生效。
- 没有已生效策略时仍使用内置 `QUALITY-RISK-V1`，保证升级兼容。

## 权限与安全边界

| 动作 | 角色 | 是否直接改变生产状态 |
| --- | --- | --- |
| 查看策略与默认配置 | 已认证用户 | 否 |
| 新建草稿、提交审批、创建回滚草稿 | `MASTER_DATA_ADMIN` | 否 |
| 批准并设定生效时间 | `QUALITY` | 否 |
| 使用策略生成质检建议 | 质量 Agent Worker | 仅生成 R2 建议 |
| 创建检验、判定结果、放行/返工 | 既有人工业务流程 | 受原有角色与状态机约束 |

DeepSeek 继续只负责诊断文字，不参与风险打分、策略选择、审批或数据库写入。Agent 只读取已批准且已到生效时间的策略。

## 数据与接口

- 迁移：`0020_quality_policies`
- 表：`quality_risk_policies`
- `GET /api/v1/quality/risk-policies/default-configuration`
- `GET /api/v1/quality/risk-policies`
- `POST /api/v1/quality/risk-policies`
- `POST /api/v1/quality/risk-policies/{policyId}/submit`
- `POST /api/v1/quality/risk-policies/{policyId}/approve`
- `POST /api/v1/quality/risk-policies/{policyId}/rollback-draft`

控制台 Agent 页新增“质量风险策略”区，展示策略键、业务版本、记录版本、状态、生效时间、创建/审批身份，并提供按权限启用的生命周期操作。

## 验收结果

- 内存模式：69 项通过，5 项 PostgreSQL 环境测试按条件跳过。
- Ruff：通过。
- Mypy：38 个源文件通过。
- HTML 内嵌 JavaScript：`node --check` 通过。
- SQLite 从零迁移：通过，包含策略表、状态索引和策略键/版本唯一约束。
- PostgreSQL：已升级至 `0020_quality_policies (head)`；5 项事务/并发专项测试通过，其中包含策略持久化和生效解析。

真实业务验收采用一份全局 v1 策略：把高风险阈值调整为 40，高风险抽样比例调整为 20%。对计划 20 件、报废 2 件的工序，Agent 实际保持确定性得分 45，但风险由 `MEDIUM` 变为 `HIGH`，建议抽样由 1 件变为 4 件，并在提案中持久化了 `GLOBAL:*:*@v1`。随后创建回滚草稿 v2，已批准 v1 仍保持生效且所有治理事件均由 Worker 发布。

## 当前限制与下一决策门

当前按角色分离创建与审批能力，但 DEV 身份可同时拥有两个角色，系统尚未强制“创建人不得审批自己的策略”。进入下一步前需要确认是否同时引入：

1. 策略发布前对历史工单进行只读回放/影响仿真；
2. 强制 Maker-Checker 双人分离，并为本地测试配置不同身份。

这两个变化会调整策略发布流程和本地 DEV 单账号体验，因此作为明确确认点保留。
