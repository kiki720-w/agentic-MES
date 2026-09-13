# L4 排产自治运行时第一版

日期：2026-09-13。桌面版本：0.5.0。

CAPAXION 的产品目标已调整为 L4 Agent。L4 在这里指：人预先配置自治范围，Agent 在该范围内持续观察、规划、执行、核验和恢复；超出边界或结果不确定时转人工。它不代表模型可以直接写数据库、控制 PLC/CNC 或绕过质量与安全联锁。

## 本次实现

`SchedulingAutonomyRuntime` 完成排产域的第一条自治循环：

1. 读取最新排产输入并用 SHA256 指纹固定版本。
2. 调用确定性有限产能 APS 生成方案并提交策略检查。
3. 独立策略引擎检查任务规模、延期、能力缺口、加班、工时标准、快照来源与时效、物料、质量冻结、工艺完整性和已有发布计划。
4. 执行前再次计算输入指纹；变化时停止，不使用过时计划。
5. 使用幂等键调用专用执行适配器，并传递预期业务源版本。
6. 回读核验执行结果；核验失败时调用回滚。
7. 远端核验成功后，由独立策略主体记录批准，再由执行主体发布本地计划状态。
8. 重复事件复用已发布方案，不重复执行。
9. 保存最近 50 次运行状态于当前进程，提供紧急停止与恢复接口。

生成主体、策略主体和执行主体分别为 `scheduling-agent-l4-v1`、`scheduling-autonomy-policy-v1` 和 `scheduling-autonomy-executor-v1`。模型不拥有这些身份，也不能自行改变策略。

## 三种运行模式

| 模式 | 行为 |
|---|---|
| `SHADOW` | 完成观察、排产和全部策略检查，只记录 `WOULD_EXECUTE`，不调用执行适配器。默认模式。 |
| `SUPERVISED` | 通过策略后停在 `AWAITING_HUMAN`，用于上线前人工监督。 |
| `AUTONOMOUS` | 通过预授权策略后执行、回读核验并发布；失败回滚或进入 CRITICAL。 |

执行目标 `LOCAL_CORE` 会在版本和来源修订一致时，把方案发布到 CAPAXION 本地计划库并回读核验。它不代表已经写入外部 MES。外部系统仍需要客户专用适配器。

`AUTONOMOUS` 不等于一定执行。任何策略阻断、停止开关、输入版本变化、执行适配器缺失、回读失败或本地提交失败都会停止闭环。

## 当前预授权边界

默认要求：真实外部排产快照；快照不超过 300 秒；不得使用回退工时；不得新增延期、能力缺口或加班；最多 50 条分配和 20 个工单；无缺料、质量冻结或工艺缺失；不能替换已有发布计划，因为跨本地与外部系统的原子替换尚未实现。

仓库默认配置是 `SHADOW`、循环关闭、执行目标 `NONE`。当前本机试点环境已显式启用 `AUTONOMOUS + LOCAL_CORE`，循环仍关闭，只在用户明确下达执行或发布指令时运行。能力缺口、缺料、质量冻结、输入版本变化和未处理的已发布方案替换仍会停止执行。

只有显式启用 `SIMULATOR_MODE=true` 时才能选择 `SIMULATOR` 执行适配器。它只用于闭环自动测试，不连接真实 MES。配置成未知执行目标，或在非模拟环境选择模拟执行器，Core 会拒绝启动。

## 服务端配置

```dotenv
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MODE=SHADOW
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_LOOP_ENABLED=false
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_POLL_SECONDS=60
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_EXECUTION_TARGET=NONE
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MAX_ASSIGNMENTS=50
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MAX_AFFECTED_ORDERS=20
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MAX_LATE_ORDERS=0
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MAX_SHORTAGES=0
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_MAX_SNAPSHOT_AGE_SECONDS=300
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_ALLOW_OVERTIME=false
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_REQUIRE_EXTERNAL_SNAPSHOT=true
AUTONOMOUS_MES_SCHEDULING_AUTONOMY_REQUIRE_PROCESS_STANDARDS=true
```

本地试点可将模式设为 `AUTONOMOUS`、执行目标设为 `LOCAL_CORE`，并按工厂允许的批量规模调整数量上限。不得把 `LOCAL_CORE` 描述成外部 MES 写回。

白名单与自治配置只能由 Core 部署环境提供。桌面可以运行一次评估、查看证据，并由主管操作停止开关；不能把 SHADOW 改为 AUTONOMOUS，也不能增加执行权限。

API：

- `GET /api/v1/planning/autonomy`
- `GET /api/v1/planning/autonomy/runs`
- `POST /api/v1/planning/autonomy/run`
- `POST /api/v1/planning/autonomy/stop`
- `POST /api/v1/planning/autonomy/resume`

## 验证与尚未完成

自动测试覆盖自动执行与核验、幂等去重、影子模式、过期快照、停止/恢复、核验失败回滚、不确定执行结果进入 CRITICAL，以及演示数据、回退工时和非原子替换拒绝。当前使用模拟执行适配器完成闭环测试。

本版发布验证结果：Ruff、Mypy 和 Vite 构建通过；Core 测试为 167 项通过、5 项因未配置隔离 PostgreSQL 测试库而跳过；打包后的七个桌面页面全部完成加载和滚动检查，没有前端运行时异常。0.4.5 在真实《车工计划.xlsx》中识别 3 个工作表，并把“专线列表及工时”映射为 8 名可写入人员，4 名缺少产能数值的人员明确跳过；隔离内存库中的写入、回读和指定人员排产完成验证。便携安装包为 `desktop/release/CAPAXION-0.4.5-x64.exe`，SHA256 为 `F8AEABB78B98A0BC838DC317B5768368236077257DD0F9C7DDB8A53F9018BE96`。

这仍是 **L4 运行时第一版，不是生产 L4 验收完成**。本地 PostgreSQL 已开放人员能力、工单和排产结果的受控写入；真实云 MES 仍需要客户专用写回适配器、凭据轮换、远端计划替换事务和现场验收。持久化运行账本、多实例任务锁、通知升级和长期影子运行也尚未完成。
