import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState, MetricCard, PageHeader, Panel, SearchBox, StatusPill, Toast, usePagination, Pager } from "../components";
import { api, desktop, localDateISO, statusLabel } from "../platform";

export default function PlanningCenter({ actor, onNavigate }) {
  const [summary, setSummary] = useState(null);
  const [plans, setPlans] = useState([]);
  const [autonomy, setAutonomy] = useState(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState(null);
  const [preview, setPreview] = useState(null);
  const [params, setParams] = useState({ horizonStart: localDateISO(), horizonDays: 6, useOvertime: false, defaultMinutesPerUnit: 30 });

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextSummary, nextPlans, nextAutonomy] = await Promise.all([
        api("/api/v1/planning/input-summary?workshopId=WS-MACH-01&limit=100", actor),
        api("/api/v1/planning/plans?workshopId=WS-MACH-01&limit=30", actor),
        api("/api/v1/planning/autonomy", actor),
      ]);
      setSummary(nextSummary); setPlans(nextPlans); setAutonomy(nextAutonomy);
    } catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }, [actor]);
  useEffect(() => { load(); }, [load]);

  async function runAgent() {
    setBusy(true);
    try {
      const result = await api("/api/v1/planning/agent/analyze", actor, { method: "POST", body: { workshopId: "WS-MACH-01", ...params, horizonDays: Number(params.horizonDays), defaultMinutesPerUnit: Number(params.defaultMinutesPerUnit), operationRates: {} } });
      setToast({ message: `${result.plan.planNumber} 已由衡策生成，共 ${result.plan.assignments.length} 项安排。`, tone: "success" });
      await load();
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  async function pickSpreadsheet() {
    const files = await desktop.files.pick();
    const file = files.find((item) => /\.(xlsx|csv)$/i.test(item.name));
    if (!file) return;
    setBusy(true); setPreview(null);
    try {
      const result = await desktop.core.upload({ path: "/api/v1/planning/imports/spreadsheet/preview?workshopId=WS-MACH-01", filePath: file.path, actor });
      setPreview({ ...result, fileName: file.name });
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  async function runAutonomy() {
    setBusy(true);
    try {
      const result = await api("/api/v1/planning/autonomy/run", actor, { method: "POST", body: { workshopId: "WS-MACH-01", ...params, horizonDays: Number(params.horizonDays), defaultMinutesPerUnit: Number(params.defaultMinutesPerUnit), operationRates: {} } });
      setToast({ message: `L4 评估：${result.decision} · ${result.state}`, tone: result.state === "VERIFIED" || result.state === "SHADOW_COMPLETE" ? "success" : "error" });
      await load();
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  async function toggleKillSwitch() {
    setBusy(true);
    try {
      const path = autonomy?.killSwitch ? "/api/v1/planning/autonomy/resume" : "/api/v1/planning/autonomy/stop";
      const next = await api(path, actor, { method: "POST" });
      setAutonomy(next); setToast({ message: next.killSwitch ? "L4 自治停止开关已启用。" : "L4 自治已恢复。", tone: "success" });
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  async function confirmImport() {
    if (!preview?.valid) return;
    setBusy(true);
    try {
      const result = await api("/api/v1/planning/imports/spreadsheet/confirm", actor, { method: "POST", body: { previewFingerprint: preview.previewFingerprint, snapshot: preview.snapshot, runAgent: true, ...params, horizonDays: Number(params.horizonDays), defaultMinutesPerUnit: Number(params.defaultMinutesPerUnit) } });
      setPreview(null);
      setToast({ message: `输入已形成可追溯快照${result.agent?.plan ? `，并生成 ${result.agent.plan.planNumber}` : ""}。`, tone: "success" });
      await load();
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }

  const demands = useMemo(() => (summary?.demandRows || []).filter((row) => Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(query.toLowerCase()))), [summary, query]);
  const pagination = usePagination(demands, 12);
  if (loading && !summary) return <LoadingState />;
  if (error && !summary) return <ErrorState error={error} onRetry={load} />;
  return <div className="native-page">
    <Toast message={toast?.message} tone={toast?.tone} onClose={() => setToast(null)} />
    <PageHeader eyebrow="FINITE CAPACITY APS" title="有限产能排产中心" description="先核对输入与工艺，再由确定性 APS 和衡策生成可解释方案。" actions={<><button className="button" onClick={pickSpreadsheet} disabled={busy}>导入 Excel / CSV</button><button className="button primary" onClick={runAgent} disabled={busy}>{busy ? "处理中…" : "衡策生成方案"}</button></>} />
    <div className="metric-grid four">
      <MetricCard label="工单输入" value={summary.workOrderCount} note={summary.source.sourceSystem} icon="▤" />
      <MetricCard label="工序需求" value={summary.operationCount} note={`${summary.fallbackOperationCount} 项使用回退工时`} tone={summary.fallbackOperationCount ? "amber" : "green"} icon="⌁" />
      <MetricCard label="产能资源" value={summary.resourceCount} note={`${summary.personResourceCount} 人 · ${summary.cellResourceCount} 工作单元`} icon="◉" />
      <MetricCard label="历史方案" value={plans.length} note={plans[0]?.planNumber || "尚未生成"} icon="▦" />
    </div>
    <Panel title="L4 排产自治" subtitle="自主观察、规划、策略授权、执行、核验和异常升级；当前状态来自 Core。" actions={<><button className="button" disabled={busy} onClick={runAutonomy}>运行一次自治评估</button><button className="button" disabled={busy || actor !== "demo-supervisor"} onClick={toggleKillSwitch}>{autonomy?.killSwitch ? "恢复自治" : "紧急停止"}</button></>}>
      <div className="preview-stats"><div><span>目标级别</span><b>{autonomy?.level || "—"}</b></div><div><span>当前模式</span><b>{autonomy?.mode || "—"}</b></div><div><span>执行目标</span><b>{autonomy?.executionTarget || "—"}</b></div><div><span>循环</span><b>{autonomy?.loopRunning ? "运行中" : "未启动"}</b></div></div>
      <div className="source-card"><StatusPill value={autonomy?.killSwitch ? "已紧急停止" : autonomy?.mode === "AUTONOMOUS" ? "预授权自动" : autonomy?.mode === "SHADOW" ? "影子运行" : "人工监督"} tone={autonomy?.killSwitch ? "danger" : autonomy?.mode === "AUTONOMOUS" ? "published" : "warning"} /><strong>{autonomy?.claim || "正在读取自治状态"}</strong><p>{autonomy?.lastRun ? `最近决策 ${autonomy.lastRun.decision} · 状态 ${autonomy.lastRun.state}` : "尚无自治运行记录。影子模式只给出是否会执行，不会发布或回写。"}</p></div>
    </Panel>
    <div className="planning-layout">
      <Panel title="排产参数" subtitle="规则引擎的确定性输入，不由大模型私自修改。">
        <div className="form-grid">
          <label><span>计划开始日</span><input type="date" value={params.horizonStart} onChange={(e) => setParams({ ...params, horizonStart: e.target.value })} /></label>
          <label><span>工作日数量</span><input type="number" min="1" max="31" value={params.horizonDays} onChange={(e) => setParams({ ...params, horizonDays: e.target.value })} /></label>
          <label><span>缺省单件工时（分钟）</span><input type="number" min="1" value={params.defaultMinutesPerUnit} onChange={(e) => setParams({ ...params, defaultMinutesPerUnit: e.target.value })} /></label>
          <label className="toggle-label"><span>允许使用加班产能</span><button className={`toggle ${params.useOvertime ? "on" : ""}`} onClick={() => setParams({ ...params, useOvertime: !params.useOvertime })}><i /></button></label>
        </div>
        <div className="formula-card"><span>计算方式</span><strong>准备工时 + 剩余数量 × 单件标准工时</strong><p>缺少标准工时时才使用上方回退值，并在结果证据中明确标注。</p></div>
      </Panel>
      <Panel title="输入来源" subtitle="权威来源、版本和质量状态">
        <div className="source-card"><StatusPill value={summary.source.type === "DEMO_PROJECTION" ? "演示投影" : "真实快照"} tone={summary.source.type === "DEMO_PROJECTION" ? "warning" : "published"} /><strong>{summary.source.sourceSystem}</strong><p>{summary.source.warning || "来源与版本已记录，可用于可追溯排产。"}</p></div>
        <button className="button wide" onClick={() => onNavigate("capacity")}>维护人员与工作单元产能</button>
      </Panel>
    </div>
    {preview && <Panel title={`文件预检 · ${preview.fileName}`} subtitle="尚未写入生产快照，必须确认后才会导入。" className="preview-panel" actions={<button className="button primary" disabled={!preview.valid || busy} onClick={confirmImport}>确认导入并生成方案</button>}>
      <div className="preview-stats"><div><span>工单</span><b>{preview.stats.workOrderCount}</b></div><div><span>工序</span><b>{preview.stats.operationCount}</b></div><div><span>资源</span><b>{preview.stats.resourceCount}</b></div><div><span>错误</span><b>{preview.stats.errorCount}</b></div></div>
      {preview.issues?.length > 0 && <div className="issue-list">{preview.issues.slice(0, 8).map((issue, index) => <div key={index}><StatusPill value={issue.severity} tone={issue.severity === "ERROR" ? "danger" : "warning"} /><span>{issue.sheet}{issue.row ? ` · 第 ${issue.row} 行` : ""} · {issue.message}</span></div>)}</div>}
    </Panel>}
    <Panel title="工单、工序与标准工时" subtitle="大样本使用搜索和分页，避免页面无限拉长。" actions={<SearchBox value={query} onChange={(value) => { setQuery(value); pagination.setPage(1); }} placeholder="搜索工单、工序、工作中心" />}>
      <div className="data-table-wrap"><table className="data-table"><thead><tr><th>工单</th><th>工序</th><th>工作中心</th><th>剩余数量</th><th>单件工时</th><th>准备工时</th><th>需求工时</th><th>依据</th></tr></thead><tbody>{pagination.rows.map((row) => <tr key={`${row.workOrderCode}-${row.operationSequence}`}><td><b>{row.workOrderCode}</b></td><td>{row.operationSequence} · {row.operationName}</td><td>{row.workCenterId}</td><td>{row.remainingQuantity}</td><td>{row.minutesPerUnit ?? "—"}</td><td>{row.setupMinutes}</td><td>{row.requiredMinutes ?? "待计算"}</td><td><StatusPill value={row.demandSource === "FALLBACK_REQUIRED" ? "缺少标准" : "已定义"} tone={row.demandSource === "FALLBACK_REQUIRED" ? "warning" : "published"} /></td></tr>)}</tbody></table></div>
      <Pager page={pagination.page} pages={pagination.pages} total={demands.length} onChange={pagination.setPage} />
    </Panel>
  </div>;
}
