# Autonomous MES Core

当前垂直切片覆盖创建/释放工单，机械加工工序的派工、开工、报工、完工，以及设备台账和实时遥测采集；每个业务动作都会原子写入Outbox，并支持Agent只读`get_work_order`工具。

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
- `POST /api/v1/work-orders/{workOrderId}/operations/{sequence}/dispatch`
- `POST /api/v1/work-orders/{workOrderId}/operations/{sequence}/start`
- `POST /api/v1/work-orders/{workOrderId}/operations/{sequence}/report`
- `POST /api/v1/work-orders/{workOrderId}/operations/{sequence}/complete`
- `POST /api/v1/work-orders/{workOrderId}/operations/{sequence}/resume`
- `GET /api/v1/work-orders/{workOrderId}`
- `POST /api/v1/equipment`
- `GET /api/v1/equipment`
- `POST /api/v1/equipment/{equipmentId}/telemetry`
- `POST /api/v1/agent-tools/get-work-order`
- `POST /api/v1/agent/incidents/analyze`
- `GET /api/v1/agent/proposals`
- `POST /api/v1/agent/proposals/{proposalId}/approve`
- `GET /health/live`
- `GET /health/ready`
- `GET /`：可视化生产控制台HTML

API支持内存适配器和PostgreSQL持久化适配器。DeepSeek 可通过供应商中立模型网关提供诊断解释；模型只接收最小化的结构化设备/工单事实，不获得数据库连接和 MES 工具。未配置密钥或调用失败时自动回退到规则解释。

## 数据库开发

复制`.env.example`为`.env`并修改本地密码，然后执行：

```powershell
docker compose up -d postgres
.\.venv\Scripts\alembic.exe upgrade head
```

迁移创建`work_orders`、`idempotency_records`、`event_outbox`和`agent_tool_audits`，并在工单中持久化已冻结的生产工序路线。`.env`不会进入Git。

单次运行Outbox发布Worker：

```powershell
.\.venv\Scripts\python.exe -m autonomous_mes.worker --batch-size 50
```

Worker通过数据库租约领取事件；失败会指数退避，超过上限进入`QUARANTINED`，支持陈旧租约恢复和带操作者/原因的人工重放。

启动服务后访问`http://127.0.0.1:8000/`，可以查看工单指标、制造事件流、设备状态，创建演示工单并模拟机床采集。遥测样本使用`sampleId`去重，旧时间戳不能覆盖当前状态；`DOWN`和`ALARM`必须携带停机原因或报警码。该页面使用原生HTML/CSS/JavaScript，无需Node构建环境。

派工必须选择同一工作中心内状态为`IDLE`或`RUNNING`的已注册设备。绑定设备上报`DOWN`或`ALARM`后，正在执行的工序和工单会自动进入`SUSPENDED`并产生联锁事件。设备未恢复时禁止复工；恢复为健康状态后仍需由人员或受控Agent明确调用`resume`，系统不会因一次正常心跳自行恢复生产。

第一代异常处置Agent采用“规则决策内核 + 可替换模型解释层”的设计。没有模型API时仍可自动观察暂停工单、设备版本和健康状态，生成去重且持久化的诊断提案。`HOLD_AND_INSPECT`只记录观察结论；`RESUME_OPERATION`属于`R2`动作，必须由主管填写原因并批准，执行前会再次验证设备健康、工单版本和幂等键。模型只能增强诊断说明，不能绕过这些确定性安全规则。

启用 DeepSeek 时只需在本机 `.env` 设置 `AUTONOMOUS_MES_DEEPSEEK_API_KEY` 并重启 API。默认使用 `deepseek-v4-flash`、JSON 输出、关闭思考模式和 12 秒超时。每条提案记录 `narrativeSource` 与 `modelName`；不要把真实密钥写入仓库。

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
