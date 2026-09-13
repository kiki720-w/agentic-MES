# 最小 AI 文字能力验收

日期：2026-09-13。范围：现有 CAPAXION L3 BOUNDED 工作台、供应商中立解释网关和 Windows 原生桌面。

## 修复与证据

旧版一次真实调用曾以 ValueError 降级。本次用旧适配器执行合成连接测试成功，未重现历史错误；仅观察 HTTP 状态、结束原因、内容长度和 token 计数，没有输出响应原文或密钥。因此无法断言历史失败的唯一根因。

根据 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)，只读解释路径显式关闭 DeepSeek 默认思考模式并启用 JSON 输出。问答/诊断预算分别调整为 1536/1024 tokens。其他兼容服务不发送 DeepSeek 专用字段。解析器拒绝截断、空回答和非字符串字段，失败仍由现有规则路径接管，不把 reasoning_content 当成回答。

CONFIGURED 表示配置存在，VERIFIED 表示至少一次请求及返回结构验证成功，DEGRADED 表示最近请求失败，DISABLED 表示关闭。最近成功时间和失败次数属于当前内存适配器，重启或更换配置后重置。VERIFIED 不等于生产事实正确率通过。

## 合成文字基准

POST /api/v1/agent/capability-check 仅允许已授权人工身份。它直接使用只读模型网关和三个固定合成样本，不查询生产库，也不调用业务 chat/排产/审批服务。RULES/POLICY 结果不能计为模型通过。

- fact-reference：核对合成工单编号与数量。
- missing-fact：缺少交期时返回无法确定。
- arithmetic：20 + 12 × 5 = 80。

采用严格文本匹配（允许句末句号）；不把三项 smoke 结果宣传为统计准确率。桌面、网页工作台均可运行。每次报告保存于 core/.capaxion/capability-checks/，包含运行 ID、服务、模型、身份、时间、样本版本/指纹、回答 SHA256、逐项结论与耗时。目录被 Git 忽略，不保存模型原文、附件或密钥。这是本地可核对报告，不是防篡改审计；保留与清理策略尚未产品化。

真实 DeepSeek deepseek-v4-flash API 自检 3/3 通过；最终便携 EXE 点击入口再次通过 3/3：

- runId：6e885cf8-1c29-4858-95ee-a81ea41e0eea
- 时间：2026-09-13T04:30:20Z
- 耗时：1256 / 1526 / 766 ms
- source：每项 DEEPSEEK；最终网关状态 VERIFIED。

这些调用只发送合成数据。PDF/图片理解、完整多轮、生产准确率、成本和 LOCAL_ONLY 零出站尚未由本套三题验收。后续桌面 0.4.1 已接入本地 PDF 文字提取、图片/扫描 PDF OCR 和通用附件问答，但仍需独立文件样本集衡量准确率。

## 验证与运行

在仓库根目录运行，避免 core/.env 的本机数据库和模型设置影响离线测试：

    core/.venv/Scripts/python.exe -m pytest core/tests -q
    core/.venv/Scripts/ruff.exe check core/src core/tests
    core/.venv/Scripts/mypy.exe core/src

结果：101 passed，5 skipped；Ruff 和 mypy 通过。5 项 PostgreSQL 专项没有指定独立测试库，保留跳过。新增覆盖：截断/空内容/字段类型、降级恢复、失败候选不覆盖、报告证据、错误结果、接口权限与业务 chat 隔离。

本机 PostgreSQL 55432 启动成功，Alembic upgrade head 成功，Core 8000 为 READY/postgresql。正确启动路径为 core/scripts/start-api-postgres.ps1。

pnpm desktop:build 成功。最终便携程序为 desktop/release/CAPAXION-0.2.0-x64.exe，SHA256：

    E44190D6D42B7E6C4007B844EC4EAE2136A9ABDD7053AF59C79CDF447B3199EA

最终 EXE 六页无加载错误/前端异常，五个长页面鼠标滚动全部通过，自检点击链路通过。截图位于 desktop/release/ui-smoke-final/（本机保留，不提交）。运行现有 UI smoke 时可设 CAPAXION_SMOKE_SELF_CHECK=1 来额外执行三次真实合成模型调用；默认不调用模型。

下一步仍为第 40 步生产身份收口。企业身份提供商、真实系统和云端出厂策略需用户选择；本次未修改这些决策，也未连接真实生产系统。
