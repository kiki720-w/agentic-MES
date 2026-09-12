# 第二十四步：全写操作RBAC与大样本控制台验收

结论：通过。正式身份边界已覆盖全部人工写操作，控制台不再假设工厂只有少量工单，并具备浏览器OIDC PKCE登录能力。

## 人工操作权限矩阵

| 操作 | 角色 | actor来源 |
|---|---|---|
| 创建、释放工单 | `PLANNER` | OIDC `sub` |
| 派工、开工、报工、完工 | `OPERATOR` | OIDC `sub` |
| 人工复工 | `SUPERVISOR` | OIDC `sub` |
| 登记产品、加工实绩 | `OPERATOR` | OIDC `sub` |
| 发起检验、记录结果 | `QUALITY` | OIDC `sub` |
| 批准返工 | `QUALITY`或`SUPERVISOR` | OIDC `sub` |
| 设备登记、制造资源维护/导入 | `MASTER_DATA_ADMIN` | OIDC `sub` |
| Agent异常分析 | `OPERATOR`或`SUPERVISOR` | OIDC `sub` |
| Agent L3审批 | `SUPERVISOR` + L3白名单 | OIDC `sub` |

每项操作还必须包含当前实例的`factoryId`范围。签名连接器仍使用HMAC和Nonce防重放，不混用人员令牌。

## 浏览器登录

- 页面从`/api/v1/system/auth-config`取得模式、issuer和公开Web Client ID。
- OIDC模式使用Authorization Code + PKCE S256。
- 登录state和PKCE verifier保存在sessionStorage并在回调时校验。
- Access Token只保存在sessionStorage，每次API请求自动附加Bearer Token。
- 角色不足时页面隐藏明显操作入口，API仍执行最终权限判定。
- DEV模式继续使用显式开发身份，便于当前本机演示。

## 大样本页面优化

- 工单列表改为每页30项的服务端分页。
- 支持工单编号模糊搜索和状态筛选。
- 测试工单由数据库查询默认排除，用户可显式切换包含，避免测试数据占满整页。
- KPI由数据库全量聚合，不再用当前页面记录估算。
- PostgreSQL新增`status + updated_at`和`updated_at`索引。
- 左侧工单列表、右侧工序详情固定在视口内独立滚动。
- 列表密度提高，避免单张工单卡占用过多高度。
- 只有当前模块会请求和渲染数据。
- 页面隐藏时暂停轮询，恢复可见时立即刷新。
- 自动刷新从15秒调整为30秒，降低大量客户端同时在线时的查询压力。
- 长卡片列表使用浏览器`content-visibility`延迟渲染。

## API契约

`GET /api/v1/work-orders`支持：

- `limit`：1—100，默认30
- `offset`：0—1,000,000
- `query`：工单编号搜索
- `status`：状态过滤

响应包含`items`、当前返回`count`、过滤后`total`、`limit`和`offset`。`GET /api/v1/work-orders-summary`返回全量总数、在制数、暂停数和各状态计数。

## 当前规模边界

工单主工作台已完成数据库级分页。设备、制造资源、质量和事件页面当前仍按最近100项进行有界加载，避免浏览器无限渲染；进入真实工厂压力测试前，应根据数据增长率为这些模块补充游标分页和专用聚合端点。

## 下一步

使用合成数据生成至少10万工单、1万资源、100万事件，测量P95查询时间、数据库CPU、页面首屏和轮询成本。达到该规模后，才能决定是否需要把offset分页升级为基于`updated_at + id`的游标分页，以及是否拆分事件查询库。
