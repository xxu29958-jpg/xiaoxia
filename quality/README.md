# v1.3 工程基线

当前状态：EQ1 基线采集和 EQ2 静态变更入口已实现；完整 G1 的消费者／前端映射及 CI、G2、G3 仍在推进。规则来自
[`v1.3` 主合同](../docs/reference/v1.3/contract/Xiaoxia_Project_Contract_v1.3.md)。
`policy.json` 保存本次采用的统计口径和来源，不能把历史存量当成已经批准的例外。

## 独立环境

使用 Python 3.12 和 uv，在仓库根目录执行：

```powershell
uv venv .venvs/quality --python 3.12.13
uv pip install --python .venvs/quality/Scripts/python.exe -r requirements/quality.txt
npm ci --prefix quality --ignore-scripts
```

质量环境不加载 GPU、不启动服务、不读取聊天数据库。主程序和独立声音引擎继续使用各自环境。

## 当前可执行入口

```powershell
# 每次变更的静态检查：--base 使用分支起点／PR 的基线提交。
# 包含已提交、暂存、未暂存及 Git 未忽略的新文件；返回 0 通过、1 不满足、2 工具错误。
.venvs/quality/Scripts/python.exe -B -m tools.quality.gate --base <change-base-commit>

# stdout 为 JSON。固定提交，不包含未提交文件；重定向到选定的证据目录。
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD

# 有已经取得的 AstrBot 上游 Git 对象时可作精确内容对照：
.venvs/quality/Scripts/python.exe -B -m tools.quality.inventory --revision HEAD --upstream-repo <original-checkout>

# 采集器自己的反例与只读验证
.venvs/quality/Scripts/python.exe -B -m unittest discover -s quality/tests -v
.venvs/quality/Scripts/ruff.exe format --check tools/quality quality/tests
.venvs/quality/Scripts/ruff.exe check --no-cache tools/quality quality/tests
```

采集器仅使用 Python 标准库和 Git，成功完成采集退出 0；输入、Git 对象或 I/O
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
- gate 的 JSON 保留基线、HEAD、候选文件哈希、工具退出码和结果；源码在运行期间发生变化时结果作废。命令本身不生成工作树缓存或改写文件，输出由调用者重定向到证据目录。

工具和规则变化后，重新测同一原始提交，保留旧证据，避免把计数器变化误认成产品改善。
