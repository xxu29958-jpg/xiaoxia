# v1.3 工程基线

规则与验收范围来自
[`v1.3` 主合同](../docs/reference/v1.3/contract/Xiaoxia_Project_Contract_v1.3.md)。
`policy.json` 保存本次采用的统计口径和来源，不能把历史存量当成已经批准的例外。

FM-01～08 补充见 [文件地图与 CI 合同](../docs/file-map-ci-contract-v1.3.md)。
`.github/workflows/quality.yml` 对 PR 运行影响计划、G1、按消费者选择的 G2、固定源码度量和候选源码归档。
单一 `Required quality result` 汇总真实状态；依赖失败、跳过的必需检查和未执行测试均不能算全部通过。
每周计划和手动 `full` 执行全部已登记集成组；范围以报告为准，不能冒充 GPU／真实渠道／人工体验的 G3。
源码候选制品带 commit、tree 和 SHA-256，不表示生产部署。当前执行证据与剩余工作只在施工台账维护。

云端只在 plan 作业计算一次影响图，发布实际计划文件的 SHA-256，再把同一运行的制品交给 G1／G2。
执行器核对摘要、HEAD、base、完整变更清单和候选字节哈希，G2 还核对本组确在计划内；执行后再次核对候选身份。
显式传入的计划缺失、损坏或过期时直接失败并保存错误回执，不静默换成空集合或丢弃地图缺口。
每份执行回执的 `impact_plan` 记录来源、摘要和处理耗时。消费者仍独立运行原有检查，定期全量／增量核对保持原范围。

```powershell
# 云端使用相同入口；本地 BASE_SHA 指定明确基线。
$env:BASE_SHA = '<base-commit>'
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci plan --report out/plan.json
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci check --tier g1 --report out/g1.json

# 本地省略 --plan 时现场重算；明确复用自己刚生成的计划时，同时传入摘要。
# 之后编辑、改名、增加文件或改变提交／基线，需要重新生成计划。
$taskPlanSha = (Get-FileHash -Algorithm SHA256 out/plan.json).Hash.ToLowerInvariant()
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci check --tier g1 --report out/g1.json `
  --plan out/plan.json --plan-sha256 $taskPlanSha

# 同一结果包含文件地图、分类总量、复杂度、热点、差分与规则/工具指纹。
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci sources --cache out/upstreams --report out/sources.json
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --base <base-commit> --upstream-cache out/upstreams
# 仅有 AstrBot 原始对象时也可部分对照，其余外部来源明确保留未核验状态。
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --base <base-commit> --upstream-repo <upstream-checkout>
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --consumers <relative-path> --upstream-repo <upstream-checkout>
```

统计从 Git blob 读取所有文本，包括 SQL、扩展名为空的文件及 UTF-16 BOM 文本。
分类按来源与用途组成互斥桶，逐桶相加等于 Repository Total；用途、语言和领域是独立维度，不跨维度相加。
First-party Production Total 表示当前承担维护的生产源码，不表示独占原创；包含经固定源对照确认有变化的 vendor 生产文件。
工具、测试、配置、schema、文档、原样 vendor 及与固定上游逐 blob 一致的文件分别报告。
FM-4 将各层独立 `scripts/` 内的可执行评测／运维脚本按工具用途统计，避免插件脚本因不在仓库根目录而混入生产总量。
产品 `skills/` 包内由技能调用的脚本仍是生产代码；这些包由原生 SkillManager 发现，入口由各 `SKILL.md` 声明。
测试／配置／schema 的优先分类不变；`core/tools/` 等运行时工具模块也仍属生产代码，来源和本地维护责任不因用途变化而豁免。
生产文件移动到测试或脚本用途时触发维护责任审查；所有可执行源码的规模、复杂度及改名追踪门禁保持原标准。
`ci sources` 只取得固定 Git 树对象，不安装上游程序；度量入口本身只读。未准备相应对象时保留未核验状态，不能将目录来源当作豁免。
对照版本与选择依据随报告保存，特别是无对应发布标签的插件，不把与固定版本的差异冒称作者身份或原始下载历史。
物理行拆成代码、纯注释、空行和非代码内容；混合代码/注释行计为代码，Python 文档串计为注释。
固定 Ruff 的零阈值采集报告所有具名 Python 函数的 C901，实际审查阈值保持原合同不变。
固定基线与候选用同一规则测量，未变 blob 的度量可复用；依赖关系和消费者从当前源码重建。
动态计算导入、无法唯一解析的资源及未登记消费者保留地图缺口；选测会扩展，未映射的变更不能静默通过。
TypeScript 5.1.6／Vue SFC 3.3.4 从输入文本解析导入和符号，不执行 Vite 或产品代码；实际别名从 Vite AST 取得。
前端组沿既有 pnpm 10.15.1 与锁文件安装，运行 vue-tsc 和多语言键检查；动态翻译键与页面旅程不因此变成已覆盖。
同一报告的 architecture 部分保存依赖环、跨域边、扇入／扇出、重复规则候选、多写入者候选和三个代表改动的消费者路径。
源码图与影响选测保留类型引用；门禁另报告 `new_source_cycles` 与阻断性的运行时 `new_cycles`。
FM-3 另从 Python 顶层字面量容器、直接读写、别名导入／再导出及注册工厂调用提取共享状态影响。
`business_dependencies`／`business_consumers` 与源码导入分列；消费者查询、选测和热点使用两者的并集，导入环检查仍只用源码图。
`shared_state_accesses` 保存容器权威路径、符号、读写行和工厂调用；查询给出每步是 source 还是 shared_state。
这是一份容器级、未区分键的可能影响图，保留测试中的写入者，不证明所有适配器同时激活或所有消费者都需要修改。
参数同名、重绑定和直接别名逃逸会留下缺口；受影响容器的未知消费者即使没有可靠连边，也会触发扩展检查。
任意对象字段、函数返回值别名、动态补丁和运行时激活仍不能仅靠这份静态分析判定，不能据此宣称完整调用图。
基线和候选均用 FM-4 重测；旧 FM-2／FM-3 报告保留，规则修正导致的归类变化与源码增删分开说明。
新发现的边与真实代码耦合变化分开理解；FM-4 保留 FM-3 的共享状态分析口径。
只有已确认由 `typing` 导入且未重绑定的 `TYPE_CHECKING` 分支才不算运行时边；其 else、未知条件和普通导入仍参与阻断。
跨层禁止规则继续检查全部源码依赖。此区分依据 [Python TYPE_CHECKING 语义](https://docs.python.org/3.12/library/typing.html#typing.TYPE_CHECKING)，不表示消除了组合类的耦合。
重复语法、同名表或内部路径只构成调查候选，不直接判定业务规则重复或多头权威。
历史沿 Git 识别到的改名追踪，差分同时保留原／新路径、绝对规模、增删量和函数／接口／依赖／例外变化。
局部规模例外绑定 AST 指纹、上限、责任人、验证、退出条件、复查日期及不可删的历史；排版缩短和改名不能抹掉原审查记录。
`--previous-report <json>` 可复用工具与规则相同的文件度量；`--query-report <json> --consumers <path>` 直接查询已有结果。
`main` 的必需检查为 GitHub Actions 的 `Required quality result`，要求与主干同步且管理员同样受约束。
周全量／选测引擎变更运行全部已登记组，并对照普通选测集合；缺失执行、失败或范围外失败都有单独字段，不冒充未登记旅程已经验证。

## 独立环境

CI 固定 Python 3.12.13（语音类型／信号组为 3.11.15）与 Node 24.15.0；报告另记录实际 Git 版本。
Windows 组用固定 uv 0.11.7 的独立 Python 构建创建隔离环境；不依赖 setup-python 是否发布该补丁的 Windows 安装器。
使用相同运行时和 uv，在仓库根目录执行：

```powershell
uv venv .venvs/quality --python 3.12.13
uv pip install --python .venvs/quality/Scripts/python.exe -r requirements/quality.txt
npm ci --prefix quality --ignore-scripts
```

质量环境不加载 GPU、不启动生产服务、不读取聊天数据库。HTTP 契约使用临时状态目录和回环测试端口。
`registration_contracts` 在隔离 bot 环境运行实际 OAuth 插件注册／命令契约，以及原生 ProviderManager／PlatformManager 对本地测试适配器的加载、分发和终止。
注册图本身仍从源码只读采集；这些原生测试不连接外部模型或聊天渠道，也不代替真实模型质量、原生 QQ／微信收发或手机体验。
主程序和独立声音引擎继续使用各自环境；缺少匹配解释器时类型检查失败，不换成缺包环境或忽略导入错误。
认知组的原生 unittest 批次预算为 180 秒，包含既有聊天与记忆消费者的完整 pytest 批次预算为 300 秒；
这是执行器的整批预算，不替代用例内部时限或产品响应 SLO。pytest 即时记录各阶段耗时，单用例超过 60 秒输出诊断栈。
超时仍为 incomplete／失败；具体预算修订依据及历史失败保留在唯一施工台账。

## 当前可执行入口

```powershell
# 历史静态入口：--base 使用分支起点／PR 的基线提交。
# 包含已提交、暂存、未暂存及 Git 未忽略的新文件；返回 0 通过、1 不满足、2 工具错误。
# 它仍按旧路径映射检查，前端变更会拒绝；FM 消费者选测和实际执行报告使用上面的 ci plan/check。
.venvs/quality/Scripts/python.exe -B -m tools.quality.gate --base <change-base-commit>

# 涉及声音服务／信号处理时，必须提供各自已安装的 Python 3.11 环境。
# 新建环境或本机已有环境均可；检查只读，不会向它们安装依赖。
.venvs/quality/Scripts/python.exe -B -m tools.quality.gate --base <change-base-commit> `
  --gateway-python <gateway-python.exe> --singing-python <singing-python.exe>

# 涉及聊天消费者时，提供已安装的主程序 Python 3.12 环境。
# 子进程使用临时 ASTRBOT_ROOT、测试配置及回环网关，不打开运行目录的聊天数据库。
.venvs/quality/Scripts/python.exe -B -m tools.quality.gate --base <change-base-commit> `
  --bot-python <bot-python.exe>

# stdout 为 JSON。固定提交，不包含未提交文件；重定向到选定的证据目录。
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD

# 有已经取得的 AstrBot 上游 Git 对象时可作精确内容对照：
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --upstream-repo <original-checkout>

# 采集器自己的反例与只读验证
.venvs/quality/Scripts/python.exe -B -m unittest discover -s quality/tests -v
.venvs/quality/Scripts/ruff.exe format --check tools/quality quality/tests
.venvs/quality/Scripts/ruff.exe check --no-cache tools/quality quality/tests
```

采集器使用 Git、固定 Ruff 和 Pygments 内置词法器，不导入被测源码。成功完成采集退出 0；输入、Git 对象或 I/O
失败退出非零。报告内的历史超线与语法问题是调查结果，不能用采集退出 0 宣称仓库合格。
省略上游 checkout 时不会赋予任何上游规模豁免。该参数不触发联网、下载或改写 checkout。

## 统计口径和边界

- 从提交树读取路径、Git blob、字节大小；UTF-8 物理行包含空行和注释，末尾换行不增行。
- Python 函数、async 函数和类从首个装饰器／定义至末行；嵌套符号单列，跨度不相加作仓库总行数。
- 控制嵌套统计 if/for/while/with/try/match 的 AST 层级；elif 同级，嵌套定义重新计数。
- 复杂度单独使用固定版本 Ruff C901；不以自编近似公式代替 McCabe。
- 文件用途与来源／维护责任分列。只有与指定上游路径的 blob 完全一致才记为上游未修改；vendor 和插件差分尚未核实时继续纳入检查，不冒称原创。
- 生成文件的来源、生成器、重建命令核验完成前没有规模豁免。前端测量物理行、代码行、字节及 AST 符号跨度；JS/TS 的 McCabe 复杂度仍未测量，不能计作零。
- 不读取被忽略的运行资产；它们的容量、生命周期和恢复属于单独环境基线。
- inventory 输出单个固定提交的存量；gate 另比较变更前后。文件改名沿用前身，函数单纯改名按 AST 内容匹配；生产代码搬入测试目录需显式审查。Git 未跟踪目的文件的相同内容移动也会配对，文本配对允许 Git 的 CRLF/LF 转换，候选证据仍保存实际字节哈希。
- 规模与复杂度的新增／恶化审查点阻断；原有未恶化记录保留。lint、format 和类型检查要求被修改的 Python 文件通过。局部规模例外必须在 policy.json 精确登记并通过指纹、上限、期限与历史检查，不能由存量违规自动生成。
- 源码依赖检查覆盖可静态解析的 Python，包括插件／测试／vendor。具名规则禁止 worker 依赖渠道、质量工具导入运行时等；新边形成环时报告具体路径。动态插件注册与共享数据库语义仍由运行契约验证。
- Ruff 固定 0.15.22；Pyright 固定 1.1.414 并由 npm lock 重建。主程序按 3.12 检查；共享服务与语音代码兼容 3.11，memory worker 按 3.12。Pyright 配置只在根 `pyrightconfig.json` 维护，不再保留冲突的 pyproject 配置。
- 密钥扫描只报告路径／规则／行号，不输出匹配值；当前覆盖私钥头、GitHub token、OpenAI 风格 key 与运行凭据文件名，不宣称能识别所有秘密。发现疑似密钥后不再执行可能回显源内容的 lint/type/test 命令。
- 引用包单独按哈希和完整文件集合验证；额外塞入源码不能借该路径逃过检查。新增／增长到 10 MiB 以上资产需审查。
- 自动消费者映射以 test_selection.py 的单一登记表为准，包括质量、安装、前端、媒体作业、信号处理、演出、认知和阅读组，并沿源码依赖扩展。Fish 安装／模型选择覆盖标准库契约、PowerShell 语法及生成锁文件的输入／输出哈希。未映射变更仍阻断；空环境完整安装、真实模型导入、GPU 服务和产品旅程分别保留环境证据，不能由这些快速检查推断通过。
- 媒体任务映射覆盖真实 SQLite／进程强杀、HTTP 控制与回环 RPC、WAV 读回；信号处理映射使用演唱环境中的 librosa／soundfile 和确定性转换器，覆盖时长、重叠、停顿、尾音、取消及真实 yt-dlp 下载。
- 聊天演出消费者映射覆盖真实插件工具、HTTP 网关、完整 61 秒 MP3 编码与读回、跨入口查询／取消、渠道回执丢失、重启，以及真实进程强杀发生在外部回环接收后、本地回执事务提交前后的两个窗口。渠道使用测试接收器，不发送外部消息；这些证据不代替真实模型、QQ／微信原生收发、手机体验或人工听审。相关旧语音、对话、工具生成器与 MCP 返回值消费者一并回归。
- 共享核心记忆映射使用临时 ASTRBOT_ROOT、真实 AstrMessageEvent 与原生 MemoryStore，覆盖跨渠道当前值／来源历史、原子回滚、双连接 CAS、消息重放、提交前后强杀、旧 schema 备份迁移及来源撤销。旧记忆、纠错、来源采集和聊天消费者同时回归；详见 [事务边界](../docs/core-memory-transactions.md)。复杂度入口在 Windows 写入 Ruff stdin 前归一换行，CRLF 的诊断位置有真实命令级反例。
- gate 的 JSON 保留基线、HEAD、候选文件哈希、工具退出码和结果；源码在运行期间发生变化时结果作废。命令本身不生成工作树缓存或改写文件，输出由调用者重定向到证据目录。
- 工作事项映射覆盖原生目标／话题、全部实际读写消费者、旧 JSON 原子迁移、重放、取消及关闭屏障、进程强杀、备份恢复搬迁和私人／全量清除。管理组件使用 bot 环境已安装的 Python Playwright 与本机 Chrome 无头模式，执行真实页面脚本和原生存储读回；缺少浏览器直接失败，不记为跳过。详见 [工作状态边界](../docs/working-state-transactions.md)。
- 阅读映射覆盖来源／范围事务、旧库迁移、版本变化、进程强杀、恢复搬迁、原生工具注册和 QQ／微信身份隔离，以及 RSS、字幕、主动材料和浏览器消费者。需先执行 `npm ci --prefix maintenance/browser --ignore-scripts --no-audit --no-fund`，安装锁定的 MCP 包；测试实际运行 Chrome／MCP，缺包或缺浏览器均失败，不用跳过代替。真实 LLM 四阶段旅程在快速门外保留独立候选证据，详见 [阅读边界](../docs/reading-evidence.md)。

工具和规则变化后，重新测同一原始提交，保留旧证据，避免把计数器变化误认成产品改善。

事件理解映射沿 cognition 检查覆盖原生解释／修订／操作回执、来源撤回、跨端回复与提示、主动候选版本复核、
强杀及备份恢复。真实模型的旧／新／空状态对照另外保留，不能将受控状态影响当作完整认知或聊天体验通过；
详见 [事件理解边界](../docs/event-understanding.md)。
