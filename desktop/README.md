# CAPAXION Desktop

CAPAXION 的 Windows 桌面客户端。首个版本提供统一桌面导航、Agent 对话工作区、附件选择、Core 状态监测和现有制造功能的嵌入入口。

## 本机开发启动

```powershell
cd D:\mes\desktop
pnpm install
pnpm dev
```

桌面端会先检查 `http://127.0.0.1:8000/health/ready`。若 Core 未运行，它会尝试使用 `D:\mes\core\.venv` 自动启动 FastAPI。

## 构建便携版

```powershell
pnpm desktop:build
```

输出位于 `desktop/release/`。当前便携版用于本机产品验证，正式分发前需要把 Python Core 打包为 Tauri/Electron sidecar、接入 Windows 凭据库并配置代码签名。
