
// vectors: one page runtime owns shared state and DOM references.
export function createVectors(app) {
function packSupportsVectorStatus(packId) {
    const pack = app.state.managePacksById.get(String(packId || "").trim());
    return pack?.supports_vector_rebuild === true;
  }

function hideManagePackVectorStatus() {
    app.dom.packVectorStatus?.classList.add("hidden");
    app.dom.rebuildPackVectorsBtn?.classList.add("hidden");
    app.state.latestManagePackVectorStatus = null;
    app.state.latestManagePackVectorStatusId = "";
  }

function renderManagePackVectorStatus(packId, status, state = "ready") {
    const normalizedPackId = String(packId || "").trim();
    if (!normalizedPackId || !packSupportsVectorStatus(normalizedPackId)) {
      hideManagePackVectorStatus();
      return;
    }

    app.dom.packVectorStatus?.classList.remove(
      "hidden",
      "vector-ready",
      "vector-rebuild",
      "vector-unconfigured",
      "vector-pending",
      "vector-loading",
    );
    app.dom.rebuildPackVectorsBtn?.classList.remove("hidden");

    if (state === "loading") {
      app.dom.packVectorStatus?.classList.add("vector-loading");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent = "正在读取向量维度…";
      }
      if (app.dom.rebuildPackVectorsBtn) {
        app.dom.rebuildPackVectorsBtn.disabled = true;
        app.dom.rebuildPackVectorsBtn.innerHTML =
          '<i class="fas fa-spinner fa-spin icon"></i>读取中';
      }
      return;
    }

    if (state === "error" || !status) {
      app.dom.packVectorStatus?.classList.add("vector-pending");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent = "向量状态读取失败";
      }
      if (app.dom.packVectorStatus) {
        app.dom.packVectorStatus.title = "暂时无法读取当前包的向量状态，请稍后重试。";
      }
      if (app.dom.rebuildPackVectorsBtn) {
        app.dom.rebuildPackVectorsBtn.disabled = true;
        app.dom.rebuildPackVectorsBtn.innerHTML =
          '<i class="fas fa-arrows-rotate icon"></i>状态不可用';
      }
      return;
    }

    const providerReady = Boolean(status.embedding_provider_ready);
    const captionComplete = Boolean(status.semantic_caption_complete);
    const indexReady = Boolean(status.index_ready);
    const rebuildRequired = Boolean(status.dimension_rebuild_required);
    const configuredDimension = Number(
      status.embedding_configured_dimension || 0,
    );
    const indexDimension = Number(status.index_embedding_dimension || 0);
    const taskStatus = String(status.task_status || "idle");
    const taskBusy = ["running", "paused"].includes(taskStatus);
    const currentDimensionLabel = configuredDimension
      ? `${configuredDimension} 维`
      : "维度待检测";
    const modelLabel = [
      String(status.embedding_provider_id || "").trim(),
      String(status.embedding_model || "").trim(),
    ]
      .filter(Boolean)
      .join(" / ");

    if (!providerReady) {
      app.dom.packVectorStatus?.classList.add("vector-unconfigured");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent = indexDimension
          ? `索引 ${indexDimension} 维，未配置向量模型`
          : "未配置向量模型";
      }
      if (app.dom.packVectorStatus) {
        app.dom.packVectorStatus.title =
          "当前没有可用的向量模型。已有语义描述不会丢失，配置模型后即可在此重建。";
      }
      if (app.dom.rebuildPackVectorsBtn) {
        app.dom.rebuildPackVectorsBtn.disabled = true;
        app.dom.rebuildPackVectorsBtn.title =
          "请先到设置中心的“语义与模型”配置向量模型。";
        app.dom.rebuildPackVectorsBtn.innerHTML =
          '<i class="fas fa-circle-exclamation icon"></i>先配置向量模型';
      }
      return;
    }

    if (taskBusy) {
      app.dom.packVectorStatus?.classList.add("vector-pending");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent =
          taskStatus === "paused"
            ? `当前 ${currentDimensionLabel}，任务已暂停`
            : `当前 ${currentDimensionLabel}，正在处理`;
      }
    } else if (!captionComplete) {
      app.dom.packVectorStatus?.classList.add("vector-pending");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent = `当前 ${currentDimensionLabel}，描述未完成`;
      }
    } else if (indexReady && !rebuildRequired) {
      app.dom.packVectorStatus?.classList.add("vector-ready");
      if (app.dom.packVectorStatusText) {
        app.dom.packVectorStatusText.textContent = `向量 ${
          indexDimension || configuredDimension || "未知"
        } 维`;
      }
    } else {
      app.dom.packVectorStatus?.classList.add("vector-rebuild");
      if (app.dom.packVectorStatusText) {
        const oldIndexHint =
          indexDimension && indexDimension !== configuredDimension
            ? `，原索引 ${indexDimension} 维`
            : "";
        app.dom.packVectorStatusText.textContent = `当前 ${currentDimensionLabel}${oldIndexHint}，待重建`;
      }
    }

    if (app.dom.packVectorStatus) {
      const indexHint = indexDimension
        ? `包内索引为 ${indexDimension} 维。`
        : "当前包还没有可用的本机向量索引。";
      app.dom.packVectorStatus.title =
        `${modelLabel ? `当前模型：${modelLabel}；` : ""}` +
        `模型维度：${currentDimensionLabel}；${indexHint}`;
    }
    if (app.dom.rebuildPackVectorsBtn) {
      app.dom.rebuildPackVectorsBtn.disabled = taskBusy || !captionComplete;
      app.dom.rebuildPackVectorsBtn.title = taskBusy
        ? "语义任务进行中，结束后才能重建向量。"
        : !captionComplete
          ? "请先完成当前包的全部语义描述。"
          : "使用当前向量模型重新建立本机向量索引。";
      app.dom.rebuildPackVectorsBtn.innerHTML = taskBusy
        ? '<i class="fas fa-spinner fa-spin icon"></i>正在处理'
        : !captionComplete
          ? '<i class="fas fa-clock icon"></i>等待语义描述'
          : `<i class="fas fa-arrows-rotate icon"></i>按${
              configuredDimension ? ` ${configuredDimension} 维` : "当前维度"
            }重建`;
    }
  }

async function refreshManagePackVectorStatus(packId = app.state.activeManagePackId) {
    const normalizedPackId = String(packId || "").trim();
    const requestId = ++app.state.managePackVectorStatusRequestId;
    if (!normalizedPackId || !packSupportsVectorStatus(normalizedPackId)) {
      hideManagePackVectorStatus();
      return null;
    }

    renderManagePackVectorStatus(normalizedPackId, null, "loading");
    try {
      const status = await app.api.apiGet("semantic/status", {
        pack_id: normalizedPackId,
      });
      if (requestId !== app.state.managePackVectorStatusRequestId) {
        return null;
      }
      app.state.latestManagePackVectorStatus = status;
      app.state.latestManagePackVectorStatusId = normalizedPackId;
      renderManagePackVectorStatus(normalizedPackId, status);
      return status;
    } catch (error) {
      if (requestId === app.state.managePackVectorStatusRequestId) {
        app.state.latestManagePackVectorStatus = null;
        app.state.latestManagePackVectorStatusId = "";
        renderManagePackVectorStatus(normalizedPackId, null, "error");
      }
      console.warn("读取当前图包向量状态失败:", error);
      return null;
    }
  }

async function performVectorRebuild(packId, knownStatus = null) {
    const normalizedPackId = String(packId || "").trim();
    if (!normalizedPackId || !packSupportsVectorStatus(normalizedPackId)) {
      app.dialogs.showToast("旧版表情包不支持向量重建。", "warning", "无法重建");
      return false;
    }
    const status =
      knownStatus ||
      (app.state.latestManagePackVectorStatusId === normalizedPackId
        ? app.state.latestManagePackVectorStatus
        : null) ||
      (await refreshManagePackVectorStatus(normalizedPackId));
    if (!status) {
      app.dialogs.showToast("暂时无法读取向量状态，请稍后重试。", "error", "无法重建");
      return false;
    }
    if (!status.embedding_provider_ready) {
      app.dialogs.showToast(
        "请先到设置中心的“语义与模型”配置向量模型。",
        "warning",
        "未配置向量模型",
      );
      return false;
    }
    if (!status.semantic_caption_complete) {
      app.dialogs.showToast("请先完成当前包的全部语义描述。", "warning", "暂不能重建");
      return false;
    }
    if (["running", "paused"].includes(String(status.task_status || ""))) {
      app.dialogs.showToast("当前语义任务尚未结束。", "warning", "暂不能重建");
      return false;
    }

    app.widgets.setButtonBusy(app.dom.rebuildPackVectorsBtn, "正在重建…");
    try {
      app.dialogs.showToast("正在按当前向量模型建立索引…", "info", "开始重建");
      const result = await app.api.apiPost("semantic/rebuild-index", {
        pack_id: normalizedPackId,
        force: true,
      });
      await app.packCatalog.refreshManagePackSummaries();
      app.dialogs.showToast(result?.message || "向量索引已建立。", "success", "重建完成");
      return true;
    } catch (error) {
      app.dialogs.showToast(error?.message || String(error), "error", "向量重建失败", 5000);
      return false;
    } finally {
      app.widgets.restoreButton(app.dom.rebuildPackVectorsBtn);
      await refreshManagePackVectorStatus(normalizedPackId);
    }
  }

async function confirmAndRebuildVector(
    packId,
    status,
    { importedShare = false, manual = false } = {},
  ) {
    const normalizedPackId = String(packId || "").trim();
    const pack = app.state.managePacksById.get(normalizedPackId);
    const packName = String(pack?.name || normalizedPackId);
    const modelLabel = [
      String(status?.embedding_model || "").trim(),
      Number(status?.embedding_configured_dimension || 0)
        ? `${Number(status.embedding_configured_dimension)} 维`
        : "",
    ]
      .filter(Boolean)
      .join("；");
    const alreadyReady = Boolean(status?.index_ready);
    const confirmed = await app.dialogs.showConfirm({
      title: importedShare
        ? "分享包需要重建向量"
        : alreadyReady && manual
          ? "重新建立当前包向量？"
          : "当前表情包需要重建向量",
      description:
        `「${packName}」${
          importedShare
            ? "来自无向量分享版，需要在本机补建向量。"
            : alreadyReady && manual
              ? "已有可用向量，重建后会替换当前本机索引。"
              : "尚未按当前向量模型建立可用的本机索引。"
        }` +
        `本次只会调用向量模型${modelLabel ? `（${modelLabel}）` : ""}，` +
        "不会重新调用视觉模型，也不会覆盖现有语义描述。是否继续？",
      confirmLabel: "立即重建向量",
    });
    if (!confirmed) {
      if (!manual) {
        app.dialogs.showToast(
          "语义描述已保留，可稍后点击首页的重建按钮。",
          "warning",
          "稍后重建",
        );
      }
      return false;
    }
    return performVectorRebuild(normalizedPackId, status);
  }

async function maybeOfferVectorRebuild(packId, guidance = null) {
    const normalizedPackId = String(packId || "").trim();
    if (!normalizedPackId || !packSupportsVectorStatus(normalizedPackId)) {
      return false;
    }
    const status =
      app.state.latestManagePackVectorStatusId === normalizedPackId
        ? app.state.latestManagePackVectorStatus
        : await refreshManagePackVectorStatus(normalizedPackId);
    if (!status) {
      return false;
    }
    const rebuildRequired = Boolean(
      status.semantic_enabled &&
      status.embedding_provider_ready &&
      status.semantic_caption_complete &&
      status.dimension_rebuild_required &&
      !["running", "paused"].includes(String(status.task_status || "")),
    );
    if (!rebuildRequired) {
      return false;
    }
    return confirmAndRebuildVector(normalizedPackId, status, {
      importedShare: String(guidance?.export_mode || "") === "share",
    });
  }
return { packSupportsVectorStatus, hideManagePackVectorStatus, renderManagePackVectorStatus, refreshManagePackVectorStatus, performVectorRebuild, confirmAndRebuildVector, maybeOfferVectorRebuild };
}
