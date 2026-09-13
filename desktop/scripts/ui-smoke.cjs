const fs = require("node:fs");
const path = require("node:path");
const packageVersion = require("../package.json").version;

const port = process.argv[2] || "9224";
const outputRoot = process.argv[3] || path.join(__dirname, "..", "release", "ui-smoke");
const pages = ["workspace", "tower", "planning", "results", "capacity", "models", "capabilities"];

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
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`DevTools timed out: ${method}`)); }, params.awaitPromise ? 180000 : 20000);
      pending.set(id, { resolve: (value) => { clearTimeout(timer); resolve(value); }, reject: (error) => { clearTimeout(timer); reject(error); } });
    });
  }
  await call("Runtime.enable");
  await call("Page.enable");
  await call("Page.bringToFront");
  await call("Emulation.setFocusEmulationEnabled", { enabled: true });
  const results = [];
  for (let index = 0; index < pages.length; index += 1) {
    if (process.env.CAPAXION_SMOKE_PAGE && pages[index] !== process.env.CAPAXION_SMOKE_PAGE) continue;
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
    if (pages[index] === "workspace" && process.env.CAPAXION_SMOKE_RESIZE === "1") {
      const resized = await call("Runtime.evaluate", {
        expression: `(async()=>{
          const panel=document.querySelector(".conversation-history");
          const handle=document.querySelector(".history-resizer");
          const before=panel.getBoundingClientRect().width;
          const x=handle.getBoundingClientRect().left+4;
          handle.dispatchEvent(new PointerEvent("pointerdown",{bubbles:true,clientX:x}));
          await new Promise(resolve=>setTimeout(resolve,30));
          document.dispatchEvent(new PointerEvent("pointermove",{bubbles:true,clientX:x+36}));
          document.dispatchEvent(new PointerEvent("pointerup",{bubbles:true,clientX:x+36}));
          await new Promise(resolve=>setTimeout(resolve,60));
          return {before,after:panel.getBoundingClientRect().width};
        })()`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (resized.exceptionDetails || resized.result.value.after < resized.result.value.before + 30) {
        throw new Error("Workspace pane resize did not change the history width");
      }
    }
    if (pages[index] === "workspace" && process.env.CAPAXION_SMOKE_ATTACHMENT === "1") {
      const attachmentPath = process.env.CAPAXION_SMOKE_ATTACHMENT_PATH;
      const attachmentName = attachmentPath ? path.basename(attachmentPath) : "ui-smoke.txt";
      const attachmentBytes = attachmentPath
        ? fs.readFileSync(attachmentPath).toString("base64")
        : Buffer.from("工单编号：WO-UI-401，计划数量：41件。", "utf8").toString("base64");
      const requiresSpreadsheetPreview = /\.(xlsx|csv)$/i.test(attachmentName);
      const expectedAttachmentText = process.env.CAPAXION_SMOKE_EXPECT_TEXT || "";
      const attachment = await call("Runtime.evaluate", {
        expression: `(async()=>{
          const binary=atob(${JSON.stringify(attachmentBytes)});
          const bytes=Uint8Array.from(binary,character=>character.charCodeAt(0));
          const transfer=new DataTransfer();
          transfer.items.add(new File([bytes],${JSON.stringify(attachmentName)}));
          window.dispatchEvent(new DragEvent("drop",{dataTransfer:transfer,bubbles:true,cancelable:true}));
          for(let i=0;i<240;i++){
            await new Promise(resolve=>setTimeout(resolve,250));
            const attachment=document.querySelector(".attachments");
            const body=document.body.innerText;
            const parsed=attachment?.textContent.includes(${JSON.stringify(attachmentName)})&&attachment.textContent.includes("已解析");
            const preview=${JSON.stringify(requiresSpreadsheetPreview)}?(
              body.includes("可同步到排产")||
              body.includes("可生成排产")||
              body.includes("可写入产能管理")||
              body.includes("项字段需要补充")||
              body.includes("还无法识别为人员、订单或排产数据")
            ):true;
            const misleadingZero=body.includes("生产数据预检 ${attachmentName}：0 个工单、0 道工序、0 个产能资源");
            const expected=${JSON.stringify(expectedAttachmentText)}?body.includes(${JSON.stringify(expectedAttachmentText)}):true;
            if(parsed&&preview&&expected){
              return {parsed:true,preview,misleadingZero,text:attachment.textContent};
            }
          }
          return {parsed:false,text:document.body.innerText};
        })()`,
        awaitPromise: true,
        returnByValue: true,
      });
      if (attachment.exceptionDetails || !attachment.result.value?.parsed || attachment.result.value?.misleadingZero) {
        throw new Error("Virtual drag attachment did not parse in desktop UI");
      }
      if (process.env.CAPAXION_SMOKE_ACTION_TEXT) {
        const actionText = process.env.CAPAXION_SMOKE_ACTION_TEXT;
        const actionExpected = process.env.CAPAXION_SMOKE_ACTION_EXPECT || "已执行并核验";
        const action = await call("Runtime.evaluate", {
          expression: `(async()=>{
            let button=null;
            for(let i=0;i<240&&!button;i++){
              button=[...document.querySelectorAll(".message-action")].find(item=>item.textContent.includes(${JSON.stringify(actionText)}));
              if(!button) await new Promise(resolve=>setTimeout(resolve,250));
            }
            if(!button) return {passed:false,text:document.body.innerText};
            button.click();
            for(let i=0;i<240;i++){
              await new Promise(resolve=>setTimeout(resolve,250));
              const body=document.body.innerText;
              if(body.includes(${JSON.stringify(actionExpected)})) return {passed:true};
            }
            return {passed:false,text:document.body.innerText};
          })()`,
          awaitPromise: true,
          returnByValue: true,
        });
        if (action.exceptionDetails || !action.result.value?.passed) {
          throw new Error(`Attachment action failed: ${actionText}`);
        }
      }
      if (process.env.CAPAXION_SMOKE_DOCUMENT_QUERY) {
        const documentQuery = await call("Runtime.evaluate", {
          expression: `(async()=>{
            const input=document.querySelector(".composer textarea");
            const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set;
            setter.call(input,${JSON.stringify(process.env.CAPAXION_SMOKE_DOCUMENT_QUERY)});
            input.dispatchEvent(new Event("input",{bubbles:true}));
            await new Promise(resolve=>setTimeout(resolve,100));
            document.querySelector(".send-button")?.click();
            for(let i=0;i<240;i++){
              await new Promise(resolve=>setTimeout(resolve,250));
              const evidence=document.querySelectorAll(".evidence-list article");
              if(evidence.length)return {count:evidence.length,text:document.body.innerText};
            }
            return {count:0,text:document.body.innerText};
          })()`,
          awaitPromise: true,
          returnByValue: true,
        });
        if (documentQuery.exceptionDetails || documentQuery.result.value?.count < 1) {
          throw new Error("Document query did not render retrieved evidence");
        }
      }
    }
    if (pages[index] === "capabilities" && process.env.CAPAXION_SMOKE_BENCHMARK) {
      const evaluate = async (expression) => (await call("Runtime.evaluate", { expression, returnByValue: true })).result.value;
      const waitFor = async (expression) => {
        for (let attempt = 0; attempt < 40; attempt += 1) {
          if (await evaluate(expression)) return;
          await wait(500);
        }
        throw new Error("Desktop benchmark UI state did not arrive");
      };
      if (process.env.CAPAXION_SMOKE_BENCHMARK === "1") {
        const summaries = async () => (await call("Runtime.evaluate", {
          expression: `window.capaxion.core.request({path:"/api/v1/agent/capabilities"})`,
          awaitPromise: true, returnByValue: true,
        })).result.value.runs;
        const before = (await summaries())[0]?.runId;
        const started = await evaluate(`(()=>{const b=[...document.querySelectorAll("button")].find(b=>b.textContent==="开始当前模型测评");if(!b||b.disabled)return false;b.click();return true})()`);
        if (!started) throw new Error("Benchmark start unavailable");
        let complete = false;
        for (let attempt = 0; attempt < 150; attempt += 1) {
          await wait(1000);
          const latest = (await summaries())[0];
          if (latest?.runId !== before && latest?.status === "COMPLETED") { complete = true; break; }
          if (latest?.runId !== before && latest?.status === "FAILED") throw new Error("Benchmark failed");
        }
        if (!complete) throw new Error("Benchmark did not complete");
        await evaluate(`document.querySelectorAll(".sidebar nav button")[0].click()`);
        await wait(100);
        await evaluate(`document.querySelectorAll(".sidebar nav button")[6].click()`);
      }
      await waitFor(`document.querySelectorAll(".benchmark-table tbody tr").length>=2`);
      await evaluate(`document.querySelector(".benchmark-table tbody button").click()`);
      await waitFor(`document.querySelectorAll(".benchmark-case").length===9`);
      for (let side = 0; side < 2; side += 1) {
        await evaluate(`(()=>{const s=document.querySelectorAll(".benchmark-compare select")[${side}];const options=[...s.options].slice(1);s.value=(options.find(o=>o.textContent.includes(${side === 0 ? '"DEEPSEEK"' : '"本地"'}))||options[${side}]).value;s.dispatchEvent(new Event("change",{bubbles:true}));})()`);
        await wait(100);
      }
      await evaluate(`document.querySelector(".benchmark-compare button").click()`);
      await waitFor(`document.querySelectorAll(".benchmark-comparison tbody tr").length===9`);
      await evaluate(`document.querySelector(".benchmark-case").open=true`);
      await evaluate(`document.querySelector(".benchmark-comparison").scrollIntoView({block:"start"})`);
      const evidenceScreenshot = await call("Page.captureScreenshot", { format: "png", fromSurface: true });
      fs.writeFileSync(path.join(outputRoot, "comparison.png"), Buffer.from(evidenceScreenshot.data, "base64"));
    }
    const state = await call("Runtime.evaluate", {
      expression: `(()=>{const page=document.querySelector(".native-page");const content=document.querySelector(".content-area");const rect=page?.getBoundingClientRect();return {title:document.querySelector(".page-header h1")?.textContent||document.querySelector(".conversation h1")?.textContent||"",version:document.querySelector(".brand em")?.textContent||"",filePickerVisible:${JSON.stringify(pages[index] === "workspace")}?Boolean(document.querySelector(".composer-file-button")):null,textLength:document.body.innerText.length,fatal:Boolean(document.querySelector(".fatal-error")),loadError:Boolean(document.querySelector(".error-state")),viewportHeight:document.documentElement.clientHeight,contentHeight:content?.clientHeight||0,pageClientHeight:page?.clientHeight||0,pageScrollHeight:page?.scrollHeight||0,scrollable:Boolean(page&&page.scrollHeight>page.clientHeight+1),scrollPoint:rect?{x:Math.round(rect.left+rect.width/2),y:Math.round(rect.top+Math.min(rect.height/2,240))}:null}})()`,
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
    const screenshot = await call("Page.captureScreenshot", { format: "png", fromSurface: true });
    fs.writeFileSync(path.join(outputRoot, `${index + 1}-${pages[index]}.png`), Buffer.from(screenshot.data, "base64"));
    results.push({ page: pages[index], ...value, scrollWorked });
  }
  socket.close();
  process.stdout.write(`${JSON.stringify({ results, exceptions }, null, 2)}\n`);
  if (exceptions.length || results.some((result) => result.fatal || result.loadError || result.version !== `v${packageVersion}` || result.filePickerVisible === false || result.textLength < 100 || result.contentHeight >= result.viewportHeight || (result.scrollable && result.scrollWorked !== true))) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exit(1);
});
