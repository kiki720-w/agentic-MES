# 第二步技术ADR验收记录

验收日期：2026-08-30  
结论：通过，可创建最小核心代码骨架。

## 已冻结决策

- 架构：模块化单体，业务模块有明确所有权。
- 后端：Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2.0稳定线、psycopg 3、Alembic。
- 数据：PostgreSQL 17；阶段一不强制TimescaleDB。
- 事件：事务Outbox、至少一次投递、消费者幂等；暂不引入Kafka。
- 身份：OIDC；试点首选Keycloak；RBAC加对象范围；Agent独立身份。
- API：`/api/v1`、结构化错误、幂等键、乐观锁、OpenAPI契约。
- 模型：供应商中立网关，DeepSeek为首个适配器；模型故障不影响MES。
- 部署：API、worker、PostgreSQL、OIDC、边缘模拟器；优先单厂本地部署。
- 测试：领域、数据库、API、事件、Agent安全和端到端分层门禁。

## 编码入口

创建项目骨架时，第一条垂直切片只实现：创建工单 → 释放工单 → 生成Outbox事件 → 查询工单 → Agent只读`get_work_order`。不先开发页面、不连接真实机床、不调用真实DeepSeek，先用假模型完成确定性测试。

## 官方技术依据

- FastAPI部署与测试文档：`https://fastapi.tiangolo.com/deployment/`
- SQLAlchemy 2.0异步文档：`https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html`
- PostgreSQL官方文档：`https://www.postgresql.org/docs/`
- Keycloak官方文档：`https://www.keycloak.org/documentation`
- DeepSeek API官方文档：`https://api-docs.deepseek.com/`
