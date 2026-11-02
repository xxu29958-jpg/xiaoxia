# 小夏 Xiaoxia

可在 Windows 本机运行的聊天伴侣工程，基于维护后的 AstrBot，包含共享记忆、日常接话、主动聊天、QQ／微信接入和独立语音服务。语音服务可供后续桌宠直接调用。

小夏的声音固定使用已选定的 **QQ 4.145 秒回复**。文件位于 `assets/voices/qq-approved-4s.wav`，说话和唱歌共用它；不会自动换成模型示例声线。

## 当前能做什么

- **听**：SenseVoiceSmall 本地识别语音，并将原始录音保留给后续演唱调用。
- **说**：IndexTTS-2，QQ 普通语音使用语音消息，微信发送可播放的 MP3 附件。
- **唱**：保留所给歌曲／戏曲演唱的旋律、节奏和时长，将人声转换为小夏参考声线，再混回伴奏。可选任意起点和片段时长，也可处理完整录音。
- **聊天调用**：上传演唱音频后，可以直接要求“小夏，用你的声音唱这个，完整唱”或“只唱从 56 秒开始的 10 秒”。完整作品通过音频附件交付。
- **独立复用**：命令行和本机 HTTP API 都能听、说、唱，不必通过聊天软件。

使用同一 QQ 参考，已经生成过 10.54 秒京剧选段、30 秒昆曲选段、183 秒黄梅戏、155 秒越剧，以及 7 分 21 秒带伴奏歌曲。**这证明了完整处理链路；尚不代表各剧种、各种唱腔和音色自然度已经全部达标。** 自动查找网上音源受站点可访问性影响，不能保证只报曲名就一定能获取音源。当前最稳定的输入是本地文件或聊天音频附件。

完整目标见 [GOAL.md](GOAL.md)，本仓库上传不等于 Goal 完成。

## 安装和启动

目前实际运行平台为 Windows 11、NVIDIA GPU。验证机器为 RTX 5070 Ti 16 GB；模型约 15 GiB，另需 Python 环境和生成音频空间。其他硬件性能尚未验证。

准备 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 [Node.js](https://nodejs.org/)，在 PowerShell 执行：

```powershell
git clone https://github.com/xxu29958-jpg/xiaoxia.git
cd xiaoxia
.\setup.ps1
.\start.ps1
```

安装会创建各模型独立的 Python 环境、构建管理界面、生成本机服务密钥，并按清单下载模型。已有模型可通过 `xiaoxia.local.json` 指向现有目录，再用 `setup.ps1 -SkipModels` 安装程序。可配置项和默认路径见 [配置说明](docs/configuration.md)。

打开 `http://127.0.0.1:6185`，在 AstrBot 管理界面配置自己的聊天模型、QQ／微信账号和“小夏管理中心”中的本人会话。安装程序已为新安装接好本地语音识别和语音回复。聊天模型和平台登录凭据需要各自配置，仓库不包含任何现成账号。

本机服务端口：管理界面 `6185`，统一语音 API `6190`，说话引擎 `6191`，演唱引擎 `6192`，记忆向量服务 `11435`。

```powershell
.\.venvs\bot\Scripts\python.exe xiaoxia.py status
.\.venvs\bot\Scripts\python.exe xiaoxia.py stop voice
.\start.ps1 voice
```

运行日志位于 `.runtime/logs`。聊天数据位于 `data`，模型位于 `models`，本机配置、密钥、聊天记录和生成音频均不提交到 Git。

## 直接听、说、唱

```powershell
# 说话
.\.venvs\bot\Scripts\python.exe xiaoxia.py speak "嗯，我听着呢。" --output outputs\说话.wav

# 听懂一条录音
.\.venvs\bot\Scripts\python.exe xiaoxia.py hear 我的录音.wav

# 保留整首作品及伴奏
.\.venvs\bot\Scripts\python.exe xiaoxia.py sing 演唱音源.mp3 --output outputs\整首.wav

# 只唱指定片段；清唱输入可以跳过伴奏分离
.\.venvs\bot\Scripts\python.exe xiaoxia.py sing 京剧清唱.wav --acapella --start 56.1 --duration 10.54 --output outputs\片段.wav
```

不指定 `--duration` 时，保留从起点到录音结尾的全部时长，不会自动缩成短样本。转换采用有重叠的分段处理；生成长曲期间，日常说话会在分段间优先使用 GPU。

[HTTP API](docs/voice-api.md) 可直接用于桌宠。`/cover` 需要演唱音源；它不是仅凭歌词生成任意现有歌曲的作曲模型。附带的简谱合成能力只适用于短句实验，不替代真实歌曲／戏曲的演唱流程。

## 开发和检查

```powershell
uv pip install --python .venvs\bot\Scripts\python.exe pytest pytest-asyncio ruff
.\.venvs\bot\Scripts\python.exe tools\download_models.py --verify
.\.venvs\bot\Scripts\python.exe -m pytest maintenance\tests\test_ada_local_voice.py -q
```

音频编码测试使用 PATH 上的 FFmpeg 或安装时附带的 FFmpeg。实际使用采用本机配置中的编码器路径。

上游项目、模型与资源来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。保留各自许可证；主程序沿用 AstrBot 的 AGPL-3.0，模型权重的许可分别适用。
