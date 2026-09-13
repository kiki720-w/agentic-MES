# CAPAXION 新对话启动卡

更新时间：2026-09-13

项目：`D:\mes`

主续接文档：`D:\mes\PROJECT_HANDOFF.md`
GitHub：https://github.com/kiki720-w/capaxion

## 使用方法

在新的 Codex/ChatGPT 对话中打开同一个项目目录，然后把下面整段作为第一条消息发送。完整决策、架构、能力真相、安全边界和开发顺序都保存在 `PROJECT_HANDOFF.md`，本卡只负责让新对话从正确位置继续。

## 可直接复制的启动消息

> 继续开发 `D:\mes` 中的 CAPAXION（Manufacturing Decision OS）。请先完整读取 `D:\mes\PROJECT_HANDOFF.md` 和 `D:\mes\README.md`，再运行 `git status --short`、`git log -5 --oneline` 并核对现有测试与运行状态。不要重新设计成传统 MES；继承连接既有 ERP/MES/WMS/QMS/设备、统一制造数字镜像、确定性有限产能 APS、L3 BOUNDED 虚拟员工“衡策”、审批治理、本地优先和供应商中立模型网关的方向。当前 Windows 桌面版为 Electron + React 原生界面，便携程序位于 `D:\mes\desktop\release\CAPAXION-0.2.0-x64.exe`。必须遵守真实能力边界：XLSX/CSV 已有本地确定性解析，PDF/图片目前只可选择但尚未理解；DeepSeek `deepseek-v4-flash` 已配置，但最近一次实测降级为 `RULES`，模型状态为 `DEGRADED/ValueError`；当前也还不是完整多轮会话。不得记录、打印或提交任何 API Key，不得让大模型直接写数据库、批准自己的方案、发布排产或控制设备。先处理 `PROJECT_HANDOFF.md` 中的“当前最小验收门”，完成所有不需要我决策的工作并验证、提交、同步 GitHub；只有涉及产品方向、首个真实系统、企业身份提供商、云端数据出厂策略或不可逆操作时再向我确认。

## 快速核对标记

- 正确品牌：CAPAXION
- 正确定位：制造决策操作系统，不是新造传统 MES
- Agent：衡策，L3 BOUNDED
- 当前基线：`main @ 898390f`
- 当前优先事项：模型真实连通验收、能力自检、附件能力诚实标注
- 不得暴露：API Key、客户订单、图纸、工艺秘密和生产凭据
