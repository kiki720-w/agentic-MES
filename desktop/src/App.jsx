import { useEffect, useMemo, useRef, useState } from "react";

const desktop = window.capaxion || {
  coreUrl: "http://127.0.0.1:8000",
  platform: "browser",
  window: { minimize() {}, maximize() {}, close() {} },
  core: {
    async status() {
      try {
        const response = await fetch("http://127.0.0.1:8000/health/ready");
        return { online: response.ok, detail: response.ok ? await response.json() : null };
      } catch (error) {
        return { online: false, detail: error.message };
      }
    },
    async request(request) {
      const response = await fetch(`http://127.0.0.1:8000${request.path}`, {
        method: request.method || "GET",
        headers: { "Content-Type": "application/json", "X-Dev-Actor": request.actor || "demo-planner" },
        body: request.body ? JSON.stringify(request.body) : undefined,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || response.statusText);
      return payload;
    },
    async upload() {
      throw new Error("文件预检仅在 CAPAXION 桌面客户端中可用");
    },
  },
  files: { async pick() { return []; } },
};

const navItems = [
  { id: "workspace", label: "Agent 工作台", hint: "对话与任务", icon: "✦" },
  { id: "tower", label: "制造控制塔", hint: "全局态势", icon: "⌁", path: "/" },
  { id: "planning", label: "有限产能排产", hint: "方案与约束", icon: "▦", path: "/planning" },
  { id: "results", label: "排产结果", hint: "人员与资源", icon: "◫", path: "/planning/results" },
  { id: "capacity", label: "产能管理", hint: "人员与工作单元", icon: "◒", path: "/capacity" },
  { id: "models", label: "模型与连接", hint: "本地或云端", icon: "⌘", path: "/settings/models" },
];

const quickPrompts = [
  "检查当前计划的延期风险",
  "说明产能缺口及计算依据",
  "生成一个不使用加班的排产方案",
];

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function WindowControls() {
  if (desktop.platform === "browser") return null;
  return (
    <div className="window-controls no-drag">
      <button aria-label="最小化" onClick={() => desktop.window.minimize()}>—</button>
      <button aria-label="最大化" onClick={() => desktop.window.maximize()}>□</button>
      <button className="close" aria-label="关闭" onClick={() => desktop.window.close()}>×</button>
    </div>
  );
}

function Sidebar({ active, onChange, online }) {
  return (
    <aside className="sidebar">
      <div className="brand drag-region">
        <div className="brand-mark"><span>C</span></div>
        <div>
          <strong>CAPAXION</strong>
          <small>MANUFACTURING DECISION OS</small>
        </div>
      </div>
      <div className="workspace-switcher">
        <span className="factory-avatar">01</span>
        <div><strong>演示工厂</strong><small>机械加工中心</small></div>
        <span className="chevron">⌄</span>
      </div>
      <nav>
        <div className="nav-caption">工作空间</div>
        {navItems.map((item) => (
          <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onChange(item.id)}>
            <span className="nav-icon">{item.icon}</span>
            <span><strong>{item.label}</strong><small>{item.hint}</small></span>
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="agent-level"><span className="pulse" />衡策 · L3 BOUNDED</div>
        <div className="core-state"><span className={online ? "online" : "offline"} />{online ? "Core 已连接" : "Core 未连接"}</div>
        <div className="profile"><span>DP</span><div><strong>Demo Planner</strong><small>计划员工作区</small></div></div>
      </div>
    </aside>
  );
}

function Inspector({ health, activity }) {
  const detail = health?.detail || {};
  return (
    <aside className="inspector">
      <section>
        <div className="section-heading"><h3>运行状态</h3><span className="status-chip">实时</span></div>
        <div className="runtime-card">
          <div className="runtime-orbit"><span>✦</span></div>
          <strong>衡策正在待命</strong>
          <p>只在授权的数据与工具范围内分析、计算和提交方案。</p>
          <div className="runtime-grid">
            <div><small>智能体级别</small><b>{detail.schedulingAgentLevel || "L3_BOUNDED"}</b></div>
            <div><small>存储</small><b>{detail.storageBackend || "—"}</b></div>
            <div><small>模型</small><b>{detail.modelGateway || "—"}</b></div>
            <div><small>身份</small><b>{detail.authMode || "—"}</b></div>
          </div>
        </div>
      </section>
      <section>
        <div className="section-heading"><h3>最近活动</h3><button>全部</button></div>
        <div className="activity-list">
          {activity.map((item, index) => (
            <div className="activity" key={`${item.title}-${index}`}>
              <span className={`activity-dot ${item.tone}`} />
              <div><strong>{item.title}</strong><p>{item.detail}</p><small>{item.time}</small></div>
            </div>
          ))}
        </div>
      </section>
      <section className="boundary-card">
        <span>安全边界</span>
        <strong>不会直接控制设备</strong>
        <p>排产发布、外部回写和高风险动作仍需策略校验与独立审批。</p>
      </section>
    </aside>
  );
}

function AgentWorkspace({ health }) {
  const [messages, setMessages] = useState([
    {
      role: "agent",
      text: "你好，我是衡策。你可以让我检查延期风险、解释产能缺口、生成排产方案，或把 Excel、PDF 和现场图片交给我分析。",
      meta: "L3 受控智能体 · 数据来源可追溯",
    },
  ]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const [spreadsheetPreview, setSpreadsheetPreview] = useState(null);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function pickFiles() {
    const picked = await desktop.files.pick();
    if (!picked.length) return;
    setFiles((current) => [...current, ...picked]);
    const spreadsheet = picked.find((file) => /\.(xlsx|csv)$/i.test(file.name));
    if (!spreadsheet) {
      setMessages((current) => [...current, {
        role: "agent",
        text: `已在本机选择 ${picked.map((file) => file.name).join("、")}。当前版本不会伪装识图或 PDF 抽取能力；接入本地多模态与文档解析器后再进入诊断链。`,
        meta: "文件未上传至外部服务",
      }]);
      return;
    }
    setSending(true);
    try {
      const preview = await desktop.core.upload({
        path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01",
        filePath: spreadsheet.path,
        actor: "demo-planner",
      });
      setSpreadsheetPreview(preview);
      const stats = preview.stats || {};
      const issueCount = stats.errorCount || preview.issues?.length || 0;
      setMessages((current) => [...current, {
        role: "agent",
        text: `已预检 ${spreadsheet.name}：${stats.workOrderCount || 0} 个工单、${stats.operationCount || 0} 道工序、${stats.resourceCount || 0} 个产能资源。${preview.valid ? "字段与业务关联校验通过。" : `发现 ${issueCount} 项问题，尚未写入。`}`,
        meta: preview.valid ? "尚未写入 · 等待人工确认" : "预检失败 · 未写入生产快照",
        action: preview.valid ? "confirm-spreadsheet" : null,
      }]);
    } catch (error) {
      setMessages((current) => [...current, { role: "system", text: error.message, meta: "文件预检未执行" }]);
    } finally {
      setSending(false);
    }
  }

  async function confirmSpreadsheet() {
    if (!spreadsheetPreview?.valid || sending) return;
    setSending(true);
    try {
      const result = await desktop.core.request({
        path: "/api/v1/planning/imports/spreadsheet/confirm",
        method: "POST",
        actor: "demo-planner",
        body: {
          previewFingerprint: spreadsheetPreview.previewFingerprint,
          snapshot: spreadsheetPreview.snapshot,
          runAgent: true,
          horizonStart: new Date().toISOString().slice(0, 10),
          horizonDays: 10,
          useOvertime: false,
          defaultMinutesPerUnit: 30,
        },
      });
      const plan = result.agent?.plan;
      setMessages((current) => [...current, {
        role: "agent",
        text: plan
          ? `已生成 ${plan.planNumber}：${plan.assignments.length} 项安排、${plan.shortages.length} 项能力缺口。请在“排产结果”中核对，主管批准后才能发布。`
          : "已生成可追溯的制造输入快照。",
        meta: "虚拟员工 衡策 · L3 BOUNDED",
      }]);
      setSpreadsheetPreview(null);
      setFiles([]);
    } catch (error) {
      setMessages((current) => [...current, { role: "system", text: error.message, meta: "导入与排产未执行" }]);
    } finally {
      setSending(false);
    }
  }

  async function send(value = prompt) {
    const question = value.trim();
    if (!question || sending) return;
    setMessages((current) => [...current, { role: "user", text: question, meta: files.length ? `${files.length} 个附件待处理` : "文字指令" }]);
    setPrompt("");
    setSending(true);
    try {
      const result = await desktop.core.request({
        path: "/api/v1/agent/chat",
        method: "POST",
        actor: "demo-planner",
        body: { question },
      });
      setMessages((current) => [...current, {
        role: "agent",
        text: result.answer || "任务已完成。",
        meta: `${result.policyDecision || "ALLOW"} · ${result.provider || "规则与模型网关"}`,
      }]);
    } catch (error) {
      setMessages((current) => [...current, { role: "system", text: error.message, meta: "请求未执行" }]);
    } finally {
      setSending(false);
      setFiles([]);
    }
  }

  return (
    <div className="workspace-layout">
      <main className="conversation">
        <div className="conversation-head">
          <div><span className="eyebrow">制造决策会话</span><h1>今天需要处理什么？</h1><p>描述目标，衡策会选择数据、算法和工具，并在执行前说明影响。</p></div>
          <div className={`connection-pill ${health?.online ? "ok" : "bad"}`}><span />{health?.online ? "工厂数据已连接" : "等待 Core"}</div>
        </div>
        <div className="quick-prompts">
          {quickPrompts.map((item) => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}
        </div>
        <div className="messages">
          {messages.map((message, index) => (
            <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
              <div className="message-avatar">{message.role === "agent" ? "衡" : message.role === "user" ? "我" : "!"}</div>
              <div className="message-content">
                <div>{message.text}</div>
                {message.action === "confirm-spreadsheet" && spreadsheetPreview?.valid && (
                  <button className="message-action" onClick={confirmSpreadsheet}>确认导入并生成排产方案</button>
                )}
                <small>{message.meta}</small>
              </div>
            </article>
          ))}
          {sending && <article className="message agent"><div className="message-avatar">衡</div><div className="thinking"><span /><span /><span /></div></article>}
          <div ref={endRef} />
        </div>
        <div className="composer-wrap">
          {files.length > 0 && <div className="attachments">{files.map((file, index) => <div key={`${file.path}-${index}`}><span>▤</span><div><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></div><button onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></div>)}</div>}
          <div className="composer">
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
              }}
              placeholder="向衡策描述目标，或拖入制造资料…"
            />
            <div className="composer-actions">
              <div><button className="icon-button" onClick={pickFiles} title="添加附件">＋</button><span>Excel · PDF · 图片</span></div>
              <button className="send-button" disabled={!prompt.trim() || sending} onClick={() => send()}>发送 <span>↑</span></button>
            </div>
          </div>
          <p className="composer-note">衡策可能出错。生产动作始终受权限、审批、版本与安全策略约束。</p>
        </div>
      </main>
      <Inspector health={health} activity={[
        { tone: "cyan", title: "排产引擎待命", detail: "有限产能 APS 可用", time: "刚刚" },
        { tone: "green", title: "数据镜像已就绪", detail: "来源与版本已记录", time: "2 分钟前" },
        { tone: "amber", title: "连接器等待配置", detail: "当前使用演示数据", time: "需要处理" },
      ]} />
    </div>
  );
}

function EmbeddedPage({ item, keyValue }) {
  const source = useMemo(() => `${desktop.coreUrl}${item.path}?desktop=1&v=${keyValue}`, [item.path, keyValue]);
  return <iframe className="embedded-page" src={source} title={item.label} />;
}

export default function App() {
  const [active, setActive] = useState("workspace");
  const [health, setHealth] = useState({ online: false, detail: null });
  const [refreshKey, setRefreshKey] = useState(0);
  const item = navItems.find((candidate) => candidate.id === active) || navItems[0];

  useEffect(() => {
    let mounted = true;
    async function update() {
      const next = await desktop.core.status();
      if (mounted) setHealth(next);
    }
    update();
    const timer = setInterval(update, 10000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  return (
    <div className="app-shell">
      <Sidebar active={active} onChange={setActive} online={health.online} />
      <section className="app-main">
        <header className="titlebar drag-region">
          <div><span>{item.label}</span><small>{active === "workspace" ? "与虚拟员工协作并处理制造任务" : item.hint}</small></div>
          <div className="titlebar-actions no-drag">
            {active !== "workspace" && <button className="refresh" onClick={() => setRefreshKey((value) => value + 1)}>↻ 刷新</button>}
            <span className="environment"><i />LOCAL FACTORY</span>
            <WindowControls />
          </div>
        </header>
        <div className="content-area">
          {active === "workspace" ? <AgentWorkspace health={health} /> : <EmbeddedPage item={item} keyValue={refreshKey} />}
        </div>
      </section>
    </div>
  );
}
