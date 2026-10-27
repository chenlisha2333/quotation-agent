# API、页面与 Excel 导出

更新日期：2026-10-21。当前页面是原生 HTML/JavaScript，不含 Streamlit。它支持人民币施耐德清单上传、待处理补价、按任务 ID 恢复、三角色审批、内部草稿和已发布客户版导出。

## 业务目标、核心技术与选择理由

报价员需要知道每一行为什么待处理，并能在服务重启后继续。FastAPI 负责请求校验和业务入口；页面使用浏览器 `fetch`；`openpyxl` 生成 Excel。现有界面规模小，保留原生页面比为展示同一流程引入 Streamlit 更合适。**简单说**，API 契约是浏览器和服务器商量好的收发格式；**专业定义**，它包含路径、请求体、响应字段、验证规则和 HTTP 状态码。

内部草稿包含成本与计算依据，客户版只在技术、采购、商务审批和已确认规则闸门通过后，从冻结版本生成。参见[角色审批与客户报价](09-角色审批与客户报价.md)。

## 与其他模块的关系和数据流

`static/index.html → /upload → app/main.py::upload_excel → app/parser.py → app/agent.py → SQLite`。返回任务 ID、版本号、CNY、报价项和待处理原因。之后 `/tasks/{task_id}` 从数据库恢复任务；`/tasks/{task_id}/confirm` 用 `{"line-1":"12.34"}` 等明细 ID 补价；`/tasks/{task_id}/approve/{stage}` 顺序审批；`/export` 用 `{"task_id":"...","format":"internal"}` 或 `"customer"` 导出。

## 相关文件、关键函数及核心代码逐段解释

1. `app/main.py::lifespan` 启动时创建目录和初始化数据库；启动失败会直接报错，不假装数据库可用。
2. `upload_excel` 检查扩展名、空文件和 10 MiB 大小上限，用随机文件名保存上传文件；不合法的上传会被删除。随后调用解析与工作流。`_task_response` 把 `Decimal` 金额以字符串给页面，并返回 `revision`、审批状态、`pending_products[].reason` 和 `can_enter_price`。`async def` 内仍有同步解析和数据库操作，多人并发需后续改进。
3. `get_task` 按任务 ID 读取 SQLite。`confirm_prices` 拒绝非正或非有限金额；业务函数再检查施耐德型号、明细 ID 集合和状态。并发版本冲突返回 409。
4. `approve_task_endpoint` 将阶段和带令牌的角色交给 `approve_quote`；顺序及规则确认在业务层执行。配置令牌后，上传、查询、补价、审批、导出都要求 `Authorization: Bearer ...`。
5. `export_quotation_endpoint` 只接受服务端任务 ID 与格式。内部版从完成草稿生成；客户版必须有商务角色、发布版本和完整三阶段记录，并从 `quote_revisions` 读取冻结快照。它不会信任浏览器提交的金额。
6. `static/index.html` 对上传的产品文字做 HTML 转义，显示原因和审批阶段，令牌只保存在当前页面输入框。`app/exporter.py::export_quotation` 写内部计算依据；`export_customer_quotation` 只写公开列。文本写入 Excel 前经过 `_safe_excel_text`，避免产品名称被当成公式。

## 必须掌握的 Python 基础

**必须掌握**：函数参数、`dict` 与 JSON、`try/except`、HTTP 401/403/404/409/422/503、`Decimal` 以字符串传输、Pydantic `model_dump(mode="json")`、`Path`。页面部分先理解 `fetch`、请求头和 JSON 请求体。**可暂缓**：前端框架、复杂 CSS、分布式异步任务队列。

## 实际运行与测试

在项目根目录执行 `.venv/bin/python -m pytest -q -p no:cacheprovider`，当前 **30 passed**；`tests/test_product_policy.py` 测不合格草稿拒绝，`tests/test_task_persistence.py` 测任务恢复，`tests/test_approval.py` 测客户版字段隔离。页面脚本通过 `node --check` 语法检查。运行 `python run.py` 后访问 `http://localhost:8000/static/index.html`。目前没有真实浏览器端到端测试。

## 常见错误和排查

- 上传后待处理：先看 `pending_products[].reason`；仓库样例含其他品牌，按规则应被拦截。
- 补价 422：按 `line-1` 等 ID 一次提交全部可补价行；品牌或型号错误要修正 Excel 后重传。
- 任务 404：检查 ID 和 `data/quotation.db` 路径。任务已持久化，但旧内存版的任务无法自动迁移。
- 401/403/503：分别表示缺少或错误令牌、角色不符、审批配置缺失或无效。
- 客户导出 409：检查三阶段审批、冻结版本和已确认的规则 ID。
- 页面长时间等待：上传处理仍同步运行部分耗时步骤；检查服务日志和文件大小。

## 技术面试可能问的问题

**为什么导出按任务 ID？** 金额由服务端任务或冻结版本决定，避免客户端篡改。**为什么客户版另建函数？** 白名单列使成本字段不会随内部模型扩充意外流出。**现在能部署给全公司吗？** 还不能；本地令牌、上传限制、企业身份系统、备份和正式定价规则仍需建设。

## 小练习

1. 从页面 `exportExcel('customer')` 追到 `export_quotation_endpoint` 和 `export_customer_quotation`，写出客户版的字段列表。
2. 分别在 `main.py` 找到 404、409 和 503 的触发路径，解释给报价员时应怎样排查。
