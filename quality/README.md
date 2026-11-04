# v1.3 工程基线

规则与验收范围来自
[`v1.3` 主合同](../docs/reference/v1.3/contract/Xiaoxia_Project_Contract_v1.3.md)。
`policy.json` 保存本次采用的统计口径和来源，不能把历史存量当成已经批准的例外。

FM-01～08 补充见 [文件地图与 CI 合同](../docs/file-map-ci-contract-v1.3.md)。
`.github/workflows/quality.yml` 对 PR 运行影响计划、G1、按消费者选择的 G2、固定源码度量和候选源码归档。
单一 `Required quality result` 汇总真实状态；依赖失败、跳过的必需检查和未执行测试均不能算全部通过。
每周计划和手动 `full` 执行全部已登记集成组；范围以报告为准，不能冒充 GPU／真实渠道／人工体验的 G3。
源码候选制品带 commit、tree 和 SHA-256，不表示生产部署。当前执行证据与剩余工作只在施工台账维护。

```powershell
# 云端使用相同入口；本地 BASE_SHA 指定明确基线。
$env:BASE_SHA = '<base-commit>'
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci plan --report out/plan.json
.venvs/quality/Scripts/python.exe -B -m tools.quality.ci check --tier g1 --report out/g1.json

# 同一结果包含文件地图、分类总量、复杂度、热点、差分与规则/工具指纹。
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --base <base-commit> --upstream-repo <upstream-checkout>
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --consumers <relative-path> --upstream-repo <upstream-checkout>
```

统计从 Git blob 读取所有文本，包括 SQL、扩展名为空的文件及 UTF-16 BOM 文本。
分类按来源与用途组成互斥桶，逐桶相加等于 Repository Total；用途、语言和领域是独立维度，不跨维度相加。
First-party Production Total 表示当前承担维护的生产源码，不表示独占原创；工具、测试、配置、schema、文档、vendor
以及与固定上游逐 blob 一致的文件分别报告。来源未固定的外部插件标为 upstream_unverified，继续承担本地检查责任。
物理行拆成代码、纯注释、空行和非代码内容；混合代码/注释行计为代码，Python 文档串计为注释。
固定 Ruff 的零阈值采集报告所有具名 Python 函数的 C901，实际审查阈值保持原合同不变。
固定基线与候选用同一规则测量，未变 blob 的度量可复用；依赖关系和消费者从当前源码重建。
动态计算导入、无法唯一解析的资源及未登记消费者保留地图缺口；选测会扩展，未映射的变更不能静默通过。

## 独立环境

使用 Python 3.12 和 uv，在仓库根目录执行：

```powershell
uv venv .venvs/quality --python 3.12.13
uv pip install --python .venvs/quality/Scripts/python.exe -r requirements/quality.txt
npm ci --prefix quality --ignore-scripts
```

质量环境不加载 GPU、不启动生产服务、不读取聊天数据库。HTTP 契约使用临时状态目录和回环测试端口。
主程序和独立声音引擎继续使用各自环境；缺少匹配解释器时类型检查失败，不换成缺包环境或忽略导入错误。

## 当前可执行入口

```powershell
# 每次变更的静态检查：--base 使用分支起点／PR 的基线提交。
# 包含已提交、暂存、未暂存及 Git 未忽略的新文件；返回 0 通过、1 不满足、2 工具错误。
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
- 生成文件的来源、生成器、重建命令核验完成前没有规模豁免。前端目前测物理行和字节，尚未测 JS/TS 符号复杂度。
- 不读取被忽略的运行资产；它们的容量、生命周期和恢复属于单独环境基线。
- inventory 输出单个固定提交的存量；gate 另比较变更前后。文件改名沿用前身，函数单纯改名按 AST 内容匹配；生产代码搬入测试目录需显式审查。Git 未跟踪目的文件的相同内容移动也会配对，文本配对允许 Git 的 CRLF/LF 转换，候选证据仍保存实际字节哈希。
- 规模与复杂度的新增／恶化审查点阻断；原有未恶化记录保留。lint、format 和类型检查目前要求被修改的 Python 文件通过，尚未自动批准局部例外。
- 源码依赖检查覆盖可静态解析的 Python，包括插件／测试／vendor。具名规则禁止 worker 依赖渠道、质量工具导入运行时等；新边形成环时报告具体路径。动态插件注册与共享数据库语义仍由运行契约验证。
- Ruff 固定 0.15.22；Pyright 固定 1.1.414 并由 npm lock 重建。主程序按 3.12 检查；共享服务与语音代码兼容 3.11，memory worker 按 3.12。Pyright 配置只在根 `pyrightconfig.json` 维护，不再保留冲突的 pyproject 配置。
- 密钥扫描只报告路径／规则／行号，不输出匹配值；当前覆盖私钥头、GitHub token、OpenAI 风格 key 与运行凭据文件名，不宣称能识别所有秘密。发现疑似密钥后不再执行可能回显源内容的 lint/type/test 命令。
- 引用包单独按哈希和完整文件集合验证；额外塞入源码不能借该路径逃过检查。新增／增长到 10 MiB 以上资产需审查。
- 当前自动消费者映射覆盖质量工具自身，以及 Fish 源码安装／模型选择的标准库契约测试和 PowerShell 语法检查。Fish 生成锁文件的输入／输出哈希也纳入检查。其他业务代码、前端或脚本变化会显示缺少的检查映射并阻断，必须补对应的真实测试，不允许当作自动跳过。空环境依赖安装、真实模型导入、GPU 服务和完整安装旅程分别保留环境证据，不能由这些快速检查推断通过。
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
