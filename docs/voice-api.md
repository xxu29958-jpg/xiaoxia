# 桌宠与其他客户端复用

统一入口 `http://127.0.0.1:6190`。读取本机 `.runtime/voice/service.key`，请求携带 `Authorization: Bearer <key>`。若配置了其他 `voice_state`，密钥位于该目录。密钥只在本机保存。

| 接口 | 输入 | 输出 |
|---|---|---|
| `GET /health` | 无 | 引擎、参考音频摘要、最近请求耗时、忙碌状态 |
| `POST /speech` | JSON `{"text":"嗯，我听着呢。"}` | PCM WAV；响应头含生成耗时和音频时长 |
| `POST /v1/audio/transcriptions` | multipart 字段 `file` | JSON `{"text":"识别结果"}` |
| `POST /cover` | 以下 JSON | 完整演唱 WAV，含伴奏时混回原伴奏 |

说话可传 `{"text":"真来啊？那我等你。","emotion":"happy","emotion_strength":0.08}`。
`emotion` 支持 `neutral`（默认，沿用参考语气）、`happy`、`angry`、`sad`、`afraid`、`disgusted`、`melancholic`、`surprised`、`calm`；强度范围 0–1，默认 0.08。这是轻度情绪控制的待试听起点，不代表自然度已通过验收。音色参考始终保持 QQ 那条 4 秒录音。

聊天中的语气由现有一次回复结合上下文给出，不增加情绪分类模型调用；字段不进入用户文字或长期对话记录。响应头 `X-Voice-Emotion` 返回引擎实际采用的语气；旧服务没有该响应头时不能据此认定情绪控制已生效。

```json
{
  "source_path": "C:/Music/演唱音源.mp3",
  "acapella": false,
  "pitch": 0,
  "start_seconds": 0,
  "duration_seconds": null
}
```

`duration_seconds: null` 处理完整剩余录音；正数选择片段。`acapella: true` 表示已有人声独唱，跳过伴奏分离。`pitch` 是明确要求的人声半音位移，默认为 0，伴奏保持原音高。可用 `source_url` 代替 `source_path`，下载失败会返回 `422 source_unavailable`，客户端可让用户提供音源。

响应头：`X-Voice-Engine`、`X-Generation-Seconds`、`X-Audio-Seconds`；演唱还提供 `X-Result-Path`。演唱目录保留源人声、伴奏、转换人声、成品和实际耗时，便于继续调音。

请求是同步生成：桌宠应异步等待，收到 WAV 后播放。长曲请求不占用日常说话请求锁。底层演唱会在分段之间给日常说话让出 GPU。

说话与演唱引擎的 `/health` 都报告实际加载参考文件的 `reference_sha256`。换参考后重启语音服务，不能只修改显示名称。
