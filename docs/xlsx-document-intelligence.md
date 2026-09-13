# 真实 Excel 解析决策

日期：2026-09-13。验证样本：`车工计划.xlsx`。

## 采用的开源能力

本轮先对成熟项目进行原文件实测，再确定接入方式：

- [Docling](https://github.com/docling-project/docling) 支持 XLSX、本地执行和统一文档对象。`docling-slim[format-xlsx]` 对真实文件在约 1.1 秒内识别出 3 个工作表、367 个连续数据区，并把合并单元格中的人员名称正确传播到明细行。CAPAXION 使用它的结构化对象，不直接使用完整 Markdown 导出。
- [Microsoft MarkItDown](https://github.com/microsoft/markitdown) 可以把 Office 文件转换为适合 LLM 的 Markdown，但当前 XLSX 可选依赖会把本项目的 ONNX Runtime 1.30 降到旧版本，与 RapidOCR 环境冲突，因此本版没有接入。
- [Unstructured](https://github.com/Unstructured-IO/unstructured) 的 `partition_xlsx` 支持表格元素和子表检测，后续可作为复杂表格对照引擎；本版不同时引入第二套较重依赖。
- [LlamaIndex](https://github.com/run-llama/llama_index) 的文件读取器能编排 XLSX 加载，但其默认 Excel reader 仍以 Pandas 为底层，不能替代制造字段映射和写入校验。

## 为什么不直接把 Docling Markdown 交给模型

真实样本的完整 Docling Markdown 为 228394 个字符，并且 Markdown 导出中没有工作表标题。原文件的“车工每日计划”还因空行和版式被识别为 364 个连续数据区。完整文本远超当前 Qwen3 4B 的 4096-token 上下文。

Core 因此读取 Docling 的工作表组、表格网格和来源页号，生成两层结果：

1. 确定性摘要：每个工作表的名称、数据区数量、填充行数、字段和 `字段=值` 样例。
2. 有界节选：30000 字符内按工作表公平分配，避免排在末尾的人员能力表被前面的大表挤掉。

发送模型时，所有附件共享 1800 字符预算。模型不可用时，Core 直接返回确定性摘要，因此不会再声称“什么也没解析出来”。

## 真实样本结论

| 工作表 | 连续数据区 | 填充行 | 已识别字段 |
|---|---:|---:|---|
| 工艺编制中 | 2 | 130 | 人员、物料编码、图号、名称、数量、单位、日期、状态、单件工时 |
| 车工每日计划 | 364 | 1166 | 人员、物料、图号、数量、计划日期、状态、单件工时、合计工时 |
| 专线列表及工时 | 1 | 41 | 人员、加工种类、8 小时工时、加班 3 小时工时 |

这些字段足以让 Agent 理解和回答附件，但还不足以安全写入当前 MES 模型：文件中没有明确标为工单号的唯一主键、工序顺序、工作中心或资源编号。系统应生成字段映射预览，再由正式导入链路完成唯一性、关联关系和版本校验。
