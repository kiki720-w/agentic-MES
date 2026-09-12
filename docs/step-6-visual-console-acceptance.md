# 第六步可视化控制台验收记录

日期：2026-09-12
结论：通过，可作为阶段一开发与演示控制台。

## 已实现

- 响应式单页HTML生产控制台，无Node构建依赖。
- MES健康状态与当前持久化后端展示。
- 工单总数、已释放工单、待发布事件和Agent审计指标。
- 工单列表及产品版本、数量、交期、状态和乐观锁版本。
- 最近100条Outbox制造事件流。
- 创建传动轴演示工单表单。
- 每10秒自动刷新和手动刷新。
- 页面与FastAPI同源，避免额外CORS配置。
- DOM使用`textContent`填入动态数据，降低事件数据注入HTML的风险。

## 新增只读接口

- `GET /api/v1/work-orders?limit=100`
- `GET /api/v1/system/outbox`
- `GET /` 返回生产控制台

系统级Outbox接口当前用于开发环境；接入OIDC后必须限制为管理员或审计角色。

## 验证

- 自动测试：19项通过。
- Ruff：通过。
- Mypy strict：通过。
- 实际HTTP根页面：200。
- 健康状态：READY。
- 创建演示工单：成功。
- Agent授权查询并产生审计事件：成功。

## 启动

在`D:\mes\core`运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn autonomous_mes.api:app --host 127.0.0.1 --port 8000
```

访问`http://127.0.0.1:8000/`。
