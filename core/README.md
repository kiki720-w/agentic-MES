# Autonomous MES Core

第一条垂直切片：创建工单、释放工单、原子写入Outbox、查询工单，以及Agent只读`get_work_order`工具。

本目录是自主实现，不依赖`candidates/`中的任何候选MES代码。

## 当前可运行范围

领域和应用测试仅依赖Python标准库，可在当前Python 3.9环境运行：

```powershell
cd core
python -m unittest discover -s tests -v
```

FastAPI入口按Python 3.12和`pyproject.toml`依赖设计。在安装生产依赖后运行：

```powershell
uvicorn autonomous_mes.api:app --app-dir src --reload
```

## API

- `POST /api/v1/work-orders`
- `POST /api/v1/work-orders/{workOrderId}/release`
- `GET /api/v1/work-orders/{workOrderId}`
- `POST /api/v1/agent-tools/get-work-order`
- `GET /health/live`
- `GET /health/ready`
- `GET /`：可视化生产控制台HTML

API支持内存适配器和PostgreSQL持久化适配器。模型网关尚未连接，当前Agent工具是可审计、可授权的确定性工具调用，不是让大模型直接修改数据库。

## 数据库开发

复制`.env.example`为`.env`并修改本地密码，然后执行：

```powershell
docker compose up -d postgres
.\.venv\Scripts\alembic.exe upgrade head
```

首个迁移创建`work_orders`、`idempotency_records`、`event_outbox`和`agent_tool_audits`。`.env`不会进入Git。

单次运行Outbox发布Worker：

```powershell
.\.venv\Scripts\python.exe -m autonomous_mes.worker --batch-size 50
```

Worker通过数据库租约领取事件；失败会指数退避，超过上限进入`QUARANTINED`，支持陈旧租约恢复和带操作者/原因的人工重放。

启动服务后访问`http://127.0.0.1:8000/`，可以查看工单指标、制造事件流并创建演示工单。该页面使用原生HTML/CSS/JavaScript，无需Node构建环境。

### 本机D盘免安装环境

当前开发机的PostgreSQL 17安装在`D:\PostgreSQLPortable`，仅监听`127.0.0.1:55432`。一条命令可启动数据库、执行迁移并运行API：

```powershell
.\scripts\start-api-postgres.ps1
```

这个本机配置使用`trust`认证且不监听外网，只适合个人开发环境，不能照搬到测试或生产环境。生产环境必须使用凭据、TLS、网络隔离和最小权限角色。

运行真实PostgreSQL专项测试：

```powershell
$env:AUTONOMOUS_MES_POSTGRES_TEST_URL = "postgresql+psycopg://mes@127.0.0.1:55432/agentic_mes"
.\.venv\Scripts\python.exe -m unittest tests.test_postgresql_integration -v
```

该测试覆盖事务回滚和两个Outbox Worker的竞争领取；未设置测试连接时会安全跳过。
