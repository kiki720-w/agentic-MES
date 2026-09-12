# 第36步：通用排产连接器开发包验收

## 目标

在尚未选择具体ERP/MES厂商之前，固定供应商中立的数据契约和安全传输方式，使后续适配工作集中在客户字段映射，而不是修改APS或Agent核心。

## 交付

- `schemas/scheduling-snapshot.schema.json`：排产输入JSON Schema，覆盖工单、冻结版本、工序、齐套、质量冻结、人员/单元/设备产能和状态。
- `data/scheduling-snapshot.example.json`：不含真实生产信息的机械加工样例。
- `core/scripts/push-scheduling-snapshot.py`：离线结构检查、请求体SHA-256、HMAC-SHA256签名、Nonce和时间戳生成及安全推送客户端。
- `core/tests/test_scheduling_snapshot_client.py`：签名契约和传输安全边界测试。

## 使用

仅校验文件，不发送：

```powershell
python core/scripts/push-scheduling-snapshot.py data/scheduling-snapshot.example.json --validate-only
```

向本机开发实例推送时，通过进程环境提供连接器Key ID和Secret，再运行同一命令。客户端不会打印Secret。非回环地址必须使用HTTPS，防止连接器凭据和制造数据以明文传输。

## 安全边界

- 签名覆盖“Unix时间戳 + 换行 + 唯一Nonce + 换行 + 原始请求体”。
- 服务端校验时间窗、Key ID、签名和持久化Nonce，拒绝篡改、过期与重放。
- 客户端不包含数据库驱动、不保存凭据、不实现生产系统回写。
- 相同来源、车间和源版本只能对应同一内容；版本相同但内容变化会被拒绝。

## 下一决策门

通用骨架之后必须选择第一个真实来源系统。所需输入是脱敏接口/表结构和字段样例，而不是生产账号或真实密钥。字段映射完成并通过影子运行前，连接器保持单向读取，计划不回写。
