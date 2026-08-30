# 第三步最小核心切片验收记录

验收日期：2026-08-30  
结论：领域与应用垂直切片通过；PostgreSQL适配尚未开始。

## 已实现

- 创建DRAFT工单并验证数量、优先级、时区和冻结版本引用。
- 使用幂等键防止重复创建。
- 按预期版本将DRAFT工单释放为RELEASED。
- 使用乐观锁拒绝过期版本释放。
- 状态变化与Outbox事件在内存适配器的同一临界区原子保存。
- 查询工单时返回`asOf`、数据新鲜度和来源对象。
- Agent `get_work_order`工具实施车间scope授权。
- Agent工具允许、拒绝和未找到结果写入审计事件。
- MES核心没有模型依赖；不调用DeepSeek也可运行和测试。

## 自动验证

- 标准库单元测试：7/7通过。
- Python源码编译检查：通过。
- 代码事件与制造事件Schema对照：缺失0。
- 已验证事件：WorkOrderCreated、WorkOrderReleased、AgentToolCallRecorded。

## 尚未声称完成

- 当前为内存持久化，重启后数据丢失，不是生产MES。
- FastAPI依赖尚未在当前工作站安装和启动。
- PostgreSQL、Alembic迁移、真实事务Outbox worker尚未实现。
- OIDC/Keycloak尚未接入；API中的demo scope只用于开发验证。
- DeepSeek和模型网关尚未接入。

## 下一垂直切片

增加PostgreSQL 17适配器、首个Alembic迁移和数据库集成测试，使“工单+Outbox+幂等键”在真实数据库事务中成立。然后安装隔离Python 3.12环境，启动FastAPI并运行HTTP契约测试。
