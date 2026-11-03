# v1.3 工程基线

当前状态：EQ1 基线采集已实现；完整 G1、G2、G3 尚未接通。规则来自
[`v1.3` 主合同](../docs/reference/v1.3/contract/Xiaoxia_Project_Contract_v1.3.md)。
`policy.json` 保存本次采用的统计口径和来源，不能把历史存量当成已经批准的例外。

## 独立环境

使用 Python 3.12 和 uv，在仓库根目录执行：

```powershell
uv venv .venvs/quality --python 3.12.13
uv pip install --python .venvs/quality/Scripts/python.exe -r requirements/quality.txt
```

质量环境不加载 GPU、不启动服务、不读取聊天数据库。主程序和独立声音引擎继续使用各自环境。

## 当前可执行入口

```powershell
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
- 当前输出是单个固定提交的存量。新增／恶化／改名追踪、导入禁止规则、类型检查、密钥扫描和完整 G1 集成属于 EQ2，尚未完成。

工具和规则变化后，重新测同一原始提交，保留旧证据，避免把计数器变化误认成产品改善。
