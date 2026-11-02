import { setupCatalogUpdates } from "../updates.js";
// install: one page runtime owns shared state and DOM references.
export function createInstall(app) {
function setInstallBusy(busy) {
    app.state.installBusy = busy;
    app.dom.installSourceBtn.disabled = busy;
    document.querySelectorAll(".pack-update").forEach((button) => {
      button.disabled = button.dataset.blocked === "true";
    });
    document.querySelectorAll(".pack-install").forEach((button) => {
      button.disabled =
        busy || !app.state.installedStateKnown || button.dataset.installed === "true";
      if (busy && button.dataset.installed !== "true") {
        button.title = "请等待当前安装任务结束";
      } else {
        button.removeAttribute("title");
      }
    });
  }

function openInstallDialog(packName, onConfirm) {
    if (app.state.installBusy) return;
    app.state.pendingInstallAction = onConfirm;
    app.dom.installDialogPackName.textContent = `目标: ${packName || "未命名"}`;
    app.dom.installDialogOverwriteCheckbox.checked = false;
    app.dom.installDialogDefaultCheckbox.checked = false;
    app.dom.installDialog.classList.remove("hidden");
    app.dom.installDialog.setAttribute("aria-hidden", "false");
  }

function closeInstallDialog() {
    app.state.pendingInstallAction = null;
    app.dom.installDialog.classList.add("hidden");
    app.dom.installDialog.setAttribute("aria-hidden", "true");
  }

async function monitorInstallProgress(jobId, packName) {
    app.state.activeInstallJobId = jobId;
    setInstallBusy(true);
    app.dom.installTaskName.textContent = `正在安装：${packName || "未命名"}`;
    app.dom.installTaskStatus.textContent = "正在准备安装任务";
    app.dom.installTask.classList.remove("hidden");
    app.dom.installProgressPackName.textContent = `目标: ${packName || "未命名"}`;
    app.dom.installProgressPhase.textContent = "正在准备安装任务";
    app.dom.installProgressPercent.textContent = "0%";
    app.dom.installProgressBar.style.width = "0%";
    app.dom.installProgressTrack.classList.add("indeterminate");
    app.dom.installProgressTrack.setAttribute("aria-valuenow", "0");
    app.dom.installProgressBytes.textContent = "等待下载开始…";
    app.dom.installProgressCancel.disabled = false;
    app.dom.installProgressCancel.textContent = "取消安装";
    app.dom.installProgressDialog.classList.remove("hidden");
    app.dom.installProgressDialog.setAttribute("aria-hidden", "false");
    let consecutivePollFailures = 0;
    let installSucceeded = false;
    try {
      while (true) {
        await new Promise((resolve) =>
          window.setTimeout(
            resolve,
            Math.min(1000 + consecutivePollFailures * 1000, 10000),
          ),
        );
        let status;
        try {
          status = await app.api.apiGet("community/install/status", {
            job_id: jobId,
          });
          consecutivePollFailures = 0;
        } catch (error) {
          if (String(error?.message || "").includes("安装任务不存在或已过期")) {
            throw new Error("安装任务已失效，请刷新已安装列表后检查结果");
          }
          consecutivePollFailures += 1;
          app.dom.installProgressPhase.textContent =
            "连接暂时中断，正在重连；后台安装可能仍在继续";
          app.dom.installTaskStatus.textContent = app.dom.installProgressPhase.textContent;
          app.dom.installProgressPercent.textContent = "重连中";
          continue;
        }
        const hasKnownProgress =
          status?.progress !== null &&
          status?.progress !== undefined &&
          Number.isFinite(Number(status.progress));
        const progress = hasKnownProgress
          ? Math.max(0, Math.min(100, Number(status.progress)))
          : 0;
        const downloadedBytes = Number(status?.downloaded_bytes || 0);
        const totalBytes = Number(status?.total_bytes || 0);
        app.dom.installProgressPhase.textContent = status?.message || "正在安装表情包";
        app.dom.installTaskStatus.textContent = app.dom.installProgressPhase.textContent;
        app.dom.installProgressCancel.disabled = status?.status === "cancelling";
        app.dom.installProgressCancel.textContent =
          status?.status === "cancelling" ? "正在取消…" : "取消安装";
        app.dom.installProgressPercent.textContent = hasKnownProgress
          ? `${Math.round(progress)}%`
          : "进行中";
        app.dom.installProgressBar.style.width = `${progress}%`;
        if (hasKnownProgress) {
          app.dom.installProgressTrack.setAttribute(
            "aria-valuenow",
            String(Math.round(progress)),
          );
          app.dom.installProgressTrack.removeAttribute("aria-valuetext");
        } else {
          app.dom.installProgressTrack.removeAttribute("aria-valuenow");
          app.dom.installProgressTrack.setAttribute(
            "aria-valuetext",
            app.dom.installProgressPhase.textContent,
          );
        }
        app.dom.installProgressTrack.classList.toggle(
          "indeterminate",
          !hasKnownProgress,
        );
        if (status?.phase === "downloading") {
          const downloadedMegabytes = (downloadedBytes / 1024 ** 2).toFixed(1);
          const totalMegabytes = (totalBytes / 1024 ** 2).toFixed(1);
          app.dom.installProgressBytes.textContent = totalBytes
            ? `${downloadedMegabytes} MB / ${totalMegabytes} MB`
            : `已下载 ${downloadedMegabytes} MB`;
        } else {
          app.dom.installProgressBytes.textContent =
            status?.phase === "queued" || status?.phase === "connecting"
              ? "连接下载源后将显示下载进度"
              : "正在处理资源包，请稍候…";
        }
        if (status?.status === "succeeded") {
          installSucceeded = true;
          await new Promise((resolve) => window.setTimeout(resolve, 400));
          return status.result || {};
        }
        if (status?.status === "failed") {
          throw new Error(status?.message || "安装失败");
        }
        if (status?.status === "cancelled") {
          const error = new Error(status?.message || "安装已取消");
          error.name = "AbortError";
          throw error;
        }
      }
    } finally {
      if (app.state.activeInstallJobId === jobId) {
        app.state.activeInstallJobId = "";
        app.dom.installTask.classList.add("hidden");
        app.dom.installProgressDialog.classList.add("hidden");
        app.dom.installProgressDialog.setAttribute("aria-hidden", "true");
        if (installSucceeded) {
          void window.MemeUI.confirm({
            title: "表情包安装成功",
            message:
              "请前往设置中心配置使用规则，让新下载的表情包在对应会话中生效。",
            confirmText: "前往设置中心",
            cancelText: "稍后配置",
          }).then((confirmed) => {
            if (confirmed) {
              location.href = window.MemeUI.withCurrentAuthParams(
                "../settings/index.html#rules",
                { view: null, asset_token: window.MemeUI.navAuthToken || null },
              ).toString();
            }
          });
        }
      }
    }
  }

async function installWithProgress(payload, packName) {
    const startResponse = await app.api.apiPost("community/install/start", payload);
    const jobId = String(startResponse?.job_id || "").trim();
    if (!jobId) {
      throw new Error("安装任务未返回 job_id");
    }
    return monitorInstallProgress(jobId, packName);
  }

async function restoreActiveInstall() {
    setInstallBusy(true);
    try {
      const status = await app.api.apiGet("community/install/status");
      const jobId = String(status?.job_id || "").trim();
      if (!jobId || !["running", "cancelling"].includes(status?.status)) {
        return;
      }
      app.status.addLog("检测到后台安装任务，已恢复进度显示");
      await monitorInstallProgress(
        jobId,
        status?.source_label || "后台安装任务",
      );
      app.status.addLog("后台安装任务已完成");
      window.MemeUI?.toast?.("后台安装任务已完成", "success");
      await app.catalog.refreshInstalledSet();
      app.catalog.renderCatalog();
    } catch (error) {
      app.status.addLog(
        `后台任务状态: ${error?.message || String(error)}`,
        error?.name !== "AbortError",
      );
    } finally {
      setInstallBusy(false);
    }
  }

async function confirmInstallDialog() {
    if (!app.state.pendingInstallAction) {
      closeInstallDialog();
      return;
    }
    const handler = app.state.pendingInstallAction;
    app.state.pendingInstallAction = null;
    const options = {
      overwrite: app.dom.installDialogOverwriteCheckbox.checked,
      setAsDefault: app.dom.installDialogDefaultCheckbox.checked,
    };
    closeInstallDialog();
    await handler(options);
  }

async function installByPack(pack, button, options = {}) {
    if (app.state.installBusy) return;
    await app.catalog.refreshInstalledSet();
    if (!app.state.installedStateKnown) return;
    if (app.state.installedPackIds.has(pack.id) && !options.overwrite) {
      app.catalog.renderCatalog();
      app.status.addLog("此包已安装，请使用更新功能或明确选择覆盖安装", true);
      return;
    }
    const packId = String(pack?.id || "").trim();
    const source =
      pack && typeof pack.source === "object" && pack.source
        ? pack.source
        : null;
    if (!packId) {
      app.status.addLog("无效的 pack_id", true);
      return;
    }

    setInstallBusy(true);
    app.status.setLoading(button, "安装中…");
    try {
      const payload = {
        pack_id: packId,
        overwrite: Boolean(options.overwrite),
        set_as_default: Boolean(options.setAsDefault),
      };
      if (source) {
        payload.source = source;
      }

      const response = await installWithProgress(payload, pack?.name || packId);
      app.status.addLog(
        `安装成功: ${response.pack_id || packId} ${response.version || ""}`,
      );
      window.MemeUI?.toast?.(
        `已安装 ${pack.name || response.pack_id}`,
        "success",
      );
      await app.catalog.refreshInstalledSet();
      app.catalog.renderCatalog();
    } catch (error) {
      if (error?.name === "AbortError") {
        app.status.addLog(`已取消安装: ${pack.name || packId}`);
        window.MemeUI?.toast?.("已取消安装", "info");
      } else {
        app.status.addLog(`安装失败(${packId}): ${error?.message || String(error)}`, true);
      }
    } finally {
      app.status.clearLoading(button);
      setInstallBusy(false);
    }
  }

async function installBySource(options = {}) {
    if (app.state.installBusy) return;
    const repo = String(app.dom.sourceRepoInput.value || "").trim();
    const ref = String(app.dom.sourceRefInput.value || "").trim();
    const subpath = app.covers.normalizeGithubSubpath(app.dom.sourceSubpathInput.value);

    setInstallBusy(true);
    app.status.setLoading(app.dom.installSourceBtn, "安装中…");
    try {
      const response = await installWithProgress(
        {
          source: {
            type: "github",
            repo,
            ref,
            subpath,
          },
          overwrite: Boolean(options.overwrite),
          set_as_default: Boolean(options.setAsDefault),
        },
        `${repo}@${ref}`,
      );
      app.status.addLog(
        `按来源安装成功: ${response.pack_id || repo} ${response.version || ""}`,
      );
      window.MemeUI?.toast?.(`已安装 ${response.pack_id || repo}`, "success");
      await app.catalog.refreshInstalledSet();
      app.catalog.renderCatalog();
    } catch (error) {
      if (error?.name === "AbortError") {
        app.status.addLog(`已取消安装: ${repo}@${ref}`);
        window.MemeUI?.toast?.("已取消安装", "info");
      } else {
        app.status.addLog(`按来源安装失败: ${error?.message || String(error)}`, true);
      }
    } finally {
      app.status.clearLoading(app.dom.installSourceBtn);
      setInstallBusy(false);
    }
  }

async function initialize1() {
app.dom.sourceInstallForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (app.state.installBusy) return;
    const repo = app.dom.sourceRepoInput.value.trim();
    const ref = app.dom.sourceRefInput.value.trim();
    const subpath = app.covers.normalizeGithubSubpath(app.dom.sourceSubpathInput.value);
    app.dom.sourceRepoInput.setCustomValidity(
      /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)
        ? ""
        : "请填写 owner/repo 格式的 GitHub 仓库名",
    );
    app.dom.sourceRefInput.setCustomValidity(ref ? "" : "请填写分支名、标签或提交 ID");
    app.dom.sourceSubpathInput.setCustomValidity(
      subpath && !subpath.includes("\\") && !subpath.split("/").includes("..")
        ? ""
        : "请填写有效目录，根目录填 .，不能包含 .. 或反斜杠",
    );
    if (!app.dom.sourceInstallForm.reportValidity()) return;
    openInstallDialog(`${repo}@${ref}（路径：${subpath}）`, async (options) => {
      await installBySource(options);
    });
  });
for (const input of [app.dom.sourceRepoInput, app.dom.sourceRefInput, app.dom.sourceSubpathInput]) {
    input.addEventListener("input", () => input.setCustomValidity(""));
  }
}
return { setInstallBusy, openInstallDialog, closeInstallDialog, monitorInstallProgress, installWithProgress, restoreActiveInstall, confirmInstallDialog, installByPack, installBySource, initialize1 };
}
