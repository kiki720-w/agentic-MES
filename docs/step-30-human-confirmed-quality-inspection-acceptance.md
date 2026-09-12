# 第三十步：人工确认Agent建议并创建检验验收

结论：具备`QUALITY`角色的人员可以在Agent页面确认一条`CREATE_QUALITY_INSPECTION`建议，并将其转成真实检验任务。确认动作使用确定性状态复核、数据库唯一约束和单事务写入；Agent不能调用确认能力，也不能自行创建检验。

## 用户流程

1. `OperationCompleted`触发质量Agent生成R2/`OBSERVED`草稿。
2. 质量人员在Agent页面点击“确认并创建检验”。
3. 页面要求输入正整数抽样数量并再次确认。
4. API从当前身份令牌取得操作者，只接受`QUALITY`角色。
5. 服务重新读取提案、工单、工序和已有检验，不采用浏览器或模型自报状态。
6. 检验创建与提案变为`EXECUTED`在一个数据库事务内提交。
7. 页面刷新后，真实`OPEN`检验出现在质量任务列表，原提案不再显示确认按钮。

新增接口：

`POST /api/v1/agent/proposals/{proposalId}/create-inspection`

请求只包含`sampleSize`和人工确认原因。操作者来自认证身份，客户端不能伪造。

## 原子性与幂等性

- 提案必须是`CREATE_QUALITY_INSPECTION`且状态为`OBSERVED`。
- 工序在执行时必须仍为`COMPLETED`。
- 相同`work_order_id + operation_sequence`最多存在一条检验。
- 迁移`0018_unique_quality_target`建立数据库唯一索引，关闭并发穿透窗口。
- SQL事务同时更新提案、插入检验并写入两个Outbox事件；任一步失败则全部回滚。
- 重复确认已执行提案时返回原检验，不重复写表或事件。
- 直接发起检验也执行相同目标去重规则。

## 审计链

同一事务写入：

- `QualityInspectionCreated`：负载包含`sourceProposalId`。
- `AgentProposalExecuted`：负载包含`inspectionId`、执行人、原因、工单和工序。
- 两个事件使用同一`correlationId`，可从任一侧还原完整确认链路。

## 验收结果

- Ruff通过。
- mypy通过，35个源文件无类型错误。
- HTML内嵌JavaScript语法检查通过。
- 66项自动化测试通过，包含非质量类提案不能进入检验确认通道的负向用例。
- SQLite完整迁移链通过，并验证唯一索引存在。
- 真实PostgreSQL迁移头为`0018_unique_quality_target`。
- 真实提案首次确认后创建检验`7969a98c-08ee-42dc-87d5-10974285487f`，状态为`OPEN`。
- 同一提案第二次确认返回同一检验ID，数据库中目标检验仍为1条。
- 真实双事件相关ID一致，事件已由持续Worker发布。
- 实际浏览器确认：`OBSERVED`质量建议显示“确认并创建检验”，已执行建议不再显示按钮；无权限时按钮保持可见但禁用，最终仍由服务器RBAC裁决。

## 安全边界

该功能是“人确认、系统执行”，不是Agent自主写入。Agent只产生建议；质量人员必须主动点击、输入抽样数量并确认。检验结果、产品隔离和返工批准仍保持原有独立权限及状态校验。

## 下一决策门

下一步可以为质量Agent加入确定性的风险评分和抽样方案推荐，让高风险工序优先展示并给出建议样本数。模型只解释评分，不决定放行或质量结果。由于评分规则将影响人员工作优先级，需要单独确认规则来源与启用范围。
