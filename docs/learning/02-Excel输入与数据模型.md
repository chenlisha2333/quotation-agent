# 模块 1：Excel 输入与数据模型

## 业务目标与为什么这样做

报价员上传的表格列名可能是“产品名称”“名称”或 `product`。先把它们转换成同一种 `Product`，后续查价和计算才有固定输入。当前选择 pandas 读表、规则映射列名、Pydantic 检查字段：规则透明，出错可定位。这里的分类只是名称关键词的初步推断，不是技术选型结论。

**概念**：数据模型像一张有字段要求的表单。专业地说，`app/models.py` 的 Pydantic `BaseModel` 对实例化的数据进行运行时校验；Python 类型标注提示期望类型，但单独的标注不等于校验。

核心技术及作用：pandas 读取 Excel 表格，普通 Python 字典映射列名和关键词，Pydantic 校验解析结果。保留简单规则是因为目前输入模板有限且可解释，暂不需要模型识别。

## 文件、函数与数据流

- `app/main.py::upload_excel` 保存上传文件，调用 `app/parser.py::parse_excel`。
- `parse_excel` 调用 `normalize_column_name` 整理列名，调用 `infer_category` 给产品推断类别，再构造 `app/models.py::Product`。
- `parse_excel_with_rows` 提供相同解析结果并保留物理工作表行号，供离线验收评测对照原始行；线上报价入口仍使用兼容的 `parse_excel` 产品列表。
- `Product` 列表传入 `app/agent.py::run_quotation_agent`；`QuotationItem` 在后面的价格模块创建。
- `tests/test_parser.py` 覆盖这三个解析函数的基本路径。

`Excel 文件 → pandas DataFrame → 标准列名 → 每行 Product → 报价工作流`。

解析层只要求“产品名称、数量”两列；报价工作流现在还要求每行明确为施耐德且有型号。解析成功不等于可以完成报价，具体核对规则见 [人民币与施耐德产品匹配](07-人民币与施耐德产品匹配.md)。Excel 15 位限制是为避免数量精度被表格格式破坏，不是公司业务批准的最大订购量；如需其他格式或精度，须由业务确认后调整。

## 核心代码逐段读

1. `normalize_column_name` 先 `str(col).strip().lower()`，去首尾空白并统一英文大小写；`mapping.get(col, col)` 在找到别名时返回标准字段，未找到时保留原名。这个设计方便支持多种客户模板，但未知列不会自动成为可用字段。
2. `parse_excel` 用 `pd.read_excel(file_path, dtype=object)` 读第一张工作表并保留单元格类型，避免把文本单元格中的 `"10"` 自动推断为数值；随后检查 `name` 和 `quantity` 两列是否存在。注意“有列”不代表每行的值都有效。
3. 逐行处理时，空产品名行仍跳过；有效产品行的数量必须来自数值单元格，且为有限、正整数。`_parse_quantity(value, excel_row)` 拒绝空值、布尔值、文本、小数、非正数和超出 Excel 15 位精确整数范围的值，错误指向 Excel 实际行号。整数数值单元格以及数值形式的 `2.0` 可通过，随后转为 Python `int`。
4. `parse_excel_with_rows` 返回 `(Excel物理行号, Product)` 列表，供 T05 评测器将差异对齐到原始表格行。`parse_excel` 仍只返回 `Product` 列表，保持既有 API 行为。
5. `infer_category` 依次检查“柜/箱/屏”“线/排/辅料…”和元器件关键词，顺序会影响结果。类别为 `UNKNOWN` 时仍可继续流转，应由后续规则或人员确认。
5. `Product` 定义了名称、型号、规格、品牌、数量、单位等字段；`PriceRecord`、`QuotationItem` 和 `Quotation` 也在 `models.py`，供后续模块共享。金额字段已改用 `Decimal`；企业公式和外部价格精度仍需核实，见 [模块 2](03-价格查询核查与金额.md)。

## 必须掌握的 Python 基础

必须掌握：函数参数和返回值、`dict.get`、`for` 循环、`if` 分支、`None`、`List[Product]`、异常抛出、`Path`、Pydantic `Field(ge=1)`。可以暂缓：pandas 的高级索引、多工作表处理、Pydantic 自定义验证器。

## 运行与验证

在项目根目录执行 `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_parser.py tests/test_upload_limits.py tests/test_inquiry_evaluation.py`；数量边界、原表行号、可选空字段归一、哈希冻结、字段差异、预期拦截、额外行计错和清单路径隔离有合成 Excel 测试。2026-11-01 定向测试 27 项、完整回归 92 项通过。冻结样本评测命令与数据保护要求见 [T05 验收集与评测规范](../acceptance/T05-验收集与评测规范.md)。手工可用 `sample_products.xlsx` 上传，观察非施耐德品牌被拦截；它会写入 `data/`。读取旧版 `.xls` 可能缺少额外引擎，默认依赖重点支持 `.xlsx`。

## 常见错误与排查

- 提示缺少必要列：核对第一行是否为表头，列名是否在 `normalize_column_name` 映射中。
- 数量验证失败：查看错误指出的 Excel 行号；修正缺失、文本、小数、布尔值、零/负数或超过 15 位精确整数范围的数量单元格。
- `ModuleNotFoundError: app`：在项目根目录用虚拟环境的 `python -m pytest`，不要直接执行本机的 `pytest` 命令。

## 面试可能问

**为什么解析阶段还要校验数量？** Pydantic 的 `int` 字段不能保留原 Excel 单元格类型；如果读取或转换时先把文本/小数改成数字，后续模型看不到原始错误。解析阶段保留类型并指出行号。**空行怎样处理？** 产品名为空的行继续跳过；有产品名但数量缺失会拒绝上传，不会默认为 1。

## 小练习

1. 口头追踪“数量(个)”如何变成 `Product.quantity`，指出涉及的两个函数和一个模型字段。
2. 在临时 Excel 中加入“数量=0”“数量=2.5”和文本格式“数量=10”，确认它们都被拒绝且错误行号正确；再试整数数值单元格。
