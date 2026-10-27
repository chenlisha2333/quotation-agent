# 报价 Agent 学习入口

适用代码：`/Users/lisa/Desktop/agent/华坤/agent开发/quotation-agent`，更新日期：2026-10-22。先看 [开发与教学规范](00-开发与教学规范.md)，再看 [现状与开发顺序](01-现状与开发顺序.md)。教程解释实际代码；每完成一个开发模块，就同步更新对应教程和 [学习进度](学习进度.md)。

PRD 尚未有真实业务资料支持的部分，见[当前需求与验收差距](当前需求验收差距.md)。

| 阅读顺序 | 教程 | 对应代码 | 状态 |
|---|---|---|---|
| 1 | [Excel 输入与数据模型](02-Excel输入与数据模型.md) | `app/parser.py`、`app/models.py` | 现有代码解读 |
| 2 | [价格查询、核查与金额](03-价格查询核查与金额.md) | `app/db.py`、`app/tools.py`、`app/validator.py`、`app/pricing.py` | 金额精度模块已开发，企业规则待确认 |
| 3 | [人民币与施耐德产品匹配](07-人民币与施耐德产品匹配.md) | `app/product_policy.py`、`app/db.py`、`app/agent.py` | 已实现严格准入 |
| 4 | [施耐德询价表与 1.2 倍报价](10-施耐德询价表与1点2倍报价.md) | `templates/施耐德询价表示例.xlsx`、`app/config.py`、`app/pricing.py`、`app/main.py` | 示例表和规则已实现；真实采购价待取得 |
| 5 | [报价工作流与人工确认](04-报价工作流与人工确认.md) | `app/agent.py` | 已实现明细 ID 补价 |
| 6 | [任务持久化与操作审计](08-任务持久化与操作审计.md) | `app/agent.py`、`app/db.py` | 已实现重启恢复、版本冲突和幂等 |
| 7 | [角色审批与客户报价](09-角色审批与客户报价.md) | `app/auth.py`、`app/approval.py`、`app/exporter.py` | 已实现可配置本地审批与客户版 |
| 8 | [API、页面与导出](05-API页面与导出.md) | `app/main.py`、`static/index.html`、`app/exporter.py` | 任务恢复和双格式导出 |
| 9 | [可选网页查价与故障处理](06-网页查价与故障处理.md) | `app/web_scraper.py`、`app/retry.py` | 实验代码，未接入自动报价 |
| 10 | [Git 版本管理](11-Git版本管理.md) | `.gitignore`、`app/config.py`、`app/db.py` | 已建立当前项目基线；以后按模块提交 |
| 11 | [自然语言只读助手](12-自然语言只读助手.md) | `app/assistant.py`、`app/main.py`、`static/index.html` | 已接入可选模型接口；真实模型调用待验证 |
| 12 | [Word 报价导出](13-Word报价导出.md) | `app/word_exporter.py`、`app/main.py` | 已实现内部与客户版，客户版沿用冻结审批 |
| 13 | [本机与 Docker 运行](14-本机与Docker运行.md) | `Dockerfile`、`compose.yaml` | 容器配置已建立；当前环境无 Docker CLI |
| 14 | [官方产品检索与 RAG](15-候选资料检索与RAG.md) | `app/knowledge.py`、`data_sources/schneider_products.json` | 六款有官方来源的真实型号；不含供应商价格 |
| 15 | [只读 Tool Calling](16-只读ToolCalling.md) | `app/assistant.py`、`app/main.py` | 四个白名单工具；模拟调用与越权拒绝测试通过 |

每篇先说明业务原因，再沿实际函数走数据。阅读时可先看“必须掌握”，独立回答面试题，最后做练习。练习只需在自己的分支或临时文件中完成，真实业务资料不要放进练习样本。读不懂时，把**文件名、函数名、具体一段代码、你的理解和疑问**发给我，我会按当前代码逐行解释。

仓库原有的 `docs/项目代码全解.md` 有些描述超前，例如把当前流程说成由模型动态决策、把 `round(..., 2)` 视为财务精度保证。请以当前代码和本目录的标注为准。
