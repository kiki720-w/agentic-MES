import { useCallback, useEffect, useRef, useState } from "react";
import { api, desktop, localDateISO } from "../platform";

const quickPrompts = ["同步附件中的人员能力到产能管理", "检查当前计划的延期风险", "生成一个不使用加班的排产方案"];
const blockerLabels = {
  ASSIGNMENT_LIMIT_EXCEEDED: "安排数量超过当前授权上限",
  AFFECTED_ORDER_LIMIT_EXCEEDED: "受影响工单超过当前授权上限",
  LATE_ORDER_LIMIT_EXCEEDED: "延期工单超过当前授权上限",
  SHORTAGE_LIMIT_EXCEEDED: "仍有能力或产能缺口",
  SNAPSHOT_STALE: "输入快照已经过期",
  ATOMIC_PLAN_REPLACEMENT_REQUIRED: "已有发布方案，需要先完成原子替换能力",
  PROCESS_STANDARD_REQUIRED: "部分工序缺少标准工时",
  MATERIAL_NOT_READY: "存在缺料工单",
  QUALITY_HOLD: "存在质量冻结工单",
};
const conversationStorageKey = "capaxion.agent.conversations.v1";
const paneStorageKey = "capaxion.agent.panes.v1";
const welcomeMessage = { role: "agent", text: "你好，我是衡策。你可以直接拖入人员能力表或订单资料，也可以描述生产任务。我会在本机理解资料，在授权范围内写入生产数据、运行排产并回读核验。", meta: "本地制造 Agent · 人工页面始终可以检查和修改" };
function conversationId() { return globalThis.crypto?.randomUUID?.() || `conversation-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function newConversation() { const now = new Date().toISOString(); return { id: conversationId(), title: "新任务", createdAt: now, updatedAt: now, messages: [welcomeMessage] }; }
function isWelcome(message) { return message?.role === "agent" && message?.text === welcomeMessage.text; }
function meaningfulMessages(messages) { return messages.filter((message) => !isWelcome(message) && message?.text?.trim()); }
function compactText(value, length = 36) { const text = String(value || "").replace(/\s+/g, " ").trim(); return text.length > length ? `${text.slice(0, length)}…` : text; }
function attachmentNames(messages) {
  const names = [];
  const pattern = /[^，；。\n：:]*?\.(?:xlsx?|csv|docx?|pdf|txt|png|jpe?g|webp)/gi;
  messages.forEach((message) => {
    (message.attachments || []).forEach((name) => { if (name && !names.includes(name)) names.push(name); });
    `${message.text || ""} ${message.meta || ""}`.match(pattern)?.forEach((name) => {
    const cleaned = name.replace(/^.*(?:已选择|已拖入|已附加|附件|能力表|排产表|导入检查)\s*/i, "").trim();
    if (cleaned && !names.includes(cleaned)) names.push(cleaned);
    });
  });
  return names;
}
function conversationTitle(messages) {
  const meaningful = meaningfulMessages(messages);
  const firstUser = meaningful.find((message) => message.role === "user");
  const files = attachmentNames(meaningful);
  if (files.length && firstUser) return compactText(`${files[0]} · ${firstUser.text}`, 34);
  if (files.length) return compactText(`${files[0]} · 文件分析`, 34);
  return compactText((firstUser || meaningful[0])?.text, 34) || "新任务";
}
function conversationDetails(conversation) {
  const meaningful = meaningfulMessages(conversation.messages);
  const files = attachmentNames(meaningful);
  const last = meaningful.at(-1);
  const combined = meaningful.map((message) => `${message.text || ""} ${message.meta || ""}`).join(" ");
  let status = "进行中";
  let tone = "active";
  if (!meaningful.length) { status = "未开始"; tone = "idle"; }
  else if (/请求未执行|写入未完成|解析失败|ERROR|失败/.test(`${last?.text || ""} ${last?.meta || ""}`)) { status = "需处理"; tone = "error"; }
  else if (/EXECUTED_AND_VERIFIED|已执行并发布|已写入并回读|已回读核验/.test(combined)) { status = "已完成"; tone = "done"; }
  else if (/尚未写入|待执行|NEEDS_INFORMATION|需要补充/.test(`${last?.text || ""} ${last?.meta || ""}`)) { status = "待确认"; tone = "pending"; }
  else if (files.length) { status = "已解析"; tone = "file"; }
  return {
    title: conversationTitle(conversation.messages),
    preview: compactText(last?.text, 54) || "还没有任务内容",
    status,
    tone,
    fileCount: files.length,
    turnCount: meaningful.filter((message) => message.role === "user").length,
  };
}
function loadConversations() {
  try {
    const parsed = JSON.parse(localStorage.getItem(conversationStorageKey) || "null");
    let conversations = Array.isArray(parsed?.conversations) ? parsed.conversations
      .filter((item) => item?.id && Array.isArray(item.messages))
      .map((item) => ({ ...item, title: conversationTitle(item.messages) })) : [];
    const empty = conversations.filter((item) => meaningfulMessages(item.messages).length === 0);
    if (empty.length > 1) {
      const keep = empty.find((item) => item.id === parsed.activeId) || empty[0];
      conversations = conversations.filter((item) => item.id === keep.id || meaningfulMessages(item.messages).length > 0);
    }
    if (conversations.length) return { conversations, activeId: conversations.some((item) => item.id === parsed.activeId) ? parsed.activeId : conversations[0].id };
  } catch { /* Start with a clean local history if persisted data is invalid. */ }
  const initial = newConversation();
  return { conversations: [initial], activeId: initial.id };
}
function historyTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
function branchForConversation(conversation) {
  const details = conversationDetails(conversation);
  const content = `${details.title} ${details.preview}`;
  if (/排产|计划|车工/.test(content)) return { id: "scheduling", label: "排产任务" };
  if (/人员|能力|产能/.test(content)) return { id: "capacity", label: "人员与产能" };
  if (/订单|工单/.test(content)) return { id: "orders", label: "订单任务" };
  if (/附件|文件|图纸|图片|文档/.test(content)) return { id: "documents", label: "资料分析" };
  return { id: "general", label: "其他对话" };
}
function messageMetaLabel(meta) {
  if (/EXECUTED_AND_VERIFIED|已执行并发布|已写入并回读|已回读核验/.test(meta || "")) return "已执行并核验";
  if (/失败|未执行|未完成|ERROR/.test(meta || "")) return "需要处理";
  return "";
}
function loadPaneWidths() {
  try {
    const saved = JSON.parse(localStorage.getItem(paneStorageKey) || "null");
    return {
      history: Math.min(420, Math.max(190, Number(saved?.history) || 248)),
      inspector: Math.min(480, Math.max(240, Number(saved?.inspector) || 292)),
    };
  } catch { return { history: 248, inspector: 292 }; }
}
function formatBytes(bytes) { if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`; return `${(bytes / 1024 / 1024).toFixed(1)} MB`; }
function containsDraggedFiles(event) { return Array.from(event.dataTransfer?.types || []).includes("Files"); }
function workbookDescription(metadata) {
  const sheets = metadata?.sheets || [];
  if (!sheets.length) return "";
  return `已识别 ${sheets.length} 个工作表：${sheets.map((sheet) => `${sheet.name}（约 ${sheet.populatedRowCount} 行）`).join("、")}`;
}

export default function AgentWorkspace({ health, actor, onNavigate, incomingDrop, onDropHandled }) {
  const [conversationState, setConversationState] = useState(loadConversations);
  const activeConversation = conversationState.conversations.find((item) => item.id === conversationState.activeId) || conversationState.conversations[0];
  const messages = activeConversation?.messages || [welcomeMessage];
  const setMessages = useCallback((updater) => {
    setConversationState((current) => {
      const now = new Date().toISOString();
      return {
        ...current,
        conversations: current.conversations.map((conversation) => {
          if (conversation.id !== current.activeId) return conversation;
          const nextMessages = typeof updater === "function" ? updater(conversation.messages) : updater;
          const boundedMessages = nextMessages.slice(-200).map((message) => ({ ...message, text: String(message.text || "").slice(0, 12000) }));
          return { ...conversation, title: conversationTitle(boundedMessages), updatedAt: now, messages: boundedMessages };
        }),
      };
    });
  }, []);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [spreadsheetPreview, setSpreadsheetPreview] = useState(null);
  const [capacityPreview, setCapacityPreview] = useState(null);
  const [pendingOrderInstruction, setPendingOrderInstruction] = useState("");
  const [evidence, setEvidence] = useState([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [paneWidths, setPaneWidths] = useState(loadPaneWidths);
  const [resizingPane, setResizingPane] = useState("");
  const endRef = useRef(null);
  const dragDepth = useRef(0);
  const resizeOrigin = useRef(null);
  useEffect(() => {
    try {
      const conversations = [...conversationState.conversations]
        .sort((left, right) => String(right.updatedAt).localeCompare(String(left.updatedAt)))
        .slice(0, 50);
      localStorage.setItem(conversationStorageKey, JSON.stringify({ conversations, activeId: conversationState.activeId }));
    } catch { /* Conversation continues even if local storage is unavailable. */ }
  }, [conversationState]);
  useEffect(() => {
    try { localStorage.setItem(paneStorageKey, JSON.stringify(paneWidths)); } catch { /* Resizing still works without persistence. */ }
  }, [paneWidths]);
  useEffect(() => {
    if (!resizingPane) return undefined;
    function move(event) {
      const origin = resizeOrigin.current;
      if (!origin) return;
      const delta = event.clientX - origin.x;
      const available = document.querySelector(".apple-workspace")?.clientWidth || window.innerWidth;
      setPaneWidths((current) => {
        const maxHistory = Math.max(190, Math.min(420, available - current.inspector - 396));
        const maxInspector = Math.max(240, Math.min(480, available - current.history - 396));
        return resizingPane === "history"
          ? { ...current, history: Math.min(maxHistory, Math.max(190, origin.width + delta)) }
          : { ...current, inspector: Math.min(maxInspector, Math.max(240, origin.width - delta)) };
      });
    }
    function stop() { resizeOrigin.current = null; setResizingPane(""); }
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", stop, { once: true });
    document.addEventListener("pointercancel", stop, { once: true });
    window.addEventListener("blur", stop, { once: true });
    document.body.classList.add("resizing-workspace");
    return () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", stop);
      document.removeEventListener("pointercancel", stop);
      window.removeEventListener("blur", stop);
      document.body.classList.remove("resizing-workspace");
    };
  }, [resizingPane]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, sending]);

  function beginResize(pane, event) {
    event.preventDefault();
    resizeOrigin.current = { x: event.clientX, width: paneWidths[pane] };
    setResizingPane(pane);
  }

  function resetTaskState() {
    setPrompt(""); setFiles([]); setSpreadsheetPreview(null); setCapacityPreview(null);
    setPendingOrderInstruction(""); setEvidence([]);
  }

  function createConversation() {
    if (sending) return;
    setConversationState((current) => {
      const active = current.conversations.find((item) => item.id === current.activeId);
      if (active && meaningfulMessages(active.messages).length === 0) return current;
      const conversation = newConversation();
      const completed = current.conversations.filter((item) => meaningfulMessages(item.messages).length > 0);
      return { conversations: [conversation, ...completed].slice(0, 50), activeId: conversation.id };
    });
    resetTaskState();
  }

  function selectConversation(id) {
    if (sending || id === conversationState.activeId) return;
    setConversationState((current) => ({ ...current, activeId: id }));
    resetTaskState();
  }

  function deleteConversation(id) {
    if (sending) return;
    setConversationState((current) => {
      const remaining = current.conversations.filter((item) => item.id !== id);
      if (remaining.length) return { conversations: remaining, activeId: current.activeId === id ? remaining[0].id : current.activeId };
      const conversation = newConversation();
      return { conversations: [conversation], activeId: conversation.id };
    });
    if (id === conversationState.activeId) resetTaskState();
  }

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
    const spreadsheet = picked.find((file) => /\.(xlsx|csv)$/i.test(file.name));
    if (!spreadsheet) {
      setMessages((current) => [...current, {
        role: failed.length ? "system" : "agent",
        text: `已${source} ${picked.map((file) => file.name).join("、")}；${details || "没有提取到内容"}。`,
        meta: failed.length ? "解析失败" : "已读取",
        attachments: picked.map((file) => file.name),
      }]);
    }
    if (spreadsheet) {
      try {
        let capacityCandidate = null;
        if (/\.xlsx$/i.test(spreadsheet.name)) {
          const capacity = await desktop.core.upload({ path: "/api/v1/agent/imports/capacity/preview?workshopId=WS-MACH-01", fileId: spreadsheet.id, filePath: spreadsheet.path, actor });
          if (capacity.valid) capacityCandidate = capacity;
        }
        const preview = await desktop.core.upload({ path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01", fileId: spreadsheet.id, filePath: spreadsheet.path, actor });
        const stats = preview.stats || {};
        const parsedSpreadsheet = parseResults.find((item) => item.file.id === spreadsheet.id)?.parsed;
        const recognized = (stats.workOrderCount || 0) + (stats.operationCount || 0) + (stats.resourceCount || 0) > 0;
        if (preview.valid) {
          setSpreadsheetPreview(preview);
          setCapacityPreview(null);
          const text = preview.mappingMode === "TURNING_PLAN_ADAPTER"
            ? `已识别 ${stats.workOrderCount || 0} 条未完成计划和 ${stats.resourceCount || 0} 名人员，可同步到排产。`
            : `已识别 ${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序和 ${stats.resourceCount || 0} 个资源，可生成排产。`;
          setMessages((current) => [...current, { role: "agent", text, meta: "尚未写入", action: "confirm-spreadsheet", attachments: [spreadsheet.name] }]);
        } else if (capacityCandidate?.valid) {
          setCapacityPreview(capacityCandidate);
          setSpreadsheetPreview(null);
          const capacityStats = capacityCandidate.stats || {};
          const missing = capacityStats.skippedCount ? `；${capacityStats.skippedCount} 人缺少产能值` : "";
          setMessages((current) => [...current, {
            role: "agent",
            text: `已识别 ${capacityStats.personCount || 0} 名人员：新增 ${capacityStats.createCount || 0}、更新 ${capacityStats.updateCount || 0}${missing}。可写入产能管理。`,
            meta: "尚未写入",
            action: "execute-capacity",
            attachments: [spreadsheet.name],
          }]);
        } else {
          setSpreadsheetPreview(preview);
          const text = recognized
            ? `已读取表格，但还有 ${stats.errorCount || preview.issues?.length || 0} 项字段需要补充。`
            : `${workbookDescription(parsedSpreadsheet?.metadata) || `已读取 ${spreadsheet.name}`}，还无法识别为人员、订单或排产数据。`;
          setMessages((current) => [...current, { role: "agent", text, meta: "等待补充", attachments: [spreadsheet.name] }]);
        }
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
    setMessages((current) => [...current, { role: "user", text: question, meta: parsedFiles.length ? `已附加：${attachmentNames}` : "文字指令", attachments: parsedFiles.map((file) => file.name) }]); setPrompt(""); setSending(true);
    try {
      if (pendingOrderInstruction && /^(?:取消|不用了|放弃|停止)(?:这个|该)?(?:订单|工单|任务)?[。！!]?$/i.test(question)) {
        setPendingOrderInstruction("");
        setMessages((current) => [...current, { role: "agent", text: "已取消尚未写入的订单任务。", meta: "PENDING_TASK_CANCELLED · 未写入生产数据" }]);
        return;
      }
      const capacityWriteIntent = /(?:同步|导入|写入|更新).{0,16}(?:人员|能力|产能)|(?:人员|能力|产能).{0,16}(?:同步|导入|写入|更新)/i.test(question);
      const scheduleWriteIntent = /(?:同步|导入|写入|加入).{0,16}(?:排产|计划)|(?:排产|计划).{0,16}(?:同步|导入|写入|加入)/i.test(question);
      const scheduleExecuteIntent = /(?:执行|发布|应用|生效|批准).{0,16}(?:排产|方案|计划)|(?:排产|方案|计划).{0,16}(?:执行|发布|应用|生效|批准)/i.test(question);
      const scheduleGenerateIntent = /(?:生成|制定|重排|重新排|优化).{0,16}(?:排产|方案|计划)|(?:排产|方案|计划).{0,16}(?:生成|制定|重排|重新排|优化)/i.test(question);
      if (scheduleExecuteIntent) {
        const result = await api("/api/v1/planning/autonomy/run", actor, { method: "POST", body: { workshopId: "WS-MACH-01", horizonStart: localDateISO(), horizonDays: 10, useOvertime: false, defaultMinutesPerUnit: 30, operationRates: {} } });
        const published = result.publishedPlan;
        const blockers = result.policyDecision?.blockers || [];
        setMessages((current) => [...current, {
          role: result.state === "VERIFIED" ? "agent" : "system",
          text: published
            ? `已执行并发布排产方案 ${published.planNumber}，共 ${published.assignments.length} 项安排；执行结果已回读核验。`
            : result.decision === "NO_CHANGE_ALREADY_PUBLISHED"
              ? "当前排产方案已经发布，输入没有变化，因此没有重复执行。"
              : `排产执行已停止：${blockers.length ? blockers.map((item) => blockerLabels[item] || item).join("；") : result.decision || result.state}。`,
          meta: `${result.decision || "L4_POLICY"} · ${result.state || "UNKNOWN"}`,
          action: "open-results",
        }]);
        return;
      }
      if (scheduleGenerateIntent && !spreadsheetPreview?.valid) {
        const useOvertime = /(?:允许|使用|启用).{0,6}加班/.test(question) && !/不(?:允许|使用|启用)?.{0,3}加班/.test(question);
        const result = await api("/api/v1/planning/agent/analyze", actor, { method: "POST", body: { workshopId: "WS-MACH-01", horizonStart: localDateISO(), horizonDays: 10, useOvertime, defaultMinutesPerUnit: 30, operationRates: {} } });
        const plan = result.plan;
        setMessages((current) => [...current, { role: "agent", text: `已生成排产方案 ${plan.planNumber}：${plan.assignments.length} 项安排、${plan.shortages.length} 项能力缺口。你可以继续说“执行并发布当前排产方案”。`, meta: `${result.decision} · ${result.reused ? "复用现有方案" : "新方案待执行"}`, action: "open-results" }]);
        return;
      }
      if ((scheduleWriteIntent || scheduleGenerateIntent) && spreadsheetPreview?.valid) {
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
        setMessages((current) => [...current, { role: result.status === "EXECUTED_AND_VERIFIED" ? "agent" : "system", text: result.answer || "请补充订单的名称、数量、交期和工序。", meta: result.status, action: result.status === "EXECUTED_AND_VERIFIED" ? "open-results" : null }]);
        setPendingOrderInstruction(result.status === "NEEDS_INFORMATION" ? instruction : "");
        return;
      }
      if (/(?:执行|写入|发布|同步|导入|更新|创建|新建)/.test(question)) {
        setMessages((current) => [...current, { role: "agent", text: "请说明要处理的人员、订单或排产方案。外部 MES 写入需先配置对应接口。", meta: "等待补充" }]);
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

  const sortedHistory = [...conversationState.conversations].sort((left, right) => String(right.updatedAt).localeCompare(String(left.updatedAt)));
  const history = sortedHistory.filter((conversation) => {
    const query = historyQuery.trim().toLowerCase();
    if (!query) return true;
    const details = conversationDetails(conversation);
    return `${details.title} ${details.preview} ${details.status}`.toLowerCase().includes(query);
  });
  const historyBranches = [];
  history.forEach((conversation) => {
    const branch = branchForConversation(conversation);
    let group = historyBranches.find((item) => item.id === branch.id);
    if (!group) {
      group = { ...branch, conversations: [] };
      historyBranches.push(group);
    }
    group.conversations.push(conversation);
  });
  const activeDetails = conversationDetails(activeConversation);
  const visibleMessages = meaningfulMessages(messages);
  const hasTask = visibleMessages.length > 0;
  return <div
    className={`workspace-layout open-webui-style apple-workspace ${dragActive ? "file-drag-active" : ""} ${resizingPane ? "is-resizing" : ""}`}
    style={{ "--history-width": `${paneWidths.history}px`, "--inspector-width": `${paneWidths.inspector}px` }}
    onDragEnter={handleDragEnter}
    onDragOver={(event) => { if (containsDraggedFiles(event)) event.preventDefault(); }}
    onDragLeave={handleDragLeave}
    onDrop={handleDrop}
  >
    {dragActive && <div className="file-drop-overlay"><span>⇩</span><strong>松开以添加并解析文件</strong><small>文字 / 表格 / DOCX / PDF / 图片 OCR 均在本机处理</small></div>}
    <aside className="conversation-history">
      <div className="conversation-history-head"><div><small>智能工作台</small><strong>任务记录</strong></div><button onClick={createConversation} disabled={sending} title="新建任务">＋</button></div>
      <label className="conversation-search"><span>⌕</span><input value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} placeholder="搜索任务" /></label>
      <div className="conversation-history-list">
        <section className="history-project">
          <div className="history-project-head"><span>⌄</span><strong>演示工厂</strong><small>{history.length}</small></div>
          {historyBranches.map((branch) => <details className="history-branch" open key={branch.id}>
            <summary><span>›</span><strong>{branch.label}</strong><small>{branch.conversations.length}</small></summary>
            <div className="history-branch-items">{branch.conversations.map((conversation) => {
              const details = conversationDetails(conversation);
              return <article className={conversation.id === conversationState.activeId ? "active" : ""} key={conversation.id}>
                <button className="conversation-history-main" onClick={() => selectConversation(conversation.id)} disabled={sending}>
                  <span className="conversation-card-head"><strong>{details.title}</strong><time>{historyTime(conversation.updatedAt)}</time></span>
                  <span className="conversation-card-meta"><i className={details.tone} />{details.status}{details.fileCount ? ` · ${details.fileCount} 个附件` : ""}</span>
                </button>
                <button className="conversation-history-delete" onClick={() => deleteConversation(conversation.id)} disabled={sending} title="删除任务">×</button>
              </article>;
            })}</div>
          </details>)}
        </section>
        {history.length === 0 && <div className="history-empty">没有匹配的任务</div>}
      </div>
    </aside>
    <div className="pane-resizer history-resizer" role="separator" aria-label="调整任务栏宽度" title="拖动调整任务栏宽度" onPointerDown={(event) => beginResize("history", event)} onDoubleClick={() => setPaneWidths((current) => ({ ...current, history: 248 }))}><span /></div>
    <main className="conversation">
    <header className="agent-thread-header"><div><span className={`thread-status ${activeDetails.tone}`}>{activeDetails.status}</span><h1>{hasTask ? activeDetails.title : "新的制造任务"}</h1><p>{hasTask ? `${activeDetails.turnCount || 0} 轮对话${activeDetails.fileCount ? ` · ${activeDetails.fileCount} 个附件` : ""} · 更新于 ${historyTime(activeConversation.updatedAt)}` : "上传生产资料，或直接告诉衡策要完成什么。"}</p></div><div className="thread-actions"><button className="button" onClick={() => onNavigate("models")}>模型设置</button><div className={`connection-pill ${health?.online ? "ok" : "bad"}`}><span />{health?.online ? "已连接" : "等待 Core"}</div></div></header>
    {!hasTask && <section className="conversation-empty"><div className="empty-orbit">✦</div><h2>我能为你处理什么？</h2><p>直接描述生产任务，或拖入人员能力表、订单、图纸和计划文件。</p><div className="quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}</div></section>}
    <div className={`messages ${hasTask ? "" : "empty"}`}>{visibleMessages.map((message, index) => <article className={`message ${message.role}`} key={`${message.role}-${index}`}><div className="message-avatar">{message.role === "agent" ? "衡" : message.role === "user" ? "我" : "!"}</div><div className="message-content"><div>{message.text}</div>{message.action === "execute-capacity" && capacityPreview?.valid && <button className="message-action" onClick={executeCapacityImport}>写入产能管理并核验</button>}{message.action === "confirm-spreadsheet" && spreadsheetPreview?.valid && <button className="message-action" onClick={confirmSpreadsheet}>确认导入并生成排产方案</button>}{message.action === "open-results" && <button className="message-action" onClick={() => onNavigate("results")}>打开排产结果</button>}{message.action === "open-capacity" && <button className="message-action" onClick={() => onNavigate("capacity")}>打开产能管理</button>}{messageMetaLabel(message.meta) && <small>{messageMetaLabel(message.meta)}</small>}</div></article>)}{sending && <article className="message agent"><div className="message-avatar">衡</div><div className="thinking"><span /><span /><span /></div></article>}<div ref={endRef} /></div>
    <div className="composer-wrap"><div className="composer">{files.length > 0 && <div className="attachments">{files.map((file, index) => <div key={file.id || `${file.path}-${index}`}><span>▤</span><div><strong>{file.name}</strong><small>{formatBytes(file.size)} · {file.parseStatus === "PARSING" ? "正在解析…" : file.parseStatus === "PARSED" ? "已解析" : file.parseStatus === "NO_TEXT" ? "未提取到文字" : file.parseStatus === "ERROR" ? "解析失败" : "等待解析"}</small></div><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}</div>}<textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} placeholder="输入生产任务…" /><div className="composer-actions"><button className="composer-file-button" disabled={sending} onClick={pickFiles} aria-label="选择文件并在本机解析" data-tooltip="选择文件并在本机解析">＋</button><button className="send-button" disabled={(!prompt.trim() && !files.some((file) => file.parseStatus === "PARSED")) || sending} onClick={() => send()}>发送 <span>↑</span></button></div></div></div>
  </main>
  <div className="pane-resizer inspector-resizer" role="separator" aria-label="调整资料栏宽度" title="拖动调整资料栏宽度" onPointerDown={(event) => beginResize("inspector", event)} onDoubleClick={() => setPaneWidths((current) => ({ ...current, inspector: 292 }))}><span /></div>
  <aside className="inspector"><section><div className="section-heading"><h3>运行状态</h3><span className="status-chip">实时</span></div><div className="runtime-card"><div className="runtime-orbit"><span>✦</span></div><strong>衡策正在待命</strong><p>只在授权的数据与工具范围内分析、计算和执行。</p><div className="runtime-grid"><div><small>智能体级别</small><b>{health?.detail?.schedulingAgentLevel || "状态未知"}</b></div><div><small>自治循环</small><b>{health?.detail?.schedulingAutonomyLoop || "—"}</b></div><div><small>模型</small><b>{health?.detail?.modelName ? `${health.detail.modelProvider} · ${health.detail.modelName}` : health?.detail?.modelGateway || "—"}</b></div><div><small>身份</small><b>{health?.detail?.authMode || "—"}</b></div></div></div></section>{evidence.length > 0 && <section><div className="section-heading"><h3>本次读取依据</h3><span className="status-chip">{evidence.length} 段</span></div><div className="evidence-list">{evidence.slice(0, 6).map((item) => <article key={item.chunkId}><small>{item.documentName}</small><strong>{item.location}</strong><p>{item.excerpt}</p></article>)}</div></section>}<section><div className="section-heading"><h3>任务入口</h3></div><div className="agent-links"><button onClick={() => onNavigate("planning")}><span>▦</span><div><strong>生成排产方案</strong><small>有限产能与工艺约束</small></div></button><button onClick={() => onNavigate("results")}><span>◫</span><div><strong>检查排产结果</strong><small>人员周计划与能力缺口</small></div></button><button onClick={() => onNavigate("capacity")}><span>◒</span><div><strong>维护产能</strong><small>人员与工作单元能力</small></div></button></div></section><section className="boundary-card"><span>L4 安全边界</span><strong>预授权范围内自主执行</strong><p>排产发布必须通过策略、版本和结果核验；超界或失败立即停止并转人工。设备控制和质量放行不在本域授权内。</p></section></aside>
  </div>;
}
