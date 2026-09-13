const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const { randomUUID } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const CORE_URL = process.env.CAPAXION_CORE_URL || "http://127.0.0.1:8000";
const CORE_ENDPOINT = new URL(CORE_URL);
let mainWindow;
let coreProcess;
let coreStartedByDesktop = false;
const selectedFiles = new Map();
let desktopZoomFactor = 1.1;

function rememberSelectedFile(record) {
  const id = randomUUID();
  selectedFiles.set(id, record);
  while (selectedFiles.size > 100) selectedFiles.delete(selectedFiles.keys().next().value);
  return { id, path: record.path || null, name: record.name, size: record.size };
}

function registerSelectedFiles(filePaths) {
  const files = [];
  for (const filePath of [...new Set(filePaths)].slice(0, 8)) {
    try {
      const resolved = path.resolve(String(filePath || ""));
      const stat = fs.statSync(resolved);
      if (!stat.isFile()) continue;
      files.push(rememberSelectedFile({
        path: resolved,
        name: path.basename(resolved),
        size: stat.size,
      }));
    } catch {
      // Ignore missing files and folders. The renderer reports an empty drop when none remain.
    }
  }
  return files;
}

function registerDroppedFiles(entries) {
  const files = [];
  const paths = entries.filter((entry) => entry?.path).map((entry) => entry.path);
  files.push(...registerSelectedFiles(paths));
  for (const entry of entries.filter((item) => !item?.path).slice(0, 8 - files.length)) {
    const name = path.basename(String(entry?.name || ""));
    const content = Buffer.from(entry?.bytes || []);
    if (!name || !content.length || content.length > 15 * 1024 * 1024) continue;
    files.push(rememberSelectedFile({ name, size: content.length, content }));
  }
  return files;
}

async function coreHealth() {
  try {
    const response = await fetch(`${CORE_URL}/health/ready`, {
      signal: AbortSignal.timeout(1500),
    });
    if (!response.ok) return { online: false, detail: `HTTP ${response.status}` };
    return { online: true, detail: await response.json() };
  } catch (error) {
    return { online: false, detail: error.message };
  }
}

function resolveDevelopmentCore() {
  const roots = [
    process.env.CAPAXION_REPOSITORY_ROOT,
    process.env.PORTABLE_EXECUTABLE_DIR
      ? path.resolve(process.env.PORTABLE_EXECUTABLE_DIR, "..", "..")
      : null,
    path.resolve(__dirname, "..", ".."),
  ].filter(Boolean);

  for (const repositoryRoot of [...new Set(roots)]) {
    const coreRoot = path.join(repositoryRoot, "core");
    const pythonCandidates = [
      process.env.CAPAXION_PYTHON,
      path.join(coreRoot, ".venv", "Scripts", "python.exe"),
    ].filter(Boolean);
    const python = pythonCandidates.find((candidate) => fs.existsSync(candidate));
    if (python && fs.existsSync(path.join(coreRoot, "src", "autonomous_mes"))) {
      return { python, coreRoot };
    }
  }
  return null;
}

async function ensureCore() {
  const current = await coreHealth();
  if (current.online) return current;

  if (!["127.0.0.1", "localhost", "::1"].includes(CORE_ENDPOINT.hostname)) {
    return current;
  }

  const developmentCore = resolveDevelopmentCore();
  if (!developmentCore) return current;

  const coreHost = CORE_ENDPOINT.hostname === "localhost" ? "127.0.0.1" : CORE_ENDPOINT.hostname;
  const corePort = CORE_ENDPOINT.port || (CORE_ENDPOINT.protocol === "https:" ? "443" : "80");

  coreProcess = spawn(
    developmentCore.python,
    ["-m", "uvicorn", "autonomous_mes.api:app", "--host", coreHost, "--port", corePort],
    {
      cwd: developmentCore.coreRoot,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONPATH: path.join(developmentCore.coreRoot, "src"),
        PYTHONUTF8: "1",
      },
      stdio: "ignore",
    },
  );
  coreStartedByDesktop = true;

  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const status = await coreHealth();
    if (status.online) return status;
  }
  return { online: false, detail: "CAPAXION Core 启动超时" };
}

function validateCorePath(value) {
  if (typeof value !== "string" || !value.startsWith("/")) {
    throw new Error("Invalid CAPAXION Core path");
  }
  if (!value.startsWith("/api/v1/") && !value.startsWith("/health/")) {
    throw new Error("Path is outside the CAPAXION Core API boundary");
  }
  return value;
}

function registerIpc() {
  ipcMain.handle("window:minimize", () => mainWindow?.minimize());
  ipcMain.handle("window:maximize", () => {
    if (!mainWindow) return;
    mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
  });
  ipcMain.handle("window:close", () => mainWindow?.close());
  ipcMain.handle("window:zoom", (_event, delta = 0) => {
    desktopZoomFactor = Math.max(1, Math.min(1.4, desktopZoomFactor + Number(delta || 0)));
    mainWindow?.webContents.setZoomFactor(desktopZoomFactor);
    return Math.round(desktopZoomFactor * 100);
  });
  ipcMain.handle("core:status", () => coreHealth());
  ipcMain.handle("core:request", async (_event, request = {}) => {
    const apiPath = validateCorePath(request.path);
    const method = String(request.method || "GET").toUpperCase();
    if (!["GET", "POST", "PUT", "PATCH", "DELETE"].includes(method)) {
      throw new Error("Unsupported HTTP method");
    }
    const headers = new Headers(request.headers || {});
    headers.set("X-Dev-Actor", request.actor || "demo-planner");
    if (request.body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(`${CORE_URL}${apiPath}`, {
      method,
      headers,
      body: request.body === undefined ? undefined : JSON.stringify(request.body),
      signal: AbortSignal.timeout(apiPath === "/api/v1/agent/capability-check" ? 370000 : 30000),
    });
    const text = await response.text();
    let payload;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = text;
    }
    if (!response.ok) {
      const detail = payload?.detail || payload || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  });
  ipcMain.handle("core:upload", async (_event, request = {}) => {
    const apiPath = validateCorePath(request.path);
    const endpointPath = new URL(apiPath, CORE_ENDPOINT).pathname;
    let selected = selectedFiles.get(String(request.fileId || ""));
    if (!selected && request.filePath) {
      const requestedPath = path.resolve(String(request.filePath));
      selected = [...selectedFiles.values()].find((item) => item.path === requestedPath);
    }
    if (!selected) throw new Error("File was not selected or dropped in CAPAXION");
    const extension = path.extname(selected.name).toLowerCase();
    const content = selected.content || fs.readFileSync(selected.path);
    if (endpointPath === "/api/v1/planning/imports/spreadsheet/preview") {
      if (![".xlsx", ".csv"].includes(extension) || content.length > 5 * 1024 * 1024) {
        throw new Error("Spreadsheet must be XLSX or CSV and no larger than 5 MB");
      }
    } else if (endpointPath === "/api/v1/agent/attachments/parse") {
      if (content.length > 15 * 1024 * 1024) throw new Error("Attachment exceeds 15 MB");
    } else {
      throw new Error("文件上传地址超出桌面端允许范围");
    }
    const headers = new Headers(request.headers || {});
    headers.set("Content-Type", "application/octet-stream");
    headers.set("X-File-Name", encodeURIComponent(selected.name));
    headers.set("X-Dev-Actor", request.actor || "demo-planner");
    const response = await fetch(`${CORE_URL}${apiPath}`, {
      method: "POST",
      headers,
      body: content,
      signal: AbortSignal.timeout(endpointPath === "/api/v1/agent/attachments/parse" ? 120000 : 30000),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload?.detail || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return payload;
  });
  ipcMain.handle("files:pick", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "添加制造资料",
      properties: ["openFile", "multiSelections"],
      filters: [
        { name: "可解析资料", extensions: ["xlsx", "csv", "docx", "pdf", "txt", "md", "log", "json", "yaml", "yml", "xml", "png", "jpg", "jpeg"] },
        { name: "全部文件", extensions: ["*"] },
      ],
    });
    if (result.canceled) return [];
    return registerSelectedFiles(result.filePaths);
  });
  ipcMain.handle("files:register-drop", (_event, entries = []) => {
    if (!Array.isArray(entries)) throw new Error("Invalid dropped file list");
    return registerDroppedFiles(entries);
  });
}

async function createWindow() {
  await ensureCore();
  mainWindow = new BrowserWindow({
    width: 1460,
    height: 920,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: "#090d12",
    icon: path.join(__dirname, "..", "assets", "capaxion-icon.png"),
    frame: false,
    show: false,
    title: "CAPAXION",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      additionalArguments: [`--capaxion-core-url=${CORE_URL}`],
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
    },
  });

  const developmentUrl = process.env.CAPAXION_DESKTOP_DEV_URL;
  if (developmentUrl) {
    await mainWindow.loadURL(developmentUrl);
  } else {
    await mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
  mainWindow.webContents.setZoomFactor(desktopZoomFactor);
  mainWindow.show();
}

app.whenReady().then(async () => {
  registerIpc();
  await createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (coreStartedByDesktop && coreProcess && !coreProcess.killed) coreProcess.kill();
});
