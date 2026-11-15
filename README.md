# Retail Agent Project

## 指标与组合分组

- `amount`（销售额/金额）：正向有效交易的 `Quantity × UnitPrice`，单位英镑。原有总额及国家统计保留符合口径的费用；商品榜不把已确认的非商品费用作为商品排序。
- `quantity`（销量/件数）：通过相同有效交易筛选的 `Quantity` 合计，单位件；排除已确认的非商品费用代码 `POST`、`DOT`、`C2`、`BANK CHARGES`、`AMAZONFEE`。旧计划缺少指标字段时默认金额。
- “卖得最好”默认按金额；明确问“销量最多/件数最高”按件数。若指标和排名依据都不明确，会先询问按金额还是按销量。用户同时要两项指标时，计算、表格、证据和报告保留两列。
- 汇总支持一个分组，或一个时间维度（日、周、月）加一个业务维度（国家、商品）。例如「每周哪个国家销售额最高」会保留每周各国的完整表，并单独生成每周国家金额榜首；「各国每周销量」和「每周金额榜首」是两种不同结果。
- 周按周一至周日、月按自然月；所选日期首尾截断自然周期时标为部分覆盖。比较日期包含首尾两天。
- 金额和件数榜按未舍入值排序；并列第一单独标出。金额展示为英镑两位小数，件数显示“件”，变化率显示两位百分比。

示例：

```powershell
# 德国商品件数榜（金额与销量榜首可能不同）
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "德国销量最多的前 5 个商品是什么？"

# 周 × 国家：每周各国金额排名
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "从提供的数据开始到结束，每周哪个国家销售额最高？"

# 同时保留各国每周销量明细，并另外列出每周金额榜首
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "列出各国每周销量，并指出每周销售额最高的国家"
```

## 环境

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 从零复现

以下命令均在 `E:\retail-agent-project` 项目根目录的 PowerShell 中执行。若尚未准备数据，先运行数据转换脚本。已有 `.venv` 时可直接安装依赖；若没有虚拟环境，先在项目目录执行 `py -3.13 -m venv .venv`：

```powershell
Set-Location E:\retail-agent-project
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.13 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Invoke-WebRequest -Uri "https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip" -OutFile "online+retail.zip"
Expand-Archive -LiteralPath "online+retail.zip" -DestinationPath "." -Force
.\.venv\Scripts\python.exe scripts\prepare_data.py "Online Retail.xlsx" "data\retail_demo.csv"
```

源数据来自 UCI Machine Learning Repository 的 Online Retail 数据集：
https://archive.ics.uci.edu/dataset/352/online+retail

首次下载和解压后，再次复现时只需运行依赖安装及转换命令。CSV 会保存在 `data\retail_demo.csv`。

## 两期销售比较

在项目根目录运行以下验收示例：

```powershell
.\.venv\Scripts\python.exe scripts\analyze_retail.py data\retail_demo.csv 2011-09-19 2011-09-25 2011-09-26 2011-10-02
```

命令依次接收 CSV 路径、前期起止日期和后期起止日期。日期首尾均包含。核心函数 `analyze_periods` 位于 `scripts/retail_analysis.py`，供其他 Python 模块直接调用。

## 统计口径

- 仅纳入 `InvoiceNo` 不以 `C` 开头、`Quantity > 0` 且 `UnitPrice > 0` 的明细。金额按 `Quantity × UnitPrice` 计算，单位为英镑；先汇总，再按两位小数展示。
- 总金额和国家榜包含所有符合条件的费用明细。差额为后期减前期，变化率为差额除以前期金额；前期金额为零时显示“不可计算”。
- 两期比较榜按所选指标的差额排名，只列差额为负的下降项或差额为正的增长项。只在一期出现的项目，另一期按零计算。未指定条数时每榜为 5 项，也可在问题中指定其他条数。
- 商品榜按 `StockCode` 汇总，`Description` 仅作展示。同一代码有多个描述时，取出现最多的描述；并列时按文本排序取首项。
- 以下已确认的非商品费用代码只从商品榜排除，并单独列出两期金额和差额；它们仍计入总金额及国家榜：`POST`（邮费）、`DOT`（Dotcom 邮费）、`C2`（运输费）、`BANK CHARGES`（银行手续费）、`AMAZONFEE`（Amazon 手续费）。其他代码不因描述缺失、非数字格式或名称含“fee”等字样而自动排除。

## 规划智能体

规划智能体和工作流从项目根目录的 `.env` 读取模型配置。`.env.example` 是可提交的无密钥模板；首次配置时只需在本机 `.env` 中填写 `OPENAI_API_KEY`。显式进程环境变量优先于 `.env`，方便临时覆盖配置。

从项目根目录运行规划命令：

```powershell
.\.venv\Scripts\python.exe scripts\plan_analysis.py data\retail_demo.csv "比较 2011 年 9 月 19–25 日和 9 月 26 日–10 月 2 日，列出金额下降最多的 5 个国家和 5 个商品。"
```

规划提示词位于 `prompts/planning_system_zh.txt`，输出由 `scripts/planning_agent.py` 中的 Pydantic `AnalysisPlan` 校验。CSV 仅在本地读取 `InvoiceDate` 以获得可用日期范围；发给模型的上下文仅含用户问题、日期范围和分析所需字段说明，不包含 CSV 行或文件路径。完整日期区间会由代码检查起止顺序和 CSV 范围。日期未明确时会返回一条澄清问题。

### 同济大学 DeepSeek-V4.1-Flash

项目根目录 `.env` 已预填同济 Chat Completions 地址、模型名和 `NO_PROXY`。请勿将 `.env` 提交或分享；每位组员都应在自己的电脑上填写自己的学校 Key。程序会保留已有 `NO_PROXY` 条目并追加 `agi.tongji.edu.cn`。

运行验收问题 A：

```powershell
.\.venv\Scripts\python.exe scripts\plan_analysis.py data\retail_demo.csv "比较 2011 年 9 月 19–25 日和 9 月 26 日–10 月 2 日，列出金额下降最多的 5 个国家和 5 个商品。"
```

运行验收问题 B：

```powershell
.\.venv\Scripts\python.exe scripts\plan_analysis.py data\retail_demo.csv "哪些商品卖得更好了？"
```

`chat_completions` 模式要求显式设置 `OPENAI_BASE_URL`，避免学校 Key 被发送到默认服务地址。该模式优先请求 `response_format={"type":"json_object"}`；只有服务明确拒绝该参数时才重试一次普通文本，并仍使用同一 Pydantic 模型校验。Responses API 保持为默认模式。Key 从本机 `.env` 或进程环境变量读取，不会写入日志或回复；CSV 仍在本地处理，只发送日期范围和字段说明，不发送交易明细。

## LangGraph 规划与计算工作流

在填写好项目根目录 `.env` 的电脑上，从项目根目录运行完整工作流。

验收 A（审核通过时应走 `plan → calculate → draft_report → review → 结束` 并导出文件）：

```powershell
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "比较 2011 年 9 月 19–25 日和 9 月 26 日–10 月 2 日，列出金额下降最多的 5 个国家和 5 个商品。"
```

验收 B（应走 `plan → 结束`，仅询问日期，不计算）：

```powershell
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "哪些商品卖得更好了？"
```

工作流定义在 `scripts/langgraph_workflow.py`。计划含有澄清问题或日期不完整时会直接结束；日期完整时执行 `calculate → draft_report → review`。审核退回后最多返修一次并复核；复核仍退回时结束且不产生最终报告。只有审核通过的结构化报告保存在 `final_report` 中。

报告结论正文只负责解释，不得自行写金额或百分比；报告数据摘要、两张榜单和证据表由代码直接从计算结果渲染，英镑和百分比都保留两位小数，不改变内部计算精度。证据编号从本次结果生成，每条报告结论需引用有效编号。审核会检查证据是否真正支持结论，包括排名、用户指定维度/方向和未经验证的因果归因。报告初稿、审核意见、计算结果和证据表都保存在 LangGraph 状态中，供后续审核流程扩展使用。CSV 明细仅在本地计算节点读取。

审核通过后，CLI 会在 `outputs\run_<UTC时间>_<随机后缀>\` 中生成：

- `report.md`：包含日期、统计口径、两期金额、差额、变化率、用户要求的榜单、审核通过结论、证据编号及同目录图表引用。
- `period_comparison.png`：由本次工作流状态中的计算结果绘制的图表；两期比较是柱状图，分组排名和时间趋势按对应指标绘制，使用 Matplotlib Agg 无界面后端。导出不会重新计算或调用模型。

只有审核通过且存在 `final_report` 时才会创建输出目录。澄清路径和审核两次仍退回的路径不会生成文件。CLI 会打印两份文件的完整路径。

## Windows 一键启动

依赖和演示 CSV 准备好后，每位组员在自己的电脑上完成两步：

1. 用记事本打开 `E:\retail-agent-project\.env`，填写 `OPENAI_API_KEY=` 后面的个人 Key 并保存。如果首次克隆后没有 `.env`，先将 `.env.example` 复制并命名为 `.env`。不要分享或提交 `.env`；每位组员填写自己的 Key。
2. 双击 `E:\retail-agent-project\start_web.bat`。它会切换到项目目录、启动网页并打开 `http://127.0.0.1:8501`。使用期间保持终端窗口打开。

从 PowerShell 手动启动时：

```powershell
Set-Location E:\retail-agent-project
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
$env:STREAMLIT_SERVER_SHOW_EMAIL_PROMPT = "false"
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

网页默认读取 `data\retail_demo.csv`，也可上传具有相同七个字段的 CSV；上传内容在本机临时目录校验和处理，工作流结束后自动清理。只有点击“运行分析”才会调用模型。切换榜单、展开证据和下载文件只读取本次会话保存的结果。

案例 A（增长榜）：

```text
比较 2011 年 9 月 12–18 日和 9 月 19–25 日，列出金额增长最多的 5 个国家。
```

案例 B（日期澄清）：

```text
哪些商品卖得更好了？
```

日期完整且审核通过时，页面展示金额、用户要求的榜单、审核结论和证据，可下载本次的 `report.md` 与 `period_comparison.png`。下载文件同时保存在 `outputs\run_<UTC时间>_<随机后缀>\`。日期不完整时仅显示规划智能体的澄清问题；复核仍未通过时显示审核意见，不会提供报告下载。网页与 CLI 共用 `scripts/langgraph_workflow.py` 和 `scripts/report_exporter.py`，金额口径与导出格式一致。CSV 明细留在本地，API Key 只从现有进程环境变量读取。

### 多轮追问

在同一网页会话、同一份 CSV 下，先提交完整问题，再直接输入修改条件：

```text
德国销量最高的 5 个商品是什么？
那英国呢？
改按销售额排。
只看前 3。
```

每轮只更改明确提出的国家、排名指标或条数，其余条件继承最近一次审核通过的计划。日期澄清也可直接回复，例如先问「哪些商品卖得更好了？」，再回复「2011 年 9 月 19–25 日和 9 月 26 日–10 月 2 日」；系统沿用商品增长、金额排名和前 5 条件，只补日期。每轮都需点击“运行分析”后才执行工作流；切换榜单、查看证据和下载不会触发模型调用。

“开始新分析”会清除当前会话中待澄清内容和可继承计划。切换数据源也会清除它们及当前结果；数据身份由来源类型、文件名和文件内容 SHA-256 共同确定，同名但内容不同的 CSV 会被视为新数据源。页面会显示本轮最新输入和本次实际采用的条件。

### 基于上轮榜单下钻

同一网页会话和同一份 CSV 下，系统可以从最近一次审核通过的结构化榜单中解析实体，再按新问题重新汇总：

```text
德国销售额最高的 5 个商品是什么？
这个商品在德国每周卖了多少件？
```

若榜单第一名并列或存在多个可引用榜单，页面会列出商品编号/国家及必要的分期信息；回复候选商品编号或国家名即可继续，例如 `21498`。引用结果会保留相关日期和筛选，并采用本轮明确提出的新指标、分组和排名要求。比较榜单也可引用“下降最多的国家”或“增长最多的商品”。

“开始新分析”或切换 CSV 后会清除可引用榜单；此时输入“这个商品”会先要求明确商品编号或国家。新问题可以照常独立提交。所有引用只来自当前数据源最近一次审核通过的结果，选择候选、查看证据和下载不会重新调用模型。

## 可复用分析能力

规划器会返回三种状态：可执行、需要澄清、当前能力不支持。可执行计划支持：

- 单范围汇总：分析整份 CSV 或一个明确且含首尾的日期区间，可按总额、日、周、月、国家或商品分组；可按金额排序并取前 N 项。
- 两期比较：保留前后期金额、差额、变化率，以及国家和商品增长榜、下降榜。
- 澄清与能力边界：比较日期缺失或有歧义时只追问日期；促销、缺货等原因缺少对应数据时说明原因。两类情况都在规划后结束，不调用计算、报告或审核。

周按周一至周日分组，月按自然月分组。纯时间分组会补齐范围内没有有效交易的周/月桶并显示 0；日期范围截断自然周或月时会标为“部分覆盖”。正向交易排除取消订单、非正数量和非正单价；费用保留在总额和国家结果中，从商品榜排除。日期含首尾，且必须位于 CSV 的可用日期范围内。

PowerShell 用法示例：

```powershell
# 整份数据中金额最高的一周
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "从提供的数据开始到结束，哪一周卖得最好？"

# 单区间国家金额前三
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "2011 年 9 月 19–25 日，正向交易金额最高的 3 个国家是什么？"

# 两期比较（保留原有能力）
.\.venv\Scripts\python.exe scripts\run_workflow.py data\retail_demo.csv "比较 2011 年 9 月 12–18 日和 9 月 19–25 日的正向交易金额，分别列出国家和商品金额增长最多、下降最多的 3 项。"
```

结果表、证据编号、报告和图表均来自本次工作流的本地计算状态；只有审核通过才导出文件。网页与命令行共用同一工作流。

## 国家和商品筛选

汇总、排名及两期比较都可以附加国家和商品条件。汇总不指定日期时默认使用 CSV 的完整日期范围；指定一个区间时使用该区间。两期比较需要用户提供两个区间。国家别名由本地 `Country` 列匹配，商品编号按字符串匹配（保留前导零和字母后缀），商品描述须唯一对应一个 `StockCode`。同一维度的多个条件按并集处理，国家与商品跨维度按交集处理；未匹配或描述对应多个编号时会在规划后要求用户确认，不会退回全量数据。实体存在但所选日期范围内没有正向记录时，结果会保留筛选并明确标记无记录。CSV 明细始终只在本地读取。
