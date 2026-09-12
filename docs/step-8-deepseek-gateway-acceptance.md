# 第八步 DeepSeek 诊断解释网关验收记录

日期：2026-09-12  
结论：代码、契约测试与安全降级通过；真实云端调用等待配置用户自己的 API Key。

## 设计边界

- DeepSeek 仅生成诊断解释和人工处置建议，不决定生产状态。
- 模型只接收当前工单、工序和设备的最小结构化事实。
- 不向模型提供数据库连接、写操作工具、审批工具、NC 程序或图纸。
- 模型输出使用 JSON 模式，进入系统前校验字段、空值和长度。
- 实际复工仍由现有规则层校验设备健康、工单版本、人员审批和幂等键。
- API 超时、网络错误、非 JSON 或非法字段全部回退到确定性规则解释。
- 未配置 API Key 时 MES 以 `RULES_ONLY` 运行，生产、采集、质量和追溯不受影响。

## 配置

真实密钥只写入未纳入 Git 的 `core/.env`：

```text
AUTONOMOUS_MES_DEEPSEEK_API_KEY=<your-key>
AUTONOMOUS_MES_DEEPSEEK_MODEL=deepseek-v4-flash
```

## 可审计性

Agent 提案新增 `narrativeSource` 和 `modelName`，控制台会显示 `DEEPSEEK · 模型名` 或 `RULES`，便于区分模型解释与降级结果。

## 验证

- DeepSeek 结构化响应契约测试：通过。
- 模型故障自动规则降级测试：通过。
- 全量自动测试：32 项通过。
- PostgreSQL 迁移 `0007_agent_model_metadata`：通过。
- Ruff 与 Mypy strict：通过。
