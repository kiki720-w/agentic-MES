# ADR-0006：API契约

状态：Accepted  
日期：2026-08-30

## 决策

- REST API前缀为`/api/v1`；破坏性变化提升主版本。
- JSON字段统一camelCase，数据库与Python内部可使用snake_case并显式映射。
- 写命令必须携带`Idempotency-Key`和`If-Match`/业务版本，防止重试和并发覆盖。
- 查询响应包含`asOf`、`dataFreshness`和来源对象；分页使用稳定cursor。
- 错误响应采用Problem Details形态，至少包含`type`、`title`、`status`、`code`、`detail`、`requestId`和字段错误。
- OpenAPI是外部契约，CI对破坏性变化执行检查。
- API层只调用应用命令/查询，不直接操作ORM。

主要状态码：200查询成功，201创建，202异步接受，204幂等无内容，400格式错误，401未认证，403无权限，404不存在，409状态/幂等冲突，412版本冲突，422业务校验失败，429限流，503依赖不可用。

Agent工具不是任意REST透传。每个工具对应固定应用查询或命令，限制字段、数量、超时和数据范围。

