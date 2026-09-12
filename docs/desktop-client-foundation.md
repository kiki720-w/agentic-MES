# CAPAXION Windows 桌面客户端基线

日期：2026-09-13

## 目标

把 CAPAXION 从以网页为入口的原型推进为可独立启动的 Windows 制造决策工作台。桌面端采用 Agent 优先的信息结构，而不是复制传统 MES 的多层菜单和表格堆叠。

## 已完成

- Electron + React + Vite 桌面壳和 Windows x64 便携构建。
- 无边框窗口、统一侧栏、Agent 对话中心、运行状态与安全边界检查器。
- 控制塔、有限产能排产、排产结果、产能管理和模型设置已全部改造成直接调用 Core API 的 React 原生组件，不再嵌入旧 HTML 页面。
- 统一可读性基线：14px 基准字体、放大的输入与点击区域、默认 110% 缩放，并允许在 100%—140% 间即时调整。
- CAPAXION 专属 Windows 图标已用于应用窗口、任务栏、便携程序和品牌侧栏。
- 自动检查本地 Core；在本机便携目录或显式仓库路径中找到 Python 环境后可自动启动 Core。
- 通过窄 IPC 调用 Core；渲染进程启用 `contextIsolation`、关闭 `nodeIntegration` 和 `webview`。
- 文件选择由主进程完成，只有用户明确选择且不超过 5 MB 的 XLSX/CSV 才能进入排产预检。
- XLSX/CSV 先预检，界面再次人工确认后才写入可追溯快照并调用 L3 排产 Agent。
- 图片和 PDF 当前只保留本地选择状态；在本地多模态/文档解析器接入前不会伪装已理解文件内容，也不会自动发送到云端。

## 安全边界

- 桌面渲染层不能直接访问文件系统、数据库、Shell、PLC 或 CNC。
- API 路径被限制在 `/api/v1/` 和 `/health/`。
- 文件上传只允许本次由系统文件选择器返回的 XLSX/CSV 路径。
- 排产导入和方案生成仍使用现有角色校验、快照指纹、版本与审批规则。
- L3 Agent 不能批准或发布自己的方案，也不能直接执行设备动作。

## 当前产物

    D:\mes\desktop\release\CAPAXION-0.2.0-x64.exe

当前便携包用于这台电脑上的产品验证。正式交付客户前还需要完成 Core sidecar 安装、代码签名、自动更新、Windows 凭据库、生产 OIDC 和安装包升级/卸载策略。

## 验证记录

- Vite production build：通过。
- Electron main/preload 语法检查：通过。
- electron-builder Windows portable：通过。
- 本机启动：窗口标题 `CAPAXION`，Core `/health/ready` 返回 `READY`。
- 1280×720 界面渲染检查：侧栏、主会话、输入框和右侧状态区无重叠或截断。
- Windows 打包运行回归：修正 Electron 44 中 `scrollIntoView()` 返回对象被 React 误识别为 effect 清理函数导致的黑屏。
- Windows 无调试启动回归：窗口资源加载完成后显式显示，消除 `ready-to-show` 监听注册过晚造成的隐藏窗口竞态。
