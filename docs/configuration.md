# 本机配置

`xiaoxia.local.json` 是可选的本机配置，不上传 Git。仅填写需要覆盖的项目，其余使用 `services/runtime.py` 中的默认值。所有相对路径以工程根目录为基准。

```json
{
  "download_proxy": "http://127.0.0.1:7892",
  "ffmpeg": "C:/Tools/ffmpeg/bin/ffmpeg.exe",
  "index_models": "D:/Models/IndexTTS-2",
  "seed_models": "D:/Models/seed-vc"
}
```

| 配置 | 默认路径／含义 |
|---|---|
| `bot_root` | `.`；聊天配置、数据库和插件位于其 `data` 子目录 |
| `voice_state` | `.runtime/voice`；服务密钥、生成音频和工作文件 |
| `memory_state` | `.runtime/memory`；向量服务密钥 |
| `voice_profile` | `assets/voices/profile.json`；说话和唱歌共享参考 |
| `index_models` / `index_aux` | `models/index-tts` / `models/index-aux` |
| `seed_models` | `models/seed-vc` |
| `soul_models` | `models/soulx` |
| `separator_models` | `models/separator` |
| `recognition_models` | `models/sensevoice` |
| `memory_models` | `models/e5-small` |
| `python_bot` 等 | `.venvs/<组件>/Scripts/python.exe` |

已有 AstrBot 用户可把 `bot_root` 指向原实例，以继续使用其账号和聊天数据。此时该实例的 `data/plugins` 仍是实际加载的插件目录；迁移插件时应保存自己的改动。新安装使用本仓库的插件。

新安装的 `xiaoxia.py init` 只创建缺失配置，不替换已有账号配置。已有实例需要在“小夏管理中心”配置 `voice_key_file`，并让识别提供商指向 `http://127.0.0.1:6190/v1`。微信发送方式为 MP3 文件，QQ 普通说话为语音消息，长作品为文件。

新安装默认选择 `Ada`。小夏插件首次启动时从 `data/plugins/astrbot_plugin_ada_companion/persona.md` 创建人设；已有的 `Ada` 人设保留原内容，后续在管理界面的“人格设定”中编辑。接入已有 AstrBot 时，可在 Agent Runner 中选择 `Ada`。

聊天模型、QQ／微信登录和本人私聊会话在管理界面配置。主动聊天提供商应选中自己已配置的模型。记忆和表情检索可使用本地 `http://127.0.0.1:11435/v1`，模型名 `ada-e5-small`，密钥读取 `memory_state/local-key.txt`。
