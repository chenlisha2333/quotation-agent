import pandas as pd
from pathlib import Path
from typing import List
from decimal import Decimal, InvalidOperation
from numbers import Real
from .models import Product, ProductCategory

MAX_EXCEL_EXACT_INTEGER = 999_999_999_999_999


class InquiryParseError(ValueError):
    """A user-correctable inquiry parsing error with stable evaluation metadata."""

    def __init__(self, message: str, *, category: str, row_number: int | None = None):
        super().__init__(message)
        self.category = category
        self.row_number = row_number


def normalize_column_name(col: str) -> str:
    """把常见列名映射到标准字段"""
    col = str(col).strip().lower()
    mapping = {
        "产品名称": "name",
        "名称": "name",
        "产品": "name",
        "product": "name",
        "型号": "model",
        "规格": "spec",
        "参数": "spec",
        "品牌": "brand",
        "厂商": "brand",
        "数量": "quantity",
        "数量(个)": "quantity",
        "单位": "unit",
    }
    return mapping.get(col, col)


def infer_category(name: str) -> ProductCategory:
    name = name.lower()
    if any(k in name for k in ["柜", "箱", "屏"]):
        return ProductCategory.CABINET
    elif any(k in name for k in ["线", "排", "辅料", "胶带", "螺丝"]):
        return ProductCategory.AUXILIARY
    elif any(k in name for k in ["断路器", "接触器", "继电器", "开关", "按钮", "熔断器", "互感器"]):
        return ProductCategory.COMPONENT
    return ProductCategory.UNKNOWN


def _parse_quantity(value: object, excel_row: int) -> int:
    """Validate a quantity cell without silently coercing or truncating it."""
    location = f"Excel 第 {excel_row} 行“数量”"
    if pd.isna(value):
        raise InquiryParseError(f"{location}不能为空，必须填写正整数", category="invalid_quantity", row_number=excel_row)
    if pd.api.types.is_bool(value):
        raise InquiryParseError(f"{location}不能是布尔值，必须填写正整数", category="invalid_quantity", row_number=excel_row)
    if not isinstance(value, (Real, Decimal)):
        raise InquiryParseError(f"{location}必须是数值单元格中的正整数，文本值不接受", category="invalid_quantity", row_number=excel_row)

    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InquiryParseError(f"{location}必须是有限的正整数", category="invalid_quantity", row_number=excel_row) from None

    if not quantity.is_finite():
        raise InquiryParseError(f"{location}必须是有限的正整数", category="invalid_quantity", row_number=excel_row)
    if quantity <= 0:
        raise InquiryParseError(f"{location}必须大于 0", category="invalid_quantity", row_number=excel_row)
    if quantity != quantity.to_integral_value():
        raise InquiryParseError(f"{location}不能是小数，必须填写正整数", category="invalid_quantity", row_number=excel_row)
    if quantity > MAX_EXCEL_EXACT_INTEGER:
        raise InquiryParseError(f"{location}超过 Excel 可可靠表示的 15 位整数范围", category="invalid_quantity", row_number=excel_row)
    return int(quantity)


def _optional_text(row: pd.Series, column: str) -> str | None:
    value = row.get(column)
    if value is None or pd.isna(value):
        return None
    normalized = str(value).strip()
    return normalized or None


def parse_excel_with_rows(file_path: Path) -> List[tuple[int, Product]]:
    """解析 Excel 并保留每条产品明细对应的原始工作表行号。"""
    # Preserve Excel cell types so text like "10" is not silently inferred as numeric.
    df = pd.read_excel(file_path, dtype=object)
    df.columns = [normalize_column_name(c) for c in df.columns]

    required = {"name", "quantity"}
    if not required.issubset(set(df.columns)):
        raise InquiryParseError(
            f"Excel 缺少必要列: 至少需要 '产品名称' 和 '数量'，当前列: {list(df.columns)}",
            category="missing_required_columns",
        )

    products = []
    for excel_row, (_, row) in enumerate(df.iterrows(), start=2):
        if pd.isna(row["name"]) or str(row["name"]).strip() == "":
            continue
        product = Product(
            name=str(row["name"]).strip(),
            model=_optional_text(row, "model"),
            spec=_optional_text(row, "spec"),
            brand=_optional_text(row, "brand"),
            category=infer_category(str(row["name"])),
            quantity=_parse_quantity(row["quantity"], excel_row),
            unit=_optional_text(row, "unit") or "个",
        )
        products.append((excel_row, product))
    return products


def parse_excel(file_path: Path) -> List[Product]:
    """解析上传的 Excel 文件，返回产品列表。"""
    return [product for _, product in parse_excel_with_rows(file_path)]
