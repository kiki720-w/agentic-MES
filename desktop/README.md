# CAPAXION Desktop

CAPAXION 的 Windows 桌面客户端。当前版本提供统一桌面导航、Agent 对话工作区、附件选择与文件拖放、Core 状态监测，以及原生控制塔、有限产能排产、人员周计划、产能管理和模型设置。拖入的 XLSX/CSV 会进入本地预检；PDF/图片只登记为附件，尚未解析内容。

## 本机开发启动

```powershell
cd D:\mes\desktop
pnpm install
pnpm dev
```

桌面端会先检查 `http://127.0.0.1:8000/health/ready`。若 Core 未运行，它会尝试使用 `D:\mes\core\.venv` 自动启动 FastAPI。默认界面缩放为 110%，标题栏可在 100%—140% 之间调整。

## 构建便携版

```powershell
pnpm desktop:build
```

输出位于 `desktop/release/`。当前便携版用于本机产品验证，正式分发前需要把 Python Core 打包为 Tauri/Electron sidecar、接入 Windows 凭据库并配置代码签名。
