import { useCallback, useEffect, useState } from "react";
import { ErrorState, LoadingState, PageHeader, Panel, StatusPill, Toast } from "../components";
import { api, formatDate, statusLabel } from "../platform";

const presets = {
  DEEPSEEK: { label: "DeepSeek V4 Vision", baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash-vision-exp", key: true, note: "云端文字、图片与长上下文" },
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
    <PageHeader eyebrow="MODEL CONTROL" title="模型与数据边界" description="选择本地模型、厂内 GPU 或云模型；模型生成候选动作，Core 校验、执行并核验。" actions={<button className="button" onClick={load}>↻ 刷新状态</button>} />
    <div className="model-status-banner"><div className="model-orbit">✦</div><div><span>当前推理模型</span><strong>{status.provider} · {status.model}</strong><p>{status.baseUrl}</p></div><StatusPill value={statusLabel(status.connectionStatus)} tone={status.connectionStatus === "VERIFIED" ? "published" : "warning"} /></div>
    {!canAdmin && <div className="permission-banner"><span>锁</span><div><strong>当前身份只有查看权限</strong><p>模型切换需要主管或主数据管理员。开发模式可从左下角切换到 Demo Supervisor；生产环境由企业 OIDC 决定身份，前端不可自行提权。</p></div></div>}
    <div className="models-layout">
      <Panel title="选择模型服务" subtitle="选择 DeepSeek 时，相关文档片段和图片会发送给 DeepSeek API。">
        <div className="provider-grid">{Object.entries(presets).map(([key, preset]) => <button key={key} className={form.provider === key ? "active" : ""} onClick={() => selectProvider(key)}><span>{key === "OLLAMA" || key === "LM_STUDIO" || key === "VLLM" ? "本地" : "API"}</span><strong>{preset.label}</strong><small>{preset.note}</small><i>{form.provider === key ? "✓" : ""}</i></button>)}</div>
      </Panel>
      <Panel title="连接配置" subtitle="密钥只发送到本机 Core，不写浏览器存储，也不会通过状态接口回显。">
        <div className="model-form">
          <label><span>服务商标识</span><input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value.toUpperCase() })} /></label>
          <label><span>API Base URL</span><input value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} /></label>
          <label><span>模型 ID</span><input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></label>
          <div className="form-two"><label><span>API Key {currentPreset.key ? "" : "（厂内服务可不填）"}</span><input type="password" value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} placeholder={status.apiKeyConfigured ? "已配置；留空则保持现有密钥" : "输入后不会回显"} /></label><label><span>超时（秒）</span><input type="number" min="1" max="120" value={form.timeoutSeconds} onChange={(e) => setForm({ ...form, timeoutSeconds: e.target.value })} /></label></div>
          <label className="check-row"><input type="checkbox" checked={form.verifyConnection} onChange={(e) => setForm({ ...form, verifyConnection: e.target.checked })} /><span>保存前验证连接；失败时不覆盖当前可用配置</span></label>
          <div className="model-actions"><button className="button" disabled={!canAdmin || busy} onClick={() => save(false)}>仅保存</button><button className="button primary" disabled={!canAdmin || busy || !form.baseUrl || !form.model} onClick={() => save(form.verifyConnection)}>{busy ? "验证中…" : "验证并启用"}</button></div>
        </div>
      </Panel>
    </div>
    <Panel title="模型与业务系统的独立网络边界" subtitle="白名单由 Core 服务端部署配置控制，选择服务商不会自动授权联网。">
      <div className="policy-cards">
        <article className="active"><span>模型通道</span><strong>{status.networkPolicy?.modelMode || "未知"}</strong><p>厂内模型：{status.networkPolicy?.modelLocalEndpoints?.join("、") || "未授权"}</p><p>云模型：{status.networkPolicy?.modelCloudEndpoints?.join("、") || "未授权"}。不会自动回退到云模型。</p></article>
        <article><span>业务通道</span><strong>MES / ERP</strong><p>授权地址：{status.networkPolicy?.businessEndpoints?.join("、") || "未配置"}</p><p>已提供受控读取组件，实际云 MES 连接器尚未接入；此通道不授权云模型。</p></article>
        <article><span>执行范围</span><strong>应用层限制</strong><p>模型请求与业务读取组件禁用环境代理和自动重定向。主机防火墙、模型进程自身联网及企业身份服务需要单独部署管理。</p></article>
      </div>
      <p className="status-footnote">配置来源：{status.configurationSource || "—"} · 最近检查：{formatDate(status.lastCheckedAt, true)} · 最近成功：{formatDate(status.lastSuccessAt, true)} · 失败次数：{status.failureCount || 0} · {status.lastError || "无已知连接错误"}</p>
    </Panel>
  </div>;
}
