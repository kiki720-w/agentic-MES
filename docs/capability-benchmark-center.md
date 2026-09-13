# 制造业务 AI 能力验收中心

日期：2026-09-13。桌面版本：0.2.1。

新增原生侧栏“AI 能力验收”及工作台入口。将文字、XLSX/CSV、PDF、图片、连续会话、真实执行安全分别展示，明确区分可测评、已接入但本中心未测、未接入。没有将文字成绩解释成文件或视觉能力。

## 基准和评分

manufacturing-text-v1 包含 9 道固定合成题：工单数量与编号、缺失交期、剩余工时、正常产能缺口、秒/分钟换算、ERP/MES 冲突、备注提示注入、质量冻结、只读权限理解。

每题保存问题、合成事实、标准答案、确定性核对依据、脱敏实际回答、来源、耗时、错误类别及判定。严格匹配答案，仅忽略两端空白和句末句号。题目要求固定格式；这是可重复的基础任务测试，不是开放式推理能力或生产准确率认证。权限理解题不调用任何生产动作，也不代替真实权限、maker-checker 和发布联锁测试。

报告包含试卷版本、样本指纹、评分版本、操作者、时间、模型、端点指纹和请求参数。只有相同试卷、样本、评分规则的已完成报告允许对比。界面显示逐题结果、同题通过率、请求失败数与中位延迟，不自动估算成本、显存或综合幻觉率。所有请求失败时标记运行失败，不给模型打 0 分。

## 模型隔离与本地边界

当前模型测评在开始时捕获适配器，运行中切换模型配置不会混入另一模型结果。本地候选使用独立临时适配器，不替换当前工作台配置，不继承云端 API Key，不回退云端。地址仅接受字面量 http://127.0.0.1:1024-65535/v1，不接受 DNS、凭据、查询、片段或公网地址；请求禁用环境代理，且不跟随 HTTP 重定向。

本地候选的超时为每题 30 秒。DeepSeek 使用当前解释路径的 JSON / 非思考模式，其他兼容模型遵循自身默认输出模式；报告记录参数差异，不宣称云端与本地推理配置完全相同。模型服务自身是否代理外部请求仍需独立网络审计。

本机为 NVIDIA GeForce RTX 4060 Laptop GPU（8188 MiB，驱动 576.52）。最初无本地模型服务；本次已下载并 SHA256 校验 [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) 的 [bartowski Q4_K_M 量化权重](https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF)，以及官方 llama.cpp b10936 Windows Vulkan 运行时。量化包为社区发布，不是 Qwen 官方量化包。精确版本、地址与 SHA256 固定在 setup-local-benchmark.py。

本机服务采用 Vulkan0（RTX 4060）、全部层 GPU offload、4096 上下文、单并发、--offline、--no-webui、--log-disable，只绑定 127.0.0.1:11434。启动不加载云端密钥，未改动当前 DeepSeek 配置。未安装系统服务或开机启动项。该本地模型仅用于文字对照，不提供图像理解。

运行方法（在仓库根目录）：

    core/.venv/Scripts/python.exe core/scripts/setup-local-benchmark.py
    ./core/scripts/start-local-benchmark.ps1

桌面本地候选地址填写 http://127.0.0.1:11434/v1，模型 ID 填写 qwen3-4b-instruct-2507-q4_k_m。停止用 ./core/scripts/stop-local-benchmark.ps1；该脚本核对记录 PID 的可执行文件路径后才停止，不删除模型。权重及运行时位于 .local-models/，被 Git 忽略。下载总计约 2.53 GB，只发生于显式执行安装脚本；桌面测评按钮本身不会下载模型。

## 运行与证据

测评后台串行运行，同时只允许一个任务，防止重复点击堆积模型调用。按题原子更新报告；Core 启动时把未结束报告标记中断，不自动重试云端调用。单 Core 进程部署，不承诺跨进程分布式任务队列。

报告保存在 core/.capaxion/benchmarks/，可在桌面查询最近 50 份并导出 JSON。只保存合成数据和移除已配置密钥后的回答；不读取生产数据库、真实附件，不提交报告至 Git。这是可核对的本地证据，不是防篡改审计系统。后续应增加报告保留策略、文件样本集和生产身份收口。

API：

- GET /api/v1/agent/capabilities
- POST /api/v1/agent/capability-runs（target=ACTIVE 或 LOCAL）
- GET /api/v1/agent/capability-runs/{runId}
- GET /api/v1/agent/capability-runs/compare?left=...&right=...

均复用服务端人工身份、角色和工厂范围校验。

## 验收

首轮真实 DeepSeek deepseek-v4-flash：9/9 通过，中位 1172 ms，零请求失败。运行 ID：0052b00b-95da-46c4-8ca4-af1758d7273d。只发送合成样本。

自动回归覆盖报告判定、实际回答脱敏、无模型回答不打分、跨版本不可比、UUID 路径校验、防重复、重启中断、固定模型适配器、本地不带 Authorization/不使用环境代理、拒绝公网及含凭据地址。全量回归 112 passed / 5 skipped，Ruff 与 mypy 通过。最终 EXE 七页无前端异常，六个长页面滚动通过；真实测评、逐题展开、同题对比通过。测试脚本使用主机端轮询与焦点仿真，避免隐藏窗口计时器限速造成挂起。

便携版：desktop/release/CAPAXION-0.2.1-x64.exe
SHA256：83A9E7E8405BB70B09F5F0B171D399951FB9FC0D8D0B8794CD68FD340175AA94

默认 pnpm ui:smoke 不调用模型。CAPAXION_SMOKE_BENCHMARK=1 会通过桌面执行一轮真实 9 题测评，并检查逐题证据与历史对比；需已有一份同版报告作为比较对象。旧三题自检继续保留，报告与本基准分开。


## 本地与 DeepSeek 实测结论

DeepSeek 三轮均 9/9。用于最终对比的 DeepSeek runId 为 2ed05ef5-180f-490e-812c-3a88ea7b3902，中位耗时 1083 ms。

Qwen3 4B Q4_K_M 两轮均 6/9（66.7%），没有请求失败：

- 首轮 31100453-a621-409f-9bcb-456dcf752aa9，中位 293 ms，首次推理首题 7555 ms。
- 复测 729ee81f-23bd-440a-a8ca-9b877d0d9329，中位 313 ms。

两轮相同错误：剩余需求 30+(20-5)×12 应为 210，回答 150；90秒×40件÷60 应为 60 分钟，回答 120；缺少来源版本优先级时应要求核对，模型擅自采用数量 25。不是仅格式不符合，而是数值或事实裁定错误。

因此此本地候选在这套基础试卷上未达到当前 DeepSeek 表现，虽然热运行响应更快。结果不能推广为所有本地模型、所有量化级别或所有业务的结论。后续应扩展独立样本、分开评估模型本身和确定性工具辅助路径；保持 APS、工时计算、权限、审批和执行由代码负责。

推理后抽样全 GPU 已用显存约 4241 MiB（包含桌面等其他进程），不冒充模型独占或峰值显存。下载结束后以离线标志运行；尚未完成整个产品的数据零出站认证。

本地与 DeepSeek 逐题对照截图位于 desktop/release/ui-local-comparison/comparison.png（本机保留）。CAPAXION_SMOKE_BENCHMARK=existing 可仅验证已有报告对比，不再次调用模型。
