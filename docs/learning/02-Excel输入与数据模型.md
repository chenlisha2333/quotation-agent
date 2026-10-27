# 模块 1：Excel 输入与数据模型

## 业务目标与为什么这样做

报价员上传的表格列名可能是“产品名称”“名称”或 `product`。先把它们转换成同一种 `Product`，后续查价和计算才有固定输入。当前选择 pandas 读表、规则映射列名、Pydantic 检查字段：规则透明，出错可定位。这里的分类只是名称关键词的初步推断，不是技术选型结论。

**概念**：数据模型像一张有字段要求的表单。专业地说，`app/models.py` 的 Pydantic `BaseModel` 对实例化的数据进行运行时校验；Python 类型标注提示期望类型，但单独的标注不等于校验。

核心技术及作用：pandas 读取 Excel 表格，普通 Python 字典映射列名和关键词，Pydantic 校验解析结果。保留简单规则是因为目前输入模板有限且可解释，暂不需要模型识别。

## 文件、函数与数据流

- `app/main.py::upload_excel` 保存上传文件，调用 `app/parser.py::parse_excel`。
- `parse_excel` 调用 `normalize_column_name` 整理列名，调用 `infer_category` 给产品推断类别，再构造 `app/models.py::Product`。
- `Product` 列表传入 `app/agent.py::run_quotation_agent`；`QuotationItem` 在后面的价格模块创建。
- `tests/test_parser.py` 覆盖这三个解析函数的基本路径。

`Excel 文件 → pandas DataFrame → 标准列名 → 每行 Product → 报价工作流`。

解析层只要求“产品名称、数量”两列；报价工作流现在还要求每行明确为施耐德且有型号。解析成功不等于可以完成报价，具体核对规则见 [人民币与施耐德产品匹配](07-人民币与施耐德产品匹配.md)。

## 核心代码逐段读

1. `normalize_column_name` 先 `str(col).strip().lower()`，去首尾空白并统一英文大小写；`mapping.get(col, col)` 在找到别名时返回标准字段，未找到时保留原名。这个设计方便支持多种客户模板，但未知列不会自动成为可用字段。
2. `parse_excel` 用 `pd.read_excel(file_path)` 读第一张工作表；随后检查 `name` 和 `quantity` 两列是否存在。注意“有列”不代表每行的值都有效。
3. 逐行处理时，空产品名跳过；`quantity` 缺失时默认为 1，否则用 `int(...)` 转换。`Product.quantity` 有 `ge=1` 约束，负数或 0 会报校验错。小数数量经 `int` 转换可能截断；真实业务要决定数量能否为小数。
4. `infer_category` 依次检查“柜/箱/屏”“线/排/辅料…”和元器件关键词，顺序会影响结果。类别为 `UNKNOWN` 时仍可继续流转，应由后续规则或人员确认。
5. `Product` 定义了名称、型号、规格、品牌、数量、单位等字段；`PriceRecord`、`QuotationItem` 和 `Quotation` 也在 `models.py`，供后续模块共享。金额字段已改用 `Decimal`；企业公式和外部价格精度仍需核实，见 [模块 2](03-价格查询核查与金额.md)。

## 必须掌握的 Python 基础

必须掌握：函数参数和返回值、`dict.get`、`for` 循环、`if` 分支、`None`、`List[Product]`、异常抛出、`Path`、Pydantic `Field(ge=1)`。可以暂缓：pandas 的高级索引、多工作表处理、Pydantic 自定义验证器。

## 运行与验证

在项目根目录执行 `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_parser.py`；解析相关 3 项测试通过，完整项目截至 2026-10-21 为 21 项通过。手工可用 `sample_products.xlsx` 上传，观察非施耐德品牌被拦截；它会写入 `data/`。读取旧版 `.xls` 可能缺少额外引擎，默认依赖重点支持 `.xlsx`。

## 常见错误与排查

- 提示缺少必要列：核对第一行是否为表头，列名是否在 `normalize_column_name` 映射中。
- 数量转换失败：检查 Excel 单元格里是否混入“10个”或小数，定位 `parse_excel` 的 `int(row["quantity"])`。
- `ModuleNotFoundError: app`：在项目根目录用虚拟环境的 `python -m pytest`，不要直接执行本机的 `pytest` 命令。

## 面试可能问

**为什么使用 Pydantic？** 它在解析后校验产品数量等字段，减少无效数据进入后续流程；不能替代业务规则校验。**关键词分类可靠吗？** 当前只是原型启发式规则，没有产品主数据或人工验收证据。**空行怎样处理？** `parse_excel` 跳过产品名为空的行，数量缺失则默认为 1。

## 小练习

1. 口头追踪“数量(个)”如何变成 `Product.quantity`，指出涉及的两个函数和一个模型字段。
2. 在临时 Excel 中加入“数量=0”和“数量=2.5”，预测当前行为，再运行解析验证；记录哪一种行为需要业务人员确认。
