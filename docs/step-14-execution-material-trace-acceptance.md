# 第十四步加工会话、人员与物料谱系验收记录

日期：2026-09-12  
结论：产品序列号谱系已从“机、法、测”扩展到操作人员、加工会话和物料批次。

## 已实现

- 新增`ExecutionSession`和`MaterialConsumption`领域对象。
- 新增`execution_sessions`、`material_consumptions`表及Alembic 0009迁移。
- 采集端必须提供稳定`sessionId`；重复会话ID被拒绝，避免网络重试造成重复用料。
- 会话必须引用已有产品序列号和已完工工序。
- 实际设备必须与该工序派工设备完全一致。
- 开始时间不能晚于结束时间；物料批次、正数量和单位均为必填。
- 会话、用料、`Person`/`ExecutionSession`/`MaterialLot`谱系链接及Outbox事件原子提交。
- 页面序列号追溯显示操作员、会话时间及物料批次数量。

## 真实环境验收

- PostgreSQL成功执行`0008 → 0009`迁移。
- 为`SN-SHAFT-44760337`记录`SESSION-SHAFT-44760337-OP10`。
- 操作人员：`OPERATOR-07`。
- 设备与OP 10派工记录一致。
- 物料批次：`STEEL-LOT-2026-0912-A`，数量`1.25 KG`。
- 查询结果包含1个加工会话和新增`Person`、`ExecutionSession`、`MaterialLot`关系。
- 最新制造事件为`ExecutionSessionRecorded`。
- 全量43项测试、Ruff、Mypy与差异检查通过。

## 下一缺口

“人、机、料、法、测”的骨架已经形成，但刀具、夹具、量具和NC程序仍只有设计定义，没有生产实绩引用。下一步应给加工会话增加刀具/程序装载记录，并给检验结果绑定量具和校准状态。
