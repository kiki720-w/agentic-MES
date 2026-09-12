# 第三十九步：供应商中立模型网关与 BYOK 设置

## 本步结论

原先在进程启动时固定创建的 DeepSeek 适配器，已替换为可热切换的模型网关。管理员可在 /settings/models 为当前工厂部署配置 DeepSeek、Kimi / Moonshot 或其他兼容 OpenAI Chat Completions 的服务。

## 功能边界

- 配置项包括供应商标识、API Base URL、模型 ID、超时与 API Key。
- 可在启用前执行真实连接测试；测试失败时不替换当前可用模型。
- 新配置对当前服务器进程立即生效，Agent 问答与异常解释会使用新供应商。
- API Key 只提交给服务器，不通过状态 API 回显，不写入浏览器存储。
- 只有 SUPERVISOR 或 MASTER_DATA_ADMIN 可以修改或停用模型。
- 停用远程模型后，Agent 自动回退到确定性规则摘要。
- 运行时修改不会替代正式部署的密钥管理。服务器重启后仍以 AUTONOMOUS_MES_MODEL_PROVIDER、MODEL_API_KEY、MODEL_NAME、MODEL_BASE_URL 与 MODEL_TIMEOUT_SECONDS 等部署环境变量为准。

## 不随模型改变的安全约束

模型供应商不拥有数据库连接、排产发布权、审批权或设备控制工具。有限产能计算、版本校验、maker-checker 审批、联锁与最终执行继续由现有确定性代码负责。

## 验收

- 模型适配器契约测试覆盖供应商切换、Markdown JSON 解析和密钥不回显。
- API 契约测试覆盖管理员修改、计划员拒绝、停用与设置页面。
- 页面在 Agent 工作台、控制塔、排产中心、排产结果和产能管理中提供“模型设置”入口。
