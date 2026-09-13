import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState, PageHeader, Panel, StatusPill, Toast } from "../components";
import { api, formatDate, statusLabel } from "../platform";

const presets = {
  DEEPSEEK: { label: "DeepSeek", baseUrl: "https://api.deepseek.com", model: "deepseek-chat", key: true, note: "云端通用推理" },
  KIMI: { label: "Kimi / Moonshot", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-auto", key: true, note: "云端长上下文" },
  OPENAI: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-5", key: true, note: "云端多能力模型" },
  OLLAMA: { label: "Ollama 本地", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen3:8b", key: false, note: "待本地服务与出站策略验收" },
  LM_STUDIO: { label: "LM Studio 本地", baseUrl: "http://127.0.0.1:1234/v1", model: "local-model", key: false, note: "桌面本地推理" },
  VLLM: { label: "vLLM 厂内服务", baseUrl: "http://127.0.0.1:8001/v1", model: "Qwen/Qwen3-14B", key: false, note: "厂内共享 GPU" },
  CUSTOM: { label: "自定义兼容接口", baseUrl: "", model: "", key: true, note: "OpenAI Chat Completions 兼容" },
};

export default function ModelSettings({ actor }) {
  const [status, setStatus] = useState(null);
  const [identity, setIdentity] = useState(null);
  const [form, setForm] = useState({ provider: "DEEPSEEK", baseUrl: presets.DEEPSEEK.baseUrl, model: presets.DEEPSEEK.model, apiKey: "", timeoutSeconds: 20, verifyConnection: true });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState(null);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextStatus, nextIdentity] = await Promise.all([api("/api/v1/agent/model-status", actor), api("/api/v1/identity/me", actor)]);
      setStatus(nextStatus); setIdentity(nextIdentity);
      setForm((current) => ({ ...current, provider: nextStatus.provider || current.provider, baseUrl: nextStatus.baseUrl || current.baseUrl, model: nextStatus.model || current.model }));
    } catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }, [actor]);
  useEffect(() => { load(); }, [load]);
  const canAdmin = identity?.roles?.some((role) => ["SUPERVISOR", "MASTER_DATA_ADMIN"].includes(role));
  function selectProvider(provider) {
    const preset = presets[provider];
    setForm((current) => ({ ...current, provider, baseUrl: preset.baseUrl, model: preset.model, apiKey: "" }));
  }
  async function save(verifyConnection) {
    setBusy(true);
    try {
      const payload = { ...form, timeoutSeconds: Number(form.timeoutSeconds), verifyConnection };
      if (!payload.apiKey) delete payload.apiKey;
      const next = await api("/api/v1/system/model-gateway/configuration", actor, { method: "PUT", body: payload });
      setStatus(next); setForm((current) => ({ ...current, apiKey: "" }));
      setToast({ message: verifyConnection ? "连接验证通过，模型配置已切换。" : "模型配置已保存。", tone: "success" });
    } catch (requestError) { setToast({ message: requestError.message, tone: "error" }); }
    finally { setBusy(false); }
  }
  if (loading && !status) return <LoadingState />;
  if (error && !status) return <ErrorState error={error} onRetry={load} />;
  const currentPreset = presets[form.provider] || presets.CUSTOM;
  return <div className="native-page models-page">
    <Toast message={toast?.message} tone={toast?.tone} onClose={() => setToast(null)} />
    <PageHeader eyebrow="MODEL CONTROL" title="模型与数据边界" description="选择云模型、本地模型或厂内 GPU 服务；模型只参与理解与解释，不直接控制生产。" actions={<button className="button" onClick={load}>↻ 刷新状态</button>} />
    <div className="model-status-banner"><div className="model-orbit">✦</div><div><span>当前推理模型</span><strong>{status.provider} · {status.model}</strong><p>{status.baseUrl}</p></div><StatusPill value={statusLabel(status.connectionStatus)} tone={status.connectionStatus === "VERIFIED" ? "published" : "warning"} /></div>
    {!canAdmin && <div className="permission-banner"><span>锁</span><div><strong>当前身份只有查看权限</strong><p>模型切换需要主管或主数据管理员。开发模式可从左下角切换到 Demo Supervisor；生产环境由企业 OIDC 决定身份，前端不可自行提权。</p></div></div>}
    <div className="models-layout">
      <Panel title="选择模型服务" subtitle="本地优先，云端必须由客户主动选择。">
        <div className="provider-grid">{Object.entries(presets).map(([key, preset]) => <button key={key} className={form.provider === key ? "active" : ""} onClick={() => selectProvider(key)}><span>{key === "OLLAMA" || key === "LM_STUDIO" || key === "VLLM" ? "本地" : "API"}</span><strong>{preset.label}</strong><small>{preset.note}</small><i>{form.provider === key ? "✓" : ""}</i></button>)}</div>
      </Panel>
      <Panel title="连接配置" subtitle="密钥只发送到本机 Core，不写浏览器存储，也不会通过状态接口回显。">
        <div className="model-form">
          <label><span>服务商标识</span><input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value.toUpperCase() })} /></label>
          <label><span>API Base URL</span><input value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} /></label>
          <label><span>模型 ID</span><input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
          <div className="form-two"><label><span>API Key {currentPreset.key ? "" : "（当前仍需密钥）"}</span><input type="password" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} placeholder={status.apiKeyConfigured ? "已配置；留空则保持现有密钥" : "输入后不会回显"} /></label><label><span>超时（秒）</span><input type="number" min="1" max="120" value={form.timeoutSeconds} onChange={(e) => setForm({ ...form, timeoutSeconds: e.target.value })} /></label></div>
          <label className="check-row"><input type="checkbox" checked={form.verifyConnection} onChange={(e) => setForm({ ...form, verifyConnection: e.target.checked })} /><span>保存前验证连接；失败时不覆盖当前可用配置</span></label>
          <div className="model-actions"><button className="button" disabled={!canAdmin || busy} onClick={() => save(false)}>仅保存</button><button className="button primary" disabled={!canAdmin || busy || !form.baseUrl || !form.model} onClick={() => save(form.verifyConnection)}>{busy ? "验证中…" : "验证并启用"}</button></div>
        </div>
      </Panel>
    </div>
    <Panel title="数据出厂策略" subtitle="正式版将由服务端网络策略强制执行，而不是依赖提示词。">
      <div className="policy-cards"><article className={form.provider === "OLLAMA" || form.provider === "LM_STUDIO" ? "active" : ""}><span>推荐</span><strong>LOCAL ONLY</strong><p>模型、知识库、日志与文件解析全部位于本机或工厂网络，默认拒绝公网出口。</p></article><article className={form.provider === "VLLM" ? "active" : ""}><span>厂内部署</span><strong>PRIVATE ENHANCED</strong><p>多台客户端共享厂内 GPU 推理服务器，订单和图纸不离开工厂。</p></article><article className={["DEEPSEEK", "KIMI", "OPENAI", "CUSTOM"].includes(form.provider) ? "active" : ""}><span>主动授权</span><strong>CLOUD OPT-IN</strong><p>只发送最小化、经授权的结构化上下文，不允许隐藏的云端回退。</p></article></div>
      <p className="status-footnote">配置来源：{status.configurationSource || "—"} · 最近检查：{formatDate(status.lastCheckedAt, true)} · 最近成功：{formatDate(status.lastSuccessAt, true)} · 失败次数：{status.failureCount || 0} · {status.lastError || "无已知连接错误"}</p>
    </Panel>
  </div>;
}
