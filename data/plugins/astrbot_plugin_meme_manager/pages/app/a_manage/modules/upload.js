
// upload: one page runtime owns shared state and DOM references.
export function createUpload(app) {
function normalizeUploadFiles(fileList) {
    const validFiles = [];
    let invalidCount = 0;

    Array.from(fileList || []).forEach((file) => {
      const isImageFile =
        file instanceof File &&
        (file.type.startsWith("image/") ||
          /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(file.name));

      if (isImageFile) {
        validFiles.push(file);
        return;
      }
      invalidCount += 1;
    });

    return { validFiles, invalidCount };
  }

function dedupeUploadFiles(files) {
    const uniqueFiles = [];
    const seenSignatures = new Set();
    let duplicateCount = 0;

    files.forEach((file) => {
      const signature = [
        file.name,
        file.size,
        file.lastModified,
        file.type,
      ].join("::");

      if (seenSignatures.has(signature)) {
        duplicateCount += 1;
        return;
      }

      seenSignatures.add(signature);
      uniqueFiles.push(file);
    });

    return { uniqueFiles, duplicateCount };
  }

function refreshUploadDropzones(category = null) {
    document.querySelectorAll(".emoji-upload").forEach((uploadBlock) => {
      if (category && uploadBlock.dataset.category !== category) {
        return;
      }

      const uploadTitle = uploadBlock.querySelector(".emoji-upload-title");
      const uploadHint = uploadBlock.querySelector(".emoji-upload-hint");
      const uploadMeta = uploadBlock.querySelector(".emoji-upload-meta");
      const uploadProgress = uploadBlock.querySelector(
        ".emoji-upload-progress",
      );
      const uploadProgressBar = uploadBlock.querySelector(
        ".emoji-upload-progress-bar",
      );
      const uploadIconInner = uploadBlock.querySelector(".emoji-upload-icon i");

      if (
        !uploadTitle ||
        !uploadHint ||
        !uploadMeta ||
        !uploadProgress ||
        !uploadProgressBar ||
        !uploadIconInner
      ) {
        return;
      }

      const state = app.state.uploadStateByCategory.get(uploadBlock.dataset.category);

      if (!state) {
        uploadBlock.classList.remove("uploading");
        uploadBlock.setAttribute("aria-busy", "false");
        uploadTitle.textContent = "上传表情包";
        uploadHint.textContent = "点击上传图片；鼠标拖动可移动，触屏长按可多选";
        uploadMeta.textContent = "";
        uploadMeta.classList.add("hidden");
        uploadProgress.classList.add("hidden");
        uploadProgressBar.style.width = "0%";
        uploadIconInner.className = "fas fa-cloud-arrow-up";
        return;
      }

      const processedCount = state.completed + state.failed + state.duplicates;
      const currentIndex = Math.min(processedCount + 1, state.total);
      const progressPercent =
        state.total > 0 ? Math.round((processedCount / state.total) * 100) : 0;

      uploadBlock.classList.add("uploading");
      uploadBlock.setAttribute("aria-busy", "true");
      uploadIconInner.className = "fas fa-spinner fa-spin";
      uploadMeta.classList.remove("hidden");
      uploadProgress.classList.remove("hidden");
      uploadProgressBar.style.width = `${progressPercent}%`;

      if (state.refreshing) {
        uploadTitle.textContent = "正在刷新列表";
        uploadHint.textContent = `已处理 ${state.total} 个文件，正在更新界面`;
      } else {
        uploadTitle.textContent = `正在上传 ${currentIndex}/${state.total}`;
        uploadHint.textContent = state.currentFileName
          ? `当前文件：${state.currentFileName}`
          : "正在准备上传文件";
      }

      const metaParts = [`已完成 ${processedCount}/${state.total}`];
      if (state.duplicates > 0) {
        metaParts.push(`重复 ${state.duplicates}`);
      }
      if (state.failed > 0) {
        metaParts.push(`失败 ${state.failed}`);
      }
      uploadMeta.textContent = metaParts.join("，");
    });
  }

function isCategoryUploading(category) {
    return app.state.uploadStateByCategory.has(category);
  }

async function uploadFilesToCategory(category, fileList) {
    const { validFiles, invalidCount } = normalizeUploadFiles(fileList);

    if (invalidCount > 0) {
      app.dialogs.showToast(
        `已忽略 ${invalidCount} 个非图片文件。`,
        "warning",
        "文件类型不支持",
      );
    }

    if (validFiles.length === 0) {
      return;
    }

    const { uniqueFiles, duplicateCount } = dedupeUploadFiles(validFiles);

    if (duplicateCount > 0) {
      app.dialogs.showToast(
        `已忽略本批次中 ${duplicateCount} 个重复文件。`,
        "info",
        "已自动去重",
      );
    }

    if (uniqueFiles.length === 0) {
      return;
    }

    if (isCategoryUploading(category)) {
      app.dialogs.showToast(
        `分类 ${category} 正在上传文件，请等待当前批次完成。`,
        "info",
        "上传进行中",
      );
      return;
    }

    const uploadState = {
      total: uniqueFiles.length,
      completed: 0,
      failed: 0,
      duplicates: 0,
      currentFileName: uniqueFiles[0]?.name || "",
      refreshing: false,
    };
    app.state.uploadStateByCategory.set(category, uploadState);
    refreshUploadDropzones(category);

    app.dialogs.showToast(
      uniqueFiles.length > 1
        ? `开始向 ${category} 上传 ${uniqueFiles.length} 个文件。`
        : `开始向 ${category} 上传 1 个文件。`,
      "info",
      "上传开始",
      2200,
    );

    const failedUploads = [];
    const duplicateUploads = [];

    for (const file of uniqueFiles) {
      uploadState.currentFileName = file.name;
      refreshUploadDropzones(category);

      try {
        await app.mutations.uploadEmoji(category, file);
        uploadState.completed += 1;
      } catch (error) {
        if (error.code === "duplicate_emoji" || error.status === 409) {
          uploadState.duplicates += 1;
          duplicateUploads.push({ fileName: file.name, error });
        } else {
          uploadState.failed += 1;
          failedUploads.push({ fileName: file.name, error });
        }
      }

      refreshUploadDropzones(category);
    }

    if (uploadState.completed > 0) {
      uploadState.refreshing = true;
      uploadState.currentFileName = "";
      refreshUploadDropzones(category);
      await app.api.refreshUi({ emojis: true });
    }

    app.state.uploadStateByCategory.delete(category);
    refreshUploadDropzones(category);

    if (uploadState.failed === 0 && uploadState.duplicates === 0) {
      app.dialogs.showToast(
        uploadState.completed > 1
          ? `已向 ${category} 上传 ${uploadState.completed} 个文件。`
          : `已向 ${category} 上传 1 个文件。`,
        "success",
        "上传成功",
      );
      return;
    }

    if (uploadState.completed > 0 && uploadState.failed === 0) {
      app.dialogs.showToast(
        `上传完成，新增 ${uploadState.completed} 个，跳过重复 ${uploadState.duplicates} 个。`,
        "warning",
        "上传已去重",
        4500,
      );
      return;
    }

    if (
      uploadState.completed === 0 &&
      uploadState.duplicates > 0 &&
      uploadState.failed === 0
    ) {
      const firstDuplicateMessage =
        duplicateUploads[0]?.error?.message || "这些文件已存在于当前分类";
      app.dialogs.showToast(
        `未新增文件，已跳过 ${uploadState.duplicates} 个重复项：${firstDuplicateMessage}`,
        "info",
        "无需重复上传",
        4500,
      );
      return;
    }

    if (uploadState.completed > 0) {
      app.dialogs.showToast(
        `上传完成，成功 ${uploadState.completed} 个，重复 ${uploadState.duplicates} 个，失败 ${uploadState.failed} 个。`,
        "warning",
        "部分上传失败",
        4500,
      );
      return;
    }

    const firstErrorMessage =
      failedUploads[0]?.error?.message || "服务器返回错误";
    app.dialogs.showToast(
      `本次上传全部失败：${firstErrorMessage}`,
      "error",
      "上传失败",
      4500,
    );
  }

function createUploadDropzone(category) {
    const uploadBlock = document.createElement("div");
    uploadBlock.className = "emoji-upload";
    uploadBlock.dataset.category = category;
    uploadBlock.tabIndex = 0;
    uploadBlock.setAttribute("role", "button");
    uploadBlock.setAttribute(
      "aria-label",
      `上传 ${category} 分类表情包，支持点击选择或拖拽图片`,
    );

    const uploadIcon = document.createElement("div");
    uploadIcon.className = "emoji-upload-icon";
    const uploadIconInner = document.createElement("i");
    uploadIconInner.className = "fas fa-cloud-arrow-up";
    uploadIcon.appendChild(uploadIconInner);

    const uploadTitle = document.createElement("div");
    uploadTitle.className = "emoji-upload-title";
    uploadTitle.textContent = "上传表情包";

    const uploadHint = document.createElement("div");
    uploadHint.className = "emoji-upload-hint";
    uploadHint.textContent = "点击上传图片；鼠标拖动可移动，触屏长按可多选";

    const uploadMeta = document.createElement("div");
    uploadMeta.className = "emoji-upload-meta hidden";

    const uploadProgress = document.createElement("div");
    uploadProgress.className = "emoji-upload-progress hidden";
    const uploadProgressBar = document.createElement("span");
    uploadProgressBar.className = "emoji-upload-progress-bar";
    uploadProgress.appendChild(uploadProgressBar);

    uploadBlock.appendChild(uploadIcon);
    uploadBlock.appendChild(uploadTitle);
    uploadBlock.appendChild(uploadHint);
    uploadBlock.appendChild(uploadMeta);
    uploadBlock.appendChild(uploadProgress);

    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.style.display = "none";
    fileInput.accept = "image/*";
    fileInput.multiple = true;

    let dragDepth = 0;

    const setDragState = (active) => {
      uploadBlock.classList.toggle("drag-active", active);
    };

    uploadBlock.addEventListener("click", () => {
      if (isCategoryUploading(category)) {
        app.dialogs.showToast(
          `分类 ${category} 正在上传文件，请稍候。`,
          "info",
          "上传进行中",
        );
        return;
      }
      fileInput.click();
    });

    uploadBlock.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (isCategoryUploading(category)) {
          app.dialogs.showToast(
            `分类 ${category} 正在上传文件，请稍候。`,
            "info",
            "上传进行中",
          );
          return;
        }
        fileInput.click();
      }
    });

    fileInput.addEventListener("change", (event) => {
      void uploadFilesToCategory(category, event.target.files);
      fileInput.value = "";
    });

    uploadBlock.addEventListener("dragenter", (event) => {
      if (app.drag.isInternalEmojiDrag(event)) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      dragDepth += 1;
      setDragState(true);
    });

    uploadBlock.addEventListener("dragover", (event) => {
      if (app.drag.isInternalEmojiDrag(event)) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "copy";
      }
      setDragState(true);
    });

    uploadBlock.addEventListener("dragleave", (event) => {
      if (app.drag.isInternalEmojiDrag(event)) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) {
        setDragState(false);
      }
    });

    uploadBlock.addEventListener("drop", (event) => {
      if (app.drag.isInternalEmojiDrag(event)) {
        event.preventDefault();
        dragDepth = 0;
        setDragState(false);
        return;
      }
      event.preventDefault();
      dragDepth = 0;
      setDragState(false);
      if (isCategoryUploading(category)) {
        app.dialogs.showToast(
          `分类 ${category} 正在上传文件，请等待当前批次完成。`,
          "info",
          "上传进行中",
        );
        return;
      }
      void uploadFilesToCategory(category, event.dataTransfer?.files);
    });

    refreshUploadDropzones(category);

    return { uploadBlock, fileInput };
  }
return { normalizeUploadFiles, dedupeUploadFiles, refreshUploadDropzones, isCategoryUploading, uploadFilesToCategory, createUploadDropzone };
}
