# ADR-0001：模块化单体

状态：Accepted  
日期：2026-08-30

## 决策

第一阶段采用单一部署单元、清晰模块边界的模块化单体，不拆分微服务。

模块：`identity`、`master_data`、`process_definition`、`production`、`equipment`、`quality`、`genealogy`、`events`、`agent_gateway`、`audit`。

每个业务模块内部按 `domain → application → infrastructure → api` 分层。领域层不依赖FastAPI、SQLAlchemy、DeepSeek SDK或设备协议；跨模块只能调用公开应用接口或消费事件，不读取其他模块的私有表。

## 理由

- 领域模型仍在快速验证，分布式事务会放大复杂度。
- 工单状态和Outbox需要强事务一致性。
- 单体便于本地部署、离线工厂运维和端到端测试。
- 清晰边界保留未来按吞吐量拆分设备采集、AI和时序服务的可能。

## 不采用

- 微服务：当前收益不足以覆盖部署、追踪、一致性和版本协调成本。
- 纯事件溯源：保留不可变事件，但事务表仍是业务状态主存储；避免所有查询依赖事件重放。
- 直接魔改某个开源MES：许可证和领域模型均不满足主底座要求。

## 拆分触发条件

只有出现独立扩缩容、独立安全域、独立发布周期或可量化的性能瓶颈，才提交新的拆分ADR。

