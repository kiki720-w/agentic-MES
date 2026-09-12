const { contextBridge, ipcRenderer } = require("electron");

const coreArgument = process.argv.find((value) => value.startsWith("--capaxion-core-url="));
const coreUrl = coreArgument ? coreArgument.slice("--capaxion-core-url=".length) : "http://127.0.0.1:8000";

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
  },
});
