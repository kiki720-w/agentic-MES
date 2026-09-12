import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, Panel, SearchBox, StatusPill } from "../components";
import { api, formatDate, formatMinutes, statusLabel, workingDates } from "../platform";

export default function PlanningResults({ actor, onNavigate }) {
  const [plans, setPlans] = useState([]);
  const [resources, setResources] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState("people");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextPlans, nextResources] = await Promise.all([
        api("/api/v1/planning/plans?workshopId=WS-MACH-01&limit=50", actor),
        api("/api/v1/planning/resources?workshopId=WS-MACH-01", actor),
      ]);
      setPlans(nextPlans); setResources(nextResources);
      setSelectedId((current) => current && nextPlans.some((plan) => plan.planId === current) ? current : nextPlans[0]?.planId || "");
    } catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }, [actor]);
  useEffect(() => { load(); }, [load]);
  const plan = plans.find((item) => item.planId === selectedId);
  const dates = useMemo(() => plan ? workingDates(plan.horizonStart, plan.horizonDays) : [], [plan]);
  const assignments = useMemo(() => (plan?.assignments || []).filter((item) => Object.values(item).some((value) => String(value ?? "").toLowerCase().includes(query.toLowerCase()))), [plan, query]);
  const grouped = useMemo(() => {
    const map = new Map();
    assignments.forEach((item) => {
      const key = view === "people" && item.resourceType === "PERSON" ? item.resourceId : view === "people" ? `other-${item.resourceId}` : item.resourceId;
      if (!map.has(key)) map.set(key, { id: key, name: item.resourceName, code: item.resourceCode, type: item.resourceType, items: [] });
      map.get(key).items.push(item);
    });
    return [...map.values()].sort((a, b) => (a.type === "PERSON" ? -1 : 1) - (b.type === "PERSON" ? -1 : 1));
  }, [assignments, view]);
  const capacityById = useMemo(() => new Map(resources.map((item) => [item.resourceId, item])), [resources]);
  if (loading && !plan) return <LoadingState text="正在计算人员与资源排程视图…" />;
  if (error && !plan) return <ErrorState error={error} onRetry={load} />;
  if (!plan) return <div className="native-page"><PageHeader eyebrow="SCHEDULE EVIDENCE" title="排产结果" description="展示人员、资源和计算依据。" /><EmptyState title="尚无排产方案" description="请先在排产中心生成方案。" /></div>;
  return <div className="native-page results-page">
    <PageHeader eyebrow="SCHEDULE EVIDENCE" title="排产结果与人员周计划" description="先看每个人每天做什么，再下钻工单、资源负荷、能力缺口和计算依据。" actions={<><select className="select-control" value={selectedId} onChange={(e) => setSelectedId(e.target.value)}>{plans.map((item) => <option key={item.planId} value={item.planId}>{item.planNumber} · {statusLabel(item.status)}</option>)}</select><button className="button" onClick={load}>↻ 刷新</button></>} />
    <div className="metric-grid four">
      <MetricCard label="计划状态" value={statusLabel(plan.status)} note={plan.planNumber} tone={plan.status === "PUBLISHED" ? "green" : "cyan"} icon="▦" />
      <MetricCard label="排产安排" value={plan.metrics.assignmentCount} note={`${plan.metrics.scheduledOrderCount} 个工单`} icon="▤" />
      <MetricCard label="能力缺口" value={plan.metrics.shortageCount} note={`${plan.metrics.lateOrderCount} 个延期`} tone={plan.metrics.shortageCount ? "amber" : "green"} icon="△" />
      <MetricCard label="利用率" value={`${plan.metrics.capacityUtilizationPercent}%`} note={`${Math.round(plan.metrics.plannedWorkMinutes / 60)} 计划小时`} icon="◒" />
    </div>
    <Panel title="人员与工作单元日计划" subtitle={`计划周期 ${formatDate(plan.horizonStart)} 起 · 至少显示六个工作日 · 底部横向滚动`} actions={<div className="segmented"><button className={view === "people" ? "active" : ""} onClick={() => setView("people")}>人员优先</button><button className={view === "resources" ? "active" : ""} onClick={() => setView("resources")}>全部资源</button></div>}>
      <div className="schedule-scroll"><div className="schedule-grid" style={{ "--day-count": dates.length }}>
        <div className="schedule-corner"><strong>执行资源</strong><span>每日任务与负荷</span></div>
        {dates.map((date) => <div className="day-head" key={date}><strong>{formatDate(date).split(" ")[0]}</strong><span>{formatDate(date).split(" ").slice(1).join(" ")}</span></div>)}
        {grouped.map((resource) => {
          const capacity = capacityById.get(resource.id);
          const total = resource.items.reduce((sum, item) => sum + item.plannedWorkMinutes, 0);
          return <div className="schedule-row" key={resource.id}>
            <div className="resource-head"><span className={`resource-avatar ${resource.type.toLowerCase()}`}>{resource.type === "PERSON" ? "人" : "机"}</span><div><strong>{resource.name}</strong><span>{resource.code}</span><small>周期负荷 {formatMinutes(total)}</small></div></div>
            {dates.map((date) => {
              const dayItems = resource.items.filter((item) => item.productionDate === date);
              const used = dayItems.reduce((sum, item) => sum + item.plannedWorkMinutes, 0);
              const denominator = capacity?.dailyCapacityMinutes || 480;
              return <div className="day-cell" key={date}>{dayItems.map((item) => <article className={`assignment-card ${item.deliveryStatus === "LATE" ? "late" : ""}`} key={item.assignmentId}><strong>{item.workOrderCode}</strong><span>{item.operationName}</span><small>{formatMinutes(item.plannedWorkMinutes)} · {item.plannedQuantity} 件</small><i title={`${Math.round(used / denominator * 100)}% 负荷`} style={{ width: `${Math.min(100, used / denominator * 100)}%` }} /></article>)}{!dayItems.length && <span className="day-empty">—</span>}</div>;
            })}
          </div>;
        })}
      </div></div>
    </Panel>
    <div className="results-split">
      <Panel title="排产明细与计算依据" subtitle="每项安排均保留资源、工时、交付与理由。" actions={<SearchBox value={query} onChange={setQuery} placeholder="搜索工单、人员或工序" />}>
        <div className="data-table-wrap"><table className="data-table"><thead><tr><th>执行者/资源</th><th>日期</th><th>工单工序</th><th>数量</th><th>工时</th><th>交付</th><th>计算依据</th></tr></thead><tbody>{assignments.map((item) => <tr key={item.assignmentId}><td><b>{item.resourceName}</b><small>{item.resourceType} · {item.resourceCode}</small></td><td>{formatDate(item.productionDate)}</td><td><b>{item.workOrderCode}</b><small>{item.operationSequence} · {item.operationName}</small></td><td>{item.plannedQuantity}</td><td>{formatMinutes(item.plannedWorkMinutes)}</td><td><StatusPill value={statusLabel(item.deliveryStatus)} tone={item.deliveryStatus === "LATE" ? "danger" : "published"} /></td><td className="rationale">{item.rationale}</td></tr>)}</tbody></table></div>
      </Panel>
      <Panel title="能力缺口" subtitle="未排入的工单不会被静默隐藏。">
        <div className="shortage-list">{plan.shortages.length ? plan.shortages.slice(0, 20).map((item, index) => <div key={`${item.workOrderId}-${index}`}><span>!</span><div><strong>{item.workOrderCode} · {item.operationName || "未配置工艺"}</strong><p>{item.reason}</p><small>{item.workCenterId || "无工作中心"} · 交期 {formatDate(item.dueAt, true)}</small></div></div>) : <EmptyState title="没有能力缺口" />}</div>
      </Panel>
    </div>
    <div className="evidence-footer"><div><span>生成主体</span><strong>{plan.generationParameters?.generatedBy || plan.createdBy}</strong></div><div><span>Agent 级别</span><strong>{plan.generationParameters?.agentLevel || "HUMAN"}</strong></div><div><span>批准人</span><strong>{plan.approvedBy || "尚未批准"}</strong></div><div><span>算法</span><strong>{plan.generationParameters?.algorithm || "FINITE_CAPACITY"}</strong></div><button className="button" onClick={() => onNavigate("workspace")}>向衡策询问本方案</button></div>
  </div>;
}
