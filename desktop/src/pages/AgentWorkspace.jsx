import { useEffect, useRef, useState } from "react";
import { PageHeader } from "../components";
import { api, desktop, localDateISO } from "../platform";

const quickPrompts = ["检查当前计划的延期风险", "说明产能缺口及计算依据", "生成一个不使用加班的排产方案"];
function formatBytes(bytes) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }

export default function AgentWorkspace({ health, actor, onNavigate }) {
  const [messages, setMessages] = useState([{ role: "agent", text: "你好，我是衡策。你可以让我检查延期风险、解释产能缺口、生成排产方案，或把 Excel、PDF 和现场图片交给我分析。", meta: "L3 受控智能体 · 数据来源可追溯" }]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [spreadsheetPreview, setSpreadsheetPreview] = useState(null);
  const endRef = useRef(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  async function pickFiles() {
    const picked = await desktop.files.pick();
    if (!picked.length) return;
    setFiles((current) => [...current, ...picked]);
    const spreadsheet = picked.find((file) => /\.(xlsx|csv)$/i.test(file.name));
    if (!spreadsheet) {
      setMessages((current) => [...current, { role: "agent", text: `已在本机选择 ${picked.map((file) => file.name).join("、")}。当前版本不会伪装识图或 PDF 抽取能力；接入本地解析器后再进入诊断链。`, meta: "文件未上传至外部服务" }]); return;
    }
    setSending(true);
    try {
      const preview = await desktop.core.upload({ path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01", filePath: spreadsheet.path, actor });
      setSpreadsheetPreview(preview);
      const stats = preview.stats || {};
      setMessages((current) => [...current, { role: "agent", text: `已预检 ${spreadsheet.name}：${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序、${stats.resourceCount || 0} 个产能资源。${preview.valid ? "字段与业务关联校验通过。" : `发现 ${stats.errorCount || preview.issues?.length || 0} 项问题，尚未写入。`}`, meta: preview.valid ? "尚未写入 · 等待人工确认" : "预检失败 · 未写入生产快照", action: preview.valid ? "confirm-spreadsheet" : null }]);
    } catch (error) { setMessages((current) => [...current, { role: "system", text: error.message, meta: "文件预检未执行" }]); }
    finally { setSending(false); }
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

  return <div className="workspace-layout"><main className="conversation">
    <PageHeader eyebrow="MANUFACTURING AGENT" title="今天需要处理什么？" description="描述目标，衡策会选择数据、算法和工具，并在执行前说明影响。" actions={<div className={`connection-pill ${health?.online ? "ok" : "bad"}`}><span />{health?.online ? "工厂数据已连接" : "等待 Core"}</div>} />
    <div className="quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}</div>
    <div className="messages">{messages.map((message, index) => <article className={`message ${message.role}`} key={`${message.role}-${index}`}><div className="message-avatar">{message.role === "agent" ? "衡" : message.role === "user" ? "我" : "!"}</div><div className="message-content"><div>{message.text}</div>{message.action === "confirm-spreadsheet" && spreadsheetPreview?.valid && <button className="message-action" onClick={confirmSpreadsheet}>确认导入并生成排产方案</button>}{message.action === "open-results" && <button className="message-action" onClick={() => onNavigate("results")}>打开排产结果</button>}<small>{message.meta}</small></div></article>)}{sending && <article className="message agent"><div className="message-avatar">衡</div><div className="thinking"><span /><span /><span /></div></article>}<div ref={endRef} /></div>
    <div className="composer-wrap">{files.length > 0 && <div className="attachments">{files.map((file, index) => <div key={`${file.path}-${index}`}><span>▤</span><div><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></div><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}</div>}<div className="composer"><textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="向衡策描述目标，或添加制造资料…" /><div className="composer-actions"><div><button className="icon-button" onClick={pickFiles} title="添加附件">＋</button><span>Excel · PDF · 图片</span></div><button className="send-button" disabled={!prompt.trim() || sending} onClick={() => send()}>发送 <span>↑</span></button></div></div><p className="composer-note">衡策可能出错。生产动作始终受权限、审批、版本与安全策略约束。</p></div>
  </main><aside className="inspector"><section><div className="section-heading"><h3>运行状态</h3><span className="status-chip">实时</span></div><div className="runtime-card"><div className="runtime-orbit"><span>✦</span></div><strong>衡策正在待命</strong><p>只在授权的数据与工具范围内分析、计算和提交方案。</p><div className="runtime-grid"><div><small>智能体级别</small><b>{health?.detail?.schedulingAgentLevel || "L3_BOUNDED"}</b></div><div><small>存储</small><b>{health?.detail?.storageBackend || "—"}</b></div><div><small>模型</small><b>{health?.detail?.modelGateway || "—"}</b></div><div><small>身份</small><b>{health?.detail?.authMode || "—"}</b></div></div></div></section><section><div className="section-heading"><h3>任务入口</h3></div><div className="agent-links"><button onClick={() => onNavigate("planning")}><span>▦</span><div><strong>生成排产方案</strong><small>有限产能与工艺约束</small></div></button><button onClick={() => onNavigate("results")}><span>◫</span><div><strong>检查排产结果</strong><small>人员周计划与能力缺口</small></div></button><button onClick={() => onNavigate("capacity")}><span>◒</span><div><strong>维护产能</strong><small>人员与工作单元能力</small></div></button></div></section><section className="boundary-card"><span>安全边界</span><strong>不会直接控制设备</strong><p>排产发布、外部回写和高风险动作仍需策略校验与独立审批。</p></section></aside></div>;
}
