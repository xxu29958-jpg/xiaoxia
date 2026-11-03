# 日常语音自然度：社区实现与论文核对

2026-11-03。当前问题是用户认为语气仍平、机械，且此前强化整句情绪的版本像配音。QQ 四秒参考与已认可的 CosyVoice 底色保持不变。最新反馈要求先深入社区实现和论文；它没有认可或否定最新 EditX 样本。

**当前部署更新：**用户随后要求直接落实候选，并明确“完成候选切换就暂停”。本机现已启用 **Fish S2＋Seed-VC**；QQ 四秒参考未变。下面保留的是候选形成过程，先前“未切换”的描述对应当时的离线实验。此次没有把等待再次选样作为上线前提，也没有声称完整自然度已获认可。

启用后，六段既有测试录音作为真实 Record 输入现用 WebChat：五轮返回候选语音，一轮遵从只打字要求，回顾上一轮的内容正确。五轮完整语音回复为 10.066–13.967 秒，平均 11.893 秒。整组语音服务重新启动后，再完成 8.115 秒演唱和后续说话；唱后说话请求 13.868 秒，识别内容正确，五个服务就绪。接口与模型切换已验证，没有追加手机端送达或真人感验收结论。16 项现有语音接收／回复路径检查通过。实际记录在 `.runtime/voice-base/fish-activation/` 和 `voice-turn/fish-activated.json`。

## 本轮结论

研究指向**同一轮回复中的局部说话方式，由语音模型原生生成**，例如重音、带笑的短语。但实际验证后，当前 CosyVoice3 的局部标记还不适合自动用于日常聊天：保留完整参考时出现句尾遗漏，官方 cross-lingual 调用则有明显的声线稳定性问题。没有把“支持标记”当作自然度已通过，也没有给聊天增加一层表达规则。

当前 `chat_replyer.py` 只输出整句 `voice_emotion`，`server_cosy.py` 将它映射到固定指令。Cosy 的 `emotion_strength` 目前是关闭或启用轻度指令，数值大于零不产生连续强度变化；这一点已在 `voice-api.md` 说明。继续微调该数值不会得到渐进表达。

## 社区的实际实现

| 项目与读取版本 | 代码中的做法 | 对小夏的启发与范围 |
| --- | --- | --- |
| [N.E.K.O.：tts_runtime.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/4177047b757894978237bb0b7e4d7be8ae9ab451/main_logic/core/tts_runtime.py#L2077) | 原生实时语音与自定义音色的外部 TTS 分别路由；[架构页](https://project-neko.online/architecture/tts-pipeline)说明双向流式和按句处理 | 比较示例要先确认走的是哪条声音链路。不能把服务商原生声音的表现归因于一个本地情绪参数。 |
| [Open-LLM-VTuber：CosyVoice 适配](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/tts/cosyvoice2_tts.py#L34) | 每次传入配置中的同一个 `instruct_text`；[任务管理器](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/conversations/tts_manager.py#L140)把动作另外传给前端 | 该适配没有提供可直接搬来的逐句语气决策；表情动作与声音表现需要分别核查。 |
| [小智后端：双向流式 TTS](https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/5b0de039c993df5fd39cfb0ab8f63d5ee8d9bc8b/main/xiaozhi-server/core/providers/tts/huoshan_double_stream.py#L384) | 文本片段经持久连接发送，服务端返回音频片段 | 可借鉴到以后桌宠的实时播放。QQ 语音消息和微信附件目前需要完整文件，首包速度不能直接等同于收到消息的时间。 |
| [LiveKit / Fish 表达模式](https://docs.livekit.io/agents/models/tts/fishaudio/#expressive-mode) | LLM 给回复添加表达标记，语音模型执行局部语气、停顿等 | 借鉴“内容与表达同轮产生”的连接方式。该页面的 S2.1 Pro 服务不等同于本地 S2 Pro；不引入整个框架。 |

## 论文与可复用机制

- [CosyVoice 3，2.5 节](https://arxiv.org/html/2505.17589v2#S2.SS5)：区分自然语言指令与细粒度声音标记。[官方示例](https://github.com/QwenAudio/CosyVoice/blob/main/example.py#L80)和本机 tokenizer 都包含原生控制。官方细粒度示例使用 cross-lingual 路径；**保留完整 QQ 参考的 zero-shot 路径不等同于它**。两种调用已分别实测，见下文。
- [Fish Audio S2，3.2、5 节](https://arxiv.org/html/2603.08823v1)：训练文本在局部位置标注语气与声音事件，这为局部控制提供了学习基础。论文的约 100 ms 首包来自 H200 与 SGLang 环境，不是这台 Windows / RTX 5070 Ti 的承诺。在现用 Cosy 原生控制暴露具体限制后，开始对官方 S2 权重做独立可行性验证；没有切换正式聊天。
- [EmoSteer-TTS](https://arxiv.org/html/2508.03543v1)：在 flow 模型内部调整激活；实验含 CosyVoice2，未验证本机 CosyVoice3。论文 Discussion 明确仍有细微情绪控制难点。表中 CosyVoice2 的自然度 3.69 与加 steering 的 3.65 也不支持“装上就更自然”的结论。保留为研究路线，不把非官方移植当成熟组件。

## 已做的本机核对

首先只加载实际安装的 tokenizer 和 WeText：

- `<strong>…</strong>`、`<laughter>…</laughter>`、`[sigh]`、`[laughter]`、`[breath]` 均经过当前文本规范化后保留，并命中各自单一原生 token。
- 普通 `明天18:30见。` 仍转换为 `明天十八点三十分见。`。
- 这一步只证明输入通路成立，不证明发声效果、自然程度或音色稳定。

随后实际新生成 20 条对照，词句、参考与成对随机种子固定。已存在且源码未变化的基线按文件哈希复用，没有把旧耗时当作新测得的加速。

| 调用方式 | 实测结果 | 决定 |
| --- | --- | --- |
| 完整参考 zero-shot，四句、两个种子 | 8 条带标记输出中，SenseVoice 完全同字 4 条；原文本基线为 6/8。CAMPPlus 均值 0.86265，对照 0.87750。两个种子的重音短句都少了句尾“啊”，独立 Whisper 也得到同样结果。 | 不给日常回复自动添加这些标记。其他识别差异仍可能是识别误差，不能全部判为发音错误。 |
| 官方 cross-lingual，两句、两个种子 | 重音短句保住了“啊”，但带标记声纹相似度均值 0.78878，对照 0.82861；同一句在不同种子下波动较大。 | 不整体改用该调用方式。相似度只是身份一致性的辅助指标，不是听感评分。 |
| 完整参考，另一个重音范围 | 两条仍被 SenseVoice 识别为少“啊”。 | 没有继续枚举范围和全句情绪强度。 |

两次短暂停机分别为 68.858 秒、64.685 秒，均已恢复 Cosy。脚本与原始音频索引在本机 `.runtime/voice-base/native-cues/`、`native-cues-official/`。全部输出没有削波；这不能代替自然度验收。

前一阶段 EditX 的五条实际输出、耗时和恢复结果在 `editx/outcome.json`：额外热编辑耗时 2.9–8.9 秒，未进入默认服务。用户随后要求调研社区和论文，并未认可或否定这组 EditX 声音。

## S2 的资源可行性依据

[官方安装说明](https://github.com/fishaudio/fish-speech/blob/0f8a12e8a651062fdee9c33a894bdccd3a8513f1/docs/zh/install.md)建议 S2 至少 24 GB 显存；本机只有 16 GB。[社区 issue #1310](https://github.com/fishaudio/fish-speech/issues/1310)定位到完整 KV 缓存参与每次注意力计算的浪费，当前官方源码已有按有效长度切片的优化，也有 `max_length` 参数。独立实验据此使用较小的 4096-token 缓存与原始 bf16 权重。采用 meta 构造后加载官方权重，避免先在内存创建一份约 18 GB 的随机参数；使用官方函数重建位置和因果掩码，没有改推理权重或采样算法。

源码固定 `0f8a12e8a651062fdee9c33a894bdccd3a8513f1`，权重固定 `1de9996b6be38b745688de084d87a5633f714e4e`。三个大文件从官方魔搭镜像获取，大小与 SHA-256 和该 Hugging Face 版本一致。目录为 `D:/AI/AdaVoice/research/fish-s2-20261103`，与正式服务环境分开。

[双 16 GB 显卡案例](https://github.com/fishaudio/fish-speech/discussions/1264)不是单张 16 GB 可用的证据。[s2.cpp](https://github.com/rodrigomatta/s2.cpp)明确仍属实验阶段，因此这一轮先验证官方 PyTorch 路径，没有同时引入量化权重和另一套运行时。

本机已经成功生成两句、各一对带／不带标记的四条音频。原说话与唱歌工作进程在实测期间暂停，106.383 秒后均恢复：

| 文本 | 无标记：整段生成 / 音频长度 | 局部标记：整段生成 / 音频长度 |
| --- | --- | --- |
| 你还真记得啊。 | 2.314 / 1.068 秒 | 1.979 / 1.207 秒，`[emphasis]` |
| 好嘛，我连叉子都没拿到，你就毁尸灭迹了。 | 5.922 / 3.855 秒 | 5.904 / 3.855 秒，`[laughing tone]` |

模型加载 18.014 秒，QQ 参考编码 1.652 秒，未启用编译。PyTorch 显存保留峰值 13.441 GiB，全卡最低空闲约 0.653 GiB。**这只证明该缓存配置下的四条短句可运行，不证明与现有模型共驻、长句或正式聊天已经适配。** 两组声纹相似度均值分别为 0.71036、0.70266；均无削波。SenseVoice 对重音短句漏记“啊”，但独立 Whisper 能识别出来；另一处“好吗／好嘛”也有识别分歧，未据此认定发音错误。

## 复用已有音色转换的后续核对

[Seed-VC 论文](https://arxiv.org/abs/2411.09943)以参考声音转换来源音色，并用 F0 条件保留音高。[官方实现](https://github.com/Plachtaa/seed-vc)区分音色转换与风格／口音转换。本机已有前者的 F0 版本，因此直接把上面四条音频交给现有转换服务：参考仍是同一条 QQ 四秒音频，音高调整为零，保持时长，没有重装或新增服务。

四条转换增加 0.640–1.098 秒。带笑接话的声纹相似度由 0.72751 升至 0.87216，识别内容一致，输出没有削波。**这仍只是身份一致性辅助证据，不代表已经听起来更像真人。** 合成与转换在两次独立运行中测量，不能把两段耗时相加冒充包含模型切换的聊天总延迟。也没有用这一候选替换现用 Cosy。

完整记录在本机 `.runtime/voice-base/fish-s2/`、`fish-s2-transfer/`；音频在 `outputs/voice-fish-s2/`、`outputs/voice-fish-s2-transfer/`。最终复核五个服务就绪，实际 Cosy 发声请求 1.595 秒，识别正确；没有发送 QQ／微信消息或新增手机端验收结论。

## 当前停点

候选切换已经完成，按用户要求暂停后续打磨，服务继续在线。待用户恢复 Goal 后再处理实际使用中的自然度、速度与演唱问题；此次暂停没有缩减听、说、长短歌曲与各类戏曲的完整目标。
