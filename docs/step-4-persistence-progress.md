# 第四步持久化切片进度

日期：2026-09-11  
状态：进行中

## 已完成

- 建立Python 3.13项目内隔离环境，满足项目`>=3.12,<3.14`约束。
- 安装并锁定FastAPI、SQLAlchemy 2.0、Alembic、psycopg 3及测试工具。
- 增加PostgreSQL 17 Compose配置和`.env.example`。
- 建立`work_orders`、`idempotency_records`、`event_outbox`、`agent_tool_audits`模型。
- 建立首个Alembic迁移，包含约束和Outbox待发布索引。
- 实现SQLAlchemy工单仓储：创建/释放、乐观锁、幂等记录、Outbox、Agent审计。
- 幂等语义升级为“同一Key且请求内容哈希一致”才允许重放。
- FastAPI可通过`AUTONOMOUS_MES_STORAGE_BACKEND`选择memory或postgresql。
- 新增HTTP契约、迁移和SQLAlchemy仓储测试。
- 新增ADR-0010，明确阶段一同步数据库事务边界。

## 验证结果

- 自动测试：14项通过。
- Ruff：通过。
- Mypy strict：通过。
- Alembic临时数据库升级：通过。
- PostgreSQL方言离线SQL生成：通过，四张表均存在。

## 未完成

- 当前机器没有Docker/PostgreSQL，尚未完成真实PostgreSQL运行测试。
- Outbox领取、重试、隔离和重放Worker尚未实现。
- PostgreSQL readiness探针尚未连接真实数据库。
- OIDC仍未接入。

## 下一工作

实现Outbox Worker及其失败重试测试；随后安装或提供PostgreSQL运行环境，完成真实事务回滚、并发幂等和服务重启持久化验收。
