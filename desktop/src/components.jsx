import { useEffect, useState } from "react";

export function PageHeader({ eyebrow, title, description, actions }) {
  return (
    <header className="page-header">
      <div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function MetricCard({ label, value, note, tone = "cyan", icon = "◆" }) {
  return (
    <article className={`metric-card ${tone}`}>
      <div className="metric-icon">{icon}</div>
      <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
    </article>
  );
}

export function StatusPill({ value, tone }) {
  const normalized = String(value || "UNKNOWN").toLowerCase();
  return <span className={`status-pill ${tone || normalized}`}>{value}</span>;
}

export function Panel({ title, subtitle, actions, children, className = "" }) {
  return (
    <section className={`native-panel ${className}`}>
      {(title || actions) && <div className="panel-head"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{actions && <div className="panel-actions">{actions}</div>}</div>}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function LoadingState({ text = "正在读取制造数据…" }) {
  return <div className="state-box"><span className="loader" /><strong>{text}</strong></div>;
}

export function EmptyState({ title = "暂无数据", description }) {
  return <div className="state-box empty-state"><span>◇</span><strong>{title}</strong>{description && <p>{description}</p>}</div>;
}

export function ErrorState({ error, onRetry }) {
  return <div className="state-box error-state"><span>!</span><strong>读取失败</strong><p>{error}</p>{onRetry && <button className="button" onClick={onRetry}>重新加载</button>}</div>;
}

export function SearchBox({ value, onChange, placeholder = "搜索…" }) {
  return <label className="search-box"><span>⌕</span><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>;
}

export function Toast({ message, tone = "success", onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(onClose, 4200);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return <div className={`toast ${tone}`}><span>{tone === "error" ? "!" : "✓"}</span>{message}<button onClick={onClose}>×</button></div>;
}

export function usePagination(items, pageSize = 20) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  useEffect(() => { if (page > pages) setPage(pages); }, [page, pages]);
  return {
    page,
    pages,
    setPage,
    rows: items.slice((page - 1) * pageSize, page * pageSize),
  };
}

export function Pager({ page, pages, onChange, total }) {
  return (
    <div className="pager"><span>共 {total} 条</span><button disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</button><b>{page} / {pages}</b><button disabled={page >= pages} onClick={() => onChange(page + 1)}>下一页</button></div>
  );
}
