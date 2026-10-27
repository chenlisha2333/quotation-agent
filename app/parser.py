import pandas as pd
from pathlib import Path
from typing import List
from .models import Product, ProductCategory


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


def parse_excel(file_path: Path) -> List[Product]:
    """解析上传的 Excel 文件，返回产品列表"""
    df = pd.read_excel(file_path)
    df.columns = [normalize_column_name(c) for c in df.columns]

    required = {"name", "quantity"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Excel 缺少必要列: 至少需要 '产品名称' 和 '数量'，当前列: {list(df.columns)}")

    products = []
    for _, row in df.iterrows():
        if pd.isna(row["name"]) or str(row["name"]).strip() == "":
            continue
        product = Product(
            name=str(row["name"]).strip(),
            model=str(row.get("model", "")) if not pd.isna(row.get("model", "")) else None,
            spec=str(row.get("spec", "")) if not pd.isna(row.get("spec", "")) else None,
            brand=str(row.get("brand", "")) if not pd.isna(row.get("brand", "")) else None,
            category=infer_category(str(row["name"])),
            quantity=int(row["quantity"]) if not pd.isna(row.get("quantity")) else 1,
            unit=str(row.get("unit", "个")) if not pd.isna(row.get("unit", "")) else "个",
        )
        products.append(product)
    return products
