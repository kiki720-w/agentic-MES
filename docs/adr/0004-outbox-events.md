# ADR-0004：事务Outbox与事件交付

状态：Accepted  
日期：2026-08-30

## 决策

领域状态更新与`event_outbox`在同一PostgreSQL事务内提交。独立发布worker使用`FOR UPDATE SKIP LOCKED`领取事件，至少一次投递；消费者以`event_id`幂等。

原型阶段先使用PostgreSQL作为Outbox和内部事件通道，不引入Kafka。需要跨进程通知时可增加RabbitMQ适配器，但数据库Outbox仍是可靠来源。

核心字段：`event_id`、`event_type`、`schema_version`、`aggregate_type`、`aggregate_id`、`occurred_at`、`received_at`、`correlation_id`、`causation_id`、`payload`、`publish_status`、`attempts`、`next_attempt_at`。

## 保证

- 不承诺exactly-once；通过至少一次+幂等获得业务等效一次。
- 指数退避后进入隔离状态，保留错误并支持授权重放。
- 不允许在数据库事务提交前直接调用消息代理。
- 事件Schema必须通过JSON Schema验证。
- 事件保留期阶段一不少于一年；审计保留策略由客户法规进一步决定。

## 升级触发条件

当事件吞吐、多个独立消费者、跨站点复制或长时间流处理超出PostgreSQL worker能力时，再评估RabbitMQ/Kafka，不预先增加复杂度。

