# 日常语音自然度：社区实现与论文核对

2026-11-03。当前问题是用户认为语气仍平、机械，且此前强化整句情绪的版本像配音。QQ 四秒参考与已认可的 CosyVoice 底色保持不变。最新反馈要求先深入社区实现和论文；它没有认可或否定最新 EditX 样本。

## 本轮结论

优先验证**同一轮回复中的局部说话方式，交给现有模型原生生成**。例如一句话中的重音、带笑的短语，而非继续为整句话统一增大“开心”强度。暂未证明这能达到用户要求的听感，不把接口或标记通过当作自然度通过。

当前 `chat_replyer.py` 只输出整句 `voice_emotion`，`server_cosy.py` 将它映射到固定指令。Cosy 的 `emotion_strength` 目前是关闭或启用轻度指令，数值大于零不产生连续强度变化；这一点已在 `voice-api.md` 说明。继续微调该数值不会得到渐进表达。

## 社区的实际实现

| 项目与读取版本 | 代码中的做法 | 对小夏的启发与范围 |
| --- | --- | --- |
| [N.E.K.O.：tts_runtime.py](https://github.com/Project-N-E-K-O/N.E.K.O/blob/4177047b757894978237bb0b7e4d7be8ae9ab451/main_logic/core/tts_runtime.py#L2077) | 原生实时语音与自定义音色的外部 TTS 分别路由；[架构页](https://project-neko.online/architecture/tts-pipeline)说明双向流式和按句处理 | 比较示例要先确认走的是哪条声音链路。不能把服务商原生声音的表现归因于一个本地情绪参数。 |
| [Open-LLM-VTuber：CosyVoice 适配](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/tts/cosyvoice2_tts.py#L34) | 每次传入配置中的同一个 `instruct_text`；[任务管理器](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/conversations/tts_manager.py#L140)把动作另外传给前端 | 该适配没有提供可直接搬来的逐句语气决策；表情动作与声音表现需要分别核查。 |
| [小智后端：双向流式 TTS](https://github.com/xinnan-tech/xiaozhi-esp32-server/blob/5b0de039c993df5fd39cfb0ab8f63d5ee8d9bc8b/main/xiaozhi-server/core/providers/tts/huoshan_double_stream.py#L384) | 文本片段经持久连接发送，服务端返回音频片段 | 可借鉴到以后桌宠的实时播放。QQ 语音消息和微信附件目前需要完整文件，首包速度不能直接等同于收到消息的时间。 |
| [LiveKit / Fish 表达模式](https://docs.livekit.io/agents/models/tts/fishaudio/#expressive-mode) | LLM 给回复添加表达标记，语音模型执行局部语气、停顿等 | 借鉴“内容与表达同轮产生”的连接方式。该页面的 S2.1 Pro 服务不等同于本地 S2 Pro；不引入整个框架。 |

## 论文与可复用机制

- [CosyVoice 3，2.5 节](https://arxiv.org/html/2505.17589v2#S2.SS5)：区分自然语言指令与细粒度声音标记。[官方示例](https://github.com/QwenAudio/CosyVoice/blob/main/example.py#L80)和本机 tokenizer 都包含原生控制。官方细粒度示例使用 cross-lingual 路径；**不能据此假定保留完整 QQ 参考的 zero-shot 路径效果相同**。下一步须实测这一处，而不是先改默认服务。
- [Fish Audio S2，3.2、5 节](https://arxiv.org/html/2603.08823v1)：训练文本在局部位置标注语气与声音事件，这为局部控制提供了学习基础。论文的约 100 ms 首包来自 H200 与 SGLang 环境，不是这台 Windows / RTX 5070 Ti 的承诺。其机制可参考，现阶段没有充分依据再装一个后端。
- [EmoSteer-TTS](https://arxiv.org/html/2508.03543v1)：在 flow 模型内部调整激活；实验含 CosyVoice2，未验证本机 CosyVoice3。论文 Discussion 明确仍有细微情绪控制难点。表中 CosyVoice2 的自然度 3.69 与加 steering 的 3.65 也不支持“装上就更自然”的结论。保留为研究路线，不把非官方移植当成熟组件。

## 已做的本机核对

仅加载实际安装的 tokenizer 和 WeText，没有停服务，也没有新生成试音：

- `<strong>…</strong>`、`<laughter>…</laughter>`、`[sigh]`、`[laughter]`、`[breath]` 均经过当前文本规范化后保留，并命中各自单一原生 token。
- 普通 `明天18:30见。` 仍转换为 `明天十八点三十分见。`。
- 此结果只证明输入通路成立，尚不证明发声效果、自然程度或音色稳定。

复现脚本和输出在本机 `.runtime/voice-base/check_native_cues.py`、`native-cues-preflight.json`。前一阶段 EditX 的五条实际输出、耗时和恢复结果在 `editx/outcome.json`：额外热编辑耗时 2.9–8.9 秒，未进入默认服务。

## 接下来的实现顺序

1. 在固定 QQ 参考、相同词句与随机种子下，核对原生局部控制能否作用于当前 zero-shot 路径，检查是否读出标记、改字或明显漂移。先验证机制，不继续盲调全句情绪提示。
2. 有有效证据后，再复用当前单次回复调用输出语音表达信息，让文字显示与发声共用同一句内容。无需新增情绪分类模型或第二轮改写。
3. 放回连续日常接话核对使用频率、前后语气和总等待；语音自然度最终仍需真实听感。听、说、长短歌曲与各类戏曲的完整 Goal 保持未完成。
