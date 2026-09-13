import { Component, useEffect, useState } from "react";
import appIcon from "../assets/capaxion-icon.png";
import packageInfo from "../package.json";
import AgentWorkspace from "./pages/AgentWorkspace";
import CapabilityCenter from "./pages/CapabilityCenter";
import CapacityCenter from "./pages/CapacityCenter";
import ControlTower from "./pages/ControlTower";
import ModelSettings from "./pages/ModelSettings";
import PlanningCenter from "./pages/PlanningCenter";
import PlanningResults from "./pages/PlanningResults";
import { desktop } from "./platform";

const navItems = [
  { id: "workspace", label: "Agent 工作台", hint: "对话、文件与任务" },
  { id: "tower", label: "制造控制塔", hint: "全局态势与风险" },
  { id: "planning", label: "有限产能排产", hint: "输入、工艺与方案" },
  { id: "results", label: "排产结果", hint: "人员日程与证据" },
  { id: "capacity", label: "产能管理", hint: "人员与工作单元" },
  { id: "models", label: "模型与数据边界", hint: "本地或云端" },
  { id: "capabilities", label: "AI 能力验收", hint: "实测、证据与模型对比" },
];

function NavGlyph({ id }) {
  const paths = {
    workspace: <><path d="M12 3l1.1 3.2L16 7.4l-2.9 1.2L12 12l-1.1-3.4L8 7.4l2.9-1.2L12 3Z" /><path d="M6 13l.8 2.2L9 16l-2.2.8L6 19l-.8-2.2L3 16l2.2-.8L6 13Z" /><path d="M17.5 13l.6 1.7 1.9.8-1.9.7-.6 1.8-.6-1.8-1.9-.7 1.9-.8.6-1.7Z" /></>,
    tower: <><path d="M4 18V9" /><path d="M10 18V5" /><path d="M16 18v-7" /><path d="M3 18h16" /></>,
    planning: <><rect x="3.5" y="5" width="17" height="15" rx="3" /><path d="M7 3v4M17 3v4M3.5 9h17M7.5 13h3M13.5 13h3M7.5 16.5h3" /></>,
    results: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
    capacity: <><circle cx="9" cy="8" r="3" /><circle cx="16.5" cy="9" r="2.5" /><path d="M3.5 19c.5-4 2.5-6 5.5-6s5 2 5.5 6M14 14c3-.6 5.5 1.1 6 4" /></>,
    models: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6" /></>,
    capabilities: <><circle cx="12" cy="12" r="9" /><path d="m8 12 2.5 2.5L16 9" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[id]}</svg>;
}

class ErrorBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return <div className="fatal-error"><span>!</span><h1>页面组件发生异常</h1><p>{this.state.error.message}</p><button className="button primary" onClick={() => window.location.reload()}>重新加载桌面端</button></div>;
  }
}

function WindowControls() {
  if (desktop.platform === "browser") return null;
  return <div className="window-controls no-drag"><button className="close" aria-label="关闭" onClick={() => desktop.window.close()}>×</button><button className="minimize" aria-label="最小化" onClick={() => desktop.window.minimize()}>−</button><button className="maximize" aria-label="最大化" onClick={() => desktop.window.maximize()}>+</button></div>;
}

function Sidebar({ active, onChange, online, agentLevel, actor, profiles, onActorChange }) {
  return <aside className="sidebar">
    <WindowControls />
    <div className="brand drag-region"><img src={appIcon} alt="" /><div><strong>CAPAXION <em>v{packageInfo.version}</em></strong><small>制造智能系统</small></div></div>
    <div className="workspace-switcher"><span className="factory-avatar">01</span><div><strong>演示工厂</strong><small>机械加工中心</small></div><span className="chevron">⌄</span></div>
    <nav><div className="nav-caption">工作区</div>{navItems.map((item) => <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onChange(item.id)}><span className="nav-icon"><NavGlyph id={item.id} /></span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}</nav>
    <div className="sidebar-bottom"><div className="agent-level"><span className="pulse" />衡策 · {agentLevel || "状态未知"}</div><div className="core-state"><span className={online ? "online" : "offline"} />{online ? "Core 已连接" : "Core 未连接"}</div><label className="profile"><span>{actor === "demo-supervisor" ? "DS" : actor === "demo-quality" ? "DQ" : "DP"}</span><div><strong>当前演示身份</strong><select value={actor} onChange={(e) => onActorChange(e.target.value)}>{profiles.map((profile) => <option key={profile.subjectId} value={profile.subjectId}>{profile.displayName}</option>)}</select></div></label></div>
  </aside>;
}

export default function App() {
  const [active, setActive] = useState("workspace");
  const [health, setHealth] = useState({ online: false, detail: null });
  const [actor, setActor] = useState("demo-planner");
  const [profiles, setProfiles] = useState([{ subjectId: "demo-planner", displayName: "Demo Planner" }]);
  const [zoom, setZoom] = useState(110);
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("capaxion.theme") || "light"; } catch { return "light"; }
  });
  const [incomingDrop, setIncomingDrop] = useState(null);
  const item = navItems.find((candidate) => candidate.id === active) || navItems[0];

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("capaxion.theme", theme); } catch { /* Keep the selected theme for this session. */ }
  }, [theme]);

  useEffect(() => {
    let mounted = true;
    async function update() { const next = await desktop.core.status(); if (mounted) setHealth(next); }
    update();
    const timer = setInterval(update, 10000);
    desktop.core.request({ path: "/api/v1/system/auth-config", method: "GET" }).then((result) => {
      if (!mounted || !result.devProfiles?.length) return;
      setProfiles(result.devProfiles);
      if (!result.devProfiles.some((profile) => profile.subjectId === actor)) setActor(result.devProfiles[0].subjectId);
    }).catch(() => {});
    return () => { mounted = false; clearInterval(timer); };
  }, []);

  useEffect(() => {
    desktop.files.onDrop?.((files, error) => {
      setIncomingDrop({ id: `${Date.now()}-${Math.random()}`, files, error });
      setActive("workspace");
    });
    return () => desktop.files.offDrop?.();
  }, []);

  const pageProps = { actor, onNavigate: setActive };
  return <ErrorBoundary><div className="app-shell">
    <Sidebar active={active} onChange={setActive} online={health.online} agentLevel={health.detail?.schedulingAgentLevel} actor={actor} profiles={profiles} onActorChange={setActor} />
    <section className="app-main"><header className="titlebar drag-region"><div><span>{item.label}</span><small>{item.hint}</small></div><div className="titlebar-actions no-drag"><button className="theme-toggle" onClick={() => setTheme((current) => current === "light" ? "dark" : "light")} title={theme === "light" ? "切换深色模式" : "切换浅色模式"}>{theme === "light" ? "◐" : "☼"}<span>{theme === "light" ? "浅色" : "深色"}</span></button><div className="zoom-control" title="界面缩放"><button disabled={zoom <= 100} onClick={async () => setZoom(await desktop.window.zoom(-0.1))}>−</button><span>{zoom}%</span><button disabled={zoom >= 140} onClick={async () => setZoom(await desktop.window.zoom(0.1))}>＋</button></div><span className="environment"><i />本机</span></div></header><div className="content-area">
      <div className="workspace-page" hidden={active !== "workspace"}><AgentWorkspace health={health} incomingDrop={incomingDrop} onDropHandled={(id) => setIncomingDrop((current) => current?.id === id ? null : current)} {...pageProps} /></div>
      {active === "tower" && <ControlTower {...pageProps} />}
      {active === "planning" && <PlanningCenter {...pageProps} />}
      {active === "results" && <PlanningResults {...pageProps} />}
      {active === "capacity" && <CapacityCenter {...pageProps} />}
      {active === "models" && <ModelSettings {...pageProps} />}
      {active === "capabilities" && <CapabilityCenter {...pageProps} />}
    </div></section>
  </div></ErrorBoundary>;
}
