# 技术架构决策记录（ADR）

状态：阶段一编码基线

| ADR | 决策 | 状态 |
|---|---|---|
| 0001 | 模块化单体与清洁架构边界 | Accepted |
| 0002 | Python 3.12、FastAPI、SQLAlchemy 2.0 | Accepted |
| 0003 | PostgreSQL、迁移与数据所有权 | Accepted |
| 0004 | 事务Outbox与事件交付 | Accepted |
| 0005 | OIDC身份、授权与审计 | Accepted |
| 0006 | REST API、幂等与错误契约 | Accepted |
| 0007 | 模型网关与DeepSeek适配 | Accepted |
| 0008 | 部署、可观测性与恢复 | Accepted |
| 0009 | 测试、质量门禁与安全验证 | Accepted |
| 0010 | 阶段一同步生产事务边界 | Accepted |

ADR只能通过新增ADR取代，不能直接改掉历史结论。代码若偏离Accepted ADR，必须在合并前记录原因和迁移影响。
