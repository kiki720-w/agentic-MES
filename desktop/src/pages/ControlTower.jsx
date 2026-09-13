import { useCallback, useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState, MetricCard, PageHeader, Panel, StatusPill } from "../components";
import { api, formatDate, statusLabel } from "../platform";

export default function ControlTower({ actor, onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [orders, equipment, quality, plans, input, model] = await Promise.all([
        api("/api/v1/work-orders-summary", actor),
        api("/api/v1/equipment-summary", actor),
        api("/api/v1/quality/inspections-summary", actor),
        api("/api/v1/planning/plans?workshopId=WS-MACH-01&limit=10", actor),
        api("/api/v1/planning/input-summary?workshopId=WS-MACH-01&limit=8", actor),
        api("/api/v1/agent/model-status", actor),
      ]);
      setData({ orders, equipment, quality, plans, input, model });
    } catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }, [actor]);

  useEffect(() => { load(); }, [load]);
  const latest = data?.plans?.[0];
  const statusRows = useMemo(() => Object.entries(data?.orders?.statusCounts || {}), [data]);
  const maxStatus = Math.max(1, ...statusRows.map(([, value]) => value));

  if (loading && !data) return <LoadingState />;
  if (error && !data) return <ErrorState error={error} onRetry={load} />;
  return (
    <div className="native-page">
      <PageHeader eyebrow="REAL-TIME MANUFACTURING" title="制造智能控制塔" description="把订单、设备、质量、产能和排产收敛到一个可决策的实时视图。" actions={<><button className="button ghost" onClick={load}>↻ 刷新</button><button className="button primary" onClick={() => onNavigate("workspace")}>询问衡策</button></>} />
      <div className="metric-grid four">
        <MetricCard label="工单总量" value={data.orders.total} note={`${data.orders.workInProgress} 个正在生产`} icon="▤" />
        <MetricCard label="设备状态" value={`${data.equipment.healthy}/${data.equipment.total}`} note={`${data.equipment.attention} 台需要关注`} tone={data.equipment.attention ? "amber" : "green"} icon="◉" />
        <MetricCard label="质量待办" value={data.quality.open} note={`${data.quality.quarantined} 项隔离`} tone={data.quality.open ? "amber" : "green"} icon="◇" />
        <MetricCard label="当前计划" value={latest ? statusLabel(latest.status) : "未生成"} note={latest?.planNumber || "等待输入"} tone={latest?.status === "PUBLISHED" ? "green" : "cyan"} icon="▦" />
      </div>
      <div className="dashboard-grid">
        <Panel title="当前排产方案" subtitle="最新方案及确定性计算结果" actions={<button className="text-button" onClick={() => onNavigate("results")}>查看全量结果 →</button>}>
          {latest ? <div className="plan-overview">
            <div className="plan-title"><div><span>{latest.planNumber}</span><strong>{latest.metrics.assignmentCount} 项安排</strong></div><StatusPill value={statusLabel(latest.status)} tone={latest.status.toLowerCase()} /></div>
            <div className="plan-kpis">
              <div><span>已排工单</span><strong>{latest.metrics.scheduledOrderCount}</strong></div>
              <div><span>能力缺口</span><strong>{latest.metrics.shortageCount}</strong></div>
              <div><span>产能利用率</span><strong>{latest.metrics.capacityUtilizationPercent}%</strong></div>
              <div><span>计划工时</span><strong>{Math.round(latest.metrics.plannedWorkMinutes / 60)}h</strong></div>
            </div>
            <div className="progress-track"><span style={{ width: `${Math.min(100, latest.metrics.capacityUtilizationPercent)}%` }} /></div>
            <p className="source-note">生成者：{latest.generationParameters?.generatedBy || latest.createdBy} · {formatDate(latest.createdAt, true)} · 算法：有限产能最早完工</p>
          </div> : <div className="compact-empty">尚无排产方案。先导入输入或运行衡策分析。</div>}
        </Panel>
        <Panel title="制造风险" subtitle="需要人或 Agent 优先处理的约束">
          <div className="risk-list">
            <div className="risk-row danger"><span>01</span><div><strong>能力与工艺缺口</strong><p>{latest?.metrics?.shortageCount || 0} 项未能排入计划</p></div><b>{latest?.metrics?.shortageCount || 0}</b></div>
            <div className="risk-row amber"><span>02</span><div><strong>设备异常</strong><p>报警或未知状态设备</p></div><b>{data.equipment.attention}</b></div>
            <div className="risk-row cyan"><span>03</span><div><strong>标准工时完整度</strong><p>{data.input.fallbackOperationCount} 道工序仍使用回退工时</p></div><b>{data.input.processStandardCount}</b></div>
          </div>
        </Panel>
        <Panel title="工单状态结构" subtitle="当前制造数字镜像中的工单分布">
          <div className="bar-list">{statusRows.map(([status, value]) => <div className="bar-row" key={status}><span>{statusLabel(status)}</span><div><i style={{ width: `${value / maxStatus * 100}%` }} /></div><b>{value}</b></div>)}</div>
        </Panel>
        <Panel title="系统与模型" subtitle="本地控制层的运行边界">
          <div className="system-list">
            <div><span className="system-dot online" /><div><strong>CAPAXION Core</strong><small>本地制造数据与规则服务</small></div><b>已连接</b></div>
            <div><span className={`system-dot ${data.model.connectionStatus === "VERIFIED" ? "online" : "warning"}`} /><div><strong>{data.model.provider}</strong><small>{data.model.model}</small></div><b>{statusLabel(data.model.connectionStatus)}</b></div>
            <div><span className="system-dot warning" /><div><strong>企业系统连接器</strong><small>当前使用演示投影</small></div><b>待配置</b></div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
