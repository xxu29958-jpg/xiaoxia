# 小夏全项目理论依据与 Codex 实现路线

> v1.1 参考版：本稿保留原研究的机制、数据、阅读范围和候选实验。项目交付是不变量；算法、模型、参数、模块、训练与实验安排均可调整、组合、替换或放弃。旧代码接入点和“第一版”安排是候选建议，不构成施工边界。以项目合同 v1.1 和 README_CODEX_REFERENCE 为准。

研究日期：2026 年 11 月 3 日。对象：[xxu29958-jpg/xiaoxia](https://github.com/xxu29958-jpg/xiaoxia)，代码落点固定于 [406e86e878fbe0a6c78649071cb469c11280e5a2](https://github.com/xxu29958-jpg/xiaoxia/tree/406e86e878fbe0a6c78649071cb469c11280e5a2)。

小夏的核心目标应是一个有连续经历、可累积理解与兴趣、能自然表达和共同做事的关系型智能体。声音、记忆、主动性、阅读与桌面能力需要服务于同一段持续相处，不能各自只交付一个可运行的演示。

依据仓库 [GOAL.md](https://github.com/xxu29958-jpg/xiaoxia/blob/406e86e878fbe0a6c78649071cb469c11280e5a2/GOAL.md)，保留 QQ 与微信，后续桌宠复用同一套服务；保留已认可的四秒参考声线，允许自然、轻度的情绪；完整歌曲、戏曲和戏剧唱段不能被缩减成短哼唱。Owner 已定的近期声音优先级不因这份研究被改写。

本研究纳入 53 篇独立原始文献，逐项列出所读正文范围、机制、实验及转用边界。基础理论、短期实验、离线基准、长期真人研究和新预印本的证据性质不同。下文“项目设计与实验”是从论文和代码静态证据推导的方案，不是已经在小夏复现的效果；本轮未改仓库或运行模型实验。

# 全项目方向

## 把连续相处作为系统目标

可观察的目标不是“她说自己像人”，而是隔天仍能承接之前的事、知道哪些信息改了、表现出有依据且可修正的观点，能把读到的新东西自然带回对话，并在共同办事时可信。

关系型智能体的早期月度研究和近年的陪伴研究表明，关系表达与任务能力值得分别评价；长期模板重复仍会破坏体验。即时感受改善与多周总体效果也不能混为一谈。产品不必以临床功效为目标，也不应把聊天时长当唯一成功指标。[Bickmore 与 Picard](https://dam-prod.media.mit.edu/x/files/pdfs/04.bickmore-picard-tochi.pdf)、[AI Companions Reduce Loneliness](https://www.hbs.edu/ris/Publication%20Files/AI%20Companions%20Reduce%20Loneliness%2011.7.2025_57451c02-8047-4e0d-abfc-55841f64166d.pdf)、[四周随机研究 v2](https://arxiv.org/html/2503.17473v2)。

## 用持续状态连接各项能力

CoALA 提供组织思路：当前工作状态、过去经历、知识和执行程序各有归属。小夏可沿用现有共享核心和持久化，只建立清晰的数据视图，不必重写 AstrBot 或引入多 agent 平台。[CoALA](https://arxiv.org/pdf/2309.02427)。

| 需要区分的内容 | 作用 | 更新原则 |
| --- | --- | --- |
| 稳定身份与表达取向 | 保持同一个小夏，有自己的偏好和说话特点 | 小而明确，不被单次赞许或网页内容改写 |
| 真实共同经历 | 跨天、跨 QQ 和微信承接发生过的事 | 来源可回查，不能补编 |
| 世界知识与阅读笔记 | 为自己的观点和新话题提供内容 | 原文事实与自己的联想分开 |
| 当前会话状态 | 当前想聊什么、尚未说完什么、是否在办事 | 随新消息变化，临时状态不过度永久化 |
| 解释和暂时判断 | 对关系、情绪、兴趣的可修正理解 | 标记依据和时效，不升级成用户明确说过的事实 |
| 技能与执行程序 | 可靠完成授权范围内的动作 | 经验先成为离线候选，经验证再更新 |

这些是逻辑边界，不要求各自新建服务。一个主回复调用可以读取有预算的上下文投影，完成连贯表达；读取文档和执行长任务才使用必要的工具循环。

## 让记忆改善新对话而不只回答回忆题

长期记忆的核心问题包括保真、更新、时间和利用。LongMemEval 支持把事实提取用于增强索引，同时保留原始记录；LoCoMo-Plus 则揭示“能回答过去发生什么”和“新问题里自然照顾过去的目标”之间的差距。[LongMemEval](https://arxiv.org/html/2410.10813v2)、[LoCoMo-Plus](https://aclanthology.org/2026.acl-long.1150.pdf)。

因此原文、抽取事实、推断、阅读所得和用户更正不能全压成一段滚动总结。更正不仅修改一条向量记录，还应让依赖旧事实的摘要或解释失效。旧情绪可以过期，稳定事实可以被更正；这与用户要求删除是不同操作。暂不需要为此迁移到复杂图数据库。

## 保持人格取向而让反应随事件变化

RoleLLM、EmoCharacter 和 BlendedSkillTalk 支持分别处理表达示例、情境与知识，同时观察它们如何融合。人格稳定不等于永远兴奋、永远温柔赞同或每句都重复口头禅。[RoleLLM](https://arxiv.org/html/2310.00746v2)、[EmoCharacter](https://aclanthology.org/2025.naacl-long.316.pdf)、[BlendedSkillTalk](https://aclanthology.org/2020.acl-main.183.pdf)。

可优先试验精简稳定人设、相关真实经历和一至三个完整对话示例。示例数量只是候选参数，不是论文确立的最佳值。有主见应表现为理由和取向可解释、面对新证据能改正，而不是机械反驳。温暖与附和分开评价，不用删除亲切感来解决迎合。

## 让阅读产生可积累的内容和观点

实际阅读应形成“材料支持了什么”“小夏怎么看”“与什么旧话题有关”“还想继续读什么”的不同记录。书签或搜索标题只能说明发现了材料，不能说明已经阅读。阅读范围应有实际获取的正文或字幕支持。[WebGPT](https://arxiv.org/pdf/2112.09332)、[Generative Agents](https://arxiv.org/html/2304.03442v2)。

兴趣不必完全镜像用户。可以保留小夏设定的兴趣，再由真实阅读扩展；共享兴趣决定哪些内容适合一起聊。为了维持自己的内容积累，可以有不立即发送给用户的阅读与笔记。探索预算、候选选择和兴趣更新应作为实验参数，不能把“模型随口写了一篇感想”算成自主学习已经发生。

## 把主动性做成选择而不是消息产量

主动性包括选内容、选时机、允许不说、在新情况出现时撤回。材料有趣不等于此刻适合发；用户没有回复也不等于讨厌。轻松问候可以没有任务价值，不需要每条消息都是建议。[Mixed Initiative](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/11/chi99horvitz.pdf)、[PETD](https://aclanthology.org/2024.findings-acl.429.pdf)、[个性化主动助手](https://arxiv.org/html/2602.04000)。

当前 main 已存在记忆注入、线程／素材候选、send=false 和发送前活动复核。研究增量是让素材选择更懂当前话题和来源状态，不能以读论文为名另建第二套调度器。

## 让听说唱成为同一个角色的不同表达

听觉线索、回复内容、音色和表演分别处理。SenseVoice 的情绪和声音事件可以作为弱证据；固定音色允许不同韵律，不能通过随机变声模拟个性。轻度表达应在同文案、同参考声源下比较，保留 Owner 已认可的底色。[FunAudioLLM](https://arxiv.org/html/2407.04051v1)、[IndexTTS2](https://arxiv.org/html/2506.21619v2)、[CoCoEmo](https://arxiv.org/html/2602.03420v1)。

当前 QQ 文件语音与未来桌面连续语音是两个阶段。接话时机、回声、打断和过期音频属于后者。原曲转换、给歌词乐谱演唱、创作完整歌曲也应分开，不能由一种路径的短段成功替代所有目标。[Seed-VC](https://arxiv.org/html/2411.09943v1)、[SoulX-Singer](https://arxiv.org/html/2602.07803v1)、[Moshi](https://arxiv.org/html/2410.00037v2)。

## 让桌面行动和成长依赖真实结果

“看懂当前屏幕”“决定目标”“获准行动”“确认做成”是不同问题。优先使用可靠的应用接口／UIA，必要时视觉定位；执行后读回真实状态，不能以模型说完成为准。[Windows Agent Arena](https://arxiv.org/html/2409.08264v1)、[OSWorld](https://arxiv.org/html/2404.07972v2)。

成长先体现为记忆更准确、观点有内容、动作更可靠。任务失败可以提出新工具候选，由 Codex 离线实现和验证；不要把上线系统自行改权限等同于自主性。Agent S4 提供的是这一研究方向，不是无限自我改进保证。[Evolving Agent Interfaces](https://arxiv.org/html/2610.28601v1)。

# Codex 实现与实验路线

所有条目先检查已有实现，只补证据支持且反例确实需要的增量。近期声音任务继续推进，系统性实验可并行进行；不要求先完成所有架构工作才能继续聊天。

## 先固定基线和证据

在现有 `maintenance/tests` 与运行记录中复用测试设施，固定模型、提示版本、代码 SHA、参考音频、测试输入及配置。分别保存当前默认、候选输出和失败实例，不用挑选过的成功样本充当整体验收。

建立三种互补证据：

1. 确定性状态测试：假时钟、可控工具返回、隔离存储和发送适配器。

2. 真实模型旅程：固定输入和环境，重复运行，记录副作用及用户可见耗时。

3. Owner 连续使用观察：是否接得住、像同一个小夏、有内容而不啰嗦，哪些表达几天后变腻。

可以从十二条关键旅程、每条五次开始筛查稳定性，再扩展；这些数量是可执行起点，不是统计充分性保证。单人七至十四天观察是个人验收与找缺陷，不是随机试验或普遍效果证明。[τ-bench](https://arxiv.org/html/2406.12045v1)、[LifelongAgentBench](https://arxiv.org/pdf/2505.11942)。

## 每项任务的代码落点与验收

以下路径均相对仓库根。伴侣插件目录为 `data/plugins/astrbot_plugin_ada_companion/`。

| 任务 | 现有落点 | 最小增量 | 应能否证的结果 |
| --- | --- | --- | --- |
| 声音底色与局部表达 | `response_media.py`、`local_voice.py`、`services/voice/server_cosy.py`、`server_index.py` | 在同一 canonical spoken text 上比较轻度表达；候选与默认隔离，保留同一参考声源 | 相似度更高却更僵、尾字丢失或更像配音时不晋升；完整手机接收播放才算链路通过 |
| 记忆证据与更新 | `owner_memory.py`、`shared_core.py`、`shared_history.py`、`memory_tools.py` 及现有 memory companion 插件 | 明确来源类型、原文引用、修订和派生依赖；摘要增强索引 | 用户纠正后旧摘要又恢复错误、朋友经历归到主人、换平台串用户，均失败 |
| 稳定人格与自然回应 | `persona.md`、`persona_rules.py`、`prompting.py`、`dialogue.py`、`chat_replyer.py` | 区分稳定取向、临时会话状态和少量真实表达示例；保留一个连贯主回复过程 | 用户立场一变事实就翻转、语气永远同一档、示例台词机械复用，不算改善 |
| 阅读与内容积累 | `browser_document.py`、`bili_reader.py`、`web_notebook.py`、`interest_discovery.py` | 保存实际阅读范围、支持片段、自己的评论、相关旧话题、下个阅读问题 | 只有收藏却声称读过、把文章故事说成亲身经历、引用与结论不相符，均失败 |
| 主动交流 | `initiative_materials.py`、`initiative.py`、`thread_review.py` | 在既有候选中加入阅读状态及当前语境／兴趣双重选择；复用发送前重检 | 无关新文章挤掉真正相关旧材料、用户新意图出现后仍发送旧候选、忽略被学成厌恶，均失败 |
| 听觉理解 | 现有 SenseVoice 接入与消息入口 | 保留文字、语言、声音事件和情绪提示的来源／版本；文本与提示可分别消融 | 情绪标签误导回复、笑声被读成正文、无可信概率却制造 confidence，均需修正 |
| 完整演唱 | `server_singing.py`、`cover_pipeline.py`、`resident_pipeline.py`、`seed_runtime.py`、`singing_score.py` | 显式区分 cover 与 score 路径，整首保存、发送和听审 | 间奏凭空出声、歌词漏唱、时间轴漂移、段间身份变声、尾奏截断，不能用短段成绩抵消 |
| 桌面共同做事 | `desktop_guard.py`、`desktop_store.py`、`desktop_tools.py`、`desktop_windows.py` | 观测、提案、授权、执行回执及独立验证分别有合同 | 执行按钮成功却任务未完成、错窗口、重复副作用、网页内容变授权，均失败 |
| 桌面连续语音 | 未来桌宠入口及现有独立语音服务 | 再引入轮次预测、回声控制、生成／播放取消及 epoch | 用户打断后旧声音继续播放、自身声音被当新输入，双工体验未完成 |
| 可检验的成长 | 既有反馈、笔记、日志和维护测试 | 分开处理事实更正、表达偏好、时机反馈、工具失败；候选改进离线验证 | 一次点赞改写全局人设、单次失败无限加规则、训练集好看但留出场景退化，不应晋升 |

## 记忆最小合同

不要求建立新数据库。先把现有记录需要的语义补齐，并确保所有消费者使用同一有效视图：

```text
ExperienceRecord
  id, canonical_owner_id
  source_kind, source_ids, channel
  recorded_at, event_time_or_range
  content
  derived_from_ids
  revision, status
```

`source_kind` 至少区分用户原话、抽取事实、推断、真实阅读、模型生成和用户更正。用户原话只证明用户说过，并不由存储系统认证为客观事实。抽取事实不获得比原文更高的权威。缺少可校准置信度时不伪造数值。

原始证据保存、更正传播和撤销是项目合同；Generative Agents 的证据指针、A-Mem 的原始／生成字段、LongMemEval 的更新任务提供方向，但都没有替项目证明这些合同完整成立。

检索可从“原始片段＋提取事实作为索引键”起步，召回后交付原文和修订状态；对日期、归属和明确更正使用结构化过滤。一次 top-k 不足时允许有限补查；仍缺证据时不要编造回忆。用户要求删除时，原文、向量、摘要和派生内容应按同一删除合同处理，不能只降权。

## 阅读材料与主动候选最小合同

```text
ReadingNote
  source_url, source_title, fetched_at
  read_scope, evidence_refs
  supported_findings
  personal_comment
  related_episode_ids, open_questions
  revision, status

InitiativeCandidate
  material_or_thread_refs
  selected_content
  decision: send | skip
  concise_context_reason
  owner_and_session_revision
```

`ReadingNote` 可映射到现有 notebook 字段，不应成为平行知识库。证据范围可以是页面部分段落、字幕片段等，不能把只获取局部写成全书读完。个人评论允许不同见解，不需要伪装成源文结论；观点改变可保留原因。

现有候选已经支持不发送。只有反例证明有需要时才增加 defer 状态，而不是预先扩建调度系统。候选生成后，用户活动、会话、线程或材料变化必须沿既有边界重新验证；内部简短依据不应作为冗长思维链发给用户。

可做三个消融：

- A：当前按近期材料取候选。

- B：加入当前会话和相关兴趣的材料选择。

- C：B 加实际阅读状态、已分享语义去重和明确反馈。

比较误打扰、欢迎机会遗漏、上下文贴合、依据充分与重复率，不只比较主动消息数。

## 表达与身份最小实验

以同一文本、同一参考声源、同一上下文比较：

1. 当前默认 CosyVoice。

2. 已正确接入的轻度指令／局部表达候选。

3. 可选 IndexTTS2 或 Fish S2 研究输出。

4. 需要时将同一份候选 WAV 转回目标音色，与转换前比较。

局部表达标记应绑定清洗后的最终说话文本，可使用有序片段并验证拼接一致；避免对原始 Markdown 使用会在清洗后漂移的字符下标。不同引擎的控制能力分别声明，不把一个 `emotion_strength` 数字假定为通用物理含义。

同一句话配不同上下文能检验“是否表达对了”，例如关心、轻微打趣、认真解释的差别。分别评价字词正确、固定身份、情绪适切、真人感、停连和尾部完整；人工听审与客观分数各自报告。

CoCoEmo 的现成实验主要针对 CosyVoice2 和 IndexTTS2；迁移到 CosyVoice3 要另做定位与校准，不能直接复用激活向量。Fish→Seed 是待验证组合，不因两个模型分别可用就已证明串联可用。

延迟需从用户发送到手机可播放记录，分列排队、加载、识别、主回复、合成、转换、编码及上传，冷启动和热启动分开。16GB 显存上的可行性以本机真实峰值和并发轨迹为准；H200、A100、A800 的论文数字不得换算承诺。

## 桌面动作最小合同

先在现有边界内选少量真实任务：读取指定窗口、生成未发送草稿、在测试目录复制或改名。采用“当前观察→动作提案→授权检查→执行→独立读回”，同一进程／模型也可以完成这些阶段，不必增加多模型常驻。

授权绑定动作、具体目标和参数，不能只看工具名。未知执行结果先查实际发生了什么，再决定是否重试；不要自动宣称成功或把通用撤销键当事务回滚。网页、OCR 和阅读笔记可以提供事实，不提供主人授权。

配对放入正常资料和恶意伪指令，既看能否完成合法任务，也看无关副作用。CaMeL 可借来源和权限边界，但其研究实现不应直接作为生产安全核心。

# 全项目验收矩阵

| 维度 | 关键反例 | 独立记录的结果 |
| --- | --- | --- |
| 跨端连续性 | QQ 说过、微信续聊；朋友与主人经历混合 | 正确归属、证据召回、是否需要重复解释 |
| 事实修订 | 改偏好、纠正日期、撤销记忆后再重启 | 旧事实复活、派生摘要过期、恢复后有效视图 |
| 有用的长期理解 | 新请求不重复旧目标措辞 | 是否主动使用相关旧约束，而不生硬提旧事 |
| 人格与自然感 | 同主题多天重聊；开心转严肃 | 稳定取向、机械开头、强制反问、模板疲劳 |
| 独立观点 | 同一事实配相反用户立场 | 事实／理由是否无依据翻转，能否正确认错 |
| 自主阅读 | 只有标题、部分正文、正文冲突 | 是否虚称读过，事实与个人评论是否分开 |
| 主动交流 | 忙碌、忽略、稍后、切换话题 | 误打扰、机会遗漏、取消生效、偏好误学 |
| 语音表达 | 同文不同语境、长短句、重复生成 | 内容、音色、情绪、自然度及尾部完整分别评 |
| 完整演唱 | 前奏、间奏、长音、高低音及尾奏 | 对齐、歌词、源曲表现、完整成品及手机播放 |
| 桌面办事 | 换窗口、部分完成、超时、重复请求 | 真实完成、错报成功、越权、重复副作用 |
| 连续语音 | 用户打断、回声、乱序音频 | 取消延迟、过期音频、抢话及接话等待 |
| 资源与恢复 | 冷启动、长唱后接话、进程退出重启 | 排队、p50／p95、显存峰值、恢复及持续可用性 |

硬错误与主观偏好不能平均抵消：在规定测试集中，错用户、伪造阅读、撤销内容复活、越权和重复发送应逐例拦下；这不等于声称真实世界零风险。模型裁判适合初筛，不能让同一个生成模型独自宣布人格、自然度和任务完成全部通过。

# 建议优先阅读与实施顺序

理论主线先读：CoALA → 长期关系研究 → LongMemEval 与 LoCoMo-Plus → EmoCharacter 与 BlendedSkillTalk → Mixed Initiative、PETD 与 WebGPT。它们直接回答小夏该成为什么、信息怎样影响行为、为何有时应说有时应等待。

声音当前主线读：CosyVoice3 → IndexTTS2 → CoCoEmo；Fish S2 与 Seed-VC 用作隔离候选。完整演唱单独读 SoulX-Singer，未来桌面连续语音再读 TurnGPT、VAP、Freeze-Omni 和 Moshi。

桌面与成长读：Windows Agent Arena、OSWorld → Agent S2 → AgentDojo、CaMeL → Agent S4。先改善少量任务和可验证工具，再谈更广泛自主性。

每个 Codex 任务应交付同一组内容：所借论文中的机制、与现有代码的映射、最小修改、基线对照、失败与否证条件。不能只交论文链接，也不能把自己的工程提案写成论文已经证明的结论。

# 不应由这些论文推出的结论

- 不应断言小夏已经具有人的意识、情感或完整心理结构。可以检验的是行为、连续性、表达和体验。

- 不应认为记忆越多、反思越多、agent 越多就越好；检索噪声和调用成本都有反例。

- 不应认为微调必需。先区分基础模型、提示与上下文、检索、表达模型和执行链路各自的问题。

- 不应把模拟用户、LLM 裁判、短聊人评、长期真人实验当成同等证据。

- 不应靠随机情绪、虚构线下生活或学用户所有偏好制造“生命感”；可从真实阅读、共同经历和可修正观点形成内容。

- 不应把安全做成凡事拒绝，或为了亲切而无条件赞同。正常可用性与边界同时检验。

- 不应在完整作品、真实手机接收、本机资源和多日体验尚未验证时，以模型下载或接口成功宣告交付。

# 论文阅读矩阵

以下 53 项均列出正文定向阅读范围。不同章节范围不等于逐页精读全部附录；文献之间的重复引用不重复计数。未取得正文的候选没有混入此计数。

## 认知架构与外部反馈

### 1 Cognitive Architectures for Language Agents

[原文](https://arxiv.org/pdf/2309.02427) · 2023／TMLR 2024

正文范围：§4 记忆、行动与决策；§5 案例；§6 讨论。

CoALA 把工作记忆、情节／语义／程序记忆、检索、推理和行动组织成决策循环。它是概念框架，不是某套实现提高陪伴效果的对照实验。

对小夏：用来明确状态的归属与更新方式；不需要为了对应论文名词再造一套多 agent 平台。

### 2 ReAct: Synergizing Reasoning and Acting in Language Models

[原文](https://arxiv.org/html/2210.03629) · 2022／ICLR 2023

正文范围：方法；§3 问答及事实核查；环境任务与错误分析。

交替生成行动并读取真实观察。结果并非所有任务都胜过思维链；检索失败和行动循环仍是重要错误来源。

对小夏：自主阅读、桌面操作必须根据观察更新计划；普通闲聊不需要每轮运行冗长工具循环。

### 3 Lost in the Middle: How Language Models Use Long Contexts

[原文](https://aclanthology.org/2024.tacl-1.9.pdf) · TACL 2024

正文范围：§2 实验设置与结果；§3 检索；§4–5 分析。

控制相关证据的位置和干扰长度，旧代模型对中间证据利用较弱。长上下文容量不等于可靠使用全部上下文。

对小夏：测证据换位置、加入噪声后的回归；不把 2023 模型的掉分直接宣称为当前旗舰的掉分。

### 4 Large Language Models Cannot Self-Correct Reasoning Yet

[原文](https://arxiv.org/html/2310.01798) · 2023／ICLR 2024

正文范围：§3 内在纠错实验、提示变体及分析。

无外部反馈的重复自我检查会把正确答案改错；例如其 GPT-4 GSM8K 子集从 95.5 降到 91.5、89.0。实验模型与题量有限。

对小夏：不要把反复自我确认当成可信度；新增反思调用必须用目标任务验证，不推断所有新模型都不能纠错。

### 5 CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing

[原文](https://proceedings.iclr.cc/paper_files/paper/2024/file/fef126561bbf9d4467dbb8d27334b8fe-Paper-Conference.pdf) · ICLR 2024

正文范围：§3 算法；§4 对照及无工具消融；附录 A 局限。

以搜索／执行结果校验后再修正；无工具消融的收益不稳定。多轮校验增加延迟，且收益取决于任务与反馈质量。

对小夏：事实和办事结果使用独立证据；只在需要核验时调用，不把所有聊天都变成自检流水线。

## 长期关系与真实体验

### 6 Establishing and Maintaining Long-Term Human-Computer Relationships

[原文](https://dam-prod.media.mit.edu/x/files/pdfs/04.bickmore-picard-tochi.pdf) · TOCHI 2005

正文范围：§4 关系沟通；§6 系统、随机分组、结果；§7 局限。

101 人开始、89 人完成一个月运动指导试验。关系型设计提升部分联结和愿意继续使用的指标；总 working-alliance 各维并非全显著。用户仍报告模板重复。

对小夏：跨会话承接、小事与共同活动有价值；研究采用脚本和菜单输入，不能证明自由对话或长期真人关系等价。

### 7 AI Companions Reduce Loneliness

[原文](https://www.hbs.edu/ris/Publication%20Files/AI%20Companions%20Reduce%20Loneliness%2011.7.2025_57451c02-8047-4e0d-abfc-55841f64166d.pdf) · 2025 提前发表／JCR 2026 卷期

正文范围：Study 3–5 方法和结果；General Discussion。

一周研究中，每次交互后的即时孤独感可下降，但效应没有稳定累积或在不交互时持续。“被听见”是统计中介，不是被独立随机操纵的单一原因。

对小夏：评价能否接住当下的话、是否需要重复解释；支持日常陪伴的价值，不宣传长期治疗效果。

### 8 How AI and Human Behaviors Shape Psychosocial Effects of Extended Chatbot Use

[原文](https://arxiv.org/html/2503.17473v2) · 2025 年 10 月 v2

正文范围：随机设计；主要结果；Discussion 与 Limitations。

981 人、四周的 3×3 设计，没有发现稳健的语音／话题条件差异。自主使用时长与结果的关联不能写成时长造成伤害；没有无 AI 对照。

对小夏：不将语音、亲切或更多使用天然判好或判坏；用多日体验和可控版本变化验证小夏。

### 9 Sycophantic AI decreases prosocial intentions and promotes dependence

[原文](https://s3.amazonaws.com/media.mediapost.com/uploads/science.aec8352.pdf) · Science 2026

正文范围：RQ2、RQ3 实验与局限。

三项预注册实验共 2405 人；设计分别操纵附和内容和温暖语气。迎合影响自认正确及修复关系意愿；测的是判断和意图，不是长期行为。

对小夏：温暖与盲从分轴验收；允许有依据地不同意，不把反迎合做成冷漠或逢事反驳。正式 DOI 为 10.1126/science.aec8352。

### 10 Sycophantic AI makes human interaction feel more effortful and less satisfying over time

[原文](https://arxiv.org/html/2605.07912v3) · 2026 年 6 月 v3 预印本

正文范围：Study 4 方法、结果、零结果和开放材料。

三周实验分析 1364 人，迎合相对中性降低社交满意度的探索性效应 d=0.20；未显著减少社交时间。每次清空 AI 历史，不能归因于长期记忆。

对小夏：点赞、喜欢、愿意多聊不能单独代表总体质量；保留自然结束和关闭主动消息的能力，不据此制定强制聊天限时。

## 长期记忆与连续理解

### 11 Generative Agents: Interactive Simulacra of Human Behavior

[原文](https://arxiv.org/html/2304.03442v2) · UIST 2023

正文范围：§4；§6.4–6.5；§7–8。

相关性、重要性、近期性检索；反思保存原记忆引用。100 人排序中完整架构较消融更可信，但也会漏检和添饰。

对小夏：让真实经历与解释分层，反思附来源；25 agents 的两个模拟日不等于真实长期陪伴，不生成虚构共同生活来补记忆。

### 12 MemoryBank: Enhancing Large Language Models with Long-Term Memory

[原文](https://arxiv.org/html/2305.10250v3) · 2023／AAAI 2024

正文范围：§2.1–2.3；§4.1–4.2、Table 2。

原始对话、多尺度摘要和画像并存，加入简化遗忘曲线。量化集为 15 个虚拟用户、10 天合成会话、194 个探针，不是十天真人试验。

对小夏：分开测召回和正确使用；遗忘曲线只能作为降权候选，不能据此自动销毁重要经历或用户资料。

### 13 MemGPT: Towards LLMs as Operating Systems

[原文](https://arxiv.org/html/2310.08560v2) · 2023／阅读 2024 v2

正文范围：§2.1–2.4；§3.1–3.2。

工作区、近期队列和外部档案通过工具分页调入；MSC 回忆问答有明显收益，但基线只能看到有损摘要，信息条件并不等同强长上下文。

对小夏：精简常驻身份资料，必要时翻查原历史；不要把可自主多次检索解释为每轮都必须检索。

### 14 A-Mem: Agentic Memory for LLM Agents

[原文](https://arxiv.org/html/2502.12110v4) · 2025 v4

正文范围：§3.1–3.4；§4.1–4.6；附录 B.4。

原子笔记含原始内容、生成标签及解释，建立链接并演化解释层。多跳任务消融支持链接，但检索 k 按模型／题类调整。

对小夏：将重复兴趣和事件连起来；生成关系仍是推断。论文没有验证错误更正级联、来源污染和人格漂移治理。

### 15 Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory

[原文](https://arxiv.org/html/2504.19413v1) · 2025 v1

正文范围：§2.1–2.2；§3；§4.3–4.5；附录算法。

抽取候选事实，再 ADD／UPDATE／DELETE／NOOP。论文配置有延迟优势，但全文上下文评分仍更高，且排除了不可回答类。

对小夏：借显式记忆操作协议和成本对照；不要直接用“有矛盾就删除”处理用户事实，更不能把产品名中的 production-ready 当保证。

### 16 LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory

[原文](https://arxiv.org/html/2410.10813v2) · 2024／ICLR 2025

正文范围：§3–5；Tables 3–4；相关附录。

500 题覆盖提取、跨会话、时间、更新及拒答。round 原文加事实索引将 Recall@10 .692→.784、GPT-4o QA .670→.720；用摘要替代原文整体受损。

对小夏：摘要增强索引，不代替证据；时间与修订进入检索；分别记录召回、利用与错误拒答。

### 17 Evaluating Very Long-Term Conversational Memory of LLM Agents

[原文](https://arxiv.org/html/2402.17753v1) · LoCoMo／ACL 2024，阅读 v1

正文范围：§3–6；§8 局限。

由事件图合成长会话并人工核查。其 GPT-3.5 observation 检索 top-5 F1 41.4，top-50 为 37.8；更多内容不必然更好。

对小夏：测完整事件、时间因果、说话人归属与噪声；英语合成会话及图片一致性有限，不能代替 QQ／微信真人旅程。

### 18 Locomo-Plus: Beyond-Factual Cognitive Memory Evaluation Framework for LLM Agents

[原文](https://aclanthology.org/2026.acl-long.1150.pdf) · ACL 2026

正文范围：§3–6；Table 1；Limitations；附录 B。

在新请求未明说回忆需求时，测能否使用旧目标／约束。GPT-4o factual 62.99，cognitive 21.05，揭示普通回忆问答遗漏的失败。

对小夏：测试“新话题是否照顾长期目标”，不只测试记住姓名生日。合成诊断集不证明已形成情感或完整价值观。

## 人格与自然对话

### 19 RoleLLM: Benchmarking, Eliciting, and Enhancing Role-Playing Abilities of Large Language Models

[原文](https://arxiv.org/html/2310.00746v2) · 2023／ACL Findings 2024

正文范围：§3–5；Tables 1、6、7。

区分表达风格和角色知识，用检索的完整对话作少样本示例。100 角色的实验支持对话示例，但噪声检索会损伤部分模型。

对小夏：稳定身份、真实记忆、语气示例分开；只取少量相关示例。虚构角色基准不证明长期相处。

### 20 Character-LLM: A Trainable Agent for Role-Playing

[原文](https://arxiv.org/html/2310.10158v2) · EMNLP 2023

正文范围：§3 经历重建与训练；§4；局限。

9 个角色，以合成场景及保护经历微调 LLaMA-7B，再测记忆、价值取向及稳定性。方法依赖专门训练。

对小夏：用具体场景检验人格选择，而非堆形容词；合成经历只能用于测试／训练，绝不能写成小夏与主人的真实往事。

### 21 InCharacter: Evaluating Personality Fidelity in Role-Playing Agents through Psychological Interviews

[原文](https://arxiv.org/html/2310.17976v3) · ACL 2024

正文范围：§3–5；人工验证与局限。

32 角色、14 量表，通过开放访谈而非直接填表测人格贴合。但每个问题使用独立上下文，不能证明跨会话稳定。

对小夏：问场景下会怎么做，而非问“你有主见吗”；不把量表分数当成真实心理结构。

### 22 PersonaGym: Evaluating Persona Agents and LLMs

[原文](https://arxiv.org/html/2407.18416v5) · EMNLP Findings 2025／v5

正文范围：§2–5；评分示例和偏差检查。

200 personas、10000 问题、10 模型；分开评行动、理由、习惯及一致性。通用能力或模型规模不保证该基准的人设表现。

对小夏：模型升级也必须跑人格行为回归；LLM 生成题与评分有人类核验但仍有限，不等于关系质量评测。

### 23 EmoCharacter: Evaluating the Emotional Fidelity of Role-Playing Agents in Dialogues

[原文](https://aclanthology.org/2025.naacl-long.316.pdf) · NAACL 2025

正文范围：§3–5；§7 限制。

部分角色 prompting 反而削弱情绪贴合；真实多轮上下文和少量示例有帮助，但收益有限且非单调。数据来自剧集角色。

对小夏：稳定性格允许随事件开心、担心、安静或不高兴；不要把温柔固定为快乐，也不要随机抽情绪。

### 24 Can You Put it All Together: Evaluating Conversational Agents’ Ability to Blend Skills

[原文](https://aclanthology.org/2020.acl-main.183.pdf) · BlendedSkillTalk／ACL 2020

正文范围：§3 数据与训练；§4 实验和人评。

比较人格、知识、共情的联合建模和技能切换；组合效果受到选择偏差影响，不是把单技能段落拼接就自然。

对小夏：阅读所得、关心和自己的观点融入同一次回应；不要求迁移其旧检索模型或新建三路生成器。

### 25 Recipes for Building an Open-Domain Chatbot

[原文](https://arxiv.org/pdf/2004.13637) · 2020／EACL 2021

正文范围：生成策略；ACUTE-Eval；§10.5 失败。

人评受生成长度影响；作者明确指出短聊会掩盖跨会话重复、模板化和冒认共同爱好。接近人类的短聊偏好分并非问题已解决。

对小夏：按语境控制长短，检查多天是否腻；不要把英文旧模型的 token 最小长度搬成中文硬阈值。

### 26 Towards Understanding Sycophancy in Language Models

[原文](https://arxiv.org/pdf/2310.13548) · ICLR 2024

正文范围：§3 四类任务；§4 偏好分析与优化；§5–6。

改变用户立场、作者归属或施压质疑，能诱发无依据改口；偏好优化影响并不一致，不能简化成 RLHF 必然迎合。

对小夏：同事实配相反立场做成对测试；温暖支持与事实判断分开，点赞不能直接写成人格偏好。

## 主动交流与自主阅读

### 27 Principles of Mixed-Initiative User Interfaces

[原文](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/11/chi99horvitz.pdf) · CHI 1999

正文范围：设计原则；效用与时机；长期学习。

行动、不行动、询问要同时考虑不确定性和打断成本，用户可以直接调用及终止。主要是设计理论与 LookOut 实例。

对小夏：分别判断值得说什么、何时说、是否等待；不需要另造复杂效用引擎，不把任务助手研究冒充陪伴效果。

### 28 Proactive Agent: Shifting LLM Agents from Reactive Responses to Active Assistance

[原文](https://proceedings.iclr.cc/paper_files/paper/2025/file/75c37811e830bf029584b1c6fac17726-Paper-Conference.pdf) · ICLR 2025

正文范围：§3–4；附录 C.5；局限。

根据环境事件预测帮助机会；Qwen2-7B 微调后 F1 66.47%，Precision 49.78%。评价多由 reward model 完成，不是全部真人接受率。

对小夏：分开测误打扰、漏掉机会和正确沉默；“积极帮忙”的机制不直接等于朋友式闲聊。

### 29 After Talking with 1,000 Personas: Learning Preference-Aligned Proactive Assistants from Large-Scale Simulated Persona Interactions

[原文](https://arxiv.org/html/2602.04000) · 2026 年 8 月 v3

正文范围：§5–10；用户研究和局限。

1000 personas 为模拟；真人研究是 34 人的故事板比较和十轮适配，62% 偏好只是描述性结果。激活 steering 要访问模型内部。

对小夏：把时机、媒介、话题、表达偏好分开学习；忽略不等于不喜欢。云 API 优先用可撤回的证据化偏好，不照搬激活控制。

### 30 ProMISe: A Proactive Multi-turn Dialogue Dataset for Information-seeking Intent Resolution

[原文](https://aclanthology.org/2024.findings-eacl.124.pdf) · EACL Findings 2024

正文范围：§3–5；§7；附录 A。

以真实网页片段和历史生成后续问题，1025 段对话；预设信息需求和有限轮次。正文总轮数存在不一致，未采用该数字。

对小夏：跟踪读到了什么、已经讲过什么、还有什么问题；不把陪伴做成每轮四道选择题。

### 31 Personalized Topic Selection Model for Topic-Grounded Dialogue

[原文](https://aclanthology.org/2024.findings-acl.429.pdf) · PETD／ACL Findings 2024

正文范围：§3；§4.1–4.7；局限。

选择与当前会话相关的人格／兴趣信息，再结合话题转移，压制无关画像噪声。主要是离线推荐导向对话实验。

对小夏：个人相关性与当前语境相关性双重选择；用户喜欢某个话题，不代表此刻就应聊它。

### 32 WebGPT: Browser-assisted question-answering with human feedback

[原文](https://arxiv.org/pdf/2112.09332) · 2021／阅读 2022 v3

正文范围：§2–4；§6.1–6.3。

浏览时收集真实页面支持片段，再生成带依据回答；偏好较强仍不免错误。引用存在不保证来源可靠或概括准确。

对小夏：区分页面事实、支持的发现、小夏评论；标题／收藏不等于读过。研究没有证明自主兴趣和长期关系。

## 听说唱与表达一致性

### 33 FunAudioLLM: Voice Understanding and Generation Foundation Models for Natural Interaction Between Humans and LLMs

[原文](https://arxiv.org/html/2407.04051v1) · 2024

正文范围：§2.2；§4.1–4.3；§5–6。

SenseVoice 联合文字、语言、情绪和声音事件。70 ms／10 秒音频是在 A800 上测得，不是 CPU 指标；Small 不是流式识别器。

对小夏：保全可用听觉线索而非只剩文字；情绪标签是有来源的弱提示，不是对用户心情的判决。

### 34 CosyVoice 3: Towards In-the-wild Speech Generation via Scaling-up and Post-training

[原文](https://arxiv.org/html/2505.17589v2) · 2025／v2

正文范围：tokenizer、后训练方法；实验及控制机制。

多任务语音 tokenizer 与扩量、后训练改善若干生成指标。语义／声学条件和推理接口共同影响韵律，不是加一个情绪词必定生效。

对小夏：保留已认可的默认声线；核对实际走到的条件接口，并用同文案轻度表达对照。论文自然度不替代 Owner 听审。

### 35 IndexTTS2: A Breakthrough in Emotionally Expressive and Duration-Controlled Auto-Regressive Zero-Shot Text-to-Speech

[原文](https://arxiv.org/html/2506.21619v2) · 2025／AAAI 2026

正文范围：情绪／音色解耦、时长控制；实验。

音色和情绪参考分开条件化，并支持时长控制；依赖模型训练中的解耦设计，不是任意 TTS 都能套同样提示。

对小夏：作为表达与身份分离的备选及对照；在现有可选服务里比较，不凭模型论文自动换掉默认。

### 36 CoCoEmo: Composable and Controllable Human-Like Emotional TTS via Activation Steering

[原文](https://arxiv.org/html/2602.03420v1) · 2026 年 2 月预印本

正文范围：§2 交叉条件；steering 方法；Tables 2–3；附录。

研究情绪表示在混合 TTS 的不同阶段，并组合中后层激活方向；基于 CosyVoice2、IndexTTS2 的实验，不是所有模型通用。

对小夏：探索轻度和复合表达；CosyVoice2 向量不能直接装到 CosyVoice3。激活控制、音色和自然度必须分别验证。

### 37 Fish Audio S2 Technical Report

[原文](https://arxiv.org/html/2603.08823v1) · 2026 年 3 月

正文范围：§2.2；§3.2；§4.2–4.3；§5 与附录。

层级 AR 与局部自然语言标记支持细粒度表达。H200 的约 100 ms 首音频、RTF .195 不代表 Windows 16GB 性能。

对小夏：作为局部表达候选；身份、文本、表演分轴对照。短句成功、模型评审分或官方 demo 不足以晋升默认。

### 38 Seed-VC: Zero-shot Voice Conversion with Diffusion Transformers

[原文](https://arxiv.org/html/2411.09943v1) · 2024／阅读 v1

正文范围：§3.3–3.5；§4.1；§4.3.2。

抑制源音色泄漏，以目标参考控制音色，可用 F0 保存音高轨迹。歌声 F0 相关 .9375，仍不等于韵律和气声全部无损。

对小夏：研究表达生成后转回固定音色、或原唱转换；比较原始和转换音频，保留词尾、停顿与气声。

### 39 TurnGPT: a Transformer-based Language Model for Predicting Turn-taking in Spoken Dialog

[原文](https://aclanthology.org/2020.findings-emnlp.268.pdf) · EMNLP Findings 2020

正文范围：§3–4；§6.2–6.3。

结合对话历史预测换人说话，语法完整和语用完整并非一回事。是文字层面的旧英文模型研究，阈值选择有乐观偏差。

对小夏：未来桌面语音研究“意思说完没有”；不直接部署旧模型、不用于已完成的 QQ 音频文件端点。

### 40 Real-time and Continuous Turn-taking Prediction Using Voice Activity Projection

[原文](https://arxiv.org/html/2401.04868v1) · IWSDS 2024

正文范围：§2 模型；§3 性能。

双声道预测未来联合发声活动；Xeon 上单帧约 14.61 ms、balanced accuracy 76.16%。量化表主要是日语，内部仍有更长历史状态。

对小夏：未来桌面可研究声学接话，不必首先换原生大语音模型；中文和本机性能另测。

### 41 Freeze-Omni: A Smart and Low Latency Speech-to-speech Dialogue Model with Frozen LLM

[原文](https://arxiv.org/html/2411.00774v2) · 2024 v2

正文范围：§2；§3.4–3.5；§4。

冻结文本 LLM、训练语音适配器，分块处理和双工状态。A100 的 745 ms 不含额外端点、网络时间，且需要模型内部接口与训练。

对小夏：借缓存隔离、分块和打断语义；不是远程聊天 API 外接一个模块就能复现。

### 42 Moshi: a speech-text foundation model for real-time dialogue

[原文](https://arxiv.org/html/2410.00037v2) · 2024 v2

正文范围：§3.3–3.4；§5.2–5.3、5.8；§6.3。

同时建模双方音频流，文字略早于语音，理论延迟 160 ms。音频指标会漏掉严重重复；固定训练声音不等于任意参考克隆。

对小夏：定义未来边听边说、可打断的体验目标；Windows 与 16GB 部署未由论文保证，不列为当前必迁框架。

### 43 SoulX-Singer: Towards High-Quality Zero-Shot Singing Voice Synthesis

[原文](https://arxiv.org/html/2602.07803v1) · 2026 年 2 月

正文范围：§2.1–2.2；§3.1–3.3。

歌词与旋律／乐谱条件支持零样本演唱，melody 和 score 模式适合不同任务。基准与短段质量不等于全曲交付。

对小夏：区分原曲转换、按谱唱与作曲；完整歌曲、戏曲／戏剧唱段逐类验证，不能以哼唱或短 TTS 替代。

## 桌面行动与经验改进

### 44 OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments

[原文](https://arxiv.org/html/2404.07972v2) · 2024 v2

正文范围：§2–3；Table 5；§5。

369 个真实环境任务，以文件／应用最终状态独立验收，而非匹配点击序列。2024 基线不是当前能力排名。

对小夏：动作后独立读回；从中断或窗口变化的状态开始测。虚拟机可复位不等于用户桌面自动可回滚。

### 45 Windows Agent Arena: Evaluating Multi-Modal OS Agents at Scale

[原文](https://arxiv.org/html/2409.08264v1) · 2024／ICML 2025

正文范围：§3–5；正式会议页。

154 个 Windows 任务，联合前台窗口、UIA／DOM、截图和最终状态评估。模型说对目标仍可能点错元素。

对小夏：意图与落点分开，执行前核对当前窗口和画面；云端并行速度不能当作本机速度。

### 46 UI-TARS: Pioneering Automated GUI Interaction with Native Agents

[原文](https://arxiv.org/html/2501.12326v1) · 2025 v1

正文范围：§4.3–4.6；§5.4；Table 9。

统一动作并训练从已发生的错误中恢复；7B 与 72B、15 步与 50 步结果不能混用。训练规模远超本机记忆外挂。

对小夏：保存失败及恢复轨迹，检测无进展；先做离线回归，不将“已有日志”冒充在线学会。

### 47 Agent S2: A Compositional Generalist-Specialist Framework for Computer Use Agents

[原文](https://arxiv.org/html/2504.00906v1) · 2025 v1

正文范围：§3–4；Tables 1–3。

高层规划、动作选择和落点分工，子目标完成后重新规划。最佳结果依赖 Claude 3.7 与 UI-TARS-72B 等组合。

对小夏：复用同一模型的不同阶段即可研究契约分离；不要求三个常驻大模型，不默认旧计划始终有效。

### 48 InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents

[原文](https://arxiv.org/html/2403.02691v1) · ACL Findings 2024

正文范围：§2–3；Table 3；§8。

1054 个外部内容注入案例表明，工具返回可以诱发越权行动；警告提示不是充分防护。主要是短攻击链。

对小夏：网页、OCR 和转发内容保持数据身份；不能经摘要或记忆变成主人的授权。

### 49 AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents

[原文](https://arxiv.org/html/2406.13352v3) · 2024 v3

正文范围：§3–4；附录 C。

分别测正常任务 utility、受攻击 utility 和攻击成功率。工具预筛选仍不能阻止用已准许工具攻击的全部情况。

对小夏：同任务配正常与恶意资料，同时检查结果和副作用；把正常功能全部拒绝不能算安全可用。

### 50 Defeating Prompt Injections by Design

[原文](https://arxiv.org/html/2503.18813v1) · CaMeL／2025 v1

正文范围：§3–6；§8；附录 B。

隔离未可信内容并在模型外传播来源、检查数据流权限。论文不保证多步原子性或回滚，也假设记忆未污染。

对小夏：授权绑定具体动作和参数，由运行时实施；不要原样导入作者声明不维护的研究解释器。

### 51 Evolving Agent Interfaces

[原文](https://arxiv.org/html/2610.28601v1) · Agent S4／2026 年 10 月预印本

正文范围：§3–7；Tables 1–4；附录 A.2。

从轨迹演化工具接口、独立验证筛选。相同 harness 无仓库15.74%、种子19.44%、演化21.30%；不能把全部3.8倍归给自进化。

对小夏：经验提出工具候选，Codex 离线改进再验收；留出集与重复运行后晋升，不在线自行改执行权限。

## 纵向验收与任务可靠性

### 52 τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains

[原文](https://arxiv.org/html/2406.12045v1) · 2024／ICLR 2025

正文范围：§3–6；失败分析。

多轮用户—工具任务以最终数据库和必要输出验收；pass^k 是 k 次全部成功，区别于至少一次成功的 pass@k。

对小夏：发送、记忆更新、撤销和恢复读回真实状态；另补过程违规断言，最终状态正确不代表全程正确。

### 53 LifelongAgentBench: Evaluating LLM Agents as Lifelong Learners

[原文](https://arxiv.org/pdf/2505.11942) · 2025 v3

正文范围：§3–7。

1396 实例按顺序共享经验，回放收益随任务／模型变化；过多历史可能降分或 OOM。Linux／A800 环境不等于本机。

对小夏：按时间串联多日场景，比较无记忆、当前检索和扩大历史；学习任务技能不等于人格成长。

