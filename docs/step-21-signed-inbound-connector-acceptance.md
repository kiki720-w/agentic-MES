# 第二十一步：签名入站连接器验收

结论：通过。首版通用连接器采用工厂系统主动推送，MES不主动访问客户任意URL、数据库或文件共享。

## 协议

端点：`POST /api/v1/connectors/v1/manufacturing-resources`

请求头：

- `X-Connector-Key`：部署级连接器Key ID。
- `X-Connector-Timestamp`：Unix秒。
- `X-Connector-Nonce`：16至128位唯一安全字符串。
- `X-Connector-Signature`：HMAC-SHA256十六进制签名。

签名原文为`timestamp + "\n" + nonce + "\n" + rawBody`。服务器使用常量时间比较，防止时序侧信道。

## 防护

- 未配置Key、未知Key和错误签名拒绝。
- 默认只接受服务器时间前后5分钟内请求。
- Nonce写入PostgreSQL唯一主键，正确签名也不能重复使用。
- 签名通过后才解析业务载荷，载荷继续复用资源领域校验和原子批量写入。
- 请求体摘要随回执保存，密钥本身不落业务事件。

## 配置

使用`AUTONOMOUS_MES_CONNECTOR_KEY_ID`、`AUTONOMOUS_MES_CONNECTOR_HMAC_SECRET`和`AUTONOMOUS_MES_CONNECTOR_MAX_CLOCK_SKEW_SECONDS`。生产环境必须由密钥管理设施注入，不得提交`.env`。

## 下一决策点

单连接器凭据和统一资源模型已经验证。继续产品化需要选择多租户/多工厂隔离模型：单工厂独立部署，或一个平台实例承载多个工厂。该选择会改变所有表的租户键、密钥生命周期、权限域和商业部署方式，应在继续扩展连接器前冻结。
