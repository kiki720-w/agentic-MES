# ADR-0008：部署、可观测性与恢复

状态：Accepted  
日期：2026-08-30

## 决策

开发与演示采用Docker Compose描述API、worker、PostgreSQL和OIDC；代码在没有模型API的情况下也能启动。真实试点优先单厂本地部署，可按客户要求放在私有云。

组件：

- `api`：FastAPI无状态进程
- `worker`：Outbox发布、异步任务与重试
- `postgres`：事务、事件和审计
- `identity`：OIDC提供方
- `edge-simulator`：设备事件模拟，后续替换为边缘网关
- `model-gateway`：可与API同部署但保持代码边界

可观测性遵循统一`requestId/correlationId`，输出结构化日志、指标和追踪。关键指标包括API延迟/错误率、数据库池、Outbox积压、事件失败、设备数据新鲜度、Agent工具拒绝率、模型延迟/token/成本。

健康检查分为liveness和readiness；模型不可用不应使MES readiness失败。备份必须通过实际恢复验证，不以“备份任务成功”代替恢复证明。

## 当前环境

当前工作站未安装Docker。ADR完成不依赖Docker；进入可运行代码验收前，需要安装Docker Desktop/兼容运行时，或提供本机PostgreSQL与隔离Python环境。

