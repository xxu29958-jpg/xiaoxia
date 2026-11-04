# 阅读来源、续读与恢复边界

本协议说明 v1.3 的 M1／M5 阅读切片；完整交付状态只在 [交付台账](delivery-v1.3.md) 维护。
来源、范围和个人判断由实际消费者携带，不能从文件存在或模型回答流畅反推“已经读完”。

## 权威与消费者

沿用 `web_backpack/notebook.sqlite3`。`reading_sources` 保存按主人作用域隔离的不可变来源快照，
`reading_ranges` 保存工具边界准备过的文本区间；原 `visits`、`notes`、`bookmarks` 保存对应来源引用。
没有另造一份主人事实库。`InterestMaterials` 从原生笔记读材料，`ContextBuilder` 将其作为带来源的阅读素材，
与原生 MemoryStore 的个人事实、工作事项和共同经历区分。

消费链为：公开浏览器／RSS／B 站字幕 → 原生笔记库 → 实际注册的 `ada_web_notes` → 提示和主动材料。
`ada_web_notes(action="read")` 先执行与公开浏览相同的工具隔离，移除本轮私人和游戏操作工具。
同一已绑定主人的 QQ／微信共享作用域；模型不能用参数指定另一个主人。

## 身份、范围与引用

- `source_id` 绑定材料 URL、实际取材 URL、种类、捕获文本 SHA-256、上游长度及截断标志。
  同 URL 内容或截断状态改变产生新版本；重读相同版本复用原记录和首次捕获时间。
- `web_dom` 是浏览器当时取得的 DOM 文本；`rss_entry` 是发布者的 RSS 条目；
  `bilibili_subtitles` 是取得的字幕片段；旧接口仅能标为 `captured_excerpt`。
  RSS／字幕不是文章全文、完整视频、观看或聆听证据。
- 单来源最多捕获 200,000 个 Unicode 码点，一次最多返回 24,000 个；浏览器首次返回 24,000，
  RSS 选材实际传给模型 4,000。内部来源缓存不随首次工具结果全部暴露。
  `captured_chars`、`source_total_chars` 和 `capture_incomplete` 明确保留截断。
- `provided_range` 是半开区间 `[start,end)`，单位为 Unicode 码点，浏览器和 Python 均按此计数。
  `range_evidence=prepared_for_consumer` 说明在消费者边界准备文本；
  `current_model_context=unknown` 不冒充模型已收到、理解或在当前上下文仍保有全部文本。
  提交后进程退出可能早于工具送达；不能据准备记录宣称读完。
- `source_quote` 必须逐字存在于相应 visit 实际准备的片段，`quote_range` 固定其位置。
  `comment` 是自身理解，`next_step` 是未解问题；两者不充当来源原话或自动调度承诺。

## 续读与版本变化

`ada_web_notes` 的 `read` 可用 `source_id`，或既有 `item_id`／`page_id` 定位来源。
默认 `offset=-1` 从连续已准备区间后的第一处缺口继续；显式非负 offset 可重新读任意已捕获位置。
默认 limit 为 4,000。失败、重启或上下文变化后应按需要重读，不能把默认游标当作理解进度。
`next_offset=null` 仅表示本次到达已捕获快照末尾；上游截断时仍不能称全文结束。

返回的 page_id 可以保存新引文，且始终引用同一冻结版本。检查网站更新需要另外打开 URL，
不会静默用新网页内容替换旧引文。来源不存在、所属作用域不符或无旧版范围时明确失败，不联网拼造旧文本。
同 URL 同类型的笔记更新仍遵循原有 upsert 语义；需要同时保留旧版时保留旧笔记／书签或导出。

## 原子性、上限与恢复

捕获来源、合并区间和 visit 在同一 SQLite 事务提交。保存笔记／书签时在写事务内确认对应来源仍存在。
重放和重叠区间合并，不无限追加重复范围。保持原有笔记／书签和 visit 上限；
每主人额外仅保留最近 30 个未被笔记或书签引用的来源。被引用的来源不会因浏览缓存过期而消失。
删除引用后，来源在后续缓存回收时可被清理；这不构成全状态隐私清除能力的完成证明。

旧库升级前对非空笔记库做 SQLite 一致性备份，名称为 `notebook.before-reading-v1-<id>.sqlite3`。
新增表及 reading 列在同一事务中建立。旧资料保留 `legacy_unknown/coverage=unknown`，
不会补造阅读时间、来源版本或已读范围；再次打开原址建立的是新版本。
重复启动不会在已完成迁移的库上再次生成升级备份。

原导出格式升为 version 2，包含主人范围内的来源快照、区间及笔记引用；所有表从同一读事务快照导出。
原始 SQLite 文件的一致性备份／搬迁可以保持续读；JSON 导入接口不在本切片中提供。
恢复旧程序时须使用迁移前数据库备份；旧程序不应直接向新 schema 使用无列名 INSERT。

## 验证入口和限制

先按仓库锁文件准备独立浏览器工具环境：

```powershell
npm ci --prefix maintenance/browser --ignore-scripts --no-audit --no-fund
.venvs/quality/Scripts/python.exe -B -m tools.quality.gate --base <base-commit> --bot-python <bot-python.exe>
```

阅读消费者检查要求 bot 环境的 Python Playwright、本机 Chrome 及锁定的 MCP 包可用；缺失直接失败。
全部运行在临时 ASTRBOT_ROOT，实际执行 Chrome DOM、MCP、原生工具注册／执行器和 SQLite。
覆盖隐藏尾部引用拒绝、Unicode 长页续读、版本变更、缓存回收、迁移备份、故障回滚、提交前后进程强杀、
双实例重放、导出并发快照和恢复搬迁，并回归 RSS、B 站、主动材料及原有浏览消费者。

真实模型验收独立于快速门：使用当前配置 gpt-6.1-sol、原生 ContextBuilder 和工具循环，
固定合成材料及四项预定标准，分别执行续读保存、新进程微信身份承接、同址更正和来源缺失。
每次启动独立进程，保留原始提示、回复、工具调用、数据读回和耗时。没有平台适配器或外部渠道发送。
这些证据支持此阅读切片，不替代长期自然交流、真实 QQ／微信手机体验、完整文章／视频理解、
主动联系时机、完整 M1／M5 或 G3 人工验收。
