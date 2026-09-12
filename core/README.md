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
- `GET /api/v1/work-orders-summary`：全量工单状态聚合
- `POST /api/v1/equipment`
- `GET /api/v1/equipment`
- `GET /api/v1/quality/eligible-operations`：全库已完工未检工序分页队列
- `GET /api/v1/system/operation-projection-health`：JSON与关系工序在线一致性巡检
- `POST /api/v1/equipment/{equipmentId}/telemetry`
- `POST /api/v1/genealogy/product-units`
- `GET /api/v1/genealogy/product-units/{productSerial}`
- `POST /api/v1/genealogy/product-units/{productSerial}/execution-sessions`
- `POST /api/v1/agent-tools/get-work-order`
- `POST /api/v1/agent-tools/list-quality-candidates`：按车间授权并审计的R1只读候选池
- `POST /api/v1/agent/incidents/analyze`
- `POST /api/v1/agent/chat`：基于 MES 实时快照的查询，以及受控自然语言动作提案
- `GET /api/v1/agent/model-status`
- `GET /api/v1/agent/proposals`
- `POST /api/v1/agent/proposals/{proposalId}/approve`
- `GET /api/v1/identity/me`
- `GET /health/live`
- `GET /health/ready`
- `GET /`：可视化生产控制台HTML

API支持内存适配器和PostgreSQL持久化适配器。DeepSeek 可通过供应商中立模型网关提供诊断解释；模型只接收最小化的结构化设备/工单事实，不获得数据库连接和 MES 工具。未配置密钥或调用失败时自动回退到规则解释。

产品序列号谱系使用只追加的`product_units`和`genealogy_links`保存。登记序列号时，从工单冻结快照固化工单、产品版本、工艺路线、BOM、图纸，以及已绑定的工序和设备关系；追溯查询同时聚合该工单的质量检验结果。页面“事件追溯”区支持按序列号查询。

加工会话由上游采集端提供稳定`sessionId`作为幂等键，记录操作人员、实际设备、开始/结束时间和物料批次消耗。设备必须与工序派工结果一致，工序必须已经完工；会话、物料消耗、谱系链接和Outbox事件在同一数据库事务写入。

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

显式启用事件驱动质量Agent并持续消费：

```powershell
.\.venv\Scripts\python.exe -m autonomous_mes.worker --quality-agent-auto-draft --watch --poll-seconds 1
```

它只在`OperationCompleted`后生成去重、可追溯的R2质检建议草稿。已有检验时跳过；重复投递不会重复建草稿。该Worker不创建检验、不隔离产品，也不批准返工。未提供`--quality-agent-auto-draft`时保持普通事件发布行为。

启动服务后访问`http://127.0.0.1:8000/`，可以查看工单指标、制造事件流、设备状态，创建演示工单并模拟机床采集。遥测样本使用`sampleId`去重，旧时间戳不能覆盖当前状态；`DOWN`和`ALARM`必须携带停机原因或报警码。该页面使用原生HTML/CSS/JavaScript，无需Node构建环境。

派工必须选择同一工作中心内状态为`IDLE`或`RUNNING`的已注册设备。绑定设备上报`DOWN`或`ALARM`后，正在执行的工序和工单会自动进入`SUSPENDED`并产生联锁事件。设备未恢复时禁止复工；恢复为健康状态后仍需由人员或受控Agent明确调用`resume`，系统不会因一次正常心跳自行恢复生产。

第一代异常处置Agent采用“规则决策内核 + 可替换模型解释层”的设计。没有模型API时仍可自动观察暂停工单、设备版本和健康状态，生成去重且持久化的诊断提案。`HOLD_AND_INSPECT`只记录观察结论；`RESUME_OPERATION`属于`R2`动作，必须由主管填写原因并批准，执行前会再次验证设备健康、工单版本和幂等键。模型只能增强诊断说明，不能绕过这些确定性安全规则。

自然语言接口已开放第一项受控动作：输入“请恢复工单 WO-...”时，系统先要求明确工单号，再读取实时状态并调用异常处置Agent生成或复用`RESUME_OPERATION`提案。响应策略为`REQUIRE_APPROVAL`，不会在对话请求中执行复工；只有主管在提案区审批、且执行时安全检查仍通过，MES应用服务才会改变生产状态。其他未显式开放的写指令继续被策略层拒绝。

输入“分析当前设备异常并生成建议”可通过自然语言触发全局异常扫描。Agent对暂停工单形成去重提案：设备健康时生成`PENDING_APPROVAL`复工提案，设备异常时生成`OBSERVED`保持停机结论。该安全自动化会记录分析结果和Outbox事件，但不会直接改变工单或设备状态。

阶段一默认运行在`L2`：即使数据库中存在待审批复工提案，Agent审批执行端点也会拒绝生产状态变更，页面不显示“审批执行”。实验性L3必须显式设置`AUTONOMOUS_MES_AGENT_L3_EXECUTION_ENABLED=true`，仅用于隔离验证环境。自然语言“为工单 WO-... 的 OP 10 生成检验建议”只创建去重、可追溯的R2建议草稿；真正创建检验、记录结果、隔离和返工仍由质量角色在业务页面完成。

加工会话可同时冻结刀具、夹具和NC程序实绩快照。刀具必须可用且剩余寿命大于零，夹具必须可用，NC程序必须携带已发布版本；不满足条件的数据会在进入谱系前被领域规则拒绝。

质量结果必须绑定量具、测量时间和校准有效期。量具在测量发生时已经过期会被拒绝；通过后的量具证据随检验记录和产品谱系返回。

Agent可通过`POST /api/v1/agent-tools/get-product-genealogy`读取序列号谱系。该工具为`R1`敏感只读，先由关联工单解析车间范围，再执行授权；允许、拒绝和对象不存在均写入`AgentToolCallRecorded`，模型不能绕过工具直接查询数据库。

制造资源主数据中心提供`POST/GET /api/v1/master-data/manufacturing-resources`和版本化状态更新端点。每条刀具、夹具、NC程序或量具记录都带权威来源、外部引用、来源更新时间和MES版本；较旧来源时间戳与并发旧版本均不能覆盖新数据。加工和检验服务只从该台账取得状态并冻结实绩快照，不接受客户端自报“可用”或“校准有效”。

控制台“制造资源”页面集中展示四类资源、权威来源、状态、版本、刀具寿命和量具校准期限，并提供内置MES主数据登记表单与类型筛选。

通用CSV接入提供模板、预检和确认导入三个端点。预检限制1 MB/500行、执行逐行领域校验和重复检查并返回内容指纹；确认导入必须提交同一指纹，整批资源与事件在单一事务中完成，避免文件预检后被替换或只导入部分行。

通用入站连接器`POST /api/v1/connectors/v1/manufacturing-resources`只接收标准模型，不访问客户URL或数据库。请求必须携带Key ID、Unix时间戳、唯一Nonce和HMAC-SHA256签名；签名覆盖原始请求体，时间窗口默认5分钟，Nonce持久化后不可重放。凭据仅通过环境变量配置，不进入页面、Agent上下文或仓库。

部署上下文固定为`FACTORY_EDGE`独立工厂实例，健康检查及`GET /api/v1/system/deployment-context`返回组织、工厂和专用数据库隔离声明。本地实例已显式启用`L3_EXPERIMENTAL`；审批执行除能力开关外还要求`AUTONOMOUS_MES_AGENT_L3_APPROVER_IDS`白名单，未授权actor即使知道提案ID也不能执行。

正式身份边界支持标准OIDC Bearer Token，并固定只接受`RS256`，强制校验签发方、受众、过期时间和subject。所有人工写操作都要求令牌角色与`factory_ids`工厂范围，审计actor由令牌`sub`注入，不再接受客户端自报身份；连接器继续使用独立HMAC凭据。页面内置Authorization Code + PKCE登录流程，访问令牌只保存在浏览器sessionStorage。本地演示显式使用`AUTONOMOUS_MES_AUTH_MODE=DEV`，不能用于生产。`compose.keycloak.yaml`和`keycloak/agentic-mes-realm.json`提供可选Keycloak开发参考；客户已有身份平台时只需按`.env.example`配置兼容OIDC的issuer、audience、JWKS和Web Client ID。

生产控制台按大样本场景改为工单服务端分页、编号搜索、状态筛选和数据库全量KPI聚合。模块切换只请求当前模块，浏览器页签不可见时暂停轮询，长列表元素启用延迟渲染。`GET /api/v1/work-orders`支持`limit`、`offset`、`query`和`status`，返回`total`；数据库迁移`0014`增加状态/更新时间查询索引。

事件追溯API只读取有界窗口，并支持基于`occurred_at + event_id`的游标翻页；页面通过“加载更早”逐批追加，不使用百万级深offset。迁移`0015`增加事件时间线复合索引。`scripts/scale-benchmark.py`只能在`agentic_mes_scale*`隔离schema内运行，已用于10万工单、100万事件实测。

设备、制造资源和质量任务列表支持数据库分页、搜索与状态/类型筛选，并分别提供全量汇总端点。控制台每页仅保留24条记录，迁移`0016`增加运营读模型复合索引。

迁移`0017`已建立`work_order_operations`独立工序表并回填原JSON。工单聚合写入会在同一事务内同步更新JSON兼容字段、关系工序、Outbox和幂等记录；质量页面的待检池改为全库数据库查询，不再受最近100个工单窗口限制。旧JSON仍作为兼容读路径保留，待一致性观测稳定后再决定是否移除。

工序兼容期已增加在线投影巡检：PostgreSQL在数据库内比较JSON与`work_order_operations`的数量和业务字段，返回`CONSISTENT`或`DRIFT_DETECTED`供监控告警。质量Agent只能通过`list_quality_candidates`按获授权车间分页读取待检候选；该R1工具的允许与拒绝都会审计，且没有创建检验或修改数据库的能力。

质量Agent可由独立Outbox Worker消费`OperationCompleted`事件，按当前权威状态自动生成`CREATE_QUALITY_INSPECTION`的R2/`OBSERVED`草稿。去重目标固定为工单与工序，事件重放或工单后续版本变化不会重复生成；提案事件保存源事件因果ID。自动化没有检验写接口，建议转检验仍需质量角色明确确认。

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
