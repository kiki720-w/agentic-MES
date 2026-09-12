# 第二十三步：OIDC身份与工厂权限边界验收

结论：通过。MES API 已具备供应商中立的OIDC令牌验证边界，并提供可选Keycloak开发参考；L3审批执行不再信任请求体自报身份。

## 实现范围

- `DEV`模式：只供本机演示，使用环境变量定义的明确开发身份。
- `OIDC`模式：从Authorization Bearer Token解析真实主体。
- 固定接受`RS256`，校验签名、`iss`、`aud`、`exp`和`sub`。
- 支持从嵌套claim映射角色，默认`realm_access.roles`。
- 支持从`factory_ids`映射工厂范围。
- `GET /api/v1/identity/me`返回当前已验证身份、角色和工厂范围。
- L3审批要求`SUPERVISOR`角色、当前工厂scope和原有审批人白名单。
- 审批人ID由令牌`sub`注入，审批请求体只保留原因。

## Keycloak参考部署

`core/compose.keycloak.yaml`使用Keycloak 26.7.3和realm启动导入，仅用于开发验证。realm包含：

- API bearer-only client：`capaxion-api`
- Web public client：`capaxion-web`
- API audience mapper
- `factory_ids`工厂scope mapper
- `SUPERVISOR`、`PLANNER`、`OPERATOR`、`QUALITY`、`MASTER_DATA_ADMIN`角色

启动前必须在shell设置临时管理员密码：

```powershell
$env:KEYCLOAK_ADMIN_PASSWORD = "replace-with-a-local-secret"
docker compose -f compose.keycloak.yaml up -d
```

随后在Keycloak管理台创建用户并分配角色。仓库不附带默认用户密码。

MES切换示例：

```dotenv
AUTONOMOUS_MES_AUTH_MODE=OIDC
AUTONOMOUS_MES_OIDC_ISSUER=http://127.0.0.1:8081/realms/capaxion
AUTONOMOUS_MES_OIDC_AUDIENCE=capaxion-api
```

## 验证结果

- 有效RS256令牌可映射用户、角色和多个工厂范围。
- 错误audience被拒绝。
- 过期令牌被拒绝。
- 缺少主管角色或当前工厂scope时，授权检查被拒绝。
- 向审批请求体伪造`actorId`会因额外字段被拒绝。
- 原有MES领域、持久化、Agent、连接器测试保持通过。

## 保持的安全边界

OIDC只解决“谁在操作”和“属于哪个工厂”，不会放宽Agent动作能力。当前L3仍只允许已批准的设备恢复后复工，不允许模型直接控制PLC/CNC、改工艺参数、发布NC程序或质量放行。

## 下一决策门

在扩大任何L3动作前，应先决定是否保持“仅复工”白名单进入现场影子试运行，或新增第二种可执行动作。建议先保持仅复工，采集误报率、审批通过率、执行成功率和人工回退数据后再扩大权限。
