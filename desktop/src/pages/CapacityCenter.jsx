import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState, MetricCard, PageHeader, Pager, Panel, SearchBox, StatusPill, Toast, usePagination } from "../components";
import { api } from "../platform";

const blankResource = { code: "", name: "", resourceType: "PERSON", workshopId: "WS-MACH-01", workCenterId: "", dailyCapacityMinutes: 480, overtimeCapacityMinutes: 600, capabilityCodes: "", active: true };

export default function CapacityCenter({ actor }) {
  const [resources, setResources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [type, setType] = useState("ALL");
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { setResources(await api("/api/v1/planning/resources?workshopId=WS-MACH-01", actor)); }
    catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }, [actor]);
  useEffect(() => { load(); }, [load]);
  const filtered = useMemo(() => resources.filter((item) => (type === "ALL" || item.resourceType === type) && Object.values(item).some((value) => String(value ?? "").toLowerCase().includes(query.toLowerCase()))), [resources, query, type]);
  const pagination = usePagination(filtered, 15);
  const people = resources.filter((item) => item.resourceType === "PERSON");
  const cells = resources.filter((item) => item.resourceType !== "PERSON");
  const totalMinutes = resources.reduce((sum, item) => sum + (item.active ? item.dailyCapacityMinutes : 0), 0);

  async function save() {
    setBusy(true);
    const body = { ...editing, dailyCapacityMinutes: Number(editing.dailyCapacityMinutes), overtimeCapacityMinutes: Number(editing.overtimeCapacityMinutes), capabilityCodes: String(editing.capabilityCodes).split(/[,，\s]+/).filter(Boolean).map((item) => item.toUpperCase()) };
    try {
      if (editing.resourceId) {
        await api(`/api/v1/planning/resources/${editing.resourceId}`, actor, { method: "PUT", body: { expectedVersion: editing.version, name: body.name, workCenterId: body.workCenterId, dailyCapacityMinutes: body.dailyCapacityMinutes, overtimeCapacityMinutes: body.overtimeCapacityMinutes, capabilityCodes: body.capabilityCodes, active: body.active } });
      } else {
        await api("/api/v1/planning/resources", actor, { method: "POST", body: { code: body.code, name: body.name, resourceType: body.resourceType, workshopId: body.workshopId, workCenterId: body.workCenterId, dailyCapacityMinutes: body.dailyCapacityMinutes, overtimeCapacityMinutes: body.overtimeCapacityMinutes, capabilityCodes: body.capabilityCodes } });
      }
      setEditing(null); setToast({ message: "产能资源已保存并形成新版本。", tone: "success" }); await load();
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  if (loading && !resources.length) return <LoadingState />;
  if (error && !resources.length) return <ErrorState error={error} onRetry={load} />;
  return <div className="native-page capacity-page">
    <Toast message={toast?.message} tone={toast?.tone} onClose={() => setToast(null)} />
    <PageHeader eyebrow="CAPACITY MASTER" title="人员与工作单元产能" description="统一维护人员、设备单元、每日工时、加班上限和可执行工序。" actions={<><button className="button" onClick={load}>↻ 刷新</button><button className="button primary" onClick={() => setEditing({ ...blankResource })}>新增产能资源</button></>} />
    <div className="metric-grid four"><MetricCard label="人员资源" value={people.length} note={`${people.filter((item) => item.active).length} 个启用`} icon="人" /><MetricCard label="工作单元" value={cells.length} note={`${cells.filter((item) => item.active).length} 个启用`} icon="机" /><MetricCard label="每日标准产能" value={`${Math.round(totalMinutes / 60)}h`} note="所有启用资源合计" icon="◷" /><MetricCard label="能力标签" value={new Set(resources.flatMap((item) => item.capabilityCodes)).size} note="可用于工序匹配" icon="◇" /></div>
    <Panel title="产能资源台账" subtitle="点击编辑即可修改人员或工作单元能力；保存时执行版本冲突校验。" actions={<div className="table-tools"><SearchBox value={query} onChange={(value) => { setQuery(value); pagination.setPage(1); }} placeholder="搜索人员、编码、工作中心" /><select value={type} onChange={(e) => { setType(e.target.value); pagination.setPage(1); }}><option value="ALL">全部类型</option><option value="PERSON">人员</option><option value="CELL">工作单元</option><option value="EQUIPMENT">设备</option></select></div>}>
      <div className="data-table-wrap"><table className="data-table capacity-table"><thead><tr><th>资源</th><th>类型</th><th>工作中心</th><th>每日产能</th><th>加班上限</th><th>能力</th><th>状态</th><th></th></tr></thead><tbody>{pagination.rows.map((item) => <tr key={item.resourceId}><td><b>{item.name}</b><small>{item.code}</small></td><td>{item.resourceType === "PERSON" ? "人员" : item.resourceType === "CELL" ? "工作单元" : "设备"}</td><td>{item.workCenterId}</td><td>{item.dailyCapacityMinutes} 分钟</td><td>{item.overtimeCapacityMinutes} 分钟</td><td><div className="tag-list">{item.capabilityCodes.map((tag) => <span key={tag}>{tag}</span>)}</div></td><td><StatusPill value={item.active ? "启用" : "停用"} tone={item.active ? "published" : "unknown"} /></td><td><button className="text-button" onClick={() => setEditing({ ...item, capabilityCodes: item.capabilityCodes.join(", ") })}>编辑</button></td></tr>)}</tbody></table></div>
      <Pager page={pagination.page} pages={pagination.pages} total={filtered.length} onChange={pagination.setPage} />
    </Panel>
    {editing && <div className="drawer-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setEditing(null); }}><aside className="edit-drawer"><div className="drawer-head"><div><span>{editing.resourceId ? "EDIT CAPACITY" : "NEW CAPACITY"}</span><h2>{editing.resourceId ? editing.name : "新增产能资源"}</h2></div><button onClick={() => setEditing(null)}>×</button></div><div className="drawer-body">
      <label><span>资源名称</span><input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
      {!editing.resourceId && <><label><span>资源编码</span><input value={editing.code} onChange={(e) => setEditing({ ...editing, code: e.target.value })} /></label><label><span>资源类型</span><select value={editing.resourceType} onChange={(e) => setEditing({ ...editing, resourceType: e.target.value })}><option value="PERSON">人员</option><option value="CELL">工作单元</option><option value="EQUIPMENT">设备</option></select></label></>}
      <label><span>工作中心</span><input value={editing.workCenterId} onChange={(e) => setEditing({ ...editing, workCenterId: e.target.value })} /></label>
      <div className="form-two"><label><span>每日产能（分钟）</span><input type="number" value={editing.dailyCapacityMinutes} onChange={(e) => setEditing({ ...editing, dailyCapacityMinutes: e.target.value })} /></label><label><span>加班上限（分钟）</span><input type="number" value={editing.overtimeCapacityMinutes} onChange={(e) => setEditing({ ...editing, overtimeCapacityMinutes: e.target.value })} /></label></div>
      <label><span>能力代码（逗号分隔）</span><input value={editing.capabilityCodes} onChange={(e) => setEditing({ ...editing, capabilityCodes: e.target.value })} placeholder="TURN, MILL, INSPECT" /></label>
      {editing.resourceId && <label className="check-row"><input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /><span>启用该资源</span></label>}
      <div className="drawer-note">保存后将产生新版本。已生成计划不会被静默重写，需要重新运行排产。</div>
    </div><div className="drawer-actions"><button className="button" onClick={() => setEditing(null)}>取消</button><button className="button primary" disabled={busy || !editing.name || !editing.workCenterId || (!editing.resourceId && !editing.code)} onClick={save}>{busy ? "保存中…" : "保存新版本"}</button></div></aside></div>}
  </div>;
}
