# 第十三步产品序列号制造谱系验收记录

日期：2026-09-12  
目标：补齐初始MES目标中“任一序列号可还原制造谱系”的第一条可运行垂直切片。

## 已实现

- 新增`ProductUnit`和只追加`GenealogyLink`领域对象。
- 新增`product_units`、`genealogy_links`表及Alembic 0008迁移。
- 产品序列号全局唯一并统一标准化为大写。
- 草稿工单禁止登记产品序列号。
- 登记时自动固化`WorkOrder`、`ProductRevision`、`RoutingRevision`、`BomRevision`和全部`DrawingRevision`关系。
- 已派工工序自动固化`OperationTask`与`Equipment`关系及工序号。
- 查询时聚合同一工单的质量检验状态、结论和缺陷代码。
- 登记产品单元与全部谱系链接在同一事务提交，并写入`ProductUnitRegistered` Outbox事件。
- HTML事件追溯页新增序列号搜索与谱系列表。

## API

- `POST /api/v1/genealogy/product-units`
- `GET /api/v1/genealogy/product-units/{productSerial}`

## 当前边界

本切片已经覆盖“机、法、测”的基础引用，但人员、物料批次、刀具、夹具、量具、NC程序和精确加工会话尚未形成独立领域记录。下一步应从`ExecutionSession`和`MaterialConsumption`开始扩充，而不是让Agent推测缺失事实。

## 真实环境验收

- PostgreSQL成功执行`0007 → 0008`事务迁移。
- 为工单`WO-QA-44760337`登记序列号`SN-SHAFT-44760337`。
- 单事务写入1个产品单元、7条谱系关系和1条`ProductUnitRegistered` Outbox事件。
- 关系类型覆盖`WorkOrder`、`ProductRevision`、`RoutingRevision`、`BomRevision`、`DrawingRevision`、`Equipment`和`OperationTask`。
- 浏览器“事件追溯”页面按序列号返回与API一致的7条事实。
- 全量41项测试通过，包括真实PostgreSQL原子事务测试；Ruff、Mypy和差异检查通过。
