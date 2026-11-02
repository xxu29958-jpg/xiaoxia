import { PreviewClient } from "../../shared/preview.js";
// metrics: one page runtime owns shared state and DOM references.
export function createMetrics(app) {
function formatDuration(value) {
    const total = Math.max(0, Math.floor(Number(value) || 0));
    const hours = String(Math.floor(total / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
    const seconds = String(total % 60).padStart(2, "0");
    return `${hours}:${minutes}:${seconds}`;
  }

function updateTaskTimer() {
    if (app.state.timerRunning) {
      app.state.elapsedSeconds += Math.max(0, (Date.now() - app.state.timerUpdatedAt) / 1000);
    }
    app.state.timerUpdatedAt = Date.now();
    app.dom.taskTimer.textContent = `用时 ${formatDuration(app.state.elapsedSeconds)}`;
  }

function renderMetricGroup(title, metrics, open = true) {
    const group = document.createElement("details");
    group.className = "metric-group";
    group.open = open;
    const summary = document.createElement("summary");
    summary.textContent = title;
    const content = document.createElement("div");
    content.className = "metric-group-content";
    group.append(summary, content);
    metrics.forEach(([label, value]) => {
      const metric = document.createElement("div");
      metric.className = "metric";
      const name = document.createElement("span");
      name.textContent = label;
      const valueNode = document.createElement("strong");
      valueNode.textContent = String(value ?? "-");
      metric.append(name, valueNode);
      content.append(metric);
    });
    app.dom.statusBox.append(group);
  }

function renderStatus(data) {
    app.state.latestStatus = data;
    app.state.embeddingReady = Boolean(data.embedding_provider_ready);
    app.state.visionReady = Boolean(data.vision_provider_ready);
    app.state.captionComplete = Boolean(data.semantic_caption_complete);
    app.inbox.renderAutoInbox(data.auto_collect_inbox);
    if (data.concurrency && !app.state.concurrencyDirty)
      app.dom.concurrencyInput.value = String(data.concurrency);
    app.state.elapsedSeconds = Number(data.elapsed_seconds || 0);
    app.state.timerRunning = data.task_status === "running";
    app.state.timerUpdatedAt = Date.now();
    const previousOpen = new Map(
      Array.from(app.dom.statusBox.querySelectorAll(".metric-group")).map((group) => [
        group.querySelector("summary")?.textContent,
        group.open,
      ]),
    );
    const groupOpen = (title, defaultOpen) =>
      previousOpen.has(title) ? previousOpen.get(title) : defaultOpen;
    const focusedGroup = app.dom.statusBox.contains(document.activeElement)
      ? document.activeElement
          .closest(".metric-group")
          ?.querySelector("summary")?.textContent
      : null;
    let taskText =
      {
        idle: "空闲",
        running: "运行中",
        paused: "已暂停",
        completed: "已完成",
        completed_with_errors: "完成但有失败",
        failed: "任务失败",
      }[data.task_status] || data.task_status;
    if (data.task_status === "running" && !data.worker_alive)
      taskText = "已中断（可继续）";
    if (data.task_status === "paused" && data.active_request_count)
      taskText = "正在中断请求";
    const phaseText =
      {
        captioning: "生成图片描述",
        indexing: "建立向量索引",
        finished: "已结束",
        failed: "异常结束",
      }[data.task_phase] || "尚未开始";
    const total = Math.max(0, Number(data.total_tasks || 0));
    const completed = Math.max(0, Number(data.caption_done || 0));
    app.dom.taskState.textContent = `${taskText || "等待开始"}（${phaseText}）`;
    app.dom.taskProgress.max = total || 1;
    app.dom.taskProgress.value = Math.min(completed, total);
    app.dom.taskProgressLabel.textContent = total
      ? `描述 ${completed} / ${total}`
      : "暂无待处理图片";
    app.dom.taskGuidance.textContent =
      data.status_message ||
      (data.task_status === "running"
        ? "正在处理图片，进度每 3 秒自动更新；需要时可暂停任务。"
        : data.can_resume
        ? "任务已暂停或中断，点击“继续队列”接着处理。"
        : data.can_retry
        ? "部分图片处理失败，可筛选失败记录，或点击“重试失败项”。"
        : "点击“一键完整语义化”开始。任务运行时每 3 秒自动更新。");
    app.dom.statusBox.replaceChildren();
    renderMetricGroup(
      "任务进度",
      [
        ["任务状态", taskText],
        ["当前阶段", phaseText],
        ["图片总数", data.total_tasks],
        ["模型请求中", data.active_request_count],
        ["排队待描述", data.queued_caption_tasks],
        ["描述完成", data.caption_done],
        ["自动重分类", data.reclassified_items || 0],
        ["处理失败", data.failed_tasks],
        ["并发上限", data.concurrency || 1],
      ],
      groupOpen("任务进度", false),
    );
    renderMetricGroup(
      "图片和描述",
      [
        ["文件", data.file_total],
        ["独立图片", data.unique_total],
        ["重复复用", data.reused_duplicate_files],
        ["描述完成", data.caption_done],
        ["描述失败", data.caption_failed],
      ],
      groupOpen("图片和描述", false),
    );
    renderMetricGroup(
      "向量处理",
      [
        ["向量完成", data.embedding_done],
        ["向量失败", data.embedding_failed],
        ["向量模型", data.embedding_provider_ready ? "已配置并可用" : "不可用"],
        [
          "实际向量模型",
          [data.embedding_provider_id, data.embedding_model]
            .filter(Boolean)
            .join(" / ") || "自动选择中",
        ],
        ["向量索引", data.index_ready ? "可用" : "未建立"],
        ["配置维度", data.embedding_configured_dimension || "未检测"],
        ["已校验维度", data.embedding_verified_dimension || "未校验"],
        ["索引维度", data.index_embedding_dimension || "未建立"],
      ],
      groupOpen("向量处理", false),
    );
    renderMetricGroup(
      "视觉模型和消耗",
      [
        ["视觉模型", data.vision_provider_ready ? "已配置并可用" : "不可用"],
        [
          "当前视觉模型",
          [data.vision_provider_id, data.vision_model]
            .filter(Boolean)
            .join(" / ") || "未选择",
        ],
        ["视觉调用次数", data.vision_calls],
        ["输入 Token", data.token_usage_input],
        ["输出 Token", data.token_usage_output],
        ["消耗 Token", data.token_usage_total],
      ],
      groupOpen("视觉模型和消耗", false),
    );
    renderMetricGroup(
      "其他状态",
      [
        [
          "任务队列",
          {
            external_operation: "其他文件任务运行中",
            cleared: "已清空",
            settling: "正在中断请求",
            paused: "已完全暂停",
            interrupted: "已中断，可继续",
            indexing: "描述完成，正在建索引",
            running: "正在处理",
            failed: "有失败项",
            waiting: "等待开始",
            done: "无待处理项",
            empty: "没有图片",
          }[data.queue_status] || "未知",
        ],
        ["待建立向量", data.queued_embedding_tasks],
        [
          "其他资源包任务",
          Array.isArray(data.other_active_tasks)
            ? data.other_active_tasks.length
            : 0,
        ],
        [
          "语义查询",
          data.semantic_enabled
            ? data.semantic_config_ready
              ? "已配置"
              : "未配置"
            : "未启用",
        ],
      ],
      groupOpen("其他状态", false),
    );
    app.dom.otherTasksWarning.textContent = String(data.other_tasks_warning || "");
    if (focusedGroup) {
      Array.from(app.dom.statusBox.querySelectorAll("summary"))
        .find((summary) => summary.textContent === focusedGroup)
        ?.focus({ preventScroll: true });
    }
    if (app.state.lastActionError) {
      app.status.showNotice(app.state.lastActionError, true);
    } else if (data.last_error) {
      app.status.showNotice(data.last_error, true);
    } else if (["running", "paused"].includes(String(data.task_status || ""))) {
      app.status.showNotice(data.status_message || "任务状态已更新");
    } else if (data.semantic_enabled && data.semantic_config_ready === false) {
      app.status.showNotice(
        "语义检索已开启，但向量模型尚未就绪；当前仍使用分类逻辑。请前往设置中心的“语义与模型”检查配置。",
        true,
      );
    } else if (!app.state.embeddingReady) {
      app.status.showNotice(
        data.embedding_configured_provider_id
          ? `已配置 Embedding 模型「${data.embedding_configured_provider_id}」，但当前 Provider 不可用；请检查 Provider 是否启用或配置是否已加载。`
          : "尚未选择向量模型，请前往设置中心的“语义与模型”完成配置。",
        true,
      );
    } else if (
      data.dimension_rebuild_required &&
      !["running", "paused"].includes(String(data.task_status || ""))
    ) {
      app.status.showNotice(
        `已检测到 Embedding 模型「${
          [data.embedding_provider_id, data.embedding_model]
            .filter(Boolean)
            .join(" / ") || "当前模型"
        }」，但当前资源包还没有按此模型建立本机索引；请展开“高级维护”，点击“按当前维度重建向量”。`,
        false,
      );
    } else if (!app.state.visionReady) {
      app.status.showNotice(
        "未配置视觉模型，请前往设置中心的“语义与模型”选择图片描述模型。",
        true,
      );
    } else {
      app.status.showNotice(data.status_message || "");
    }
    updateTaskTimer();
    app.controls.updateButtonState();
  }
return { formatDuration, updateTaskTimer, renderMetricGroup, renderStatus };
}
