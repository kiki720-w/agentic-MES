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
  await call("Page.enable");
  const results = [];
  for (let index = 0; index < pages.length; index += 1) {
    await call("Runtime.evaluate", {
      expression: `document.querySelectorAll(".sidebar nav button")[${index}]?.click()`,
    });
    await wait(1600);
    if (pages[index] === "workspace" && process.env.CAPAXION_SMOKE_SELF_CHECK === "1") {
      const check = await call("Runtime.evaluate", {
        expression: `(async()=>{
          const button=[...document.querySelectorAll(".quick-prompts button")].find(b=>b.textContent.includes("文字能力自检"));
          if(!button) throw new Error("Capability check entry missing");
          button.click();
          for(let i=0;i<120;i++){
            await new Promise(resolve=>setTimeout(resolve,500));
            const text=document.querySelector(".messages")?.textContent||"";
            if(text.includes("text-smoke-v1")) return {passed:text.includes("文字自检：通过"),evidence:text.includes("证据已保存到本机 Core")};
          }
          throw new Error("Capability check did not complete");
        })()`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (check.exceptionDetails || !check.result.value?.passed || !check.result.value?.evidence) {
        throw new Error("Live synthetic capability check failed in desktop UI");
      }
    }
    const state = await call("Runtime.evaluate", {
      expression: `(()=>{const page=document.querySelector(".native-page");const content=document.querySelector(".content-area");const rect=page?.getBoundingClientRect();return {title:document.querySelector(".page-header h1")?.textContent||document.querySelector(".conversation h1")?.textContent||"",textLength:document.body.innerText.length,fatal:Boolean(document.querySelector(".fatal-error")),loadError:Boolean(document.querySelector(".error-state")),viewportHeight:document.documentElement.clientHeight,contentHeight:content?.clientHeight||0,pageClientHeight:page?.clientHeight||0,pageScrollHeight:page?.scrollHeight||0,scrollable:Boolean(page&&page.scrollHeight>page.clientHeight+1),scrollPoint:rect?{x:Math.round(rect.left+rect.width/2),y:Math.round(rect.top+Math.min(rect.height/2,240))}:null}})()`,
      returnByValue: true,
    });
    const value = state.result.value;
    let scrollWorked = null;
    if (value.scrollable && value.scrollPoint) {
      await call("Runtime.evaluate", { expression: `document.querySelector(".native-page").scrollTop=0` });
      await call("Input.dispatchMouseEvent", { type: "mouseMoved", x: value.scrollPoint.x, y: value.scrollPoint.y });
      await call("Input.dispatchMouseEvent", { type: "mouseWheel", x: value.scrollPoint.x, y: value.scrollPoint.y, deltaX: 0, deltaY: 460 });
      await wait(180);
      const scrolled = await call("Runtime.evaluate", {
        expression: `document.querySelector(".native-page")?.scrollTop||0`,
        returnByValue: true,
      });
      scrollWorked = scrolled.result.value > 0;
    }
    const screenshot = await call("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(outputRoot, `${index + 1}-${pages[index]}.png`), Buffer.from(screenshot.data, "base64"));
    results.push({ page: pages[index], ...value, scrollWorked });
  }
  socket.close();
  process.stdout.write(`${JSON.stringify({ results, exceptions }, null, 2)}\n`);
  if (exceptions.length || results.some((result) => result.fatal || result.loadError || result.textLength < 100 || result.contentHeight >= result.viewportHeight || (result.scrollable && result.scrollWorked !== true))) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
