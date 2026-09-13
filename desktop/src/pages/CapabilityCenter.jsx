import { useEffect, useState } from "react";
import { PageHeader, Panel } from "../components";
import { api, formatDate, statusLabel } from "../platform";

const labels = { READY_TO_TEST: "可测评", IMPLEMENTED_NOT_BENCHMARKED: "已接入 · 本中心未测", NOT_INTEGRATED: "未接入", NOT_BENCHMARKED: "本中心未测", RUNNING: "测评中", COMPLETED: "已完成", FAILED: "运行失败", INTERRUPTED: "已中断" };
const runLabel = (run) => `${run.target === "LOCAL" ? "本地" : run.provider} · ${run.model} · ${formatDate(run.createdAt, true)} · ${run.runId.slice(0, 8)}`;

export default function CapabilityCenter({ actor }) {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState("");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [local, setLocal] = useState({ baseUrl: "http://127.0.0.1:11434/v1", model: "" });
  const [pair, setPair] = useState({ left: "", right: "" });
  const [comparison, setComparison] = useState(null);
  useEffect(() => {
    let stopped = false;
    async function refresh() {
      try {
        const next = await api("/api/v1/agent/capabilities", actor);
        if (!stopped) { setData(next); setError(""); }
        if (selected) {
          const detail = await api(`/api/v1/agent/capability-runs/${selected}`, actor);
          if (!stopped) setReport(detail);
        }
      } catch (e) { if (!stopped) setError(e.message); }
    }
    refresh(); const timer = setInterval(refresh, 2500);
    return () => { stopped = true; clearInterval(timer); };
  }, [actor, selected]);
  const running = data?.runs?.some((run) => run.status === "RUNNING");
  async function start(target) {
    setBusy(true); setError("");
    try {
      const next = await api("/api/v1/agent/capability-runs", actor, { method: "POST", body: { target, ...(target === "LOCAL" ? local : {}) } });
      setSelected(next.runId); setReport(next);
      setData((current) => ({ ...current, runs: [next, ...(current?.runs || [])] }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function compare() {
    setBusy(true); setComparison(null);
    try { setComparison(await api(`/api/v1/agent/capability-runs/compare?left=${pair.left}&right=${pair.right}`, actor)); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = `capaxion-benchmark-${report.runId}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div className="native-page capability-page">
    <PageHeader eyebrow="CAPABILITY EVIDENCE" title="AI 能力验收中心" description="用同一套制造业务试题看实际表现。所有样本均为合成数据，不读取订单、图纸或生产库。" />
    {error && <div role="alert" className="permission-banner">{error}</div>}
    <div className="capability-catalog">{data?.capabilities?.map((item) => <article key={item.id}><strong>{item.name}</strong><span>{labels[item.status]}</span><p>{item.detail}</p></article>)}</div>
    <div className="models-layout">
      <Panel title="当前模型 · 制造文字基准" subtitle="9 题：事实引用、缺失与冲突、工时和单位计算、边界理解。">
        <p>{data?.modelStatus?.provider || "—"} · {data?.modelStatus?.model || "未配置"} · {statusLabel(data?.modelStatus?.connectionStatus)}</p>
        <p className="status-footnote">采用严格答案匹配。每次发出 9 次模型请求；若当前配置为云端，将发送这些合成样本。运行期间固定本次模型，不受随后切换配置影响。</p>
        <button className="button primary" disabled={busy || running || !data?.modelStatus?.model} onClick={() => start("ACTIVE")}>开始当前模型测评</button>
      </Panel>
      <Panel title="本地候选 · 独立测评" subtitle="使用相同题目，不替换工作台正在使用的模型。">
        <div className="model-form"><label><span>本机 OpenAI 兼容服务地址</span><input value={local.baseUrl} onChange={(e) => setLocal({ ...local, baseUrl: e.target.value })} /></label>
          <label><span>已安装模型的 ID</span><input value={local.model} placeholder="填写本地服务中的实际模型 ID" onChange={(e) => setLocal({ ...local, model: e.target.value })} /></label>
          <button className="button" disabled={busy || running || !local.model.trim()} onClick={() => start("LOCAL")}>开始本地模型测评</button></div>
        <p className="status-footnote">仅支持 127.0.0.1 的无密钥服务。不会下载模型，不会向本地服务发送云端密钥，也不回退云端。本地服务自身是否转发数据仍需另行审计。</p>
      </Panel>
    </div>
    <Panel title="历史结果与同题对比" subtitle="未运行的模型没有分数。不同版本、样本或评分规则的报告不能直接比较。">
      {!data?.runs?.length && <p>尚无制造基准报告；之前三题自检与本基准分开保存。</p>}
      <div className="benchmark-table"><table><thead><tr><th>模型 / 时间 / 运行编号</th><th>状态</th><th>进度</th><th>同题通过率</th><th>请求失败</th><th>中位耗时</th></tr></thead><tbody>{data?.runs?.map((run) => <tr key={run.runId}><td><button className="button" onClick={() => { setReport(null); setSelected(run.runId); }}>{runLabel(run)}</button></td><td>{labels[run.status] || run.status}</td><td>{run.completed}/{run.total}</td><td>{run.status === "COMPLETED" ? `${run.scorePercent}%` : "—"}</td><td>{run.requestFailures ?? "—"}</td><td>{run.medianLatencyMs == null ? "—" : `${run.medianLatencyMs} ms`}</td></tr>)}</tbody></table></div>
      <div className="benchmark-compare">{["left", "right"].map((side) => <select aria-label={side === "left" ? "基准报告" : "候选报告"} key={side} value={pair[side]} onChange={(e) => { setPair({ ...pair, [side]: e.target.value }); setComparison(null); }}><option value="">{side === "left" ? "选择基准报告" : "选择候选报告"}</option>{data?.runs?.map((run) => <option key={run.runId} value={run.runId}>{runLabel(run)}</option>)}</select>)}<button className="button" disabled={busy || !pair.left || !pair.right || pair.left === pair.right} onClick={compare}>对比</button></div>
      {comparison && <div className="benchmark-comparison"><p>{comparison.note}</p>{comparison.comparable ? <><p>基准 {comparison.left.scorePercent}% · 候选 {comparison.right.scorePercent}% · 中位耗时 {comparison.left.medianLatencyMs} / {comparison.right.medianLatencyMs} ms</p><div className="benchmark-table"><table><thead><tr><th>题目</th><th>基准结果</th><th>候选结果</th></tr></thead><tbody>{comparison.left.checks.map((check, index) => <tr key={check.id}><td>{check.id}</td><td>{check.passed ? "通过" : "未通过"} · {check.actual ?? check.error}</td><td>{comparison.right.checks[index].passed ? "通过" : "未通过"} · {comparison.right.checks[index].actual ?? comparison.right.checks[index].error}</td></tr>)}</tbody></table></div></> : <strong>不可比较：有报告未完成，或试卷/评分版本不同。</strong>}</div>}
      <p className="status-footnote">暂不估算成本、显存占用或综合“幻觉率”。本页通过率仅针对这 9 道固定合成题，不能推广为真实生产准确率。</p>
    </Panel>
    {report && <Panel title={`逐题证据 · ${report.runId}`} subtitle={`${report.suiteVersion} · ${report.fixtureSha256}`} actions={<button className="button" disabled={report.status === "RUNNING"} onClick={download}>导出 JSON 证据</button>}>
      <p>{labels[report.status]} · 操作者 {report.actor} · {report.completed}/{report.total} 题</p>
      {report.checks.map((check) => <details className="benchmark-case" key={check.id} open={!check.passed}><summary>{check.passed ? "✓ 通过" : "× 未通过"} · {check.category} · {check.id} · {check.latencyMs} ms</summary><p>{check.question}</p><div className="benchmark-answers"><div><strong>标准答案</strong><pre>{check.expected}</pre></div><div><strong>实际结果</strong><pre>{check.actual ?? check.error}</pre></div></div><p>核对依据：{check.evidence}</p><pre>{JSON.stringify(check.facts, null, 2)}</pre></details>)}
      <p className="status-footnote">本地保存合成题与脱敏后的回答，便于逐题核对；报告不是防篡改审计。这里的边界理解题不能代替服务端权限与审批测试。</p>
    </Panel>}
  </div>;
}
