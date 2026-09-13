const { contextBridge, ipcRenderer, webUtils } = require("electron");

const coreArgument = process.argv.find((value) => value.startsWith("--capaxion-core-url="));
const coreUrl = coreArgument ? coreArgument.slice("--capaxion-core-url=".length) : "http://127.0.0.1:8000";
let droppedFilesHandler = null;

function containsFiles(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

window.addEventListener("dragover", (event) => {
  if (!containsFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
}, true);

window.addEventListener("drop", async (event) => {
  if (!containsFiles(event)) return;
  event.preventDefault();
  const filePaths = Array.from(event.dataTransfer.files || [])
    .map((file) => webUtils.getPathForFile(file))
    .filter(Boolean);
  try {
    const files = await ipcRenderer.invoke("files:register-drop", filePaths);
    droppedFilesHandler?.(files, null);
  } catch (error) {
    droppedFilesHandler?.([], error?.message || "无法读取拖入的文件");
  }
}, true);

contextBridge.exposeInMainWorld("capaxion", {
  platform: process.platform,
  coreUrl,
  window: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    maximize: () => ipcRenderer.invoke("window:maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    zoom: (delta) => ipcRenderer.invoke("window:zoom", delta),
  },
  core: {
    request: (request) => ipcRenderer.invoke("core:request", request),
    status: () => ipcRenderer.invoke("core:status"),
    upload: (request) => ipcRenderer.invoke("core:upload", request),
  },
  files: {
    pick: () => ipcRenderer.invoke("files:pick"),
    onDrop: (callback) => { droppedFilesHandler = typeof callback === "function" ? callback : null; },
    offDrop: () => { droppedFilesHandler = null; },
  },
});
