
// viewer: one page runtime owns shared state and DOM references.
export function createViewer(app) {
async function openMemory(memoryId) {
  await app.shell.withBusy("正在读取记忆…", async () => {
    const response = await app.api.apiGet("/memory?id=" + encodeURIComponent(memoryId));
    // The page API wraps detail responses as { memory: ... }; normalize it
    // here so all detail renderers receive the actual memory object.
    const memory = response && response.memory ? response.memory : response;
    if (!memory || typeof memory !== "object") throw new Error("记忆详情为空");
    app.shell.openDrawer(app.common.clip(app.records.memoryTitle(memory), 42), "记忆详情", app.records.memoryDetailHtml(memory));
    app.records.bindMemoryActions(memoryId);
  });
}

async function openAssemblyViewer(memoryId) {
  const viewer = app.values.$("#assemblyViewer");
  const canvas = app.values.$("#assemblyViewerCanvas");
  if (!viewer || !canvas) return;
  const ticket = ++app.values.assemblyViewerTicket;
  const title = app.values.$("#drawerTitle")?.textContent || "档案结构";
  app.values.$("#assemblyViewerTitle").textContent = title;
  app.values.$("#assemblyViewerFile").textContent = "FILE " + app.common.compact(memoryId) + " / INTERNAL DATABASE";
  viewer.hidden = false;
  viewer.setAttribute("aria-hidden", "false");
  document.body.classList.add("assembly-viewer-open");
  app.values.$("#assemblyViewerLoading").hidden = false;
  app.values.$("#assemblyViewerRetry").hidden = true;
  const mount = () => window.MemoryCinema3D?.mountAssemblyViewer(canvas, {
    onState: (value) => { const node = app.values.$("#assemblyViewerState"); if (node) node.textContent = value; },
    onError: () => { const node = app.values.$("#assemblyViewerRetry"); if (node) node.hidden = false; },
  });
  let mounted;
  if (window.MemoryCinema3D?.mountAssemblyViewer) mounted = await mount();
  else {
    await Promise.race([
      new Promise((resolve) => window.addEventListener("memory-cinema-ready", resolve, { once: true })),
      app.common.sleep(8000),
    ]);
    if (!window.MemoryCinema3D?.mountAssemblyViewer) {
      canvas.innerHTML = '<span class="scene-loading">ASSEMBLY VIEWER / UNAVAILABLE</span>';
      app.values.$("#assemblyViewerLoading").hidden = true;
      app.values.$("#assemblyViewerRetry").hidden = false;
      return;
    }
    mounted = await mount();
  }
  if (ticket !== app.values.assemblyViewerTicket || viewer.hidden) {
    mounted?.dispose?.();
    return;
  }
  app.values.state.assemblyViewer = mounted;
  app.values.$("#assemblyViewerLoading").hidden = true;
}

function closeAssemblyViewer() {
  app.values.assemblyViewerTicket += 1;
  app.values.state.assemblyViewer?.dispose?.();
  app.values.state.assemblyViewer = null;
  const viewer = app.values.$("#assemblyViewer");
  if (viewer) {
    viewer.hidden = true;
    viewer.setAttribute("aria-hidden", "true");
  }
  document.body.classList.remove("assembly-viewer-open");
}
return { openMemory, openAssemblyViewer, closeAssemblyViewer };
}
