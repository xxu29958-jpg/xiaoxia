import { openStorageGuide } from "../storage-guide.js";
// setup: one page runtime owns shared state and DOM references.
export function createSetup(app) {
async function initialize0() {
await window.AstrBotPluginPage.ready();
app.state.api = window.AstrBotPluginPage;
app.dom.form = document.getElementById("plugin-config-form");
app.dom.sections = document.getElementById("config-sections");
app.dom.search = document.getElementById("settings-search");
app.dom.save = document.getElementById("save-config-btn");
app.dom.reload = document.getElementById("reload-config-btn");
app.dom.resetPrompts = document.getElementById("reset-prompts-btn");
app.dom.status = document.getElementById("config-save-status");
app.dom.notice = document.getElementById("config-load-status");
app.dom.tabs = [...document.querySelectorAll("[data-settings-tab]")];
app.dom.panels = [...document.querySelectorAll("[data-settings-panel]")];
app.dom.modePanel = document.getElementById("selection-mode-panel");
app.dom.configDialog = document.getElementById("config-flow-dialog");
app.dom.configDialogBody = document.getElementById("config-flow-dialog-body");
app.dom.configDialogSave = document.getElementById("config-flow-dialog-save");
app.dom.configDialogReset = document.getElementById("config-flow-dialog-reset");
app.dom.configDialogStatus = document.getElementById(
    "config-flow-dialog-status",
  );
app.dom.rulesDialog = document.getElementById("rules-flow-dialog");
app.dom.rulesDialogBody = document.getElementById("rules-flow-dialog-body");
app.dom.rulesPanel = document.querySelector('[data-settings-panel="rules"]');
app.dom.modeInputs = [...app.dom.form.querySelectorAll('input[name="selection-mode"]')];
app.state.modePaths = ["semantic.enabled", "generation.emotion.llm.enabled"];
app.dom.workflow = document.getElementById("mode-workflow");
app.dom.workflowSteps = document.getElementById("mode-workflow-steps");
app.dom.collectionWorkflow = document.getElementById("collection-workflow");
app.state.collectionSteps = [
    [
      "来源与目标",
      "符合范围的聊天图片进入收集；目标包决定分类依据和收录位置。",
      "collect",
      [
        ["enabled", "收集开关"],
        ["scope", "来源范围"],
        ["target_pack_id", "目标表情包"],
      ],
    ],
    [
      "采样与间隔",
      "先限制每条消息的图片数，再抽样并检查来源冷却。抽样概率仅作用于平台表情；普通图片首次只记录，重复出现后逐步提高识别概率。",
      "collect-limits",
      [
        ["max_images_per_message", "单条图片上限"],
        ["sampling_probability", "平台表情抽样"],
        ["cooldown_seconds", "来源冷却"],
      ],
    ],
    [
      "视觉识别",
      "待审箱有容量才继续；有缓存时复用，否则检查每日额度并调用支持图片输入的模型。",
      "collect-recognition",
      [
        ["pending_limit", "待审核容量"],
        ["daily_recognition_limit", "每日识别上限"],
        ["vision_provider_id", "视觉模型"],
      ],
    ],
    [
      "质量与分类",
      "非表情或表情置信度不足时不收录；分类置信度决定自动入包时是否归入待分类。",
      "collect-quality",
      [
        ["min_meme_confidence", "表情置信度"],
        ["min_category_confidence", "分类置信度"],
      ],
    ],
    [
      "审核与入包",
      "按审核开关决定由你确认，还是自动接收入包。",
      "collect-review",
      [["manual_review", "人工审核"]],
    ],
  ];
for (const [title, description, group, settings] of app.state.collectionSteps) {
    const item = document.createElement("li");
    const node = document.createElement("div");
    node.className = "workflow-node collection-workflow-node";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const detail = document.createElement("span");
    detail.textContent = description;
    node.append(heading, detail);
    const links = document.createElement("div");
    links.className = "collection-workflow-links";
    for (const [path, label] of settings) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.dataset.workflowTarget = group;
      button.dataset.workflowField = `auto_collect.${path}`;
      const icon = document.createElement("i");
      icon.className = "fas fa-gear";
      icon.setAttribute("aria-hidden", "true");
      button.append(icon, document.createTextNode(` ${label}`));
      links.append(button);
    }
    node.append(links);
    item.append(node);
    document.getElementById("collection-workflow-steps").append(item);
  }
app.state.workflows = {
    "category-native": [
      ["选择表情包", "按使用规则为当前会话选择表情包"],
      ["决定是否配图", "按触发范围、概率和数量控制"],
      ["生成分类标签", "回复模型根据提示词和示例输出标签"],
      ["匹配并取图", "识别标签，匹配当前图包分类并随机取图"],
      ["发送表情", "按平台消息格式发送图片"],
    ],
    "category-auxiliary": [
      ["选择表情包", "按使用规则为当前会话选择表情包"],
      ["决定是否配图", "按触发范围、概率和数量控制"],
      ["辅助模型选分类", "根据回复选择分类标签"],
      ["匹配并取图", "识别标签，匹配当前图包分类并随机取图"],
      ["发送表情", "按平台消息格式发送图片"],
    ],
    "semantic-tool": [
      ["选择表情包", "按使用规则为当前会话选择表情包"],
      ["决定是否配图", "按触发范围、概率和数量控制"],
      ["发起搜索", "回复模型按需调用搜索工具"],
      ["检索并选图", "从索引中查找候选并选择图片"],
      ["发送表情", "按平台消息格式发送图片"],
    ],
    "semantic-auxiliary": [
      ["选择表情包", "按使用规则为当前会话选择表情包"],
      ["决定是否配图", "按触发范围、概率和数量控制"],
      ["辅助模型生成检索词", "根据回复提炼选图意图"],
      ["检索并选图", "从索引中查找候选并选择图片"],
      ["发送表情", "按平台消息格式发送图片"],
    ],
  };
app.state.workspaceModes = {
    "category-native": {
      primary: [
        "prompts",
        "category-example",
        "reply-example",
        "matching",
        "category-matching",
      ],
      fallback: [],
    },
    "category-auxiliary": {
      primary: ["emotion-model", "matching", "category-matching"],
      fallback: [],
    },
    "semantic-tool": {
      primary: ["semantic", "semantic-models"],
      fallback: [
        "prompts",
        "category-example",
        "reply-example",
        "matching",
        "category-matching",
      ],
    },
    "semantic-auxiliary": {
      primary: ["semantic", "semantic-models", "emotion-model"],
      fallback: ["matching", "category-matching"],
    },
  };
app.state.workspaceCommon = ["quantity-guidance", "appearance", "delivery"];
app.state.workspaceCollection = [
    "collect",
    "collect-limits",
    "collect-recognition",
    "collect-quality",
    "collect-review",
  ];
app.state.workspaceStorage = [
    "storage",
    "r2",
    "webdav",
    "stardots",
    "lsky",
    "downloads",
    "sync",
    "preview",
  ];
app.state.categories = {
    sending: ["表情发送", "控制表情何时出现，以及它在聊天中的呈现方式。"],
    models: ["模式", "选择模式，并从流程图打开相关设置。"],
    collect: ["自动收集", "从聊天中收集表情，用来源范围和频率控制收集节奏。"],
    storage: ["存储与下载", "连接图床、管理同步，并设置资源下载方式。"],
    prompts: [
      "提示词模板",
      "调整分类标签模式下，提供给回复模型的表情使用说明。",
    ],
    matching: [
      "标签与匹配",
      "设置表情标签的识别方式，让模型输出与表情分类对应。",
    ],
  };
}
return { initialize0 };
}
