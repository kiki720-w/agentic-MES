# ADR-0002：后端运行时

状态：Accepted  
日期：2026-08-30

## 决策

- Python 3.12.x作为阶段一运行时；补丁版本由锁文件固定。
- FastAPI提供HTTP/OpenAPI边界，Pydantic 2负责输入输出验证。
- SQLAlchemy 2.0稳定线提供ORM/Core，使用显式`AsyncSession`，不使用全局scoped session和隐式lazy loading。
- PostgreSQL驱动采用psycopg 3异步接口。
- Alembic管理所有数据库迁移。
- 依赖使用`pyproject.toml`声明并生成可重现锁文件；禁止宽泛无上限版本进入生产构建。

## 理由

Python降低AI、优化和工业数据处理的集成成本；FastAPI能够生成明确的工具API契约；SQLAlchemy 2.0是稳定线并有正式异步支持。暂不采用仍处于beta的SQLAlchemy 2.1。

## 约束

- 请求内一个数据库session，不跨并发任务共享AsyncSession。
- 领域对象不暴露ORM实体给API。
- 数据访问通过repository端口；复杂报表可使用显式SQL/Core。
- CPU密集优化、文档解析和视觉任务进入独立worker，不阻塞API事件循环。
- 当前工作站只有Python 3.9，编码前需安装隔离的Python 3.12工具链，不替换系统Python。

