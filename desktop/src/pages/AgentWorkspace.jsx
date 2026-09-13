import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../components";
import { api, desktop, localDateISO } from "../platform";

const quickPrompts = ["同步附件中的人员能力到产能管理", "检查当前计划的延期风险", "生成一个不使用加班的排产方案"];
function formatBytes(bytes) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function containsDraggedFiles(event) { return Array.from(event.dataTransfer?.types || []).includes("Files"); }
function workbookDescription(metadata) {
  const sheets = metadata?.sheets || [];
  if (!sheets.length) return "";
  return `已识别 ${sheets.length} 个工作表：${sheets.map((sheet) => `${sheet.name}（约 ${sheet.populatedRowCount} 行）`).join("、")}`;
}

export default function AgentWorkspace({ health, actor, onNavigate, incomingDrop, onDropHandled }) {
  const [messages, setMessages] = useState([{ role: "agent", text: "你好，我是衡策。你可以直接拖入人员能力表或订单资料，也可以描述生产任务。我会在本机理解资料，在授权范围内写入生产数据、运行排产并回读核验。", meta: "本地制造 Agent · 人工页面始终可以检查和修改" }]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [spreadsheetPreview, setSpreadsheetPreview] = useState(null);
  const [capacityPreview, setCapacityPreview] = useState(null);
  const [pendingOrderInstruction, setPendingOrderInstruction] = useState("");
  const [evidence, setEvidence] = useState([]);
  const endRef = useRef(null);
  const dragDepth = useRef(0);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  const processFiles = useCallback(async (picked, source = "选择") => {
    if (!picked.length) return;
    if (sending) {
      setMessages((current) => [...current, { role: "system", text: "当前任务完成后再添加文件。", meta: "文件尚未处理" }]);
      return;
    }
    const pending = picked.map((file) => ({ ...file, parseStatus: "PARSING", parsed: null, parseError: null }));
    if (picked.some((file) => /\.(xlsx|csv)$/i.test(file.name))) {
      setCapacityPreview(null);
      setSpreadsheetPreview(null);
    }
    setFiles((current) => [
      ...current.filter((existing) => !pending.some((file) => file.path && file.path === existing.path)),
      ...pending,
    ]);
    setSending(true);
    const parseResults = await Promise.all(pending.map(async (file) => {
      try {
        const parsed = await desktop.core.upload({ path: "/api/v1/agent/attachments/parse", fileId: file.id, filePath: file.path, actor });
        setFiles((current) => current.map((item) => item.id === file.id ? { ...item, parseStatus: parsed.status, parsed } : item));
        return { file, parsed, error: null };
      } catch (error) {
        setFiles((current) => current.map((item) => item.id === file.id ? { ...item, parseStatus: "ERROR", parseError: error.message } : item));
        return { file, parsed: null, error: error.message };
      }
    }));
    const parsed = parseResults.filter((item) => item.parsed?.status === "PARSED");
    const noText = parseResults.filter((item) => item.parsed?.status === "NO_TEXT");
    const failed = parseResults.filter((item) => item.error);
    const details = [
      parsed.length ? `${parsed.length} 个已提取内容` : null,
      noText.length ? `${noText.length} 个未提取到文字` : null,
      failed.length ? `${failed.length} 个解析失败` : null,
    ].filter(Boolean).join("，");
    setMessages((current) => [...current, {
      role: failed.length ? "system" : "agent",
      text: `已${source} ${picked.map((file) => file.name).join("、")}；${details || "没有可解析内容"}。${parsed.length ? "现在可以直接针对附件提问。" : ""}`,
      meta: "本机解析 · 原文件未上传外部文件服务",
    }]);
    const spreadsheet = picked.find((file) => /\.(xlsx|csv)$/i.test(file.name));
    if (spreadsheet) {
      try {
        if (/\.xlsx$/i.test(spreadsheet.name)) {
          const capacity = await desktop.core.upload({ path: "/api/v1/agent/imports/capacity/preview?workshopId=WS-MACH-01", fileId: spreadsheet.id, filePath: spreadsheet.path, actor });
          if (capacity.valid) {
            setCapacityPreview(capacity);
            const stats = capacity.stats || {};
            const skipped = stats.skippedCount ? `；另有 ${stats.skippedCount} 人缺少产能数值，将跳过并保留人工补录` : "";
            setMessages((current) => [...current, {
              role: "agent",
              text: `已识别人员能力表 ${spreadsheet.name}：${stats.personCount || 0} 名可写入人员，其中新增 ${stats.createCount || 0}、更新 ${stats.updateCount || 0}、无需变更 ${stats.unchangedCount || 0}${skipped}。你可以直接说“同步到产能管理”，也可以使用写入按钮。\n\n${(capacity.assumptions || []).map((item) => `• ${item}`).join("\n")}`,
              meta: "已生成本地写入计划 · 尚未写入",
              action: "execute-capacity",
            }]);
          }
        }
        const preview = await desktop.core.upload({ path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01", fileId: spreadsheet.id, filePath: spreadsheet.path, actor });
        setSpreadsheetPreview(preview);
        const stats = preview.stats || {};
        const parsedSpreadsheet = parseResults.find((item) => item.file.id === spreadsheet.id)?.parsed;
        const recognized = (stats.workOrderCount || 0) + (stats.operationCount || 0) + (stats.resourceCount || 0) > 0;
        const text = preview.valid
          ? preview.mappingMode === "TURNING_PLAN_ADAPTER"
            ? `已识别车工排产表 ${spreadsheet.name}：${stats.workOrderCount || 0} 条未完成计划、${stats.resourceCount || 0} 名产能人员；已排除 ${stats.completedExcludedCount || 0} 条完成记录。现在可以直接说“同步到排产”，或使用导入按钮。`
            : `排产导入预检 ${spreadsheet.name}：${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序、${stats.resourceCount || 0} 个产能资源，字段与业务关联校验通过。`
          : recognized
            ? `结构化导入检查 ${spreadsheet.name}：识别到 ${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序、${stats.resourceCount || 0} 个产能资源，还有 ${stats.errorCount || preview.issues?.length || 0} 项映射或校验问题。`
            : `${workbookDescription(parsedSpreadsheet?.metadata) || `已解析 ${spreadsheet.name}`}。它不是 CAPAXION 的“工单/工序/产能”标准导入模板，因此尚未映射成可写入 MES 的业务对象；附件内容已经可以提问。`;
        setMessages((current) => [...current, { role: "agent", text, meta: preview.valid ? "排产快照已生成 · 尚未写入" : "内容已解析 · 完成字段映射后才能写入", action: preview.valid ? "confirm-spreadsheet" : null }]);
      } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "生产数据预检未执行 · 通用附件解析不受影响" }]); }
    }
    setSending(false);
  }, [actor, sending]);

  useEffect(() => {
    if (!incomingDrop) return;
    onDropHandled(incomingDrop.id);
    if (incomingDrop.error) {
      setMessages((current) => [...current, { role: "system", text: incomingDrop.error, meta: "拖入文件失败" }]);
      return;
    }
    if (!incomingDrop.files.length) {
      setMessages((current) => [...current, { role: "system", text: "没有读取到可用文件，请确认拖入的是文件而不是文件夹。", meta: "拖入文件失败" }]);
      return;
    }
    void processFiles(incomingDrop.files, "拖入");
  }, [incomingDrop, onDropHandled, processFiles]);

  async function pickFiles() {
    const picked = await desktop.files.pick();
    await processFiles(picked);
  }

  async function confirmSpreadsheet() {
    if (!spreadsheetPreview?.valid || sending) return;
    setSending(true);
    try {
      const result = await api("/api/v1/planning/imports/spreadsheet/confirm", actor, { method: "POST", body: { previewFingerprint: spreadsheetPreview.previewFingerprint, snapshot: spreadsheetPreview.snapshot, runAgent: true, horizonStart: localDateISO(), horizonDays: 6, useOvertime: false, defaultMinutesPerUnit: 30 } });
      const plan = result.agent?.plan;
      setMessages((current) => [...current, { role: "agent", text: plan ? `已生成 ${plan.planNumber}：${plan.assignments.length} 项安排、${plan.shortages.length} 项能力缺口。请进入排产结果核对，主管批准后才能发布。` : "已生成可追溯的制造输入快照。", meta: "虚拟员工 衡策 · L3 BOUNDED", action: plan ? "open-results" : null }]);
      setSpreadsheetPreview(null); setFiles([]);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "导入与排产未执行" }]); }
    finally { setSending(false); }
  }

  async function submitCapacityPlan(plan) {
    try {
      return await api("/api/v1/agent/imports/capacity/execute", actor, { method: "POST", body: { previewFingerprint: plan.previewFingerprint, plan } });
    } catch (error) {
      const stale = /fingerprint changed|data changed after preview|preview the file again/i.test(error.message);
      const sourceFile = files.find((file) => file.name === plan.source?.filename && /\.xlsx$/i.test(file.name));
      if (!stale || !sourceFile) throw error;
      const refreshed = await desktop.core.upload({ path: "/api/v1/agent/imports/capacity/preview?workshopId=WS-MACH-01", fileId: sourceFile.id, filePath: sourceFile.path, actor });
      if (!refreshed.valid) throw error;
      setCapacityPreview(refreshed);
      return api("/api/v1/agent/imports/capacity/execute", actor, { method: "POST", body: { previewFingerprint: refreshed.previewFingerprint, plan: refreshed } });
    }
  }

  async function executeCapacityImport() {
    if (!capacityPreview?.valid || sending) return;
    setSending(true);
    try {
      const result = await submitCapacityPlan(capacityPreview);
      const stats = result.stats || {};
      setMessages((current) => [...current, { role: "agent", text: `已写入并回读核验 ${result.verifiedCount || 0} 名人员：新增 ${stats.createCount || 0}、更新 ${stats.updateCount || 0}、保持不变 ${stats.unchangedCount || 0}。现在可以在产能管理中人工检查或修改。`, meta: "EXECUTED_AND_VERIFIED · 本地生产数据库", action: "open-capacity" }]);
      setCapacityPreview(null);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "写入未完成 · 未通过回读核验" }]); }
    finally { setSending(false); }
  }

  async function send(value = prompt) {
    const parsedFiles = files.filter((file) => file.parsed?.status === "PARSED");
    const question = value.trim() || (parsedFiles.length ? "请概述附件内容，并指出与制造业务相关的关键信息。" : ""); if (!question || sending) return;
    const attachmentNames = parsedFiles.map((file) => file.name).join("、");
    setMessages((current) => [...current, { role: "user", text: question, meta: parsedFiles.length ? `已附加：${attachmentNames}` : "文字指令" }]); setPrompt(""); setSending(true);
    try {
      if (pendingOrderInstruction && /^(?:取消|不用了|放弃|停止)(?:这个|该)?(?:订单|工单|任务)?[。！!]?$/i.test(question)) {
        setPendingOrderInstruction("");
        setMessages((current) => [...current, { role: "agent", text: "已取消尚未写入的订单任务。", meta: "PENDING_TASK_CANCELLED · 未写入生产数据" }]);
        return;
      }
      const capacityWriteIntent = /(?:同步|导入|写入|更新).{0,16}(?:人员|能力|产能)|(?:人员|能力|产能).{0,16}(?:同步|导入|写入|更新)/i.test(question);
      const scheduleWriteIntent = /(?:同步|导入|写入|加入).{0,16}(?:排产|计划)|(?:排产|计划).{0,16}(?:同步|导入|写入|加入)/i.test(question);
      if (scheduleWriteIntent && spreadsheetPreview?.valid) {
        const result = await api("/api/v1/planning/imports/spreadsheet/confirm", actor, { method: "POST", body: { previewFingerprint: spreadsheetPreview.previewFingerprint, snapshot: spreadsheetPreview.snapshot, runAgent: true, horizonStart: localDateISO(), horizonDays: 10, useOvertime: false, defaultMinutesPerUnit: 30 } });
        const plan = result.agent?.plan;
        setMessages((current) => [...current, { role: "agent", text: plan ? `已把附件中的 ${spreadsheetPreview.stats?.workOrderCount || 0} 条未完成计划同步为排产输入，并生成 ${plan.planNumber}：${plan.assignments.length} 项安排、${plan.shortages.length} 项能力缺口。` : "附件计划已写入排产输入快照。", meta: "EXECUTED_AND_VERIFIED · 排产输入已回读", action: plan ? "open-results" : null }]);
        setSpreadsheetPreview(null);
        setFiles([]);
        return;
      }
      if (capacityWriteIntent && capacityPreview?.valid) {
        const result = await submitCapacityPlan(capacityPreview);
        const stats = result.stats || {};
        setMessages((current) => [...current, { role: "agent", text: `任务已执行：${result.verifiedCount || 0} 名人员已写入产能管理并回读一致。新增 ${stats.createCount || 0}、更新 ${stats.updateCount || 0}、保持不变 ${stats.unchangedCount || 0}。`, meta: "EXECUTED_AND_VERIFIED · 本地生产数据库", action: "open-capacity" }]);
        setCapacityPreview(null);
        return;
      }
      if (scheduleWriteIntent && parsedFiles.length) {
        setMessages((current) => [...current, { role: "system", text: "附件已经解析，但还没有形成可执行的排产映射。需要至少识别物料编码、名称、数量、计划完成时间和单件工时；请查看上方预检问题。", meta: "REQUIRE_SCHEDULE_MAPPING · 未写入" }]);
        return;
      }
      const orderWriteIntent = /(?:新增|新建|加入|添加|创建|录入).{0,10}(?:订单|工单)|(?:订单|工单).{0,10}(?:新增|新建|加入|添加|创建|录入)/i.test(question);
      if ((orderWriteIntent || pendingOrderInstruction) && !parsedFiles.length) {
        const instruction = pendingOrderInstruction ? `${pendingOrderInstruction}\n用户补充：${question}` : question;
        const result = await api("/api/v1/agent/tasks/execute", actor, { method: "POST", body: { instruction } });
        setMessages((current) => [...current, { role: result.status === "EXECUTED_AND_VERIFIED" ? "agent" : "system", text: `${result.answer || "任务未完成"}${(result.assumptions || []).length ? `\n\n${result.assumptions.map((item) => `• ${item}`).join("\n")}` : ""}`, meta: `${result.status} · ${result.parser || "本地任务解析器"}`, action: result.status === "EXECUTED_AND_VERIFIED" ? "open-results" : null }]);
        setPendingOrderInstruction(result.status === "NEEDS_INFORMATION" ? instruction : "");
        return;
      }
      const attachments = parsedFiles.map((file) => ({ name: file.parsed.name, kind: file.parsed.kind, parser: file.parsed.parser, sha256: file.parsed.sha256, documentId: file.parsed.documentId, summary: file.parsed.summary || "", truncated: file.parsed.truncated }));
      const history = messages.filter((item) => item.role === "user" || item.role === "agent").slice(-10).map((item) => ({ role: item.role === "agent" ? "assistant" : "user", content: item.text.slice(0, 2000) }));
      const result = await api("/api/v1/agent/chat", actor, { method: "POST", body: { question, attachments, history } });
      setEvidence(result.documentEvidence || []);
      const cited = (result.sourceObjects || []).filter((item) => item.type === "DocumentChunk");
      const citation = cited.length ? ` · 引用 ${cited.slice(0, 4).map((item) => item.location || item.id).join("、")}` : "";
      setMessages((current) => [...current, { role: "agent", text: result.answer || "任务已完成。", meta: `${result.policyDecision || "ALLOW"} · ${result.provider || result.source || "规则与模型网关"}${citation}` }]);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "请求未执行" }]); }
    finally { setSending(false); }
  }

  function handleDragEnter(event) {
    if (!containsDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth.current += 1;
    setDragActive(true);
  }

  function handleDragLeave(event) {
    if (!containsDraggedFiles(event)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragActive(false);
  }

  function handleDrop(event) {
    if (!containsDraggedFiles(event)) return;
    event.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
  }

  return <div className={`workspace-layout ${dragActive ? "file-drag-active" : ""}`} onDragEnter={handleDragEnter} onDragOver={(event) => { if (containsDraggedFiles(event)) event.preventDefault(); }} onDragLeave={handleDragLeave} onDrop={handleDrop}>{dragActive && <div className="file-drop-overlay"><span>⇩</span><strong>松开以添加并解析文件</strong><small>文字 / 表格 / DOCX / PDF / 图片 OCR 均在本机处理</small></div>}<main className="conversation">
    <PageHeader eyebrow="LOCAL MANUFACTURING AGENT" title="今天需要处理什么？" description="拖入生产资料或直接下达任务；衡策会理解、写入、排产并核验，人工页面保留修改权。" actions={<><button className="button" onClick={() => onNavigate("models")}>切换本地 / DeepSeek</button><div className={`connection-pill ${health?.online ? "ok" : "bad"}`}><span />{health?.online ? "本地生产数据已连接" : "等待 Core"}</div></>} />
    <div className="quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}</div>
    <div className="messages">{messages.map((message, index) => <article className={`message ${message.role}`} key={`${message.role}-${index}`}><div className="message-avatar">{message.role === "agent" ? "衡" : message.role === "user" ? "我" : "!"}</div><div className="message-content"><div>{message.text}</div>{message.action === "execute-capacity" && capacityPreview?.valid && <button className="message-action" onClick={executeCapacityImport}>写入产能管理并核验</button>}{message.action === "confirm-spreadsheet" && spreadsheetPreview?.valid && <button className="message-action" onClick={confirmSpreadsheet}>确认导入并生成排产方案</button>}{message.action === "open-results" && <button className="message-action" onClick={() => onNavigate("results")}>打开排产结果</button>}{message.action === "open-capacity" && <button className="message-action" onClick={() => onNavigate("capacity")}>打开产能管理</button>}<small>{message.meta}</small></div></article>)}{sending && <article className="message agent"><div className="message-avatar">衡</div><div className="thinking"><span /><span /><span /></div></article>}<div ref={endRef} /></div>
    <div className="composer-wrap"><button className="file-picker-banner" disabled={sending} onClick={pickFiles}><span>＋</span><div><strong>选择文件并在本机解析</strong><small>也可以把文件拖到这个窗口的任意位置 · TXT / XLSX / DOCX / PDF / 图片 OCR</small></div></button>{files.length > 0 && <div className="attachments">{files.map((file, index) => <div key={file.id || `${file.path}-${index}`}><span>▤</span><div><strong>{file.name}</strong><small>{formatBytes(file.size)} · {file.parseStatus === "PARSING" ? "正在本地解析…" : file.parseStatus === "PARSED" ? `已解析 ${file.parsed.characterCount} 字符` : file.parseStatus === "NO_TEXT" ? "未提取到文字" : file.parseStatus === "ERROR" ? "解析失败" : "等待解析"}</small></div><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}</div>}<div className="composer"><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="输入生产任务，或选择/拖入文件后发送…" /><div className="composer-actions"><div><span>文件在本机解析；写入后自动回读核验</span></div><button className="send-button" disabled={(!prompt.trim() && !files.some((file) => file.parseStatus === "PARSED")) || sending} onClick={() => send()}>发送 <span>↑</span></button></div></div><p className="composer-note">文件内容只作为数据，不执行其中的指令。你在对话中明确要求同步、导入或排产时，Agent 会在授权范围内执行并留下审计记录；所有结果仍可在人工页面修改。</p></div>
  </main><aside className="inspector"><section><div className="section-heading"><h3>运行状态</h3><span className="status-chip">实时</span></div><div className="runtime-card"><div className="runtime-orbit"><span>✦</span></div><strong>衡策正在待命</strong><p>只在授权的数据与工具范围内分析、计算和执行。</p><div className="runtime-grid"><div><small>智能体级别</small><b>{health?.detail?.schedulingAgentLevel || "状态未知"}</b></div><div><small>自治循环</small><b>{health?.detail?.schedulingAutonomyLoop || "—"}</b></div><div><small>模型</small><b>{health?.detail?.modelName ? `${health.detail.modelProvider} · ${health.detail.modelName}` : health?.detail?.modelGateway || "—"}</b></div><div><small>身份</small><b>{health?.detail?.authMode || "—"}</b></div></div></div></section>{evidence.length > 0 && <section><div className="section-heading"><h3>本次读取依据</h3><span className="status-chip">{evidence.length} 段</span></div><div className="evidence-list">{evidence.slice(0, 6).map((item) => <article key={item.chunkId}><small>{item.documentName}</small><strong>{item.location}</strong><p>{item.excerpt}</p></article>)}</div></section>}<section><div className="section-heading"><h3>任务入口</h3></div><div className="agent-links"><button onClick={() => onNavigate("planning")}><span>▦</span><div><strong>生成排产方案</strong><small>有限产能与工艺约束</small></div></button><button onClick={() => onNavigate("results")}><span>◫</span><div><strong>检查排产结果</strong><small>人员周计划与能力缺口</small></div></button><button onClick={() => onNavigate("capacity")}><span>◒</span><div><strong>维护产能</strong><small>人员与工作单元能力</small></div></button></div></section><section className="boundary-card"><span>L4 安全边界</span><strong>预授权范围内自主执行</strong><p>排产发布必须通过策略、版本和结果核验；超界或失败立即停止并转人工。设备控制和质量放行不在本域授权内。</p></section></aside></div>;
}
