export const CORE_URL = "http://127.0.0.1:8000";

const browserFallback = {
  coreUrl: CORE_URL,
  platform: "browser",
  window: { minimize() {}, maximize() {}, close() {}, async zoom() { return 110; } },
  core: {
    async status() {
      try {
        const response = await fetch(`${CORE_URL}/health/ready`);
        return { online: response.ok, detail: response.ok ? await response.json() : null };
      } catch (error) {
        return { online: false, detail: error.message };
      }
    },
    async request(request) {
      const response = await fetch(`${CORE_URL}${request.path}`, {
        method: request.method || "GET",
        headers: {
          "Content-Type": "application/json",
          "X-Dev-Actor": request.actor || "demo-planner",
        },
        body: request.body === undefined ? undefined : JSON.stringify(request.body),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(payload?.detail || response.statusText);
      return payload;
    },
    async upload() {
      throw new Error("文件预检仅在 CAPAXION 桌面客户端中可用");
    },
  },
  files: { async pick() { return []; }, onDrop() {}, offDrop() {} },
};

export const desktop = window.capaxion || browserFallback;

export function api(path, actor, options = {}) {
  return desktop.core.request({ path, actor, ...options });
}

export function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value) || 0));
}

export function formatMinutes(value) {
  const minutes = Math.round(Number(value) || 0);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", withTime
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { month: "2-digit", day: "2-digit", weekday: "short" }).format(date);
}

export function statusLabel(value) {
  return ({
    BLOCKED: "网络策略已阻止", CONFIGURED: "已配置 · 未验证", VERIFIED: "请求已验证", DEGRADED: "模型降级 · 规则可用", DISABLED: "已停用",
    DRAFT: "草稿", SUBMITTED: "待审批", PENDING_APPROVAL: "待审批", APPROVED: "已批准", PUBLISHED: "已发布",
    WITHDRAWN: "已撤回", RUNNING: "运行", IDLE: "空闲", ALARM: "报警",
    UNKNOWN: "未知", SUSPENDED: "暂停", IN_PROGRESS: "进行中", RELEASED: "已下达",
    COMPLETED: "已完成", OPEN: "待处理", ON_TIME: "准时", LATE: "延期",
  })[value] || value || "—";
}

export function localDateISO(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function workingDates(start, count) {
  const dates = [];
  const cursor = new Date(`${start}T00:00:00`);
  while (dates.length < Math.max(6, count || 6)) {
    if (cursor.getDay() !== 0) dates.push(localDateISO(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}
