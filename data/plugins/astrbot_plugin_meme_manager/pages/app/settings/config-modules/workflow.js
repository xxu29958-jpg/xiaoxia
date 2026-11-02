import { openStorageGuide } from "../storage-guide.js";
// workflow: one page runtime owns shared state and DOM references.
export function createWorkflow(app) {
async function initialize1() {
app.state.groups = [
    {
      id: "appearance",
      category: "sending",
      title: "出现时机",
      description: "少一点打扰，多一点恰好。",
      prefixes: [
        "generation.trigger.",
        "generation.emotion.probability",
        "generation.emotion.max_memes_per_message",
      ],
    },
    {
      id: "delivery",
      category: "sending",
      title: "发送方式",
      description: "根据聊天平台选择适合的图片和消息格式。",
      prefixes: ["generation.message."],
    },
    {
      id: "semantic",
      modes: ["semantic-tool", "semantic-auxiliary"],
      category: "models",
      title: "语义检索",
      description: "启用后，优先从已完成语义化的表情包中检索。",
      prefixes: ["semantic.enabled", "semantic.top_k", "semantic.min_score"],
    },
    {
      id: "semantic-models",
      modes: ["semantic-tool", "semantic-auxiliary"],
      category: "models",
      title: "语义化模型",
      description: "视觉模型生成图片描述，向量模型用于相似度检索。",
      prefixes: [
        "semantic.vision_provider_id",
        "semantic.embedding_provider_id",
      ],
    },
    {
      id: "emotion-model",
      modes: ["category-auxiliary", "semantic-auxiliary"],
      category: "models",
      title: "情感辅助",
      description: "可由单独的模型负责选图，默认复用当前对话的回复模型。",
      prefixes: ["generation.emotion.llm."],
    },
    {
      id: "collect",
      category: "collect",
      title: "来源与目标",
      description: "决定是否收集、从哪里收集，以及使用哪个表情包的分类。",
      prefixes: [
        "auto_collect.enabled",
        "auto_collect.scope",
        "auto_collect.target_pack_id",
      ],
    },
    {
      id: "collect-limits",
      category: "collect",
      title: "采样与间隔",
      description: "限制消息中的候选图片数、平台表情抽样及同一来源的识别间隔。",
      prefixes: [
        "auto_collect.max_images_per_message",
        "auto_collect.sampling_probability",
        "auto_collect.cooldown_seconds",
      ],
    },
    {
      id: "collect-recognition",
      category: "collect",
      title: "视觉识别",
      description:
        "待审箱满时停止提交候选；没有缓存时，按每日额度调用视觉模型。",
      prefixes: [
        "auto_collect.pending_limit",
        "auto_collect.daily_recognition_limit",
        "auto_collect.vision_provider_id",
      ],
    },
    {
      id: "collect-quality",
      category: "collect",
      title: "质量与分类",
      description:
        "表情置信度控制是否收录；分类置信度控制自动入包时的分类去向。",
      prefixes: [
        "auto_collect.min_meme_confidence",
        "auto_collect.min_category_confidence",
      ],
    },
    {
      id: "collect-review",
      category: "collect",
      title: "审核与入包",
      description:
        "开启后由你确认入包；关闭后仅新收集图片自动入包，已有待审核图片仍需处理。",
      prefixes: ["auto_collect.manual_review"],
    },
    {
      id: "storage",
      category: "storage",
      title: "图床连接",
      description:
        "先选择服务，再填写下方连接参数。切换服务会保留已填写的配置。",
      icon: "fa-cloud",
      prefixes: ["storage.provider"],
    },
    {
      id: "r2",
      category: "storage",
      title: "Cloudflare R2",
      prefixes: ["storage.providers.cloudflare_r2."],
      provider: "cloudflare_r2",
    },
    {
      id: "webdav",
      category: "storage",
      title: "WebDAV",
      prefixes: ["storage.providers.webdav."],
      provider: "webdav",
    },
    {
      id: "stardots",
      category: "storage",
      title: "StarDots",
      prefixes: ["storage.providers.stardots."],
      provider: "stardots",
    },
    {
      id: "lsky",
      category: "storage",
      title: "兰空图床（开源版 2.x）",
      prefixes: ["storage.providers.lsky."],
      provider: "lsky",
    },
    {
      id: "downloads",
      category: "storage",
      title: "资源下载",
      description: "用于从 GitHub 安装社区表情包。",
      icon: "fa-download",
      prefixes: ["community."],
    },
    {
      id: "sync",
      category: "storage",
      title: "同步状态",
      description: "控制管理页同步状态的缓存时长。",
      icon: "fa-arrows-rotate",
      prefixes: ["sync."],
    },
    {
      id: "preview",
      category: "storage",
      title: "页面预览",
      description: "控制浏览图片时的加载并发。",
      icon: "fa-images",
      prefixes: ["webui."],
    },
    {
      id: "prompts",
      modes: ["category-native"],
      category: "prompts",
      title: "分类提示词",
      description:
        "前缀定义能力与发送格式，后缀规定内部资料边界和图片理解；中间自动插入分类列表。",
      prefixes: ["generation.prompt.head", "generation.prompt.tail"],
    },
    {
      id: "quantity-guidance",
      category: "prompts",
      title: "表情回复数量",
      description:
        "控制何时配图和单条回复选多少张，适用于分类、语义和情感辅助选图，与发送阶段的硬上限独立。",
      prefixes: [
        "generation.prompt.quantity_guidance_enabled",
        "generation.prompt.quantity_guidance",
      ],
    },
    {
      id: "category-example",
      modes: ["category-native"],
      category: "prompts",
      title: "分类使用示例",
      description:
        "用一组虚构分类演示如何选取分类键并生成标记。实际回复仍使用当前表情包的分类。",
      prefixes: [
        "generation.prompt.category_example_enabled",
        "generation.prompt.category_example",
      ],
    },
    {
      id: "reply-example",
      modes: ["category-native"],
      category: "prompts",
      title: "回复形式示例",
      description:
        "演示图文结合、纯文字和纯表情三种形式。可单独开启，也可与分类使用示例一起使用。",
      prefixes: [
        "generation.prompt.reply_example_enabled",
        "generation.prompt.reply_example",
      ],
    },
    {
      id: "matching",
      category: "matching",
      title: "标签识别",
      description: "识别回复中的表情标记，并清理无法匹配的标签。",
      prefixes: ["generation.markup."],
    },
    {
      id: "category-matching",
      modes: ["category-native", "category-auxiliary"],
      category: "matching",
      title: "分类匹配",
      description: "自动使用当前会话所选表情包的分类，无需维护额外词表。",
      prefixes: ["generation.matching."],
    },
  ];
app.state.labels = {
    "generation.trigger.scope": "应用范围",
    "generation.emotion.probability": "表情出现概率（%）",
    "generation.emotion.llm.provider_id": "情感辅助模型",
    "semantic.top_k": "每次检索候选数",
    "semantic.min_score": "最低相似度",
    "semantic.vision_provider_id": "图片描述模型",
    "semantic.embedding_provider_id": "向量模型",
    "auto_collect.vision_provider_id": "收集识别模型",
    "auto_collect.target_pack_id": "收集到的表情包",
    "generation.message.mixed_probability": "图文同条回复概率（%）",
  };
app.state.hints = {
    "generation.emotion.probability":
      "在语义检索模式下，每轮按此概率决定是否让回复模型选图。",
    "generation.emotion.llm.enabled":
      "将选图交给情感辅助模型，回复模型只负责对话。",
    "generation.emotion.llm.provider_id":
      "留空时复用当前回复模型，也可以单独指定。",
    "generation.trigger.scope":
      "选择为哪些模型回复附加表情。指令和固定文本回复不受影响。",
    "semantic.enabled":
      "由回复模型检索表情；启用情感辅助后，改由辅助模型选图。",
    "semantic.embedding_provider_id":
      "留空时自动选择可用向量模型。更换模型后，可到语义化页面检查并重建索引。",
    "auto_collect.target_pack_id":
      "留空时跟随人设或会话规则；指定后使用该包的分类进行识别，收集的图片也归属该包。",
  };
app.state.optionLabels = {
    cloudflare_r2: "Cloudflare R2",
    stardots: "StarDots",
    webdav: "WebDAV",
    lsky: "兰空图床（开源版 2.x）",
    only_chat_llm: "仅普通聊天回复",
    chat_and_plugin_llm: "普通聊天与插件触发的回复",
  };
app.state.fields = new Map();
app.state.controls = new Map();
app.state.changes = new Map();
app.state.revision = "";
app.state.active = "models";
app.state.providers = { chat: [], embedding: [] };
app.state.packs = [];
app.state.loaded = false;
app.state.semanticStatus = null;
app.state.applicationFailed = false;
app.state.dialogOrigin = null;
app.state.dialogCard = null;
app.state.dialogPlaceholder = null;
app.state.rulesPlaceholder = null;
}
return { initialize1 };
}
