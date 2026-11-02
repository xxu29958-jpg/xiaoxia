
// sync: one page runtime owns shared state and DOM references.
export function createSync(app) {
function setImgHostSyncProgress(message, state = "info") {
    if (!app.dom.imgHostSyncProgress || !app.dom.imgHostSyncProgressText) {
      return;
    }

    app.dom.imgHostSyncProgress.classList.remove(
      "hidden",
      "sync-progress-success",
      "sync-progress-error",
      "sync-progress-warning",
    );
    if (state === "success") {
      app.dom.imgHostSyncProgress.classList.add("sync-progress-success");
    } else if (state === "error") {
      app.dom.imgHostSyncProgress.classList.add("sync-progress-error");
    } else if (state === "warning") {
      app.dom.imgHostSyncProgress.classList.add("sync-progress-warning");
    }
    const icon = app.dom.imgHostSyncProgress.querySelector("i");
    if (icon) {
      icon.className =
        state === "success"
          ? "fas fa-check icon"
          : state === "error"
            ? "fas fa-circle-exclamation icon"
            : "fas fa-spinner fa-spin icon";
    }
    app.dom.imgHostSyncProgressText.textContent = message;
  }

function hideImgHostSyncProgress(delay = 0) {
    if (!app.dom.imgHostSyncProgress) {
      return;
    }
    window.setTimeout(() => {
      app.dom.imgHostSyncProgress.classList.add("hidden");
    }, delay);
  }

async function waitForSyncCompletion(actionLabel = "同步") {
    return new Promise((resolve, reject) => {
      let done = false;
      let subscriptionId = null;
      let pollTimer = null;
      let timeoutTimer = null;

      const cleanup = async () => {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        if (timeoutTimer) {
          clearTimeout(timeoutTimer);
          timeoutTimer = null;
        }
        if (subscriptionId && window.AstrBotPluginPage.unsubscribeSSE) {
          try {
            await window.AstrBotPluginPage.unsubscribeSSE(subscriptionId);
          } catch (error) {
            console.warn("取消同步进度订阅失败:", error);
          }
        }
      };

      const finish = (error, result) => {
        if (done) return;
        done = true;
        void cleanup();
        if (error) {
          reject(error);
        } else {
          resolve(result);
        }
      };

      const handleStatus = (status) => {
        if (!status || done) return;
        document
          .getElementById("cancel-img-host-sync-btn")
          ?.classList.toggle("hidden", !status.running);

        if (status.running) {
          const phase =
            {
              starting: "启动中",
              planning: "检查差异",
              upload: "上传",
              download: "下载",
              verifying: "复核差异",
              delete: "清理目标端",
            }[status.phase] || "处理中";
          const count = status.total
            ? `，${status.processed || 0}/${status.total}`
            : "";
          const current = status.current_file ? `，${status.current_file}` : "";
          setImgHostSyncProgress(`${phase}${count}${current}`, "info");
          return;
        }

        if (!status.completed) {
          return;
        }

        if (status.success === false) {
          const firstError = status.errors?.[0];
          const detail = firstError
            ? `，${firstError.path}: ${firstError.message}`
            : "";
          const message =
            status.phase === "cancelled"
              ? "同步已停止，已完成的文件会保留。"
              : status.conflicts
                ? `已处理 ${status.succeeded || 0} 个文件；还有 ${
                    status.conflicts
                  } 个同名冲突，请检查后选择覆盖方向。`
                : `${actionLabel}失败：${
                    status.message || "请查看日志"
                  }${detail}`;
          void app.api.refreshUi({
            emojis: true,
            syncStatus: true,
            imgHostStatus: true,
          });
          finish(new Error(message));
          return;
        }

        if (status.success === true) {
          finish(null, status);
          return;
        }

        finish(new Error("没有检测到正在运行的同步任务"));
      };

      const pollStatus = async () => {
        try {
          const status = await app.api.apiGet("img_host/sync/task_status");
          handleStatus(status);
        } catch (error) {
          console.warn("轮询同步状态失败:", error);
        }
      };

      timeoutTimer = window.setTimeout(
        () => {
          finish(
            new Error(`${actionLabel}超时，请查看 AstrBot 日志确认结果。`),
          );
        },
        30 * 60 * 1000,
      );

      pollTimer = window.setInterval(pollStatus, 2000);
      void pollStatus();

      const syncParams = {};
      const currentManagedPackId = String(
        app.state.activeManagePackId ||
          app.dom.managePackSelect?.value ||
          app.state.managedPackIdFromUrl ||
          "",
      ).trim();
      if (currentManagedPackId) {
        syncParams.managed_pack_id = currentManagedPackId;
      }

      window.AstrBotPluginPage.subscribeSSE(
        "img_host/sync/progress",
        {
          onOpen: () =>
            setImgHostSyncProgress(`${actionLabel}进行中...`, "info"),
          onMessage: ({ parsed }) => handleStatus(parsed),
          onError: () =>
            setImgHostSyncProgress(
              "实时进度连接异常，已切换轮询确认结果。",
              "warning",
            ),
        },
        syncParams,
      )
        .then((id) => {
          subscriptionId = id;
        })
        .catch((error) => {
          console.warn("订阅同步进度失败，改用轮询:", error);
          setImgHostSyncProgress(
            "实时进度不可用，正在轮询同步结果。",
            "warning",
          );
        });
    });
  }

function createSyncStatusSection(title, categories, actionsBuilder = null) {
    const section = document.createElement("div");
    section.className = "status-section";

    const heading = document.createElement("h4");
    heading.textContent = title;
    section.appendChild(heading);

    const list = document.createElement("ul");
    categories.forEach((category) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = category;
      item.appendChild(label);

      if (actionsBuilder) {
        item.appendChild(actionsBuilder(category));
      }

      list.appendChild(item);
    });
    section.appendChild(list);

    return section;
  }

function normalizeSyncDifferences(payload) {
    const source =
      payload &&
      typeof payload.differences === "object" &&
      payload.differences !== null
        ? payload.differences
        : payload;

    return {
      missing_in_config: Array.isArray(source?.missing_in_config)
        ? source.missing_in_config
        : [],
      deleted_categories: Array.isArray(source?.deleted_categories)
        ? source.deleted_categories
        : [],
    };
  }

function renderSyncStatus(statusDiv, differences) {
    statusDiv.innerHTML = "";
    const fragments = [];
    const normalizedDifferences = normalizeSyncDifferences(differences);

    if (normalizedDifferences.missing_in_config.length > 0) {
      fragments.push(
        createSyncStatusSection(
          "新增类别（需要添加到配置）：",
          normalizedDifferences.missing_in_config,
          () =>
            app.widgets.createButton({
              className: "sync-btn",
              text: "同步配置",
              onClick: () => syncConfig(),
            }),
        ),
      );
    }

    if (normalizedDifferences.deleted_categories.length > 0) {
      fragments.push(
        createSyncStatusSection(
          "已删除的类别（配置中仍存在）：",
          normalizedDifferences.deleted_categories,
          (category) => {
            const actions = document.createElement("div");
            actions.className = "action-buttons";
            actions.appendChild(
              app.widgets.createButton({
                className: "restore-btn",
                text: "恢复类别",
                onClick: () => app.categories.restoreCategory(category),
              }),
            );
            actions.appendChild(
              app.widgets.createButton({
                className: "remove-btn",
                text: "从配置中删除",
                onClick: () => app.categories.removeFromConfig(category),
              }),
            );
            return actions;
          },
        ),
      );
    }

    if (fragments.length === 0) {
      const text = document.createElement("p");
      text.textContent = "配置与文件夹结构一致！";
      statusDiv.appendChild(text);
      return;
    }

    fragments.forEach((fragment) => {
      statusDiv.appendChild(fragment);
    });

    const syncActions = document.createElement("div");
    syncActions.className = "sync-actions";
    syncActions.appendChild(
      app.widgets.createButton({
        className: "main-sync-btn",
        text: "同步所有配置",
        onClick: () => syncConfig(),
      }),
    );
    statusDiv.appendChild(syncActions);
  }

function renderSyncStatusError(statusDiv, message) {
    statusDiv.innerHTML = "";

    const errorText = document.createElement("p");
    errorText.style.color = "red";
    errorText.textContent = `检查同步状态失败: ${message}`;
    statusDiv.appendChild(errorText);

    statusDiv.appendChild(
      app.widgets.createButton({
        className: "retry-btn",
        text: "重试",
        onClick: () => checkSyncStatus(),
      }),
    );
  }

async function checkSyncStatus(showAlert = true) {
    const statusDiv = document.getElementById("sync-status");
    if (!statusDiv) return;

    const btn = document.getElementById("check-sync-btn");
    app.widgets.setButtonBusy(btn, "正在检查中...");

    try {
      const data = await app.api.apiGet("sync/status");
      if (data.status === "error") throw new Error(data.message);

      const differences = normalizeSyncDifferences(data);
      renderSyncStatus(statusDiv, differences);

      if (showAlert) {
        app.dialogs.showToast("配置状态已刷新。", "success", "检查完成");
      }
    } catch (error) {
      console.error("检查同步状态失败:", error);
      renderSyncStatusError(statusDiv, error.message);
      if (showAlert) {
        app.dialogs.showToast(error.message, "error", "检查失败");
      }
    } finally {
      app.widgets.restoreButton(btn);
    }
  }

async function syncToRemote() {
    const btn = document.getElementById("upload-sync-btn");
    try {
      app.widgets.setButtonBusy(btn, "上传中, 这可能需要很久...");
      setImgHostSyncProgress("正在启动上传同步...", "info");

      await app.api.apiPost("img_host/sync/upload");
      await waitForSyncCompletion("上传同步");
      await app.api.refreshUi({ syncStatus: true, imgHostStatus: true });
      setImgHostSyncProgress("上传同步已完成。", "success");
      hideImgHostSyncProgress(3000);
      app.dialogs.showToast("云端上传同步已完成。", "success", "同步成功");
    } catch (error) {
      console.error("同步到云端失败:", error);
      setImgHostSyncProgress(error.message, "error");
      app.dialogs.showToast(error.message, "error", "同步失败");
    } finally {
      app.widgets.restoreButton(btn);
    }
  }

async function forceSync(direction) {
    const toRemote = direction === "remote";
    const label = toRemote ? "强制同步云端" : "从云端覆盖本地";
    const btn = document.getElementById(
      toRemote ? "force-upload-sync-btn" : "force-download-sync-btn",
    );
    try {
      const plan = await app.api.apiGet("img_host/sync/status");
      const transfers =
        (toRemote ? plan.to_overwrite_remote : plan.to_overwrite_local)
          ?.length || 0;
      const deletes =
        (toRemote ? plan.to_delete_remote : plan.to_delete_local)?.length || 0;
      const confirmed = await app.danger.showDangerConfirm({
        title: label,
        description: `以${
          toRemote ? "当前本地表情包" : "云端图库"
        }为准：写入或覆盖 ${transfers} 张图片，删除${
          toRemote ? "云端" : "本地"
        }多出的 ${deletes} 张图片。同名冲突将按此方向覆盖。`,
        actionLabel: `确认${label}`,
        countdown: 5,
      });
      if (!confirmed) return;
      app.widgets.setButtonBusy(btn, "强制同步中...");
      setImgHostSyncProgress(`正在启动${label}...`, "warning");

      await app.api.apiPost(
        `img_host/sync/overwrite_${toRemote ? "to" : "from"}_remote`,
      );
      await waitForSyncCompletion(label);
      await app.api.refreshUi({
        emojis: !toRemote,
        syncStatus: true,
        imgHostStatus: true,
      });
      setImgHostSyncProgress(`${label}已完成。`, "success");
      hideImgHostSyncProgress(3000);
      app.dialogs.showToast(`${label}已完成。`, "success", "同步成功");
    } catch (error) {
      console.error("图床覆盖同步失败：", error);
      setImgHostSyncProgress(error.message, "error");
      app.dialogs.showToast(error.message, "error", "同步失败");
    } finally {
      app.widgets.restoreButton(btn);
    }
  }

async function syncFromRemote() {
    const btn = document.getElementById("download-sync-btn");
    try {
      app.widgets.setButtonBusy(btn, "下载中...");
      setImgHostSyncProgress("正在启动下载同步...", "info");

      await app.api.apiPost("img_host/sync/download");
      await waitForSyncCompletion("下载同步");
      await app.api.refreshUi({ emojis: true, syncStatus: true, imgHostStatus: true });
      setImgHostSyncProgress("下载同步已完成。", "success");
      hideImgHostSyncProgress(3000);
      app.dialogs.showToast("云端下载同步已完成。", "success", "同步成功");
    } catch (error) {
      console.error("从云端同步失败:", error);
      setImgHostSyncProgress(error.message, "error");
      app.dialogs.showToast(error.message, "error", "同步失败");
    } finally {
      app.widgets.restoreButton(btn);
    }
  }

async function syncConfig() {
    try {
      await app.api.apiPost("sync/config");
      await app.api.refreshUi({ emojis: true, syncStatus: true });
      app.dialogs.showToast("配置已同步到最新状态。", "success", "同步成功");
    } catch (error) {
      console.error("同步配置失败:", error);
      app.dialogs.showToast(error.message, "error", "同步失败");
    }
  }

async function checkImgHostSyncStatus(showAlert = true) {
    const uploadCountElement = document.getElementById("upload-count");
    const downloadCountElement = document.getElementById("download-count");
    const remoteExtraCountElement =
      document.getElementById("remote-extra-count");
    const localExtraCountElement = document.getElementById("local-extra-count");
    const providerElement = document.getElementById("img-host-provider");
    const remoteImageCountElement =
      document.getElementById("remote-image-count");
    const remoteStorageSizeElement = document.getElementById(
      "remote-storage-size",
    );

    try {
      const data = await app.api.apiGet("img_host/sync/status");
      const conflicts = data.conflicts || [];
      document.getElementById("conflict-count").textContent = conflicts.length;
      document
        .getElementById("img-host-conflicts")
        .classList.toggle("hidden", !conflicts.length);
      const conflictFiles = document.getElementById("img-host-conflict-files");
      conflictFiles.replaceChildren();
      for (const item of conflicts.slice(0, 20)) {
        const row = document.createElement("li");
        row.textContent = `${item.relative_path}：${
          item.reason === "both_changed"
            ? "两端都已修改"
            : "尚未建立内容校验记录"
        }`;
        conflictFiles.append(row);
      }
      if (conflicts.length > 20) {
        const row = document.createElement("li");
        row.textContent = `另有 ${conflicts.length - 20} 个文件`;
        conflictFiles.append(row);
      }

      const uploadCount = data.upload_count ?? data.to_upload?.length ?? 0;
      const downloadCount =
        data.download_count ?? data.to_download?.length ?? 0;
      const remoteExtraCount =
        data.remote_extra_count ?? data.to_delete_remote?.length ?? 0;
      const localExtraCount =
        data.local_extra_count ?? data.to_delete_local?.length ?? 0;
      const remoteImageCount =
        data.remote_image_count ??
        data.remote_count ??
        data.remote_images?.length ??
        0;
      let remoteStorageText = "未知";
      if (typeof data.remote_total_bytes === "number") {
        remoteStorageText = app.layout.formatBytes(data.remote_total_bytes);
      } else if (typeof data.remote_total_bytes_estimated === "number") {
        remoteStorageText = `${app.layout.formatBytes(
          data.remote_total_bytes_estimated,
        )}（本地估算）`;
      }

      if (uploadCountElement) {
        uploadCountElement.textContent = uploadCount;
      }
      if (downloadCountElement) {
        downloadCountElement.textContent = downloadCount;
      }
      if (remoteExtraCountElement) {
        remoteExtraCountElement.textContent = remoteExtraCount;
      }
      if (localExtraCountElement) {
        localExtraCountElement.textContent = localExtraCount;
      }
      if (providerElement) {
        providerElement.textContent = data.provider_label || "未知图床";
      }
      if (remoteImageCountElement) {
        remoteImageCountElement.textContent = remoteImageCount;
      }
      if (remoteStorageSizeElement) {
        remoteStorageSizeElement.textContent = remoteStorageText;
      }

      if (showAlert) {
        app.dialogs.showToast(
          `${
            data.provider_label || "图床"
          }：云端 ${remoteImageCount} 张，待上传 ${uploadCount} 个，待下载 ${downloadCount} 个，同名待确认 ${
            conflicts.length
          } 个，云端多出 ${remoteExtraCount} 个。`,
          "info",
          "图床状态已刷新",
        );
      }
    } catch (error) {
      console.error("检查图床同步状态失败:", error);
      if (uploadCountElement) {
        uploadCountElement.textContent = "--";
      }
      if (downloadCountElement) {
        downloadCountElement.textContent = "--";
      }
      if (remoteExtraCountElement) {
        remoteExtraCountElement.textContent = "--";
      }
      if (localExtraCountElement) {
        localExtraCountElement.textContent = "--";
      }
      if (providerElement) {
        providerElement.textContent = "--";
      }
      if (remoteImageCountElement) {
        remoteImageCountElement.textContent = "--";
      }
      if (remoteStorageSizeElement) {
        remoteStorageSizeElement.textContent = "--";
      }
      if (showAlert) {
        app.dialogs.showToast(error.message, "error", "检查失败");
      }
    }
  }

async function initialize6() {
document
    .getElementById("check-sync-btn")
    .addEventListener("click", checkSyncStatus);
document
    .getElementById("upload-sync-btn")
    .addEventListener("click", syncToRemote);
document
    .getElementById("force-upload-sync-btn")
    .addEventListener("click", () => forceSync("remote"));
document
    .getElementById("force-download-sync-btn")
    .addEventListener("click", () => forceSync("local"));
document
    .getElementById("cancel-img-host-sync-btn")
    .addEventListener("click", async () => {
      const btn = document.getElementById("cancel-img-host-sync-btn");
      try {
        app.widgets.setButtonBusy(btn, "正在停止...");
        await app.api.apiPost("img_host/sync/cancel");
      } catch (error) {
        app.dialogs.showToast(error.message, "error", "停止失败");
      } finally {
        app.widgets.restoreButton(btn);
      }
    });
document
    .getElementById("download-sync-btn")
    .addEventListener("click", syncFromRemote);
}
return { setImgHostSyncProgress, hideImgHostSyncProgress, waitForSyncCompletion, createSyncStatusSection, normalizeSyncDifferences, renderSyncStatus, renderSyncStatusError, checkSyncStatus, syncToRemote, forceSync, syncFromRemote, syncConfig, checkImgHostSyncStatus, initialize6 };
}
