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

当前API使用内存适配器，只用于验证领域切片；下一切片将增加PostgreSQL/Alembic适配器。模型网关尚未连接，Agent工具可直接进行确定性契约测试。

## 数据库开发

复制`.env.example`为`.env`并修改本地密码，然后执行：

```powershell
docker compose up -d postgres
.\.venv\Scripts\alembic.exe upgrade head
```

首个迁移创建`work_orders`、`idempotency_records`、`event_outbox`和`agent_tool_audits`。`.env`不会进入Git。
