const fs = require("node:fs");
const path = require("node:path");

const port = process.argv[2] || "9224";
const outputRoot = process.argv[3] || path.join(__dirname, "..", "release", "ui-smoke");
const pages = ["workspace", "tower", "planning", "results", "capacity", "models"];

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function main() {
  const targets = await fetch(`http://127.0.0.1:${port}/json`).then((response) => response.json());
  const target = targets.find((item) => item.type === "page" && item.title === "CAPAXION");
  if (!target) throw new Error("CAPAXION DevTools target not found");
  fs.mkdirSync(outputRoot, { recursive: true });
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map();
  const exceptions = [];
  let nextId = 1;
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.exceptionThrown") {
      exceptions.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    }
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
  };
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
  });
  function call(method, params = {}) {
    const id = nextId++;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }
  await call("Runtime.enable");
  const results = [];
  for (let index = 0; index < pages.length; index += 1) {
    await call("Runtime.evaluate", {
      expression: `document.querySelectorAll(".sidebar nav button")[${index}]?.click()`,
    });
    await wait(1600);
    const state = await call("Runtime.evaluate", {
      expression: `({title:document.querySelector(".page-header h1")?.textContent||document.querySelector(".conversation h1")?.textContent||"",textLength:document.body.innerText.length,fatal:Boolean(document.querySelector(".fatal-error")),loadError:Boolean(document.querySelector(".error-state"))})`,
      returnByValue: true,
    });
    const screenshot = await call("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(outputRoot, `${index + 1}-${pages[index]}.png`), Buffer.from(screenshot.data, "base64"));
    results.push({ page: pages[index], ...state.result.value });
  }
  socket.close();
  process.stdout.write(`${JSON.stringify({ results, exceptions }, null, 2)}\n`);
  if (exceptions.length || results.some((result) => result.fatal || result.loadError || result.textLength < 100)) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
