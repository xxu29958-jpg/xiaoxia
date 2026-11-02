# 上游与资源来源

本工程保留上游署名和许可证。模型文件单独下载，不存入 Git。

| 组件 | 来源 |
|---|---|
| AstrBot 主程序与管理界面 | https://github.com/AstrBotDevs/AstrBot |
| IndexTTS-2 | https://github.com/index-tts/index-tts |
| Seed-VC | https://github.com/Plachtaa/seed-vc |
| SoulX-Singer | https://github.com/Soul-AILab/SoulX-Singer |
| Mel-Band Roformer 分离模型 | https://huggingface.co/becruily/mel-band-roformer-karaoke |
| SenseVoiceSmall | https://huggingface.co/FunAudioLLM/SenseVoiceSmall |
| 记忆向量模型 | https://huggingface.co/intfloat/multilingual-e5-small |

语音推理源码的版本见 `vendor/sources.json`。Windows 音频读写、模型本地路径和显存调度等修改随工程源码保存。模型版本、文件大小和 SHA-256 见 `models.manifest.json`。Seed-VC 及各模型附属的 Whisper、BigVGAN、CAMPPlus、RMVPE、MaskGCT、w2v-BERT 资源来源也在清单中。

`data/plugins` 保留各插件的 `metadata.yaml`、上游文档和现有许可证，包括 AnySearch、Codex OAuth Plug、Meme Manager、Memory Companion、TurnFlow。小夏的本地修改位于对应插件中；这些插件没有因此变成本工程独占作品。

QQ 4 秒参考是用户选定的小夏合成回复，来自 IndexTTS-2 声线；不是用户的私人语音录音。SoulX-Singer 自带的 `zh_prompt` 是其简谱合成输入，最终声线仍由共同的 QQ 参考转换决定。

长曲验证使用了 UPF 的 [京剧教学唱段数据集](https://zenodo.org/records/1286350)（Rong Gong、Xavier Serra，CC BY-NC 4.0）和 [A Better Way](https://ccmixter.org/files/Rataxes/65367)（Rataxes feat. Snowflake，CC BY 3.0）。这些验证音源和转换作品未打包进源码仓库。测试覆盖不代表其他剧种已经达到相同质量。

其他剧种的实际链路验证使用央视公开节目：[昆曲《牡丹亭·游园》](https://tv.cctv.com/2009/12/11/VIDEPoDXGbmmFyyc7xSBqEaK091211.shtml)、[黄梅戏《女驸马》](https://tv.cctv.com/2024/02/25/VIDEHSXwYR2AHnv33Vcn9QpD240225.shtml)、[越剧《梁祝·草桥结拜》](https://tv.cctv.com/2022/02/15/VIDEmMESAmys0M5WGMQJG4IQ220215.shtml)。节目和转换音频均未随仓库分发。
