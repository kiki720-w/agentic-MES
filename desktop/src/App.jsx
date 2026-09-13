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
  { id: "workspace", label: "Agent 工作台", hint: "对话、文件与任务", icon: "✦" },
  { id: "tower", label: "制造控制塔", hint: "全局态势与风险", icon: "⌁" },
  { id: "planning", label: "有限产能排产", hint: "输入、工艺与方案", icon: "▦" },
  { id: "results", label: "排产结果", hint: "人员日程与证据", icon: "◫" },
  { id: "capacity", label: "产能管理", hint: "人员与工作单元", icon: "◒" },
  { id: "models", label: "模型与数据边界", hint: "本地或云端", icon: "⌘" },
  { id: "capabilities", label: "AI 能力验收", hint: "实测、证据与模型对比", icon: "✓" },
];

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
  return <div className="window-controls no-drag"><button aria-label="最小化" onClick={() => desktop.window.minimize()}>—</button><button aria-label="最大化" onClick={() => desktop.window.maximize()}>□</button><button className="close" aria-label="关闭" onClick={() => desktop.window.close()}>×</button></div>;
}

function Sidebar({ active, onChange, online, agentLevel, actor, profiles, onActorChange }) {
  return <aside className="sidebar">
    <div className="brand drag-region"><img src={appIcon} alt="" /><div><strong>CAPAXION <em>v{packageInfo.version}</em></strong><small>MANUFACTURING DECISION OS</small></div></div>
    <div className="workspace-switcher"><span className="factory-avatar">01</span><div><strong>演示工厂</strong><small>机械加工中心</small></div><span className="chevron">⌄</span></div>
    <nav><div className="nav-caption">工作空间</div>{navItems.map((item) => <button key={item.id} className={active === item.id ? "active" : ""} onClick={() => onChange(item.id)}><span className="nav-icon">{item.icon}</span><span><strong>{item.label}</strong><small>{item.hint}</small></span></button>)}</nav>
    <div className="sidebar-bottom"><div className="agent-level"><span className="pulse" />衡策 · {agentLevel || "状态未知"}</div><div className="core-state"><span className={online ? "online" : "offline"} />{online ? "Core 已连接" : "Core 未连接"}</div><label className="profile"><span>{actor === "demo-supervisor" ? "DS" : actor === "demo-quality" ? "DQ" : "DP"}</span><div><strong>当前演示身份</strong><select value={actor} onChange={(e) => onActorChange(e.target.value)}>{profiles.map((profile) => <option key={profile.subjectId} value={profile.subjectId}>{profile.displayName}</option>)}</select></div></label></div>
  </aside>;
}

export default function App() {
  const [active, setActive] = useState("workspace");
  const [health, setHealth] = useState({ online: false, detail: null });
  const [actor, setActor] = useState("demo-planner");
  const [profiles, setProfiles] = useState([{ subjectId: "demo-planner", displayName: "Demo Planner" }]);
  const [zoom, setZoom] = useState(110);
  const [incomingDrop, setIncomingDrop] = useState(null);
  const item = navItems.find((candidate) => candidate.id === active) || navItems[0];

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
    <section className="app-main"><header className="titlebar drag-region"><div><span>{item.label}</span><small>{item.hint}</small></div><div className="titlebar-actions no-drag"><div className="zoom-control" title="界面缩放"><button disabled={zoom <= 100} onClick={async () => setZoom(await desktop.window.zoom(-0.1))}>A−</button><span>{zoom}%</span><button disabled={zoom >= 140} onClick={async () => setZoom(await desktop.window.zoom(0.1))}>A＋</button></div><span className="environment"><i />LOCAL FACTORY</span><WindowControls /></div></header><div className="content-area">
      {active === "workspace" && <AgentWorkspace health={health} incomingDrop={incomingDrop} onDropHandled={(id) => setIncomingDrop((current) => current?.id === id ? null : current)} {...pageProps} />}
      {active === "tower" && <ControlTower {...pageProps} />}
      {active === "planning" && <PlanningCenter {...pageProps} />}
      {active === "results" && <PlanningResults {...pageProps} />}
      {active === "capacity" && <CapacityCenter {...pageProps} />}
      {active === "models" && <ModelSettings {...pageProps} />}
      {active === "capabilities" && <CapabilityCenter {...pageProps} />}
    </div></section>
  </div></ErrorBoundary>;
}
