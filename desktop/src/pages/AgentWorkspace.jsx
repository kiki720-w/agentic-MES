import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../components";
import { api, desktop, localDateISO } from "../platform";

const quickPrompts = ["检查当前计划的延期风险", "说明产能缺口及计算依据", "生成一个不使用加班的排产方案"];
function formatBytes(bytes) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function containsDraggedFiles(event) { return Array.from(event.dataTransfer?.types || []).includes("Files"); }

export default function AgentWorkspace({ health, actor, onNavigate }) {
  const [messages, setMessages] = useState([{ role: "agent", text: "你好，我是衡策。你可以让我检查延期风险、解释产能缺口、生成排产方案，也可以预检 XLSX/CSV。PDF 和图片目前仅支持选择，尚未解析或理解。", meta: "L4 目标智能体 · 当前自治模式以运行状态为准" }]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [spreadsheetPreview, setSpreadsheetPreview] = useState(null);
  const endRef = useRef(null);
  const dragDepth = useRef(0);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  async function selfCheck() {
    if (sending) return;
    setSending(true);
    try {
      const result = await api("/api/v1/agent/capability-check", actor, { method: "POST" });
      setMessages((current) => [...current, { role: "agent", text: `文字自检：${result.passed ? "通过" : "未通过"}。${result.checks.map((check) => `${check.id}：${check.passed ? "通过" : "未通过"}（${check.latencyMs} ms）`).join("；")}。仅测试合成事实、引用、缺失事实与计算，不代表生产准确率。PDF/图片、多轮会话及数据零出站尚未验收。`, meta: `${result.suiteVersion} · ${result.runId} · ${result.modelStatus.connectionStatus} · 证据已保存到本机 Core` }]);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "自检未完成" }]); }
    finally { setSending(false); }
  }

  const processFiles = useCallback(async (picked, source = "选择") => {
    if (!picked.length) return;
    if (sending) {
      setMessages((current) => [...current, { role: "system", text: "当前任务完成后再添加文件。", meta: "文件尚未处理" }]);
      return;
    }
    setFiles((current) => {
      const known = new Set(current.map((file) => file.path));
      return [...current, ...picked.filter((file) => !known.has(file.path))];
    });
    const unsupported = picked.filter((file) => !/\.(xlsx|csv)$/i.test(file.name));
    if (unsupported.length) setMessages((current) => [...current, { role: "agent", text: `${unsupported.map((file) => file.name).join("、")} 已作为文件${source}到本机，但当前版本尚未解析这类文件，也不会发送给模型。`, meta: "附件能力边界" }]);
    const spreadsheet = picked.find((file) => /\.(xlsx|csv)$/i.test(file.name));
    if (!spreadsheet) {
      if (!unsupported.length) setMessages((current) => [...current, { role: "agent", text: `没有可处理的文件。`, meta: "文件未上传至外部服务" }]);
      return;
    }
    setSending(true);
    try {
      const preview = await desktop.core.upload({ path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01", filePath: spreadsheet.path, actor });
      setSpreadsheetPreview(preview);
      const stats = preview.stats || {};
      setMessages((current) => [...current, { role: "agent", text: `已预检 ${spreadsheet.name}：${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序、${stats.resourceCount || 0} 个产能资源。${preview.valid ? "字段与业务关联校验通过。" : `发现 ${stats.errorCount || preview.issues?.length || 0} 项问题，尚未写入。`}`, meta: preview.valid ? "尚未写入 · 等待人工确认" : "预检失败 · 未写入生产快照", action: preview.valid ? "confirm-spreadsheet" : null }]);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "文件预检未执行" }]); }
    finally { setSending(false); }
  }, [actor, sending]);

  useEffect(() => {
    desktop.files.onDrop?.((picked, error) => {
      if (error) {
        setMessages((current) => [...current, { role: "system", text: error, meta: "拖入文件失败" }]);
        return;
      }
      if (!picked.length) {
        setMessages((current) => [...current, { role: "system", text: "没有读取到可用文件，请确认拖入的是文件而不是文件夹。", meta: "拖入文件失败" }]);
        return;
      }
      void processFiles(picked, "拖入");
    });
    return () => desktop.files.offDrop?.();
  }, [processFiles]);

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

  async function send(value = prompt) {
    const question = value.trim(); if (!question || sending) return;
    setMessages((current) => [...current, { role: "user", text: question, meta: files.length ? `${files.length} 个本地附件已选择` : "文字指令" }]); setPrompt(""); setSending(true);
    try {
      const result = await api("/api/v1/agent/chat", actor, { method: "POST", body: { question } });
      setMessages((current) => [...current, { role: "agent", text: result.answer || "任务已完成。", meta: `${result.policyDecision || "ALLOW"} · ${result.provider || result.source || "规则与模型网关"}` }]);
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

  return <div className={`workspace-layout ${dragActive ? "file-drag-active" : ""}`} onDragEnter={handleDragEnter} onDragOver={(event) => { if (containsDraggedFiles(event)) event.preventDefault(); }} onDragLeave={handleDragLeave} onDrop={handleDrop}>{dragActive && <div className="file-drop-overlay"><span>⇩</span><strong>松开以添加制造资料</strong><small>XLSX / CSV 将立即在本机预检</small></div>}<main className="conversation">
    <PageHeader eyebrow="MANUFACTURING AGENT" title="今天需要处理什么？" description="描述目标，衡策会选择数据、算法和工具，并在执行前说明影响。" actions={<div className={`connection-pill ${health?.online ? "ok" : "bad"}`}><span />{health?.online ? "工厂数据已连接" : "等待 Core"}</div>} />
    <div className="quick-prompts"><button onClick={() => onNavigate("capabilities")}>AI 能力验收与模型对比<span>↗</span></button><button disabled={sending} onClick={selfCheck}>文字能力自检（3 项合成样本）<span>✓</span></button>{quickPrompts.map((item) => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}</div>
    <div className="messages">{messages.map((message, index) => <article className={`message ${message.role}`} key={`${message.role}-${index}`}><div className="message-avatar">{message.role === "agent" ? "衡" : message.role === "user" ? "我" : "!"}</div><div className="message-content"><div>{message.text}</div>{message.action === "confirm-spreadsheet" && spreadsheetPreview?.valid && <button className="message-action" onClick={confirmSpreadsheet}>确认导入并生成排产方案</button>}{message.action === "open-results" && <button className="message-action" onClick={() => onNavigate("results")}>打开排产结果</button>}<small>{message.meta}</small></div></article>)}{sending && <article className="message agent"><div className="message-avatar">衡</div><div className="thinking"><span /><span /><span /></div></article>}<div ref={endRef} /></div>
    <div className="composer-wrap">{files.length > 0 && <div className="attachments">{files.map((file, index) => <div key={`${file.path}-${index}`}><span>▤</span><div><strong>{file.name}</strong><small>{formatBytes(file.size)} · {/\.(xlsx|csv)$/i.test(file.name) ? "已识别文件，预检结果见对话" : "已识别文件，尚未解析内容"}</small></div><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}</div>}<div className="composer"><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="向衡策描述目标，或拖入制造资料…" /><div className="composer-actions"><div><button className="icon-button" onClick={pickFiles} title="添加附件">＋</button><span>可拖入文件 · XLSX/CSV 本地预检 · PDF/图片尚未解析</span></div><button className="send-button" disabled={!prompt.trim() || sending} onClick={() => send()}>发送 <span>↑</span></button></div></div><p className="composer-note">拖入文件会显示为附件，不再写入文字框。当前为单轮请求，PDF/图片附件尚不随文字发送。生产动作受自治策略、版本、执行核验和停止开关约束。</p></div>
  </main><aside className="inspector"><section><div className="section-heading"><h3>运行状态</h3><span className="status-chip">实时</span></div><div className="runtime-card"><div className="runtime-orbit"><span>✦</span></div><strong>衡策正在待命</strong><p>只在授权的数据与工具范围内分析、计算和执行。</p><div className="runtime-grid"><div><small>智能体级别</small><b>{health?.detail?.schedulingAgentLevel || "状态未知"}</b></div><div><small>自治循环</small><b>{health?.detail?.schedulingAutonomyLoop || "—"}</b></div><div><small>模型</small><b>{health?.detail?.modelGateway || "—"}</b></div><div><small>身份</small><b>{health?.detail?.authMode || "—"}</b></div></div></div></section><section><div className="section-heading"><h3>任务入口</h3></div><div className="agent-links"><button onClick={() => onNavigate("planning")}><span>▦</span><div><strong>生成排产方案</strong><small>有限产能与工艺约束</small></div></button><button onClick={() => onNavigate("results")}><span>◫</span><div><strong>检查排产结果</strong><small>人员周计划与能力缺口</small></div></button><button onClick={() => onNavigate("capacity")}><span>◒</span><div><strong>维护产能</strong><small>人员与工作单元能力</small></div></button></div></section><section className="boundary-card"><span>L4 安全边界</span><strong>预授权范围内自主执行</strong><p>排产发布必须通过策略、版本和结果核验；超界或失败立即停止并转人工。设备控制和质量放行不在本域授权内。</p></section></aside></div>;
}
