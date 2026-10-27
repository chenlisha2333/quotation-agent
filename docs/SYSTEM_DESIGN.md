# SYSTEM_DESIGN：报价 Agent 技术设计

| 项目 | 内容 |
|---|---|
| 版本 | 1.0，2026-10-22 |
| 对应基线 | 应用 `0.5.0`；以 [PRD V3.1](PRD.md) 和当前代码为核对起点 |
| 文档用途 | 说明**当前代码如何工作**、已知设计缺口，以及报价与计划中的二维 CAD/Text-to-CAD 演进约束 |
| 状态用语 | **当前**＝代码已存在；**设计约束**＝后续开发须保持；**计划**＝尚未实现 |

业务范围见 [PRD](PRD.md)，可执行任务见 [TASKS](TASKS.md)。已有的[架构设计](架构设计.md)和[代码全解](项目代码全解.md)提供更多模块讲解；本文聚焦工程边界、接口、状态与技术决策。所有示例金额都是合成数据，不代表企业采购价。

## 1. 目标与不变量

当前系统处理字段明确的 Excel 零件清单：逐行核对施耐德品牌和型号，以人民币成本生成 `成本 × 1.2` 草稿，人工处理缺价，按技术→采购→商务顺序审批，再导出冻结第 1 版的客户 Excel/Word。自然语言窗口只查询任务与候选资料。真实供应商价、税口径、正式选型与企业定价规则未签核，系统仍是本机原型。

以下约束比具体框架更重要：

1. 没有可信的型号或价格依据时不能自动接受；缺价不能用零元代替。
2. 金额由确定性 Python 代码计算，使用 `Decimal`；模型输出不能写入报价、审批或价格库。
3. 任务“完成”不等于对外发布；客户文件只能基于通过审批的冻结快照。
4. 不同业务角色只收到其可见字段；内部成本不能出现在客户文件中。
5. 对已发布报价的变更必须生成新版本，不能覆盖历史。当前只支持第 1 版，后续版本是计划。

## 2. 系统上下文与技术选择

```mermaid
flowchart LR
    User[本机用户/浏览器] --> UI[static/index.html]
    UI --> API[FastAPI app/main.py]
    API --> Parser[parser.py]
    API --> Flow[LangGraph app/agent.py]
    Flow --> Policy[product_policy.py]
    Flow --> Prices[db.py + SQLite]
    Flow --> Check[validator.py]
    Flow --> Math[pricing.py]
    API --> Gate[auth.py + approval.py]
    Gate --> Prices
    API --> Files[exporter.py + word_exporter.py]
    API --> Chat[assistant.py]
    Chat --> Knowledge[knowledge.py + 六款官方产品事实清单]
    Chat --> Model[Responses API；可选]
    API --> Local[(data/ 上传和导出文件)]
    Files --> Local
```

当前是**单进程优先的模块化单体**。FastAPI 处理 HTTP 和身份边界，Pydantic 校验领域/请求数据，LangGraph 显式编排报价节点，SQLite 保存价格、任务、事件和发布快照；pandas/openpyxl 读 Excel，openpyxl 与 python-docx 生成文件。前端为原生 HTML/CSS/JavaScript，**没有 Streamlit**。Dockerfile/Compose 仅提供可选本机包装，尚无容器实机验收。这样可保持完整业务路径可运行，避免在业务规则未定时引入多服务和消息队列。

LangGraph 当前只是确定性报价流程的图结构；任务恢复使用 `app/agent.py::TaskSnapshot` 与 `app/db.py` 的 SQLite 快照，**不是** LangGraph 内置 checkpointer。`app/web_scraper.py` 和 `app/retry.py` 是可选实验代码，未进入自动报价主链。问答中的模型工具调用是局部 AI 能力，不改变报价工作流。

## 3. 模块边界与调用契约

| 层 | 当前文件与入口 | 输入→输出及责任 |
|---|---|---|
| HTTP/页面 | `app/main.py`、`static/index.html` | 校验上传、令牌与请求；按角色构造响应；调用流程、审批、问答或导出 |
| 输入标准化 | `app/parser.py::parse_excel`、`app/models.py::Product` | Excel→产品列表；常见列名映射、关键词类别推断；不负责正式技术选型 |
| 产品/价格准入 | `app/product_policy.py::product_issue/eligible_price`、`app/db.py::search_local_price` | 产品与本地价格→阻断原因或合格候选；不从官网资料推定成本 |
| 报价流程 | `app/agent.py::run_quotation_agent/apply_manual_prices` | 产品/补价→状态与报价项；节点顺序、暂停和持久化 |
| 金额/警告 | `app/pricing.py::calculate_item_price`、`app/validator.py::validate_price` | `Decimal` 成本→报价项；异常价格警告目前不自动阻断 |
| 持久化 | `app/db.py::save_task_snapshot/load_quote_revision` | 快照/事件/发布版本→SQLite；事务与版本冲突保护 |
| 身份/审批 | `app/auth.py::authorize`、`app/approval.py::approve_quote` | 本机 Bearer 令牌→角色；三关审批和规则闸门 |
| 文件输出 | `app/exporter.py`、`app/word_exporter.py` | 服务端任务或冻结快照→内部/客户 Excel、Word；不以客户端明细计算客户报价 |
| 问答检索 | `app/knowledge.py::search_knowledge/get_product_knowledge/compare_product_models`、`app/assistant.py::answer_question` | 问题/授权摘要→有官方来源的产品事实、型号差异与模型回答；只读、与报价分离 |
| CAD设计（计划） | `CADDesignService`、`CAD2DGenerator`、`DXFValidator`（待建） | 已审核 BOM 快照与 `DesignSpec`→受限二维布局初稿、校验报告和工程师审核任务；不改变报价或自动发布图纸 |

依赖方向是 `main → 领域模块/流程 → db`。当前 `main.py` 仍同时负责部分响应字段过滤和文件导出编排；若未来拆服务，应先抽出稳定的应用服务接口和测试，再评估是否必要。

### 3.1 二维 CAD 与 Text-to-CAD（计划设计，当前未实现）

报价和 CAD 是两个独立业务工作流，通过不可变 `BOMSnapshot`、产品主数据版本及项目版本关联。CAD 生成不回写报价；若工程师修改导致 BOM/成本变化，必须创建报价新版本并走原审批链。CAD 纯布局修改也创建新的 CAD 版本，保留生成输入、产物哈希、校验报告和人工意见。

```text
已审核的 BOMSnapshot + 柜型/安装板参数 + 元器件尺寸与安装规则
    → DesignSpec（结构化、带数据来源和版本）
    → CAD2DGenerator 接口
         ├─ Template2DGenerator（确定性基线/回退）
         └─ TextToCAD2DAdapter（开源实现，兼容性验证通过后接入）
    → 隔离的生成/转换边界
    → DXF产物 + 预览
    → DXF解析与确定性几何/BOM校验
    → 失败：产生结构化错误；有界修复后重验，或转工程师
    → 通过：仍需工程师审核，存为“待审核初稿”
```

- **适配契约**：`CAD2DGenerator.generate(spec, constraints) -> CADArtifact`。`DesignSpec` 包含柜型/安装板边界、BOM项和数量、元件二维外形/安装孔/可选方向、禁布区、净空和间距约束，并记录字段来源及版本。`CADArtifact` 包含 DXF、预览、生成器标识/版本、输入快照引用和校验前元数据。缺少必要尺寸、安装信息或规则时返回可定位的缺项，不得让模型推测工程参数。
- **Text-to-CAD边界**：只用于受限二维布局初稿。先验证选定开源实现支持二维、可提供稳定接口且允许本项目使用；不假设它能直接输出符合本项目规则的 DXF。若它只生成代码，优先改为模型输出受 schema 约束的布局表示，再由确定性绘图代码生成 DXF。`TextToCAD2DAdapter` 让上游开源项目可替换，避免其 SDK/提示格式渗入报价域模型。
- **执行隔离**：禁止在 Web/API 主进程执行不可信生成代码。首选不执行代码的结构化输出路径。若兼容性验证证明必须执行，则放入可丢弃的隔离 worker/容器：禁网络、无应用密钥、只读输入挂载、单独临时输出目录、非特权用户，并设置 CPU、内存、文件大小和墙钟时限；只允许白名单绘图 API。解析产物并重新校验，任何执行错误、超限或异常实体都拒绝并记录，不接受模型自行宣称校验通过。
- **确定性校验**：用 DXF 解析和二维几何算法检查文件结构、BOM及数量一致、边界/禁布区、碰撞和已签核间距。验证器独立于生成器；每次自动修复后从头重新校验。几何规则不能证明接线正确、电气安全、散热或可制造性。
- **Agent与人工**：计划中的 `CADGenerationRepairAgent` 只可读取结构化校验错误，在明确工具白名单及最大修复轮数内提出修改；它不能改产品主数据、BOM、尺寸库、价格或审批状态。输入不全、持续失败或规则冲突时转工程师。自动几何校验通过仍不是工程批准。
- **存储与追溯**：DXF、预览和必要的运行日志作为文件产物存储，数据库仅保存版本、哈希、状态、引用与审核事件。每个产物关联项目版本、`BOMSnapshot`、`DesignSpec`、产品尺寸库版本、规则版本、适配器/模型版本和任务轨迹；客户成本或密钥不得写入提示或 CAD 产物。
- **第一版边界**：一个工程师确认的柜型、5–10种标准元器件、二维安装板布局；输出DXF和预览，人工审核后归档。暂不做完整接线图、原理图、通用三维建模、生产加工文件或自动投产。`TextToCAD2DAdapter` 是否采用由先行技术验证任务决定，确定性布局基线必须保留。

## 4. 报价主链：请求、状态和失败路径

```mermaid
sequenceDiagram
    actor U as 用户
    participant A as FastAPI
    participant P as Excel解析
    participant G as LangGraph
    participant D as SQLite
    participant E as 文件导出
    U->>A: POST /upload（Excel、客户名）
    A->>P: parse_excel(临时保存的文件)
    P-->>A: Product[]
    A->>G: run_quotation_agent(...)
    G->>D: 查符合条件的人民币价格
    G->>D: 保存任务快照与事件
    alt 有缺价或产品问题
        G-->>A: 人工确认 + item_id/原因
        A-->>U: 待处理明细
        U->>A: 修正 Excel 重传，或 POST /tasks/{id}/confirm 补齐成本
        A->>G: apply_manual_prices(...)
        G->>D: 保存完成草稿
    else 价格齐全
        G-->>A: 完成草稿
    end
    U->>A: 内部导出/三关审批
    A->>D: 保存审批事件和发布版 1
    U->>A: 客户导出
    A->>D: 读取发布版 1 快照
    A->>E: 生成 Excel 或 Word
    E-->>U: 客户文件
```

`app/agent.py::build_agent` 的实际节点是 `parse → price_lookup → validate_prices → {human_review | calculate}`。`human_review` 是本次图调用的终点；补价由单独 API 调用 `apply_manual_prices` 后直接完成计算，并非图内自动等待/恢复。状态可为待处理、解析中、查价中、价格核查中、人工确认、计算中、完成；模型也定义了“错误”，但当前没有统一的错误状态节点。

产品品牌不符或缺型号时，即使用户输入成本也不得绕过，必须修正清单重新上传。合格价格来自本地 SQLite，要求产品名称/施耐德品牌/型号/规格/单位精确一致、CNY、正数、来源与凭证、有效期未过、已核准、`price_decimal` 精确文本存在。查询按 `fetched_at` 排序返回首条合格记录；多条价格的业务选价策略尚未确定。数据库查价异常目前会记录日志并返回空列表，用户看到的是待补价；生产前应区分“库故障”和“确实无价”。

`parse_excel` 当前对小数数量执行 `int(...)`，缺数量填 1；这会隐藏输入错误，是 [TASKS](TASKS.md) 的首项修复。解析器跳过空名称行，主要覆盖默认工作表；`.xls` 可能缺读取引擎。上传接口限制后缀 `.xlsx/.xls`、非空和 10 MiB；不能把通过后缀检查当作内容安全检查。

## 5. 金额、版本与持久化

当前规则 ID：`draft-cost-times-1.2-v1`，币种 `CNY`。`app/pricing.py` 先把输入转成有限 `Decimal`，计算：

```text
销售单价 = (成本 × (1 + 0.20) × 折扣).quantize(Decimal("0.01"), ROUND_HALF_UP)
行金额   = 已舍入销售单价 × 数量
总金额   = 各行金额之和
```

对外上传接口只接受默认 20% 加价率；正常流程折扣为 1。代码中 `profit_rate` 是兼容旧接口的字段名，业务含义是**成本加价率**，不是毛利率。税、运费、辅材、人工与正式折扣策略尚无企业规则。`QuotationItem` 保留成本、销售单价、行价、规则 ID、舍入方式、来源类别与精度标记；目前未把本地 `PriceRecord.source_reference` 完整复制到报价明细或发布版，因此“从客户金额直接追到供应商凭证”的链条仍不完整。

`data/quotation.db` 的表：

| 表 | 当前用途 | 关键约束 |
|---|---|---|
| `price_records` | 本地价格与元数据；`price_decimal TEXT` 保存精确金额，旧 `price REAL` 只为兼容 | 旧实数价格或缺元数据记录不参与自动采用 |
| `quotation_tasks` | 任务状态 JSON 快照和 `version` | 任务 ID 唯一；保存时比较预期版本 |
| `task_events` | 每次状态/审批操作及详情 | `(task_id, version)` 唯一；与快照同事务 |
| `quote_revisions` | 发布的完整报价快照 | `(task_id, revision_no)` 主键；当前只写入第 1 版 |

`save_task_snapshot` 用 `BEGIN IMMEDIATE`、版本比较与单事务写入任务/事件/发布快照，避免旧状态覆盖新状态；重复相同补价和审批请求可幂等返回。原始上传文件存于 `data/uploads/`，但当前任务未持久关联原件哈希/文件 ID；导出的文件在 `data/`，也没有独立产物表和发送记录。SQLite 文件与业务文件都不进 Git；正式部署需要备份、恢复、访问控制和留存策略。

## 6. 审批、权限与文件边界

配置 `QUOTE_APPROVERS_JSON` 后，私有 API 要求 Bearer 令牌。`technical` 看不到成本或销售金额；`procurement` 可见成本、可补价，但不能见销售金额；`commercial` 执行最后审批和客户导出。三个关卡必须按序、不同 `actor`、有非空意见。商务发布时 `QUOTE_APPROVED_RULE_ID` 必须匹配每行规则 ID；该配置仅是程序闸门，不能代替企业书面批准。没有配置审批人时本机草稿接口可无令牌使用，审批返回 503，所以当前运行方式必须保持本机隔离。

`POST /export` 只接受 `internal/customer` 和 `xlsx/docx`。内部草稿必须是已补齐价格的施耐德人民币任务，技术角色不能导出；客户版额外要求商务角色和完整三关审批，读取冻结快照，由两个导出模块各自使用客户字段白名单。客户版不应出现成本、加价率、供应商信息。当前 Word 内容已通过测试，但企业模板和目标办公软件的中文排版未验收。

## 7. 自然语言分支：RAG 与 Tool Calling

`POST /assistant/chat` 接收最多 1000 字的问题、最多 6 条历史和可选任务 ID。若指定任务，`app/main.py::_task_response` 先按角色过滤，再构造至多 30 行的摘要。`knowledge.py` 从 `data_sources/schneider_products.json` 的六款施耐德官方产品事实做词项检索，并核对完整型号与电气参数；只接受允许的施耐德 HTTPS 来源域名。事实清单附来源和核对日期，但不是向量库、完整产品目录或供应商实时价格库。

`assistant.py` 将问题、检索片段发送到配置的 Responses API。模型可要求任务摘要、产品搜索、完整型号详情或两个型号的确定性比较四个工具；后端检查白名单及参数，最多执行两次只读工具调用；`parallel_tool_calls=False`、`store=False`。模型没有补价、审批、导出或文件写入工具。未配置 `OPENAI_API_KEY/OPENAI_MODEL` 时返回 503；调用失败返回 502，报价主链独立可用。真实模型工具选择、提示注入抵抗和企业数据传输许可尚未实测。

## 8. HTTP 接口与错误语义

| 方法/路径 | 当前用途 | 重要边界 |
|---|---|---|
| `GET /templates/inquiry` | 下载三款候选施耐德询价表示例 | 成本列留空；不得视为正式报价 |
| `POST /upload` | 上传并运行报价图 | 10 MiB、Excel 后缀、固定加价率；输入错误为 400/413/422 |
| `GET /tasks/{task_id}` | 从 SQLite 恢复任务 | 按角色过滤；不存在返回 404 |
| `POST /tasks/{task_id}/confirm` | 按 `item_id` 一次补齐成本 | 角色/金额校验；冲突为 409，参数错误为 422 |
| `POST /tasks/{task_id}/approve/{stage}` | 技术、采购、商务审批 | 令牌、顺序、意见、规则闸门；非法状态为 409 |
| `POST /export` | 内部/客户 Excel 或 Word | 仅服务端任务数据；未完成或未发布返回 409 |
| `POST /assistant/chat` | 只读问答与来源 | 未配置模型为 503，模型故障为 502 |

API 定义以 `app/main.py` 和服务启动后的 `/docs` 为准。当前不存在企业客户门户、外发接口、价格维护 API 或 CAD API。未来 CAD API 需在任务状态、文件授权和 worker/超时策略定稿后另行定义；不能把规划接口描述成当前实现。

## 9. 验证、部署与演进决策

测试入口：`.venv/bin/python -m pytest -q -p no:cacheprovider`。`tests/conftest.py` 把每个测试的 SQLite 隔离到临时目录；现有测试覆盖金额、严格价格准入、任务恢复与并发版本、审批、文件内容、官方产品型号约束和只读工具协议模拟等。60 项通过是代码回归证据；真实模型、浏览器、Docker、正式 Word 排版和企业案例均未验收。可选 Docker Compose 绑定 `127.0.0.1:8000` 并挂载 `data/` 卷；不等于生产部署。

下一步按 [TASKS](TASKS.md) 推进：先修输入完整性与故障可见性，再用脱敏真实资料建立产品/价格金标准并确认税与定价规则，随后完成多版本、企业身份和部署。取得柜型、安装数据与工程师批准样例后，先完成二维确定性布局基线和校验，再独立评测 Text-to-CAD 开源实现；在兼容性、安全和质量验收通过前，不把它加入报价主链或当前运行依赖。不要先引入微服务。
