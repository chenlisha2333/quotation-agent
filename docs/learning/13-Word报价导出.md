# 13 Word 报价导出

## 业务目标：为什么需要 Word

Excel 适合录入和计算，Word 方便客户阅读正式版式。客户版必须与已审批 Excel 价格一致，不能由模型重新写数字；内部草稿要保留成本、来源和计算规则供核对。因此两个文件格式都从同一批 `QuotationItem` 读取金额。

## 技术与方案选择

**必须掌握：** `python-docx` 生成 `.docx`；`Decimal` 保存十进制金额；FastAPI 的 `FileResponse` 提供下载。简单说，导出器是“把已算好的数据排版”，不是“再算一次报价”。专业地说，导出层是对领域结果的表示转换。**可暂缓：** OOXML 底层标签、页眉页脚和复杂企业模板。本版用少量 OOXML 设置表格边框、底色和内边距，避免引入额外排版框架。

## 为什么这么设计，与其他模块的关系

`app/main.py:export_quotation_endpoint()` 先判断 `format` 和 `file_type`。内部版检查任务已完成、施耐德与 CNY 价格齐全，技术角色不能导出含成本的文件。客户版额外要求三角色审批，读取 `db.load_quote_revision()` 的冻结快照。之后按扩展名调用 `app/exporter.py` 的 Excel 函数或 `app/word_exporter.py` 的 Word 函数。前端 `exportQuote()` 只传选择，不能绕过后端限制。这条数据流保证客户 Word 不会因后续任务变化而改变。

## 关键文件与核心代码逐段解释

1. `app/word_exporter.py:_new_document()` 设置页边距、标题、客户名、任务号和 CNY；生成可编辑 Word 文档。
2. `_style_table()` 给表头、边框和隔行底色加样式。它不改价格；改变样式不会改变业务数据。
3. `export_internal_word()` 写产品、数量、成本、报价单价和合计，后面逐项写成本、加价率、折扣、规则 ID、舍入方式和来源，便于核算追溯。
4. `export_customer_word()` 只写客户可见字段，标题包含施耐德，版本与发布日期来自冻结记录；没有内部价格和规则字段。`_money()` 接收 `Decimal` 并格式化为两位人民币。
5. `app/main.py:ExportRequest.file_type` 默认为 `xlsx`，所以旧调用仍返回 Excel。`tests/test_quote_journey.py` 读取两个 Word 文件，检查客户版没有“成本”和“1.2”，并与 Excel 的 24 元金额对应。

## 必须掌握的 Python 基础

`Sequence[QuotationItem]` 表示按顺序提供报价项，既能传列表也能传其他序列；`enumerate(items, 1)` 给行号；`zip(cells, values)` 把每个单元格与对应文本配对；`sum(..., Decimal("0.00"))` 保持金额类型。`Path` 处理文件路径，`output_path.parent.mkdir()` 保证目录存在。不要用二进制浮点数累加金额。

## 实际运行与测试

运行 `.venv/bin/python -m pytest -q tests/test_quote_journey.py`。网页完成补价后可导出内部 Excel/Word；三角色顺序审批后商务可导出客户 Excel/Word。接口例子：`POST /export`，JSON 为 `{"task_id":"实际任务ID","format":"internal","file_type":"docx"}`。生成文件位于 `data/`，其内容可能含客户和成本，不入 Git。测试还用 `python-docx` 重新读取文档验证文本。当前自动渲染器对中文显示方框，真实 Word/办公套件的中文排版需在目标环境再验收。

## 常见错误与排查

- 403：当前角色无权导出含成本或客户正式文件；检查令牌角色。
- 409：任务未补齐价格或客户版尚未完成审批；先看任务状态与审批记录。
- 422：`file_type` 只接受 `xlsx` 或 `docx`，`format` 只接受 `internal` 或 `customer`。
- 中文排版异常：目标电脑缺少字体；检查文档显示字体与系统中文字体。数字不一致时先核对冻结快照，不能在 Word 里手工修价。

## 面试可能问

**如何保证 Excel 和 Word 金额一致？** 两者接收同一已计算的 `QuotationItem`，客户版读同一冻结版本；测试交叉核对金额。**为什么内部版与客户版分开函数？** 输出字段的安全边界不同，避免条件分支遗漏导致成本泄漏。**为什么不让 LLM 写 Word？** 模型可能改写型号和金额，报价文件必须由确定性程序生成。

## 小练习

1. 观察 `export_customer_word()` 的 `values`，列出比内部版少的字段，并解释每个字段为什么不该给客户。
2. 用测试临时生成数量 3、成本 10 元的明细，预计客户金额应为 36 元；运行后从 Word 表格中核对。
